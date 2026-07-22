"use client";

import { useMutation } from "@tanstack/react-query";
import { useRef } from "react";
import {
	addTaskToCache,
	removeTaskFromCache,
} from "@/hooks/use-data-cache-helpers";
import {
	TOAST_WINDOW_MS,
	useOptimisticAction,
} from "@/hooks/use-optimistic-action";
import { trpc } from "@/utils/trpc";
import type { Task } from "./use-data";

/**
 * Delete-with-undo for a single task (FEAT-008 items 1 + 2).
 *
 * The row disappears from every cached list immediately (optimistic), but
 * the real `tasks.delete` mutation is deferred until the undo window closes
 * — a plain cache rollback can't un-delete a row that already hit the
 * database, so the network call itself has to wait out the same window the
 * "Undo" toast button (and Cmd+Z, via `useUndoLastOptimistic`) is armed for.
 */
export function useTaskDeleteWithUndo() {
	const { mutateAsync: deleteTaskMutation } = useMutation(
		trpc.tasks.delete.mutationOptions(),
	);
	const pendingTimers = useRef(
		new Map<string, ReturnType<typeof setTimeout>>(),
	);

	return useOptimisticAction<Task, Task>({
		action: "task.delete",
		optimisticUpdate: (task) => {
			removeTaskFromCache(task.id);
			return task;
		},
		mutateFn: (task) =>
			new Promise((resolve, reject) => {
				const timer = setTimeout(() => {
					pendingTimers.current.delete(task.id);
					deleteTaskMutation({ id: task.id }).then(resolve, reject);
				}, TOAST_WINDOW_MS);
				pendingTimers.current.set(task.id, timer);
			}),
		rollback: (task) => {
			const timer = pendingTimers.current.get(task.id);
			if (timer) {
				clearTimeout(timer);
				pendingTimers.current.delete(task.id);
			}
			addTaskToCache(task);
		},
		toastLabel: "Task deleted",
	});
}

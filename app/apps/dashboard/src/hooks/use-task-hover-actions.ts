"use client";

import { useMutation } from "@tanstack/react-query";
import { useMemo } from "react";
import {
	useCloneTaskPanel,
	useTaskPanel,
} from "@/components/panels/task-panel";
import { useTasksViewContext } from "@/components/tasks-view/tasks-view";
import { useTaskHoverStore } from "@/store/task-hover";
import { trpc } from "@/utils/trpc";
import { type EnrichedTask, useStatuses } from "./use-data";
import { updateTaskInCache } from "./use-data-cache-helpers";
import { useOptimisticAction } from "./use-optimistic-action";
import { useShortcut } from "./use-shortcuts";
import { useTaskDeleteWithUndo } from "./use-task-delete-with-undo";

/**
 * Board & list keyboard quick-actions on the hovered task (FEAT-008 item 1):
 *   x         -> complete (optimistic, undo-able)
 *   e         -> edit (opens the task detail panel)
 *   backspace -> delete (with undo — see `useTaskDeleteWithUndo`)
 *   mod+d     -> duplicate (opens the clone panel)
 *
 * Mount once per view (`TasksBoard` / `TasksList`) — it reads the shared
 * hover store rather than taking a task prop, so a single instance covers
 * whichever card/row currently has the mouse.
 */
export function useTaskHoverQuickActions() {
	const { tasks } = useTasksViewContext();
	const hoveredTaskId = useTaskHoverStore((s) => s.hoveredTaskId);
	const taskPanel = useTaskPanel();
	const cloneTaskPanel = useCloneTaskPanel();
	const { data: statuses } = useStatuses();
	const deleteWithUndo = useTaskDeleteWithUndo();

	const hoveredTask = useMemo(
		() => tasks.find((t) => t.id === hoveredTaskId),
		[tasks, hoveredTaskId],
	);

	const doneStatus = useMemo(
		() => statuses?.data?.find((s) => s.type === "done"),
		[statuses],
	);

	const { mutateAsync: updateTaskMutation } = useMutation(
		trpc.tasks.update.mutationOptions(),
	);

	const completeAction = useOptimisticAction<EnrichedTask, EnrichedTask>({
		action: "task.complete",
		// Only `statusId` needs to land in the raw cache — `useTasks()` recomputes
		// each task's joined `status` object from that id + the statuses map on
		// every render, so there's no separate joined field to keep in sync here.
		optimisticUpdate: (task) => {
			updateTaskInCache({ id: task.id, statusId: doneStatus?.id });
			return task;
		},
		mutateFn: (task) => {
			if (!doneStatus) return Promise.reject(new Error("No done status"));
			return updateTaskMutation({ id: task.id, statusId: doneStatus.id });
		},
		rollback: (task) =>
			updateTaskInCache({ id: task.id, statusId: task.statusId }),
		toastLabel: "Marked done",
	});

	useShortcut(
		"tasks.hover.complete",
		() => {
			if (hoveredTask && doneStatus && hoveredTask.status?.type !== "done") {
				completeAction.run(hoveredTask);
			}
		},
		{ enabled: !!hoveredTask },
	);

	useShortcut(
		"tasks.hover.edit",
		() => {
			if (hoveredTask) taskPanel.open(hoveredTask.id);
		},
		{ enabled: !!hoveredTask },
	);

	useShortcut(
		"tasks.hover.delete",
		() => {
			if (hoveredTask) deleteWithUndo.run(hoveredTask);
		},
		{ enabled: !!hoveredTask },
	);

	useShortcut(
		"tasks.hover.duplicate",
		() => {
			if (hoveredTask) cloneTaskPanel.open(hoveredTask.id);
		},
		{ enabled: !!hoveredTask },
	);
}

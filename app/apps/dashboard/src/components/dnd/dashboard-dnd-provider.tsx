"use client";

import {
	closestCenter,
	DndContext,
	type DragEndEvent,
	KeyboardSensor,
	PointerSensor,
	useSensor,
	useSensors,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useMutation } from "@tanstack/react-query";
import {
	createContext,
	type ReactNode,
	useCallback,
	useContext,
	useMemo,
	useRef,
} from "react";
import { toast } from "sonner";
import { trpc } from "@/utils/trpc";

export const PROJECT_DROPPABLE_PREFIX = "project-droppable-";

export function projectDroppableId(projectId: string) {
	return `${PROJECT_DROPPABLE_PREFIX}${projectId}`;
}

type SortableHandler = (event: DragEndEvent) => void;

type DashboardDndContextValue = {
	registerSortableHandler: (handler: SortableHandler | null) => void;
};

export const DashboardDndContext =
	createContext<DashboardDndContextValue | null>(null);

export function DashboardDndProvider({ children }: { children: ReactNode }) {
	const sortableHandlerRef = useRef<SortableHandler | null>(null);

	const registerSortableHandler = useCallback(
		(handler: SortableHandler | null) => {
			sortableHandlerRef.current = handler;
		},
		[],
	);

	const sensors = useSensors(
		useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
		useSensor(KeyboardSensor, {
			coordinateGetter: sortableKeyboardCoordinates,
		}),
	);

	const updateTodo = useMutation(
		trpc.todos.update.mutationOptions({
			onError: (e) => toast.error(e.message),
		}),
	);

	const handleDragEnd = useCallback(
		(event: DragEndEvent) => {
			const { active, over } = event;
			if (!over) return;
			const overId = String(over.id);
			if (overId.startsWith(PROJECT_DROPPABLE_PREFIX)) {
				const projectId = overId.slice(PROJECT_DROPPABLE_PREFIX.length);
				const todoId = String(active.id);
				const projectName =
					(over.data?.current as { name?: string } | undefined)?.name ??
					"project";
				updateTodo.mutate(
					{ id: todoId, projectId },
					{
						onSuccess: () => {
							toast.success(`Moved to ${projectName}`);
						},
					},
				);
				return;
			}
			sortableHandlerRef.current?.(event);
		},
		[updateTodo],
	);

	const value = useMemo<DashboardDndContextValue>(
		() => ({ registerSortableHandler }),
		[registerSortableHandler],
	);

	return (
		<DashboardDndContext.Provider value={value}>
			<DndContext
				sensors={sensors}
				collisionDetection={closestCenter}
				onDragEnd={handleDragEnd}
			>
				{children}
			</DndContext>
		</DashboardDndContext.Provider>
	);
}

export function useDashboardSortableHandler() {
	const ctx = useContext(DashboardDndContext);
	return ctx?.registerSortableHandler ?? null;
}

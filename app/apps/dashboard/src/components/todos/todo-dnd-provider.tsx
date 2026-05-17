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

/**
 * The droppable-id namespace used by sidebar project rows. Keep this in lock-step
 * with `SidebarProjects` where the matching `useDroppable({ id })` call lives.
 */
export const PROJECT_DROPPABLE_PREFIX = "project-droppable-";

export function projectDroppableId(projectId: string) {
	return `${PROJECT_DROPPABLE_PREFIX}${projectId}`;
}

/**
 * Provider-level DnD context that lets a todo dragged out of the To-do list
 * land on a sidebar project row. The inner sortable reorder logic still runs
 * inside `TodosView`, but registers its handler via `useTodoSortableHandler`
 * below so the routing decision happens here.
 *
 * Drop routing:
 *   - `over.id` starts with `PROJECT_DROPPABLE_PREFIX` → call `todos.update`
 *     with the parsed projectId.
 *   - Otherwise → forward the event to the registered sortable handler (the
 *     todo list reorder).
 */
type SortableHandler = (event: DragEndEvent) => void;

type TodoDndContextValue = {
	registerSortableHandler: (handler: SortableHandler | null) => void;
};

const TodoDndContext = createContext<TodoDndContextValue | null>(null);

export function TodoDndProvider({ children }: { children: ReactNode }) {
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
			// Fall through to the registered sortable reorder handler (TodosView).
			sortableHandlerRef.current?.(event);
		},
		[updateTodo],
	);

	const value = useMemo<TodoDndContextValue>(
		() => ({ registerSortableHandler }),
		[registerSortableHandler],
	);

	return (
		<TodoDndContext.Provider value={value}>
			<DndContext
				sensors={sensors}
				collisionDetection={closestCenter}
				onDragEnd={handleDragEnd}
			>
				{children}
			</DndContext>
		</TodoDndContext.Provider>
	);
}

/**
 * Hook for `TodosView` to register its sortable drag-end logic. Returns a
 * stable setter — call with `null` on unmount to detach.
 */
export function useTodoSortableHandler() {
	const ctx = useContext(TodoDndContext);
	return ctx?.registerSortableHandler ?? null;
}

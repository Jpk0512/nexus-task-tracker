import { create } from "zustand";

/**
 * Tracks the currently mouse-hovered task across board (kanban) and list
 * surfaces. Board/list rows have no roving-tabindex keyboard focus today, so
 * hover is used as the "focused" proxy for the FEAT-008 quick-action
 * shortcuts (x/e/backspace/mod+d) — see `use-task-hover-actions.ts`.
 */
interface TaskHoverState {
	hoveredTaskId: string | null;
	setHoveredTask: (taskId: string) => void;
	clearHoveredTask: (taskId: string) => void;
}

export const useTaskHoverStore = create<TaskHoverState>()((set, get) => ({
	hoveredTaskId: null,

	setHoveredTask: (taskId: string) => set({ hoveredTaskId: taskId }),

	// Only clears when the leaving task is still the recorded one — guards
	// against an out-of-order mouseleave(A)/mouseenter(B) pair clobbering B.
	clearHoveredTask: (taskId: string) => {
		if (get().hoveredTaskId === taskId) set({ hoveredTaskId: null });
	},
}));

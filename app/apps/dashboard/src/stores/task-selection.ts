"use client";

import { create } from "zustand";

/**
 * Task-selection store — single Zustand store powering multi-select across
 * every task surface (Todos, Triage, Inbox, Recurring).
 *
 * **Why a shared store and not local state?**
 * The selection drives a global `<BulkOpsBar />` that slides up from the
 * bottom regardless of which surface the user is on. Local state would force
 * each surface to lift the bar into its own tree, duplicate the bar's UI, and
 * re-implement multi-select wiring (rangeFrom, keyboard handlers) per page.
 * One store + one bar = one mental model.
 *
 * **Surface scoping (`activeSurface`).**
 * The bar's available actions depend on which surface the selection lives on
 * (Inbox can't move tasks to "Now"; Triage can't snooze inbox items). The
 * surface is set by the page when it mounts and cleared on unmount. The store
 * also tracks `selectedSet` + `lastFocusedId` so that:
 *   - `x` adds / removes the focused id from the set,
 *   - `shift+x` adds the inclusive range between `lastFocusedId` and the
 *     currently focused id (using the surface's `orderedIds` snapshot —
 *     surfaces push their visible row order each render so range-select
 *     respects the current filter / scope / group).
 *
 * **Why a Set, not an array?**
 * O(1) membership checks (every row queries `isSelected(id)` on render).
 * Order is recovered from `orderedIds` when the bar needs to act on the
 * selection (e.g. "mark these 4 as done" in the user's visible order).
 */

export type TaskSurface = "todos" | "triage" | "inbox" | "recurring";

interface TaskSelectionState {
	/** Stable id of the surface that currently owns the selection. */
	activeSurface: TaskSurface | null;
	/** Selected row ids (entity ids — todo id, task id, inbox id). */
	selected: Set<string>;
	/** Snapshot of the surface's currently-rendered row order. */
	orderedIds: string[];
	/** The last id `x` was toggled on — anchor for `shift+x` range select. */
	lastFocusedId: string | null;

	// ─── mutators (called by surfaces or by the BulkOpsBar) ─────────────────
	setSurface(surface: TaskSurface | null, orderedIds: string[]): void;
	setOrderedIds(orderedIds: string[]): void;
	toggle(id: string): void;
	rangeTo(id: string): void;
	add(id: string): void;
	remove(id: string): void;
	clear(): void;
	selectAll(): void;
}

export const useTaskSelection = create<TaskSelectionState>((set, get) => ({
	activeSurface: null,
	selected: new Set<string>(),
	orderedIds: [],
	lastFocusedId: null,

	setSurface(surface, orderedIds) {
		set((prev) => {
			// Switching surfaces always clears the prior selection — selecting a
			// todo and then navigating to Triage shouldn't have those ids leak
			// into a different entity space.
			if (prev.activeSurface !== surface) {
				return {
					activeSurface: surface,
					selected: new Set<string>(),
					orderedIds,
					lastFocusedId: null,
				};
			}
			return { orderedIds };
		});
	},

	setOrderedIds(orderedIds) {
		// Pure ordering refresh — keep the current selection / anchor.
		set({ orderedIds });
	},

	toggle(id) {
		set((prev) => {
			const next = new Set(prev.selected);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return { selected: next, lastFocusedId: id };
		});
	},

	rangeTo(id) {
		set((prev) => {
			const ordered = prev.orderedIds;
			if (ordered.length === 0) return prev;
			const anchor = prev.lastFocusedId;
			// No anchor yet — degrade to a normal toggle so the user can begin a
			// range from this row on the next shift+x press.
			if (!anchor) {
				const next = new Set(prev.selected);
				next.add(id);
				return { selected: next, lastFocusedId: id };
			}
			const i = ordered.indexOf(anchor);
			const j = ordered.indexOf(id);
			if (i < 0 || j < 0) return prev;
			const [lo, hi] = i < j ? [i, j] : [j, i];
			const next = new Set(prev.selected);
			for (let k = lo; k <= hi; k++) next.add(ordered[k]);
			return { selected: next };
		});
	},

	add(id) {
		set((prev) => {
			if (prev.selected.has(id)) return prev;
			const next = new Set(prev.selected);
			next.add(id);
			return { selected: next };
		});
	},

	remove(id) {
		set((prev) => {
			if (!prev.selected.has(id)) return prev;
			const next = new Set(prev.selected);
			next.delete(id);
			return { selected: next };
		});
	},

	clear() {
		set({ selected: new Set<string>(), lastFocusedId: null });
	},

	selectAll() {
		set((prev) => ({ selected: new Set(prev.orderedIds) }));
	},
}));

// ─── Convenience selectors (avoid re-rendering rows on unrelated state changes)
export function useIsSelected(id: string): boolean {
	return useTaskSelection((s) => s.selected.has(id));
}

export function useSelectionCount(): number {
	return useTaskSelection((s) => s.selected.size);
}

export function useActiveSurface(): TaskSurface | null {
	return useTaskSelection((s) => s.activeSurface);
}

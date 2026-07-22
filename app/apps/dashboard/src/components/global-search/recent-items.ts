import type { GlobalSearchItem } from "./types";

/**
 * Shared "recently visited" store — single source of truth for the command
 * palette's Recent section, the Cmd+O quick-open ring, and (FEAT-006 item 4)
 * the Home "Continue" card. Originally lived only inside
 * `global-search-dialog.tsx` and only recorded palette *selections*; this
 * module also backs direct-navigation visits recorded by `RecordVisit` so a
 * user landing on a project/chat/document via a normal `Link` shows up here
 * too, not just entities picked from the palette.
 */

export const RECENT_KEY = "nexus.palette.recent";
export const RECENT_MAX = 5;

export type RecentItem = GlobalSearchItem & { visitedAt?: string };

export function loadRecent(): RecentItem[] {
	if (typeof window === "undefined") return [];
	try {
		const raw = window.localStorage.getItem(RECENT_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed)
			? (parsed.slice(0, RECENT_MAX) as RecentItem[])
			: [];
	} catch {
		return [];
	}
}

export function persistRecent(item: GlobalSearchItem): void {
	if (typeof window === "undefined") return;
	try {
		const prior = loadRecent().filter((x) => x.id !== item.id);
		const stamped: RecentItem = {
			...item,
			visitedAt: new Date().toISOString(),
		};
		const next = [stamped, ...prior].slice(0, RECENT_MAX);
		window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
	} catch {
		// Quota / privacy mode — recent items are a nicety, not a contract.
	}
}

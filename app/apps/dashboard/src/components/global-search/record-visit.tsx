"use client";

// "use client": the parent route is a server component (it fetches the
// entity, then renders this) and persisting a visit needs localStorage,
// which only exists on the client. Mirrors `BreadcrumbSetter`'s shape —
// a null-rendering leaf that fires an effect from server-fetched data.

import { useEffect } from "react";
import { persistRecent } from "./recent-items";
import type { GlobalSearchItem } from "./types";

/**
 * Records a direct-navigation visit (FEAT-006 item 4) into the same
 * `nexus.palette.recent` store the command palette's own selections write
 * to, so "recently visited" reflects real browsing, not just Cmd+K picks.
 */
export function RecordVisit({ item }: { item: GlobalSearchItem }): null {
	// Re-fire only when the identity of the visited entity changes, not on
	// every incidental parent re-render (the caller passes a fresh object
	// literal each time it renders).
	// biome-ignore lint/correctness/useExhaustiveDependencies: identity-only deps by design
	useEffect(() => {
		persistRecent(item);
	}, [item.id, item.type, item.title]);

	return null;
}

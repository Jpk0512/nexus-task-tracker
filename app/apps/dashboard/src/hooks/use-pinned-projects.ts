"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Client-side pin state for the projects grid (iter-10 Round E, Task 5).
 *
 * The eventual home for this state is a `projects.pinned BOOLEAN` column +
 * tRPC mutation — that migration is deferred to iter-8 alongside the backlinks
 * schema work. Until then we surface the visual state via localStorage so the
 * card UI works end-to-end and a future swap to a server-backed mutation is a
 * one-file change inside `useOptimisticAction`.
 *
 * Storage shape: an `Array<string>` of project ids, JSON-encoded. Reading is
 * fault-tolerant — quota errors, private-mode, and malformed payloads all
 * collapse to "no pins". Writes are best-effort for the same reason; we never
 * surface a toast for a localStorage write failure because the user-visible
 * outcome (pin doesn't survive reload) is already obvious.
 *
 * Cross-tab sync: a `storage` event listener keeps multiple tabs in sync so
 * pinning a project in one tab updates every other tab without a refresh.
 */

const STORAGE_KEY = "nexus.projects.pinned";

function readPinnedSet(): Set<string> {
	if (typeof window === "undefined") return new Set();
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) return new Set();
		const parsed = JSON.parse(raw) as unknown;
		if (Array.isArray(parsed)) {
			return new Set(
				parsed.filter((v): v is string => typeof v === "string"),
			);
		}
		return new Set();
	} catch {
		return new Set();
	}
}

function writePinnedSet(set: Set<string>): void {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(
			STORAGE_KEY,
			JSON.stringify(Array.from(set)),
		);
	} catch {
		// ignore quota / private-mode failures — UI state already updated
	}
}

export interface UsePinnedProjectsApi {
	/** Reactive set of pinned project ids. */
	pinned: Set<string>;
	/** Toggle a single project's pin state. */
	toggle: (id: string) => void;
	/** Whether a project is currently pinned. */
	isPinned: (id: string) => boolean;
}

export function usePinnedProjects(): UsePinnedProjectsApi {
	const [pinned, setPinned] = useState<Set<string>>(() => new Set());

	useEffect(() => {
		setPinned(readPinnedSet());
		const onStorage = (event: StorageEvent) => {
			if (event.key === STORAGE_KEY) {
				setPinned(readPinnedSet());
			}
		};
		window.addEventListener("storage", onStorage);
		return () => window.removeEventListener("storage", onStorage);
	}, []);

	const toggle = useCallback((id: string) => {
		setPinned((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			writePinnedSet(next);
			return next;
		});
	}, []);

	const isPinned = useCallback((id: string) => pinned.has(id), [pinned]);

	return { pinned, toggle, isPinned };
}

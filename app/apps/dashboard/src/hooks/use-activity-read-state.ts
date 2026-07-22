"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Client-only "read" tracking for the Activity feed (FEAT-009 item 8).
 *
 * The `activities` table has no per-user read/seen column server-side — unlike
 * Inbox (`seen` on the row) there's no schema support to persist this today.
 * Rather than block "mark all read" on a schema change (forge-wire/atlas
 * territory), this follows the same precedent already established by
 * `use-pinned-projects.ts` (project pin state, also localStorage-only "because
 * the ... migration is deferred"): track read ids client-side, keyed by id, so
 * the feature ships now and can migrate to a real column later without
 * changing the call sites that use this hook.
 */

const STORAGE_KEY = "nexus.activity.read";
const MAX_TRACKED = 500;

function loadReadIds(): Set<string> {
	if (typeof window === "undefined") return new Set();
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) return new Set();
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? new Set(parsed as string[]) : new Set();
	} catch {
		return new Set();
	}
}

function persistReadIds(ids: Set<string>): void {
	if (typeof window === "undefined") return;
	try {
		// Cap the tracked set so this never grows unbounded — oldest entries
		// (by insertion order) are dropped first once past MAX_TRACKED.
		const trimmed = Array.from(ids).slice(-MAX_TRACKED);
		window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
	} catch {
		// Quota / privacy mode — read-state is a nicety, not a contract.
	}
}

export function useActivityReadState() {
	// Start from an always-empty Set — mirrors use-pinned-projects.ts's
	// deferred-read precedent. This component is "use client" but still
	// server-rendered by the App Router, so a synchronous localStorage read
	// in the initializer would run during hydration too and mismatch
	// whatever the server rendered, breaking the unread-dot/mark-all-read UI
	// for any returning user. The real read happens in the mount effect
	// below, after hydration has settled.
	const [readIds, setReadIds] = useState<Set<string>>(() => new Set());

	useEffect(() => {
		setReadIds(loadReadIds());
	}, []);

	// Multi-tab sync — another tab marking activity read should reflect here.
	useEffect(() => {
		const onStorage = (e: StorageEvent) => {
			if (e.key === STORAGE_KEY) setReadIds(loadReadIds());
		};
		window.addEventListener("storage", onStorage);
		return () => window.removeEventListener("storage", onStorage);
	}, []);

	const markRead = useCallback((ids: string[]) => {
		setReadIds((prev) => {
			const next = new Set(prev);
			for (const id of ids) next.add(id);
			persistReadIds(next);
			return next;
		});
	}, []);

	const markUnread = useCallback((ids: string[]) => {
		setReadIds((prev) => {
			const next = new Set(prev);
			for (const id of ids) next.delete(id);
			persistReadIds(next);
			return next;
		});
	}, []);

	const isRead = useCallback((id: string) => readIds.has(id), [readIds]);

	return { isRead, markRead, markUnread, readIds };
}

"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { queryClient, trpc } from "@/utils/trpc";

/**
 * Pin state for the projects grid (iter-10 Round F — server-backed).
 *
 * Server is source of truth: `projects.pinned BOOLEAN` via tRPC
 * `projects.setPinned` / `projects.listPinned`. localStorage is retained
 * as an OFFLINE FALLBACK so the UI still works when the API is
 * unreachable; once the network returns the server state wins.
 *
 * Storage shape: an `Array<string>` of project ids, JSON-encoded.
 *
 * Cross-tab sync: the `storage` event listener keeps multiple tabs in
 * sync without requiring a server round-trip on every read.
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
		window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(set)));
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
	// Start from localStorage so the first paint matches the previous
	// session immediately. The server hydration step below will overwrite
	// this if the server has a different view.
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

	// Server-of-truth hydration. Drops the result into both state and
	// localStorage so the offline fallback stays current.
	const listQuery = useQuery(
		trpc.projects.listPinned.queryOptions(undefined, {
			staleTime: 30_000,
		}),
	);

	useEffect(() => {
		if (!listQuery.data) return;
		const set = new Set(listQuery.data);
		writePinnedSet(set);
		setPinned(set);
	}, [listQuery.data]);

	const setPinnedMutation = useMutation(
		trpc.projects.setPinned.mutationOptions({
			onSettled: () => {
				queryClient.invalidateQueries({
					queryKey: trpc.projects.listPinned.queryKey(),
				});
				queryClient.invalidateQueries({
					queryKey: trpc.projects.get.pathKey(),
				});
			},
		}),
	);

	const toggle = useCallback(
		(id: string) => {
			// Optimistic: flip locally + persist so the UI feels instant; the
			// mutation reconciles in the background. On failure the next
			// listPinned refetch will roll us back to server truth.
			setPinned((prev) => {
				const next = new Set(prev);
				const willPin = !next.has(id);
				if (willPin) next.add(id);
				else next.delete(id);
				writePinnedSet(next);
				setPinnedMutation
					.mutateAsync({ projectId: id, pinned: willPin })
					.catch(() => {
						// Quiet failure — the next refetch will reconcile.
					});
				return next;
			});
		},
		[setPinnedMutation],
	);

	const isPinned = useCallback((id: string) => pinned.has(id), [pinned]);

	return { pinned, toggle, isPinned };
}

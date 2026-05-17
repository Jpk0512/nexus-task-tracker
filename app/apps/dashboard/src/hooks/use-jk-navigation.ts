"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";

/**
 * Vim-style j/k navigation across a flat list of row IDs.
 *
 * - `j` moves to the next row (wraps at the end).
 * - `k` moves to the previous row (wraps at the start).
 * - `Enter` invokes `onOpen` with the currently focused id when one exists,
 *   and (when `toastLabel` is supplied) fires a subtle 800ms confirmation toast
 *   so the user can feel the keyboard muscle-memory paying off.
 * - Hotkeys are ignored when the user is typing in an input / textarea /
 *   contenteditable element — `react-hotkeys-hook` handles that filter for
 *   plain `j`/`k` bindings by default.
 *
 * The focused row is also scrolled into view via the `data-jk-row` attribute
 * convention so consumers don't have to wire refs.
 *
 * Designed for read-mostly list pages (Todos, Triage, Library, Documents,
 * Inbox). Pages just call this hook with their row IDs and an open handler.
 */
export function useJkNavigation({
	ids,
	onOpen,
	enabled = true,
	toastLabel,
}: {
	ids: string[];
	onOpen?: (id: string) => void;
	enabled?: boolean;
	/**
	 * Optional resolver that returns the toast text shown on `Enter`. Receives
	 * the focused row id. Return null to suppress the toast for that row.
	 */
	toastLabel?: (id: string) => string | null;
}) {
	const [focusedId, setFocusedId] = useState<string | null>(null);

	// Keep the focused id valid as the list changes (filters, refetches).
	// If the previously focused row vanished, fall back to the first row.
	useEffect(() => {
		if (ids.length === 0) {
			if (focusedId !== null) setFocusedId(null);
			return;
		}
		if (focusedId === null || !ids.includes(focusedId)) {
			setFocusedId(ids[0]);
		}
	}, [ids, focusedId]);

	const move = useCallback(
		(delta: 1 | -1) => {
			if (ids.length === 0) return;
			const idx =
				focusedId === null ? -1 : ids.findIndex((id) => id === focusedId);
			let next: number;
			if (idx === -1) {
				next = delta === 1 ? 0 : ids.length - 1;
			} else {
				next = (idx + delta + ids.length) % ids.length;
			}
			const nextId = ids[next];
			setFocusedId(nextId);
			// Scroll the new row into view if it's marked.
			if (typeof document !== "undefined") {
				requestAnimationFrame(() => {
					const el = document.querySelector<HTMLElement>(
						`[data-jk-row="${nextId}"]`,
					);
					el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
				});
			}
		},
		[ids, focusedId],
	);

	useHotkeys("j", () => enabled && move(1), {
		enabled,
		preventDefault: true,
	});
	useHotkeys("k", () => enabled && move(-1), {
		enabled,
		preventDefault: true,
	});
	useHotkeys(
		"enter",
		() => {
			if (!enabled) return;
			if (!focusedId || !onOpen) return;
			onOpen(focusedId);
			if (toastLabel) {
				const label = toastLabel(focusedId);
				if (label) {
					toast(label, {
						id: `jk-open-${focusedId}`,
						duration: 800,
					});
				}
			}
		},
		{ enabled: enabled && !!onOpen, preventDefault: false },
	);

	const helpers = useMemo(
		() => ({
			isFocused: (id: string) => id === focusedId,
			focusedId,
			setFocusedId,
		}),
		[focusedId],
	);

	return helpers;
}

/**
 * Small visual indicator for the page header showing j/k are wired up.
 * Returned as a React element from a function — keeps the import surface
 * small. Pages can drop `<JkHint />` next to the page title.
 */

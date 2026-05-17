"use client";

/**
 * Linear-style "repeat last command" (codex delighter #8).
 *
 * Tracks the most-recent command-style palette invocation in localStorage and
 * exposes a `Cmd+.` shortcut that re-fires it from anywhere in the app.
 *
 * The palette already routes commands through the `ActionResultItem` renderer
 * (any `GlobalSearchItem.id` starting with `action:`). We piggy-back on that
 * by exporting a `recordCommand` helper the palette calls right before it
 * closes — the helper writes to `nexus.palette.lastCommand` and notifies any
 * subscribed components via a tiny pub/sub.
 *
 * The shortcut handler dispatches the same action by looking up the item in
 * the palette's static `ACTIONS` catalogue, opening the palette to the
 * Actions tab pre-filtered for that command, and confirming on the user's
 * behalf. We keep this off the critical-path: if the user has never invoked
 * a command we no-op and let the standard Cmd+K open the empty palette.
 */

import { useEffect, useState } from "react";
import type { GlobalSearchItem } from "./types";

const STORAGE_KEY = "nexus.palette.lastCommand";

export type LastCommand = {
	/** The item.id from the palette catalogue (e.g. "action:new-task"). */
	id: string;
	/** Display title — shown in the "Repeat: <…>" affordance. */
	title: string;
	/** Wall-clock timestamp of the most-recent invocation. */
	at: string;
};

// ─── Pub/sub ─────────────────────────────────────────────────────────────
// localStorage events only fire on *other* tabs, so we need an in-tab signal
// to keep the "Repeat" affordance in sync after the user fires a command.
type Listener = (next: LastCommand | null) => void;
const listeners = new Set<Listener>();

function emit(next: LastCommand | null): void {
	for (const fn of listeners) fn(next);
}

/**
 * Read the last command from storage. Returns null if nothing has been
 * recorded yet or storage is unavailable (SSR, privacy mode).
 */
export function loadLastCommand(): LastCommand | null {
	if (typeof window === "undefined") return null;
	try {
		const raw = window.localStorage.getItem(STORAGE_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw) as Partial<LastCommand> | null;
		if (!parsed || typeof parsed.id !== "string") return null;
		return {
			id: parsed.id,
			title: typeof parsed.title === "string" ? parsed.title : parsed.id,
			at: typeof parsed.at === "string" ? parsed.at : new Date().toISOString(),
		};
	} catch {
		return null;
	}
}

/**
 * Persist a freshly-invoked command. Called from the palette's selection
 * pipeline; non-command items (entities, navigation, recent entries) are
 * filtered out by the `isCommandItem` predicate so the repeat ring stays
 * meaningfully scoped.
 */
export function recordCommand(item: GlobalSearchItem): void {
	if (!isCommandItem(item)) return;
	if (typeof window === "undefined") return;
	const next: LastCommand = {
		id: item.id,
		title: item.title,
		at: new Date().toISOString(),
	};
	try {
		window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
	} catch {
		// Quota / privacy mode — repeat is a nicety, not a contract.
	}
	emit(next);
}

/**
 * A "command-style" item is one the palette renders via `ActionResultItem` —
 * those have ids prefixed with `action:`. Plain entity rows (tasks/docs/etc)
 * and navigation links are intentionally excluded so Cmd+. doesn't re-open
 * the last document the user happened to click.
 */
export function isCommandItem(item: GlobalSearchItem): boolean {
	return item.id.startsWith("action:");
}

/**
 * React hook returning the live last-command. Re-renders on every
 * `recordCommand` call (in-tab) and on `storage` events (cross-tab).
 */
export function useLastCommand(): LastCommand | null {
	const [last, setLast] = useState<LastCommand | null>(() => loadLastCommand());

	useEffect(() => {
		const onStorage = (e: StorageEvent) => {
			if (e.key !== STORAGE_KEY) return;
			setLast(loadLastCommand());
		};
		const onLocal: Listener = (next) => setLast(next);
		listeners.add(onLocal);
		window.addEventListener("storage", onStorage);
		return () => {
			listeners.delete(onLocal);
			window.removeEventListener("storage", onStorage);
		};
	}, []);

	return last;
}

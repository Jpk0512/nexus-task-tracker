"use client";

import { useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { OptimisticToast } from "@/components/optimistic-toast";
import { useShortcut } from "@/hooks/use-shortcuts";

/**
 * useOptimisticAction — write-then-show-undo pattern (codex amendment #6).
 *
 * The hook wraps a mutation so callers can:
 *   1. apply an optimistic update to their cache,
 *   2. surface a 5-second undo toast with an animated progress bar,
 *   3. fire the real mutation in the background, and
 *   4. revert via Cmd+Z (or the toast's Undo button) before the window closes.
 *
 * Caller shape:
 *
 *   const action = useOptimisticAction({
 *     action: 'task.complete',
 *     optimisticUpdate: (task) => ({ ...task, status: 'done' }),
 *     mutateFn:        () => trpc.tasks.complete.mutate({ id }),
 *     rollback:        (snapshot) => restoreTaskCache(snapshot),
 *     toastLabel:      'Marked done',
 *   });
 *   action.run(task);
 *
 * Behaviour:
 *   - The most-recent optimistic action lives on a tiny module-level LIFO so
 *     Cmd+Z always targets the freshest write. Once the 5s window expires (or
 *     the toast is dismissed) the stack entry is dropped — undo only works
 *     while the toast is on screen.
 *   - Server failure path: revert + show an error toast. The hook does not
 *     swallow the error; callers can attach an `onError` for telemetry.
 *
 * Notes:
 *   - The hook intentionally does *not* know about TRPC / React-Query
 *     internals. Callers manage their own caches inside `optimisticUpdate`
 *     and `rollback` — keeps this primitive thin and applicable to non-trpc
 *     mutations (settings prefs, localStorage, in-memory state).
 */

interface UndoEntry {
	rollback: () => void;
	toastId: string | number;
}

// Module-level LIFO. We only ever push from `run()` and pop from `Cmd+Z` or
// natural toast expiry, so concurrent races are bounded to the visible window
// (≤5s). One stale entry max — no leak risk worth a more complex store.
const undoStack: UndoEntry[] = [];

function pushUndo(entry: UndoEntry): void {
	undoStack.push(entry);
}

function popUndo(entry: UndoEntry): void {
	const idx = undoStack.indexOf(entry);
	if (idx >= 0) undoStack.splice(idx, 1);
}

function popLatest(): UndoEntry | undefined {
	return undoStack.pop();
}

export const TOAST_WINDOW_MS = 5_000;

export interface UseOptimisticActionOptions<TInput, TSnapshot> {
	/** Stable identifier (e.g. `'task.complete'`) used for telemetry + toast id. */
	action: string;
	/**
	 * Apply the optimistic update to local cache. Must return a snapshot the
	 * `rollback` function can use to restore prior state.
	 */
	optimisticUpdate: (input: TInput) => TSnapshot;
	/** Restore the cache from a snapshot produced by `optimisticUpdate`. */
	rollback: (snapshot: TSnapshot) => void;
	/** The real mutation. Receives the same input the caller passed to `run`. */
	mutateFn: (input: TInput) => Promise<unknown>;
	/** Headline label shown in the undo toast (e.g. "Marked done"). */
	toastLabel: string;
	/** Optional secondary line — usually the entity name. */
	toastDescription?: string;
	/** Optional error callback (e.g. for telemetry). */
	onError?: (err: unknown, input: TInput) => void;
	/** Optional success callback (mutation resolved). */
	onSuccess?: (input: TInput) => void;
}

export interface OptimisticActionApi<TInput> {
	run: (input: TInput) => void;
}

export function useOptimisticAction<TInput, TSnapshot>(
	options: UseOptimisticActionOptions<TInput, TSnapshot>,
): OptimisticActionApi<TInput> {
	const optionsRef = useRef(options);
	optionsRef.current = options;

	const run = useCallback((input: TInput) => {
		const opts = optionsRef.current;
		const snapshot = opts.optimisticUpdate(input);
		const toastId = `optimistic:${opts.action}:${Date.now()}`;

		const doRollback = () => {
			opts.rollback(snapshot);
		};

		const entry: UndoEntry = {
			rollback: doRollback,
			toastId,
		};
		pushUndo(entry);

		const dismiss = () => {
			popUndo(entry);
			toast.dismiss(toastId);
		};

		toast.custom(
			(t) => (
				<OptimisticToast
					label={opts.toastLabel}
					description={opts.toastDescription}
					durationMs={TOAST_WINDOW_MS}
					onUndo={() => {
						doRollback();
						popUndo(entry);
						toast.dismiss(t);
					}}
					onDismiss={() => {
						popUndo(entry);
						toast.dismiss(t);
					}}
				/>
			),
			{ id: toastId, duration: TOAST_WINDOW_MS },
		);

		// Fire the mutation. On error, revert (only if the entry is still on
		// the stack — the user may have already undone it manually).
		opts.mutateFn(input).then(
			() => {
				// Mutation landed — clean up the undo entry once the window
				// closes naturally. We don't dismiss the toast early; the user
				// may still want to undo until the timer runs out.
				opts.onSuccess?.(input);
				setTimeout(() => popUndo(entry), TOAST_WINDOW_MS);
			},
			(err) => {
				if (undoStack.includes(entry)) {
					doRollback();
					popUndo(entry);
				}
				toast.dismiss(toastId);
				toast.error(`Couldn't ${opts.toastLabel.toLowerCase()}`, {
					description:
						err instanceof Error
							? err.message
							: "The server rejected the change.",
				});
				opts.onError?.(err, input);
			},
		);

		// Belt-and-braces: even if mutateFn never resolves, drop the entry
		// once the visible window is gone so Cmd+Z doesn't target a stale row.
		setTimeout(dismiss, TOAST_WINDOW_MS + 100);
	}, []);

	return { run };
}

/**
 * Global Cmd+Z wiring. Mount once near the app root (the GlobalShortcuts tree
 * is a fine home) so that any optimistic action pushed to the stack can be
 * reverted with the standard system undo chord.
 *
 * If the stack is empty the hotkey falls through — we don't preventDefault.
 */
export function useUndoLastOptimistic(): void {
	useShortcut(
		"undo.last",
		(event) => {
			const entry = popLatest();
			if (!entry) return;
			event.preventDefault?.();
			entry.rollback();
			toast.dismiss(entry.toastId);
			toast("Undone", { duration: 1_200, id: "optimistic-undo" });
		},
		{ enabled: true, preventDefault: false },
	);
}

/**
 * Test/storybook escape hatch — clears the undo LIFO between renders. Not
 * needed in production code paths.
 */
export function __resetOptimisticStack(): void {
	undoStack.length = 0;
}

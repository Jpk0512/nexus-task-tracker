import { toast } from "sonner";

/**
 * Canonical toast lifecycle for any async, user-triggered action (FEAT-009
 * item 4): **loading -> success (with an optional Undo/View action) -> error
 * (with an optional Retry action)**, all coalesced onto a single toast slot
 * via a shared `id` so the loading toast morphs into its outcome instead of
 * stacking a second one.
 *
 * Before this helper, call sites hand-rolled the same three-call shape
 * (`toast.loading` in `onMutate`, `toast.success` in `onSuccess`, `toast.error`
 * in `onError`) with inconsistent wording and no recovery affordance on
 * failure — a transient network error had no path back to retrying the same
 * action short of re-finding the original button. New call sites should
 * reach for `runToastAction`; existing ones migrate opportunistically.
 *
 * Usage:
 * ```ts
 * await runToastAction(() => trpcClient.projects.create.mutate(input), {
 *   loading: "Creating project…",
 *   success: (project) => `Project "${project.name}" created`,
 *   view: { onClick: (project) => router.push(`/projects/${project.id}`) },
 *   error: "Failed to create project",
 *   retry: () => submit(input),
 * });
 * ```
 */
export interface ToastActionOptions<T> {
	/** Coalescing id — defaults to a fresh one per call so callers don't have
	 *  to invent one just to get the loading->outcome morph. */
	id?: string | number;
	loading: string;
	success: string | ((result: T) => string);
	error?: string | ((error: unknown) => string);
	/** Revert affordance on the success toast (e.g. undo an archive/delete). */
	undo?: () => void;
	/** Navigate-to affordance on the success toast (e.g. "View" a new entity).
	 *  `onClick` receives the resolved result as a snapshot — read from it
	 *  instead of re-reading shared mutation state at click time, which can
	 *  point at the wrong entity if a second call overlaps before the toast
	 *  is dismissed. */
	view?: { label?: string; onClick: (result: T) => void };
	/** Re-run affordance on the error toast — re-invokes the same action. */
	retry?: () => void;
}

/**
 * Discriminated result — deliberately NOT `T | undefined`. A successful
 * action's own resolved value is often `undefined` (delete/void endpoints),
 * so callers need `ok` to tell "succeeded with no payload" apart from
 * "failed"; both `retry` and `toast.error` already ran by the time a caller
 * sees `ok: false`, so callers only need this to decide whether to run their
 * own success side-effects (cache invalidation, closing a form, navigating).
 */
export type ToastActionResult<T> =
	| { ok: true; data: T }
	| { ok: false; error: unknown };

export async function runToastAction<T>(
	action: () => Promise<T>,
	options: ToastActionOptions<T>,
): Promise<ToastActionResult<T>> {
	const id =
		options.id ?? `toast-action-${Math.random().toString(36).slice(2)}`;
	toast.loading(options.loading, { id });

	try {
		const result = await action();
		const message =
			typeof options.success === "function"
				? options.success(result)
				: options.success;

		toast.success(message, {
			id,
			action: options.undo
				? { label: "Undo", onClick: () => options.undo?.() }
				: options.view
					? {
							label: options.view.label ?? "View",
							onClick: () => options.view?.onClick(result),
						}
					: undefined,
		});
		return { ok: true, data: result };
	} catch (err) {
		const message =
			typeof options.error === "function"
				? options.error(err)
				: (options.error ?? "Something went wrong");

		toast.error(message, {
			id,
			action: options.retry
				? { label: "Retry", onClick: () => options.retry?.() }
				: undefined,
		});
		return { ok: false, error: err };
	}
}

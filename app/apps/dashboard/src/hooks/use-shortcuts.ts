"use client";

import { useHotkeys } from "react-hotkeys-hook";
import { getShortcut } from "@/lib/shortcuts/registry";

/**
 * Wire a handler against a registered shortcut action.
 *
 * Consumers don't pass key strings — they pass the stable `action` id from
 * `SHORTCUTS` and this hook looks up the current binding. Remapping a chord
 * is then a one-line registry edit; every consumer follows automatically.
 *
 *   useShortcut('palette.open', () => setOpen(true));
 *   useShortcut('row.next', () => move(1), { enabled: !modalOpen });
 *
 * Options pass through to `react-hotkeys-hook`. When the registered action
 * does not exist, the hook is a no-op — missing entries warn at module load
 * via the dev-only collision check in `registry.ts`, but they never crash a
 * screen.
 *
 * `enabled` defaults to true. For row/modal scopes you generally pass an
 * explicit guard so the binding only fires while the row list or modal owns
 * focus.
 */
export function useShortcut(
	action: string,
	handler: (event: KeyboardEvent) => void,
	options?: {
		enabled?: boolean;
		preventDefault?: boolean;
		enableOnFormTags?: boolean;
		enableOnContentEditable?: boolean;
	},
): void {
	const spec = getShortcut(action);
	const keys = spec?.keys ?? "";
	const enabled = options?.enabled !== false && keys.length > 0;

	useHotkeys(
		keys,
		(event) => {
			if (!enabled) return;
			handler(event as unknown as KeyboardEvent);
		},
		{
			enabled,
			preventDefault: options?.preventDefault ?? true,
			enableOnFormTags: options?.enableOnFormTags ?? false,
			enableOnContentEditable: options?.enableOnContentEditable ?? false,
			// react-hotkeys-hook only treats a `keys` string as a sequence when it
			// contains its `sequenceSplitKey` (default ">"). The registry's
			// documented convention is space-separated sequences (e.g. "g t"), so
			// without this override every "g <key>" binding silently parses as one
			// literal (unmatchable) combo token instead of a two-key sequence and
			// never fires. No registered `keys` string mixes "+" and " ", so this
			// is a no-op for every non-sequence binding.
			sequenceSplitKey: " ",
		},
	);
}

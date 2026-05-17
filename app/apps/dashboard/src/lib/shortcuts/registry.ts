/**
 * Central shortcut registry — single source of truth for every keyboard
 * binding in the dashboard.
 *
 * This file backs **codex amendment #4 (Shortcut Governance Spec)** from the
 * iter-10 review: every shortcut lives here, with an explicit scope so that
 * the runtime can enforce a collision matrix and so that the help overlay can
 * be generated rather than maintained by hand.
 *
 * Conventions:
 *   - `keys` uses the `react-hotkeys-hook` notation
 *     (e.g. `"mod+k"`, `"g t"`, `"shift+x"`). `mod` is Cmd on macOS and Ctrl
 *     elsewhere; the hotkeys hook handles that platform split for us.
 *   - `scope` decides where the binding is wired:
 *       global → window-level, always on (still respects editable-target
 *         filters via the hook).
 *       route  → registered by a specific route component; consumers must
 *         match the `route` field before calling `useShortcut`.
 *       row    → registered by row-aware lists (Todos, Triage, Library, …);
 *         single-letter keys live here so they cannot fire while focus is
 *         inside an input.
 *       modal  → registered inside a specific modal/dialog while it is open.
 *   - `action` is the stable identifier consumers register handlers against;
 *     handlers are wired via the `useShortcut(action, handler)` hook. This
 *     keeps the *what-key-fires-it* concern out of feature code so we can
 *     remap a chord here and have every consumer follow along.
 *
 * Adding a shortcut?
 *   1. Add an entry below.
 *   2. Register a handler with `useShortcut(action, handler)` in the owning
 *      component.
 *   3. The dev-only collision check at the bottom of this file will warn if
 *      the new entry duplicates an existing (scope, route, keys) tuple.
 */

export type ShortcutScope = "global" | "route" | "row" | "modal";

export interface ShortcutSpec {
	/**
	 * `react-hotkeys-hook` keys string. Use `mod` for Cmd-or-Ctrl. Sequence
	 * chords (e.g. `g t`) are space-separated.
	 */
	keys: string;
	/** Human label shown in the help overlay. */
	label: string;
	/** Where the binding is wired — see file header. */
	scope: ShortcutScope;
	/**
	 * For `scope: 'row' | 'route'`, the route-prefix (e.g. `"/triage"`) the
	 * binding is restricted to. Omit for ambient row/route shortcuts.
	 */
	route?: string;
	/** Stable identifier consumers register a handler against via `useShortcut`. */
	action: string;
	/** Optional long-form description for tooltips / docs. */
	description?: string;
	/** Restrict to a platform (`"mac" | "win" | "linux"`). */
	reservedFor?: "mac" | "win" | "linux";
}

export const SHORTCUTS: ShortcutSpec[] = [
	// ─── Global ────────────────────────────────────────────────────────────
	{
		keys: "mod+k",
		label: "Open command palette",
		scope: "global",
		action: "palette.open",
	},
	{
		keys: "mod+/",
		label: "Focus filter",
		scope: "global",
		action: "filter.focus",
	},
	{
		keys: "mod+,",
		label: "Open settings",
		scope: "global",
		action: "nav.settings",
	},
	{
		keys: "mod+b",
		label: "Toggle sidebar",
		scope: "global",
		action: "sidebar.toggle",
	},
	{
		keys: "mod+shift+p",
		label: "Switch project",
		scope: "global",
		action: "project.switch",
	},
	{
		keys: "?",
		label: "Shortcuts help",
		scope: "global",
		action: "help.open",
	},
	// ─── Navigate (g <key> sequences) ──────────────────────────────────────
	{
		keys: "g t",
		label: "Go to Todos",
		scope: "global",
		action: "nav.todos",
	},
	{
		keys: "g d",
		label: "Go to Documents",
		scope: "global",
		action: "nav.documents",
	},
	{
		keys: "g p",
		label: "Go to Projects",
		scope: "global",
		action: "nav.projects",
	},
	{
		keys: "g s",
		label: "Go to Settings",
		scope: "global",
		action: "nav.settings.alt",
	},
	{
		keys: "g i",
		label: "Go to Inbox",
		scope: "global",
		action: "nav.inbox",
	},
	{
		keys: "g r",
		label: "Go to Triage",
		scope: "global",
		action: "nav.triage",
	},
	// ─── Undo ──────────────────────────────────────────────────────────────
	{
		keys: "mod+z",
		label: "Undo last write",
		scope: "global",
		action: "undo.last",
	},
	// ─── Row actions ───────────────────────────────────────────────────────
	{
		keys: "j",
		label: "Next row",
		scope: "row",
		action: "row.next",
	},
	{
		keys: "k",
		label: "Previous row",
		scope: "row",
		action: "row.prev",
	},
	{
		keys: "x",
		label: "Toggle select",
		scope: "row",
		action: "row.toggle",
	},
	{
		keys: "shift+x",
		label: "Range select",
		scope: "row",
		action: "row.range",
	},
	{
		keys: "enter",
		label: "Open detail",
		scope: "row",
		action: "row.open",
	},
	{
		keys: "e",
		label: "Inline edit",
		scope: "row",
		action: "row.edit",
	},
	{
		keys: "escape",
		label: "Cancel / close",
		scope: "row",
		action: "row.escape",
	},
	// ─── Quick-capture (Home) ──────────────────────────────────────────────
	{
		keys: "mod+n",
		label: "Focus quick-capture",
		scope: "global",
		action: "capture.focus",
		description:
			"Focus the natural-language quick-capture bar on the Home page.",
	},
	// ─── Palette delighters (codex review §2 — Linear/Raycast parity) ──────
	{
		keys: "mod+.",
		label: "Repeat last command",
		scope: "global",
		action: "palette.repeat-last",
		description:
			"Re-fire the most-recent command-style palette invocation (Linear-style).",
	},
	{
		keys: "mod+o",
		label: "Quick open recent",
		scope: "global",
		action: "palette.quick-open",
		description:
			"Raycast-style ring of recently-visited entities — faster than the full palette.",
	},
	// ─── Route-scoped ──────────────────────────────────────────────────────
	{
		keys: "c",
		label: "Create new",
		scope: "route",
		action: "create.new",
	},
	{
		keys: "/",
		label: "Focus filter",
		scope: "route",
		action: "filter.focus.local",
	},
	// ─── Triage column moves (row + route) ─────────────────────────────────
	{
		keys: "1",
		label: "Move to Now",
		scope: "row",
		route: "/triage",
		action: "triage.move.now",
	},
	{
		keys: "2",
		label: "Move to Next",
		scope: "row",
		route: "/triage",
		action: "triage.move.next",
	},
	{
		keys: "3",
		label: "Move to Later",
		scope: "row",
		route: "/triage",
		action: "triage.move.later",
	},
];

/**
 * Look up a `ShortcutSpec` by its action id. Returns `undefined` if no entry
 * exists — the `useShortcut` hook treats that as a "binding not wired yet"
 * no-op rather than throwing, so a missing entry never crashes a screen.
 */
export function getShortcut(action: string): ShortcutSpec | undefined {
	return SHORTCUTS.find((s) => s.action === action);
}

/**
 * Group every shortcut by scope (and route, for triage-style sub-scopes).
 * Used by the help overlay so the rendered list always tracks the registry.
 */
export function shortcutsByScope(): Record<string, ShortcutSpec[]> {
	const grouped: Record<string, ShortcutSpec[]> = {};
	for (const s of SHORTCUTS) {
		const bucket = s.route ? `${s.scope}:${s.route}` : s.scope;
		if (!grouped[bucket]) grouped[bucket] = [];
		grouped[bucket].push(s);
	}
	return grouped;
}

// ─── Collision check (dev-only invariant) ────────────────────────────────
// Surfaces accidental duplicates as a console.warn at module-load time. We
// intentionally do *not* throw — the registry is consumed during render in
// dev and a hard throw would mask the real warning behind a React boundary.
if (process.env.NODE_ENV !== "production") {
	const seen = new Map<string, ShortcutSpec>();
	for (const s of SHORTCUTS) {
		const key = `${s.scope}:${s.route ?? "*"}:${s.keys}`;
		const prior = seen.get(key);
		if (prior) {
			// eslint-disable-next-line no-console
			console.warn(
				`[shortcuts] collision on ${key} — '${s.action}' clashes with '${prior.action}'`,
				{ next: s, prior },
			);
		}
		seen.set(key, s);
	}
	const actions = new Set<string>();
	for (const s of SHORTCUTS) {
		if (actions.has(s.action)) {
			// eslint-disable-next-line no-console
			console.warn(`[shortcuts] duplicate action id '${s.action}'`);
		}
		actions.add(s.action);
	}
}

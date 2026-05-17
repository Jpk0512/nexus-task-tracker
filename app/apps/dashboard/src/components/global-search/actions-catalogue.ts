/**
 * Static catalogue of command-style palette entries.
 *
 * Lifted into its own module so the **repeat-last** (codex delighter #8) and
 * **quick-open** (delighter #9) flows can resolve a command id back to its
 * full `GlobalSearchItem` without duplicating the catalogue inside each
 * surface.
 *
 * Every entry is rendered by `ActionResultItem`; new entries here only need
 * a fresh `id: 'action:*'` and a route for the renderer to fire (handled
 * inside `action-result-item.tsx`).
 */

import type { GlobalSearchItem } from "./types";

export const ACTIONS: GlobalSearchItem[] = [
	{
		id: "action:new-task",
		type: "task",
		title: "/new task",
		teamId: "",
	},
	{
		id: "action:new-document",
		type: "document",
		title: "/new doc",
		teamId: "",
	},
	{
		id: "action:new-project",
		type: "project",
		title: "/new project",
		teamId: "",
	},
	{
		id: "action:toggle-sidebar",
		type: "navigation",
		title: "/toggle sidebar",
		teamId: "",
	},
	{
		id: "action:go-settings-labels",
		type: "navigation",
		title: "/go to settings/labels",
		teamId: "",
		href: "/settings/labels",
	},
	{
		id: "action:go-settings-shortcuts",
		type: "navigation",
		title: "/go to settings/shortcuts",
		teamId: "",
		href: "/settings/shortcuts",
	},
	{
		id: "action:open-inbox",
		type: "navigation",
		title: "/open inbox",
		teamId: "",
		href: "/inbox",
	},
];

/** Resolve a catalogue entry by id. Used by the repeat-last and quick-open flows. */
export function findActionById(id: string): GlobalSearchItem | undefined {
	return ACTIONS.find((a) => a.id === id);
}

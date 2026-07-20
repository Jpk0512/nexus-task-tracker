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
		id: "action:create-project-starter",
		type: "navigation",
		title: "/create project starter",
		teamId: "",
		href: "/create-project/starter",
	},
	{
		id: "action:toggle-sidebar",
		type: "navigation",
		title: "/toggle sidebar",
		teamId: "",
	},
	{
		id: "action:go-focus",
		type: "navigation",
		title: "/go focus",
		teamId: "",
		href: "/focus",
	},
	{
		id: "action:go-capture",
		type: "navigation",
		title: "/go outline",
		teamId: "",
		href: "/capture",
	},
	{
		id: "action:go-notes",
		type: "navigation",
		title: "/go notes",
		teamId: "",
		href: "/notes",
	},
	{
		id: "action:go-skills",
		type: "navigation",
		title: "/go skills",
		teamId: "",
		href: "/skills",
	},
	{
		id: "action:go-meetings",
		type: "navigation",
		title: "/go meetings",
		teamId: "",
		href: "/meetings",
	},
	{
		id: "action:go-health",
		type: "navigation",
		title: "/go health",
		teamId: "",
		href: "/health",
	},
	{
		id: "action:go-activity",
		type: "navigation",
		title: "/go activity",
		teamId: "",
		href: "/activity",
	},
	{
		id: "action:go-rituals",
		type: "navigation",
		title: "/go rituals",
		teamId: "",
		href: "/rituals",
	},
	{
		id: "action:go-todos",
		type: "navigation",
		title: "/go todos",
		teamId: "",
		href: "/todos",
	},
	{
		id: "action:go-vault",
		type: "navigation",
		title: "/go vault",
		teamId: "",
		href: "/vault",
	},
	{
		id: "action:go-mcps",
		type: "navigation",
		title: "/go mcps",
		teamId: "",
		href: "/vault",
	},
	{
		id: "action:go-secrets",
		type: "navigation",
		title: "/go secrets",
		teamId: "",
		href: "/vault",
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
		title: "/open needs you",
		teamId: "",
		href: "/focus?tab=needs-you",
	},
];

/** Resolve a catalogue entry by id. Used by the repeat-last and quick-open flows. */
export function findActionById(id: string): GlobalSearchItem | undefined {
	return ACTIONS.find((a) => a.id === id);
}

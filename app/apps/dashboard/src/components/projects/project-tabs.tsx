"use client";

import { cn } from "@ui/lib/utils";
import {
	BookOpenIcon,
	BrainIcon,
	FileTextIcon,
	KanbanIcon,
	LayoutDashboardIcon,
	LayoutIcon,
	ListChecksIcon,
	RadioIcon,
	UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";

export type ProjectTab =
	| "overview"
	| "board"
	| "docs"
	| "todos"
	| "library"
	| "knowledge"
	| "updates"
	| "views"
	| "members";

/**
 * Derive the active tab from the URL pathname. Falls back to "board" — the
 * default project route — when no sub-segment matches a known tab.
 *
 * `/projects/<id>` itself = Board (the default work surface).
 * `/projects/<id>/overview` = Overview (the description / progress page).
 */
function deriveActiveTab(pathname: string, projectId: string): ProjectTab {
	const marker = `/projects/${projectId}`;
	const idx = pathname.indexOf(marker);
	if (idx < 0) return "board";
	const after = pathname.slice(idx + marker.length).replace(/^\//, "");
	const first = after.split("/")[0];
	if (
		first === "overview" ||
		first === "docs" ||
		first === "todos" ||
		first === "library" ||
		first === "knowledge" ||
		first === "updates" ||
		first === "views" ||
		first === "members"
	) {
		return first;
	}
	return "board";
}

type TabDef = {
	id: ProjectTab;
	label: string;
	href: (team: string, projectId: string) => string;
	icon: typeof KanbanIcon;
};

// Order intentionally mirrors Linear's project subnav: project context
// (Overview) first, then primary work surface (Board, Docs), then capture
// (Todos), then resources (Library), then async signal (Updates), then saved
// views, then people.
const TABS: TabDef[] = [
	{
		id: "overview",
		label: "Overview",
		href: (team, p) => `/team/${team}/projects/${p}/overview`,
		icon: LayoutDashboardIcon,
	},
	{
		id: "board",
		label: "Board",
		href: (team, p) => `/team/${team}/projects/${p}`,
		icon: KanbanIcon,
	},
	{
		id: "docs",
		label: "Docs",
		href: (team, p) => `/team/${team}/projects/${p}/docs`,
		icon: FileTextIcon,
	},
	{
		id: "todos",
		label: "Todos",
		href: (team, p) => `/team/${team}/projects/${p}/todos`,
		icon: ListChecksIcon,
	},
	{
		id: "library",
		label: "Library",
		href: (team, p) => `/team/${team}/projects/${p}/library`,
		icon: BookOpenIcon,
	},
	{
		id: "knowledge",
		label: "Knowledge",
		href: (team, p) => `/team/${team}/projects/${p}/knowledge`,
		icon: BrainIcon,
	},
	{
		id: "updates",
		label: "Updates",
		href: (team, p) => `/team/${team}/projects/${p}/updates`,
		icon: RadioIcon,
	},
	{
		id: "views",
		label: "Views",
		href: (team, p) => `/team/${team}/projects/${p}/views`,
		icon: LayoutIcon,
	},
	{
		id: "members",
		label: "Members",
		href: (team, p) => `/team/${team}/projects/${p}/members`,
		icon: UsersIcon,
	},
];

type Props = {
	projectId: string;
	/** Override the auto-detected active tab when needed. */
	activeTab?: ProjectTab;
};

/**
 * Linear-style sub-nav for a project page.
 * 13px Inter weight 510, ink-subtle when inactive, ink + 2px lavender
 * bottom underline when active. No pills, no chips — pure underline.
 *
 * When `activeTab` is omitted, the component derives it from the current
 * pathname so the layout can mount this once for every project sub-route.
 */
export function ProjectTabs({ projectId, activeTab }: Props) {
	const { team } = useParams<{ team: string }>();
	const pathname = usePathname() ?? "";
	const resolvedActive = activeTab ?? deriveActiveTab(pathname, projectId);
	if (!team) return null;
	return (
		<nav
			aria-label="Project sections"
			className="flex items-center gap-0 border-border border-b bg-background px-6"
		>
			{TABS.map((tab) => {
				const Icon = tab.icon;
				const isActive = tab.id === resolvedActive;
				return (
					<Link
						key={tab.id}
						href={tab.href(team, projectId)}
						aria-current={isActive ? "page" : undefined}
						className={cn(
							// Linear's 13px Inter 510, slight negative tracking.
							"-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2.5 font-[510] text-[13px] tracking-[-0.006em] transition-colors",
							isActive
								? "border-primary text-foreground"
								: "border-transparent text-muted-foreground hover:text-foreground",
						)}
					>
						<Icon className="size-3.5" />
						{tab.label}
					</Link>
				);
			})}
		</nav>
	);
}

"use client";
import { DialogTitle } from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import {
	Command,
	CommandGroup,
	CommandInput,
	CommandList,
} from "@ui/components/ui/command";
import {
	Dialog,
	DialogContent,
	DialogFooter,
	DialogHeader,
} from "@ui/components/ui/dialog";
import { cn } from "@ui/lib/utils";
import { ArrowDownIcon, ArrowUpIcon, CornerDownLeftIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";
import { ACTIONS, findActionById } from "./actions-catalogue";
import {
	GlobalSearchProvider,
	type PaletteLinkMode,
	useGlobalSearch,
} from "./global-search-context";
import {
	isCommandItem,
	loadLastCommand,
	recordCommand,
	useLastCommand,
} from "./repeat-last-command";
import { SearchResultItem } from "./search-result-item";
import type { GlobalSearchItem } from "./types";

// ─── Tabs (codex amendment #4 / designer-crosscutting §1) ────────────────
//
// Tabs scope the palette without forcing a prefix; the user can also drive
// the same filter via the reserved prefix scheme:
//   `> …` → Navigation (force tab=settings until you type, then nav results)
//   `/ …` → Actions (commands, not entities)
//   plain → entity search across the current tab
//
// We keep the "All" tab as the default so muscle-memory (Cmd+K → type → Enter)
// still works.
const TAB_DEFS = [
	{ id: "all", label: "All", types: null }, // null = no filter
	{ id: "tasks", label: "Tasks", types: ["task"] },
	{ id: "documents", label: "Documents", types: ["document", "knowledge"] },
	{ id: "prompts", label: "Prompts", types: ["prompt"] },
	{ id: "projects", label: "Projects", types: ["project", "milestone"] },
	{ id: "settings", label: "Settings", types: ["navigation"] },
	{ id: "actions", label: "Actions", types: ["__action__"] }, // synthetic
] as const;

type TabId = (typeof TAB_DEFS)[number]["id"];

// ─── Recent items (last 5 visited entities, persisted) ───────────────────
// Persisted to `nexus.palette.recent`. We keep the storage small (5 entries,
// stringified) and refresh whenever an item is selected.
const RECENT_KEY = "nexus.palette.recent";
const RECENT_MAX = 5;

function loadRecent(): GlobalSearchItem[] {
	if (typeof window === "undefined") return [];
	try {
		const raw = window.localStorage.getItem(RECENT_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed.slice(0, RECENT_MAX) : [];
	} catch {
		return [];
	}
}

function persistRecent(item: GlobalSearchItem): void {
	if (typeof window === "undefined") return;
	try {
		const prior = loadRecent().filter((x) => x.id !== item.id);
		// Stamp the entry with a `visitedAt` so the Cmd+O quick-open ring
		// (codex delighter #9) can render a relative time. We intentionally
		// widen the storage shape only here — readers tolerate a missing
		// timestamp.
		const stamped = {
			...item,
			visitedAt: new Date().toISOString(),
		} as GlobalSearchItem & { visitedAt?: string };
		const next = [stamped, ...prior].slice(0, RECENT_MAX);
		window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
	} catch {
		// Quota / privacy mode — recent items are a nicety, not a contract.
	}
}

// Static Actions catalogue lives in `./actions-catalogue` so it can be reused
// by the repeat-last (Cmd+.) and quick-open (Cmd+O) delighters.

const defaultSearchState: GlobalSearchItem[] = [
	{
		id: "action:create-task",
		type: "task",
		title: "Create a new task",
		teamId: "",
	},
	{
		id: "action:create-project",
		type: "project",
		title: "Create a new project",
		teamId: "",
	},
	{
		id: "action:view-projects",
		type: "project",
		title: "View all projects",
		teamId: "",
	},
	{
		id: "navigate:inbox",
		type: "navigation",
		title: "Inbox",
		teamId: "",
		href: "/inbox",
	},
	{
		id: "navigate:reviews",
		type: "navigation",
		title: "Reviews",
		teamId: "",
		href: "/pr-reviews",
	},
	{
		id: "navigate:settings",
		type: "navigation",
		title: "Settings",
		teamId: "",
		href: "/settings",
	},
	{
		id: "navigate:general",
		type: "navigation",
		title: "General",
		teamId: "",
		href: "/settings/general",
	},
	{
		id: "navigate:profile",
		type: "navigation",
		title: "Profile",
		teamId: "",
		href: "/settings/profile",
	},
	{
		id: "navigate:billing",
		type: "navigation",
		title: "Billing",
		teamId: "",
		href: "/settings/billing",
	},
	{
		id: "navigate:labels",
		type: "navigation",
		title: "Labels",
		teamId: "",
		href: "/settings/labels",
	},
	{
		id: "navigate:members",
		type: "navigation",
		title: "Members",
		teamId: "",
		href: "/settings/members",
	},
	{
		id: "navigate:integrations",
		type: "navigation",
		title: "Integrations",
		teamId: "",
		href: "/settings/integrations",
	},
];

// Linear-style fixed section order. Unknown types fall through to the end so
// future entity types still render.
const SECTION_ORDER = [
	"task",
	"project",
	"milestone",
	"document",
	"todo",
	"knowledge",
	"library",
	"prompt",
	"navigation",
] as const;

const SECTION_LABELS: Record<string, string> = {
	task: "Tasks",
	project: "Projects",
	milestone: "Milestones",
	document: "Documents",
	todo: "Todos",
	knowledge: "Knowledge",
	library: "Library",
	prompt: "Prompts",
	navigation: "Navigation",
};

/**
 * Parse the search input for reserved prefixes (`>` → navigation, `/` → action).
 * Returns the stripped query plus the inferred mode.
 */
function parsePrefix(raw: string): {
	mode: "default" | "navigation" | "action";
	query: string;
} {
	const trimmed = raw.trimStart();
	if (trimmed.startsWith(">")) {
		return { mode: "navigation", query: trimmed.slice(1).trim() };
	}
	if (trimmed.startsWith("/")) {
		return { mode: "action", query: trimmed.slice(1).trim() };
	}
	return { mode: "default", query: trimmed };
}

export type GlobalSearchDialogProps = {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSelect?: (item: GlobalSearchItem) => void;
	defaultValues?: {
		search?: string;
		type?: string[];
	};
	defaultState?: GlobalSearchItem[];
	/**
	 * iter-10 Round F: when set, the palette acts as an entity picker for a
	 * backlinks sidebar. Result-items fire `onLinkPick` instead of navigating.
	 */
	linkMode?: PaletteLinkMode | null;
	onLinkPick?: (item: GlobalSearchItem) => void;
};

// Maps the link-mode entity scope to the type filter the global-search
// endpoint understands. Skills + agents don't have first-class result items
// today, so we widen to library/all (the result list still surfaces them).
const LINK_MODE_TYPE_FILTER: Record<PaletteLinkMode["entity"], string[]> = {
	prompts: ["prompt"],
	agents: ["library"],
	knowledge: ["knowledge", "document"],
	skills: ["library"],
	documents: ["document"],
};

const LINK_MODE_DEFAULT_TAB: Record<PaletteLinkMode["entity"], TabId> = {
	prompts: "prompts",
	agents: "all",
	knowledge: "documents",
	skills: "all",
	documents: "documents",
};

export const GlobalSearchDialog = ({
	open,
	onOpenChange,
	onSelect,
	defaultValues,
	defaultState = defaultSearchState,
	linkMode = null,
	onLinkPick,
}: GlobalSearchDialogProps) => {
	const user = useUser();
	const [search, setSearch] = useState(defaultValues?.search || "");
	const [activeTab, setActiveTab] = useState<TabId>(
		linkMode ? LINK_MODE_DEFAULT_TAB[linkMode.entity] : "all",
	);
	const [recent, setRecent] = useState<GlobalSearchItem[]>([]);
	const [debouncedSearch] = useDebounceValue(search, 300);

	// Reset the tab whenever link-mode changes so the picker always opens
	// on the right scope.
	useEffect(() => {
		if (linkMode) setActiveTab(LINK_MODE_DEFAULT_TAB[linkMode.entity]);
	}, [linkMode]);

	// Refresh recent items each time the dialog opens. localStorage is the
	// source of truth — multiple tabs would otherwise see stale lists.
	useEffect(() => {
		if (open) {
			setRecent(loadRecent());
		}
	}, [open]);

	// Reserved prefix → tab inference. `>` forces Settings (navigation entries
	// land there); `/` forces Actions.
	const parsed = useMemo(() => parsePrefix(debouncedSearch), [debouncedSearch]);
	useEffect(() => {
		if (parsed.mode === "navigation" && activeTab !== "settings") {
			setActiveTab("settings");
		} else if (parsed.mode === "action" && activeTab !== "actions") {
			setActiveTab("actions");
		}
	}, [parsed.mode, activeTab]);

	// Resolve the effective type-filter from active tab + caller defaults.
	// In link mode the caller's scope wins so the picker doesn't show
	// out-of-scope entities (e.g. tasks while linking prompts to a project).
	const effectiveTypes = useMemo(() => {
		if (linkMode) return LINK_MODE_TYPE_FILTER[linkMode.entity];
		const tabDef = TAB_DEFS.find((t) => t.id === activeTab);
		if (defaultValues?.type) return defaultValues.type;
		if (!tabDef || !tabDef.types) return undefined;
		// Drop the synthetic '__action__' marker — server doesn't know it.
		const filtered = tabDef.types.filter((t) => t !== "__action__");
		return filtered.length > 0 ? filtered : undefined;
	}, [activeTab, defaultValues?.type, linkMode]);

	// Skip the network round-trip in action-only mode. The Actions catalogue
	// is local and small; querying server search for `/new task` would waste
	// the request and burn latency.
	const isActionsOnly = activeTab === "actions";

	const { data } = useQuery({
		...trpc.globalSearch.search.queryOptions({
			search: parsed.query,
			type: effectiveTypes,
		}),
		enabled: !isActionsOnly,
	});

	const groupedData = useMemo(() => {
		const dataToGroup = data as GlobalSearchItem[] | undefined;
		const showEmptyState =
			defaultState && defaultState.length > 0 && parsed.query.length === 0;

		const grouped =
			dataToGroup?.reduce(
				(acc, item) => {
					if (!acc[item.type]) {
						acc[item.type] = [];
					}
					acc[item.type]!.push(item);
					return acc;
				},
				{} as Record<string, GlobalSearchItem[]>,
			) ?? {};

		if (showEmptyState) {
			// slice to avoid too many items when showing empty state
			for (const key in grouped) {
				if (grouped[key]!.length > 1) {
					grouped[key] = grouped[key]!.slice(0, 5);
				}
			}
		}

		for (const item of defaultState) {
			// Fill missing types with default state items
			if (!grouped?.[item.type]) {
				grouped![item.type] = [];
			}

			if (showEmptyState) {
				// include all default state items if no search is applied
				grouped[item.type]!.push(item);
			} else {
				// include default state items that match the search
				const shouldInclude = item.title
					.toLowerCase()
					.includes(parsed.query.toLowerCase());
				if (shouldInclude) grouped[item.type]?.push(item);
			}
		}

		return grouped;
	}, [data, parsed.query, defaultState]);

	// Track the last command-style invocation so we can surface it as a
	// "Repeat: <…>" affordance at the top of the Actions tab (codex
	// delighter #8). The hook subscribes to in-tab + cross-tab updates.
	const lastCommand = useLastCommand();

	// Actions tab / action-prefix mode → render the local ACTIONS catalogue
	// (filtered by the parsed query) instead of server results.
	//
	// When there's a remembered last-command we prepend a synthetic
	// "Repeat: <…>" entry (id `action:repeat-last`) so the user can re-fire
	// it with a single keystroke from inside the palette. The repeat row
	// only shows when the input is empty — once the user types we get out
	// of the way.
	const actionMatches = useMemo(() => {
		const q = parsed.query.toLowerCase();
		const base = q
			? ACTIONS.filter((a) => a.title.toLowerCase().includes(q))
			: ACTIONS;
		if (!lastCommand || q) return base;
		const target = findActionById(lastCommand.id);
		if (!target) return base;
		const repeatRow: GlobalSearchItem = {
			id: "action:repeat-last",
			type: target.type,
			title: `Repeat: ${target.title}`,
			teamId: target.teamId,
			href: target.href,
		};
		// De-duplicate — if the underlying action would also appear in `base`,
		// we still keep both so the user can see what the repeat refers to.
		return [repeatRow, ...base];
	}, [parsed.query, lastCommand]);

	const orderedEntries = useMemo(() => {
		if (isActionsOnly || parsed.mode === "action") {
			return [["action", actionMatches]] as ReadonlyArray<
				readonly [string, GlobalSearchItem[]]
			>;
		}
		const known = SECTION_ORDER.filter((key) => groupedData[key]).map(
			(key) => [key, groupedData[key]!] as const,
		);
		const unknown = Object.entries(groupedData).filter(
			([key]) => !(SECTION_ORDER as readonly string[]).includes(key),
		);
		return [...known, ...unknown];
	}, [groupedData, isActionsOnly, parsed.mode, actionMatches]);

	const handleOpenChange = (isOpen: boolean) => {
		if (!isOpen) {
			setSearch("");
			setActiveTab("all");
		}
		onOpenChange(isOpen);
	};

	// Wrap the existing onSelect (or default close) with the recent-items
	// persistence side-effect so every successful pick gets recorded.
	//
	// Command-style picks (id `action:*`) also flow through `recordCommand`
	// so the Cmd+. "repeat last" shortcut has something to fire (codex
	// delighter #8). Entity picks stay on the recent-items list only.
	const recordRecent = useCallback((item: GlobalSearchItem) => {
		if (isCommandItem(item)) {
			recordCommand(item);
			return;
		}
		if (item.id.startsWith("navigate:")) {
			// Nav rows aren't entities — don't pollute the recent list.
			return;
		}
		persistRecent(item);
	}, []);

	const handleItemOpenChange = useCallback(
		onSelect
			? (open: boolean) => {
					if (!open) {
						// When using custom onSelect, we don't close automatically
					}
				}
			: handleOpenChange,
		[],
	);

	return (
		<Dialog open={open} onOpenChange={handleOpenChange}>
			<DialogContent
				className="h-[calc(100vh-8rem)] p-0 transition-all duration-200 sm:max-w-6xl"
				showCloseButton={false}
			>
				<DialogHeader className="hidden">
					<DialogTitle />
				</DialogHeader>
				<GlobalSearchProvider
					onOpenChange={handleItemOpenChange}
					onSelectItem={recordRecent}
					basePath={user?.basePath || ""}
					linkMode={linkMode}
					onLinkPick={onLinkPick}
				>
					<GlobalSearchContent
						search={search}
						setSearch={setSearch}
						orderedEntries={orderedEntries}
						activeTab={activeTab}
						setActiveTab={setActiveTab}
						recent={recent}
						parsedQuery={parsed.query}
						linkMode={linkMode}
					/>
				</GlobalSearchProvider>
			</DialogContent>
		</Dialog>
	);
};

const GlobalSearchContent = ({
	search,
	setSearch,
	orderedEntries,
	activeTab,
	setActiveTab,
	recent,
	parsedQuery,
	linkMode,
}: {
	search: string;
	setSearch: (search: string) => void;
	orderedEntries: ReadonlyArray<readonly [string, GlobalSearchItem[]]>;
	activeTab: TabId;
	setActiveTab: (tab: TabId) => void;
	recent: GlobalSearchItem[];
	parsedQuery: string;
	linkMode: PaletteLinkMode | null;
}) => {
	const { preview } = useGlobalSearch();
	const hasPreview = preview !== null;

	const showRecent = recent.length > 0 && parsedQuery.length === 0;

	return (
		<div className="flex h-full" data-has-preview={hasPreview}>
			<div
				className={cn(
					"flex h-full flex-1 flex-col p-4",
					hasPreview && "border-border border-r",
				)}
			>
				<div className="-mt-1 mb-2 flex items-center gap-1 overflow-x-auto pb-1">
					{TAB_DEFS.map((tab) => {
						const active = activeTab === tab.id;
						return (
							<button
								key={tab.id}
								type="button"
								onClick={() => setActiveTab(tab.id)}
								className={cn(
									"shrink-0 rounded-md px-2.5 py-1 text-[11.5px] tracking-[-0.005em] transition-colors",
									active
										? "bg-foreground/[0.08] font-[510] text-foreground"
										: "text-muted-foreground hover:text-foreground",
								)}
							>
								{tab.label}
							</button>
						);
					})}
				</div>
				{linkMode && (
					<div className="mb-2 rounded-md border border-brand/30 bg-brand/5 px-3 py-2 text-[12px] text-brand">
						Picking {linkMode.entity} to link to this {linkMode.sourceType}
					</div>
				)}
				<Command shouldFilter={false} className="h-full bg-transparent">
					<CommandInput
						value={search}
						onValueChange={setSearch}
						containerClassName="h-11"
						placeholder={
							linkMode
								? `Search ${linkMode.entity} to link…`
								: 'Search… (try "> settings" or "/ new task")'
						}
					/>
					<CommandList className="max-h-[calc(100vh-18rem)] overflow-y-auto">
						{showRecent && (
							<CommandGroup
								heading="Recent"
								className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.04em]"
							>
								{recent.map((item) => (
									<SearchResultItem key={`recent-${item.id}`} item={item} />
								))}
							</CommandGroup>
						)}
						{orderedEntries.map(([type, items]) => {
							if (!items || items.length === 0) {
								return null;
							}
							return (
								<CommandGroup
									key={type}
									heading={
										SECTION_LABELS[type] ??
										(type === "action" ? "Actions" : type)
									}
									className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-[11px] [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-[0.04em]"
								>
									{items?.map((item) => (
										<SearchResultItem key={item.id} item={item} />
									))}
								</CommandGroup>
							);
						})}
					</CommandList>
				</Command>
				<DialogFooter className="mt-auto flex justify-between px-2 pt-2">
					<div className="text-[11px] text-muted-foreground">
						<span className="font-mono">&gt;</span> nav ·{" "}
						<span className="font-mono">/</span> actions · tab to switch
					</div>
					<div className="flex items-center gap-4 text-muted-foreground">
						<ArrowDownIcon className="size-4" />
						<ArrowUpIcon className="size-4" />
						<CornerDownLeftIcon className="size-4" />
					</div>
				</DialogFooter>
			</div>
			{hasPreview && (
				<div className="w-xl p-4">
					<div className="max-h-[calc(100vh-10rem)] overflow-y-auto">
						{preview}
					</div>
				</div>
			)}
		</div>
	);
};

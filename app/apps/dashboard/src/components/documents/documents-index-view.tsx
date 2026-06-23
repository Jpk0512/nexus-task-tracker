"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import { Input } from "@ui/components/ui/input";
import { LabelBadge } from "@ui/components/ui/label-badge";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { cn } from "@ui/lib/utils";
import {
	ArrowRightIcon,
	BookOpenIcon,
	BrainIcon,
	CalendarDaysIcon,
	ChevronRightIcon,
	ClockIcon,
	FileTextIcon,
	FilterIcon,
	FolderIcon,
	GlobeIcon,
	LayoutGridIcon,
	LayoutListIcon,
	ListTreeIcon,
	type LucideIcon,
	PlusIcon,
	SearchIcon,
	TagsIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { DocumentIcon } from "@/components/documents/document-icon";
import { JkHint } from "@/components/jk-hint";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { IS_SINGLE_USER_MODE } from "@/lib/single-user-mode";
import { trpc } from "@/utils/trpc";

// Documents index — iter-10 Round B redesign.
//
//   - Scope toggle (All / Team / Personal): merges knowledge-vault notes
//     into the Documents surface so users have one place to find any
//     written artifact. Team = current behaviour (Drizzle documents only).
//     Personal = knowledge-vault notes. All = both, sorted by updatedAt.
//
//   - View-mode toggle (List / Grouped / Cards): three layouts over the
//     same underlying rows.
//
//   - Group-by selector (None / Category / Owner / Recency): when view-mode
//     is Grouped, items bucket by the chosen key.
//
//   - Single-user-mode: multi-user affordances (Owner chips, member lists)
//     are hidden when IS_SINGLE_USER_MODE — see lib/single-user-mode.ts.
//
// State precedence (codex amendment #3): URL param > localStorage > default.
// URL only wins when explicitly present in the address; localStorage
// remembers the user's last manual choice across sessions; default fires
// only on first visit.

// -------- Types ---------------------------------------------------------

type Scope = "all" | "team" | "personal";
type ViewMode = "list" | "grouped" | "cards";
type GroupBy = "none" | "category" | "owner" | "recency";
type UpdatedFilter = "all" | "today" | "week" | "month";
type IconComponent = LucideIcon;

type DocumentLabel = {
	id: string;
	name: string;
	color: string | null;
};

type DocRow = {
	id: string;
	name: string | null;
	icon?: string | null;
	projectId: string | null;
	projectName?: string | null;
	parentId?: string | null;
	updatedAt?: string | Date;
	labels?: DocumentLabel[] | null;
	// "team" = Drizzle-backed document; "personal" = knowledge-vault note.
	// Used by the scope filter and to route to the right detail page.
	source: "team" | "personal";
	// Only present for personal notes — vault-relative path so we can route
	// the click to the Knowledge tab.
	knowledgeRelativePath?: string | null;
};

// -------- Persistence helpers -------------------------------------------

const SCOPE_KEY = "nexus.documents.scope";
const VIEW_MODE_KEY = "nexus.documents.viewMode";
const GROUP_BY_KEY = "nexus.documents.groupBy";
const PROJECT_FILTER_KEY = "nexus.documents.projectFilter";
const LABEL_FILTER_KEY = "nexus.documents.labelFilter";
const UPDATED_FILTER_KEY = "nexus.documents.updatedFilter";

function readStored<T extends string>(
	key: string,
	allowed: readonly T[],
	fallback: T,
): T {
	if (typeof window === "undefined") return fallback;
	try {
		const v = window.localStorage.getItem(key);
		if (v && (allowed as readonly string[]).includes(v)) return v as T;
	} catch {
		// localStorage can throw in private-browsing / quota-exceeded modes —
		// we just fall back to the default rather than break the page.
	}
	return fallback;
}

function readStoredString(key: string, fallback: string): string {
	if (typeof window === "undefined") return fallback;
	try {
		return window.localStorage.getItem(key) || fallback;
	} catch {
		return fallback;
	}
}

function writeStored(key: string, value: string) {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(key, value);
	} catch {
		// see readStored — same swallow.
	}
}

function formatDate(value?: string | Date | null): string {
	if (!value) return "No edits yet";
	return new Date(value).toLocaleDateString(undefined, {
		month: "short",
		day: "numeric",
		year:
			new Date(value).getFullYear() === new Date().getFullYear()
				? undefined
				: "numeric",
	});
}

function updatedFilterMatches(
	value: string | Date | undefined,
	filter: UpdatedFilter,
): boolean {
	if (filter === "all") return true;
	if (!value) return false;
	const ts = new Date(value).getTime();
	if (!Number.isFinite(ts)) return false;
	const ageDays = (Date.now() - ts) / (24 * 60 * 60 * 1000);
	if (filter === "today") return ageDays < 1;
	if (filter === "week") return ageDays < 7;
	return ageDays < 30;
}

function getDocHref(doc: DocRow, team: string): string {
	return doc.source === "personal"
		? `/team/${team}/knowledge?note=${encodeURIComponent(doc.id)}`
		: `/team/${team}/documents/${doc.id}`;
}

// -------- Pagination ----------------------------------------------------

// Codex amendment #7 — performance budget. Groups paginate at 50 with
// "Show more" reveal. 50 is enough for any realistic single-user vault
// without slowing down React reconciliation on the initial render.
const GROUP_PAGE = 50;

// -------- Group-section primitive --------------------------------------

function GroupSection({
	icon: Icon,
	title,
	count,
	docs,
	team,
	defaultOpen = true,
	focusedId,
	viewMode,
	onPreview,
}: {
	icon: IconComponent;
	title: string;
	count: number;
	docs: DocRow[];
	team: string;
	defaultOpen?: boolean;
	focusedId?: string | null;
	viewMode: ViewMode;
	onPreview?: (id: string | null) => void;
}) {
	const [visibleCount, setVisibleCount] = useState(GROUP_PAGE);
	const visible = docs.slice(0, visibleCount);
	const hasMore = docs.length > visibleCount;

	return (
		<Collapsible
			defaultOpen={defaultOpen}
			className="border-border/60 border-b"
		>
			<CollapsibleTrigger className="group sticky top-0 z-10 flex w-full items-center gap-2 bg-card px-4 py-2 text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground [&[data-state=open]>svg]:rotate-90">
				<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
				<Icon className="size-3.5 shrink-0" />
				<span className="font-[510] uppercase tracking-[0.04em]">{title}</span>
				<Badge variant="outline" className="ml-1 h-4 px-1.5 font-normal">
					{count}
				</Badge>
			</CollapsibleTrigger>
			<CollapsibleContent>
				{visible.length === 0 ? (
					<div className="px-10 py-2 text-[12px] text-muted-foreground italic">
						Nothing here yet.
					</div>
				) : viewMode === "cards" ? (
					<div className="grid grid-cols-1 gap-2 px-6 py-2 sm:grid-cols-2 xl:grid-cols-3">
						{visible.map((d) => (
							<DocCard
								key={d.id}
								doc={d}
								team={team}
								focused={focusedId === d.id}
								onPreview={onPreview}
							/>
						))}
					</div>
				) : (
					<ul className="pb-2">
						{visible.map((d) => (
							<li key={d.id} data-jk-row={d.id}>
								<DocListItem
									doc={d}
									team={team}
									focused={focusedId === d.id}
									onPreview={onPreview}
								/>
							</li>
						))}
					</ul>
				)}
				{hasMore && (
					<div className="px-10 py-2">
						<button
							type="button"
							onClick={() => setVisibleCount((v) => v + GROUP_PAGE)}
							className="text-[12px] text-muted-foreground transition-colors hover:text-foreground"
						>
							Show {Math.min(GROUP_PAGE, docs.length - visibleCount)} more
						</button>
					</div>
				)}
			</CollapsibleContent>
		</Collapsible>
	);
}

function DocListItem({
	doc,
	team,
	focused,
	onPreview,
}: {
	doc: DocRow;
	team: string;
	focused: boolean;
	onPreview?: (id: string | null) => void;
}) {
	const href = getDocHref(doc, team);
	const labels = doc.labels ?? [];
	return (
		<Link
			href={href}
			onMouseEnter={() => onPreview?.(doc.id)}
			onMouseLeave={() => onPreview?.(null)}
			onFocus={() => onPreview?.(doc.id)}
			onBlur={() => onPreview?.(null)}
			className={cn(
				"grid grid-cols-[minmax(0,1.5fr)_minmax(120px,0.8fr)_auto] items-center gap-4 rounded-md border border-transparent px-3 py-2 text-[13px] text-foreground transition-colors hover:border-border/70 hover:bg-accent/35 focus-visible:border-violet-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40",
				focused && "ring-2 ring-violet-400/40 ring-inset",
			)}
		>
			<span className="flex min-w-0 items-center gap-2">
				{doc.source === "personal" ? (
					<BrainIcon className="size-3.5 shrink-0 text-violet-500" />
				) : (
					<DocumentIcon
						icon={doc.icon}
						className="size-3.5 shrink-0"
						hasChildren={false}
					/>
				)}
				<span className="min-w-0">
					<span className="block truncate font-[510] tracking-[-0.005em]">
						{doc.name || "Untitled"}
					</span>
					<span className="block truncate text-[11px] text-muted-foreground/80">
						{doc.source === "personal"
							? doc.knowledgeRelativePath || "Knowledge vault"
							: doc.projectName || "Team-wide"}
					</span>
				</span>
			</span>
			<span className="hidden min-w-0 items-center gap-1.5 md:flex">
				{labels.slice(0, 2).map((label) => (
					<LabelBadge
						key={label.id}
						name={label.name}
						color={label.color ?? undefined}
						className="h-[18px] max-w-24 truncate px-1.5 font-normal text-[10px]"
					/>
				))}
				{labels.length > 2 && (
					<span className="text-[11px] text-muted-foreground">
						+{labels.length - 2}
					</span>
				)}
			</span>
			<span className="text-right text-[11px] text-muted-foreground">
				{formatDate(doc.updatedAt)}
			</span>
		</Link>
	);
}

function DocCard({
	doc,
	team,
	focused,
	onPreview,
}: {
	doc: DocRow;
	team: string;
	focused: boolean;
	onPreview?: (id: string | null) => void;
}) {
	const href = getDocHref(doc, team);
	const labels = doc.labels ?? [];
	return (
		<Link
			href={href}
			onMouseEnter={() => onPreview?.(doc.id)}
			onMouseLeave={() => onPreview?.(null)}
			onFocus={() => onPreview?.(doc.id)}
			onBlur={() => onPreview?.(null)}
			className={cn(
				"flex min-h-[132px] flex-col gap-3 rounded-md border border-border bg-card p-3 transition-colors hover:border-border/80 hover:bg-accent/30 focus-visible:border-violet-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40",
				focused && "ring-2 ring-violet-400/40",
			)}
		>
			<div className="flex items-center gap-2 text-[13px] text-foreground">
				{doc.source === "personal" ? (
					<BrainIcon className="size-3.5 shrink-0 text-violet-500" />
				) : (
					<DocumentIcon
						icon={doc.icon}
						className="size-3.5 shrink-0"
						hasChildren={false}
					/>
				)}
				<span className="truncate font-[510] tracking-[-0.005em]">
					{doc.name || "Untitled"}
				</span>
			</div>
			<div className="flex items-center justify-between text-[11px] text-muted-foreground">
				<span className="truncate">
					{doc.source === "personal"
						? doc.knowledgeRelativePath || "Knowledge vault"
						: doc.projectName || "Team-wide"}
				</span>
				<span className="shrink-0">{formatDate(doc.updatedAt)}</span>
			</div>
			<div className="mt-auto flex min-h-5 flex-wrap items-center gap-1.5">
				{labels.slice(0, 3).map((label) => (
					<LabelBadge
						key={label.id}
						name={label.name}
						color={label.color ?? undefined}
						className="h-[18px] max-w-24 truncate px-1.5 font-normal text-[10px]"
					/>
				))}
			</div>
		</Link>
	);
}

function DocumentsPreviewRail({
	doc,
	team,
}: {
	doc: DocRow | null;
	team: string;
}) {
	const documentQuery = useQuery({
		...trpc.documents.getById.queryOptions({ id: doc?.id ?? "" }),
		enabled: doc?.source === "team",
		staleTime: 30_000,
	});
	const noteQuery = useQuery({
		...trpc.knowledge.getById.queryOptions({ id: doc?.id ?? "" }),
		enabled: doc?.source === "personal",
		staleTime: 30_000,
	});

	if (!doc) {
		return (
			<aside className="flex h-full w-[336px] flex-col rounded-md border border-border/60 bg-card/30 p-4">
				<div className="flex items-center gap-2 text-[12px] text-muted-foreground">
					<FileTextIcon className="size-3.5" />
					<span>Hover or focus a document to preview it here.</span>
				</div>
			</aside>
		);
	}

	const href = getDocHref(doc, team);
	const isPersonal = doc.source === "personal";
	const isLoading =
		(isPersonal && noteQuery.isFetching && !noteQuery.data) ||
		(!isPersonal && documentQuery.isFetching && !documentQuery.data);
	const content = isPersonal
		? (noteQuery.data?.content ?? "")
		: (documentQuery.data?.content ?? "");
	const labels = isPersonal
		? []
		: (documentQuery.data?.labels ?? doc.labels ?? []);
	const preview = content.trim()
		? content.trim().slice(0, 900)
		: "No preview content yet.";
	const truncated = content.trim().length > 900;

	return (
		<aside className="flex h-full w-[336px] flex-col overflow-hidden rounded-md border border-border/60 bg-card/40">
			<div className="border-border/60 border-b p-4">
				<div className="flex items-start gap-2">
					<div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-accent/70">
						{isPersonal ? (
							<BrainIcon className="size-3.5 text-violet-500" />
						) : (
							<DocumentIcon
								icon={doc.icon}
								className="size-3.5"
								hasChildren={false}
							/>
						)}
					</div>
					<div className="min-w-0 grow">
						<h2 className="truncate font-[510] text-[14px] text-foreground tracking-[-0.01em]">
							{doc.name || "Untitled"}
						</h2>
						<p className="mt-1 truncate text-[11px] text-muted-foreground">
							{isPersonal
								? doc.knowledgeRelativePath || "Knowledge vault"
								: doc.projectName || "Team-wide"}
						</p>
					</div>
				</div>
				<div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
					<div className="rounded-md border border-border/50 bg-background/40 p-2">
						<div className="text-muted-foreground">Source</div>
						<div className="mt-0.5 font-[510] text-foreground capitalize">
							{isPersonal ? "Personal" : "Team"}
						</div>
					</div>
					<div className="rounded-md border border-border/50 bg-background/40 p-2">
						<div className="text-muted-foreground">Updated</div>
						<div className="mt-0.5 font-[510] text-foreground">
							{formatDate(doc.updatedAt)}
						</div>
					</div>
				</div>
				{labels.length > 0 && (
					<div className="mt-3 flex flex-wrap gap-1.5">
						{labels.slice(0, 5).map((label) => (
							<LabelBadge
								key={label.id}
								name={label.name}
								color={label.color ?? undefined}
								className="h-[18px] max-w-28 truncate px-1.5 font-normal text-[10px]"
							/>
						))}
					</div>
				)}
			</div>
			<div className="min-h-0 grow overflow-y-auto p-4">
				{isLoading ? (
					<div className="space-y-2">
						<div className="h-2 w-3/4 animate-pulse rounded bg-muted/60" />
						<div className="h-2 w-full animate-pulse rounded bg-muted/60" />
						<div className="h-2 w-5/6 animate-pulse rounded bg-muted/60" />
						<div className="h-2 w-2/3 animate-pulse rounded bg-muted/60" />
					</div>
				) : (
					<pre className="whitespace-pre-wrap break-words font-sans text-[12px] text-foreground/80 leading-[1.55]">
						{preview}
						{truncated && <span className="text-muted-foreground/70">...</span>}
					</pre>
				)}
			</div>
			<div className="border-border/60 border-t p-3">
				<Link
					href={href}
					className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md bg-primary px-3 font-[510] text-[12px] text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
				>
					Open
					<ArrowRightIcon className="size-3.5" />
				</Link>
			</div>
		</aside>
	);
}

// -------- Empty-state --------------------------------------------------

const STARTER_TEMPLATES = [
	{
		key: "blank",
		title: "New blank doc",
		hint: "Start with a clean editor",
		icon: PlusIcon,
		content: "",
	},
	{
		key: "spec",
		title: "Project spec template",
		hint: "Problem / Goals / Scope / Open questions",
		icon: BookOpenIcon,
		content:
			"# Untitled spec\n\n## Problem\n\n## Goals\n\n## Non-goals\n\n## Scope\n\n## Open questions\n",
	},
	{
		key: "meeting",
		title: "Meeting notes template",
		hint: "Attendees / Agenda / Decisions / Action items",
		icon: ClockIcon,
		content:
			"# Meeting notes — \n\n## Attendees\n\n## Agenda\n\n## Decisions\n\n## Action items\n",
	},
] as const;

function DocumentsEmptyState({ team }: { team: string }) {
	const router = useRouter();
	// Templates ride on the existing /create route as a query param so we
	// don't need a new endpoint — the create page reads `template` and
	// pre-fills the editor.
	const start = (templateKey: string) => {
		router.push(
			`/team/${team}/documents/create${
				templateKey === "blank" ? "" : `?template=${templateKey}`
			}`,
		);
	};
	return (
		<div className="flex grow flex-col items-center justify-center gap-6 px-6 py-16 text-center">
			<div className="flex flex-col items-center gap-2">
				<FileTextIcon className="size-10 text-muted-foreground/60" />
				<p className="font-[510] text-[15px] tracking-[-0.012em]">
					No documents yet
				</p>
				<p className="max-w-md text-balance text-[12px] text-muted-foreground">
					Documents are the notes, specs, and references for your projects.
					Start with a template or a blank page.
				</p>
			</div>
			<div className="grid w-full max-w-2xl gap-3 sm:grid-cols-3">
				{STARTER_TEMPLATES.map((t) => (
					<button
						key={t.key}
						type="button"
						onClick={() => start(t.key)}
						className="flex flex-col items-start gap-2 rounded-md border border-border bg-card p-4 text-left transition-colors hover:border-border/80 hover:bg-accent/40"
					>
						<t.icon className="size-4 text-violet-500" />
						<div className="font-[510] text-[13px] tracking-[-0.005em]">
							{t.title}
						</div>
						<div className="text-[12px] text-muted-foreground">{t.hint}</div>
					</button>
				))}
			</div>
		</div>
	);
}

// -------- Segmented control --------------------------------------------

function Segmented<T extends string>({
	value,
	onChange,
	options,
	ariaLabel,
}: {
	value: T;
	onChange: (next: T) => void;
	options: ReadonlyArray<{ value: T; label: string; icon?: IconComponent }>;
	ariaLabel: string;
}) {
	return (
		<div
			role="radiogroup"
			aria-label={ariaLabel}
			className="inline-flex h-7 rounded-md border border-border bg-muted/40 p-0.5"
		>
			{options.map((opt) => {
				const active = opt.value === value;
				const Icon = opt.icon;
				return (
					<button
						key={opt.value}
						type="button"
						role="radio"
						aria-checked={active}
						onClick={() => onChange(opt.value)}
						className={cn(
							"inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2.5 font-[510] text-[12px] transition-colors",
							active
								? "bg-background text-foreground shadow-sm"
								: "text-muted-foreground hover:text-foreground",
						)}
					>
						{Icon && <Icon className="size-3.5" />}
						{opt.label}
					</button>
				);
			})}
		</div>
	);
}

// -------- Main view -----------------------------------------------------

export function DocumentsIndexView() {
	const { team } = useParams<{ team: string }>();
	const router = useRouter();
	const searchParams = useSearchParams();
	const [search, setSearch] = useState("");
	const [debouncedSearch] = useDebounceValue(search, 300);
	const [previewId, setPreviewId] = useState<string | null>(null);

	// State precedence — URL param > localStorage > default. URL only wins
	// when explicitly present so deep links can pin a view without
	// permanently overriding the user's stored preference.
	const urlScope = searchParams?.get("scope") as Scope | null;
	const urlView = searchParams?.get("view") as ViewMode | null;
	const urlGroup = searchParams?.get("groupBy") as GroupBy | null;

	const [scope, setScopeState] = useState<Scope>(() =>
		urlScope && ["all", "team", "personal"].includes(urlScope)
			? urlScope
			: readStored<Scope>(
					SCOPE_KEY,
					["all", "team", "personal"] as const,
					"all",
				),
	);
	const [viewMode, setViewModeState] = useState<ViewMode>(() =>
		urlView && ["list", "grouped", "cards"].includes(urlView)
			? urlView
			: readStored<ViewMode>(
					VIEW_MODE_KEY,
					["list", "grouped", "cards"] as const,
					"grouped",
				),
	);
	const [groupBy, setGroupByState] = useState<GroupBy>(() =>
		urlGroup && ["none", "category", "owner", "recency"].includes(urlGroup)
			? urlGroup
			: readStored<GroupBy>(
					GROUP_BY_KEY,
					["none", "category", "owner", "recency"] as const,
					"category",
				),
	);
	const [projectFilter, setProjectFilterState] = useState<string>(() =>
		readStoredString(PROJECT_FILTER_KEY, "all"),
	);
	const [labelFilter, setLabelFilterState] = useState<string>(() =>
		readStoredString(LABEL_FILTER_KEY, "all"),
	);
	const [updatedFilter, setUpdatedFilterState] = useState<UpdatedFilter>(() =>
		readStored<UpdatedFilter>(
			UPDATED_FILTER_KEY,
			["all", "today", "week", "month"] as const,
			"all",
		),
	);

	// Persist on change. Wrappers keep call-sites readable and ensure the
	// localStorage write happens in lockstep with the state update.
	const setScope = (s: Scope) => {
		setScopeState(s);
		writeStored(SCOPE_KEY, s);
	};
	const setViewMode = (v: ViewMode) => {
		setViewModeState(v);
		writeStored(VIEW_MODE_KEY, v);
	};
	const setGroupBy = (g: GroupBy) => {
		setGroupByState(g);
		writeStored(GROUP_BY_KEY, g);
	};
	const setProjectFilter = (projectId: string) => {
		setProjectFilterState(projectId);
		writeStored(PROJECT_FILTER_KEY, projectId);
	};
	const setLabelFilter = (labelId: string) => {
		setLabelFilterState(labelId);
		writeStored(LABEL_FILTER_KEY, labelId);
	};
	const setUpdatedFilter = (next: UpdatedFilter) => {
		setUpdatedFilterState(next);
		writeStored(UPDATED_FILTER_KEY, next);
	};

	// Re-sync from URL when params change after mount (e.g. deep-link nav
	// within the SPA). Only applies when the param is actually present so
	// it can never quietly clobber a stored preference.
	useEffect(() => {
		if (
			urlScope &&
			["all", "team", "personal"].includes(urlScope) &&
			urlScope !== scope
		) {
			setScopeState(urlScope);
		}
		if (
			urlView &&
			["list", "grouped", "cards"].includes(urlView) &&
			urlView !== viewMode
		) {
			setViewModeState(urlView);
		}
		if (
			urlGroup &&
			["none", "category", "owner", "recency"].includes(urlGroup) &&
			urlGroup !== groupBy
		) {
			setGroupByState(urlGroup);
		}
	}, [urlScope, urlView, urlGroup, scope, viewMode, groupBy]);

	// Team documents (Drizzle-backed). Skip when scope=personal to avoid an
	// unused network round-trip.
	const { data: docsPage } = useQuery({
		...trpc.documents.get.queryOptions({
			pageSize: 100,
			tree: false,
			...(debouncedSearch ? { search: debouncedSearch } : {}),
		}),
		enabled: scope !== "personal",
	});

	// Knowledge-vault notes. Skip when scope=team. Search piggy-backs on the
	// same debounced query so both surfaces filter in sync.
	const { data: notesPage } = useQuery({
		...trpc.knowledge.get.queryOptions({
			...(debouncedSearch ? { search: debouncedSearch } : {}),
		}),
		enabled: scope !== "team",
	});

	const { data: projects } = useQuery(
		trpc.projects.get.queryOptions({ pageSize: 100 }),
	);

	const projectsById = useMemo(() => {
		const out = new Map<string, { id: string; name: string }>();
		const items = (projects?.data ?? []) as Array<{ id: string; name: string }>;
		for (const p of items) out.set(p.id, p);
		return out;
	}, [projects]);

	const teamDocs = useMemo<DocRow[]>(() => {
		if (scope === "personal") return [];
		const items = (docsPage?.data ?? []) as Array<{
			id: string;
			name: string | null;
			icon?: string | null;
			projectId: string | null;
			parentId?: string | null;
			updatedAt?: string | Date;
			labels?: DocumentLabel[] | null;
		}>;
		return items.map((d) => ({
			id: d.id,
			name: d.name,
			icon: d.icon,
			projectId: d.projectId,
			projectName: d.projectId ? projectsById.get(d.projectId)?.name : null,
			parentId: d.parentId,
			updatedAt: d.updatedAt,
			labels: d.labels ?? [],
			source: "team" as const,
		}));
	}, [docsPage, scope, projectsById]);

	const personalNotes = useMemo<DocRow[]>(() => {
		if (scope === "team") return [];
		const items = (notesPage?.notes ?? []) as Array<{
			id: string;
			name: string;
			relativePath: string;
			parentDir: string | null;
			updatedAt: string;
		}>;
		return items.map(
			(n): DocRow => ({
				id: n.id,
				name: n.name,
				projectId: null,
				updatedAt: n.updatedAt,
				source: "personal" as const,
				knowledgeRelativePath: n.relativePath,
			}),
		);
	}, [notesPage, scope]);

	const docs = useMemo<DocRow[]>(() => {
		// Merge + sort by updatedAt desc so the union view feels like one
		// timeline rather than two stacked lists.
		const merged = [...teamDocs, ...personalNotes].filter((d) => {
			if (
				projectFilter !== "all" &&
				(d.source !== "team" ||
					(projectFilter === "team-wide"
						? d.projectId !== null
						: d.projectId !== projectFilter))
			) {
				return false;
			}
			if (
				labelFilter !== "all" &&
				(d.source !== "team" ||
					!(d.labels ?? []).some((label) => label.id === labelFilter))
			) {
				return false;
			}
			return updatedFilterMatches(d.updatedAt, updatedFilter);
		});
		return merged.sort((a, b) => {
			const at = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
			const bt = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
			return bt - at;
		});
	}, [teamDocs, personalNotes, projectFilter, labelFilter, updatedFilter]);

	const labelOptions = useMemo(() => {
		const out = new Map<string, DocumentLabel>();
		for (const doc of teamDocs) {
			for (const label of doc.labels ?? []) {
				out.set(label.id, label);
			}
		}
		return Array.from(out.values()).sort((a, b) =>
			a.name.localeCompare(b.name),
		);
	}, [teamDocs]);

	const previewDoc = useMemo(
		() => docs.find((d) => d.id === previewId) ?? docs[0] ?? null,
		[docs, previewId],
	);

	const jkIds = useMemo(() => docs.map((d) => d.id), [docs]);
	const docById = useMemo(() => {
		const m = new Map<string, (typeof docs)[number]>();
		for (const d of docs) m.set(d.id, d);
		return m;
	}, [docs]);
	const jk = useJkNavigation({
		ids: jkIds,
		onOpen: (id) => {
			const d = docById.get(id);
			if (!d) return;
			if (d.source === "personal") {
				router.push(`/team/${team}/knowledge?note=${encodeURIComponent(d.id)}`);
			} else {
				router.push(`/team/${team}/documents/${d.id}`);
			}
		},
		toastLabel: (id) => {
			const d = docById.get(id) as
				| { name?: string; title?: string }
				| undefined;
			if (!d) return null;
			return `Opened ${d.name ?? d.title ?? "document"}`;
		},
	});

	// -------- Grouping --------------------------------------------------

	type Group = {
		key: string;
		title: string;
		icon: IconComponent;
		docs: DocRow[];
	};

	const groups = useMemo<Group[]>(() => {
		if (viewMode !== "grouped") return [];
		const out: Group[] = [];

		if (groupBy === "none") {
			out.push({
				key: "all",
				title: "All",
				icon: ListTreeIcon,
				docs,
			});
			return out;
		}

		if (groupBy === "category") {
			const byProject = new Map<string, DocRow[]>();
			const teamWide: DocRow[] = [];
			const personal: DocRow[] = [];
			for (const d of docs) {
				if (d.source === "personal") {
					personal.push(d);
				} else if (d.projectId && projectsById.has(d.projectId)) {
					const arr = byProject.get(d.projectId) ?? [];
					arr.push(d);
					byProject.set(d.projectId, arr);
				} else {
					teamWide.push(d);
				}
			}
			for (const [projectId, group] of byProject) {
				const project = projectsById.get(projectId)!;
				out.push({
					key: `project:${projectId}`,
					title: project.name,
					icon: FolderIcon,
					docs: group,
				});
			}
			if (teamWide.length > 0) {
				out.push({
					key: "team-wide",
					title: "Team-wide",
					icon: GlobeIcon,
					docs: teamWide,
				});
			}
			if (personal.length > 0) {
				out.push({
					key: "personal",
					title: "Personal (Knowledge vault)",
					icon: BrainIcon,
					docs: personal,
				});
			}
			return out;
		}

		if (groupBy === "owner") {
			// Single-user mode collapses the owner dimension — there is one
			// actor. Show a single "You" bucket so the UI doesn't lie.
			if (IS_SINGLE_USER_MODE) {
				return [
					{
						key: "you",
						title: "You",
						icon: ListTreeIcon,
						docs,
					},
				];
			}
			// Multi-user owner-grouping is deferred — the documents.get
			// response does not yet include creator metadata in the list
			// payload. Fall back to a single bucket rather than ship a
			// half-working group.
			return [
				{
					key: "all",
					title: "All",
					icon: ListTreeIcon,
					docs,
				},
			];
		}

		// recency
		const today: DocRow[] = [];
		const week: DocRow[] = [];
		const earlier: DocRow[] = [];
		const now = Date.now();
		const ONE_DAY = 24 * 60 * 60 * 1000;
		for (const d of docs) {
			const ts = d.updatedAt ? new Date(d.updatedAt).getTime() : 0;
			const ageDays = (now - ts) / ONE_DAY;
			if (!ts) earlier.push(d);
			else if (ageDays < 1) today.push(d);
			else if (ageDays < 7) week.push(d);
			else earlier.push(d);
		}
		if (today.length > 0) {
			out.push({ key: "today", title: "Today", icon: ClockIcon, docs: today });
		}
		if (week.length > 0) {
			out.push({
				key: "week",
				title: "This week",
				icon: ClockIcon,
				docs: week,
			});
		}
		if (earlier.length > 0) {
			out.push({
				key: "earlier",
				title: "Earlier",
				icon: ClockIcon,
				docs: earlier,
			});
		}
		return out;
	}, [viewMode, groupBy, docs, projectsById]);

	// -------- Render ----------------------------------------------------

	const hasActiveFilters =
		Boolean(debouncedSearch) ||
		projectFilter !== "all" ||
		labelFilter !== "all" ||
		updatedFilter !== "all";
	const isEmpty =
		teamDocs.length + personalNotes.length === 0 && !hasActiveFilters;

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-4">
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[18px] text-foreground tracking-[-0.012em]">
							Documents
						</h1>
						<p className="mt-1 max-w-2xl text-[12px] text-muted-foreground">
							{scope === "personal"
								? "Personal notes from your Knowledge vault."
								: scope === "team"
									? "Shared docs grouped by project."
									: "All your written work — team docs and personal notes."}
						</p>
					</div>
					<div className="flex items-center gap-3">
						<JkHint />
						<Link
							href={`/team/${team}/documents/create`}
							className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 font-[510] text-[12px] text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
						>
							<PlusIcon className="size-3.5" />
							New
						</Link>
					</div>
				</div>

				{/* Scope + view-mode + group-by toolbar. */}
				<div className="mt-4 flex flex-wrap items-center gap-2">
					<div className="relative mr-1 w-full max-w-sm sm:w-72">
						<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2.5 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="Search documents…"
							className="h-8 pl-8 text-[12px]"
						/>
					</div>
					<Segmented<Scope>
						ariaLabel="Document scope"
						value={scope}
						onChange={setScope}
						options={[
							{ value: "all", label: "All" },
							{ value: "team", label: "Team" },
							{ value: "personal", label: "Personal" },
						]}
					/>
					<Segmented<ViewMode>
						ariaLabel="View mode"
						value={viewMode}
						onChange={setViewMode}
						options={[
							{ value: "list", label: "List", icon: LayoutListIcon },
							{ value: "grouped", label: "Grouped", icon: ListTreeIcon },
							{ value: "cards", label: "Cards", icon: LayoutGridIcon },
						]}
					/>
					<Select
						value={groupBy}
						onValueChange={(v) => setGroupBy(v as GroupBy)}
						disabled={viewMode !== "grouped"}
					>
						<SelectTrigger className="h-8 w-[164px] text-[12px]">
							<ListTreeIcon className="mr-1.5 size-3.5 text-muted-foreground" />
							<SelectValue placeholder="Group by" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="none">No grouping</SelectItem>
							<SelectItem value="category">Group by category</SelectItem>
							<SelectItem value="owner">Group by owner</SelectItem>
							<SelectItem value="recency">Group by recency</SelectItem>
						</SelectContent>
					</Select>
					<Select value={projectFilter} onValueChange={setProjectFilter}>
						<SelectTrigger className="h-8 w-[172px] text-[12px]">
							<FilterIcon className="mr-1.5 size-3.5 text-muted-foreground" />
							<SelectValue placeholder="Project" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All projects</SelectItem>
							<SelectItem value="team-wide">Team-wide</SelectItem>
							{Array.from(projectsById.values()).map((project) => (
								<SelectItem key={project.id} value={project.id}>
									{project.name}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					<Select value={labelFilter} onValueChange={setLabelFilter}>
						<SelectTrigger className="h-8 w-[156px] text-[12px]">
							<TagsIcon className="mr-1.5 size-3.5 text-muted-foreground" />
							<SelectValue placeholder="Label" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All labels</SelectItem>
							{labelOptions.map((label) => (
								<SelectItem key={label.id} value={label.id}>
									{label.name}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					<Select
						value={updatedFilter}
						onValueChange={(v) => setUpdatedFilter(v as UpdatedFilter)}
					>
						<SelectTrigger className="h-8 w-[148px] text-[12px]">
							<CalendarDaysIcon className="mr-1.5 size-3.5 text-muted-foreground" />
							<SelectValue placeholder="Updated" />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">Any time</SelectItem>
							<SelectItem value="today">Today</SelectItem>
							<SelectItem value="week">This week</SelectItem>
							<SelectItem value="month">This month</SelectItem>
						</SelectContent>
					</Select>
				</div>
			</header>

			<div className="flex min-h-0 grow">
				<div className="min-w-0 grow overflow-y-auto">
					{isEmpty ? (
						<DocumentsEmptyState team={team} />
					) : docs.length === 0 ? (
						<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
							<FileTextIcon className="size-10 text-muted-foreground/60" />
							<p className="font-[510] text-[13px] text-foreground">
								No documents match these filters.
							</p>
							<p className="max-w-sm text-[12px] text-muted-foreground">
								Adjust search, project, label, or updated date to widen the
								result set.
							</p>
						</div>
					) : viewMode === "grouped" ? (
						<div className="px-6 py-4">
							{groups.map((g) => (
								<GroupSection
									key={g.key}
									icon={g.icon}
									title={g.title}
									count={g.docs.length}
									docs={g.docs}
									team={team}
									defaultOpen={true}
									focusedId={jk.focusedId}
									viewMode="list"
									onPreview={setPreviewId}
								/>
							))}
						</div>
					) : viewMode === "cards" ? (
						<div className="grid grid-cols-1 gap-3 p-6 sm:grid-cols-2 xl:grid-cols-3">
							{docs.map((d) => (
								<DocCard
									key={d.id}
									doc={d}
									team={team}
									focused={jk.focusedId === d.id}
									onPreview={setPreviewId}
								/>
							))}
						</div>
					) : (
						<ul className="space-y-1 p-6">
							{docs.map((d) => (
								<li key={d.id} data-jk-row={d.id}>
									<DocListItem
										doc={d}
										team={team}
										focused={jk.focusedId === d.id}
										onPreview={setPreviewId}
									/>
								</li>
							))}
						</ul>
					)}
				</div>
				{!isEmpty && docs.length > 0 && (
					<div className="hidden shrink-0 border-border/60 border-l px-4 py-4 xl:block">
						<DocumentsPreviewRail doc={previewDoc} team={team} />
					</div>
				)}
			</div>
		</div>
	);
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import { Input } from "@ui/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { cn } from "@ui/lib/utils";
import {
	BrainIcon,
	ChevronRightIcon,
	ClockIcon,
	FolderIcon,
	GlobeIcon,
	LayoutGridIcon,
	LayoutListIcon,
	ListTreeIcon,
	SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { DocumentIcon } from "@/components/documents/document-icon";
import { JkHint } from "@/components/jk-hint";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { trpc } from "@/utils/trpc";

// Documents index — iter-10 Round B.
//
//   - Scope toggle (All / Team / Personal): merges knowledge-vault notes
//     into the Documents surface.
//   - View-mode toggle (List / Grouped / Cards): three layouts over the
//     same underlying rows.
//   - Group-by selector (None / Category / Owner / Recency): when view-mode
//     is Grouped, items bucket by the chosen key.
//
// State precedence (codex amendment #3): URL param > localStorage >
// default. URL only wins when explicitly present so deep links can pin a
// view without permanently overriding the user's stored preference.

// -------- Types ---------------------------------------------------------

type Scope = "all" | "team" | "personal";
type ViewMode = "list" | "grouped" | "cards";
type GroupBy = "none" | "category" | "owner" | "recency";

type DocRow = {
	id: string;
	name: string | null;
	icon?: string | null;
	projectId: string | null;
	updatedAt?: string | Date;
	source: "team" | "personal";
};

// -------- Persistence helpers -------------------------------------------

const SCOPE_KEY = "nexus.documents.scope";
const VIEW_MODE_KEY = "nexus.documents.viewMode";
const GROUP_BY_KEY = "nexus.documents.groupBy";

const SCOPE_VALUES = ["all", "team", "personal"] as const;
const VIEW_MODE_VALUES = ["list", "grouped", "cards"] as const;
const GROUP_BY_VALUES = ["none", "category", "owner", "recency"] as const;

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
		// fall back to the default rather than break the page.
	}
	return fallback;
}

function writeStored(key: string, value: string) {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(key, value);
	} catch {
		// see readStored — same swallow.
	}
}

// -------- Pagination ----------------------------------------------------

// Codex amendment #7 — performance budget. Groups paginate at 50 with
// "Show more" reveal. 50 is enough for any realistic single-user vault
// without slowing React reconciliation on the initial render.
const GROUP_PAGE = 50;

// -------- Row primitives -----------------------------------------------

function DocListItem({
	doc,
	team,
	focused,
}: {
	doc: DocRow;
	team: string;
	focused: boolean;
}) {
	const href =
		doc.source === "personal"
			? `/team/${team}/knowledge?note=${encodeURIComponent(doc.id)}`
			: `/team/${team}/documents/${doc.id}`;
	return (
		<Link
			href={href}
			className={cn(
				"flex items-center gap-2 px-10 py-1.5 text-[13px] text-foreground transition-colors hover:bg-accent/40",
				focused && "ring-2 ring-violet-400/40 ring-inset",
			)}
		>
			{doc.source === "personal" ? (
				<BrainIcon className="size-3.5 text-violet-500" />
			) : (
				<DocumentIcon
					icon={doc.icon}
					className="size-3.5"
					hasChildren={false}
				/>
			)}
			<span className="truncate font-[510] tracking-[-0.005em]">
				{doc.name || "Untitled"}
			</span>
			{doc.updatedAt && (
				<span className="ml-auto text-[11px] text-muted-foreground">
					{new Date(doc.updatedAt).toLocaleDateString(undefined, {
						month: "short",
						day: "numeric",
					})}
				</span>
			)}
		</Link>
	);
}

function DocCard({
	doc,
	team,
	focused,
}: {
	doc: DocRow;
	team: string;
	focused: boolean;
}) {
	const href =
		doc.source === "personal"
			? `/team/${team}/knowledge?note=${encodeURIComponent(doc.id)}`
			: `/team/${team}/documents/${doc.id}`;
	return (
		<Link
			href={href}
			className={cn(
				"flex flex-col gap-2 rounded-md border border-border bg-card p-3 transition-colors hover:border-border/80 hover:bg-accent/30",
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
				<span className="capitalize">{doc.source}</span>
				{doc.updatedAt && (
					<span>
						{new Date(doc.updatedAt).toLocaleDateString(undefined, {
							month: "short",
							day: "numeric",
						})}
					</span>
				)}
			</div>
		</Link>
	);
}

// -------- Group section primitive --------------------------------------

function GroupSection({
	icon: Icon,
	title,
	count,
	docs,
	team,
	defaultOpen = true,
	focusedId,
}: {
	icon: any;
	title: string;
	count: number;
	docs: DocRow[];
	team: string;
	defaultOpen?: boolean;
	focusedId?: string | null;
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
				) : (
					<ul className="pb-2">
						{visible.map((d) => (
							<li key={d.id} data-jk-row={d.id}>
								<DocListItem
									doc={d}
									team={team}
									focused={focusedId === d.id}
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

// -------- Segmented control --------------------------------------------

function Segmented<T extends string>({
	value,
	onChange,
	options,
	ariaLabel,
}: {
	value: T;
	onChange: (next: T) => void;
	options: ReadonlyArray<{ value: T; label: string; icon?: any }>;
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
							"inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2.5 text-[12px] font-[510] transition-colors",
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

	// State precedence — URL > localStorage > default. See module doc.
	const urlScope = searchParams?.get("scope") as Scope | null;
	const urlView = searchParams?.get("view") as ViewMode | null;
	const urlGroup = searchParams?.get("groupBy") as GroupBy | null;

	const [scope, setScopeState] = useState<Scope>(() =>
		urlScope && (SCOPE_VALUES as readonly string[]).includes(urlScope)
			? (urlScope as Scope)
			: readStored<Scope>(SCOPE_KEY, SCOPE_VALUES, "all"),
	);
	const [viewMode, setViewModeState] = useState<ViewMode>(() =>
		urlView && (VIEW_MODE_VALUES as readonly string[]).includes(urlView)
			? (urlView as ViewMode)
			: readStored<ViewMode>(VIEW_MODE_KEY, VIEW_MODE_VALUES, "list"),
	);
	const [groupBy, setGroupByState] = useState<GroupBy>(() =>
		urlGroup && (GROUP_BY_VALUES as readonly string[]).includes(urlGroup)
			? (urlGroup as GroupBy)
			: readStored<GroupBy>(GROUP_BY_KEY, GROUP_BY_VALUES, "none"),
	);

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

	// Re-sync from URL after mount when the param changes (in-SPA nav).
	useEffect(() => {
		if (
			urlScope &&
			(SCOPE_VALUES as readonly string[]).includes(urlScope) &&
			urlScope !== scope
		) {
			setScopeState(urlScope as Scope);
		}
		if (
			urlView &&
			(VIEW_MODE_VALUES as readonly string[]).includes(urlView) &&
			urlView !== viewMode
		) {
			setViewModeState(urlView as ViewMode);
		}
		if (
			urlGroup &&
			(GROUP_BY_VALUES as readonly string[]).includes(urlGroup) &&
			urlGroup !== groupBy
		) {
			setGroupByState(urlGroup as GroupBy);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [urlScope, urlView, urlGroup]);

	const { data: docsPage } = useQuery({
		...trpc.documents.get.queryOptions({
			pageSize: 100,
			...(debouncedSearch
				? { search: debouncedSearch }
				: { tree: false as any }),
		} as any),
		enabled: scope !== "personal",
	});

	const { data: notesPage } = useQuery({
		...trpc.knowledge.get.queryOptions({
			...(debouncedSearch ? { search: debouncedSearch } : {}),
		} as any),
		enabled: scope !== "team",
	});

	const { data: projects } = useQuery(
		trpc.projects.get.queryOptions({ pageSize: 100 } as any),
	);

	const teamDocs = useMemo<DocRow[]>(() => {
		if (scope === "personal") return [];
		const items = (docsPage?.data ?? []) as Array<{
			id: string;
			name: string | null;
			icon?: string | null;
			projectId: string | null;
			updatedAt?: string | Date;
		}>;
		return items.map((d) => ({
			id: d.id,
			name: d.name,
			icon: d.icon,
			projectId: d.projectId,
			updatedAt: d.updatedAt,
			source: "team" as const,
		}));
	}, [docsPage, scope]);

	const personalNotes = useMemo<DocRow[]>(() => {
		if (scope === "team") return [];
		const items = (notesPage?.notes ?? []) as Array<{
			id: string;
			name: string;
			updatedAt: string;
		}>;
		return items.map((n) => ({
			id: n.id,
			name: n.name,
			projectId: null,
			updatedAt: n.updatedAt,
			source: "personal" as const,
		}));
	}, [notesPage, scope]);

	const docs = useMemo<DocRow[]>(() => {
		const merged = [...teamDocs, ...personalNotes];
		return merged.sort((a, b) => {
			const at = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
			const bt = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
			return bt - at;
		});
	}, [teamDocs, personalNotes]);

	const projectsById = useMemo(() => {
		const out = new Map<string, { id: string; name: string }>();
		const items = (projects?.data ?? []) as Array<{ id: string; name: string }>;
		for (const p of items) out.set(p.id, p);
		return out;
	}, [projects]);

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

	type Group = { key: string; title: string; icon: any; docs: DocRow[] };

	const groups = useMemo<Group[]>(() => {
		if (viewMode !== "grouped") return [];
		const out: Group[] = [];

		if (groupBy === "none") {
			return [{ key: "all", title: "All", icon: ListTreeIcon, docs }];
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
			// Multi-user owner-grouping is deferred — the documents.get
			// response does not yet include creator metadata in the list
			// payload, and single-user mode collapses this dimension entirely.
			// Single bucket keeps the UI honest rather than shipping a
			// half-working group.
			return [{ key: "all", title: "All", icon: ListTreeIcon, docs }];
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

	const isEmpty = docs.length === 0 && !debouncedSearch;

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							Documents
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							{scope === "personal"
								? "Personal notes from your Knowledge vault."
								: scope === "team"
									? "Shared docs grouped by project."
									: "All your written work — team docs and personal notes."}
						</p>
					</div>
					<JkHint />
				</div>

				<div className="mt-3 flex flex-wrap items-center gap-3">
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
					{viewMode === "grouped" && (
						<Select
							value={groupBy}
							onValueChange={(v) => setGroupBy(v as GroupBy)}
						>
							<SelectTrigger className="h-7 w-[160px] text-[12px]">
								<SelectValue placeholder="Group by" />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="none">No grouping</SelectItem>
								<SelectItem value="category">Group by category</SelectItem>
								<SelectItem value="owner">Group by owner</SelectItem>
								<SelectItem value="recency">Group by recency</SelectItem>
							</SelectContent>
						</Select>
					)}
					<div className="relative ml-auto w-full max-w-xs sm:w-auto">
						<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="Search documents…"
							className="h-7 pl-7 text-[12px] sm:w-64"
						/>
					</div>
				</div>
			</header>

			<div className="grow overflow-y-auto">
				{isEmpty ? (
					<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
						<p className="text-[13px] text-muted-foreground">
							No documents yet. Create one from the left sidebar.
						</p>
					</div>
				) : viewMode === "grouped" ? (
					groups.map((g) => (
						<GroupSection
							key={g.key}
							icon={g.icon}
							title={g.title}
							count={g.docs.length}
							docs={g.docs}
							team={team}
							defaultOpen={true}
							focusedId={jk.focusedId}
						/>
					))
				) : viewMode === "cards" ? (
					<div className="grid grid-cols-1 gap-3 p-6 sm:grid-cols-2 xl:grid-cols-3">
						{docs.map((d) => (
							<DocCard
								key={d.id}
								doc={d}
								team={team}
								focused={jk.focusedId === d.id}
							/>
						))}
					</div>
				) : (
					<ul className="py-2">
						{docs.map((d) => (
							<li key={d.id} data-jk-row={d.id} className="px-4">
								<DocListItem
									doc={d}
									team={team}
									focused={jk.focusedId === d.id}
								/>
							</li>
						))}
					</ul>
				)}
				{!isEmpty && docs.length === 0 && (
					<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
						<p className="text-[13px] text-muted-foreground">
							No matches for "{debouncedSearch}".
						</p>
					</div>
				)}
			</div>
		</div>
	);
}

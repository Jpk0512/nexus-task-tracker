"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import { Input } from "@ui/components/ui/input";
import { cn } from "@ui/lib/utils";
import {
	BrainIcon,
	ChevronRightIcon,
	ClockIcon,
	FolderIcon,
	GlobeIcon,
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
// Scope toggle (All / Team / Personal) merges knowledge-vault notes into
// the Documents surface so users have one place to find any written
// artifact. Team = Drizzle documents only. Personal = knowledge-vault
// notes only. All = both, merged & sorted by updatedAt desc.
//
// State precedence (codex amendment #3): URL param > localStorage >
// default. URL only wins when explicitly present so deep links can pin a
// scope without permanently overriding the user's stored preference.

type Scope = "all" | "team" | "personal";

type DocRow = {
	id: string;
	name: string | null;
	icon?: string | null;
	projectId: string | null;
	updatedAt?: string | Date;
	// "team" = Drizzle-backed document; "personal" = knowledge-vault note.
	// Used by the scope filter and to route to the right detail page.
	source: "team" | "personal";
};

const SCOPE_KEY = "nexus.documents.scope";
const SCOPE_VALUES = ["all", "team", "personal"] as const;

function readStoredScope(): Scope {
	if (typeof window === "undefined") return "all";
	try {
		const v = window.localStorage.getItem(SCOPE_KEY);
		if (v && (SCOPE_VALUES as readonly string[]).includes(v)) return v as Scope;
	} catch {
		// localStorage can throw in private-browsing / quota-exceeded modes —
		// fall back to the default rather than break the page.
	}
	return "all";
}

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
	return (
		<Collapsible
			defaultOpen={defaultOpen}
			className="border-border/60 border-b"
		>
			<CollapsibleTrigger className="group flex w-full items-center gap-2 px-4 py-2 text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground [&[data-state=open]>svg]:rotate-90">
				<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
				<Icon className="size-3.5 shrink-0" />
				<span className="font-[510] uppercase tracking-[0.04em]">{title}</span>
				<Badge variant="outline" className="ml-1 h-4 px-1.5 font-normal">
					{count}
				</Badge>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<ul className="pb-2">
					{docs.length === 0 ? (
						<li className="px-10 py-2 text-[12px] text-muted-foreground italic">
							Nothing here yet.
						</li>
					) : (
						docs.map((d) => (
							<li key={d.id} data-jk-row={d.id}>
								<DocLink
									doc={d}
									team={team}
									focused={focusedId === d.id}
								/>
							</li>
						))
					)}
				</ul>
			</CollapsibleContent>
		</Collapsible>
	);
}

function DocLink({
	doc,
	team,
	focused,
}: {
	doc: DocRow;
	team: string;
	focused: boolean;
}) {
	// Personal notes route to the Knowledge tab so the editor experience
	// matches the source-of-truth (the Obsidian vault on disk).
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

function ScopeToggle({
	value,
	onChange,
}: {
	value: Scope;
	onChange: (next: Scope) => void;
}) {
	const options: Array<{ value: Scope; label: string }> = [
		{ value: "all", label: "All" },
		{ value: "team", label: "Team" },
		{ value: "personal", label: "Personal" },
	];
	return (
		<div
			role="radiogroup"
			aria-label="Document scope"
			className="inline-flex h-7 rounded-md border border-border bg-muted/40 p-0.5"
		>
			{options.map((opt) => {
				const active = opt.value === value;
				return (
					<button
						key={opt.value}
						type="button"
						role="radio"
						aria-checked={active}
						onClick={() => onChange(opt.value)}
						className={cn(
							"inline-flex h-6 items-center rounded-[5px] px-2.5 text-[12px] font-[510] transition-colors",
							active
								? "bg-background text-foreground shadow-sm"
								: "text-muted-foreground hover:text-foreground",
						)}
					>
						{opt.label}
					</button>
				);
			})}
		</div>
	);
}

export function DocumentsIndexView() {
	const { team } = useParams<{ team: string }>();
	const router = useRouter();
	const searchParams = useSearchParams();
	const [search, setSearch] = useState("");
	const [debouncedSearch] = useDebounceValue(search, 300);

	const urlScope = searchParams?.get("scope") as Scope | null;
	const [scope, setScopeState] = useState<Scope>(() =>
		urlScope && (SCOPE_VALUES as readonly string[]).includes(urlScope)
			? (urlScope as Scope)
			: readStoredScope(),
	);

	const setScope = (s: Scope) => {
		setScopeState(s);
		if (typeof window !== "undefined") {
			try {
				window.localStorage.setItem(SCOPE_KEY, s);
			} catch {
				// see readStoredScope — same swallow.
			}
		}
	};

	// Re-sync from URL when params change after mount (e.g. deep-link nav
	// inside the SPA). Only fires when the param is actually present so it
	// can never quietly clobber a stored preference.
	useEffect(() => {
		if (
			urlScope &&
			(SCOPE_VALUES as readonly string[]).includes(urlScope) &&
			urlScope !== scope
		) {
			setScopeState(urlScope as Scope);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [urlScope]);

	// Team-side docs (Drizzle). Skipped entirely when scope=personal so we
	// don't burn a network round-trip on data the user isn't asking for.
	const { data: docsPage } = useQuery({
		...trpc.documents.get.queryOptions({
			pageSize: 100,
			...(debouncedSearch
				? { search: debouncedSearch }
				: { tree: false as any }),
		} as any),
		enabled: scope !== "personal",
	});

	// Knowledge-vault notes. Skipped when scope=team. Search piggy-backs on
	// the same debounced query so both surfaces filter in sync.
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
		// Merge + sort by updatedAt desc so the union view feels like one
		// timeline rather than two stacked lists.
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

	const groups = useMemo(() => {
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
		const recent = [...docs]
			.filter((d) => d.updatedAt)
			.sort(
				(a, b) =>
					new Date(b.updatedAt!).getTime() - new Date(a.updatedAt!).getTime(),
			)
			.slice(0, 10);
		return { byProject, teamWide, personal, recent };
	}, [docs, projectsById]);

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
					<ScopeToggle value={scope} onChange={setScope} />
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
				{!debouncedSearch && scope !== "personal" && groups.recent.length > 0 && (
					<GroupSection
						icon={ClockIcon}
						title="Recently edited"
						count={groups.recent.length}
						docs={groups.recent}
						team={team}
						defaultOpen={true}
						focusedId={jk.focusedId}
					/>
				)}
				{Array.from(groups.byProject.entries()).map(([projectId, docs]) => {
					const project = projectsById.get(projectId)!;
					return (
						<GroupSection
							key={projectId}
							icon={FolderIcon}
							title={project.name}
							count={docs.length}
							docs={docs}
							team={team}
							defaultOpen={true}
							focusedId={jk.focusedId}
						/>
					);
				})}
				{groups.teamWide.length > 0 && (
					<GroupSection
						icon={GlobeIcon}
						title="Team-wide"
						count={groups.teamWide.length}
						docs={groups.teamWide}
						team={team}
						defaultOpen={true}
						focusedId={jk.focusedId}
					/>
				)}
				{groups.personal.length > 0 && (
					<GroupSection
						icon={BrainIcon}
						title="Personal (Knowledge vault)"
						count={groups.personal.length}
						docs={groups.personal}
						team={team}
						defaultOpen={true}
						focusedId={jk.focusedId}
					/>
				)}
				{isEmpty && (
					<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
						<p className="text-[13px] text-muted-foreground">
							No documents yet. Create one from the left sidebar.
						</p>
					</div>
				)}
			</div>
		</div>
	);
}

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
	ChevronRightIcon,
	ClockIcon,
	FolderIcon,
	GlobeIcon,
	SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { DocumentIcon } from "@/components/documents/document-icon";
import { JkHint } from "@/components/jk-hint";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { trpc } from "@/utils/trpc";

// Linear's docs surface is grouped, not flat. We render three sections:
//   1. Pinned to project — grouped by project name
//   2. Team-wide — projectId IS NULL
//   3. Recently edited — top 10 by updatedAt
//
// Search is debounced and hits the same documents.get with `search` param so
// the existing FTS index does the work.

type Doc = {
	id: string;
	name: string | null;
	icon?: string | null;
	projectId: string | null;
	updatedAt?: string | Date;
};

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
	docs: Doc[];
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
								<Link
									href={`/team/${team}/documents/${d.id}`}
									className={`flex items-center gap-2 px-10 py-1.5 text-[13px] text-foreground transition-colors hover:bg-accent/40 ${
										focusedId === d.id
											? "ring-2 ring-violet-400/40 ring-inset"
											: ""
									}`}
								>
									<DocumentIcon
										icon={d.icon}
										className="size-3.5"
										hasChildren={false}
									/>
									<span className="truncate font-[510] tracking-[-0.005em]">
										{d.name || "Untitled"}
									</span>
									{d.updatedAt && (
										<span className="ml-auto text-[11px] text-muted-foreground">
											{new Date(d.updatedAt).toLocaleDateString(undefined, {
												month: "short",
												day: "numeric",
											})}
										</span>
									)}
								</Link>
							</li>
						))
					)}
				</ul>
			</CollapsibleContent>
		</Collapsible>
	);
}

export function DocumentsIndexView() {
	const { team } = useParams<{ team: string }>();
	const router = useRouter();
	const [search, setSearch] = useState("");
	const [debouncedSearch] = useDebounceValue(search, 300);

	const { data: docsPage } = useQuery(
		trpc.documents.get.queryOptions({
			pageSize: 100,
			...(debouncedSearch
				? { search: debouncedSearch }
				: { tree: false as any }),
		} as any),
	);

	const { data: projects } = useQuery(
		trpc.projects.get.queryOptions({ pageSize: 100 } as any),
	);

	const docs = (docsPage?.data ?? []) as Doc[];
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
		onOpen: (id) => router.push(`/team/${team}/documents/${id}`),
		toastLabel: (id) => {
			const d = docById.get(id) as
				| { name?: string; title?: string }
				| undefined;
			if (!d) return null;
			return `Opened ${d.name ?? d.title ?? "document"}`;
		},
	});

	const groups = useMemo(() => {
		const byProject = new Map<string, Doc[]>();
		const teamWide: Doc[] = [];
		for (const d of docs) {
			if (d.projectId && projectsById.has(d.projectId)) {
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
		return { byProject, teamWide, recent };
	}, [docs, projectsById]);

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							Documents
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Grouped by project. Use the left sidebar for the full tree.
						</p>
					</div>
					<JkHint />
				</div>
				<div className="relative mt-3 max-w-md">
					<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground" />
					<Input
						value={search}
						onChange={(e) => setSearch(e.target.value)}
						placeholder="Search documents…"
						className="h-8 pl-7"
					/>
				</div>
			</header>
			<div className="grow overflow-y-auto">
				{!debouncedSearch && (
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
				<GroupSection
					icon={GlobeIcon}
					title="Team-wide"
					count={groups.teamWide.length}
					docs={groups.teamWide}
					team={team}
					defaultOpen={true}
					focusedId={jk.focusedId}
				/>
				{docs.length === 0 && (
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

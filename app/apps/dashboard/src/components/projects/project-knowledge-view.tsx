"use client";

import { useQuery } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { BookOpenIcon, BrainIcon } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { trpc } from "@/utils/trpc";

type Props = { projectId: string; team: string };

type NoteListItem = {
	id: string;
	name: string;
	relativePath: string;
	parentDir: string | null;
	updatedAt: string;
	frontmatter: Record<string, unknown> | null;
};

/**
 * Project-scoped Knowledge tab. The `knowledge_notes` table has no project
 * foreign key — but the YAML frontmatter on each note can carry a `project:`
 * key. This view queries `trpc.knowledge.get` (which now surfaces the
 * frontmatter jsonb) and filters client-side to notes whose
 * `frontmatter.project` value matches the current project's name OR prefix.
 *
 * Filtering is deliberately client-side: it keeps the soft-link nature of
 * the relation visible and avoids a server-side jsonb scan on every render.
 * As a fallback we also include notes living under `projects/<key>/…` since
 * that path-based convention is common in Obsidian vaults.
 */
export function ProjectKnowledgeView({ projectId, team }: Props) {
	const projectQuery = useQuery(
		trpc.projects.getById.queryOptions({ id: projectId } as any),
	);
	const knowledgeQuery = useQuery(
		trpc.knowledge.get.queryOptions(undefined as any),
	);

	const project = projectQuery.data as
		| { name?: string; prefix?: string | null }
		| undefined;
	const allNotes = (knowledgeQuery.data?.notes ?? []) as NoteListItem[];

	// Lowercase the two human-readable identifiers we accept as a match.
	// The brief calls out "name OR slug"; this codebase uses `prefix` as the
	// short-form identifier on projects (no `slug` column), so prefix stands
	// in. Both are trimmed + lowercased to make the soft link forgiving.
	const matchKeys = useMemo(() => {
		const out = new Set<string>();
		if (project?.name) out.add(project.name.trim().toLowerCase());
		if (project?.prefix) out.add(project.prefix.trim().toLowerCase());
		return out;
	}, [project?.name, project?.prefix]);

	const linkedNotes = useMemo(() => {
		if (matchKeys.size === 0) return [] as NoteListItem[];
		const out: NoteListItem[] = [];
		const seen = new Set<string>();
		for (const n of allNotes) {
			let matched = false;

			// 1. Frontmatter project key — primary signal.
			const fmProject = n.frontmatter?.project;
			if (typeof fmProject === "string") {
				if (matchKeys.has(fmProject.trim().toLowerCase())) matched = true;
			} else if (Array.isArray(fmProject)) {
				// Some vaults store project as a list. Accept any element match.
				for (const v of fmProject) {
					if (typeof v === "string" && matchKeys.has(v.trim().toLowerCase())) {
						matched = true;
						break;
					}
				}
			}

			// 2. Path-based fallback: notes under `projects/<key>/…`.
			if (!matched) {
				const path = n.relativePath.toLowerCase().replace(/\\+/g, "/");
				for (const key of matchKeys) {
					if (path.startsWith(`projects/${key}/`)) {
						matched = true;
						break;
					}
				}
			}

			if (matched && !seen.has(n.id)) {
				seen.add(n.id);
				out.push(n);
			}
		}
		out.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
		return out;
	}, [allNotes, matchKeys]);

	const isLoading = knowledgeQuery.isLoading;

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							{project?.name ?? "Project"} — Knowledge
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Notes whose frontmatter{" "}
							<code className="rounded bg-muted px-1 text-[11px]">
								project:
							</code>{" "}
							matches{" "}
							<code className="rounded bg-muted px-1 text-[11px]">
								{project?.name ?? "this project"}
							</code>
							{project?.prefix ? (
								<>
									{" "}
									or{" "}
									<code className="rounded bg-muted px-1 text-[11px]">
										{project.prefix}
									</code>
								</>
							) : null}
							, plus anything under{" "}
							<code className="rounded bg-muted px-1 text-[11px]">
								projects/
							</code>{" "}
							with a matching folder.
						</p>
					</div>
				</div>
			</header>
			<div className="grow overflow-y-auto px-6 py-4">
				{isLoading && linkedNotes.length === 0 && (
					<div className="text-[12px] text-muted-foreground">Loading…</div>
				)}
				{linkedNotes.length === 0 && !isLoading && (
					<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
						<BrainIcon className="size-10 text-muted-foreground" />
						<p className="text-muted-foreground">
							No knowledge notes linked to this project yet.
						</p>
						<p className="text-muted-foreground text-xs">
							Add{" "}
							<code className="rounded bg-muted px-1 text-[11px]">
								project: {project?.name ?? "Project"}
							</code>{" "}
							to a note's frontmatter — it'll appear here automatically.
						</p>
					</div>
				)}
				<ul className="space-y-1">
					{linkedNotes.map((n) => (
						<li key={n.id}>
							<Link
								href={`/team/${team}/knowledge?note=${n.id}`}
								className="group flex items-start gap-3 rounded-md border border-transparent px-3 py-2 transition hover:border-border hover:bg-accent/40"
							>
								<div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded bg-cyan-500/10 text-cyan-600 dark:text-cyan-300">
									<BookOpenIcon className="size-3.5" />
								</div>
								<div className="min-w-0 grow">
									<div className="flex items-center gap-2">
										<span className="truncate font-medium text-sm">
											{n.name}
										</span>
										{n.parentDir && (
											<Badge variant="outline" className="font-normal text-xs">
												{n.parentDir}
											</Badge>
										)}
									</div>
									<p className="mt-0.5 line-clamp-1 text-muted-foreground text-xs">
										{n.relativePath}
									</p>
								</div>
								<span className="hidden text-muted-foreground text-xs sm:inline">
									{new Date(n.updatedAt).toLocaleDateString(undefined, {
										month: "short",
										day: "numeric",
									})}
								</span>
							</Link>
						</li>
					))}
				</ul>
			</div>
		</div>
	);
}

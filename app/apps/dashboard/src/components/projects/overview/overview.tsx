"use client";
import { useQuery } from "@tanstack/react-query";
import { BrainIcon, FolderOpenIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";
import { useSetBreadcrumbs } from "@/components/breadcrumbs";
import { ProjectForm } from "@/components/forms/project-form/form";
import { trpc } from "@/utils/trpc";
import { MilestonesCard } from "./milestones-card";

export const ProjectOverview = ({ projectId }: { projectId: string }) => {
	const { team } = useParams<{ team: string }>();
	const { data } = useQuery(
		trpc.projects.getById.queryOptions({
			id: projectId,
		}),
	);

	useSetBreadcrumbs([
		{
			label: data?.name,
			segments: ["projects", projectId],
			icon: FolderOpenIcon,
		},
	]);

	return (
		<div className="h-full overflow-y-auto">
			{data && (
				// Linear / Notion-style centered document column. The ProjectForm
				// renders the Tiptap block editor for `description`, and we drop
				// the rest of the properties into a compact chip strip directly
				// below — no card chrome, full width within the constrained column.
				<div className="mx-auto max-w-2xl px-6 py-8 lg:py-12">
					<ProjectForm
						defaultValues={{
							...data,
						}}
						propertiesLayout="compact"
						descriptionVariant="page"
					/>
					<div className="mt-10 border-t pt-6">
						<MilestonesCard projectId={projectId} />
					</div>
					<LinkedKnowledgeCard
						projectId={projectId}
						projectName={data.name}
						projectPrefix={(data as { prefix?: string | null }).prefix ?? null}
						team={team ?? ""}
					/>
				</div>
			)}
		</div>
	);
};

/**
 * Compact "Linked knowledge" preview on the project Overview tab.
 * Surfaces up to 5 knowledge notes whose frontmatter `project:` key matches
 * this project's name OR prefix. Click-through goes to the full Knowledge
 * sub-route. Same matching logic as ProjectKnowledgeView — keep them in
 * sync if the rules ever change.
 */
function LinkedKnowledgeCard({
	projectId,
	projectName,
	projectPrefix,
	team,
}: {
	projectId: string;
	projectName: string;
	projectPrefix: string | null;
	team: string;
}) {
	const knowledgeQuery = useQuery(
		trpc.knowledge.get.queryOptions(undefined as any),
	);
	const notes = (knowledgeQuery.data?.notes ?? []) as Array<{
		id: string;
		name: string;
		relativePath: string;
		parentDir: string | null;
		updatedAt: string;
		frontmatter: Record<string, unknown> | null;
	}>;

	const linked = useMemo(() => {
		const keys = new Set<string>();
		if (projectName) keys.add(projectName.trim().toLowerCase());
		if (projectPrefix) keys.add(projectPrefix.trim().toLowerCase());
		if (keys.size === 0) return [];
		const out: typeof notes = [];
		for (const n of notes) {
			let matched = false;
			const fmProject = n.frontmatter?.project;
			if (typeof fmProject === "string") {
				if (keys.has(fmProject.trim().toLowerCase())) matched = true;
			} else if (Array.isArray(fmProject)) {
				for (const v of fmProject) {
					if (typeof v === "string" && keys.has(v.trim().toLowerCase())) {
						matched = true;
						break;
					}
				}
			}
			if (!matched) {
				const path = n.relativePath.toLowerCase().replace(/\\+/g, "/");
				for (const key of keys) {
					if (path.startsWith(`projects/${key}/`)) {
						matched = true;
						break;
					}
				}
			}
			if (matched) out.push(n);
		}
		return out.slice(0, 5);
	}, [notes, projectName, projectPrefix]);

	if (linked.length === 0) return null;

	return (
		<div className="mt-10 border-t pt-6">
			<div className="mb-3 flex items-baseline justify-between">
				<div className="flex items-center gap-2">
					<BrainIcon className="size-3.5 text-violet-500" />
					<h2 className="font-[510] text-[13px] tracking-[-0.005em]">
						Linked knowledge
					</h2>
					<span className="text-[11px] text-muted-foreground">
						· {linked.length} note{linked.length === 1 ? "" : "s"}
					</span>
				</div>
				<Link
					href={`/team/${team}/projects/${projectId}/knowledge`}
					className="text-[11px] text-muted-foreground transition-colors hover:text-foreground"
				>
					View all →
				</Link>
			</div>
			<ul className="space-y-1">
				{linked.map((n) => (
					<li key={n.id}>
						<Link
							href={`/team/${team}/knowledge?note=${n.id}`}
							className="flex items-center gap-2 rounded-md border border-transparent px-2 py-1.5 text-[13px] transition hover:border-border hover:bg-accent/40"
						>
							<BrainIcon className="size-3 shrink-0 text-violet-500" />
							<span className="truncate font-[510]">{n.name}</span>
							<span className="ml-auto truncate text-[11px] text-muted-foreground">
								{n.relativePath}
							</span>
						</Link>
					</li>
				))}
			</ul>
		</div>
	);
}

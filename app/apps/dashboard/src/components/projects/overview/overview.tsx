"use client";
import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@ui/components/ui/skeleton";
import {
	BookOpenIcon,
	BrainIcon,
	FileTextIcon,
	FolderOpenIcon,
	ListChecksIcon,
	RadioIcon,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo } from "react";
import { useSetBreadcrumbs } from "@/components/breadcrumbs";
import { ProjectForm } from "@/components/forms/project-form/form";
import { SoftIcon } from "@/components/ui/soft-icon";
import { trpc } from "@/utils/trpc";
import { MilestonesCard } from "./milestones-card";

export const ProjectOverview = ({ projectId }: { projectId: string }) => {
	const { team } = useParams<{ team: string }>();
	const { data, isLoading } = useQuery(
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
			{/* Client-side refetch (e.g. switching projects without a full route
			 *  transition) never hits the route-level loading.tsx, so this
			 *  in-place skeleton covers the resource strip + form + milestones
			 *  shape while `getById` resolves. */}
			{isLoading && !data && <ProjectOverviewSkeleton />}
			{data && (
				// Linear / Notion-style centered document column. The ProjectForm
				// renders the Tiptap block editor for `description`, and we drop
				// the rest of the properties into a compact chip strip directly
				// below — no card chrome, full width within the constrained column.
				<div className="mx-auto max-w-2xl px-6 py-8 lg:py-12">
					<ProjectResourceStrip projectId={projectId} team={team ?? ""} />
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

/** Matches the resource-strip / form / milestones geometry of the loaded
 *  page so there's no layout jump once `data` arrives. */
function ProjectOverviewSkeleton() {
	return (
		<div className="mx-auto max-w-2xl px-6 py-8 lg:py-12" aria-hidden>
			<div className="mb-8 grid grid-cols-3 gap-2 sm:grid-cols-6">
				{Array.from({ length: 6 }).map((_, i) => (
					<div
						key={`overview-skel-strip-${i}`}
						className="flex flex-col items-center gap-1.5 rounded-xl border border-border/60 bg-card/30 px-2 py-3"
					>
						<Skeleton className="size-6 rounded-full" />
						<Skeleton className="h-2.5 w-10" />
					</div>
				))}
			</div>
			<Skeleton className="h-7 w-2/3" />
			<Skeleton className="mt-4 h-4 w-full" />
			<Skeleton className="mt-2 h-4 w-5/6" />
			<Skeleton className="mt-2 h-4 w-3/5" />
			<div className="mt-10 border-t pt-6">
				<Skeleton className="h-4 w-32" />
				<Skeleton className="mt-3 h-10 w-full rounded-md" />
				<Skeleton className="mt-2 h-10 w-full rounded-md" />
			</div>
		</div>
	);
}

/** Dashboard OS resource strip — one-click jump to project surfaces. */
function ProjectResourceStrip({
	projectId,
	team,
}: {
	projectId: string;
	team: string;
}) {
	const items = [
		{
			label: "Board",
			href: `/team/${team}/projects/${projectId}`,
			icon: FolderOpenIcon,
			tone: "blue" as const,
		},
		{
			label: "Todos",
			href: `/team/${team}/projects/${projectId}/todos`,
			icon: ListChecksIcon,
			tone: "green" as const,
		},
		{
			label: "Docs",
			href: `/team/${team}/projects/${projectId}/docs`,
			icon: FileTextIcon,
			tone: "orange" as const,
		},
		{
			label: "Notes",
			href: `/team/${team}/projects/${projectId}/knowledge`,
			icon: BrainIcon,
			tone: "violet" as const,
		},
		{
			label: "Skills",
			href: `/team/${team}/projects/${projectId}/library`,
			icon: BookOpenIcon,
			tone: "teal" as const,
		},
		{
			label: "Updates",
			href: `/team/${team}/projects/${projectId}/updates`,
			icon: RadioIcon,
			tone: "pink" as const,
		},
	];
	return (
		<div className="mb-8 grid grid-cols-3 gap-2 sm:grid-cols-6">
			{items.map((it) => (
				<Link
					key={it.label}
					href={it.href}
					className="flex flex-col items-center gap-1.5 rounded-xl border border-border/60 bg-card/30 px-2 py-3 text-center transition-colors hover:bg-accent/40"
				>
					<SoftIcon icon={it.icon} tone={it.tone} size="sm" />
					<span className="font-[510] text-[11px]">{it.label}</span>
				</Link>
			))}
		</div>
	);
}

/**
 * Compact "Linked notes" preview on the project Overview tab.
 * Matches frontmatter project key, name/prefix, or projects/{projectId}/ path.
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
				// Lock: vault folders by projectId for rename stability
				if (path.startsWith(`projects/${projectId.toLowerCase()}/`)) {
					matched = true;
				}
				if (!matched) {
					for (const key of keys) {
						if (path.startsWith(`projects/${key}/`)) {
							matched = true;
							break;
						}
					}
				}
			}
			if (matched) out.push(n);
		}
		return out.slice(0, 5);
	}, [notes, projectName, projectPrefix, projectId]);

	if (linked.length === 0) return null;

	return (
		<div className="mt-10 border-t pt-6">
			<div className="mb-3 flex items-baseline justify-between">
				<div className="flex items-center gap-2">
					<BrainIcon className="size-3.5 text-cyan-500" />
					<h2 className="font-[510] text-[13px] tracking-[-0.005em]">
						Linked notes
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
							<BrainIcon className="size-3 shrink-0 text-cyan-500" />
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

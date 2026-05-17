"use client";

import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { ProjectIcon } from "@/components/project-icon";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";

/**
 * Horizontal rail of active projects with progress bars (designer-meta §5).
 *
 * Each tile shows: project icon + name, progress bar, "N open · M%" labels.
 * Horizontally scrollable on overflow — keeps the home page above-the-fold
 * tight even with 12+ active projects.
 *
 * Sort: most-active first (in-progress task count desc, then updatedAt desc).
 * Archived projects filtered out.
 */
export const ActiveProjectsRail = () => {
	const user = useUser();
	const { data, isLoading } = useQuery(
		trpc.projects.get.queryOptions(
			{ pageSize: 50 },
			{ staleTime: 5 * 60 * 1000 },
		),
	);

	const basePath = user?.basePath ?? "/team";

	const projects = useMemo(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC response is typed unknown
		const list = ((data as any)?.data ?? []) as Array<any>;
		return list
			.filter((p) => !p.archived)
			.slice()
			.sort((a, b) => {
				const aActive = a.progress?.inProgress ?? 0;
				const bActive = b.progress?.inProgress ?? 0;
				if (aActive !== bActive) return bActive - aActive;
				const aTime = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
				const bTime = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
				return bTime - aTime;
			})
			.slice(0, 12);
	}, [data]);

	return (
		<section className="rounded-[12px] border border-border bg-card">
			<header className="flex items-center justify-between gap-2 border-border border-b px-3 py-2">
				<div className="flex items-center gap-1.5">
					<h2 className="font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						Active projects
					</h2>
					{projects.length > 0 ? (
						<span className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-muted px-1.5 font-[510] text-[11px] text-muted-foreground tabular-nums">
							{projects.length}
						</span>
					) : null}
				</div>
				<Link
					href={`${basePath}/projects`}
					className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
					aria-label="View all projects"
				>
					View all
					<ArrowRight className="size-3" />
				</Link>
			</header>
			<div className="px-3 py-3">
				{isLoading ? (
					<RailSkeleton />
				) : projects.length === 0 ? (
					<p className="px-1 py-3 text-[12px] text-muted-foreground">
						No active projects. Create one from the Projects tab.
					</p>
				) : (
					<div className="-mx-1 flex snap-x snap-mandatory gap-2 overflow-x-auto pb-1 pl-1">
						{projects.map((project) => (
							<ProjectTile
								key={project.id}
								project={project}
								basePath={basePath}
							/>
						))}
					</div>
				)}
			</div>
		</section>
	);
};

function ProjectTile({
	project,
	basePath,
}: {
	// biome-ignore lint/suspicious/noExplicitAny: project shape from tRPC
	project: any;
	basePath: string;
}) {
	const inProgress = project.progress?.inProgress ?? 0;
	const completed = project.progress?.completed ?? 0;
	const total = inProgress + completed;
	const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
	const hasTasks = total > 0;

	return (
		<Link
			href={`${basePath}/projects/${project.id}`}
			className={cn(
				"group flex w-[220px] shrink-0 snap-start flex-col gap-1.5 rounded-[10px] border border-border bg-background px-3 py-2.5",
				"transition-colors hover:bg-accent/40",
			)}
		>
			<div className="flex min-w-0 items-center gap-1.5">
				<ProjectIcon
					hasTasks={hasTasks}
					color={project.color}
					className="size-4 shrink-0"
				/>
				<span className="min-w-0 flex-1 truncate font-[510] text-[13px] text-foreground">
					{project.name}
				</span>
			</div>
			<div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
				<div
					className="h-full rounded-full bg-primary transition-all"
					style={{ width: `${percent}%` }}
					aria-label={`Progress: ${percent}%`}
				/>
			</div>
			<div className="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
				<span>{inProgress} open</span>
				<span>{percent}%</span>
			</div>
		</Link>
	);
}

function RailSkeleton() {
	return (
		<div className="flex gap-2">
			{Array.from({ length: 4 }).map((_, i) => (
				<div
					// biome-ignore lint/suspicious/noArrayIndexKey: skeleton
					key={i}
					className="flex w-[220px] shrink-0 flex-col gap-2 rounded-[10px] border border-border bg-background px-3 py-2.5"
				>
					<Skeleton className="h-4 w-3/4 rounded" />
					<Skeleton className="h-1.5 w-full rounded-full" />
					<Skeleton className="h-3 w-1/2 rounded" />
				</div>
			))}
		</div>
	);
}

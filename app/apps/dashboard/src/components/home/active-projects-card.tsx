"use client";

import { useQuery } from "@tanstack/react-query";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";
import { ProjectIcon } from "../project-icon";
import { HomeCard, HomeCardEmpty, HomeCardRow } from "./home-card";

export const ActiveProjectsCard = () => {
	const user = useUser();
	const { data, isLoading } = useQuery(
		trpc.projects.get.queryOptions(
			{ pageSize: 50 },
			{ staleTime: 5 * 60 * 1000 },
		),
	);

	const basePath = user?.basePath ?? "/team";
	const projects = (data?.data ?? [])
		.filter((p) => !p.archived)
		.slice()
		.sort((a, b) => {
			// Most-active first: in-progress count desc, then by updatedAt
			const aActive = a.progress?.inProgress ?? 0;
			const bActive = b.progress?.inProgress ?? 0;
			if (aActive !== bActive) return bActive - aActive;
			const aTime = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
			const bTime = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
			return bTime - aTime;
		});
	const top5 = projects.slice(0, 5);

	return (
		<HomeCard
			title="Active Projects"
			count={projects.length}
			href={`${basePath}/projects`}
			isLoading={isLoading}
			isEmpty={top5.length === 0}
			emptyState={
				<HomeCardEmpty
					title="No active projects"
					description="Create a project to organize your work."
					ctaHref={`${basePath}/projects`}
					ctaLabel="New project"
				/>
			}
		>
			<ul className="space-y-0.5">
				{top5.map((project) => {
					const total =
						(project.progress?.inProgress ?? 0) +
						(project.progress?.completed ?? 0);
					const percent =
						total > 0
							? Math.round(((project.progress?.completed ?? 0) / total) * 100)
							: 0;
					return (
						<li key={project.id}>
							<HomeCardRow
								href={`${basePath}/projects/${project.id}`}
								leading={
									<ProjectIcon
										hasTasks={total > 0}
										color={project.color}
										className="size-3.5"
									/>
								}
								title={project.name}
								trailing={
									<>
										<span className="tabular-nums">
											{project.progress?.inProgress ?? 0} open
										</span>
										<span className="tabular-nums opacity-60">{percent}%</span>
									</>
								}
							/>
						</li>
					);
				})}
			</ul>
		</HomeCard>
	);
};

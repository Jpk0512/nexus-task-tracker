"use client";

import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";
import { StatusIcon } from "../status-icon";
import { Priority } from "../tasks-view/properties/priority";
import { HomeCard, HomeCardEmpty, HomeCardRow } from "./home-card";

const PRIORITY_ORDER: Record<string, number> = {
	urgent: 0,
	high: 1,
	medium: 2,
	low: 3,
};

export const MyIssuesCard = () => {
	const user = useUser();
	const { tasks, isLoading } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 20,
		},
		{ enabled: !!user?.id },
	);

	const top5 = tasks
		.slice()
		.sort((a, b) => {
			const aPriority = a.priority ? (PRIORITY_ORDER[a.priority] ?? 99) : 99;
			const bPriority = b.priority ? (PRIORITY_ORDER[b.priority] ?? 99) : 99;
			if (aPriority !== bPriority) return aPriority - bPriority;
			// fall back to status change recency
			const aTime = a.statusChangedAt
				? new Date(a.statusChangedAt).getTime()
				: 0;
			const bTime = b.statusChangedAt
				? new Date(b.statusChangedAt).getTime()
				: 0;
			return bTime - aTime;
		})
		.slice(0, 5);

	const basePath = user?.basePath ?? "/team";
	const prefix = user?.team?.prefix ?? "";

	return (
		<HomeCard
			title="My Issues"
			count={tasks.length}
			href={`${basePath}/views/my-tasks`}
			isLoading={isLoading}
			isEmpty={top5.length === 0}
			emptyState={
				<HomeCardEmpty
					title="No assigned issues"
					description="Issues assigned to you will appear here."
					ctaHref={`${basePath}/views/my-tasks`}
					ctaLabel="Browse all issues"
				/>
			}
		>
			<ul className="space-y-0.5">
				{top5.map((task) => (
					<li key={task.id}>
						<HomeCardRow
							href={`${basePath}/projects/${task.projectId}/${task.id}`}
							leading={
								<StatusIcon type={task.status?.type} className="size-3.5" />
							}
							id={prefix ? `${prefix}-${task.sequence}` : undefined}
							title={task.title}
							trailing={
								task.priority ? <Priority value={task.priority} /> : null
							}
						/>
					</li>
				))}
			</ul>
		</HomeCard>
	);
};

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useFormContext } from "react-hook-form";
import { MilestoneIcon } from "@/components/milestone-icon";
import { ProjectIcon } from "@/components/project-icon";
import { useUser } from "@/components/user-provider";
import { useProjects } from "@/hooks/use-data";
import { trpc } from "@/utils/trpc";
import type { TaskFormValues } from "./form-type";

/**
 * Clickable project + milestone chips at the top of the task detail sheet
 * (FEAT-006 item 1) — one click jumps to the owning project board / the
 * milestone-filtered board, mirroring the routes the global-search palette
 * already uses for the same entities. Only rendered for an existing task —
 * a task being created has nowhere meaningful to "jump to" yet.
 */
export const TaskLinkChips = () => {
	const user = useUser();
	const form = useFormContext<TaskFormValues>();
	const id = form.watch("id");
	const projectId = form.watch("projectId");
	const milestoneId = form.watch("milestoneId");

	const { data: projects } = useProjects();
	const project = projects?.data.find((p) => p.id === projectId);

	const { data: milestones } = useQuery(
		trpc.milestones.get.queryOptions(
			{ projectId: projectId! },
			{ enabled: Boolean(projectId), select: (data) => data.data },
		),
	);
	const milestone = milestones?.find((m) => m.id === milestoneId);

	if (!id || !projectId) return null;

	const base = user.basePath;

	return (
		<div className="flex flex-wrap items-center gap-2 px-4">
			{project && (
				<Link
					href={`${base}/projects/${projectId}`}
					className="flex h-5.5 items-center gap-2 rounded-sm bg-secondary px-2 text-xs transition-colors hover:bg-accent"
				>
					<ProjectIcon className="size-3.5" {...project} />
					{project.name}
				</Link>
			)}
			{milestone && (
				<Link
					href={`${base}/projects/${projectId}/tasks?mId=${milestoneId}`}
					className="flex h-5.5 items-center gap-2 rounded-sm bg-secondary px-2 text-xs transition-colors hover:bg-accent"
				>
					<MilestoneIcon className="size-3.5" {...milestone} />
					{milestone.name}
				</Link>
			)}
		</div>
	);
};

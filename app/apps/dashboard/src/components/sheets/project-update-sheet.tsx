"use client";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@nexus-app/ui/dialog";
import { Skeleton } from "@nexus-app/ui/skeleton";
import { useQuery } from "@tanstack/react-query";
import { useProjectParams } from "@/hooks/use-project-params";
import { trpc } from "@/utils/trpc";
import { ProjectForm } from "../forms/project-form/form";

export const ProjectUpdateSheet = () => {
	const { projectId, setParams } = useProjectParams();

	const isOpen = Boolean(projectId);

	const { data: project } = useQuery(
		trpc.projects.getById.queryOptions(
			{
				id: projectId!,
			},
			{
				enabled: isOpen,
				placeholderData: (old) => {
					if (!projectId) return old;
					if (projectId === old?.id) return old;
					return undefined;
				},
			},
		),
	);

	return (
		<Dialog open={isOpen} onOpenChange={() => setParams({ projectId: null })}>
			<DialogContent
				showCloseButton={false}
				className="max-h-[85vh] overflow-y-auto pt-0 sm:min-w-[60vw]"
			>
				<DialogHeader className="sr-only">
					<DialogTitle>Edit project</DialogTitle>
				</DialogHeader>
				{project ? (
					<div className="pt-4">
						<ProjectForm
							defaultValues={{
								...project,
							}}
							propertiesLayout="compact"
						/>
					</div>
				) : (
					<Skeleton className="h-[120px] w-full" />
				)}
			</DialogContent>
		</Dialog>
	);
};

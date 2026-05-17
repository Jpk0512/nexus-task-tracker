import { notFound } from "next/navigation";
import { BreadcrumbSetter } from "@/components/breadcrumbs";
import { ProjectTabs } from "@/components/projects/project-tabs";
import { trpcClient } from "@/utils/trpc";

type Props = {
	children: React.ReactNode;
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectLayout({ children, params }: Props) {
	const { projectId } = await params;

	const project = await trpcClient.projects.getById.query({
		id: projectId,
	});

	if (!project) {
		return notFound();
	}

	return (
		<div className="flex h-full min-h-0 flex-col overflow-x-auto">
			<BreadcrumbSetter
				crumbs={[
					{
						label: project.name,
						segments: ["projects", project.id],
					},
				]}
			/>
			<ProjectTabs projectId={project.id} />
			<div className="min-h-0 grow">{children}</div>
		</div>
	);
}

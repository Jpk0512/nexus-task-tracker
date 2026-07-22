import { notFound } from "next/navigation";
import { BreadcrumbSetter } from "@/components/breadcrumbs";
import { RecordVisit } from "@/components/global-search/record-visit";
import { ProjectBreadcrumb } from "@/components/projects/project-breadcrumb";
import { ProjectRelationshipsSidebar } from "@/components/projects/project-relationships-sidebar";
import { ProjectTabs } from "@/components/projects/project-tabs";
import { trpcClient } from "@/utils/trpc";

type Props = {
	children: React.ReactNode;
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectLayout({ children, params }: Props) {
	const { projectId, team } = await params;

	const project = await trpcClient.projects.getById.query({
		id: projectId,
	});

	if (!project) {
		return notFound();
	}

	const backHref = `/team/${team}/projects`;

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
			<ProjectBreadcrumb projectName={project.name} backHref={backHref} />
			<RecordVisit
				item={{
					id: project.id,
					type: "project",
					title: project.name,
					color: project.color,
					teamId: "",
				}}
			/>
			<ProjectTabs projectId={project.id} />
			<div className="flex min-h-0 grow">
				<div className="min-w-0 grow">{children}</div>
				<ProjectRelationshipsSidebar projectId={project.id} />
			</div>
		</div>
	);
}

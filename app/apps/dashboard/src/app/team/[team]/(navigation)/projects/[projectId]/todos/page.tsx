import { ProjectTodosView } from "@/components/projects/project-todos-view";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectTodosPage({ params }: Props) {
	const { projectId, team } = await params;
	return (
		<div className="h-full animate-blur-in">
			<ProjectTodosView projectId={projectId} team={team} />
		</div>
	);
}

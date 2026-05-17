import { ProjectKnowledgeView } from "@/components/projects/project-knowledge-view";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectKnowledgePage({ params }: Props) {
	const { projectId, team } = await params;
	return (
		<div className="h-full animate-blur-in">
			<ProjectKnowledgeView projectId={projectId} team={team} />
		</div>
	);
}

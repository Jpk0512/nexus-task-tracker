import { ProjectDocsView } from "@/components/projects/project-docs-view";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectDocsPage({ params }: Props) {
	const { projectId, team } = await params;
	return (
		<div className="animate-blur-in">
			<ProjectDocsView projectId={projectId} team={team} />
		</div>
	);
}

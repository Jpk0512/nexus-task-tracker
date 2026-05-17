import { ProjectMembersView } from "@/components/projects/project-members-view";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectMembersPage({ params }: Props) {
	const { projectId, team } = await params;
	return (
		<div className="h-full animate-blur-in">
			<ProjectMembersView projectId={projectId} team={team} />
		</div>
	);
}

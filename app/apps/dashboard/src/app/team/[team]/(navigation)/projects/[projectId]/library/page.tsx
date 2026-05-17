import { ProjectLibraryView } from "@/components/projects/project-library-view";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectLibraryPage({ params }: Props) {
	const { projectId, team } = await params;
	return (
		<div className="h-full animate-blur-in">
			<ProjectLibraryView projectId={projectId} team={team} />
		</div>
	);
}

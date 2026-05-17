import { ProjectOverview } from "@/components/projects/overview/overview";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectOverviewPage({ params }: Props) {
	const { projectId } = await params;
	return (
		<div className="h-full animate-blur-in">
			<ProjectOverview projectId={projectId} />
		</div>
	);
}

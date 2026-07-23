import { ProjectSettingsView } from "@/components/projects/project-settings-view";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

export default async function ProjectSettingsPage({ params }: Props) {
	const { projectId } = await params;
	return (
		<div className="h-full animate-blur-in">
			<ProjectSettingsView projectId={projectId} />
		</div>
	);
}

import { redirect } from "next/navigation";

type Props = {
	params: Promise<{ projectId: string; team: string }>;
};

// /projects/[id]/tasks is the natural-language fallback for "show me the tasks
// on this project" — Linear-style board lives at the project root, so redirect
// there instead of 404ing.
export default async function ProjectTasksRedirect({ params }: Props) {
	const { team, projectId } = await params;
	redirect(`/team/${team}/projects/${projectId}`);
}

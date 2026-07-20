import { redirect } from "next/navigation";

/** Legacy My Tasks → Focus. */
export default async function MyTasksRedirectPage({
	params,
}: {
	params: Promise<{ team: string }>;
}) {
	const { team } = await params;
	redirect(`/team/${team}/focus`);
}

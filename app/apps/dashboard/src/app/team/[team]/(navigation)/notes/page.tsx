import { redirect } from "next/navigation";

export default async function NotesAliasPage({
	params,
}: {
	params: Promise<{ team: string }>;
}) {
	const { team } = await params;
	redirect(`/team/${team}/knowledge`);
}

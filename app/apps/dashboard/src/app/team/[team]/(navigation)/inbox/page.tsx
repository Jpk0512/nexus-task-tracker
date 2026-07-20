import { redirect } from "next/navigation";

/** Legacy Inbox → Focus Needs you. */
export default async function InboxRedirectPage({
	params,
}: {
	params: Promise<{ team: string }>;
}) {
	const { team } = await params;
	redirect(`/team/${team}/focus?tab=needs-you`);
}

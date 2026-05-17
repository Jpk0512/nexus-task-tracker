import { redirect } from "next/navigation";

interface PageProps {
	params: Promise<{ team: string }>;
}

export default async function Page({ params }: PageProps) {
	const { team } = await params;
	// Notifications were a duplicate of Inbox — unify under /inbox with the
	// Mentions tab preselected so links from the top-bar bell still feel
	// "notification-y".
	redirect(`/team/${team}/inbox?tab=mentions`);
}

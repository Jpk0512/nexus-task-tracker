import { redirect } from "next/navigation";
import { getSession } from "@/lib/get-session";
import { trpcClient } from "@/utils/trpc";

// Starter templates surfaced from the Documents empty-state (iter-10).
// Kept inline here so the templates ship with the redirect endpoint that
// already exists — a full template gallery is deferred per the brief.
const TEMPLATES: Record<string, { name: string; content: string }> = {
	spec: {
		name: "Untitled spec",
		content:
			"# Untitled spec\n\n## Problem\n\n## Goals\n\n## Non-goals\n\n## Scope\n\n## Open questions\n",
	},
	meeting: {
		name: "Meeting notes",
		content:
			"# Meeting notes\n\n## Attendees\n\n## Agenda\n\n## Decisions\n\n## Action items\n",
	},
};

type CreateSearchParams = Promise<{ template?: string | string[] }>;

export default async function CreateDocumentPage({
	searchParams,
}: {
	searchParams: CreateSearchParams;
}) {
	const session = await getSession();

	if (!session?.user?.teamSlug) {
		redirect("/sign-in");
	}

	const sp = await searchParams;
	const rawTemplate = sp?.template;
	const templateKey =
		typeof rawTemplate === "string"
			? rawTemplate
			: Array.isArray(rawTemplate)
				? rawTemplate[0]
				: undefined;
	const template = templateKey ? TEMPLATES[templateKey] : undefined;

	const newDocument = await trpcClient.documents.create.mutate({
		name: template?.name ?? "",
		content: template?.content ?? "",
	});

	return redirect(`/team/${session.user.teamSlug}/documents/${newDocument.id}`);
}

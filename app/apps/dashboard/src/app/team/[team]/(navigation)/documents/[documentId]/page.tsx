import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
import { BreadcrumbSetter } from "@/components/breadcrumbs";
import { DocumentForm } from "@/components/documents/document-form";
import { DocumentToc } from "@/components/documents/document-toc";
import { trpcClient } from "@/utils/trpc";

type Props = {
	params: Promise<{ documentId: string; team: string }>;
};

export default async function DocumentPage({ params }: Props) {
	const { documentId, team } = await params;

	const document = await trpcClient.documents.getById.query({
		id: documentId,
	});

	if (!document) {
		return (
			<div className="flex h-full flex-col items-center justify-center gap-3">
				<p className="font-header font-medium text-xl">Document not found</p>
				<p className="max-w-md text-balance text-muted-foreground text-sm">
					This document doesn't exist or was deleted. The id in the URL was{" "}
					<code className="rounded bg-muted px-1.5 py-0.5 text-xs">
						{documentId}
					</code>
					.
				</p>
				<Link
					href={`/team/${team}/documents`}
					className="inline-flex items-center gap-1 text-muted-foreground text-sm hover:text-foreground"
				>
					<ArrowLeftIcon className="size-3.5" /> Back to Documents
				</Link>
			</div>
		);
	}

	return (
		<div className="animate-blur-in">
			<BreadcrumbSetter
				crumbs={[
					{
						label: "Documents",
						segments: ["documents"],
					},
					{
						label: document.name,
						segments: ["documents", documentId],
					},
				]}
			/>
			{/*
			  Linear / Notion-style page: max-w-2xl centered editor with a
			  right-rail TOC visible from xl+ (hidden on smaller screens to keep
			  the column readable).
			*/}
			<div className="mx-auto flex w-full max-w-5xl justify-center gap-8 px-4 py-6">
				<div className="w-full min-w-0 max-w-2xl">
					<DocumentForm
						defaultValues={{
							...document,
							labels: document.labels?.map((l) => l.id) || [],
						}}
						creatorName={document.creatorName}
						updatedAt={document.updatedAt}
					/>
					<BacklinksPanel entityType="document" entityId={documentId} />
				</div>
				<DocumentToc content={document.content} />
			</div>
		</div>
	);
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { type NodeViewProps, NodeViewWrapper } from "@tiptap/react";
import { Skeleton } from "@ui/components/ui/skeleton";
import { BrainIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useUser } from "@/components/user-provider";
import { cn } from "@/lib/utils";
import { trpc } from "@/utils/trpc";
import { createMentionNodeExtension } from "./mention-node-extension";
import type { KnowledgeMentionEntity, MentionItemRendererProps } from "./types";

/**
 * Knowledge mention list item renderer — used in slash-menu entity pickers.
 */
export function KnowledgeMentionListItem({
	entity,
}: MentionItemRendererProps<KnowledgeMentionEntity>) {
	return (
		<>
			<BrainIcon className="size-4 shrink-0 text-muted-foreground" />
			<div className="flex min-w-0 flex-col text-left">
				<span className="truncate text-sm">{entity.name}</span>
				{entity.relativePath ? (
					<span className="truncate text-muted-foreground text-xs">
						{entity.relativePath}
					</span>
				) : null}
			</div>
		</>
	);
}

/**
 * Knowledge mention node — rendered inline as a compact entity-link pill.
 * Click navigates to `/knowledge?note={id}` (single-page knowledge view with
 * left-rail selection), matching iter3 global-search behaviour.
 */
function KnowledgeMentionNodeComponent({ node }: NodeViewProps) {
	const { id, label, relativePath } = node.attrs;
	const knowledgeId = id as string;
	const router = useRouter();
	const user = useUser();

	const { data: note, isLoading } = useQuery(
		trpc.knowledge.getById.queryOptions({ id: knowledgeId }),
	);

	const displayName = note?.name ?? label ?? "Untitled";
	const basePath = user?.basePath ?? "";

	return (
		<NodeViewWrapper
			as="button"
			type="button"
			className="inline"
			onClick={(event: React.MouseEvent) => {
				// Cmd/Ctrl-click → new tab; otherwise navigate in-app.
				const url = `${basePath}/knowledge?note=${knowledgeId}`;
				if (event.metaKey || event.ctrlKey) {
					window.open(url, "_blank");
					return;
				}
				router.push(url);
			}}
		>
			{isLoading && !note ? (
				<Skeleton className="h-6 w-24 rounded-md" />
			) : (
				<span
					className={cn(
						"inline-flex items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-0.5 align-middle font-medium text-sm transition-colors hover:bg-muted",
					)}
					data-mention-type="knowledge"
					data-mention-id={id}
				>
					<BrainIcon className="size-3.5 shrink-0 text-muted-foreground" />
					<span className="max-w-[300px] truncate">{displayName}</span>
					{relativePath ? (
						<span className="hidden text-muted-foreground text-xs sm:inline">
							{relativePath}
						</span>
					) : null}
				</span>
			)}
		</NodeViewWrapper>
	);
}

/**
 * TipTap extension for knowledge-note mentions.
 * Note: stores id + label + relativePath; live name is fetched on render so
 * stale serialized markdown reflects the current note name.
 */
export const KnowledgeMentionExtension = createMentionNodeExtension({
	name: "knowledgeMention",
	entityName: "knowledge",
	mentionType: "knowledge",
	className:
		"inline-flex items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-0.5 font-medium text-sm",
	attributes: {
		id: {
			default: null as string | null,
		},
		label: {
			default: null as string | null,
		},
		relativePath: {
			default: null as string | null,
		},
	},
	nodeView: KnowledgeMentionNodeComponent,
});

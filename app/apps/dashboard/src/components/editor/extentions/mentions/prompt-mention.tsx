"use client";

import { type NodeViewProps, NodeViewWrapper } from "@tiptap/react";
import { MessageSquareTextIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useUser } from "@/components/user-provider";
import { cn } from "@/lib/utils";
import { createMentionNodeExtension } from "./mention-node-extension";
import type { MentionItemRendererProps, PromptMentionEntity } from "./types";

/**
 * Prompt mention list item — used in slash-menu entity pickers.
 */
export function PromptMentionListItem({
	entity,
}: MentionItemRendererProps<PromptMentionEntity>) {
	const productSlug = (entity.parentSlug ?? "").split(":")[0];
	return (
		<>
			<MessageSquareTextIcon className="size-4 shrink-0 text-muted-foreground" />
			<div className="flex min-w-0 flex-1 items-center gap-2">
				<span className="truncate text-sm">{entity.name}</span>
				{productSlug ? (
					<span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground uppercase tracking-wide">
						{productSlug}
					</span>
				) : null}
			</div>
		</>
	);
}

/**
 * Prompt mention node — rendered inline as a compact entity-link pill.
 *
 * Storage shape:
 *   - id           = prompt UUID
 *   - label        = prompt name at insertion time
 *   - parentSlug   = "productSlug:promptSlug" (same encoding as iter3
 *                    globalSearchView parent_id, so routing needs no extra
 *                    network call).
 *
 * Click navigates to /prompts/[productSlug]/[promptSlug]. Cmd/Ctrl-click
 * opens in a new tab.
 */
function PromptMentionNodeComponent({ node }: NodeViewProps) {
	const { id, label, parentSlug } = node.attrs;
	const router = useRouter();
	const user = useUser();
	const [productSlug, promptSlug] = ((parentSlug as string | null) ?? "").split(
		":",
	);
	const basePath = user?.basePath ?? "";
	const target =
		productSlug && promptSlug
			? `${basePath}/prompts/${productSlug}/${promptSlug}`
			: `${basePath}/prompts`;

	return (
		<NodeViewWrapper
			as="button"
			type="button"
			className="inline"
			onClick={(event: React.MouseEvent) => {
				if (event.metaKey || event.ctrlKey) {
					window.open(target, "_blank");
					return;
				}
				router.push(target);
			}}
		>
			<span
				className={cn(
					"inline-flex items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-0.5 align-middle font-medium text-sm transition-colors hover:bg-muted",
				)}
				data-mention-type="prompt"
				data-mention-id={id}
			>
				<MessageSquareTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
				<span className="max-w-[300px] truncate">
					{label ?? "Untitled prompt"}
				</span>
				{productSlug ? (
					<span className="rounded-sm bg-muted px-1 text-[10px] text-muted-foreground uppercase tracking-wide">
						{productSlug}
					</span>
				) : null}
			</span>
		</NodeViewWrapper>
	);
}

/**
 * TipTap extension for prompt mentions.
 */
export const PromptMentionExtension = createMentionNodeExtension({
	name: "promptMention",
	entityName: "prompt",
	mentionType: "prompt",
	className:
		"inline-flex items-center gap-1.5 rounded-md border bg-muted/50 px-2 py-0.5 font-medium text-sm",
	attributes: {
		id: {
			default: null as string | null,
		},
		label: {
			default: null as string | null,
		},
		parentSlug: {
			default: null as string | null,
		},
	},
	nodeView: PromptMentionNodeComponent,
});

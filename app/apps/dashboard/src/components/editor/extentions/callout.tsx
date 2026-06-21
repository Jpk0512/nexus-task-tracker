"use client";

// Callout block — Notion-style aside with a coloured left rail and an icon.
// Variants: info / warn / tip / quote. Stored in markdown as a fenced
// `:::callout type="info"` block when serialised by tiptap-markdown.
//
// Per DESIGN.md:
//  - hairline 1px border, `--surface-2` background
//  - left rail 2px lavender (`--brand`) for "info", semantic colour otherwise
//  - 16px vertical rhythm, 12px inset on each side

import {
	mergeAttributes,
	Node,
	NodeViewContent,
	NodeViewWrapper,
	ReactNodeViewRenderer,
} from "@tiptap/react";
import {
	InfoIcon,
	LightbulbIcon,
	QuoteIcon,
	TriangleAlertIcon,
} from "lucide-react";

export type CalloutVariant = "info" | "warn" | "tip" | "quote";

const VARIANTS: Record<
	CalloutVariant,
	{ icon: typeof InfoIcon; railClass: string; iconClass: string; label: string }
> = {
	info: {
		icon: InfoIcon,
		railClass: "border-l-[var(--brand,theme(colors.violet.500))]",
		iconClass: "text-[var(--brand,theme(colors.violet.500))]",
		label: "Info",
	},
	warn: {
		icon: TriangleAlertIcon,
		railClass: "border-l-amber-500",
		iconClass: "text-amber-500",
		label: "Warning",
	},
	tip: {
		icon: LightbulbIcon,
		railClass: "border-l-emerald-500",
		iconClass: "text-emerald-500",
		label: "Tip",
	},
	quote: {
		icon: QuoteIcon,
		railClass: "border-l-muted-foreground",
		iconClass: "text-muted-foreground",
		label: "Quote",
	},
};

function CalloutView(props: {
	node: { attrs: { variant: CalloutVariant } };
	updateAttributes: (attrs: Record<string, unknown>) => void;
}) {
	const variant = (props.node.attrs.variant ?? "info") as CalloutVariant;
	const config = VARIANTS[variant] ?? VARIANTS.info;
	const Icon = config.icon;

	return (
		<NodeViewWrapper
			as="aside"
			data-callout-variant={variant}
			className={`my-4 flex gap-3 rounded-md border border-border border-l-2 bg-[var(--surface-2,theme(colors.muted))] px-3 py-2 ${config.railClass}`}
		>
			<button
				type="button"
				aria-label={`Cycle callout variant (current: ${config.label})`}
				onMouseDown={(e) => {
					// Don't steal focus from the editor caret.
					e.preventDefault();
					const order: CalloutVariant[] = ["info", "warn", "tip", "quote"];
					const next =
						order[(order.indexOf(variant) + 1) % order.length] ?? "info";
					props.updateAttributes({ variant: next });
				}}
				className={`mt-0.5 shrink-0 ${config.iconClass}`}
				contentEditable={false}
			>
				<Icon className="size-4" />
			</button>
			<div className="min-w-0 grow [&>p]:my-0">
				{/* `content` of the Node is rendered here via NodeViewContent. */}
				<NodeViewContent />
			</div>
		</NodeViewWrapper>
	);
}

export const Callout = Node.create({
	name: "callout",
	group: "block",
	// Permit paragraph content (and inline marks). Block-level descent — yes.
	content: "block+",
	defining: true,

	addAttributes() {
		return {
			variant: {
				default: "info",
				parseHTML: (el) => el.getAttribute("data-callout-variant") ?? "info",
				renderHTML: (attrs) => ({
					"data-callout-variant": String(attrs.variant ?? "info"),
				}),
			},
		};
	},

	parseHTML() {
		return [{ tag: "aside[data-callout-variant]" }];
	},

	renderHTML({ HTMLAttributes }) {
		return [
			"aside",
			mergeAttributes(HTMLAttributes, { "data-callout-variant": "info" }),
			0,
		];
	},

	addCommands() {
		return {
			setCallout:
				(attrs?: { variant?: CalloutVariant }) =>
				({ commands }: { commands: any }) =>
					commands.wrapIn(this.name, attrs ?? { variant: "info" }),
			toggleCallout:
				(attrs?: { variant?: CalloutVariant }) =>
				({ commands }: { commands: any }) =>
					commands.toggleWrap(this.name, attrs ?? { variant: "info" }),
		} as any;
	},

	addNodeView() {
		return ReactNodeViewRenderer(CalloutView as any);
	},
});


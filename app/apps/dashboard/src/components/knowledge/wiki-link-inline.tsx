"use client";

import { cn } from "@/lib/utils";

// Inline-rendered `[[note title]]` Obsidian-style wiki-link. Two states:
// resolved (target note exists → toNoteId is set) renders blue; unresolved
// (no matching note → toNoteId is null) renders red. Dotted underline
// distinguishes wiki-links from regular `<a>` href links. Palette spec §2.

const RESOLVED_CLASS =
	"text-blue-600 dark:text-blue-400 underline decoration-dotted underline-offset-2 cursor-pointer transition-colors duration-150 hover:text-blue-500 dark:hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-sm active:opacity-80";

const UNRESOLVED_CLASS =
	"text-red-600 dark:text-red-400 underline decoration-dotted underline-offset-2 cursor-pointer transition-colors duration-150 hover:text-red-500 dark:hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-sm opacity-80 active:opacity-60";

export type WikiLinkInlineProps = {
	text: string;
	toNoteId: string | null;
	onClick?: () => void;
};

export function WikiLinkInline({
	text,
	toNoteId,
	onClick,
}: WikiLinkInlineProps) {
	const resolved = toNoteId !== null;
	return (
		<button
			type="button"
			data-wiki-link
			data-resolved={resolved ? "true" : "false"}
			onClick={onClick}
			title={resolved ? text : `${text} — no matching note in this vault`}
			className={cn(resolved ? RESOLVED_CLASS : UNRESOLVED_CLASS)}
		>
			{text}
		</button>
	);
}

"use client";

import { Badge } from "@ui/components/ui/badge";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuItem,
	ContextMenuSeparator,
	ContextMenuTrigger,
} from "@ui/components/ui/context-menu";
import { cn } from "@ui/lib/utils";
import { ChevronRightIcon } from "lucide-react";

// Knowledge note row + collapsible group. Mirrors the GroupSection pattern used
// in documents-index-view.tsx so the Knowledge tab visually rhymes with
// Documents (both are vault-grouped left panes).

export type KnowledgeNoteRow = {
	id: string;
	name: string;
	relativePath: string;
	parentDir: string | null;
	title?: string | null;
	updatedAt?: string | Date | null;
};

export function NoteGroup({
	icon: Icon,
	title,
	count,
	notes,
	selectedId,
	defaultOpen = false,
	open,
	onOpenChange,
	onSelect,
	onPromote,
	onDelete,
	onOpenInVault,
}: {
	icon: any;
	title: string;
	count: number;
	notes: KnowledgeNoteRow[];
	selectedId: string | null;
	defaultOpen?: boolean;
	open?: boolean;
	onOpenChange?: (open: boolean) => void;
	onSelect: (id: string) => void;
	onPromote?: (note: KnowledgeNoteRow) => void;
	onDelete?: (note: KnowledgeNoteRow) => void;
	onOpenInVault?: (note: KnowledgeNoteRow) => void;
}) {
	return (
		<Collapsible
			defaultOpen={defaultOpen}
			open={open}
			onOpenChange={onOpenChange}
			className="border-border/60 border-b"
		>
			<CollapsibleTrigger className="group flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground [&[data-state=open]>svg]:rotate-90">
				<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
				<Icon className="size-3.5 shrink-0" />
				<span className="font-[510] uppercase tracking-[0.04em]">{title}</span>
				<Badge variant="outline" className="ml-1 h-4 px-1.5 font-normal">
					{count}
				</Badge>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<ul className="pb-1.5">
					{notes.length === 0 ? (
						<li className="px-9 py-1.5 text-[12px] text-muted-foreground italic">
							Nothing here yet.
						</li>
					) : (
						notes.map((n) => (
							<NoteRow
								key={n.id}
								note={n}
								active={selectedId === n.id}
								onSelect={onSelect}
								onPromote={onPromote}
								onDelete={onDelete}
								onOpenInVault={onOpenInVault}
							/>
						))
					)}
				</ul>
			</CollapsibleContent>
		</Collapsible>
	);
}

function NoteRow({
	note,
	active,
	onSelect,
	onPromote,
	onDelete,
	onOpenInVault,
}: {
	note: KnowledgeNoteRow;
	active: boolean;
	onSelect: (id: string) => void;
	onPromote?: (note: KnowledgeNoteRow) => void;
	onDelete?: (note: KnowledgeNoteRow) => void;
	onOpenInVault?: (note: KnowledgeNoteRow) => void;
}) {
	const label = note.title?.trim() || note.name;
	const folder = note.parentDir ? `/${note.parentDir.split(/[\\/]/)[0]}` : "/";
	return (
		<li>
			<ContextMenu>
				<ContextMenuTrigger asChild>
					<button
						type="button"
						onClick={() => onSelect(note.id)}
						className={cn(
							"group flex w-full items-center gap-2 px-9 py-1 text-left text-[13px] tracking-[-0.005em] transition-colors",
							active
								? "bg-accent font-[510] text-accent-foreground"
								: "font-[400] text-foreground hover:bg-accent/40",
						)}
						title={note.relativePath}
					>
						<span className="truncate">{label}</span>
						<span
							className={cn(
								"ml-auto shrink-0 text-[10.5px] text-muted-foreground/70 tracking-[0.02em] opacity-0 transition-opacity",
								"group-hover:opacity-100",
								active && "opacity-70",
							)}
						>
							{folder}
						</span>
					</button>
				</ContextMenuTrigger>
				<ContextMenuContent>
					<ContextMenuItem onSelect={() => onSelect(note.id)}>
						Open note
					</ContextMenuItem>
					{onPromote && (
						<ContextMenuItem onSelect={() => onPromote(note)}>
							Promote to Document
						</ContextMenuItem>
					)}
					{onOpenInVault && (
						<ContextMenuItem onSelect={() => onOpenInVault(note)}>
							Copy vault path
						</ContextMenuItem>
					)}
					{onDelete && (
						<>
							<ContextMenuSeparator />
							<ContextMenuItem
								onSelect={() => onDelete(note)}
								className="text-destructive focus:text-destructive"
							>
								Delete note
							</ContextMenuItem>
						</>
					)}
				</ContextMenuContent>
			</ContextMenu>
		</li>
	);
}

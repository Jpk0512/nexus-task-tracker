"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@ui/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { BookOpenTextIcon, FileTextIcon, PlusIcon, XIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { useUser } from "@/components/user-provider";
import { queryClient, trpc } from "@/utils/trpc";

type Props = {
	taskId: string;
};

/**
 * Linked content (Documents + Knowledge notes) panel inside a task.
 * Backed by the new `documents_on_tasks` + `knowledge_notes_on_tasks` join
 * tables (iter4 fix #5). Reverse backlinks on the doc/note page are wired
 * up separately by the backlinks agent.
 */
export const TaskLinkedContent = ({ taskId }: Props) => {
	const user = useUser();
	const router = useRouter();
	const basePath = user?.basePath || "";

	const linkedDocsQuery = trpc.tasks.getLinkedDocuments.queryOptions({
		taskId,
	});
	const linkedNotesQuery = trpc.tasks.getLinkedKnowledgeNotes.queryOptions({
		taskId,
	});

	const { data: linkedDocs = [] } = useQuery(linkedDocsQuery);
	const { data: linkedNotes = [] } = useQuery(linkedNotesQuery);

	const total = linkedDocs.length + linkedNotes.length;

	const invalidate = () => {
		queryClient.invalidateQueries(linkedDocsQuery);
		queryClient.invalidateQueries(linkedNotesQuery);
	};

	const { mutate: detachDocument } = useMutation(
		trpc.tasks.detachDocument.mutationOptions({
			onSettled: invalidate,
		}),
	);
	const { mutate: detachKnowledgeNote } = useMutation(
		trpc.tasks.detachKnowledgeNote.mutationOptions({
			onSettled: invalidate,
		}),
	);

	return (
		<Collapsible defaultOpen className="space-y-1">
			<div className="mb-2 flex items-center justify-between gap-2">
				<CollapsibleTrigger className="collapsible-chevron flex items-center gap-1">
					<h3 className="text-muted-foreground text-xs">
						Linked content{total > 0 ? ` +${total}` : ""}
					</h3>
				</CollapsibleTrigger>
				<AttachPopover taskId={taskId} onAttached={invalidate} />
			</div>

			<CollapsibleContent>
				{total === 0 && (
					<div className="px-3 py-2 text-muted-foreground text-xs">
						No linked documents or notes.
					</div>
				)}

				{linkedDocs.map((doc) => (
					<LinkedItem
						key={`doc:${doc.id}`}
						icon={
							<FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
						}
						label={doc.name}
						onOpen={() => router.push(`${basePath}/documents/${doc.id}`)}
						onDetach={() => detachDocument({ taskId, documentId: doc.id })}
					/>
				))}

				{linkedNotes.map((note) => (
					<LinkedItem
						key={`note:${note.id}`}
						icon={
							<BookOpenTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
						}
						label={note.name}
						sublabel={note.parentDir ?? undefined}
						onOpen={() => router.push(`${basePath}/knowledge?note=${note.id}`)}
						onDetach={() => detachKnowledgeNote({ taskId, noteId: note.id })}
					/>
				))}
			</CollapsibleContent>
		</Collapsible>
	);
};

const LinkedItem = ({
	icon,
	label,
	sublabel,
	onOpen,
	onDetach,
}: {
	icon: React.ReactNode;
	label: string;
	sublabel?: string;
	onOpen: () => void;
	onDetach: () => void;
}) => {
	return (
		<div className="group flex items-center gap-2 rounded-md px-3 py-1.5 text-sm hover:bg-accent dark:hover:bg-accent/30">
			{icon}
			<button
				type="button"
				onClick={onOpen}
				className="flex min-w-0 flex-1 items-center gap-2 text-left"
			>
				<span className="truncate">{label}</span>
				{sublabel && (
					<span className="truncate text-muted-foreground text-xs">
						{sublabel}
					</span>
				)}
			</button>
			<Button
				type="button"
				variant="ghost"
				size="icon"
				className="size-6! p-0 opacity-0 group-hover:opacity-100"
				aria-label="Detach"
				onClick={(e) => {
					e.stopPropagation();
					onDetach();
				}}
			>
				<XIcon className="size-3.5" />
			</Button>
		</div>
	);
};

type SearchHit = {
	id: string;
	type: string;
	title: string;
};

const AttachPopover = ({
	taskId,
	onAttached,
}: {
	taskId: string;
	onAttached: () => void;
}) => {
	const [open, setOpen] = useState(false);
	const [search, setSearch] = useState("");
	const [debouncedSearch] = useDebounceValue(search, 250);

	// NOTE: trpc.globalSearch.search supports a `type` filter param but the
	// underlying SQL builder mishandles array params (`sql.join` produces
	// malformed `IN ($1$2$3)` for multi-element arrays). Pull all results
	// and filter client-side instead until the query helper is fixed.
	const { data } = useQuery(
		trpc.globalSearch.search.queryOptions(
			{
				search: debouncedSearch,
			},
			{ enabled: open },
		),
	);

	const { mutate: attachDocument } = useMutation(
		trpc.tasks.attachDocument.mutationOptions({
			onSettled: () => onAttached(),
		}),
	);
	const { mutate: attachKnowledgeNote } = useMutation(
		trpc.tasks.attachKnowledgeNote.mutationOptions({
			onSettled: () => onAttached(),
		}),
	);

	const items = (data as SearchHit[] | undefined) ?? [];
	const docs = items.filter((i) => i.type === "document");
	const notes = items.filter((i) => i.type === "knowledge");

	const handleSelect = (item: SearchHit) => {
		if (item.type === "document") {
			attachDocument({ taskId, documentId: item.id });
		} else if (item.type === "knowledge") {
			attachKnowledgeNote({ taskId, noteId: item.id });
		}
		setOpen(false);
		setSearch("");
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className="size-6! p-0 text-muted-foreground hover:text-foreground"
					aria-label="Attach document or knowledge note"
				>
					<PlusIcon className="size-3.5" />
				</Button>
			</PopoverTrigger>
			<PopoverContent align="end" className="w-80 p-0">
				<Command shouldFilter={false}>
					<CommandInput
						value={search}
						onValueChange={setSearch}
						placeholder="Search docs & knowledge..."
					/>
					<CommandList className="max-h-72">
						<CommandEmpty>No results.</CommandEmpty>
						{docs.length > 0 && (
							<CommandGroup heading="Documents">
								{docs.map((item) => (
									<CommandItem
										key={item.id}
										value={`doc:${item.id}`}
										onSelect={() => handleSelect(item)}
									>
										<FileTextIcon className="size-3.5 text-muted-foreground" />
										<span className="truncate">{item.title}</span>
									</CommandItem>
								))}
							</CommandGroup>
						)}
						{notes.length > 0 && (
							<CommandGroup heading="Knowledge">
								{notes.map((item) => (
									<CommandItem
										key={item.id}
										value={`note:${item.id}`}
										onSelect={() => handleSelect(item)}
									>
										<BookOpenTextIcon className="size-3.5 text-muted-foreground" />
										<span className="truncate">{item.title}</span>
									</CommandItem>
								))}
							</CommandGroup>
						)}
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
};

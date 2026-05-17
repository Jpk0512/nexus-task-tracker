"use client";

import type { DragEndEvent } from "@dnd-kit/core";
import {
	arrayMove,
	SortableContext,
	useSortable,
	verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Checkbox } from "@ui/components/ui/checkbox";
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
	ContextMenuSub,
	ContextMenuSubContent,
	ContextMenuSubTrigger,
	ContextMenuTrigger,
} from "@ui/components/ui/context-menu";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { Input } from "@ui/components/ui/input";
import { Kbd } from "@ui/components/ui/kbd";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { Skeleton } from "@ui/components/ui/skeleton";
import {
	ArrowUpRightIcon,
	BoxIcon,
	ChevronRightIcon,
	FileTextIcon,
	GripVerticalIcon,
	LinkIcon,
	PaperclipIcon,
	PlusIcon,
	SaveIcon,
	SearchIcon,
	StickyNoteIcon,
	Trash2Icon,
	XIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";
import { JkHint } from "@/components/jk-hint";
import { BulkOpsBar, useBindBulkSelection } from "@/components/tasks/bulk-ops-bar";
import { MetadataConflictBadge } from "@/components/tasks/metadata-conflict-badge";
import {
	TaskToolbar,
	type TaskGroupBy,
	useToolbarGroupBy,
} from "@/components/tasks/task-toolbar";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { useOptimisticAction } from "@/hooks/use-optimistic-action";
import { useShortcut } from "@/hooks/use-shortcuts";
import { useTaskParams } from "@/hooks/use-task-params";
import { useTaskSelection } from "@/stores/task-selection";
import { trpc } from "@/utils/trpc";
import { useTodoSortableHandler } from "./todo-dnd-provider";

type Todo = {
	id: string;
	content: string;
	projectId: string | null;
	projectName: string | null;
	projectPrefix: string | null;
	checked: boolean;
	checkedAt: string | null;
	tags: string[];
	order: number;
	attachmentCount: number;
};

type Project = { id: string; name: string };

// ─── DocPicker ──────────────────────────────────────────────────────────────
// Used from the right-click "Link to Doc" item. Debounced search + click to
// attach. Mounted inside the ContextMenu's Popover.
function DocPicker({
	onPick,
}: {
	onPick: (docId: string, title: string) => void;
}) {
	const [search, setSearch] = useState("");
	const { data } = useQuery(
		trpc.documents.get.queryOptions({
			pageSize: 20,
			...(search ? { search } : {}),
		} as any),
	);
	const docs = ((data as any)?.data ?? []) as Array<{
		id: string;
		name: string | null;
	}>;
	return (
		<div className="space-y-2">
			<div className="relative">
				<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground" />
				<Input
					autoFocus
					value={search}
					onChange={(e) => setSearch(e.target.value)}
					placeholder="Search documents…"
					className="h-8 pl-7"
				/>
			</div>
			<ul className="max-h-60 overflow-y-auto">
				{docs.length === 0 && (
					<li className="px-2 py-2 text-[12px] text-muted-foreground italic">
						No documents found.
					</li>
				)}
				{docs.map((d) => (
					<li key={d.id}>
						<button
							type="button"
							onClick={() => onPick(d.id, d.name ?? "Untitled")}
							className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px] text-foreground hover:bg-accent/60"
						>
							<FileTextIcon className="size-3.5 text-muted-foreground" />
							<span className="truncate">{d.name ?? "Untitled"}</span>
						</button>
					</li>
				))}
			</ul>
		</div>
	);
}

// ─── TodoRow ────────────────────────────────────────────────────────────────
function TodoRow({
	todo,
	projects,
	onCheck,
	onUncheck,
	onDelete,
	onOpen,
	onAddTag,
	onRemoveTag,
	onLinkProject,
	onLinkDoc,
	onPromote,
	onToggleSelect,
	isFocused = false,
	isSelected = false,
}: {
	todo: Todo;
	projects: Project[];
	onCheck: () => void;
	onUncheck: () => void;
	onDelete: () => void;
	onOpen: () => void;
	onAddTag: (tag: string) => void;
	onRemoveTag: (tag: string) => void;
	onLinkProject: (projectId: string | null) => void;
	onLinkDoc: (docId: string, title: string) => void;
	onPromote: () => void;
	onToggleSelect?: (extend: boolean) => void;
	isFocused?: boolean;
	isSelected?: boolean;
}) {
	const {
		attributes,
		listeners,
		setNodeRef,
		transform,
		transition,
		isDragging,
	} = useSortable({ id: todo.id });
	const [newTag, setNewTag] = useState("");
	const [docPickerOpen, setDocPickerOpen] = useState(false);
	const style = {
		transform: CSS.Transform.toString(transform),
		transition,
		opacity: isDragging ? 0.5 : 1,
	};

	return (
		<>
			<ContextMenu>
				<ContextMenuTrigger asChild>
					<div
						ref={setNodeRef}
						style={style}
						data-jk-row={todo.id}
						data-selected={isSelected || undefined}
						onClick={(e) => {
							// Shift+click is the mouse-equivalent of the shift+x range
							// shortcut — extends selection from the last anchor to here.
							if (e.shiftKey && onToggleSelect) {
								e.preventDefault();
								onToggleSelect(true);
							}
						}}
						className={`group flex items-start gap-2 rounded-md border px-2 py-1.5 transition hover:border-border hover:bg-accent/30 ${
							isFocused
								? "border-violet-400/70 ring-2 ring-violet-400/40"
								: isSelected
									? "border-primary/50 bg-primary/[0.04]"
									: "border-transparent"
						} ${todo.checked ? "opacity-60" : ""}`}
					>
						<button
							type="button"
							{...attributes}
							{...listeners}
							className="cursor-grab pt-1 text-muted-foreground opacity-0 transition active:cursor-grabbing group-hover:opacity-60"
							aria-label="Drag handle"
						>
							<GripVerticalIcon className="size-4" />
						</button>
						<Checkbox
							checked={todo.checked}
							onCheckedChange={(v) => (v ? onCheck() : onUncheck())}
							className="mt-1.5"
						/>
						<button
							type="button"
							onClick={onOpen}
							className="min-w-0 grow text-left"
						>
							<div
								className={`flex items-center gap-1.5 text-sm ${
									todo.checked
										? "text-muted-foreground line-through"
										: "text-foreground"
								}`}
							>
								<span className="min-w-0 flex-1 truncate">{todo.content}</span>
								<MetadataConflictBadge
									task={{
										id: todo.id,
										title: todo.content,
										// Todos use `checked` rather than a status enum; mirror it
										// to the rule input so future rules can target checked
										// todos with conflicting metadata.
										statusType: todo.checked ? "done" : "to_do",
									}}
								/>
							</div>
							<div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
								{todo.projectName && (
									<Badge variant="outline" className="font-normal">
										{todo.projectPrefix ? `${todo.projectPrefix} · ` : ""}
										{todo.projectName}
									</Badge>
								)}
								{todo.tags.map((t) => (
									<Badge
										key={t}
										variant="outline"
										className="gap-1 font-normal text-xs"
									>
										{t}
										<span
											role="button"
											tabIndex={0}
											onClick={(e) => {
												e.stopPropagation();
												onRemoveTag(t);
											}}
											onKeyDown={(e) => {
												if (e.key === "Enter") {
													e.stopPropagation();
													onRemoveTag(t);
												}
											}}
											className="hover:text-destructive"
										>
											<XIcon className="size-3" />
										</span>
									</Badge>
								))}
								<form
									onClick={(e) => e.stopPropagation()}
									onSubmit={(e) => {
										e.preventDefault();
										if (newTag.trim()) {
											onAddTag(newTag.trim());
											setNewTag("");
										}
									}}
								>
									<input
										value={newTag}
										onChange={(e) => setNewTag(e.target.value)}
										onClick={(e) => e.stopPropagation()}
										placeholder="+ tag"
										className="h-5 w-16 rounded border-border border-b border-dashed bg-transparent px-1 text-xs outline-none focus:border-primary focus:border-solid"
									/>
								</form>
								{todo.attachmentCount > 0 && (
									<Badge
										variant="outline"
										className="gap-1 font-normal text-xs"
									>
										<PaperclipIcon className="size-3" />
										{todo.attachmentCount}
									</Badge>
								)}
							</div>
						</button>
						<button
							type="button"
							onClick={onDelete}
							className="text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-60"
							aria-label="Delete todo"
						>
							<Trash2Icon className="size-3.5" />
						</button>
					</div>
				</ContextMenuTrigger>
				<ContextMenuContent className="w-56">
					<ContextMenuItem onClick={onPromote}>
						<ArrowUpRightIcon className="text-muted-foreground" />
						Promote to Task
					</ContextMenuItem>
					<ContextMenuSeparator />
					<ContextMenuSub>
						<ContextMenuSubTrigger className="flex items-center gap-2">
							<BoxIcon className="text-muted-foreground" />
							Link to Project
						</ContextMenuSubTrigger>
						<ContextMenuSubContent className="max-h-64 w-56 overflow-y-auto">
							<ContextMenuItem onClick={() => onLinkProject(null)}>
								<BoxIcon className="text-muted-foreground" />
								No project
							</ContextMenuItem>
							{projects.map((p) => (
								<ContextMenuItem key={p.id} onClick={() => onLinkProject(p.id)}>
									<BoxIcon className="text-muted-foreground" />
									{p.name}
								</ContextMenuItem>
							))}
						</ContextMenuSubContent>
					</ContextMenuSub>
					<ContextMenuItem
						onSelect={(e) => {
							// Keep menu logic out of the way; we drive a popover below.
							e.preventDefault();
							setDocPickerOpen(true);
						}}
					>
						<LinkIcon className="text-muted-foreground" />
						Link to Doc…
					</ContextMenuItem>
					<ContextMenuSeparator />
					<ContextMenuItem variant="destructive" onClick={onDelete}>
						<Trash2Icon />
						Delete
					</ContextMenuItem>
				</ContextMenuContent>
			</ContextMenu>

			{/*
			 * Doc picker popover lives outside the context menu so it survives the
			 * menu closing. It anchors to the row itself (modal-less).
			 */}
			<Popover open={docPickerOpen} onOpenChange={setDocPickerOpen}>
				<PopoverTrigger asChild>
					<span className="sr-only" aria-hidden />
				</PopoverTrigger>
				<PopoverContent
					align="start"
					className="w-80 p-2"
					onOpenAutoFocus={(e) => e.preventDefault()}
				>
					<DocPicker
						onPick={(docId, title) => {
							onLinkDoc(docId, title);
							setDocPickerOpen(false);
						}}
					/>
				</PopoverContent>
			</Popover>
		</>
	);
}

// ─── AttachmentsModal (unchanged) ───────────────────────────────────────────
function AttachmentsModal({
	todoId,
	onClose,
}: {
	todoId: string;
	onClose: () => void;
}) {
	const qc = useQueryClient();
	const { data: todo } = useQuery(
		trpc.todos.getById.queryOptions({ id: todoId }),
	);
	const [draft, setDraft] = useState<{ title: string; content: string }>({
		title: "",
		content: "",
	});

	const refetch = () => {
		qc.invalidateQueries({ queryKey: [["todos", "getById"]] });
		qc.invalidateQueries({ queryKey: [["todos", "get"]] });
	};

	const attachMut = useMutation(
		trpc.todos.attach.mutationOptions({
			onSuccess: () => {
				setDraft({ title: "", content: "" });
				refetch();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const detachMut = useMutation(
		trpc.todos.detach.mutationOptions({
			onSuccess: refetch,
			onError: (e) => toast.error(e.message),
		}),
	);
	const updateAttMut = useMutation(
		trpc.todos.updateAttachment.mutationOptions({
			onSuccess: refetch,
			onError: (e) => toast.error(e.message),
		}),
	);

	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-h-[80vh] max-w-2xl overflow-hidden">
				<DialogHeader>
					<DialogTitle className="line-clamp-2 pr-8 text-base">
						{todo?.content ?? "Todo"}
					</DialogTitle>
				</DialogHeader>
				<div className="max-h-[60vh] space-y-4 overflow-y-auto px-1">
					{(todo?.attachments ?? []).map((a) => (
						<div
							key={a.id}
							className="rounded-md border border-border bg-card/40 p-3"
						>
							<div className="mb-1 flex items-center justify-between">
								<div className="flex items-center gap-2">
									{a.kind === "note" ? (
										<StickyNoteIcon className="size-3.5 text-amber-500" />
									) : (
										<LinkIcon className="size-3.5 text-sky-500" />
									)}
									<input
										defaultValue={a.title}
										onBlur={(e) => {
											if (e.target.value !== a.title) {
												updateAttMut.mutate({
													attachmentId: a.id,
													title: e.target.value,
												});
											}
										}}
										className="bg-transparent font-medium text-sm outline-none"
									/>
								</div>
								<Button
									variant="ghost"
									size="sm"
									onClick={() => detachMut.mutate({ attachmentId: a.id })}
									className="text-muted-foreground hover:text-destructive"
								>
									<Trash2Icon className="size-3.5" />
								</Button>
							</div>
							{a.kind === "note" ? (
								<textarea
									defaultValue={a.content ?? ""}
									onBlur={(e) => {
										if (e.target.value !== (a.content ?? "")) {
											updateAttMut.mutate({
												attachmentId: a.id,
												content: e.target.value,
											});
										}
									}}
									className="h-32 w-full resize-y rounded border border-border bg-background p-2 font-mono text-xs"
								/>
							) : (
								<Link
									href={`/team/${(typeof window !== "undefined" && location.pathname.split("/")[2]) || ""}/documents/${a.docId}`}
									className="text-primary text-sm underline"
								>
									Open document →
								</Link>
							)}
						</div>
					))}
					{(todo?.attachments ?? []).length === 0 && (
						<p className="text-center text-muted-foreground text-sm italic">
							No attachments yet.
						</p>
					)}

					<form
						onSubmit={(e) => {
							e.preventDefault();
							if (!draft.title.trim()) return;
							attachMut.mutate({
								todoId,
								kind: "note",
								title: draft.title.trim(),
								content: draft.content,
							});
						}}
						className="space-y-2 rounded-md border border-border border-dashed bg-card/20 p-3"
					>
						<div className="flex items-center gap-2 text-muted-foreground text-xs">
							<StickyNoteIcon className="size-3.5" />
							Add a note
						</div>
						<Input
							value={draft.title}
							onChange={(e) => setDraft({ ...draft, title: e.target.value })}
							placeholder="Note title"
							className="h-8"
						/>
						<textarea
							value={draft.content}
							onChange={(e) => setDraft({ ...draft, content: e.target.value })}
							placeholder="Note body (markdown)…"
							className="h-24 w-full resize-y rounded border border-border bg-background p-2 font-mono text-xs"
						/>
						<Button
							type="submit"
							size="sm"
							disabled={attachMut.isPending || !draft.title.trim()}
						>
							<SaveIcon className="size-3.5" /> Save note
						</Button>
					</form>
				</div>
			</DialogContent>
		</Dialog>
	);
}

// ─── Inline composer (always rendered above the list) ───────────────────────
function InlineComposer({
	projects,
	composerRef,
	isPending,
	onSubmit,
}: {
	projects: Project[];
	composerRef: React.RefObject<HTMLInputElement | null>;
	isPending: boolean;
	onSubmit: (content: string, projectId: string | null) => void;
}) {
	const [content, setContent] = useState("");
	const [projectId, setProjectId] = useState<string>("none");

	const submit = () => {
		const trimmed = content.trim();
		if (!trimmed) return;
		onSubmit(trimmed, projectId === "none" ? null : projectId);
		setContent("");
	};

	return (
		<form
			onSubmit={(e) => {
				e.preventDefault();
				submit();
			}}
			className="group flex items-center gap-2 rounded-md border border-transparent px-2 py-1.5 transition hover:border-border/60 hover:bg-accent/20"
		>
			<PlusIcon className="size-4 text-muted-foreground" />
			<Input
				ref={composerRef}
				value={content}
				onChange={(e) => setContent(e.target.value)}
				onKeyDown={(e) => {
					if (e.key === "Escape") {
						setContent("");
						(e.target as HTMLInputElement).blur();
					}
				}}
				placeholder="What needs doing?"
				disabled={isPending}
				className="h-7 flex-1 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
			/>
			<Select value={projectId} onValueChange={setProjectId}>
				<SelectTrigger className="h-7 w-36 border-0 bg-transparent text-[12px] text-muted-foreground shadow-none hover:bg-accent/40 focus:ring-0 focus:ring-offset-0">
					<SelectValue placeholder="No project" />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value="none">No project</SelectItem>
					{projects.map((p) => (
						<SelectItem key={p.id} value={p.id}>
							{p.name}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<Kbd className="opacity-0 transition group-focus-within:opacity-100 group-hover:opacity-100">
				N
			</Kbd>
		</form>
	);
}

// ─── Completed disclosure (collapsible at the bottom) ───────────────────────
function CompletedSection({
	todos,
	projects,
	onCheck,
	onUncheck,
	onDelete,
	onOpen,
	onAddTag,
	onRemoveTag,
	onLinkProject,
	onLinkDoc,
	onPromote,
}: {
	todos: Todo[];
	projects: Project[];
	onCheck: (id: string) => void;
	onUncheck: (id: string) => void;
	onDelete: (id: string) => void;
	onOpen: (id: string) => void;
	onAddTag: (id: string, tag: string) => void;
	onRemoveTag: (id: string, tag: string) => void;
	onLinkProject: (id: string, projectId: string | null) => void;
	onLinkDoc: (id: string, docId: string, title: string) => void;
	onPromote: (todo: Todo) => void;
}) {
	if (todos.length === 0) return null;
	return (
		<Collapsible className="mt-6 border-border/60 border-t pt-3">
			<CollapsibleTrigger className="group flex w-full items-center gap-2 px-2 py-1 text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground [&[data-state=open]>svg]:rotate-90">
				<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
				<span className="font-[510] uppercase tracking-[0.04em]">
					Completed
				</span>
				<Badge variant="outline" className="h-4 px-1.5 font-normal">
					{todos.length}
				</Badge>
			</CollapsibleTrigger>
			<CollapsibleContent>
				<div className="mt-2 space-y-1">
					{/*
					 * We deliberately render these OUTSIDE the active SortableContext —
					 * the user shouldn't be reordering completed todos by drag. Each
					 * row still gets its full context menu.
					 */}
					{todos.map((t) => (
						<StaticTodoRow
							key={t.id}
							todo={t}
							projects={projects}
							onCheck={() => onCheck(t.id)}
							onUncheck={() => onUncheck(t.id)}
							onDelete={() => onDelete(t.id)}
							onOpen={() => onOpen(t.id)}
							onAddTag={(tag) => onAddTag(t.id, tag)}
							onRemoveTag={(tag) => onRemoveTag(t.id, tag)}
							onLinkProject={(pid) => onLinkProject(t.id, pid)}
							onLinkDoc={(did, title) => onLinkDoc(t.id, did, title)}
							onPromote={() => onPromote(t)}
						/>
					))}
				</div>
			</CollapsibleContent>
		</Collapsible>
	);
}

// Non-sortable variant of TodoRow for the Completed section. Same props minus
// the dnd-kit hook so we don't pollute the active SortableContext.
function StaticTodoRow({
	todo,
	projects,
	onCheck,
	onUncheck,
	onDelete,
	onOpen,
	onAddTag,
	onRemoveTag,
	onLinkProject,
	onLinkDoc,
	onPromote,
}: {
	todo: Todo;
	projects: Project[];
	onCheck: () => void;
	onUncheck: () => void;
	onDelete: () => void;
	onOpen: () => void;
	onAddTag: (tag: string) => void;
	onRemoveTag: (tag: string) => void;
	onLinkProject: (projectId: string | null) => void;
	onLinkDoc: (docId: string, title: string) => void;
	onPromote: () => void;
}) {
	const [newTag, setNewTag] = useState("");
	const [docPickerOpen, setDocPickerOpen] = useState(false);
	return (
		<>
			<ContextMenu>
				<ContextMenuTrigger asChild>
					<div className="group flex items-start gap-2 rounded-md border border-transparent px-2 py-1.5 opacity-70 transition hover:border-border hover:bg-accent/30 hover:opacity-100">
						<span className="w-4 shrink-0" />
						<Checkbox
							checked={todo.checked}
							onCheckedChange={(v) => (v ? onCheck() : onUncheck())}
							className="mt-1.5"
						/>
						<button
							type="button"
							onClick={onOpen}
							className="min-w-0 grow text-left"
						>
							<div className="text-muted-foreground text-sm line-through">
								{todo.content}
							</div>
							<div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
								{todo.projectName && (
									<Badge variant="outline" className="font-normal">
										{todo.projectPrefix ? `${todo.projectPrefix} · ` : ""}
										{todo.projectName}
									</Badge>
								)}
								{todo.tags.map((t) => (
									<Badge
										key={t}
										variant="outline"
										className="gap-1 font-normal text-xs"
									>
										{t}
										<span
											role="button"
											tabIndex={0}
											onClick={(e) => {
												e.stopPropagation();
												onRemoveTag(t);
											}}
											onKeyDown={(e) => {
												if (e.key === "Enter") {
													e.stopPropagation();
													onRemoveTag(t);
												}
											}}
											className="hover:text-destructive"
										>
											<XIcon className="size-3" />
										</span>
									</Badge>
								))}
								<form
									onClick={(e) => e.stopPropagation()}
									onSubmit={(e) => {
										e.preventDefault();
										if (newTag.trim()) {
											onAddTag(newTag.trim());
											setNewTag("");
										}
									}}
								>
									<input
										value={newTag}
										onChange={(e) => setNewTag(e.target.value)}
										onClick={(e) => e.stopPropagation()}
										placeholder="+ tag"
										className="h-5 w-16 rounded border-border border-b border-dashed bg-transparent px-1 text-xs outline-none focus:border-primary focus:border-solid"
									/>
								</form>
								{todo.attachmentCount > 0 && (
									<Badge
										variant="outline"
										className="gap-1 font-normal text-xs"
									>
										<PaperclipIcon className="size-3" />
										{todo.attachmentCount}
									</Badge>
								)}
							</div>
						</button>
						<button
							type="button"
							onClick={onDelete}
							className="text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-60"
							aria-label="Delete todo"
						>
							<Trash2Icon className="size-3.5" />
						</button>
					</div>
				</ContextMenuTrigger>
				<ContextMenuContent className="w-56">
					<ContextMenuItem onClick={onPromote}>
						<ArrowUpRightIcon className="text-muted-foreground" />
						Promote to Task
					</ContextMenuItem>
					<ContextMenuSeparator />
					<ContextMenuSub>
						<ContextMenuSubTrigger className="flex items-center gap-2">
							<BoxIcon className="text-muted-foreground" />
							Link to Project
						</ContextMenuSubTrigger>
						<ContextMenuSubContent className="max-h-64 w-56 overflow-y-auto">
							<ContextMenuItem onClick={() => onLinkProject(null)}>
								<BoxIcon className="text-muted-foreground" />
								No project
							</ContextMenuItem>
							{projects.map((p) => (
								<ContextMenuItem key={p.id} onClick={() => onLinkProject(p.id)}>
									<BoxIcon className="text-muted-foreground" />
									{p.name}
								</ContextMenuItem>
							))}
						</ContextMenuSubContent>
					</ContextMenuSub>
					<ContextMenuItem
						onSelect={(e) => {
							e.preventDefault();
							setDocPickerOpen(true);
						}}
					>
						<LinkIcon className="text-muted-foreground" />
						Link to Doc…
					</ContextMenuItem>
					<ContextMenuSeparator />
					<ContextMenuItem variant="destructive" onClick={onDelete}>
						<Trash2Icon />
						Delete
					</ContextMenuItem>
				</ContextMenuContent>
			</ContextMenu>
			<Popover open={docPickerOpen} onOpenChange={setDocPickerOpen}>
				<PopoverTrigger asChild>
					<span className="sr-only" aria-hidden />
				</PopoverTrigger>
				<PopoverContent
					align="start"
					className="w-80 p-2"
					onOpenAutoFocus={(e) => e.preventDefault()}
				>
					<DocPicker
						onPick={(docId, title) => {
							onLinkDoc(docId, title);
							setDocPickerOpen(false);
						}}
					/>
				</PopoverContent>
			</Popover>
		</>
	);
}

// ─── TodosView (main) ──────────────────────────────────────────────────────
export function TodosView() {
	const qc = useQueryClient();
	const { setParams: setTaskParams } = useTaskParams();
	const todosQuery = useQuery(trpc.todos.get.queryOptions(undefined));
	const projectsQuery = useQuery(
		trpc.projects.get.queryOptions({ pageSize: 100 } as any),
	);

	const [openTodoId, setOpenTodoId] = useState<string | null>(null);
	const composerRef = useRef<HTMLInputElement | null>(null);

	// `N` (case-insensitive, no modifiers, ignores typing-in-input) focuses the
	// composer from anywhere on the todos page.
	useHotkeys(
		"n",
		(e) => {
			e.preventDefault();
			composerRef.current?.focus();
		},
		{ preventDefault: true },
	);

	const refetch = () => qc.invalidateQueries({ queryKey: [["todos", "get"]] });

	// Optimistic toggle helper — flip `checked` (+ checkedAt) on a single todo
	// in every cached `todos.get` query response. Returns a snapshot of the
	// previous cache contents so `onError` can roll back.
	const todosQueryKey = trpc.todos.get.queryKey();
	const applyTodoCheckedOptimistic = async (
		id: string,
		nextChecked: boolean,
	) => {
		await qc.cancelQueries({ queryKey: todosQueryKey });
		const previous = qc.getQueriesData({ queryKey: todosQueryKey });
		qc.setQueriesData({ queryKey: todosQueryKey }, (old: any) => {
			if (!Array.isArray(old)) return old;
			return old.map((t: Todo) =>
				t.id === id
					? {
							...t,
							checked: nextChecked,
							checkedAt: nextChecked ? new Date().toISOString() : null,
						}
					: t,
			);
		});
		return { previous };
	};
	const rollbackTodos = (
		ctx: { previous: Array<[unknown, unknown]> } | undefined,
	) => {
		if (!ctx?.previous) return;
		for (const [key, snapshot] of ctx.previous) {
			qc.setQueryData(key as any, snapshot);
		}
	};

	const createMut = useMutation(
		trpc.todos.create.mutationOptions({
			onSuccess: refetch,
			onError: (e) => toast.error(e.message),
		}),
	);
	const checkMut = useMutation(
		trpc.todos.check.mutationOptions({
			onMutate: ({ id }: { id: string }) =>
				applyTodoCheckedOptimistic(id, true),
			onError: (e: { message?: string }, _vars: unknown, ctx: unknown) => {
				rollbackTodos(ctx as any);
				toast.error(e?.message ?? "Couldn't check todo");
			},
			// Re-sync from server in the background. UI stays put while it flies.
			onSettled: refetch,
		}),
	);
	const uncheckMut = useMutation(
		trpc.todos.uncheck.mutationOptions({
			onMutate: ({ id }: { id: string }) =>
				applyTodoCheckedOptimistic(id, false),
			onError: (e: { message?: string }, _vars: unknown, ctx: unknown) => {
				rollbackTodos(ctx as any);
				toast.error(e?.message ?? "Couldn't uncheck todo");
			},
			onSettled: refetch,
		}),
	);
	const deleteMut = useMutation(
		trpc.todos.delete.mutationOptions({ onSuccess: refetch }),
	);
	const reorderMut = useMutation(
		trpc.todos.reorder.mutationOptions({ onSuccess: refetch }),
	);
	const updateMut = useMutation(
		trpc.todos.update.mutationOptions({ onSuccess: refetch }),
	);
	const attachMut = useMutation(
		trpc.todos.attach.mutationOptions({
			onSuccess: () => {
				refetch();
				toast.success("Document linked");
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const allTodos = (todosQuery.data ?? []) as Todo[];
	const projects = ((projectsQuery.data as { data: Project[] } | undefined)
		?.data ?? []) as Project[];

	// j/k navigation over the active list. Enter opens the todo's
	// attachments modal — same affordance as clicking the row body.
	const jkIds = useMemo(
		() => allTodos.filter((t) => !t.checked).map((t) => t.id),
		[allTodos],
	);
	const todoByIdForJk = useMemo(() => {
		const m = new Map<string, Todo>();
		for (const t of allTodos) m.set(t.id, t);
		return m;
	}, [allTodos]);
	const jk = useJkNavigation({
		ids: jkIds,
		onOpen: (id) => setOpenTodoId(id),
		toastLabel: (id) => {
			const t = todoByIdForJk.get(id);
			const content =
				(t as { content?: string | null } | undefined)?.content?.trim() ?? "";
			const short = content.length > 40 ? `${content.slice(0, 40)}…` : content;
			return `Opened ${short || "todo"}`;
		},
	});

	const { activeTodos, completedTodos } = useMemo(() => {
		const active: Todo[] = [];
		const completed: Todo[] = [];
		for (const t of allTodos) {
			if (t.checked) completed.push(t);
			else active.push(t);
		}
		return { activeTodos: active, completedTodos: completed };
	}, [allTodos]);

	// The DndContext now lives at the layout level (TodoDndProvider) so todos
	// can be dragged onto sidebar project rows. We register our reorder logic
	// here; the provider routes project-droppable drops to a different handler.
	const registerSortableHandler = useTodoSortableHandler();

	const handleDragEnd = (e: DragEndEvent) => {
		const { active, over } = e;
		if (!over || active.id === over.id) return;
		// Drag-drop is scoped to the active (unchecked) list only.
		const ids = activeTodos.map((t) => t.id);
		const oldIdx = ids.indexOf(active.id as string);
		const newIdx = ids.indexOf(over.id as string);
		if (oldIdx < 0 || newIdx < 0) return;
		const reordered = arrayMove(ids, oldIdx, newIdx);
		reorderMut.mutate({ orderedIds: reordered });
	};

	useEffect(() => {
		if (!registerSortableHandler) return;
		registerSortableHandler(handleDragEnd);
		return () => registerSortableHandler(null);
		// `handleDragEnd` closes over `activeTodos` — re-register when it changes.
		// biome-ignore lint/correctness/useExhaustiveDependencies: closure-aware
	}, [registerSortableHandler, activeTodos]);

	const onPromote = (todo: Todo) => {
		// Open the centered create-task dialog with the todo's content prefilled.
		// When the task is created via the dialog, we don't have a hook back here
		// to delete the todo — so we optimistically delete now. If the user
		// cancels the dialog, the todo is gone but they still have it in toast
		// undo? For now: delete on promote. (Linear's "convert" pattern is also
		// destructive.)
		setTaskParams({
			createTask: true,
			taskTitle: todo.content,
			taskProjectId: todo.projectId ?? null,
		});
		deleteMut.mutate({ id: todo.id });
	};

	const onLinkProject = (todoId: string, projectId: string | null) => {
		updateMut.mutate({ id: todoId, projectId });
	};

	const onLinkDoc = (todoId: string, docId: string, title: string) => {
		attachMut.mutate({
			todoId,
			kind: "doc_link",
			title,
			docId,
		});
	};

	const onAddTag = (todoId: string, tag: string) => {
		const t = allTodos.find((x) => x.id === todoId);
		if (!t) return;
		updateMut.mutate({
			id: todoId,
			tags: [...new Set([...t.tags, tag])],
		});
	};
	const onRemoveTag = (todoId: string, tag: string) => {
		const t = allTodos.find((x) => x.id === todoId);
		if (!t) return;
		updateMut.mutate({
			id: todoId,
			tags: t.tags.filter((x) => x !== tag),
		});
	};

	// ── Toolbar state ────────────────────────────────────────────────────────
	// Grouping persists via the codex-amendment-3 URL > localStorage > default
	// chain. Todos doesn't expose grouping in the URL (we don't want deep-link
	// noise for a low-stakes preference), so we go straight to localStorage.
	const [groupBy, persistGroupBy] = useToolbarGroupBy("todos", null, "none");
	const [todosGroupBy, setTodosGroupBy] = useState<TaskGroupBy>(groupBy);
	useEffect(() => {
		setTodosGroupBy(groupBy);
	}, [groupBy]);
	const handleGroupByChange = (value: TaskGroupBy) => {
		setTodosGroupBy(value);
		persistGroupBy(value);
	};

	// ── Bulk selection ───────────────────────────────────────────────────────
	// Bind the surface so the shared bulk-ops bar can fire mutations against
	// the right entity set + so `escape` clears selection on this page only.
	const visibleIds = useMemo(
		() => activeTodos.map((t) => t.id),
		[activeTodos],
	);
	useBindBulkSelection({ surface: "todos", orderedIds: visibleIds });
	const selectedSet = useTaskSelection((s) => s.selected);
	const toggleSelection = useTaskSelection((s) => s.toggle);
	const rangeSelection = useTaskSelection((s) => s.rangeTo);
	const clearSelection = useTaskSelection((s) => s.clear);

	// ── Row shortcuts (codex amendment #4 registry) ──────────────────────────
	// Tied to the focused id from useJkNavigation — `j`/`k` already move the
	// focus ring; here we wire the toggle/range/escape/done bindings on top.
	const focusedId = jk.focusedId ?? null;
	useShortcut(
		"row.toggle",
		() => {
			if (focusedId) toggleSelection(focusedId);
		},
		{ enabled: !!focusedId },
	);
	useShortcut(
		"row.range",
		() => {
			if (focusedId) rangeSelection(focusedId);
		},
		{ enabled: !!focusedId },
	);
	useShortcut("row.escape", () => {
		clearSelection();
	});

	// Mark-done via the optimistic-undo toast (codex amendment #6). The row
	// checkbox keeps its raw onCheck path for the click case; this hook powers
	// the surface-wide "press `e` to complete focused row" gesture below.
	const todoQueryKey = trpc.todos.get.queryKey();
	const markDoneOptimistic = useOptimisticAction({
		action: "todo.complete",
		optimisticUpdate: (todo: Todo) => {
			const snapshot = qc.getQueriesData({ queryKey: todoQueryKey });
			qc.setQueriesData({ queryKey: todoQueryKey }, (old: any) => {
				if (!Array.isArray(old)) return old;
				return old.map((t: Todo) =>
					t.id === todo.id
						? { ...t, checked: true, checkedAt: new Date().toISOString() }
						: t,
				);
			});
			return snapshot;
		},
		mutateFn: (todo: Todo) => checkMut.mutateAsync({ id: todo.id } as any),
		rollback: (snapshot) => {
			for (const [k, v] of snapshot as any) qc.setQueryData(k, v);
		},
		toastLabel: "Marked done",
	});
	useShortcut(
		"row.edit",
		() => {
			if (!focusedId) return;
			const t = allTodos.find((x) => x.id === focusedId);
			if (t && !t.checked) markDoneOptimistic.run(t);
		},
		{ enabled: !!focusedId },
	);

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							To-do
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Quick captures. Not tasks. Check it off, it falls to the bottom.
						</p>
					</div>
					<div className="flex items-center gap-3 text-[11px] text-muted-foreground">
						<JkHint />
						<span className="flex items-center gap-1">
							<span>Press</span>
							<Kbd>N</Kbd>
							<span>to capture</span>
						</span>
					</div>
				</div>
			</header>
			<TaskToolbar
				routeKey="todos"
				groupBy={todosGroupBy}
				onGroupByChange={handleGroupByChange}
				groupByOptions={["none", "project", "label"]}
				viewModes={["list", "compact"]}
				onCreate={() => composerRef.current?.focus()}
				createLabel="New todo"
			/>
			<div className="grow overflow-y-auto px-4 py-4">
				{/* Always-visible top composer */}
				<div className="mb-2">
					<InlineComposer
						projects={projects}
						composerRef={composerRef}
						isPending={createMut.isPending}
						onSubmit={(content, projectId) =>
							createMut.mutate({ content, projectId })
						}
					/>
				</div>

				{/* Initial-load skeleton — Linear-style 6 row stack at the same
				 *  geometry as a real <TodoRow> so there's no layout shift on
				 *  hydrate. Falls through to the empty state below once the
				 *  query resolves to an empty array. */}
				{todosQuery.isLoading && (
					<div className="space-y-1" aria-hidden>
						{Array.from({ length: 6 }).map((_, i) => (
							<div
								// biome-ignore lint/suspicious/noArrayIndexKey: stable skeleton
								key={i}
								className="flex items-start gap-2 rounded-md border border-transparent px-2 py-1.5"
							>
								<span className="w-4 shrink-0" />
								<Skeleton className="mt-1.5 size-3.5 rounded-sm" />
								<div className="min-w-0 grow space-y-1.5">
									<Skeleton
										className="h-3.5"
										style={{ width: `${55 + ((i * 7) % 35)}%` }}
									/>
									<div className="flex items-center gap-1">
										<Skeleton className="h-3 w-16 rounded-sm" />
										{i % 2 === 0 && (
											<Skeleton className="h-3 w-10 rounded-sm" />
										)}
									</div>
								</div>
							</div>
						))}
					</div>
				)}

				{activeTodos.length === 0 &&
					completedTodos.length === 0 &&
					!todosQuery.isLoading && (
						<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
							<PlusIcon className="size-10 text-muted-foreground" />
							<p className="text-muted-foreground">
								Nothing here yet. Press <Kbd>N</Kbd> to capture your first todo.
							</p>
						</div>
					)}

				<SortableContext
					items={activeTodos.map((t) => t.id)}
					strategy={verticalListSortingStrategy}
				>
					<div className="space-y-1">
						{activeTodos.map((t) => (
							<TodoRow
								key={t.id}
								todo={t}
								projects={projects}
								onCheck={() => checkMut.mutate({ id: t.id })}
								onUncheck={() => uncheckMut.mutate({ id: t.id })}
								onDelete={() => deleteMut.mutate({ id: t.id })}
								onOpen={() => setOpenTodoId(t.id)}
								onAddTag={(tag) => onAddTag(t.id, tag)}
								onRemoveTag={(tag) => onRemoveTag(t.id, tag)}
								onLinkProject={(pid) => onLinkProject(t.id, pid)}
								onLinkDoc={(did, title) => onLinkDoc(t.id, did, title)}
								onPromote={() => onPromote(t)}
								onToggleSelect={(extend) =>
									extend ? rangeSelection(t.id) : toggleSelection(t.id)
								}
								isFocused={jk.isFocused(t.id)}
								isSelected={selectedSet.has(t.id)}
							/>
						))}
					</div>
				</SortableContext>

				<CompletedSection
					todos={completedTodos}
					projects={projects}
					onCheck={(id) => checkMut.mutate({ id })}
					onUncheck={(id) => uncheckMut.mutate({ id })}
					onDelete={(id) => deleteMut.mutate({ id })}
					onOpen={(id) => setOpenTodoId(id)}
					onAddTag={onAddTag}
					onRemoveTag={onRemoveTag}
					onLinkProject={onLinkProject}
					onLinkDoc={onLinkDoc}
					onPromote={onPromote}
				/>
			</div>
			{openTodoId && (
				<AttachmentsModal
					todoId={openTodoId}
					onClose={() => setOpenTodoId(null)}
				/>
			)}
			<BulkOpsBar surface="todos" noun="todo" />
		</div>
	);
}

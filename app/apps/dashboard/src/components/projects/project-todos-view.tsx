"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Checkbox } from "@ui/components/ui/checkbox";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import { Input } from "@ui/components/ui/input";
import {
	ChevronRightIcon,
	ListChecksIcon,
	PaperclipIcon,
	PlusIcon,
	Trash2Icon,
} from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { trpc } from "@/utils/trpc";

type Props = { projectId: string; team: string };

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

// ─── InlineComposer ─────────────────────────────────────────────────────────
// Always-visible row at the top of the list, styled like a todo row. The
// project picker is omitted vs. the global /todos composer — every capture
// here implicitly belongs to `projectId`. No `N` hotkey either: project-
// scoped pages shouldn't shadow global shortcuts.
function InlineComposer({
	composerRef,
	isPending,
	onSubmit,
}: {
	composerRef: React.RefObject<HTMLInputElement | null>;
	isPending: boolean;
	onSubmit: (content: string) => void;
}) {
	const [content, setContent] = useState("");

	const submit = () => {
		const trimmed = content.trim();
		if (!trimmed) return;
		onSubmit(trimmed);
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
				placeholder="What needs doing? (Enter to capture)"
				disabled={isPending}
				className="h-7 flex-1 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
			/>
		</form>
	);
}

// ─── TodoRow ────────────────────────────────────────────────────────────────
// Read-only display row. The project-scoped page intentionally drops the
// dnd-kit reorder + right-click context menu present on /todos — those rely
// on the project-picker submenu (no need here) and a Sortable provider mounted
// at layout level. If we ever want them back, port from `todos-view.tsx`.
function TodoRow({
	todo,
	onCheck,
	onUncheck,
	onDelete,
}: {
	todo: Todo;
	onCheck: () => void;
	onUncheck: () => void;
	onDelete: () => void;
}) {
	return (
		<div
			className={`group flex items-start gap-2 rounded-md border border-transparent px-2 py-1.5 transition hover:border-border hover:bg-accent/30 ${
				todo.checked ? "opacity-60" : ""
			}`}
		>
			<Checkbox
				checked={todo.checked}
				onCheckedChange={(v) => (v ? onCheck() : onUncheck())}
				className="mt-1.5"
			/>
			<div className="min-w-0 grow text-left">
				<div
					className={`text-sm ${
						todo.checked
							? "text-muted-foreground line-through"
							: "text-foreground"
					}`}
				>
					{todo.content}
				</div>
				<div className="mt-0.5 flex flex-wrap items-center gap-1 text-xs">
					{todo.tags.map((tag) => (
						<Badge key={tag} variant="outline" className="font-normal text-xs">
							{tag}
						</Badge>
					))}
					{todo.attachmentCount > 0 && (
						<Badge variant="outline" className="gap-1 font-normal text-xs">
							<PaperclipIcon className="size-3" />
							{todo.attachmentCount}
						</Badge>
					)}
				</div>
			</div>
			<button
				type="button"
				onClick={onDelete}
				className="text-muted-foreground opacity-0 transition hover:text-destructive group-hover:opacity-60"
				aria-label="Delete todo"
			>
				<Trash2Icon className="size-3.5" />
			</button>
		</div>
	);
}

/**
 * Project-scoped todos tab. Mirrors the global /todos page UX:
 *  - Always-visible inline composer at the top (Enter to capture)
 *  - Active todos in a flat list
 *  - "Completed (N)" disclosure at the bottom, collapsed by default
 *
 * Every capture pre-fills `projectId` so it shows up here and on /todos
 * grouped by this project. Hides the global `N` hotkey + project picker —
 * both would be redundant under a project scope.
 */
export function ProjectTodosView({ projectId }: Props) {
	const qc = useQueryClient();
	const projectQuery = useQuery(
		trpc.projects.getById.queryOptions({ id: projectId } as any),
	);
	// Pull BOTH checked and unchecked todos so we can render the Completed
	// disclosure below — same shape as the global /todos page.
	const todosQuery = useQuery(
		trpc.todos.get.queryOptions({
			projectId,
			includeChecked: true,
		}),
	);
	const project = projectQuery.data as { name?: string } | undefined;
	const allTodos = (todosQuery.data ?? []) as Todo[];
	const composerRef = useRef<HTMLInputElement | null>(null);

	const refetch = () => {
		qc.invalidateQueries({ queryKey: [["todos", "get"]] });
	};

	const createMut = useMutation(
		trpc.todos.create.mutationOptions({
			onSuccess: refetch,
			onError: (e) => toast.error(e.message),
		}),
	);
	const checkMut = useMutation(
		trpc.todos.check.mutationOptions({ onSuccess: refetch }),
	);
	const uncheckMut = useMutation(
		trpc.todos.uncheck.mutationOptions({ onSuccess: refetch }),
	);
	const deleteMut = useMutation(
		trpc.todos.delete.mutationOptions({ onSuccess: refetch }),
	);

	const activeTodos = allTodos.filter((t) => !t.checked);
	const completedTodos = allTodos.filter((t) => t.checked);

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div>
					<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
						{project?.name ?? "Project"} — Todos
					</h1>
					<p className="mt-0.5 text-[12px] text-muted-foreground">
						Quick captures scoped to this project. Type below and press Enter —
						completed ones drop into the disclosure at the bottom.
					</p>
				</div>
			</header>
			<div className="grow overflow-y-auto px-4 py-4">
				{/* Always-visible inline composer (no `N` hotkey on this scoped view). */}
				<div className="mb-2">
					<InlineComposer
						composerRef={composerRef}
						isPending={createMut.isPending}
						onSubmit={(content) => createMut.mutate({ content, projectId })}
					/>
				</div>

				{todosQuery.isLoading && (
					<div className="text-[12px] text-muted-foreground">Loading…</div>
				)}

				{activeTodos.length === 0 &&
					completedTodos.length === 0 &&
					!todosQuery.isLoading && (
						<div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
							<ListChecksIcon className="size-10 text-muted-foreground" />
							<p className="text-muted-foreground">
								No todos pinned to this project yet.
							</p>
							<p className="text-muted-foreground text-xs">
								Type above and hit Enter — it lands here automatically.
							</p>
						</div>
					)}

				<div className="space-y-1">
					{activeTodos.map((t) => (
						<TodoRow
							key={t.id}
							todo={t}
							onCheck={() => checkMut.mutate({ id: t.id })}
							onUncheck={() => uncheckMut.mutate({ id: t.id })}
							onDelete={() => deleteMut.mutate({ id: t.id })}
						/>
					))}
				</div>

				{completedTodos.length > 0 && (
					<Collapsible className="mt-6 border-border/60 border-t pt-3">
						<CollapsibleTrigger className="group flex w-full items-center gap-2 px-2 py-1 text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground [&[data-state=open]>svg]:rotate-90">
							<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
							<span className="font-[510] uppercase tracking-[0.04em]">
								Completed
							</span>
							<Badge variant="outline" className="h-4 px-1.5 font-normal">
								{completedTodos.length}
							</Badge>
						</CollapsibleTrigger>
						<CollapsibleContent>
							<div className="mt-2 space-y-1">
								{completedTodos.map((t) => (
									<TodoRow
										key={t.id}
										todo={t}
										onCheck={() => checkMut.mutate({ id: t.id })}
										onUncheck={() => uncheckMut.mutate({ id: t.id })}
										onDelete={() => deleteMut.mutate({ id: t.id })}
									/>
								))}
							</div>
						</CollapsibleContent>
					</Collapsible>
				)}
			</div>
		</div>
	);
}

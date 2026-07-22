"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { CommandIcon, PlusIcon, SparklesIcon } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";
import { useDebounceValue } from "usehooks-ts";
import { parseQuickCapture } from "@/components/home/quick-capture";
import { useUser } from "@/components/user-provider";
import { useProjects } from "@/hooks/use-data";
import { trpc } from "@/utils/trpc";

/**
 * Global capture bar — lives in the header, always available (⌘N).
 *
 * One input for everything:
 *   - Plain text        → creates a **todo** (lightweight, instant)
 *   - `@project …`      → creates a **task** on that project (falls back to
 *                         a project-linked todo if the project has no to-do
 *                         status yet, so the mention is never dropped)
 *   - `@task:query …`   → attaches the rest of the line as a **comment** on
 *                         the first matching task, instead of a
 *                         free-floating note
 *
 * Tasks vs todos: a task is a project board item (status, assignee, due date,
 * labels); a todo is a quick checklist capture. This bar picks for you —
 * reach for `@project` when it's real project work, `@task:` when you want
 * to attach a thought to something that already exists.
 */

const TASK_MENTION_RE = /(?:^|\s)@task:(\S+)/i;

/**
 * Peels off an explicit `@task:<query>` mention before the remainder goes
 * through `parseQuickCapture` — a colon-qualified prefix so it never
 * collides with that parser's own bare `@project` token. Scoped to this bar
 * only: quick-capture.tsx (Home) has no "attach to an existing task" concept,
 * so parseQuickCapture itself is intentionally left untouched.
 */
export function extractTaskMention(input: string): {
	taskQuery: string | null;
	rest: string;
} {
	const match = input.match(TASK_MENTION_RE);
	if (!match || match.index === undefined) {
		return { taskQuery: null, rest: input };
	}
	const rest = (
		input.slice(0, match.index) + input.slice(match.index + match[0].length)
	)
		.replace(/\s+/g, " ")
		.trim();
	return { taskQuery: match[1], rest };
}

export function CaptureBar({ className }: { className?: string }) {
	const user = useUser();
	const qc = useQueryClient();
	const inputRef = useRef<HTMLInputElement | null>(null);
	const [value, setValue] = useState("");

	const { data: projectsData } = useProjects();
	const { taskQuery, rest } = useMemo(() => extractTaskMention(value), [value]);
	const [debouncedTaskQuery] = useDebounceValue(taskQuery ?? "", 300);
	const parsed = useMemo(() => parseQuickCapture(rest), [rest]);

	const project = useMemo(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC return is unknown
		const rawProjects = (projectsData as any)?.data ?? [];
		const list = (
			rawProjects as Array<{
				id: string;
				name: string;
				slug?: string;
				prefix?: string;
				archived?: boolean;
			}>
		).filter((p) => !p.archived);
		if (!list.length) return null;
		if (!parsed.projectQuery) return list[0];
		const q = parsed.projectQuery.toLowerCase();
		return (
			list.find(
				(p) =>
					p.slug?.toLowerCase() === q ||
					p.prefix?.toLowerCase() === q ||
					p.name.toLowerCase().includes(q),
			) ?? null
		);
	}, [projectsData, parsed.projectQuery]);

	// `@task:query` resolves against the whole team's tasks (the header bar is
	// global, not project-scoped) — first match wins, same "good enough"
	// substring heuristic as the `@project` resolver above.
	const { data: taskMatches, isFetching: taskMatchesFetching } = useQuery(
		trpc.tasks.get.queryOptions(
			{ search: debouncedTaskQuery, pageSize: 5 },
			{
				enabled: !!taskQuery && !!debouncedTaskQuery,
				refetchOnWindowFocus: false,
			},
		),
	);
	const mentionedTask = taskQuery ? (taskMatches?.data?.[0] ?? null) : null;
	// `mentionedTask` lags the live `taskQuery` by the 300ms debounce, then by
	// the in-flight fetch after it -- reading it as final mid-resolution turns
	// a real match into a false "no task found" negative. Callers must check
	// this before trusting `mentionedTask`/`becomesTaskComment`.
	const isResolvingTaskMention =
		!!taskQuery && (taskQuery !== debouncedTaskQuery || taskMatchesFetching);

	// Will this attach to an existing task instead? Takes priority over
	// `@project` — mentioning a specific task is a more specific instruction
	// than mentioning its project.
	const becomesTaskComment = !!taskQuery && !!mentionedTask;
	// Will this become a task? Only when the user actually names a project
	// (and hasn't more specifically named a task via `@task:`).
	const becomesTask = !becomesTaskComment && !!parsed.projectQuery && !!project;

	const { data: todoStatus } = useQuery(
		trpc.statuses.get.queryOptions(
			{
				type: ["to_do"],
				pageSize: 1,
				projectId: project?.id ?? null,
				// biome-ignore lint/suspicious/noExplicitAny: status filter shape
			} as any,
			{
				// biome-ignore lint/suspicious/noExplicitAny: select
				select: (data: any) => data?.data?.[0] as { id: string } | undefined,
				enabled: !!project?.id,
				refetchOnWindowFocus: false,
			},
		),
	);

	const todoCreate = useMutation(
		trpc.todos.create.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["todos", "get"]] });
				toast.success("Todo added", { id: "capture-bar" });
			},
			onError: (e: { message?: string }) =>
				toast.error(e?.message ?? "Couldn't add todo", { id: "capture-bar" }),
		}),
	);
	const taskCreate = useMutation(
		trpc.tasks.create.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["tasks"]] });
				toast.success("Task created", { id: "capture-bar" });
			},
			onError: (e: { message?: string }) =>
				toast.error(e?.message ?? "Couldn't create task", {
					id: "capture-bar",
				}),
		}),
	);
	const taskComment = useMutation(
		trpc.tasks.comment.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["activities"]] });
				toast.success("Note attached to task", { id: "capture-bar" });
			},
			onError: (e: { message?: string }) =>
				toast.error(e?.message ?? "Couldn't attach note to task", {
					id: "capture-bar",
				}),
		}),
	);

	// ⌘N focuses the bar from anywhere.
	useHotkeys(
		"mod+n",
		(e) => {
			e.preventDefault();
			inputRef.current?.focus();
		},
		{ enableOnContentEditable: true, enableOnFormTags: true },
	);

	const submit = () => {
		if (isResolvingTaskMention) {
			// The debounce (or its trailing fetch) hasn't settled -- bail without
			// the "no match" error below, which would otherwise false-negative on
			// a match that simply hasn't resolved yet. The mode chip already
			// reads "Resolving…", so this is a silent no-op, not a toast.
			return;
		}
		const title = parsed.title?.trim();
		if (taskQuery && !mentionedTask) {
			toast.error(`No task found matching "@task:${taskQuery}".`, {
				id: "capture-bar",
			});
			return;
		}
		if (!title) {
			toast.error(
				becomesTaskComment
					? "Type a note to attach."
					: "Type something to capture.",
				{ id: "capture-bar" },
			);
			return;
		}
		if (becomesTaskComment) {
			toast.loading("Adding note…", { id: "capture-bar" });
			taskComment.mutate({ id: mentionedTask!.id, comment: title });
			setValue("");
			return;
		}
		if (becomesTask) {
			const statusId = todoStatus?.id;
			if (statusId) {
				toast.loading("Creating task…", { id: "capture-bar" });
				taskCreate.mutate({
					title,
					projectId: project!.id,
					statusId,
					assigneeId: user?.id ?? null,
					priority: parsed.priority ?? "low",
					labels: parsed.labels.length ? parsed.labels : undefined,
					dueDate: parsed.dueDate ? parsed.dueDate.toISOString() : null,
					// biome-ignore lint/suspicious/noExplicitAny: tRPC input shape
				} as any);
			} else {
				// No to-do status configured for the mentioned project yet — still
				// attach the capture to it as a project-linked todo rather than
				// dropping the mention on the floor.
				todoCreate.mutate({ content: title, projectId: project!.id });
			}
		} else {
			todoCreate.mutate({ content: title });
		}
		setValue("");
	};

	return (
		<div
			className={cn(
				"group flex h-8 items-center gap-2 rounded-lg border border-border bg-white/[0.02] px-2.5 transition-colors focus-within:border-[color:var(--brand,#6e7bff)] focus-within:ring-2 focus-within:ring-[color:var(--brand,#6e7bff)]/30",
				className,
			)}
		>
			<SparklesIcon
				className={cn(
					"size-3.5 shrink-0",
					becomesTaskComment && "text-violet-400",
					!becomesTaskComment && becomesTask && "text-sky-400",
					!becomesTaskComment && !becomesTask && "text-cyan-500",
				)}
			/>
			<input
				ref={inputRef}
				type="text"
				value={value}
				onChange={(e) => setValue(e.target.value)}
				onKeyDown={(e) => {
					if (e.key === "Enter") {
						e.preventDefault();
						submit();
					}
					if (e.key === "Escape") {
						e.preventDefault();
						setValue("");
						inputRef.current?.blur();
					}
				}}
				placeholder="Capture a todo…  @project for a task, @task:name to attach a note"
				className="min-w-0 flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted-foreground"
			/>
			{/* Mode chip — tells you what Enter will do */}
			<span
				className={cn(
					"hidden max-w-[160px] shrink-0 items-center truncate rounded-full border px-1.5 py-0.5 font-[510] text-[10px] uppercase tracking-wide sm:inline-flex",
					isResolvingTaskMention &&
						"border-border bg-muted/40 text-muted-foreground",
					!isResolvingTaskMention &&
						becomesTaskComment &&
						"border-violet-500/30 bg-violet-500/10 text-violet-300",
					!isResolvingTaskMention &&
						!becomesTaskComment &&
						becomesTask &&
						"border-sky-500/30 bg-sky-500/10 text-sky-300",
					!isResolvingTaskMention &&
						!becomesTaskComment &&
						!becomesTask &&
						"border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
				)}
			>
				{isResolvingTaskMention
					? "Resolving…"
					: becomesTaskComment
						? `Note · ${mentionedTask?.title ?? "…"}`
						: becomesTask
							? `Task${project ? ` · ${project.name}` : ""}`
							: "Todo"}
			</span>
			<span className="hidden items-center gap-0.5 text-[10px] text-muted-foreground md:flex">
				<CommandIcon className="size-3" />
				<span>N</span>
			</span>
			<button
				type="button"
				onClick={submit}
				disabled={
					!parsed.title ||
					isResolvingTaskMention ||
					todoCreate.isPending ||
					taskCreate.isPending ||
					taskComment.isPending
				}
				className="inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
				aria-label="Capture"
			>
				<PlusIcon className="size-3.5" />
			</button>
		</div>
	);
}

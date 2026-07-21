"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { CommandIcon, PlusIcon, SparklesIcon } from "lucide-react";
import { useHotkeys } from "react-hotkeys-hook";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useProjects } from "@/hooks/use-data";
import { useUser } from "@/components/user-provider";
import { parseQuickCapture } from "@/components/home/quick-capture";
import { trpc } from "@/utils/trpc";

/**
 * Global capture bar — lives in the header, always available (⌘N).
 *
 * One input for everything:
 *   - Plain text        → creates a **todo** (lightweight, instant)
 *   - `@project …`      → creates a **task** on that project
 *
 * Tasks vs todos: a task is a project board item (status, assignee, due date,
 * labels); a todo is a quick checklist capture. This bar picks for you —
 * reach for `@project` when it's real project work.
 */
export function CaptureBar({ className }: { className?: string }) {
	const user = useUser();
	const qc = useQueryClient();
	const inputRef = useRef<HTMLInputElement | null>(null);
	const [value, setValue] = useState("");

	const { data: projectsData } = useProjects();
	const parsed = useMemo(() => parseQuickCapture(value), [value]);

	const project = useMemo(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC return is unknown
		const list = (((projectsData as any)?.data ?? []) as Array<{
			id: string;
			name: string;
			slug?: string;
			prefix?: string;
			archived?: boolean;
		}>).filter((p) => !p.archived);
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

	// Will this become a task? Only when the user actually names a project.
	const becomesTask = !!parsed.projectQuery && !!project;

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
				toast.error(e?.message ?? "Couldn't create task", { id: "capture-bar" }),
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
		const title = parsed.title?.trim();
		if (!title) {
			toast.error("Type something to capture.", { id: "capture-bar" });
			return;
		}
		if (becomesTask) {
			const statusId = todoStatus?.id;
			if (!statusId) {
				toast.error("No to-do status for that project.", { id: "capture-bar" });
				return;
			}
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
					becomesTask ? "text-sky-400" : "text-cyan-500",
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
				placeholder="Capture a todo…  use @project to make a task"
				className="min-w-0 flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted-foreground"
			/>
			{/* Mode chip — tells you what Enter will do */}
			<span
				className={cn(
					"hidden shrink-0 items-center rounded-full border px-1.5 py-0.5 text-[10px] font-[510] uppercase tracking-wide sm:inline-flex",
					becomesTask
						? "border-sky-500/30 bg-sky-500/10 text-sky-300"
						: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
				)}
			>
				{becomesTask ? `Task${project ? ` · ${project.name}` : ""}` : "Todo"}
			</span>
			<span className="hidden items-center gap-0.5 text-[10px] text-muted-foreground md:flex">
				<CommandIcon className="size-3" />
				<span>N</span>
			</span>
			<button
				type="button"
				onClick={submit}
				disabled={!parsed.title || todoCreate.isPending || taskCreate.isPending}
				className="inline-flex size-5 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary transition-colors hover:bg-primary/20 disabled:opacity-40"
				aria-label="Capture"
			>
				<PlusIcon className="size-3.5" />
			</button>
		</div>
	);
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	ArchiveIcon,
	CheckSquareIcon,
	FileTextIcon,
	ListTodoIcon,
	ListTreeIcon,
	NotebookPenIcon,
	PlusIcon,
	Trash2Icon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { TodosView } from "@/components/todos/todos-view";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useProjects } from "@/hooks/use-data";
import { trpc, trpcClient } from "@/utils/trpc";

type Tab = "dump" | "todos" | "outline";

const DUMP_KEY = "nexus.capture.dump";
const OUTLINE_KEY = "nexus.capture.outline";

type DumpItem = {
	id: string;
	text: string;
	createdAt: string;
};

type OutlineNode = {
	id: string;
	text: string;
	children: OutlineNode[];
	collapsed?: boolean;
};

/**
 * Capture = brain dump surface (not Inbox / Needs you).
 * Promote MVP: Todo · Task · Note — always create-then-archive.
 */
export function CaptureShell() {
	const [tab, setTab] = useState<Tab>("dump");
	const [draft, setDraft] = useState("");
	const [items, setItems] = useState<DumpItem[]>([]);
	const [outline, setOutline] = useState<OutlineNode[]>([]);
	const [outlineDraft, setOutlineDraft] = useState("");

	const { data: projectsData } = useProjects();
	const project = useMemo(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC list shape
		const list = (((projectsData as any)?.data ?? []) as Array<{
			id: string;
			archived?: boolean;
		}>).filter((p) => !p.archived);
		return list[0] ?? null;
	}, [projectsData]);

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

	useEffect(() => {
		try {
			const raw = localStorage.getItem(DUMP_KEY);
			if (raw) setItems(JSON.parse(raw) as DumpItem[]);
			const o = localStorage.getItem(OUTLINE_KEY);
			if (o) setOutline(JSON.parse(o) as OutlineNode[]);
		} catch {
			/* ignore */
		}
	}, []);

	const persist = useCallback((next: DumpItem[]) => {
		setItems(next);
		localStorage.setItem(DUMP_KEY, JSON.stringify(next));
	}, []);

	const persistOutline = useCallback((next: OutlineNode[]) => {
		setOutline(next);
		localStorage.setItem(OUTLINE_KEY, JSON.stringify(next));
	}, []);

	const addDump = () => {
		const text = draft.trim();
		if (!text) return;
		persist([
			{
				id: crypto.randomUUID(),
				text,
				createdAt: new Date().toISOString(),
			},
			...items,
		]);
		setDraft("");
		toast.success("Dumped");
	};

	const archiveItem = (id: string) => {
		persist(items.filter((i) => i.id !== id));
		toast.message("Archived from dump");
	};

	const removeAfterPromote = (id: string) => {
		persist(items.filter((i) => i.id !== id));
	};

	const promoteTodo = async (item: DumpItem) => {
		try {
			await trpcClient.todos.create.mutate({ content: item.text });
			removeAfterPromote(item.id);
			toast.success("Promoted to Todo (archived dump)");
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Promote failed");
		}
	};

	const promoteTask = async (item: DumpItem) => {
		if (!project?.id) {
			toast.error("Create a project first to promote to Task");
			return;
		}
		const statusId = todoStatus?.id;
		if (!statusId) {
			toast.error("No to-do status available");
			return;
		}
		try {
			const title =
				item.text.split("\n")[0]?.slice(0, 255).trim() || "Captured task";
			await trpcClient.tasks.create.mutate({
				title,
				description: item.text.length > title.length ? item.text : null,
				projectId: project.id,
				statusId,
				// biome-ignore lint/suspicious/noExplicitAny: tRPC input
			} as any);
			removeAfterPromote(item.id);
			toast.success("Promoted to Task (archived dump)");
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Promote failed");
		}
	};

	const promoteNote = async (item: DumpItem) => {
		const slug = item.text
			.split("\n")[0]
			?.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-|-$/g, "")
			.slice(0, 48);
		const path = project?.id
			? `projects/${project.id}/capture-${slug || Date.now()}`
			: `drafts/capture-${slug || Date.now()}`;
		const title = item.text.split("\n")[0]?.slice(0, 120) || "Capture note";
		const content = `---\ntitle: ${JSON.stringify(title)}\nsource: capture\n---\n\n${item.text}\n`;
		try {
			await trpcClient.knowledge.create.mutate({
				relativePath: path,
				content,
			});
			removeAfterPromote(item.id);
			toast.success(`Note at ${path}.md (archived dump)`);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Note promote failed");
		}
	};

	const addOutlineRoot = () => {
		const text = outlineDraft.trim();
		if (!text) return;
		persistOutline([
			...outline,
			{ id: crypto.randomUUID(), text, children: [] },
		]);
		setOutlineDraft("");
	};

	const addChild = (parentId: string) => {
		const text = window.prompt("Child bullet");
		if (!text?.trim()) return;
		const walk = (nodes: OutlineNode[]): OutlineNode[] =>
			nodes.map((n) =>
				n.id === parentId
					? {
							...n,
							children: [
								...n.children,
								{ id: crypto.randomUUID(), text: text.trim(), children: [] },
							],
						}
					: { ...n, children: walk(n.children) },
			);
		persistOutline(walk(outline));
	};

	const removeNode = (id: string) => {
		const walk = (nodes: OutlineNode[]): OutlineNode[] =>
			nodes
				.filter((n) => n.id !== id)
				.map((n) => ({ ...n, children: walk(n.children) }));
		persistOutline(walk(outline));
	};

	const renderOutline = (nodes: OutlineNode[], depth = 0) => (
		<ul className={cn("space-y-1", depth > 0 && "ml-4 border-l border-border/50 pl-3")}>
			{nodes.map((n) => (
				<li key={n.id} className="group">
					<div className="flex items-start gap-2 rounded-md px-1 py-1 hover:bg-accent/30">
						<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
						<span className="min-w-0 flex-1 text-[13.5px] leading-snug">
							{n.text}
						</span>
						<button
							type="button"
							className="opacity-0 transition-opacity group-hover:opacity-100 text-[11px] text-muted-foreground hover:text-foreground"
							onClick={() => addChild(n.id)}
						>
							+
						</button>
						<button
							type="button"
							className="opacity-0 transition-opacity group-hover:opacity-100 text-muted-foreground hover:text-red-400"
							onClick={() => removeNode(n.id)}
						>
							<Trash2Icon className="size-3" />
						</button>
					</div>
					{n.children.length > 0 ? renderOutline(n.children, depth + 1) : null}
				</li>
			))}
		</ul>
	);

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="border-border/60 border-b px-4 py-3">
				<div className="flex flex-wrap items-end justify-between gap-3">
					<div>
						<h1 className="font-[510] text-[18px] tracking-[-0.01em]">
							Capture
						</h1>
						<p className="text-[13px] text-muted-foreground">
							Brain dump — uncommitted thoughts. Promote when ready (create
							then archive). Needs you is attention-only.
						</p>
					</div>
					<div className="inline-flex rounded-lg border border-border/60 bg-card/40 p-0.5">
						{(
							[
								["dump", "Dump"],
								["todos", "Todos"],
								["outline", "Outline"],
							] as const
						).map(([id, label]) => (
							<button
								key={id}
								type="button"
								onClick={() => setTab(id)}
								className={cn(
									"rounded-md px-3 py-1.5 text-[12.5px] font-[510] transition-colors",
									tab === id
										? "bg-accent text-foreground"
										: "text-muted-foreground hover:text-foreground",
								)}
							>
								{label}
							</button>
						))}
					</div>
				</div>
			</div>

			{tab === "dump" ? (
				<div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 py-6">
					<div className="flex items-start gap-3">
						<SoftIcon icon={NotebookPenIcon} tone="violet" size="md" />
						<div className="min-w-0 flex-1 space-y-2">
							<Textarea
								value={draft}
								onChange={(e) => setDraft(e.target.value)}
								placeholder="Dump anything — no commitment yet…"
								className="min-h-[100px] resize-y text-[14px]"
								onKeyDown={(e) => {
									if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
										e.preventDefault();
										addDump();
									}
								}}
							/>
							<div className="flex items-center justify-between">
								<span className="text-[11px] text-muted-foreground">
									⌘↵ to dump
								</span>
								<Button size="sm" onClick={addDump} disabled={!draft.trim()}>
									Dump
								</Button>
							</div>
						</div>
					</div>

					<ul className="space-y-2">
						{items.length === 0 ? (
							<li className="rounded-xl border border-dashed border-border/60 px-4 py-8 text-center text-[13px] text-muted-foreground">
								Empty dump. Thoughts live here until you promote them.
							</li>
						) : (
							items.map((item) => (
								<li
									key={item.id}
									className="flex items-start gap-3 rounded-xl border border-border/60 bg-card/40 p-3"
								>
									<p className="min-w-0 flex-1 whitespace-pre-wrap text-[13.5px] leading-relaxed">
										{item.text}
									</p>
									<div className="flex shrink-0 flex-col gap-1">
										<Button
											size="sm"
											variant="outline"
											className="h-7 gap-1 text-[11px]"
											onClick={() => promoteTodo(item)}
										>
											<CheckSquareIcon className="size-3" />
											Todo
										</Button>
										<Button
											size="sm"
											variant="outline"
											className="h-7 gap-1 text-[11px]"
											onClick={() => promoteTask(item)}
										>
											<ListTodoIcon className="size-3" />
											Task
										</Button>
										<Button
											size="sm"
											variant="outline"
											className="h-7 gap-1 text-[11px]"
											onClick={() => promoteNote(item)}
										>
											<FileTextIcon className="size-3" />
											Note
										</Button>
										<Button
											size="sm"
											variant="ghost"
											className="h-7 gap-1 text-[11px] text-muted-foreground"
											onClick={() => archiveItem(item.id)}
										>
											<ArchiveIcon className="size-3" />
											Archive
										</Button>
									</div>
								</li>
							))
						)}
					</ul>
				</div>
			) : null}

			{tab === "todos" ? (
				<div className="min-h-0 flex-1">
					<TodosView />
				</div>
			) : null}

			{tab === "outline" ? (
				<div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 py-6">
					<div className="flex items-center gap-3">
						<SoftIcon icon={ListTreeIcon} tone="teal" size="md" />
						<div>
							<h2 className="font-[510] text-[15px]">Outline</h2>
							<p className="text-[12px] text-muted-foreground">
								Nested bullets (local). Zoom / promote to board next.
							</p>
						</div>
					</div>
					<div className="flex gap-2">
						<Input
							value={outlineDraft}
							onChange={(e) => setOutlineDraft(e.target.value)}
							placeholder="New top-level bullet…"
							onKeyDown={(e) => {
								if (e.key === "Enter") {
									e.preventDefault();
									addOutlineRoot();
								}
							}}
						/>
						<Button size="sm" onClick={addOutlineRoot} disabled={!outlineDraft.trim()}>
							<PlusIcon className="size-3.5" />
						</Button>
					</div>
					{outline.length === 0 ? (
						<p className="rounded-xl border border-dashed border-border/60 px-4 py-10 text-center text-[13px] text-muted-foreground">
							Start an outline — Tab+ later for deep nesting.
						</p>
					) : (
						renderOutline(outline)
					)}
				</div>
			) : null}
		</div>
	);
}

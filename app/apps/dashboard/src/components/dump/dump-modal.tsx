"use client";

import { useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { Kbd, KbdGroup } from "@ui/components/ui/kbd";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	ArchiveIcon,
	CheckSquareIcon,
	FileTextIcon,
	ListTodoIcon,
	NotebookPenIcon,
	PlusIcon,
} from "lucide-react";
import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useState,
} from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { toast } from "sonner";
import {
	addDumpItem,
	readDump,
	removeDumpItem,
	type DumpItem,
} from "@/components/dump/dump-store";
import { useProjects } from "@/hooks/use-data";
import { trpc, trpcClient } from "@/utils/trpc";

type DumpCtx = { open: () => void };
const Ctx = createContext<DumpCtx | null>(null);

/** Read the dump trigger from anywhere the provider is mounted (global). */
export function useDumpModal() {
	const ctx = useContext(Ctx);
	return ctx ?? { open: () => {} };
}

/**
 * Global dump surface — always-on modal triggered from the header + ⌘J.
 * Brain dump (not Inbox). Promote = create then archive.
 */
export function DumpModalProvider({
	children,
}: {
	children: React.ReactNode;
}) {
	const [open, setOpen] = useState(false);
	const [items, setItems] = useState<DumpItem[]>([]);

	const refresh = useCallback(() => setItems(readDump()), []);

	useEffect(() => {
		refresh();
		const onChange = () => refresh();
		window.addEventListener("nexus.dump.changed", onChange);
		return () => window.removeEventListener("nexus.dump.changed", onChange);
	}, [refresh]);

	// ⌘J opens the dump from anywhere on the app.
	useHotkeys(
		"mod+j",
		(e) => {
			e.preventDefault();
			setOpen((o) => !o);
		},
		{ enableOnContentEditable: true, enableOnFormTags: true },
	);

	const value = useMemo<DumpCtx>(
		() => ({ open: () => setOpen(true) }),
		[],
	);

	return (
		<Ctx.Provider value={value}>
			{children}
			<DumpDialog
				open={open}
				onOpenChange={setOpen}
				items={items}
				onChanged={refresh}
			/>
		</Ctx.Provider>
	);
}

function DumpDialog({
	open,
	onOpenChange,
	items,
	onChanged,
}: {
	open: boolean;
	onOpenChange: (v: boolean) => void;
	items: DumpItem[];
	onChanged: () => void;
}) {
	const [draft, setDraft] = useState("");
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

	const dump = () => {
		const text = draft.trim();
		if (!text) return;
		addDumpItem(text);
		setDraft("");
		toast.success("Dumped");
	};

	const remove = (id: string) => {
		removeDumpItem(id);
		toast.message("Archived");
	};

	const promoteTodo = async (item: DumpItem) => {
		try {
			await trpcClient.todos.create.mutate({ content: item.text });
			removeDumpItem(item.id);
			onChanged();
			toast.success("Promoted to Todo (archived)");
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
			removeDumpItem(item.id);
			onChanged();
			toast.success("Promoted to Task (archived)");
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
		const content = `---\ntitle: ${JSON.stringify(title)}\nsource: dump\n---\n\n${item.text}\n`;
		try {
			await trpcClient.knowledge.create.mutate({
				relativePath: path,
				content,
			});
			removeDumpItem(item.id);
			onChanged();
			toast.success(`Note at ${path}.md (archived)`);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Note promote failed");
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-xl gap-0 p-0">
				<div className="flex items-center justify-between border-b border-border/60 px-4 py-3">
					<div className="flex items-center gap-2">
						<NotebookPenIcon className="size-4 text-violet-500" />
						<DialogTitle className="font-[510] text-[15px]">
							Dump anything
						</DialogTitle>
					</div>
					<Kbd className="text-[10px]">
						<KbdGroup>
							<span>⌘</span>
							<span>J</span>
						</KbdGroup>
					</Kbd>
				</div>
				<DialogDescription className="sr-only">
					Brain dump — uncommitted thoughts. Promote when ready.
				</DialogDescription>
				<div className="space-y-2 px-4 pt-3">
					<Textarea
						// biome-ignore lint/a11y/useAutofocus: modal focus is fine
						autoFocus
						value={draft}
						onChange={(e) => setDraft(e.target.value)}
						placeholder="Dump a thought — no commitment yet…  (⌘↵ to save)"
						className="min-h-[88px] resize-y text-[14px]"
						onKeyDown={(e) => {
							if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
								e.preventDefault();
								dump();
							}
						}}
					/>
					<div className="flex items-center justify-between pb-1">
						<span className="text-[11px] text-muted-foreground">
							Stays here until you promote it.
						</span>
						<Button
							size="sm"
							onClick={dump}
							disabled={!draft.trim()}
							className="gap-1.5"
						>
							<PlusIcon className="size-3.5" />
							Dump
						</Button>
					</div>
				</div>
				<div className="max-h-[44vh] overflow-y-auto px-4 pb-4">
					{items.length === 0 ? (
						<p className="py-8 text-center text-[13px] text-muted-foreground">
							Empty dump. Thoughts land here, promote when ready.
						</p>
					) : (
						<ul className="space-y-2">
							{items.map((item) => (
								<li
									key={item.id}
									className="rounded-lg border border-border/60 bg-card/40 p-2.5"
								>
									<p className="whitespace-pre-wrap text-[13px] leading-relaxed">
										{item.text}
									</p>
									<div className="mt-2 flex flex-wrap items-center gap-1">
										<Button
											size="sm"
											variant="outline"
											className="h-6 gap-1 text-[11px]"
											onClick={() => promoteTodo(item)}
										>
											<CheckSquareIcon className="size-3" />
											Todo
										</Button>
										<Button
											size="sm"
											variant="outline"
											className="h-6 gap-1 text-[11px]"
											onClick={() => promoteTask(item)}
										>
											<ListTodoIcon className="size-3" />
											Task
										</Button>
										<Button
											size="sm"
											variant="outline"
											className="h-6 gap-1 text-[11px]"
											onClick={() => promoteNote(item)}
										>
											<FileTextIcon className="size-3" />
											Note
										</Button>
										<Button
											size="sm"
											variant="ghost"
											className="ml-auto h-6 gap-1 text-[11px] text-muted-foreground"
											onClick={() => remove(item.id)}
										>
											<ArchiveIcon className="size-3" />
											Archive
										</Button>
									</div>
								</li>
							))}
						</ul>
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
}

/** Header trigger button — styled to sit next to the search box. */
export function DumpTrigger() {
	const { open } = useDumpModal();
	return (
		<button
			type="button"
			onClick={open}
			className={cn(
				"inline-flex h-7 items-center gap-2 rounded-md border border-border bg-white/[0.02] px-2 text-start text-[12px] text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground",
			)}
		>
			<NotebookPenIcon className="size-3 shrink-0 text-violet-500" />
			<span className="truncate">Dump…</span>
			<Kbd className="ml-1 text-[10px]">
				<KbdGroup>
					<span>⌘</span>
					<span>J</span>
				</KbdGroup>
			</Kbd>
		</button>
	);
}

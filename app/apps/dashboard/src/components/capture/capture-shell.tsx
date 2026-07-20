"use client";

import { Button } from "@ui/components/ui/button";
import { Textarea } from "@ui/components/ui/textarea";
import { cn } from "@ui/lib/utils";
import {
	ArchiveIcon,
	CheckSquareIcon,
	ListTreeIcon,
	NotebookPenIcon,
	SparklesIcon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { TodosView } from "@/components/todos/todos-view";
import { SoftIcon } from "@/components/ui/soft-icon";
import { trpcClient } from "@/utils/trpc";

type Tab = "dump" | "todos" | "outline";

const DUMP_KEY = "nexus.capture.dump";

type DumpItem = {
	id: string;
	text: string;
	createdAt: string;
};

/**
 * Capture = brain dump surface (not Inbox / Needs you).
 * Tabs: Dump | Todos | Outline (outline shell for later WorkFlowy).
 */
export function CaptureShell() {
	const [tab, setTab] = useState<Tab>("dump");
	const [draft, setDraft] = useState("");
	const [items, setItems] = useState<DumpItem[]>([]);

	useEffect(() => {
		try {
			const raw = localStorage.getItem(DUMP_KEY);
			if (raw) setItems(JSON.parse(raw) as DumpItem[]);
		} catch {
			/* ignore */
		}
	}, []);

	const persist = useCallback((next: DumpItem[]) => {
		setItems(next);
		localStorage.setItem(DUMP_KEY, JSON.stringify(next));
	}, []);

	const addDump = () => {
		const text = draft.trim();
		if (!text) return;
		const entry: DumpItem = {
			id: crypto.randomUUID(),
			text,
			createdAt: new Date().toISOString(),
		};
		persist([entry, ...items]);
		setDraft("");
		toast.success("Dumped");
	};

	const archiveItem = (id: string) => {
		persist(items.filter((i) => i.id !== id));
		toast.message("Archived from dump");
	};

	const promoteTodo = async (item: DumpItem) => {
		try {
			await trpcClient.todos.create.mutate({
				content: item.text,
			});
			persist(items.filter((i) => i.id !== item.id));
			toast.success("Promoted to Todo (original archived)");
		} catch (e) {
			toast.error(e instanceof Error ? e.message : "Promote failed");
		}
	};

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="border-border/60 border-b px-4 py-3">
				<div className="flex flex-wrap items-end justify-between gap-3">
					<div>
						<h1 className="font-[510] text-[18px] tracking-[-0.01em]">
							Capture
						</h1>
						<p className="text-[13px] text-muted-foreground">
							Brain dump — uncommitted thoughts. Promote when ready. Inbox is
							attention-only, not a scratch pad.
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
				<div className="mx-auto flex max-w-lg flex-col items-center gap-3 px-4 py-16 text-center">
					<SoftIcon icon={ListTreeIcon} tone="teal" size="lg" />
					<h2 className="font-[510] text-[15px]">Outline (WorkFlowy mode)</h2>
					<p className="text-[13px] text-muted-foreground">
						Nested bullets with zoom-in — ships after Capture Dump + promote
						loop is solid. For now use Dump or Notes.
					</p>
					<div className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-border/60 px-2.5 py-1 text-[11px] text-muted-foreground">
						<SparklesIcon className="size-3" /> Coming in Phase C+
					</div>
				</div>
			) : null}
		</div>
	);
}

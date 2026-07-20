"use client";

import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import { cn } from "@ui/lib/utils";
import {
	ListTreeIcon,
	NotebookPenIcon,
	PlusIcon,
	Trash2Icon,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useDumpModal } from "@/components/dump/dump-modal";
import { SoftIcon } from "@/components/ui/soft-icon";

const OUTLINE_KEY = "nexus.capture.outline";

type OutlineNode = {
	id: string;
	text: string;
	children: OutlineNode[];
};

/**
 * Capture page — Outline (WorkFlowy-style nested bullets).
 * Brain dump moved to the global header Dump modal (⌘J).
 */
export function CaptureShell() {
	const { open: openDump } = useDumpModal();
	const [outline, setOutline] = useState<OutlineNode[]>([]);
	const [draft, setDraft] = useState("");

	useEffect(() => {
		try {
			const raw = localStorage.getItem(OUTLINE_KEY);
			if (raw) setOutline(JSON.parse(raw) as OutlineNode[]);
		} catch {
			/* ignore */
		}
	}, []);

	const persist = useCallback((next: OutlineNode[]) => {
		setOutline(next);
		localStorage.setItem(OUTLINE_KEY, JSON.stringify(next));
	}, []);

	const addRoot = () => {
		const text = draft.trim();
		if (!text) return;
		persist([...outline, { id: crypto.randomUUID(), text, children: [] }]);
		setDraft("");
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
		persist(walk(outline));
	};

	const removeNode = (id: string) => {
		const walk = (nodes: OutlineNode[]): OutlineNode[] =>
			nodes
				.filter((n) => n.id !== id)
				.map((n) => ({ ...n, children: walk(n.children) }));
		persist(walk(outline));
	};

	const renderOutline = (nodes: OutlineNode[], depth = 0) => (
		<ul
			className={cn(
				"space-y-1",
				depth > 0 && "ml-4 border-l border-border/50 pl-3",
			)}
		>
			{nodes.map((n) => (
				<li key={n.id} className="group">
					<div className="flex items-start gap-2 rounded-md px-1 py-1 hover:bg-accent/30">
						<span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/50" />
						<span className="min-w-0 flex-1 text-[13.5px] leading-snug">
							{n.text}
						</span>
						<button
							type="button"
							className="text-[11px] text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
							onClick={() => addChild(n.id)}
						>
							+
						</button>
						<button
							type="button"
							className="text-muted-foreground opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
							onClick={() => removeNode(n.id)}
						>
							<Trash2Icon className="size-3" />
						</button>
					</div>
					{n.children.length > 0
						? renderOutline(n.children, depth + 1)
						: null}
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
							Outline your thinking. Brain dump moved to the header — press{" "}
							<button
								type="button"
								onClick={openDump}
								className="inline-flex items-center gap-1 font-[510] text-primary hover:underline"
							>
								<NotebookPenIcon className="size-3" /> Dump (⌘J)
							</button>
							.
						</p>
					</div>
				</div>
			</div>

			<div className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 py-6">
				<div className="flex items-center gap-3">
					<SoftIcon icon={ListTreeIcon} tone="teal" size="md" />
					<div>
						<h2 className="font-[510] text-[15px]">Outline</h2>
						<p className="text-[12px] text-muted-foreground">
							Nested bullets (local). Hover a row to add a child or remove.
						</p>
					</div>
				</div>
				<div className="flex gap-2">
					<Input
						value={draft}
						onChange={(e) => setDraft(e.target.value)}
						placeholder="New top-level bullet…"
						onKeyDown={(e) => {
							if (e.key === "Enter") {
								e.preventDefault();
								addRoot();
							}
						}}
					/>
					<Button size="sm" onClick={addRoot} disabled={!draft.trim()}>
						<PlusIcon className="size-3.5" />
					</Button>
				</div>
				{outline.length === 0 ? (
					<p className="rounded-xl border border-dashed border-border/60 px-4 py-10 text-center text-[13px] text-muted-foreground">
						Start an outline. Use + on a row to nest deeper.
					</p>
				) : (
					renderOutline(outline)
				)}
			</div>
		</div>
	);
}

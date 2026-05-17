"use client";

// EntityPicker — inline popover used by the slash-menu's entity-link
// commands (/task, /doc, /note, /prompt). Renders a search input + result
// list scoped to a single entity type and calls onSelect with the picked
// entity. Owns its own queries (debounced) so the slash-menu can stay
// vanilla DOM and only construct this once per invocation.
//
// Design intent (iter4, bonus UX win #1): each picked entity inserts a
// compact inline pill in the editor — same look-and-feel as the @ mention
// system but discovered via the slash command, since users intuitively
// reach for `/` when they want to *insert* and `@` when they want to
// *address*.

import { useQuery } from "@tanstack/react-query";
import {
	BrainIcon,
	CheckSquareIcon,
	FileTextIcon,
	Loader2Icon,
	MessageSquareTextIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDebounceValue } from "usehooks-ts";
import { cn } from "@/lib/utils";
import { trpc } from "@/utils/trpc";

export type EntityKind = "task" | "document" | "knowledge" | "prompt";

export interface PickedEntity {
	kind: EntityKind;
	id: string;
	label: string;
	/** Task sequence (e.g. EL-69's 69) when kind=task */
	sequence?: number | null;
	/** Knowledge note relative path */
	relativePath?: string | null;
	/** Prompt "productSlug:promptSlug" parent key */
	parentSlug?: string | null;
	/** Document icon (emoji or shadcn icon name) */
	icon?: string | null;
}

const DEBOUNCE_MS = 200;

interface EntityPickerProps {
	kind: EntityKind;
	onSelect: (entity: PickedEntity) => void;
	onCancel: () => void;
}

const KIND_CONFIG: Record<
	EntityKind,
	{ icon: typeof BrainIcon; placeholder: string; label: string }
> = {
	task: {
		icon: CheckSquareIcon,
		placeholder: "Search tasks by id or title…",
		label: "Insert task",
	},
	document: {
		icon: FileTextIcon,
		placeholder: "Search documents by name…",
		label: "Insert document",
	},
	knowledge: {
		icon: BrainIcon,
		placeholder: "Search knowledge notes…",
		label: "Insert knowledge note",
	},
	prompt: {
		icon: MessageSquareTextIcon,
		placeholder: "Search saved prompts…",
		label: "Insert prompt",
	},
};

export function EntityPicker({ kind, onSelect, onCancel }: EntityPickerProps) {
	const [query, setQuery] = useState("");
	const [debouncedQuery] = useDebounceValue(query, DEBOUNCE_MS);
	const [selectedIndex, setSelectedIndex] = useState(0);
	const inputRef = useRef<HTMLInputElement | null>(null);
	const config = KIND_CONFIG[kind];
	const Icon = config.icon;

	useEffect(() => {
		inputRef.current?.focus();
	}, []);

	// Each entity kind has its own query. We use useQuery directly with the
	// shape that maps to a flat PickedEntity[] so the list-rendering path
	// stays kind-agnostic.

	const tasksQ = useQuery({
		...trpc.tasks.get.queryOptions({
			search: debouncedQuery || undefined,
			pageSize: 8,
		}),
		enabled: kind === "task",
	});

	const documentsQ = useQuery({
		...trpc.documents.get.queryOptions({
			search: debouncedQuery || undefined,
			tree: false,
			pageSize: 8,
		}),
		enabled: kind === "document",
	});

	const knowledgeQ = useQuery({
		...trpc.knowledge.get.queryOptions({
			search: debouncedQuery || undefined,
		}),
		enabled: kind === "knowledge",
	});

	// Prompts have no first-class search endpoint, so we route through the
	// global-search view (which already includes prompts as of iter3) filtered
	// by type=prompt. parentId carries "productSlug:promptSlug" for routing.
	const promptsQ = useQuery({
		...trpc.globalSearch.search.queryOptions({
			search: debouncedQuery || undefined,
			type: ["prompt"],
		}),
		enabled: kind === "prompt",
	});

	const items = useMemo<PickedEntity[]>(() => {
		switch (kind) {
			case "task":
				return (tasksQ.data?.data ?? []).map((t) => ({
					kind: "task" as const,
					id: t.id,
					label: t.title,
					sequence: t.sequence,
				}));
			case "document":
				return (documentsQ.data?.data ?? []).map((d) => ({
					kind: "document" as const,
					id: d.id,
					label: d.name,
					icon: d.icon,
				}));
			case "knowledge":
				return (knowledgeQ.data?.notes ?? []).slice(0, 10).map((n) => ({
					kind: "knowledge" as const,
					id: n.id,
					label: n.name,
					relativePath: n.relativePath,
				}));
			case "prompt":
				return (promptsQ.data ?? []).map((p) => ({
					kind: "prompt" as const,
					id: p.id,
					label: p.title,
					parentSlug: p.parentId ?? null,
				}));
		}
	}, [kind, tasksQ.data, documentsQ.data, knowledgeQ.data, promptsQ.data]);

	const isLoading =
		(kind === "task" && tasksQ.isLoading) ||
		(kind === "document" && documentsQ.isLoading) ||
		(kind === "knowledge" && knowledgeQ.isLoading) ||
		(kind === "prompt" && promptsQ.isLoading);

	// Reset selection when items change so navigation always starts at top.
	useEffect(() => {
		setSelectedIndex(0);
	}, [debouncedQuery, kind]);

	const handlePick = (idx: number) => {
		const it = items[idx];
		if (it) onSelect(it);
	};

	return (
		<div
			className="w-[360px] overflow-hidden rounded-md border border-border bg-popover shadow-md"
			onMouseDown={(e) => {
				// Keep editor selection alive while interacting with the popover.
				e.preventDefault();
			}}
		>
			<div className="flex items-center gap-2 border-border border-b px-3 py-2">
				<Icon className="size-4 shrink-0 text-muted-foreground" />
				<input
					ref={inputRef}
					value={query}
					onChange={(e) => setQuery(e.target.value)}
					onKeyDown={(e) => {
						if (e.key === "Escape") {
							e.preventDefault();
							onCancel();
							return;
						}
						if (e.key === "ArrowDown") {
							e.preventDefault();
							setSelectedIndex((s) => (s + 1) % Math.max(items.length, 1));
							return;
						}
						if (e.key === "ArrowUp") {
							e.preventDefault();
							setSelectedIndex(
								(s) => (s - 1 + items.length) % Math.max(items.length, 1),
							);
							return;
						}
						if (e.key === "Enter") {
							e.preventDefault();
							handlePick(selectedIndex);
						}
					}}
					placeholder={config.placeholder}
					className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
				/>
			</div>
			<div className="max-h-72 overflow-auto py-1">
				{isLoading && items.length === 0 ? (
					<div className="flex items-center gap-2 px-3 py-3 text-muted-foreground text-sm">
						<Loader2Icon className="size-3.5 animate-spin" />
						Searching…
					</div>
				) : items.length === 0 ? (
					<div className="px-3 py-3 text-muted-foreground text-sm">
						No results
					</div>
				) : (
					items.map((it, i) => (
						<button
							key={`${it.kind}-${it.id}`}
							type="button"
							onMouseEnter={() => setSelectedIndex(i)}
							onMouseDown={(e) => {
								e.preventDefault();
								handlePick(i);
							}}
							className={cn(
								"flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm",
								i === selectedIndex && "bg-accent text-accent-foreground",
							)}
						>
							<Icon className="size-3.5 shrink-0 text-muted-foreground" />
							{it.kind === "task" &&
							typeof it.sequence === "number" &&
							it.sequence >= 0 ? (
								<span className="shrink-0 font-mono text-muted-foreground text-xs">
									#{it.sequence}
								</span>
							) : null}
							<span className="min-w-0 flex-1 truncate">{it.label}</span>
							{it.kind === "knowledge" && it.relativePath ? (
								<span className="truncate text-muted-foreground text-xs">
									{it.relativePath}
								</span>
							) : null}
							{it.kind === "prompt" && it.parentSlug ? (
								<span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground uppercase tracking-wide">
									{it.parentSlug.split(":")[0]}
								</span>
							) : null}
						</button>
					))
				)}
			</div>
			<div className="border-border border-t px-3 py-1.5 text-[11px] text-muted-foreground">
				<kbd className="font-mono">↑↓</kbd> navigate ·{" "}
				<kbd className="font-mono">↵</kbd> select ·{" "}
				<kbd className="font-mono">esc</kbd> cancel
			</div>
		</div>
	);
}

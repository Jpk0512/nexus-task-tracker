"use client";

/**
 * Raycast-style quick-open ring (codex delighter #9).
 *
 * Triggered by `Cmd+O`. A small floating overlay (not the full palette)
 * showing the 5–7 most-recently-visited entities the user has touched in the
 * palette. Faster than Cmd+K — no search input, no filtering, no debounce.
 *
 * Recent-items source: the existing `nexus.palette.recent` localStorage key
 * iter 4 introduced. We read it once on open; this component never writes
 * to that key (the palette owns writes).
 *
 * Navigation:
 *   - ↑/↓ — move the focus ring
 *   - Enter — open the highlighted entity
 *   - Esc / clicking the backdrop — close
 *   - Cmd+O again while open — close (toggle behaviour)
 */

import { DialogTitle } from "@radix-ui/react-dialog";
import {
	Dialog,
	DialogContent,
	DialogHeader,
} from "@ui/components/ui/dialog";
import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";
import {
	BoxIcon,
	BookOpenIcon,
	ChevronRightIcon,
	FileTextIcon,
	HashIcon,
	LayersIcon,
	LibraryIcon,
	ListChecksIcon,
	MapPinIcon,
	SparklesIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useUser } from "@/components/user-provider";
import { useTaskPanel } from "@/components/panels/task-panel";
import type { GlobalSearchItem } from "./types";

const RECENT_KEY = "nexus.palette.recent";
const RING_MAX = 7;

type Recent = GlobalSearchItem & { visitedAt?: string };

function loadRecentRaw(): Recent[] {
	if (typeof window === "undefined") return [];
	try {
		const raw = window.localStorage.getItem(RECENT_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		return parsed.slice(0, RING_MAX) as Recent[];
	} catch {
		return [];
	}
}

// Type-icon mapping. Mirrors the section labels used by the full palette so
// the ring feels visually coherent — same icon for the same entity type.
const TYPE_ICON: Record<string, typeof LayersIcon> = {
	task: LayersIcon,
	project: BoxIcon,
	milestone: MapPinIcon,
	document: FileTextIcon,
	todo: ListChecksIcon,
	knowledge: BookOpenIcon,
	library: LibraryIcon,
	prompt: SparklesIcon,
	navigation: HashIcon,
};

const TYPE_LABEL: Record<string, string> = {
	task: "Task",
	project: "Project",
	milestone: "Milestone",
	document: "Doc",
	todo: "Todo",
	knowledge: "Knowledge",
	library: "Library",
	prompt: "Prompt",
	navigation: "Nav",
};

function relative(at: string | undefined): string {
	if (!at) return "recently";
	try {
		return formatDistanceToNowStrict(new Date(at), { addSuffix: true });
	} catch {
		return "recently";
	}
}

export type QuickOpenRingProps = {
	open: boolean;
	onOpenChange: (open: boolean) => void;
};

export const QuickOpenRing = ({ open, onOpenChange }: QuickOpenRingProps) => {
	const router = useRouter();
	const user = useUser();
	const taskPanel = useTaskPanel();
	const [items, setItems] = useState<Recent[]>([]);
	const [cursor, setCursor] = useState(0);
	const listRef = useRef<HTMLUListElement | null>(null);

	// Refresh from storage every time the ring opens. Multi-tab users would
	// otherwise see a stale list — the palette is the source of truth.
	useEffect(() => {
		if (!open) return;
		setItems(loadRecentRaw());
		setCursor(0);
	}, [open]);

	const visible = useMemo(() => items.slice(0, RING_MAX), [items]);

	const basePath = user?.basePath ?? "";

	const openItem = (item: Recent) => {
		// Tasks go through the task-panel hook so the ring is consistent with
		// the full palette's behaviour (single-pane focus instead of a hard
		// route jump). Everything else uses the catalogued href when present
		// and falls back to no-op otherwise.
		if (item.type === "task") {
			taskPanel.open(item.id);
		} else if (item.href) {
			router.push(`${basePath}${item.href}`);
		} else if (item.type === "project") {
			router.push(`${basePath}/projects/${item.id}`);
		} else if (item.type === "document") {
			router.push(`${basePath}/documents/${item.id}`);
		} else if (item.type === "milestone") {
			router.push(`${basePath}/milestones/${item.id}`);
		} else if (item.type === "prompt") {
			router.push(`${basePath}/prompts/${item.id}`);
		} else if (item.type === "knowledge") {
			router.push(`${basePath}/knowledge/${item.id}`);
		} else if (item.type === "library") {
			router.push(`${basePath}/library/${item.id}`);
		} else if (item.type === "todo") {
			router.push(`${basePath}/todos`);
		}
		onOpenChange(false);
	};

	// Keyboard navigation — only wired while the ring is open. We listen at
	// the dialog level (vs. window) so the host page's j/k bindings don't
	// fight us.
	const onKeyDown = (e: React.KeyboardEvent) => {
		if (visible.length === 0) return;
		if (e.key === "ArrowDown") {
			e.preventDefault();
			setCursor((c) => (c + 1) % visible.length);
		} else if (e.key === "ArrowUp") {
			e.preventDefault();
			setCursor((c) => (c - 1 + visible.length) % visible.length);
		} else if (e.key === "Enter") {
			e.preventDefault();
			const target = visible[cursor];
			if (target) openItem(target);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent
				className="top-[20vh] max-w-md translate-y-0 p-0"
				showCloseButton={false}
				onKeyDown={onKeyDown}
			>
				<DialogHeader className="hidden">
					<DialogTitle>Quick open</DialogTitle>
				</DialogHeader>
				<div className="border-border border-b px-4 py-2">
					<div className="flex items-center justify-between gap-2">
						<span className="font-[510] text-[12px] text-foreground tracking-[-0.005em]">
							Quick open
						</span>
						<span className="text-[11px] text-muted-foreground">
							↑↓ navigate · ↵ open · esc close
						</span>
					</div>
				</div>
				{visible.length === 0 ? (
					<div className="px-4 py-8 text-center">
						<p className="text-[13px] text-muted-foreground">
							Nothing recent yet. Open the command palette (⌘K) to start
							building your ring.
						</p>
					</div>
				) : (
					<ul ref={listRef} className="py-1">
						{visible.map((item, i) => {
							const Icon = TYPE_ICON[item.type] ?? LayersIcon;
							const label = TYPE_LABEL[item.type] ?? item.type;
							const focused = i === cursor;
							return (
								<li key={`${item.id}-${i}`}>
									<button
										type="button"
										data-focused={focused || undefined}
										onMouseEnter={() => setCursor(i)}
										onClick={() => openItem(item)}
										className={cn(
											"flex w-full items-center gap-2.5 px-4 py-1.5 text-left text-[13px] transition-colors",
											focused
												? "bg-accent/60 text-foreground"
												: "text-foreground hover:bg-accent/30",
										)}
									>
										<Icon className="size-4 shrink-0 text-muted-foreground" />
										<span className="shrink-0 rounded-sm border border-border px-1 py-px font-mono text-[10px] text-muted-foreground uppercase tracking-[0.04em]">
											{label}
										</span>
										<span className="min-w-0 flex-1 truncate">
											{item.title}
										</span>
										<span className="shrink-0 text-[11px] text-muted-foreground tabular-nums">
											{relative(item.visitedAt)}
										</span>
										<ChevronRightIcon
											className={cn(
												"size-3.5 shrink-0 text-muted-foreground transition-opacity",
												focused ? "opacity-100" : "opacity-0",
											)}
										/>
									</button>
								</li>
							);
						})}
					</ul>
				)}
			</DialogContent>
		</Dialog>
	);
};

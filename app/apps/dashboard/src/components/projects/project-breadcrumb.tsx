"use client";

/**
 * Sticky breadcrumb row at the top of every project-detail route
 * (iter-10 Round E, Task 3).
 *
 * Layout:
 *
 *   [<] Projects / <Project name>                       [×]
 *
 * Behaviours:
 *   - 48px tall, surface-1 background, hairline bottom border, sticky top-0
 *     so it stays visible while the tab strip and content scroll.
 *   - The back chevron + "Projects" link route to `/projects`.
 *   - The trailing × closes the project detail and returns to `/projects`.
 *   - Esc fires the same action via the shortcut registry (action
 *     `row.escape`, scoped to row-aware lists — we reuse it as the canonical
 *     "back / cancel" chord rather than adding a new entry that would clash
 *     with row.escape's existing wiring).
 *
 * The component avoids `useBreadcrumbs` deliberately — the global Breadcrumbs
 * widget keeps rendering inside the topbar, and this surface is the route-
 * specific override that only exists for project detail. Keeping it scoped
 * means the route layout owns the visual hierarchy without the chrome layer
 * having to special-case project URLs.
 *
 * The "Ask AI" button (FEAT-006 item 2) seeds the global chat input store
 * with "help me with <project>" before navigating to a fresh chat id — the
 * same `useChatStore` the composer already reads from, so no new query-param
 * plumbing is needed between this header and the chat route.
 */

import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import { generateId } from "ai";
import { ArrowLeftIcon, MessageCircleIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo } from "react";
import { useChatStore } from "@/store/chat";

interface Props {
	projectName: string;
	backHref: string;
	className?: string;
}

export function ProjectBreadcrumb({ projectName, backHref, className }: Props) {
	const router = useRouter();
	const setChatInput = useChatStore((state) => state.setInput);
	// Generated once per mount so the href is stable across re-renders but a
	// fresh navigation (or a remount on projectName change) gets its own chat.
	const seededChatId = useMemo(() => generateId(), []);

	const close = useCallback(() => {
		router.push(backHref);
	}, [router, backHref]);

	// Esc returns to /projects. We avoid the shortcut registry's `row.escape`
	// because that scope assumes a row list; this is a route-level shortcut
	// that's only live on the project detail. A useEffect listener is the
	// lightest implementation that respects editable targets.
	useEffect(() => {
		const onKey = (event: KeyboardEvent) => {
			if (event.key !== "Escape") return;
			const target = event.target as HTMLElement | null;
			if (!target) return;
			const tag = target.tagName.toLowerCase();
			if (
				tag === "input" ||
				tag === "textarea" ||
				tag === "select" ||
				target.isContentEditable
			) {
				return;
			}
			event.preventDefault();
			close();
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [close]);

	const base = backHref.replace(/\/projects$/, "");

	return (
		<div
			className={cn(
				"sticky top-0 z-20 flex h-12 shrink-0 items-center justify-between gap-2 border-border border-b bg-card px-4",
				className,
			)}
		>
			<div className="flex min-w-0 items-center gap-1 text-sm">
				<Link
					href={backHref}
					aria-label="Back to projects"
					className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
				>
					<ArrowLeftIcon className="size-4" />
				</Link>
				<Link
					href={backHref}
					className="rounded px-1.5 py-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
				>
					Projects
				</Link>
				<span className="text-muted-foreground/50">/</span>
				<span className="truncate px-1.5 font-medium text-foreground">
					{projectName}
				</span>
			</div>
			<div className="flex items-center gap-1">
				<Button
					asChild
					variant="ghost"
					size="sm"
					className="h-7 gap-1 px-2 text-muted-foreground hover:text-foreground"
					title={`Ask AI about ${projectName}`}
				>
					<Link
						href={`${base}/chat/${seededChatId}`}
						onClick={() => setChatInput(`help me with ${projectName}`)}
					>
						<MessageCircleIcon className="size-4" />
						<span className="hidden text-xs sm:inline">Ask AI</span>
					</Link>
				</Button>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					onClick={close}
					className="h-7 gap-1 px-2 text-muted-foreground hover:text-foreground"
					title="Close (Esc)"
					aria-label="Close project (Esc)"
				>
					<XIcon className="size-4" />
					<span className="hidden text-xs sm:inline">Close</span>
					<kbd className="ml-1 hidden rounded border border-border bg-background px-1.5 font-mono text-[10px] text-muted-foreground sm:inline">
						Esc
					</kbd>
				</Button>
			</div>
		</div>
	);
}

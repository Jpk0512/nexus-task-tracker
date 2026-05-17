"use client";

// Link-preview hover cards (codex review delighter #4).
//
// Why a single global overlay instead of one card per <a>?
//   ProseMirror replaces the editor DOM aggressively on every transaction;
//   binding listeners per anchor node would leak. We attach delegated
//   mouseover/mouseout listeners to the document and only render one card
//   at a time.
//
// What we show:
//   - Internal links (`/team/<team>/documents/<id>`, `/team/<team>/library/<id>`,
//     `/team/<team>/prompts/<slug>/<slug>`, or `nexus://doc/<id>`):
//     entity-type chip + title fetched from tRPC (documents.getById /
//     library.getById). Cached client-side via the existing react-query
//     cache that the entity pickers already hydrate.
//   - External links: "External" chip + hostname + favicon via Google's
//     s2 endpoint (best-effort; falls back to a generic globe icon).

import { useQuery } from "@tanstack/react-query";
import { GlobeIcon, LinkIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { trpc } from "@/utils/trpc";

type HoverState = {
	href: string;
	rect: DOMRect;
} | null;

/**
 * Match internal URL patterns against the team-scoped routes our app uses.
 * Returns `null` for plain external URLs.
 */
function classifyHref(href: string):
	| { kind: "document"; id: string }
	| { kind: "library"; id: string }
	| { kind: "prompt"; productSlug: string; promptSlug: string }
	| { kind: "external"; url: URL }
	| null {
	// Nexus pseudo-protocol — used by `nexus://doc/<uuid>`.
	if (href.startsWith("nexus://")) {
		const rest = href.slice("nexus://".length);
		const [kind, id] = rest.split("/");
		if (kind === "doc" && id) return { kind: "document", id };
		if (kind === "library" && id) return { kind: "library", id };
		return null;
	}

	// In-app routes.
	const docMatch = href.match(/\/team\/[^/]+\/documents\/([^/?#]+)/);
	if (docMatch?.[1]) return { kind: "document", id: docMatch[1] };

	const libMatch = href.match(/\/team\/[^/]+\/library\/([^/?#]+)/);
	if (libMatch?.[1]) return { kind: "library", id: libMatch[1] };

	const promptMatch = href.match(
		/\/team\/[^/]+\/prompts\/([^/?#]+)\/([^/?#]+)/,
	);
	if (promptMatch?.[1] && promptMatch[2]) {
		return {
			kind: "prompt",
			productSlug: promptMatch[1],
			promptSlug: promptMatch[2],
		};
	}

	// Anything else with a parseable absolute URL → external.
	try {
		const url = new URL(href, "https://_");
		// Treat same-host relative-but-app links as external for the preview —
		// they don't resolve to a known entity. The hover card is harmless.
		if (url.protocol === "http:" || url.protocol === "https:") {
			return { kind: "external", url };
		}
	} catch {
		// fallthrough
	}
	return null;
}

function LinkPreviewCard({ state }: { state: NonNullable<HoverState> }) {
	const classified = classifyHref(state.href);

	// Position above the link with a small gap, fall back below if it would
	// overflow the viewport top.
	const above = state.rect.top > 200;
	const top = above
		? state.rect.top - 8
		: state.rect.bottom + 8;
	const left = Math.max(8, state.rect.left);
	const transform = above ? "translateY(-100%)" : "none";

	if (!classified) {
		// Couldn't parse — render a minimal pill with the raw href so the user
		// at least sees what they're about to click.
		return (
			<div
				className="pointer-events-none fixed z-[60] max-w-sm rounded-md border border-border bg-popover px-3 py-2 text-sm shadow-md"
				style={{ top, left, transform }}
				role="tooltip"
			>
				<div className="flex items-center gap-2 text-muted-foreground">
					<LinkIcon className="size-3.5" />
					<span className="truncate">{state.href}</span>
				</div>
			</div>
		);
	}

	if (classified.kind === "external") {
		return (
			<div
				className="pointer-events-none fixed z-[60] w-72 rounded-md border border-border bg-popover p-3 text-sm shadow-md"
				style={{ top, left, transform }}
				role="tooltip"
			>
				<div className="flex items-center gap-2">
					{/* eslint-disable-next-line @next/next/no-img-element */}
					<img
						src={`https://www.google.com/s2/favicons?domain=${classified.url.hostname}&sz=32`}
						alt=""
						width={16}
						height={16}
						className="size-4 rounded-sm"
						onError={(e) => {
							(e.currentTarget as HTMLImageElement).style.visibility = "hidden";
						}}
					/>
					<span className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground uppercase tracking-wider">
						External
					</span>
					<span className="truncate text-muted-foreground text-xs">
						{classified.url.hostname}
					</span>
				</div>
				<div className="mt-1.5 truncate font-medium text-foreground">
					{classified.url.href}
				</div>
			</div>
		);
	}

	return <InternalLinkPreviewCard classified={classified} top={top} left={left} transform={transform} />;
}

function InternalLinkPreviewCard({
	classified,
	top,
	left,
	transform,
}: {
	classified:
		| { kind: "document"; id: string }
		| { kind: "library"; id: string }
		| { kind: "prompt"; productSlug: string; promptSlug: string };
	top: number;
	left: number;
	transform: string;
}) {
	// Use the existing tRPC procedures; react-query caches across hovers so
	// repeat-hover on the same link is instant after the first fetch.
	const docQuery = useQuery({
		...trpc.documents.getById.queryOptions(
			classified.kind === "document" ? { id: classified.id } : (undefined as any),
		),
		enabled: classified.kind === "document",
	});
	const libQuery = useQuery({
		...trpc.library.getById.queryOptions(
			classified.kind === "library" ? { id: classified.id } : (undefined as any),
		),
		enabled: classified.kind === "library",
	});

	let chipLabel: string = "Internal";
	let chipTone = "text-[var(--brand,theme(colors.violet.500))]";
	let title = "";
	let excerpt = "";

	if (classified.kind === "document") {
		chipLabel = "Doc";
		const doc = docQuery.data as
			| { name?: string | null; content?: string | null }
			| undefined;
		title = doc?.name ?? (docQuery.isLoading ? "Loading…" : "Document");
		excerpt = stripMarkdown(doc?.content ?? "");
	} else if (classified.kind === "library") {
		chipLabel = "Library";
		chipTone = "text-emerald-500";
		const entry = libQuery.data as
			| { name?: string | null; description?: string | null; body?: string | null }
			| undefined;
		title = entry?.name ?? (libQuery.isLoading ? "Loading…" : "Library entry");
		excerpt = entry?.description ?? stripMarkdown(entry?.body ?? "");
	} else {
		chipLabel = "Prompt";
		chipTone = "text-amber-500";
		title = classified.promptSlug.replace(/-/g, " ");
		excerpt = `Saved prompt in ${classified.productSlug}`;
	}

	return (
		<div
			className="pointer-events-none fixed z-[60] w-80 rounded-md border border-border bg-popover p-3 text-sm shadow-md"
			style={{ top, left, transform }}
			role="tooltip"
		>
			<div className="flex items-center gap-2">
				<GlobeIcon className={`size-3.5 ${chipTone}`} />
				<span
					className={`rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${chipTone}`}
				>
					{chipLabel}
				</span>
			</div>
			<div className="mt-1.5 truncate font-medium text-foreground">{title}</div>
			{excerpt && (
				<div className="mt-1 line-clamp-2 text-muted-foreground text-xs">
					{excerpt}
				</div>
			)}
		</div>
	);
}

function stripMarkdown(s: string): string {
	if (!s) return "";
	return s
		.replace(/^#+\s+/gm, "")
		.replace(/\*\*|\*|`|~~/g, "")
		.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
		.replace(/\n+/g, " ")
		.trim()
		.slice(0, 220);
}

/**
 * Mounted at the BlockEditor root. Attaches delegated hover listeners
 * scoped to `[data-block-editor] a.tiptap-link`. Showing requires a 250ms
 * dwell to avoid flicker on drag-through; hiding is immediate.
 */
export function LinkPreviewOverlay() {
	const [state, setState] = useState<HoverState>(null);

	useEffect(() => {
		let dwellTimer: ReturnType<typeof setTimeout> | null = null;
		let currentAnchor: HTMLAnchorElement | null = null;

		const findAnchor = (el: EventTarget | null): HTMLAnchorElement | null => {
			if (!(el instanceof HTMLElement)) return null;
			const root = el.closest("[data-block-editor]");
			if (!root) return null;
			const anchor = el.closest("a.tiptap-link") as HTMLAnchorElement | null;
			if (anchor && root.contains(anchor)) return anchor;
			return null;
		};

		const handleOver = (e: MouseEvent) => {
			const anchor = findAnchor(e.target);
			if (!anchor) return;
			if (anchor === currentAnchor) return;
			currentAnchor = anchor;
			if (dwellTimer) clearTimeout(dwellTimer);
			dwellTimer = setTimeout(() => {
				const href = anchor.getAttribute("href");
				if (!href) return;
				setState({ href, rect: anchor.getBoundingClientRect() });
			}, 250);
		};

		const handleOut = (e: MouseEvent) => {
			const fromAnchor = findAnchor(e.target);
			const toAnchor = findAnchor(e.relatedTarget);
			if (fromAnchor && toAnchor === fromAnchor) return;
			currentAnchor = null;
			if (dwellTimer) {
				clearTimeout(dwellTimer);
				dwellTimer = null;
			}
			setState(null);
		};

		document.addEventListener("mouseover", handleOver);
		document.addEventListener("mouseout", handleOut);
		return () => {
			if (dwellTimer) clearTimeout(dwellTimer);
			document.removeEventListener("mouseover", handleOver);
			document.removeEventListener("mouseout", handleOut);
		};
	}, []);

	if (!state) return null;
	return <LinkPreviewCard state={state} />;
}

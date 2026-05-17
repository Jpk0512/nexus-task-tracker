"use client";

import { cn } from "@ui/lib/utils";
import { useEffect, useMemo, useState } from "react";

type Heading = {
	level: 1 | 2 | 3;
	text: string;
	slug: string;
};

/**
 * Linear-style right-rail outline of the document.
 *
 * Reads H1/H2/H3 from the markdown source (since that's what the Tiptap
 * editor is fed). Headings get slugified IDs assigned to DOM nodes via the
 * sibling-effect (querying for matching text inside `.tiptap`), and clicking
 * an entry scrolls the editor to the heading.
 *
 * Note: parsing markdown headings via regex is fine here because the editor
 * is markdown-first (`contentType: "markdown"` in `components/editor`).
 * Code-fences are skipped to avoid false positives.
 */
export function DocumentToc({
	content,
	className,
}: {
	content?: string | null;
	className?: string;
}) {
	const headings = useMemo(() => parseHeadings(content ?? ""), [content]);
	const [activeSlug, setActiveSlug] = useState<string | null>(null);

	// Assign IDs to the rendered headings so clicks scroll to them, and observe
	// which heading is currently in view to highlight the matching outline row.
	useEffect(() => {
		if (headings.length === 0) return;

		const editorRoot = document.querySelector<HTMLElement>(".tiptap");
		if (!editorRoot) return;

		const headingEls = Array.from(
			editorRoot.querySelectorAll<HTMLElement>("h1, h2, h3"),
		);

		// Match headings to DOM nodes in order; same-text duplicates use the
		// next available element.
		const used = new Set<HTMLElement>();
		const slugToEl = new Map<string, HTMLElement>();
		for (const h of headings) {
			const match = headingEls.find(
				(el) =>
					!used.has(el) && (el.textContent ?? "").trim() === h.text.trim(),
			);
			if (match) {
				match.id = h.slug;
				used.add(match);
				slugToEl.set(h.slug, match);
			}
		}

		const observer = new IntersectionObserver(
			(entries) => {
				const visible = entries
					.filter((e) => e.isIntersecting)
					.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
				if (visible[0]) {
					setActiveSlug(visible[0].target.id);
				}
			},
			{
				rootMargin: "-80px 0px -60% 0px",
				threshold: [0, 1],
			},
		);

		for (const el of slugToEl.values()) observer.observe(el);

		return () => {
			observer.disconnect();
		};
	}, [headings]);

	if (headings.length === 0) {
		return (
			<aside
				className={cn(
					"hidden w-56 shrink-0 self-start text-muted-foreground text-xs lg:block",
					className,
				)}
			>
				<p className="px-2 py-1 font-medium text-[11px] uppercase tracking-wide">
					On this page
				</p>
				<p className="px-2 py-1">
					Add headings (H1/H2/H3) to outline your doc.
				</p>
			</aside>
		);
	}

	return (
		<aside
			className={cn(
				"hidden w-56 shrink-0 self-start lg:block",
				"sticky top-6",
				className,
			)}
		>
			<p className="px-2 py-1 font-medium text-[11px] text-muted-foreground uppercase tracking-wide">
				On this page
			</p>
			<ul className="border-l">
				{headings.map((h) => {
					const isActive = activeSlug === h.slug;
					return (
						<li key={h.slug}>
							<button
								type="button"
								onClick={() => {
									const el = document.getElementById(h.slug);
									el?.scrollIntoView({ behavior: "smooth", block: "start" });
								}}
								className={cn(
									"-ml-px block w-full truncate border-l py-1 pl-3 text-left text-xs transition-colors",
									"hover:border-foreground hover:text-foreground",
									isActive
										? "border-foreground text-foreground"
										: "border-transparent text-muted-foreground",
									h.level === 2 && "pl-5",
									h.level === 3 && "pl-7",
								)}
								title={h.text}
							>
								{h.text}
							</button>
						</li>
					);
				})}
			</ul>
		</aside>
	);
}

function parseHeadings(markdown: string): Heading[] {
	if (!markdown) return [];
	const lines = markdown.split("\n");
	const out: Heading[] = [];
	let inFence = false;
	const slugCounts = new Map<string, number>();

	for (const raw of lines) {
		const line = raw.trimEnd();
		// Toggle code-fence state on ``` or ~~~ openers/closers.
		if (/^\s*(```|~~~)/.test(line)) {
			inFence = !inFence;
			continue;
		}
		if (inFence) continue;

		const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
		if (!match) continue;

		const level = match[1].length as 1 | 2 | 3;
		const text = match[2].trim();
		if (!text) continue;

		const baseSlug = slugify(text);
		const count = slugCounts.get(baseSlug) ?? 0;
		const slug = count === 0 ? baseSlug : `${baseSlug}-${count}`;
		slugCounts.set(baseSlug, count + 1);

		out.push({ level, text, slug });
	}

	return out;
}

function slugify(text: string): string {
	return text
		.toLowerCase()
		.replace(/[^a-z0-9\s-]/g, "")
		.trim()
		.replace(/\s+/g, "-")
		.replace(/-+/g, "-");
}

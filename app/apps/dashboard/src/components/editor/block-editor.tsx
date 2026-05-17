"use client";

// Block-based editor — a Notion-style wrapper around the existing Tiptap
// `<Editor />`. New consumers (Documents / Prompts / Library detail) should
// reach for `<BlockEditor />`; legacy callers can keep importing `<Editor />`
// directly until they're migrated.
//
// What this adds on top of `<Editor />`:
//   1. Lazy-loaded heavy ProseMirror bundle (codex review #7 — perf budget).
//      The Tiptap + StarterKit + Mention bundle is ~150KB gzipped; we keep it
//      out of the initial JS payload by routing through `next/dynamic`.
//   2. Link-preview hover cards (delighter #4) — auto-injected via the
//      `<LinkPreviewOverlay />` companion component.
//   3. Reduced-motion: a `data-reduced-motion` attribute on the wrapper that
//      CSS uses to short-circuit transitions inside the editor (callout
//      icons, slash-menu fade, bubble-menu pop). Honour `prefers-reduced-
//      motion: reduce` AND the user's Motion preference (motion/react).

import dynamic from "next/dynamic";
import { useReducedMotion } from "motion/react";
import type { ComponentProps } from "react";
import { LinkPreviewOverlay } from "./link-preview";

type EditorImpl = typeof import("./index").Editor;

// `next/dynamic` keeps SSR off — Tiptap relies on `window` at construct time
// and the parent `<Editor />` already passes `immediatelyRender: false`.
const LazyEditor = dynamic(
	async () => {
		const mod = await import("./index");
		return mod.Editor;
	},
	{
		ssr: false,
		loading: () => (
			<div
				className="min-h-[180px] animate-pulse rounded-md bg-muted/40"
				aria-label="Loading editor"
			/>
		),
	},
) as unknown as EditorImpl;

export type BlockEditorProps = ComponentProps<EditorImpl>;

export function BlockEditor(props: BlockEditorProps) {
	const reduced = useReducedMotion();
	return (
		<div data-block-editor data-reduced-motion={reduced ? "reduce" : "no-preference"}>
			<LazyEditor {...props} />
			<LinkPreviewOverlay />
		</div>
	);
}

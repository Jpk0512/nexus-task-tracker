"use client";

// Mermaid Tiptap extension. Renders fenced code blocks with language="mermaid"
// as live SVG diagrams inline in the editor. Other code-block languages fall
// through to the default StarterKit rendering, so this is a strict superset.
//
// Markdown round-trip: the `@tiptap/markdown` extension parses ```mermaid …
// fences into the standard `codeBlock` node with `language: "mermaid"`, and
// serializes back the same way — so seeded docs whose `content` field
// contains a ```mermaid fence "just work" without a custom markdown rule.

import { CodeBlock } from "@tiptap/extension-code-block";
import {
	NodeViewContent,
	NodeViewWrapper,
	ReactNodeViewRenderer,
} from "@tiptap/react";
import { useEffect, useId, useRef, useState } from "react";

let mermaidApiPromise: Promise<typeof import("mermaid").default> | null = null;

function loadMermaid() {
	if (typeof window === "undefined") return Promise.resolve(null as never);
	if (!mermaidApiPromise) {
		mermaidApiPromise = import("mermaid").then((m) => {
			m.default.initialize({
				startOnLoad: false,
				securityLevel: "loose",
				theme: "neutral",
				fontFamily: "ui-sans-serif, system-ui, sans-serif",
			});
			return m.default;
		});
	}
	return mermaidApiPromise;
}

function MermaidNodeView({ node }: any) {
	const language = (node?.attrs?.language ?? "") as string;
	const code = (node?.textContent ?? "") as string;
	const [svg, setSvg] = useState<string>("");
	const [error, setError] = useState<string>("");
	const containerId = useId().replace(/[:]/g, "");
	const renderToken = useRef(0);

	useEffect(() => {
		if (language !== "mermaid") return;
		let cancelled = false;
		const token = ++renderToken.current;
		setError("");

		loadMermaid().then(async (api) => {
			if (!api || cancelled) return;
			try {
				const id = `m-${containerId}-${token}`;
				const out = await api.render(id, code);
				if (!cancelled && token === renderToken.current) setSvg(out.svg);
			} catch (e) {
				if (!cancelled && token === renderToken.current) {
					setError(e instanceof Error ? e.message : String(e));
				}
			}
		});
		return () => {
			cancelled = true;
		};
	}, [code, language, containerId]);

	if (language !== "mermaid") {
		// Fall through to default code rendering for non-mermaid blocks.
		return (
			<NodeViewWrapper as="pre" className="not-prose">
				<code data-language={language}>
					<NodeViewContent as="span" />
				</code>
			</NodeViewWrapper>
		);
	}

	return (
		<NodeViewWrapper
			as="div"
			className="mermaid-block not-prose my-3 rounded-md border border-border bg-card p-3"
			data-language="mermaid"
		>
			{error ? (
				<div className="space-y-2">
					<div className="font-mono text-destructive text-xs">
						Mermaid parse error: {error}
					</div>
					<pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
						<NodeViewContent as="code" />
					</pre>
				</div>
			) : svg ? (
				<>
					<div
						className="mermaid-svg flex justify-center [&_svg]:max-w-full"
						// biome-ignore lint/security/noDangerouslySetInnerHtml: trusted mermaid SVG output
						dangerouslySetInnerHTML={{ __html: svg }}
					/>
					<details className="mt-2 text-muted-foreground text-xs">
						<summary className="cursor-pointer select-none">
							Show source
						</summary>
						<pre className="mt-1 overflow-x-auto rounded bg-muted p-2">
							<NodeViewContent as="code" />
						</pre>
					</details>
				</>
			) : (
				<div className="font-mono text-muted-foreground text-xs">
					Rendering Mermaid diagram…
					<NodeViewContent as="code" className="hidden" />
				</div>
			)}
		</NodeViewWrapper>
	);
}

export const MermaidCodeBlock = CodeBlock.extend({
	addNodeView() {
		return ReactNodeViewRenderer(MermaidNodeView);
	},
});

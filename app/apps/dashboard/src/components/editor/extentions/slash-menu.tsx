"use client";

// Slash-command menu for the document editor.
// Type "/" → a small popup of insert actions opens. Type to filter,
// arrow keys to navigate, Enter to insert. Esc / blur dismisses.
//
// Inserts:
//  - Block primitives: mermaid block, code block, headings, bullet /
//    numbered list, quote, divider.
//  - Iter4 entity links: /task, /doc, /note, /prompt — each opens a
//    nested entity-picker popover. Picking an entity inserts the right
//    mention node (taskMention / documentMention / knowledgeMention /
//    promptMention) which renders as a compact inline pill.

import type { Editor, Range } from "@tiptap/react";
import { Extension, ReactRenderer } from "@tiptap/react";
import Suggestion from "@tiptap/suggestion";
import { forwardRef, useEffect, useImperativeHandle, useState } from "react";
import tippy, { type Instance as TippyInstance } from "tippy.js";
import { EntityPicker, type PickedEntity } from "./entity-picker";

type SlashCategory = "Basic" | "Media" | "Code" | "Links" | "Advanced";

type SlashItem = {
	title: string;
	subtitle: string;
	icon: string;
	keywords: string[];
	/** Group header in the menu (Basic / Media / Code / Links / Advanced). */
	category: SlashCategory;
	/** Optional keyboard hint (e.g. "##", "*", "[]") shown on the right. */
	shortcut?: string;
	command: (args: { editor: Editor; range: Range }) => void;
};

const CATEGORY_ORDER: SlashCategory[] = [
	"Basic",
	"Media",
	"Code",
	"Links",
	"Advanced",
];

/**
 * Open a nested entity-picker popover anchored at the slash range. Inserts
 * the appropriate mention node when a result is picked.
 */
function openEntityPicker({
	editor,
	range,
	kind,
}: {
	editor: Editor;
	range: Range;
	kind: PickedEntity["kind"];
}) {
	// Strip the slash query *before* anchoring tippy — otherwise the popover
	// is anchored to a position that the next chain mutates and visually jumps.
	editor.chain().focus().deleteRange(range).run();
	const anchorPos = range.from;

	let pickerPopup: TippyInstance | null = null;
	let component: ReactRenderer | null = null;

	const close = () => {
		try {
			pickerPopup?.destroy();
		} catch {
			// noop
		}
		try {
			component?.destroy();
		} catch {
			// noop
		}
	};

	const insertAndClose = (entity: PickedEntity) => {
		const nodeType = ((): string => {
			switch (entity.kind) {
				case "task":
					return "taskMention";
				case "document":
					return "documentMention";
				case "knowledge":
					return "knowledgeMention";
				case "prompt":
					return "promptMention";
			}
		})();

		const attrs: Record<string, unknown> = {
			id: entity.id,
			label: entity.label,
		};
		if (entity.kind === "task") {
			attrs.sequence = entity.sequence ?? null;
		}
		if (entity.kind === "document") {
			attrs.icon = entity.icon ?? null;
		}
		if (entity.kind === "knowledge") {
			attrs.relativePath = entity.relativePath ?? null;
		}
		if (entity.kind === "prompt") {
			attrs.parentSlug = entity.parentSlug ?? null;
		}

		editor
			.chain()
			.focus()
			.insertContentAt(anchorPos, [
				{ type: nodeType, attrs },
				{ type: "text", text: " " },
			])
			.run();
		close();
	};

	component = new ReactRenderer(EntityPicker, {
		props: {
			kind,
			onSelect: insertAndClose,
			onCancel: close,
		},
		editor,
	});

	// Anchor at the deleted slash's coordinates.
	const view = editor.view;
	const coords = view.coordsAtPos(anchorPos);

	// `tippy("body", ...)` returns an Instance[] (CSS-selector form); the
	// element form returns a singleton. We pass `document.body`, so use the
	// singleton return directly — indexing into it as `[0]` returns undefined
	// and the popup never renders. (Bug caught the hard way: no console error,
	// just a silently invisible picker.)
	pickerPopup = tippy(document.body, {
		getReferenceClientRect: () =>
			({
				top: coords.top,
				bottom: coords.bottom,
				left: coords.left,
				right: coords.right,
				width: 0,
				height: coords.bottom - coords.top,
				x: coords.left,
				y: coords.top,
				toJSON: () => ({}),
			}) as DOMRect,
		appendTo: () => document.body,
		content: component.element,
		showOnCreate: true,
		interactive: true,
		trigger: "manual",
		placement: "bottom-start",
		// onClickOutside fires whenever the user clicks anywhere outside the
		// picker — treat as cancel (mirrors Notion / Linear UX).
		onClickOutside: close,
	}) as unknown as TippyInstance;
}

const ITEMS: SlashItem[] = [
	// ── Entity links (iter4) ────────────────────────────────────────────────
	{
		title: "Task",
		subtitle: "Link a task (EL-69) inline",
		icon: "☑",
		keywords: ["task", "issue", "link", "el", "ticket"],
		category: "Links",
		command: ({ editor, range }) =>
			openEntityPicker({ editor, range, kind: "task" }),
	},
	{
		title: "Document",
		subtitle: "Link a document inline",
		icon: "📄",
		keywords: ["doc", "document", "link", "page"],
		category: "Links",
		command: ({ editor, range }) =>
			openEntityPicker({ editor, range, kind: "document" }),
	},
	{
		title: "Knowledge note",
		subtitle: "Link a knowledge-vault note inline",
		icon: "🧠",
		keywords: ["note", "knowledge", "vault", "obsidian"],
		category: "Links",
		command: ({ editor, range }) =>
			openEntityPicker({ editor, range, kind: "knowledge" }),
	},
	{
		title: "Prompt",
		subtitle: "Link a saved prompt inline",
		icon: "💬",
		keywords: ["prompt", "ai", "template"],
		category: "Links",
		command: ({ editor, range }) =>
			openEntityPicker({ editor, range, kind: "prompt" }),
	},
	// ── Block primitives ────────────────────────────────────────────────────
	{
		title: "Mermaid diagram",
		subtitle: "Insert a Mermaid flowchart / sequence / etc.",
		icon: "🌳",
		keywords: ["mermaid", "diagram", "flowchart", "sequence"],
		category: "Media",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.insertContent({
					type: "codeBlock",
					attrs: { language: "mermaid" },
					content: [
						{
							type: "text",
							text: 'flowchart LR\n  A["Start"] --> B["Step"] --> C["Done"]',
						},
					],
				})
				.run();
		},
	},
	{
		title: "Code block",
		subtitle: "Fenced code (pick the language inline)",
		icon: "⌘",
		keywords: ["code", "block", "snippet"],
		category: "Code",
		shortcut: "```",
		command: ({ editor, range }) => {
			editor.chain().focus().deleteRange(range).setCodeBlock().run();
		},
	},
	{
		title: "Heading 1",
		subtitle: "Section title",
		icon: "H1",
		keywords: ["h1", "heading1", "title"],
		category: "Basic",
		shortcut: "#",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setNode("heading", { level: 1 })
				.run();
		},
	},
	{
		title: "Heading 2",
		subtitle: "Subsection",
		icon: "H2",
		keywords: ["h2", "heading2"],
		category: "Basic",
		shortcut: "##",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setNode("heading", { level: 2 })
				.run();
		},
	},
	{
		title: "Heading 3",
		subtitle: "Sub-subsection",
		icon: "H3",
		keywords: ["h3", "heading3"],
		category: "Basic",
		shortcut: "###",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setNode("heading", { level: 3 })
				.run();
		},
	},
	{
		title: "Bullet list",
		subtitle: "Unordered list",
		icon: "•",
		keywords: ["bullet", "list", "ul"],
		category: "Basic",
		shortcut: "*",
		command: ({ editor, range }) => {
			editor.chain().focus().deleteRange(range).toggleBulletList().run();
		},
	},
	{
		title: "Numbered list",
		subtitle: "Ordered list",
		icon: "1.",
		keywords: ["numbered", "ordered", "list", "ol"],
		category: "Basic",
		shortcut: "1.",
		command: ({ editor, range }) => {
			editor.chain().focus().deleteRange(range).toggleOrderedList().run();
		},
	},
	{
		title: "Quote",
		subtitle: "Blockquote",
		icon: "❝",
		keywords: ["quote", "blockquote"],
		category: "Basic",
		shortcut: ">",
		command: ({ editor, range }) => {
			editor.chain().focus().deleteRange(range).setBlockquote().run();
		},
	},
	{
		title: "Divider",
		subtitle: "Horizontal rule",
		icon: "—",
		keywords: ["divider", "hr", "rule", "horizontal"],
		category: "Media",
		shortcut: "---",
		command: ({ editor, range }) => {
			editor.chain().focus().deleteRange(range).setHorizontalRule().run();
		},
	},
	{
		title: "Task list",
		subtitle: "Checkboxes for action items",
		icon: "☐",
		keywords: ["task", "todo", "checkbox", "check", "list"],
		category: "Basic",
		shortcut: "[]",
		command: ({ editor, range }) => {
			editor.chain().focus().deleteRange(range).toggleTaskList().run();
		},
	},
	// ── Callouts (iter10) ───────────────────────────────────────────────────
	// Notion-style coloured asides. 4 variants — picked here so the user can
	// type "/info" / "/warn" / "/tip" / "/quote" and get the right one in one
	// shot rather than inserting then cycling.
	{
		title: "Callout — Info",
		subtitle: "Lavender aside for general context",
		icon: "ⓘ",
		keywords: ["callout", "info", "note", "aside"],
		category: "Advanced",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setCallout({ variant: "info" })
				.run();
		},
	},
	{
		title: "Callout — Warning",
		subtitle: "Amber aside for cautions / risks",
		icon: "⚠",
		keywords: ["callout", "warn", "warning", "caution", "danger"],
		category: "Advanced",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setCallout({ variant: "warn" })
				.run();
		},
	},
	{
		title: "Callout — Tip",
		subtitle: "Emerald aside for advice / pointers",
		icon: "💡",
		keywords: ["callout", "tip", "hint", "advice"],
		category: "Advanced",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setCallout({ variant: "tip" })
				.run();
		},
	},
	{
		title: "Callout — Quote",
		subtitle: "Muted aside framed as a pull-quote",
		icon: "❝",
		keywords: ["callout", "quote", "pull"],
		category: "Advanced",
		command: ({ editor, range }) => {
			editor
				.chain()
				.focus()
				.deleteRange(range)
				.setCallout({ variant: "quote" })
				.run();
		},
	},
];

const SlashMenuList = forwardRef<
	{ onKeyDown: (e: KeyboardEvent) => boolean },
	{ items: SlashItem[]; command: (item: SlashItem) => void }
>(function SlashMenuList({ items, command }, ref) {
	const [selected, setSelected] = useState(0);

	useEffect(() => {
		setSelected(0);
	}, [items]);

	useImperativeHandle(ref, () => ({
		onKeyDown: (e) => {
			if (e.key === "ArrowDown") {
				setSelected((s) => (s + 1) % Math.max(items.length, 1));
				return true;
			}
			if (e.key === "ArrowUp") {
				setSelected((s) => (s - 1 + items.length) % Math.max(items.length, 1));
				return true;
			}
			if (e.key === "Enter") {
				const it = items[selected];
				if (it) command(it);
				return true;
			}
			return false;
		},
	}));

	if (items.length === 0) {
		return (
			<div className="rounded-md border border-border bg-popover px-3 py-2 text-muted-foreground text-sm shadow">
				No matches
			</div>
		);
	}

	// Group by category so the menu has visible section headers (Basic /
	// Media / Code / Links / Advanced) — matches Notion's slash menu.
	const grouped = groupByCategory(items);

	// Build a flat index → button-data map so arrow keys / Enter still work
	// on the un-grouped sequence the parent state machine drives.
	let flatIndex = -1;

	return (
		<div className="max-h-80 w-80 overflow-auto rounded-md border border-border bg-popover py-1 shadow">
			{CATEGORY_ORDER.flatMap((cat) => {
				const group = grouped.get(cat);
				if (!group || group.length === 0) return [];
				return [
					<div
						key={`hdr-${cat}`}
						className="px-3 pt-2 pb-1 font-medium text-muted-foreground text-[10px] uppercase tracking-wider"
					>
						{cat}
					</div>,
					...group.map((it) => {
						flatIndex += 1;
						const i = flatIndex;
						return (
							<button
								key={it.title}
								type="button"
								onMouseDown={(e) => {
									e.preventDefault();
									command(it);
								}}
								onMouseEnter={() => setSelected(i)}
								className={`flex w-full items-start gap-3 px-3 py-1.5 text-left ${
									i === selected ? "bg-accent text-accent-foreground" : ""
								}`}
							>
								<span className="w-6 shrink-0 font-mono text-muted-foreground text-xs">
									{it.icon}
								</span>
								<span className="min-w-0 grow">
									<span className="block font-medium text-sm">{it.title}</span>
									<span className="block text-muted-foreground text-xs">
										{it.subtitle}
									</span>
								</span>
								{it.shortcut && (
									<kbd className="ml-2 shrink-0 self-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
										{it.shortcut}
									</kbd>
								)}
							</button>
						);
					}),
				];
			})}
		</div>
	);
});

function groupByCategory(items: SlashItem[]): Map<SlashCategory, SlashItem[]> {
	const m = new Map<SlashCategory, SlashItem[]>();
	for (const it of items) {
		const arr = m.get(it.category) ?? [];
		arr.push(it);
		m.set(it.category, arr);
	}
	return m;
}

function filterItems(query: string): SlashItem[] {
	if (!query) return ITEMS;
	const q = query.toLowerCase();
	return ITEMS.filter(
		(it) =>
			it.title.toLowerCase().includes(q) ||
			it.keywords.some((k) => k.includes(q)),
	);
}

export const SlashCommand = Extension.create({
	name: "slashCommand",

	addOptions() {
		return {
			suggestion: {
				char: "/",
				startOfLine: false,
				command: ({
					editor,
					range,
					props,
				}: {
					editor: Editor;
					range: Range;
					props: SlashItem;
				}) => {
					props.command({ editor, range });
				},
			},
		};
	},

	addProseMirrorPlugins() {
		return [
			Suggestion({
				editor: this.editor,
				char: "/",
				startOfLine: false,
				command: ({ editor, range, props }) => {
					(props as SlashItem).command({ editor, range });
				},
				items: ({ query }: { query: string }) => filterItems(query),
				render: () => {
					let component: any;
					let popup: any;
					// `command` only lives on Suggestion's onStart/onUpdate props in
					// Tiptap 3.x — `onKeyDown` props are { view, event, range } only.
					// Cache the most recent command callback so the Enter handler can
					// reach it. (Catch this with a guard rather than blindly calling
					// props.command(it) in onKeyDown — that path silently throws.)
					let lastCommand: ((it: SlashItem) => void) | null = null;

					return {
						onStart: (props: any) => {
							lastCommand = props.command;
							const root = document.createElement("div");
							component = { root, listRef: { current: null as any } };

							// Tiptap's `Suggestion` plugin uses a vanilla DOM renderer. We
							// reproduce the React `SlashMenuList` look here so the build
							// stays free of imperative ReactDOM mounting for the *outer*
							// slash menu; the nested EntityPicker still uses ReactRenderer
							// (see openEntityPicker above).
							const renderList = (items: SlashItem[], selected: number) => {
								root.innerHTML = "";
								const wrap = document.createElement("div");
								wrap.className =
									"max-h-80 w-80 overflow-auto rounded-md border border-border bg-popover py-1 shadow";

								if (items.length === 0) {
									const empty = document.createElement("div");
									empty.className =
										"px-3 py-2 text-muted-foreground text-sm";
									empty.textContent = "No matches";
									wrap.appendChild(empty);
									root.appendChild(wrap);
									return;
								}

								// Reproduce the grouped-by-category layout from the React
								// `SlashMenuList`. Flat index is consumed by arrow keys.
								const grouped = groupByCategory(items);
								let flat = -1;
								for (const cat of CATEGORY_ORDER) {
									const group = grouped.get(cat);
									if (!group || group.length === 0) continue;
									const hdr = document.createElement("div");
									hdr.className =
										"px-3 pt-2 pb-1 font-medium text-muted-foreground text-[10px] uppercase tracking-wider";
									hdr.textContent = cat;
									wrap.appendChild(hdr);
									for (const it of group) {
										flat += 1;
										const i = flat;
										const btn = document.createElement("button");
										btn.type = "button";
										btn.className = `flex w-full items-start gap-3 px-3 py-1.5 text-left ${
											i === selected ? "bg-accent text-accent-foreground" : ""
										}`;
										btn.onmousedown = (e) => {
											e.preventDefault();
											lastCommand?.(it);
										};
										// `it.shortcut` is sanitised at source (string literal in
										// ITEMS); no untrusted input flows into innerHTML here.
										const kbd = it.shortcut
											? `<kbd class="ml-2 shrink-0 self-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">${it.shortcut}</kbd>`
											: "";
										btn.innerHTML = `
											<span class="w-6 shrink-0 font-mono text-muted-foreground text-xs">${it.icon}</span>
											<span class="min-w-0 grow">
												<span class="block font-medium text-sm">${it.title}</span>
												<span class="block text-muted-foreground text-xs">${it.subtitle}</span>
											</span>
											${kbd}
										`;
										wrap.appendChild(btn);
									}
								}
								root.appendChild(wrap);
							};
							component.renderList = renderList;
							component.items = props.items;
							component.selected = 0;
							renderList(component.items, component.selected);

							popup = tippy("body", {
								getReferenceClientRect: props.clientRect,
								appendTo: () => document.body,
								content: root,
								showOnCreate: true,
								interactive: true,
								trigger: "manual",
								placement: "bottom-start",
							});
						},
						onUpdate(props: any) {
							lastCommand = props.command;
							component.items = props.items;
							component.selected = 0;
							component.renderList(component.items, component.selected);
							popup?.[0]?.setProps({
								getReferenceClientRect: props.clientRect,
							});
						},
						onKeyDown(props: any) {
							if (props.event.key === "Escape") {
								popup?.[0]?.hide();
								return true;
							}
							if (props.event.key === "ArrowDown") {
								component.selected =
									(component.selected + 1) % component.items.length;
								component.renderList(component.items, component.selected);
								return true;
							}
							if (props.event.key === "ArrowUp") {
								component.selected =
									(component.selected - 1 + component.items.length) %
									component.items.length;
								component.renderList(component.items, component.selected);
								return true;
							}
							if (props.event.key === "Enter") {
								const it = component.items[component.selected];
								if (it && lastCommand) {
									lastCommand(it);
									return true;
								}
							}
							return false;
						},
						onExit() {
							popup?.[0]?.destroy();
							lastCommand = null;
						},
					};
				},
			}),
		];
	},
});

export { SlashMenuList };

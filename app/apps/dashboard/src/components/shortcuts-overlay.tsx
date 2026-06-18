"use client";

import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { Kbd, KbdGroup } from "@ui/components/ui/kbd";
import { SearchIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { SHORTCUTS, type ShortcutSpec } from "@/lib/shortcuts/registry";

/**
 * Keyboard-shortcuts cheatsheet, derived live from the central registry
 * (`@/lib/shortcuts/registry`).
 *
 * Mounted globally via `GlobalShortcuts`. Pressing `?` opens it; the user can
 * dismiss with Esc, and the legacy hard-coded SECTIONS list is gone — every
 * row here is one entry from `SHORTCUTS`, so the overlay tracks the registry
 * with zero hand-maintenance.
 *
 * Visual: a search input at the top filters shortcuts in real time. Sections
 * are bucketed by scope (Global · Navigate · Row · Triage · Route).
 */

const SECTION_ORDER: { id: string; title: string }[] = [
	{ id: "global", title: "Global" },
	{ id: "navigate", title: "Navigate" },
	{ id: "row", title: "Row actions" },
	{ id: "triage", title: "Triage" },
	{ id: "route", title: "Route" },
	{ id: "modal", title: "Modal" },
];

/**
 * Bucket a shortcut into the user-facing section labels. The registry stores
 * the raw `scope`; this mapper applies the soft groupings the overlay shows
 * (e.g. `g <key>` is `scope: 'global'` but lives under "Navigate", and
 * `/triage` row moves live under "Triage").
 */
function bucketOf(spec: ShortcutSpec): string {
	if (spec.route === "/triage") return "triage";
	if (spec.scope === "global" && spec.keys.startsWith("g ")) return "navigate";
	if (spec.action.startsWith("nav.")) return "navigate";
	return spec.scope;
}

/**
 * Split a `keys` string (`"mod+shift+p"`, `"g t"`) into the individual key
 * caps the overlay should render. `mod` becomes ⌘ (we ignore the
 * Cmd/Ctrl swap here — the registry uses `mod` to mean "Cmd on Mac, Ctrl
 * elsewhere" and showing the ⌘ glyph is the Linear/Things convention).
 */
function splitKeys(keys: string): string[] {
	if (keys.includes(" ")) {
		// sequence chord e.g. "g t"
		return keys.split(/\s+/).map(formatKey);
	}
	return keys.split("+").map(formatKey);
}

function formatKey(part: string): string {
	switch (part.toLowerCase()) {
		case "mod":
			return "⌘";
		case "ctrl":
			return "Ctrl";
		case "shift":
			return "⇧";
		case "alt":
			return "⌥";
		case "enter":
			return "↵";
		case "escape":
			return "Esc";
		case " ":
		case "space":
			return "Space";
		default:
			return part.length === 1 ? part.toUpperCase() : part;
	}
}

export const ShortcutsOverlay = ({
	open,
	onOpenChange,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) => {
	const [query, setQuery] = useState("");

	// Reset the search input each time the overlay reopens — opening it stale
	// from a previous session is a tiny papercut.
	useEffect(() => {
		if (!open) setQuery("");
	}, [open]);

	// Mod+/ acts as an overlay toggle while it is open (Esc handled by Dialog).
	// Outside the overlay, Mod+/ binds to filter.focus per the registry.
	useEffect(() => {
		if (!open) return;
		const handler = (e: KeyboardEvent) => {
			if (
				(e.metaKey || e.ctrlKey) &&
				!e.shiftKey &&
				!e.altKey &&
				e.key === "/"
			) {
				e.preventDefault();
				onOpenChange(false);
			}
		};
		window.addEventListener("keydown", handler);
		return () => window.removeEventListener("keydown", handler);
	}, [open, onOpenChange]);

	const sections = useMemo(() => {
		const buckets = new Map<string, ShortcutSpec[]>();
		for (const spec of SHORTCUTS) {
			const id = bucketOf(spec);
			if (!buckets.has(id)) buckets.set(id, []);
			buckets.get(id)!.push(spec);
		}

		const q = query.trim().toLowerCase();
		const filter = (s: ShortcutSpec) => {
			if (!q) return true;
			return (
				s.label.toLowerCase().includes(q) ||
				s.keys.toLowerCase().includes(q) ||
				s.action.toLowerCase().includes(q)
			);
		};

		const known = SECTION_ORDER.map(({ id, title }) => {
			const items = (buckets.get(id) ?? []).filter(filter);
			return { id, title, items };
		}).filter((s) => s.items.length > 0);

		// Surface unknown buckets at the end so a future scope can't go missing.
		const knownIds = new Set(SECTION_ORDER.map((s) => s.id));
		const extras = [...buckets.entries()]
			.filter(([id]) => !knownIds.has(id))
			.map(([id, items]) => ({
				id,
				title: id.charAt(0).toUpperCase() + id.slice(1),
				items: items.filter(filter),
			}))
			.filter((s) => s.items.length > 0);

		return [...known, ...extras];
	}, [query]);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-md gap-0 p-0">
				<DialogHeader className="border-border border-b px-5 py-3.5">
					<DialogTitle className="font-[510] text-[14px] tracking-[-0.012em]">
						Keyboard shortcuts
					</DialogTitle>
					<DialogDescription>
						The fastest way to move around Nexus.
					</DialogDescription>
				</DialogHeader>
				<div className="border-border border-b px-4 py-2">
					<label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
						<SearchIcon className="size-3.5" aria-hidden="true" />
						<input
							autoFocus
							value={query}
							onChange={(e) => setQuery(e.target.value)}
							placeholder="Filter shortcuts…"
							className="w-full bg-transparent outline-none placeholder:text-muted-foreground/60 focus:text-foreground"
							aria-label="Filter shortcuts"
						/>
					</label>
				</div>
				<div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto px-5 py-4">
					{sections.length === 0 ? (
						<p className="py-6 text-center text-[12.5px] text-muted-foreground">
							No shortcuts match "{query}".
						</p>
					) : (
						sections.map((section) => (
							<section key={section.id}>
								<h3 className="mb-2 font-[510] text-[10px] text-muted-foreground uppercase tracking-[0.06em]">
									{section.title}
								</h3>
								<ul className="flex flex-col">
									{section.items.map((item) => (
										<li
											key={item.action}
											className="flex items-center justify-between py-1.5 text-[12.5px] text-foreground"
										>
											<span>{item.label}</span>
											<KbdGroup>
												{splitKeys(item.keys).map((k, i) => (
													<Kbd key={`${item.action}-${i}-${k}`}>{k}</Kbd>
												))}
											</KbdGroup>
										</li>
									))}
								</ul>
							</section>
						))
					)}
				</div>
				<div className="border-border border-t bg-muted/20 px-5 py-2.5 text-[11px] text-muted-foreground">
					Press <Kbd>?</Kbd> at any time to reopen this list.
				</div>
			</DialogContent>
		</Dialog>
	);
};

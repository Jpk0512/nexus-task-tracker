"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	AlertCircleIcon,
	BookOpenIcon,
	BrainIcon,
	CalendarIcon,
	CheckIcon,
	EyeIcon,
	EyeOffIcon,
	FileTextIcon,
	FolderIcon,
	FolderOpenIcon,
	FolderTreeIcon,
	LayersIcon,
	LightbulbIcon,
	PencilLineIcon,
	PlusIcon,
	RefreshCwIcon,
	SaveIcon,
	SearchIcon,
	Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
import { BlockEditor } from "@/components/editor/block-editor";
import {
	type KnowledgeNoteRow,
	NoteGroup,
} from "@/components/knowledge/note-group";
import { WikiLinkInline } from "@/components/knowledge/wiki-link-inline";
import { trpc } from "@/utils/trpc";

// Knowledge tab — Obsidian vault editor. Linear-style left rail: grouped
// collapsible sections by parent directory (Daily / Permanent / Drafts /
// References / Projects / Ideas / Other), plus an empty-state CTA panel on the
// right when no note is selected.

type NoteListItem = {
	id: string;
	name: string;
	relativePath: string;
	parentDir: string | null;
	updatedAt: string;
};

type GroupKey =
	| "daily"
	| "permanent"
	| "drafts"
	| "references"
	| "projects"
	| "ideas"
	| "other";

type GroupDef = {
	key: GroupKey;
	title: string;
	icon: any;
	// Predicate against the relativePath (forward-slash normalized).
	match: (path: string) => boolean;
};

const RESERVED_TOP_DIRS = new Set([
	"daily",
	"drafts",
	"references",
	"projects",
	"ideas",
]);

const GROUPS: GroupDef[] = [
	{
		key: "daily",
		title: "Daily",
		icon: CalendarIcon,
		match: (p) => /^daily\//i.test(p),
	},
	{
		key: "permanent",
		title: "Permanent",
		icon: LayersIcon,
		// Anything NOT in one of the reserved subtrees. Includes top-level files.
		match: (p) => {
			const first = p.split("/")[0]?.toLowerCase() ?? "";
			if (!first) return false;
			// Top-level file (no slash) — treat as permanent.
			if (!p.includes("/")) return true;
			return !RESERVED_TOP_DIRS.has(first);
		},
	},
	{
		key: "drafts",
		title: "Drafts",
		icon: PencilLineIcon,
		match: (p) => /^drafts\//i.test(p),
	},
	{
		key: "references",
		title: "References",
		icon: BookOpenIcon,
		match: (p) => /^references\//i.test(p),
	},
	{
		key: "projects",
		title: "Projects",
		icon: FolderIcon,
		match: (p) => /^projects\//i.test(p),
	},
	{
		key: "ideas",
		title: "Ideas",
		icon: LightbulbIcon,
		match: (p) => /^ideas\//i.test(p),
	},
];

const OTHER_GROUP: GroupDef = {
	key: "other",
	title: "Other",
	icon: FolderTreeIcon,
	match: () => true,
};

function normalize(path: string): string {
	return path.replace(/\\+/g, "/");
}

function classify(note: NoteListItem): GroupKey {
	const p = normalize(note.relativePath);
	for (const g of GROUPS) {
		if (g.match(p)) return g.key;
	}
	return "other";
}

export function KnowledgeView() {
	const qc = useQueryClient();
	const searchParams = useSearchParams();
	const { team } = useParams<{ team: string }>();
	const initialNoteId = searchParams?.get("note") ?? null;
	const [search, setSearch] = useState("");
	const [selectedId, setSelectedId] = useState<string | null>(initialNoteId);
	const [draft, setDraft] = useState("");
	const [newPath, setNewPath] = useState("");
	const [showNew, setShowNew] = useState(false);
	const [browseAll, setBrowseAll] = useState(false);
	// "Manage categories" toggle — when off (default), empty groups are hidden
	// to reduce sidebar clutter (per iter-10 visual-baseline). When on, all
	// reserved categories render even when empty so the user can discover
	// where new notes will land.
	const [showAllCategories, setShowAllCategories] = useState(false);
	const newPathInputRef = useRef<HTMLInputElement | null>(null);

	// Auto-save state machine for the BlockEditor (GWT#5). The header-right
	// indicator reflects this; "conflict" is transient — we re-fetch the disk
	// sha and re-save (last-write-wins, DEC-010) rather than block the user.
	const [autoSaveState, setAutoSaveState] = useState<
		"idle" | "dirty" | "saving" | "saved" | "conflict"
	>("idle");
	// Debounce timer + the latest editor content/sha, kept in refs so the
	// blur-triggered timeout always reads fresh values without re-binding.
	const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	// Single-flight mutex: skip if a save is already in progress so rapid
	// blur/refocus cannot enqueue two concurrent knowledge.update mutations.
	const saveInFlight = useRef(false);
	const draftRef = useRef("");
	const shaRef = useRef<string | null>(null);

	const listQuery = useQuery(
		trpc.knowledge.get.queryOptions({ search: search || undefined }),
	);
	const noteQuery = useQuery({
		...trpc.knowledge.getById.queryOptions({ id: selectedId ?? "" }),
		enabled: !!selectedId,
	});

	useEffect(() => {
		if (noteQuery.data) {
			// Construct file content from frontmatter + body (round-tripped).
			const fm = noteQuery.data.frontmatter as Record<string, unknown> | null;
			const lines: string[] = [];
			if (fm && Object.keys(fm).length > 0) {
				lines.push("---");
				for (const [k, v] of Object.entries(fm)) {
					if (Array.isArray(v)) {
						lines.push(`${k}: [${v.map((x) => JSON.stringify(x)).join(", ")}]`);
					} else if (typeof v === "string") {
						lines.push(`${k}: ${/[:#]/.test(v) ? JSON.stringify(v) : v}`);
					} else {
						lines.push(`${k}: ${v}`);
					}
				}
				lines.push("---");
				lines.push("");
			}
			lines.push(noteQuery.data.content ?? "");
			const next = lines.join("\n");
			setDraft(next);
			draftRef.current = next;
			shaRef.current = noteQuery.data.fileSha;
			setAutoSaveState("idle");
		}
	}, [noteQuery.data?.id, noteQuery.data?.fileSha]);

	const refetchList = () =>
		qc.invalidateQueries({ queryKey: [["knowledge", "get"]] });
	const refetchNote = () =>
		qc.invalidateQueries({ queryKey: [["knowledge", "getById"]] });

	const scanMut = useMutation(
		trpc.knowledge.scan.mutationOptions({
			onSuccess: (data) => {
				const total = (
					data as {
						results: Array<{
							inserted: number;
							updated: number;
							deleted: number;
						}>;
					}
				).results.reduce((n, r) => n + r.inserted + r.updated + r.deleted, 0);
				toast.success(
					total === 0
						? "Vault is up to date"
						: `Re-scanned (${total} change${total === 1 ? "" : "s"})`,
				);
				refetchList();
				refetchNote();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const createMut = useMutation(
		trpc.knowledge.create.mutationOptions({
			onSuccess: (note) => {
				toast.success(`Created ${(note as any).relativePath}`);
				setShowNew(false);
				setNewPath("");
				setSelectedId((note as any).id);
				refetchList();
			},
			onError: (e) => toast.error(e.message),
		}),
	);
	const updateMut = useMutation(
		trpc.knowledge.update.mutationOptions({
			onSuccess: () => {
				toast.success("Saved to disk");
				refetchList();
				refetchNote();
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	// Auto-save (GWT#5): blur fires the knowledge.update mutation after a 500ms
	// debounce. A CONFLICT (disk sha moved under us) re-fetches the live sha via
	// getById and re-saves the editor content immediately — last-write-wins,
	// DEC-010. The header indicator alone narrates; nothing blocks the writer.
	const updateAsync = updateMut.mutateAsync;
	const autoSave = useCallback(async () => {
		if (saveInFlight.current) return;
		const id = selectedId;
		if (!id) return;
		saveInFlight.current = true;
		const content = draftRef.current;
		setAutoSaveState("saving");
		try {
			await updateAsync({ id, content, expectedSha: shaRef.current ?? "" });
			setAutoSaveState("saved");
			refetchList();
			refetchNote();
		} catch (err) {
			const isConflict = err instanceof Error && /CONFLICT/i.test(err.message);
			if (!isConflict) {
				setAutoSaveState("idle");
				toast.error(err instanceof Error ? err.message : "Save failed");
				saveInFlight.current = false;
				return;
			}
			setAutoSaveState("conflict");
			// Re-fetch the current note (fresh fileSha) and re-save (LWW).
			try {
				const fresh = await qc.fetchQuery(
					trpc.knowledge.getById.queryOptions({ id }),
				);
				const freshSha = (fresh as { fileSha?: string } | undefined)?.fileSha;
				shaRef.current = freshSha ?? null;
				await updateAsync({
					id,
					content: draftRef.current,
					expectedSha: freshSha ?? "",
				});
				setAutoSaveState("saved");
				refetchList();
				refetchNote();
			} catch {
				setAutoSaveState("idle");
			}
		} finally {
			saveInFlight.current = false;
		}
	}, [selectedId, updateAsync, qc, refetchList, refetchNote]);

	// "Saved" indicator fades back to idle after 2s (palette §3 indicator spec).
	useEffect(() => {
		if (autoSaveState !== "saved") return;
		const t = setTimeout(() => setAutoSaveState("idle"), 2000);
		return () => clearTimeout(t);
	}, [autoSaveState]);

	useEffect(
		() => () => {
			if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
		},
		[],
	);

	const deleteMut = useMutation(
		trpc.knowledge.delete.mutationOptions({
			onSuccess: () => {
				toast.success("Deleted");
				setSelectedId(null);
				refetchList();
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const promoteMut = useMutation(
		trpc.documents.create.mutationOptions({
			onSuccess: (doc: any) => {
				toast.success(`Promoted to document: ${doc?.name ?? "Untitled"}`);
				// Mark the source knowledge note with frontmatter linking the new doc.
				qc.invalidateQueries({ queryKey: [["documents", "get"]] });
			},
			onError: (e) => toast.error(e.message),
		}),
	);

	const notes = (listQuery.data?.notes ?? []) as NoteListItem[];

	// Frontmatter title isn't returned by the list query, so we look it up from
	// the currently-loaded note (the only one we have rich data for). That lets
	// the active row swap to a frontmatter title without an extra round-trip.
	const activeFrontmatterTitle = useMemo(() => {
		if (!noteQuery.data) return null;
		const fm = noteQuery.data.frontmatter as Record<string, unknown> | null;
		const t = fm?.title;
		return typeof t === "string" && t.trim().length > 0 ? t : null;
	}, [noteQuery.data?.id, noteQuery.data?.frontmatter]);

	const groupedNotes = useMemo(() => {
		const buckets = new Map<GroupKey, KnowledgeNoteRow[]>();
		for (const g of GROUPS) buckets.set(g.key, []);
		buckets.set(OTHER_GROUP.key, []);
		for (const n of notes) {
			const k = classify(n);
			const row: KnowledgeNoteRow = {
				id: n.id,
				name: n.name,
				relativePath: n.relativePath,
				parentDir: n.parentDir,
				updatedAt: n.updatedAt,
				title: selectedId === n.id ? activeFrontmatterTitle : null,
			};
			buckets.get(k)!.push(row);
		}
		// Within each group, daily sorts desc (most recent first), everything else
		// sorts asc by relativePath.
		for (const [k, arr] of buckets) {
			if (k === "daily") {
				arr.sort((a, b) => b.relativePath.localeCompare(a.relativePath));
			} else {
				arr.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
			}
		}
		return buckets;
	}, [notes, selectedId, activeFrontmatterTitle]);

	// Outgoing `[[wiki links]]` parsed from the open note, resolved against the
	// vault note list — drives the resolved(blue)/unresolved(red) link strip.
	const wikiLinks = useMemo(
		() => (selectedId ? parseWikiLinks(draft, notes) : []),
		[selectedId, draft, notes],
	);

	const hasSearchActive = search.trim().length > 0;
	const totalCount = notes.length;
	const isEmpty = totalCount === 0;

	const today = () => {
		const d = new Date();
		const y = d.getFullYear();
		const m = String(d.getMonth() + 1).padStart(2, "0");
		const da = String(d.getDate()).padStart(2, "0");
		const path = `daily/${y}-${m}-${da}`;
		// Check existing first.
		const existing = notes.find((n) => n.relativePath === `${path}.md`);
		if (existing) {
			setSelectedId(existing.id);
			return;
		}
		createMut.mutate({
			relativePath: path,
			content: `# ${y}-${m}-${da}\n\n## Notes\n\n## Done today\n\n## Tomorrow\n`,
		});
	};

	const startNewNote = () => {
		setShowNew(true);
		// Focus next tick once the input mounts.
		setTimeout(() => newPathInputRef.current?.focus(), 0);
	};

	const handlePromote = async (note: KnowledgeNoteRow) => {
		// Fetch full content via the existing getById call; the list payload
		// doesn't include body. The simplest path is to require the note be open,
		// or to fetch on the fly. Open-and-promote is the most user-friendly.
		if (selectedId !== note.id) {
			setSelectedId(note.id);
			toast.message("Open note first, then choose Promote again", {
				description: "Loading note content…",
			});
			return;
		}
		const body = noteQuery.data?.content ?? "";
		const fm = (noteQuery.data?.frontmatter ?? {}) as Record<string, unknown>;
		const titleFromFm = typeof fm.title === "string" ? fm.title : null;
		const name = titleFromFm || note.name;
		const created = (await promoteMut.mutateAsync({
			name,
			content: body,
		})) as { id?: string } | undefined;
		// Re-write the knowledge note's frontmatter with promoted_to: <doc-id>.
		if (created?.id && noteQuery.data) {
			const nextFm: Record<string, unknown> = {
				...fm,
				promoted_to: created.id,
			};
			const fmLines = ["---"];
			for (const [k, v] of Object.entries(nextFm)) {
				if (Array.isArray(v)) {
					fmLines.push(`${k}: [${v.map((x) => JSON.stringify(x)).join(", ")}]`);
				} else if (typeof v === "string") {
					fmLines.push(`${k}: ${/[:#]/.test(v) ? JSON.stringify(v) : v}`);
				} else {
					fmLines.push(`${k}: ${v}`);
				}
			}
			fmLines.push("---", "");
			const nextContent = `${fmLines.join("\n")}${body}`;
			updateMut.mutate({
				id: note.id,
				content: nextContent,
				expectedSha: noteQuery.data.fileSha,
			});
		}
	};

	const handleDelete = (note: KnowledgeNoteRow) => {
		if (confirm(`Delete "${note.name}" from disk? This cannot be undone.`)) {
			deleteMut.mutate({ id: note.id });
		}
	};

	const handleCopyPath = (note: KnowledgeNoteRow) => {
		navigator.clipboard
			?.writeText(note.relativePath)
			.then(() => toast.success("Vault path copied"))
			.catch(() => toast.error("Couldn't copy path"));
	};

	// Per-group open state. Daily always defaults open; others open when search
	// is active and the group has hits, or when the active note lives there.
	const activeGroupKey = useMemo<GroupKey | null>(() => {
		if (!selectedId) return null;
		const n = notes.find((x) => x.id === selectedId);
		return n ? classify(n) : null;
	}, [selectedId, notes]);

	const groupOpenDefault = (g: GroupDef): boolean => {
		if (browseAll) return true;
		if (g.key === "daily") return true;
		const items = groupedNotes.get(g.key) ?? [];
		if (items.length === 0) return false;
		if (hasSearchActive) return true;
		if (activeGroupKey === g.key) return true;
		return false;
	};

	return (
		<div className="flex h-full">
			{/* Left rail */}
			<aside className="flex w-72 flex-col border-border border-r">
				<div className="border-border border-b p-3">
					<div className="mb-2 flex items-center gap-2">
						<BrainIcon className="size-4 text-violet-500" />
						<h2 className="font-[510] text-[13px] tracking-[-0.005em]">
							Knowledge
						</h2>
						<div className="ml-auto flex gap-1">
							<Button
								variant="ghost"
								size="sm"
								onClick={() => scanMut.mutate(undefined)}
								disabled={scanMut.isPending}
								title="Re-scan vault from disk"
							>
								<RefreshCwIcon
									className={`size-3.5 ${scanMut.isPending ? "animate-spin" : ""}`}
								/>
							</Button>
							<Button
								variant="ghost"
								size="sm"
								onClick={today}
								title="Open today's daily log"
							>
								<CalendarIcon className="size-3.5" />
							</Button>
							<Button
								variant="ghost"
								size="sm"
								onClick={startNewNote}
								title="New note"
							>
								<PlusIcon className="size-3.5" />
							</Button>
						</div>
					</div>
					{showNew && (
						<form
							onSubmit={(e) => {
								e.preventDefault();
								if (newPath.trim())
									createMut.mutate({ relativePath: newPath.trim() });
							}}
							className="mb-2 flex gap-1"
						>
							<Input
								ref={newPathInputRef}
								value={newPath}
								onChange={(e) => setNewPath(e.target.value)}
								placeholder="path/to/note (no .md)"
								className="h-7 text-xs"
								onKeyDown={(e) => {
									if (e.key === "Escape") setShowNew(false);
								}}
							/>
							<Button type="submit" size="sm" disabled={!newPath.trim()}>
								Add
							</Button>
						</form>
					)}
					<div className="relative">
						<SearchIcon className="absolute top-2 left-2 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(e) => setSearch(e.target.value)}
							placeholder="Search notes…"
							className="h-7 pl-7 text-xs"
						/>
					</div>
				</div>
				<div className="grow overflow-y-auto">
					{isEmpty && !listQuery.isLoading ? (
						<div className="p-4 text-center text-[12px] text-muted-foreground">
							Vault is empty. Use the panel on the right to start.
						</div>
					) : (
						(() => {
							const renderedGroups = [...GROUPS, OTHER_GROUP].map((g) => {
								const items = groupedNotes.get(g.key) ?? [];
								// "Other" — always hide when empty (legacy behaviour).
								if (g.key === "other" && items.length === 0) return null;
								// Reserved categories — hide when empty unless the user has
								// toggled "Manage categories" or an active search/browse-all
								// pushed the panel into discovery mode.
								if (
									items.length === 0 &&
									!showAllCategories &&
									!browseAll &&
									!hasSearchActive
								) {
									return null;
								}
								const open = groupOpenDefault(g);
								// Remount when the computed default changes (browse-toggle,
								// search, active-note change) so Radix re-reads defaultOpen.
								const remountKey = `${g.key}:${open ? "1" : "0"}:${hasSearchActive ? "s" : ""}:${browseAll ? "b" : ""}`;
								return (
									<NoteGroup
										key={remountKey}
										icon={g.icon}
										title={g.title}
										count={items.length}
										notes={items}
										selectedId={selectedId}
										defaultOpen={open}
										onSelect={setSelectedId}
										onPromote={handlePromote}
										onDelete={handleDelete}
										onOpenInVault={handleCopyPath}
									/>
								);
							});
							const hiddenCount = [...GROUPS, OTHER_GROUP].filter((g) => {
								if (g.key === "other") return false;
								const items = groupedNotes.get(g.key) ?? [];
								return items.length === 0;
							}).length;
							return (
								<>
									{renderedGroups}
									{/* "Manage categories" — reveals reserved categories that
									    have no notes yet so the user can still find them.
									    Hidden in search/browse-all mode where everything is
									    already visible. */}
									{!hasSearchActive && !browseAll && hiddenCount > 0 && (
										<button
											type="button"
											onClick={() => setShowAllCategories((v) => !v)}
											className="flex w-full items-center gap-2 px-3 py-2 text-left text-[11px] text-muted-foreground tracking-[0.02em] transition-colors hover:bg-accent/40 hover:text-foreground"
											title={
												showAllCategories
													? "Hide empty categories"
													: "Show all reserved categories"
											}
										>
											{showAllCategories ? (
												<EyeOffIcon className="size-3" />
											) : (
												<EyeIcon className="size-3" />
											)}
											<span>
												{showAllCategories
													? "Hide empty categories"
													: `Manage categories (${hiddenCount} hidden)`}
											</span>
										</button>
									)}
								</>
							);
						})()
					)}
				</div>
			</aside>

			{/* Editor pane */}
			<main className="flex grow flex-col">
				{!selectedId && (
					<EmptyState
						isVaultEmpty={isEmpty}
						onNewNote={startNewNote}
						onOpenToday={today}
						onBrowseVault={() => setBrowseAll(true)}
					/>
				)}
				{selectedId && noteQuery.data && (
					<>
						<header className="flex items-center justify-between border-border border-b px-6 py-3">
							<div className="min-w-0">
								<h1 className="truncate font-[510] text-[15px] tracking-[-0.012em]">
									{activeFrontmatterTitle || noteQuery.data.name}
								</h1>
								<div className="mt-0.5 flex items-center gap-2 text-[12px] text-muted-foreground">
									<Badge variant="outline" className="font-normal">
										{noteQuery.data.vaultLabel}
									</Badge>
									<code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
										{noteQuery.data.relativePath}
									</code>
									<ProjectLinkPill
										team={team ?? ""}
										frontmatter={
											noteQuery.data.frontmatter as Record<
												string,
												unknown
											> | null
										}
									/>
								</div>
							</div>
							<div className="flex items-center gap-2">
								<AutoSaveIndicator state={autoSaveState} />
								<Button
									size="sm"
									variant="ghost"
									onClick={() =>
										handlePromote({
											id: noteQuery.data!.id,
											name: noteQuery.data!.name,
											relativePath: noteQuery.data!.relativePath,
											parentDir: noteQuery.data!.parentDir,
										})
									}
									disabled={promoteMut.isPending}
									title="Promote to Document"
								>
									<FileTextIcon className="size-3.5" /> Promote
								</Button>
								<Button
									size="sm"
									onClick={() =>
										updateMut.mutate({
											id: selectedId,
											content: draft,
											expectedSha: noteQuery.data!.fileSha,
										})
									}
									disabled={updateMut.isPending}
								>
									<SaveIcon className="size-3.5" />{" "}
									{updateMut.isPending ? "Saving…" : "Save"}
								</Button>
								<Button
									variant="ghost"
									size="sm"
									onClick={() => {
										if (
											confirm(
												`Delete "${noteQuery.data!.name}" from disk? This cannot be undone.`,
											)
										) {
											deleteMut.mutate({ id: selectedId });
										}
									}}
									className="text-muted-foreground hover:text-destructive"
								>
									<Trash2Icon className="size-3.5" />
								</Button>
							</div>
						</header>
						<div className="grow overflow-y-auto">
							<div className="mx-auto min-h-[320px] max-w-[740px] rounded-lg px-6 py-6 transition-shadow duration-150 focus-within:ring-1 focus-within:ring-border/60">
								<BlockEditor
									key={`${selectedId}:${noteQuery.data.fileSha}`}
									value={draft}
									onChange={(value) => {
										draftRef.current = value;
										setDraft(value);
										setAutoSaveState((s) => (s === "saving" ? s : "dirty"));
									}}
									onBlur={() => {
										// blur → debounced autoSave (knowledge.update)
										if (autoSaveTimer.current)
											clearTimeout(autoSaveTimer.current);
										autoSaveTimer.current = setTimeout(autoSave, 500);
									}}
								/>
							</div>
						</div>
						<div className="shrink-0 border-border border-t px-6 pb-4">
							{wikiLinks.length > 0 && (
								<div className="pt-3">
									<div className="mb-1.5 font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
										Links
									</div>
									<div className="flex flex-wrap gap-x-3 gap-y-1 text-[13px]">
										{wikiLinks.map((l) => (
											<WikiLinkInline
												key={l.key}
												text={l.text}
												toNoteId={l.toNoteId}
												onClick={() => l.toNoteId && setSelectedId(l.toNoteId)}
											/>
										))}
									</div>
								</div>
							)}
							<BacklinksPanel entityType="knowledge" entityId={selectedId} />
						</div>
					</>
				)}
			</main>
		</div>
	);
}

function EmptyState({
	isVaultEmpty,
	onNewNote,
	onOpenToday,
	onBrowseVault,
}: {
	isVaultEmpty: boolean;
	onNewNote: () => void;
	onOpenToday: () => void;
	onBrowseVault: () => void;
}) {
	return (
		<div className="flex grow flex-col items-center justify-center gap-6 px-6 text-center">
			<div className="flex flex-col items-center gap-2">
				<BrainIcon className="size-10 text-muted-foreground/60" />
				<p className="font-[510] text-[15px] tracking-[-0.012em]">
					{isVaultEmpty ? "Your vault is empty" : "Pick a note to edit"}
				</p>
				<p className="max-w-md text-balance text-[12px] text-muted-foreground">
					Markdown notes sync with your Obsidian vault at{" "}
					<code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
						/Users/john.keeney/nexus-knowledge
					</code>
					. Start with one of the actions below.
				</p>
			</div>
			<div className="grid w-full max-w-2xl gap-3 sm:grid-cols-3">
				<CtaCard
					icon={PlusIcon}
					title="New note"
					hint="Create a markdown file at any path"
					onClick={onNewNote}
				/>
				<CtaCard
					icon={CalendarIcon}
					title="Open today"
					hint="Jump into today's daily log"
					onClick={onOpenToday}
				/>
				<CtaCard
					icon={FolderTreeIcon}
					title="Browse vault"
					hint="Expand every folder on the left"
					onClick={onBrowseVault}
				/>
			</div>
		</div>
	);
}

// Header-right auto-save indicator (palette §3). Mirrors the autoSaveState
// machine: dirty → "Unsaved", saving → spinner + "Saving…", saved → check +
// "Saved" (fades after 2s), conflict → "Conflict — reloading".
function AutoSaveIndicator({
	state,
}: {
	state: "idle" | "dirty" | "saving" | "saved" | "conflict";
}) {
	if (state === "idle") return null;
	if (state === "dirty") {
		return (
			<span className="text-[11px] text-muted-foreground opacity-60">
				Unsaved
			</span>
		);
	}
	if (state === "saving") {
		return (
			<span className="flex items-center gap-1 text-[11px] text-muted-foreground">
				<RefreshCwIcon className="size-3 animate-spin" />
				Saving…
			</span>
		);
	}
	if (state === "conflict") {
		return (
			<span className="flex items-center gap-1 text-[11px] text-destructive">
				<AlertCircleIcon className="size-3" />
				Conflict — reloading
			</span>
		);
	}
	return (
		<span className="flex items-center gap-1 text-[11px] text-[var(--color-success)] opacity-100 transition-opacity duration-500">
			<CheckIcon className="size-3" />
			Saved
		</span>
	);
}

// Parse `[[wiki links]]` out of the raw note draft and resolve each against the
// vault note list by case-insensitive basename (Obsidian-style). Returns the
// resolved note id (or null when no matching note exists).
function parseWikiLinks(
	content: string,
	notes: NoteListItem[],
): Array<{ key: string; text: string; toNoteId: string | null }> {
	const byBasename = new Map<string, string>();
	for (const n of notes) {
		const base = n.relativePath
			.split("/")
			.pop()!
			.replace(/\.md$/i, "")
			.toLowerCase();
		if (!byBasename.has(base)) byBasename.set(base, n.id);
		const nameKey = n.name.toLowerCase();
		if (!byBasename.has(nameKey)) byBasename.set(nameKey, n.id);
	}
	const out: Array<{ key: string; text: string; toNoteId: string | null }> = [];
	const seen = new Set<string>();
	const matches = content.matchAll(/\[\[([^\]]+)\]\]/g);
	let i = 0;
	for (const match of matches) {
		const raw = match[1].split("|")[0].trim();
		const lower = raw.toLowerCase();
		i++;
		if (seen.has(lower)) continue;
		seen.add(lower);
		out.push({
			key: `${lower}#${i}`,
			text: raw,
			toNoteId: byBasename.get(lower) ?? null,
		});
	}
	return out;
}

function CtaCard({
	icon: Icon,
	title,
	hint,
	onClick,
}: {
	icon: any;
	title: string;
	hint: string;
	onClick: () => void;
}) {
	return (
		<button
			type="button"
			onClick={onClick}
			className="flex flex-col items-start gap-2 rounded-md border border-border bg-card p-4 text-left transition-colors hover:border-border/80 hover:bg-accent/40"
		>
			<Icon className="size-4 text-violet-500" />
			<div className="font-[510] text-[13px] tracking-[-0.005em]">{title}</div>
			<div className="text-[12px] text-muted-foreground">{hint}</div>
		</button>
	);
}

/**
 * "Linked to {project}" pill rendered above the editor when a knowledge
 * note's frontmatter carries a `project:` key. Clicking jumps to the
 * project's Knowledge tab. We resolve the project by name OR prefix —
 * matching the surfacing logic in ProjectKnowledgeView so the link is
 * always navigable.
 */
function ProjectLinkPill({
	team,
	frontmatter,
}: {
	team: string;
	frontmatter: Record<string, unknown> | null;
}) {
	const projectKey = useMemo(() => {
		if (!frontmatter) return null;
		const raw = frontmatter.project;
		if (typeof raw === "string" && raw.trim()) return raw.trim();
		if (Array.isArray(raw)) {
			const first = raw.find(
				(v) => typeof v === "string" && (v as string).trim().length > 0,
			);
			return typeof first === "string" ? first.trim() : null;
		}
		return null;
	}, [frontmatter]);

	const projectsQuery = useQuery({
		...trpc.projects.get.queryOptions({ pageSize: 100 } as any),
		enabled: !!projectKey && !!team,
	});

	const matched = useMemo(() => {
		if (!projectKey) return null;
		const data = projectsQuery.data as
			| { data?: Array<{ id: string; name: string; prefix?: string | null }> }
			| undefined;
		const list = data?.data ?? [];
		const k = projectKey.toLowerCase();
		return (
			list.find(
				(p) =>
					p.name.trim().toLowerCase() === k ||
					(p.prefix && p.prefix.trim().toLowerCase() === k),
			) ?? null
		);
	}, [projectKey, projectsQuery.data]);

	if (!projectKey) return null;

	const inner = (
		<>
			<FolderOpenIcon className="size-3" />
			Linked to {matched?.name ?? projectKey}
		</>
	);

	if (matched && team) {
		return (
			<Link
				href={`/team/${team}/projects/${matched.id}/knowledge`}
				className="inline-flex items-center gap-1 rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 font-[510] text-[11px] text-violet-600 transition-colors hover:border-violet-500/60 hover:bg-violet-500/15 dark:text-violet-300"
			>
				{inner}
			</Link>
		);
	}
	return (
		<span
			title="Project not found in this workspace"
			className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 font-[510] text-[11px] text-muted-foreground"
		>
			{inner}
		</span>
	);
}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import { Input } from "@ui/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { cn } from "@ui/lib/utils";
import {
	AlertCircleIcon,
	BookOpenIcon,
	BrainIcon,
	CalendarIcon,
	CheckIcon,
	FileTextIcon,
	FolderIcon,
	FolderTreeIcon,
	LayersIcon,
	LightbulbIcon,
	ListPlusIcon,
	type LucideIcon,
	PanelRightCloseIcon,
	PanelRightIcon,
	PencilLineIcon,
	PlusIcon,
	RefreshCwIcon,
	SaveIcon,
	SearchIcon,
	TagsIcon,
	Trash2Icon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { BacklinksPanel } from "@/components/backlinks/backlinks-panel";
import { BlockEditor } from "@/components/editor/block-editor";
import { WikiLinkInline } from "@/components/knowledge/wiki-link-inline";
import { useTaskParams } from "@/hooks/use-task-params";
import { trpc } from "@/utils/trpc";

type Category =
	| "all"
	| "daily"
	| "permanent"
	| "drafts"
	| "references"
	| "projects"
	| "ideas"
	| "other";
type ViewMode = "list" | "cards";
type UpdatedFilter = "all" | "week" | "month";
type InspectorMode = "preview" | "edit";
type SaveState = "idle" | "dirty" | "saving" | "saved" | "conflict";

type Frontmatter = Record<string, unknown>;

type NoteListItem = {
	id: string;
	name: string;
	relativePath: string;
	parentDir: string | null;
	updatedAt: string;
	frontmatter: Frontmatter | null;
};

type CategoryDef = {
	key: Exclude<Category, "all">;
	title: string;
	icon: LucideIcon;
	match: (path: string) => boolean;
};

const RESERVED_TOP_DIRS = new Set([
	"daily",
	"drafts",
	"references",
	"projects",
	"ideas",
]);

/** Option C layout — list rail width + inspector drawer persistence. */
const LIST_WIDTH_KEY = "nexus.notes.listWidth";
const INSPECTOR_OPEN_KEY = "nexus.notes.inspectorOpen";
const LIST_WIDTH_DEFAULT = 300;
const LIST_WIDTH_MIN = 220;
const LIST_WIDTH_MAX = 480;

const CATEGORY_DEFS: CategoryDef[] = [
	{
		key: "daily",
		title: "Daily",
		icon: CalendarIcon,
		match: (path) => /^daily\//i.test(path),
	},
	{
		key: "permanent",
		title: "Permanent",
		icon: LayersIcon,
		match: (path) => {
			const first = path.split("/")[0]?.toLowerCase() ?? "";
			if (!first) return false;
			if (!path.includes("/")) return true;
			return !RESERVED_TOP_DIRS.has(first);
		},
	},
	{
		key: "drafts",
		title: "Drafts",
		icon: PencilLineIcon,
		match: (path) => /^drafts\//i.test(path),
	},
	{
		key: "references",
		title: "References",
		icon: BookOpenIcon,
		match: (path) => /^references\//i.test(path),
	},
	{
		key: "projects",
		title: "Projects",
		icon: FolderIcon,
		match: (path) => /^projects\//i.test(path),
	},
	{
		key: "ideas",
		title: "Ideas",
		icon: LightbulbIcon,
		match: (path) => /^ideas\//i.test(path),
	},
	{
		key: "other",
		title: "Other",
		icon: FolderTreeIcon,
		match: () => true,
	},
];

function normalize(path: string): string {
	return path.replace(/\\+/g, "/");
}

function classify(
	note: Pick<NoteListItem, "relativePath">,
): CategoryDef["key"] {
	const path = normalize(note.relativePath);
	for (const category of CATEGORY_DEFS) {
		if (category.match(path)) return category.key;
	}
	return "other";
}

function formatDate(value?: string | Date | null): string {
	if (!value) return "No edits yet";
	return new Date(value).toLocaleDateString(undefined, {
		month: "short",
		day: "numeric",
		year:
			new Date(value).getFullYear() === new Date().getFullYear()
				? undefined
				: "numeric",
	});
}

function updatedFilterMatches(value: string | Date, filter: UpdatedFilter) {
	if (filter === "all") return true;
	const ts = new Date(value).getTime();
	if (!Number.isFinite(ts)) return false;
	const ageDays = (Date.now() - ts) / (24 * 60 * 60 * 1000);
	return filter === "week" ? ageDays < 7 : ageDays < 30;
}

function frontmatterString(
	frontmatter: Frontmatter | null | undefined,
	key: string,
): string | null {
	const value = frontmatter?.[key];
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

function frontmatterArray(
	frontmatter: Frontmatter | null | undefined,
	key: string,
): string[] {
	const value = frontmatter?.[key];
	if (Array.isArray(value)) {
		return value.filter((item): item is string => typeof item === "string");
	}
	if (typeof value === "string" && value.trim()) return [value.trim()];
	return [];
}

function displayTitle(note: NoteListItem): string {
	return (
		frontmatterString(note.frontmatter, "title") || note.name || "Untitled"
	);
}

function statusLabel(note: NoteListItem): string {
	const explicit = frontmatterString(note.frontmatter, "status");
	if (explicit) return explicit;
	const category = classify(note);
	if (category === "daily") return "daily";
	if (category === "drafts") return "draft";
	if (category === "references") return "reference";
	if (category === "ideas") return "idea";
	if (category === "projects") return "active";
	return "note";
}

function buildFrontmatterContent(
	frontmatter: Frontmatter | null,
	body: string,
): string {
	if (!frontmatter || Object.keys(frontmatter).length === 0) return body;
	const lines = ["---"];
	for (const [key, value] of Object.entries(frontmatter)) {
		if (Array.isArray(value)) {
			lines.push(
				`${key}: [${value.map((item) => JSON.stringify(item)).join(", ")}]`,
			);
		} else if (typeof value === "string") {
			lines.push(
				`${key}: ${/[:#]/.test(value) ? JSON.stringify(value) : value}`,
			);
		} else {
			lines.push(`${key}: ${value}`);
		}
	}
	lines.push("---", "", body);
	return lines.join("\n");
}

function parseWikiLinks(
	content: string,
	notes: NoteListItem[],
): Array<{ key: string; text: string; toNoteId: string | null }> {
	const byBasename = new Map<string, string>();
	for (const note of notes) {
		const base = note.relativePath
			.split("/")
			.pop()!
			.replace(/\.md$/i, "")
			.toLowerCase();
		if (!byBasename.has(base)) byBasename.set(base, note.id);
		const nameKey = note.name.toLowerCase();
		if (!byBasename.has(nameKey)) byBasename.set(nameKey, note.id);
		const title = frontmatterString(note.frontmatter, "title");
		if (title && !byBasename.has(title.toLowerCase())) {
			byBasename.set(title.toLowerCase(), note.id);
		}
	}

	const out: Array<{ key: string; text: string; toNoteId: string | null }> = [];
	const seen = new Set<string>();
	let index = 0;
	for (const match of content.matchAll(/\[\[([^\]]+)\]\]/g)) {
		const text = match[1].split("|")[0].trim();
		const key = text.toLowerCase();
		index++;
		if (!text || seen.has(key)) continue;
		seen.add(key);
		out.push({
			key: `${key}-${index}`,
			text,
			toNoteId: byBasename.get(key) ?? null,
		});
	}
	return out;
}

export function KnowledgeView() {
	const qc = useQueryClient();
	const searchParams = useSearchParams();
	const { setParams: setTaskParams } = useTaskParams();
	const initialNoteId = searchParams?.get("note") ?? null;
	const [search, setSearch] = useState("");
	const [category, setCategory] = useState<Category>("all");
	const [status, setStatus] = useState("all");
	const [updatedFilter, setUpdatedFilter] = useState<UpdatedFilter>("all");
	const [viewMode] = useState<ViewMode>("list");
	const [selectedId, setSelectedId] = useState<string | null>(initialNoteId);
	const [inspectorMode, setInspectorMode] = useState<InspectorMode>("preview");
	const [showNew, setShowNew] = useState(false);
	const [newPath, setNewPath] = useState("");
	const [draft, setDraft] = useState("");
	const [saveState, setSaveState] = useState<SaveState>("idle");
	const [listWidth, setListWidth] = useState(LIST_WIDTH_DEFAULT);
	const [inspectorOpen, setInspectorOpen] = useState(true);
	const draftRef = useRef("");
	const shaRef = useRef<string | null>(null);
	const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const saveInFlight = useRef(false);
	const dragRef = useRef<{ startX: number; startW: number } | null>(null);

	useEffect(() => {
		try {
			const w = Number(localStorage.getItem(LIST_WIDTH_KEY));
			if (Number.isFinite(w) && w >= LIST_WIDTH_MIN && w <= LIST_WIDTH_MAX) {
				setListWidth(w);
			}
			const open = localStorage.getItem(INSPECTOR_OPEN_KEY);
			if (open === "0") setInspectorOpen(false);
		} catch {
			/* ignore */
		}
	}, []);

	useEffect(() => {
		try {
			localStorage.setItem(LIST_WIDTH_KEY, String(listWidth));
		} catch {
			/* ignore */
		}
	}, [listWidth]);

	useEffect(() => {
		try {
			localStorage.setItem(INSPECTOR_OPEN_KEY, inspectorOpen ? "1" : "0");
		} catch {
			/* ignore */
		}
	}, [inspectorOpen]);

	const onListResizeStart = (e: {
		preventDefault: () => void;
		clientX: number;
		pointerId: number;
		currentTarget: EventTarget & HTMLDivElement;
	}) => {
		e.preventDefault();
		dragRef.current = { startX: e.clientX, startW: listWidth };
		e.currentTarget.setPointerCapture(e.pointerId);
	};
	const onListResizeMove = (e: { clientX: number }) => {
		if (!dragRef.current) return;
		const dx = e.clientX - dragRef.current.startX;
		const next = Math.min(
			LIST_WIDTH_MAX,
			Math.max(LIST_WIDTH_MIN, dragRef.current.startW + dx),
		);
		setListWidth(next);
	};
	const onListResizeEnd = () => {
		dragRef.current = null;
	};

	const listInput = useMemo(
		() => ({
			...(search.trim() ? { search: search.trim() } : {}),
		}),
		[search],
	);
	const listQuery = useQuery({
		...trpc.knowledge.get.queryOptions(listInput),
		enabled: true,
	});
	const notes = (listQuery.data?.notes ?? []) as NoteListItem[];
	const selectedNote = notes.find((note) => note.id === selectedId) ?? null;

	useEffect(() => {
		if (!selectedId && notes.length > 0) {
			setSelectedId(notes[0]!.id);
		}
	}, [notes, selectedId]);

	const noteQuery = useQuery({
		...trpc.knowledge.getById.queryOptions({ id: selectedId ?? "" }),
		enabled: !!selectedId,
	});

	const refetchList = useCallback(() => {
		void qc.invalidateQueries({ queryKey: trpc.knowledge.get.queryKey() });
	}, [qc]);
	const refetchNote = useCallback(() => {
		void qc.invalidateQueries({ queryKey: trpc.knowledge.getById.queryKey() });
	}, [qc]);

	useEffect(() => {
		if (!noteQuery.data) return;
		const next = buildFrontmatterContent(
			(noteQuery.data.frontmatter as Frontmatter | null) ?? null,
			noteQuery.data.content ?? "",
		);
		setDraft(next);
		draftRef.current = next;
		shaRef.current = noteQuery.data.fileSha;
		setSaveState("idle");
	}, [noteQuery.data?.id, noteQuery.data?.fileSha, noteQuery.data]);

	const updateMut = useMutation(
		trpc.knowledge.update.mutationOptions({
			onSuccess: () => {
				refetchList();
				refetchNote();
			},
			onError: (error) => toast.error(error.message),
		}),
	);
	const updateAsync = updateMut.mutateAsync;
	const autoSave = useCallback(async () => {
		if (!selectedId || saveInFlight.current) return;
		saveInFlight.current = true;
		const content = draftRef.current;
		setSaveState("saving");
		try {
			await updateAsync({
				id: selectedId,
				content,
				expectedSha: shaRef.current ?? "",
			});
			setSaveState("saved");
			refetchList();
			refetchNote();
		} catch (error) {
			const isConflict =
				error instanceof Error && /CONFLICT/i.test(error.message);
			if (!isConflict) {
				setSaveState("idle");
				toast.error(error instanceof Error ? error.message : "Save failed");
				return;
			}
			setSaveState("conflict");
			const fresh = await qc.fetchQuery(
				trpc.knowledge.getById.queryOptions({ id: selectedId }),
			);
			shaRef.current = fresh.fileSha;
			await updateAsync({
				id: selectedId,
				content: draftRef.current,
				expectedSha: fresh.fileSha,
			});
			setSaveState("saved");
			refetchList();
			refetchNote();
		} finally {
			saveInFlight.current = false;
			if (draftRef.current !== content) {
				setSaveState("dirty");
				if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
				autoSaveTimer.current = setTimeout(() => {
					void autoSave();
				}, 500);
			}
		}
	}, [selectedId, updateAsync, qc, refetchList, refetchNote]);

	useEffect(() => {
		if (saveState !== "saved") return;
		const timeout = setTimeout(() => setSaveState("idle"), 2000);
		return () => clearTimeout(timeout);
	}, [saveState]);

	useEffect(
		() => () => {
			if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
		},
		[],
	);

	const scanMut = useMutation(
		trpc.knowledge.scan.mutationOptions({
			onSuccess: (data) => {
				const total = data.results.reduce(
					(count, result) =>
						count + result.inserted + result.updated + result.deleted,
					0,
				);
				toast.success(
					total === 0
						? "Vault is up to date"
						: `Re-scanned (${total} change${total === 1 ? "" : "s"})`,
				);
				refetchList();
				refetchNote();
			},
			onError: (error) => toast.error(error.message),
		}),
	);

	const createMut = useMutation(
		trpc.knowledge.create.mutationOptions({
			onSuccess: (note) => {
				toast.success(`Created ${note.relativePath}`);
				setShowNew(false);
				setNewPath("");
				setSelectedId(note.id);
				setInspectorMode("edit");
				refetchList();
			},
			onError: (error) => toast.error(error.message),
		}),
	);

	const deleteMut = useMutation(
		trpc.knowledge.delete.mutationOptions({
			onSuccess: () => {
				toast.success("Deleted note");
				setSelectedId(null);
				refetchList();
			},
			onError: (error) => toast.error(error.message),
		}),
	);

	const promoteMut = useMutation(
		trpc.documents.create.mutationOptions({
			onSuccess: (doc) => {
				toast.success(`Promoted to document: ${doc.name}`);
				void qc.invalidateQueries({ queryKey: [["documents", "get"]] });
			},
			onError: (error) => toast.error(error.message),
		}),
	);

	const statusOptions = useMemo(() => {
		const values = new Set<string>();
		for (const note of notes) values.add(statusLabel(note));
		return Array.from(values).sort();
	}, [notes]);

	const filteredNotes = useMemo(() => {
		return notes.filter((note) => {
			if (category !== "all" && classify(note) !== category) return false;
			if (status !== "all" && statusLabel(note) !== status) return false;
			return updatedFilterMatches(note.updatedAt, updatedFilter);
		});
	}, [notes, category, status, updatedFilter]);

	const groupedNotes = useMemo(() => {
		const groups = new Map<CategoryDef["key"], NoteListItem[]>();
		for (const def of CATEGORY_DEFS) groups.set(def.key, []);
		for (const note of filteredNotes) groups.get(classify(note))!.push(note);
		for (const [key, group] of groups) {
			group.sort((a, b) =>
				key === "daily"
					? b.relativePath.localeCompare(a.relativePath)
					: a.relativePath.localeCompare(b.relativePath),
			);
		}
		return groups;
	}, [filteredNotes]);

	const selectedContent = draft || noteQuery.data?.content || "";
	const wikiLinks = useMemo(
		() => (selectedId ? parseWikiLinks(selectedContent, notes) : []),
		[selectedId, selectedContent, notes],
	);

	const tags = selectedNote
		? frontmatterArray(selectedNote.frontmatter, "tags")
		: [];
	const selectedStatus = selectedNote ? statusLabel(selectedNote) : null;

	const createToday = () => {
		const now = new Date();
		const y = now.getFullYear();
		const m = String(now.getMonth() + 1).padStart(2, "0");
		const d = String(now.getDate()).padStart(2, "0");
		const path = `daily/${y}-${m}-${d}`;
		const existing = notes.find((note) => note.relativePath === `${path}.md`);
		if (existing) {
			setSelectedId(existing.id);
			setInspectorMode("edit");
			return;
		}
		createMut.mutate({
			relativePath: path,
			content: `---\ntitle: ${y}-${m}-${d} Daily Log\nstatus: daily\ntags: [daily]\n---\n\n# ${y}-${m}-${d} Daily Log\n\n## Notes\n\n## Links\n\n`,
		});
	};

	const createNote = () => {
		const path = newPath.trim();
		if (!path) return;
		createMut.mutate({ relativePath: path });
	};

	const saveNow = () => {
		if (!selectedId || !noteQuery.data) return;
		updateMut.mutate({
			id: selectedId,
			content: draft,
			expectedSha: noteQuery.data.fileSha,
		});
		setSaveState("saved");
	};

	const promoteSelected = async () => {
		if (!selectedNote || !noteQuery.data) return;
		await promoteMut.mutateAsync({
			name: displayTitle(selectedNote),
			content: noteQuery.data.content ?? "",
		});
	};

	// FEAT-006 item 5 — "convert note to task": seeds the create-task dialog
	// with the note's title, same non-destructive pattern as "convert task to
	// project" — the note stays put so it's still there as reference.
	const convertSelectedToTask = () => {
		if (!selectedNote) return;
		setTaskParams({ createTask: true, taskTitle: displayTitle(selectedNote) });
	};

	const onDraftChange = (value: string) => {
		draftRef.current = value;
		setDraft(value);
		setSaveState((state) => (state === "saving" ? state : "dirty"));
	};
	const onEditorBlur = () => {
		if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
		autoSaveTimer.current = setTimeout(autoSave, 500);
	};
	const onDeleteSelected = () => {
		if (!selectedId || !selectedNote) return;
		if (
			confirm(
				`Delete "${displayTitle(selectedNote)}" from disk? This cannot be undone.`,
			)
		) {
			deleteMut.mutate({ id: selectedId });
		}
	};

	return (
		<div className="flex h-full flex-col">
			{/* Slim chrome — Option C: editor-primary + collapsible inspector */}
			<header className="flex h-11 shrink-0 items-center justify-between gap-3 border-border border-b px-3">
				<div className="flex min-w-0 items-center gap-2">
					<h1 className="font-[510] text-[14px] tracking-[-0.015em]">Notes</h1>
					<span className="hidden text-[11px] text-muted-foreground sm:inline">
						{filteredNotes.length} note{filteredNotes.length === 1 ? "" : "s"}
					</span>
				</div>
				<div className="flex items-center gap-1.5">
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={() => scanMut.mutate({})}
						disabled={scanMut.isPending}
					>
						<RefreshCwIcon
							className={cn("size-3.5", scanMut.isPending && "animate-spin")}
						/>
						<span className="hidden sm:inline">
							{scanMut.isPending ? "Scanning…" : "Re-scan"}
						</span>
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						asChild
					>
						<a href="zennotes://open" title="Open vault in ZenNotes">
							<BookOpenIcon className="size-3.5" />
							<span className="hidden md:inline">ZenNotes</span>
						</a>
					</Button>
					<Button
						variant="ghost"
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={createToday}
					>
						<CalendarIcon className="size-3.5" />
						<span className="hidden sm:inline">Today</span>
					</Button>
					<Button
						variant={inspectorOpen ? "secondary" : "ghost"}
						size="sm"
						className="h-7 px-2 text-[12px]"
						onClick={() => setInspectorOpen((v) => !v)}
						title={inspectorOpen ? "Hide inspector" : "Show inspector"}
					>
						{inspectorOpen ? (
							<PanelRightCloseIcon className="size-3.5" />
						) : (
							<PanelRightIcon className="size-3.5" />
						)}
						<span className="hidden sm:inline">Inspector</span>
					</Button>
					<Button
						size="sm"
						className="h-7 px-2.5 text-[12px]"
						onClick={() => setShowNew((v) => !v)}
					>
						<PlusIcon className="size-3.5" />
						New
					</Button>
				</div>
			</header>

			{showNew ? (
				<div className="flex items-center gap-2 border-border border-b px-3 py-2">
					<Input
						value={newPath}
						onChange={(event) => setNewPath(event.target.value)}
						onKeyDown={(event) => {
							if (event.key === "Enter") createNote();
							if (event.key === "Escape") setShowNew(false);
						}}
						placeholder="projects/{projectId}/new-note"
						className="h-8 max-w-md font-mono text-[12px]"
						// biome-ignore lint/a11y/noAutofocus: path entry
						autoFocus
					/>
					<Button
						size="sm"
						className="h-8"
						onClick={createNote}
						disabled={!newPath.trim()}
					>
						Create
					</Button>
					<span className="text-[11px] text-muted-foreground">
						Prefer{" "}
						<code className="text-[10px]">projects/&#123;projectId&#125;/</code>
					</span>
				</div>
			) : null}

			<div className="flex min-h-0 grow">
				{/* Compact list rail */}
				<aside
					className="flex shrink-0 flex-col border-border border-r bg-background"
					style={{ width: listWidth }}
				>
					<div className="space-y-2 border-border border-b p-2.5">
						<div className="relative">
							<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-3.5 text-muted-foreground" />
							<Input
								value={search}
								onChange={(event) => setSearch(event.target.value)}
								placeholder="Search notes…"
								className="h-8 pl-7 text-[12px]"
							/>
						</div>
						<div className="flex flex-wrap gap-1.5">
							<Select
								value={category}
								onValueChange={(value) => setCategory(value as Category)}
							>
								<SelectTrigger className="h-7 w-full text-[11px]">
									<FolderTreeIcon className="mr-1 size-3 text-muted-foreground" />
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="all">All categories</SelectItem>
									{CATEGORY_DEFS.map((def) => (
										<SelectItem key={def.key} value={def.key}>
											{def.title}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							<Select value={status} onValueChange={setStatus}>
								<SelectTrigger className="h-7 min-w-0 flex-1 text-[11px]">
									<SelectValue placeholder="Status" />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="all">All statuses</SelectItem>
									{statusOptions.map((option) => (
										<SelectItem key={option} value={option}>
											{option}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							<Select
								value={updatedFilter}
								onValueChange={(value) =>
									setUpdatedFilter(value as UpdatedFilter)
								}
							>
								<SelectTrigger className="h-7 min-w-0 flex-1 text-[11px]">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="all">Any time</SelectItem>
									<SelectItem value="week">This week</SelectItem>
									<SelectItem value="month">This month</SelectItem>
								</SelectContent>
							</Select>
						</div>
					</div>
					<div className="min-h-0 flex-1 overflow-y-auto p-1.5">
						{listQuery.isLoading ? (
							<div className="p-3 text-[12px] text-muted-foreground">
								Loading…
							</div>
						) : filteredNotes.length === 0 ? (
							<div className="p-3 text-center text-[12px] text-muted-foreground">
								{notes.length === 0
									? "Vault empty — create a note."
									: "No matches."}
							</div>
						) : (
							<div className="space-y-1">
								{CATEGORY_DEFS.map((def) => {
									const group = groupedNotes.get(def.key) ?? [];
									if (group.length === 0) return null;
									return (
										<NoteSection
											key={def.key}
											def={def}
											notes={group}
											selectedId={selectedId}
											viewMode={viewMode}
											onSelect={(id) => {
												setSelectedId(id);
												setInspectorMode("preview");
											}}
										/>
									);
								})}
							</div>
						)}
					</div>
				</aside>

				{/* Resize gutter */}
				<div
					role="separator"
					aria-orientation="vertical"
					aria-label="Resize note list"
					tabIndex={0}
					onPointerDown={onListResizeStart}
					onPointerMove={onListResizeMove}
					onPointerUp={onListResizeEnd}
					onPointerCancel={onListResizeEnd}
					className="group relative w-1.5 shrink-0 cursor-col-resize bg-transparent hover:bg-primary/30 active:bg-primary/50"
				>
					<span className="-translate-x-1/2 absolute inset-y-0 left-1/2 w-px bg-border group-hover:bg-primary/60 group-active:bg-primary" />
				</div>

				{/* Primary editor surface */}
				<section className="flex min-w-0 flex-1 flex-col bg-background">
					{!selectedNote &&
					filteredNotes.length === 0 &&
					!listQuery.isLoading ? (
						<EmptyState
							hasNotes={notes.length > 0}
							onNew={() => setShowNew(true)}
							onToday={createToday}
						/>
					) : (
						<NoteEditor
							note={selectedNote}
							noteDetail={noteQuery.data ?? null}
							draft={draft}
							mode={inspectorMode}
							saveState={saveState}
							wikiLinks={wikiLinks}
							isSaving={updateMut.isPending}
							onModeChange={setInspectorMode}
							onDraftChange={onDraftChange}
							onBlur={onEditorBlur}
							onSave={saveNow}
							onOpenLinkedNote={setSelectedId}
						/>
					)}
				</section>

				{/* Collapsible properties inspector */}
				{inspectorOpen ? (
					<aside className="hidden w-[260px] shrink-0 flex-col border-border border-l bg-card/25 md:flex">
						<NoteInspectorRail
							note={selectedNote}
							noteDetail={noteQuery.data ?? null}
							tags={tags}
							status={selectedStatus}
							isPromoting={promoteMut.isPending}
							onPromote={promoteSelected}
							onConvertToTask={convertSelectedToTask}
							onDelete={onDeleteSelected}
							onClose={() => setInspectorOpen(false)}
						/>
					</aside>
				) : null}
			</div>
		</div>
	);
}

function NoteSection({
	def,
	notes,
	selectedId,
	onSelect,
}: {
	def: CategoryDef;
	notes: NoteListItem[];
	selectedId: string | null;
	viewMode?: ViewMode;
	onSelect: (id: string) => void;
}) {
	const Icon = def.icon;
	return (
		<section>
			<div className="mb-1 flex items-center gap-1.5 px-2 pt-2 text-[10px] text-muted-foreground uppercase tracking-[0.06em]">
				<Icon className="size-3" />
				<span className="font-[510]">{def.title}</span>
				<span className="rounded-full border border-border/60 px-1.5 text-[9.5px] tabular-nums">
					{notes.length}
				</span>
			</div>
			<ul className="space-y-0.5">
				{notes.map((note) => (
					<li key={note.id}>
						<NoteRow
							note={note}
							selected={selectedId === note.id}
							onSelect={onSelect}
						/>
					</li>
				))}
			</ul>
		</section>
	);
}

function NoteRow({
	note,
	selected,
	onSelect,
}: {
	note: NoteListItem;
	selected: boolean;
	onSelect: (id: string) => void;
}) {
	const title = displayTitle(note);
	const tags = frontmatterArray(note.frontmatter, "tags");
	return (
		<button
			type="button"
			onClick={() => onSelect(note.id)}
			className={cn(
				"flex w-full flex-col gap-0.5 rounded-md border border-transparent px-2.5 py-2 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/40",
				selected && "border-primary/40 bg-primary/10",
			)}
		>
			<span className="truncate font-[510] text-[12.5px] text-foreground tracking-[-0.005em]">
				{title}
			</span>
			<span className="truncate font-mono text-[10px] text-muted-foreground/80">
				{note.relativePath}
			</span>
			{(tags.length > 0 || statusLabel(note)) && (
				<span className="mt-0.5 flex flex-wrap gap-1">
					{tags.slice(0, 3).map((tag) => (
						<span
							key={tag}
							className="max-w-[5.5rem] truncate rounded-full border border-border/60 px-1.5 py-px text-[9.5px] text-muted-foreground"
						>
							{tag}
						</span>
					))}
				</span>
			)}
		</button>
	);
}

function NoteCard({
	note,
	selected,
	onSelect,
}: {
	note: NoteListItem;
	selected: boolean;
	onSelect: (id: string) => void;
}) {
	// Kept for viewMode===cards compatibility (rail defaults to list).
	return <NoteRow note={note} selected={selected} onSelect={onSelect} />;
}

/**
 * Primary writing surface (Option C center pane).
 * Full-width preview/edit — meta lives in the inspector rail.
 */
function NoteEditor({
	note,
	noteDetail,
	draft,
	mode,
	saveState,
	wikiLinks,
	isSaving,
	onModeChange,
	onDraftChange,
	onBlur,
	onSave,
	onOpenLinkedNote,
}: {
	note: NoteListItem | null;
	// biome-ignore lint/suspicious/noExplicitAny: tRPC detail shape varies
	noteDetail: any;
	draft: string;
	mode: InspectorMode;
	saveState: SaveState;
	wikiLinks: Array<{ key: string; text: string; toNoteId: string | null }>;
	isSaving: boolean;
	onModeChange: (mode: InspectorMode) => void;
	onDraftChange: (value: string) => void;
	onBlur: () => void;
	onSave: () => void;
	onOpenLinkedNote: (id: string) => void;
}) {
	if (!note) {
		return (
			<div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
				<BrainIcon className="size-8 text-muted-foreground/50" />
				<p className="text-[13px] text-muted-foreground">
					Select a note from the list to read or edit.
				</p>
			</div>
		);
	}

	const title = displayTitle(note);
	const body = noteDetail?.content ?? "";

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="flex shrink-0 items-center justify-between gap-3 border-border border-b px-4 py-2.5">
				<div className="min-w-0">
					<h2 className="truncate font-[510] text-[#f6f6f8] text-[18px] tracking-[-0.025em]">
						{title}
					</h2>
					<p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
						{note.relativePath}
					</p>
				</div>
				<div className="flex shrink-0 items-center gap-2">
					<div className="inline-flex h-7 rounded-md border border-border bg-muted/40 p-0.5">
						{(["preview", "edit"] as const).map((nextMode) => (
							<button
								key={nextMode}
								type="button"
								onClick={() => onModeChange(nextMode)}
								className={cn(
									"inline-flex h-6 items-center rounded-[5px] px-2.5 font-[510] text-[11px] capitalize transition-colors",
									mode === nextMode
										? "bg-background text-foreground shadow-sm"
										: "text-muted-foreground hover:text-foreground",
								)}
							>
								{nextMode}
							</button>
						))}
					</div>
					<AutoSaveIndicator state={saveState} />
					<Button
						size="sm"
						className="h-7"
						onClick={onSave}
						disabled={isSaving || !noteDetail}
					>
						<SaveIcon className="size-3.5" />
						{isSaving ? "Saving…" : "Save"}
					</Button>
				</div>
			</div>

			<div className="min-h-0 flex-1 overflow-y-auto">
				<div className="mx-auto w-full max-w-[46rem] px-6 py-6">
					{mode === "edit" ? (
						<BlockEditor
							key={`${note.id}:${noteDetail?.fileSha ?? "pending"}`}
							value={draft}
							onChange={onDraftChange}
							onBlur={onBlur}
							className="editor-xl [&_.tiptap]:min-h-[min(60vh,520px)]"
						/>
					) : (
						<div className="space-y-5">
							{body.trim() ? (
								<pre className="whitespace-pre-wrap break-words font-sans text-[13.5px] text-foreground/90 leading-[1.65]">
									{body}
								</pre>
							) : (
								<p className="text-[13px] text-muted-foreground">
									No content yet. Switch to Edit.
								</p>
							)}
							{wikiLinks.length > 0 ? (
								<div>
									<div className="mb-1.5 font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
										Wiki links
									</div>
									<div className="flex flex-wrap gap-x-3 gap-y-1 text-[13px]">
										{wikiLinks.map((link) => (
											<WikiLinkInline
												key={link.key}
												text={link.text}
												toNoteId={link.toNoteId}
												onClick={() =>
													link.toNoteId && onOpenLinkedNote(link.toNoteId)
												}
											/>
										))}
									</div>
								</div>
							) : null}
						</div>
					)}
				</div>
			</div>
		</div>
	);
}

/**
 * Right drawer — properties, tags, backlinks, promote/delete.
 * Collapsible via header Inspector toggle.
 */
function NoteInspectorRail({
	note,
	noteDetail,
	tags,
	status,
	isPromoting,
	onPromote,
	onConvertToTask,
	onDelete,
	onClose,
}: {
	note: NoteListItem | null;
	// biome-ignore lint/suspicious/noExplicitAny: tRPC detail shape varies
	noteDetail: any;
	tags: string[];
	status: string | null;
	isPromoting: boolean;
	onPromote: () => void;
	onConvertToTask: () => void;
	onDelete: () => void;
	onClose: () => void;
}) {
	if (!note) {
		return (
			<div className="flex h-full flex-col p-3">
				<div className="mb-2 flex items-center justify-between">
					<span className="font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
						Inspector
					</span>
					<button
						type="button"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground"
						aria-label="Close inspector"
					>
						<PanelRightCloseIcon className="size-3.5" />
					</button>
				</div>
				<p className="text-[12px] text-muted-foreground">Select a note.</p>
			</div>
		);
	}

	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="flex items-center justify-between border-border border-b px-3 py-2.5">
				<span className="font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
					Inspector
				</span>
				<button
					type="button"
					onClick={onClose}
					className="text-muted-foreground hover:text-foreground"
					aria-label="Close inspector"
				>
					<PanelRightCloseIcon className="size-3.5" />
				</button>
			</div>
			<div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
				<div>
					<div className="font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
						Vault
					</div>
					<div className="mt-1 font-[510] text-[12.5px]">
						{noteDetail?.vaultLabel ?? "Personal Notes"}
					</div>
				</div>
				<div>
					<div className="font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
						Updated
					</div>
					<div className="mt-1 text-[12.5px]">{formatDate(note.updatedAt)}</div>
				</div>
				<div>
					<div className="font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
						Path
					</div>
					<div className="mt-1 break-all font-mono text-[10.5px] text-muted-foreground">
						{note.relativePath}
					</div>
				</div>
				{(status || tags.length > 0) && (
					<div>
						<div className="font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
							Tags
						</div>
						<div className="mt-1.5 flex flex-wrap gap-1">
							{status ? (
								<Badge
									variant="outline"
									className="h-[18px] px-1.5 font-normal text-[10px]"
								>
									{status}
								</Badge>
							) : null}
							{tags.map((tag) => (
								<span
									key={tag}
									className="rounded-full border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground"
								>
									{tag}
								</span>
							))}
						</div>
					</div>
				)}
				<div>
					<div className="mb-1.5 font-[510] text-[10px] text-muted-foreground uppercase tracking-wider">
						Backlinks
					</div>
					<BacklinksPanel entityType="knowledge" entityId={note.id} />
				</div>
			</div>
			<div className="space-y-1.5 border-border border-t p-3">
				<Button
					size="sm"
					className="h-8 w-full"
					onClick={onPromote}
					disabled={isPromoting || !noteDetail}
				>
					<FileTextIcon className="size-3.5" />
					{isPromoting ? "Promoting…" : "Promote to doc"}
				</Button>
				<Button
					size="sm"
					variant="outline"
					className="h-8 w-full"
					onClick={onConvertToTask}
				>
					<ListPlusIcon className="size-3.5" />
					Convert to task
				</Button>
				<Button
					size="sm"
					variant="ghost"
					className="h-8 w-full text-muted-foreground hover:text-destructive"
					onClick={onDelete}
				>
					<Trash2Icon className="size-3.5" />
					Delete
				</Button>
			</div>
		</div>
	);
}

function EmptyState({
	hasNotes,
	onNew,
	onToday,
}: {
	hasNotes: boolean;
	onNew: () => void;
	onToday: () => void;
}) {
	return (
		<div className="flex min-h-[420px] flex-col items-center justify-center gap-6 px-6 text-center">
			<div className="flex flex-col items-center gap-2">
				<BrainIcon className="size-10 text-muted-foreground/60" />
				<p className="font-[510] text-[15px] tracking-[-0.012em]">
					{hasNotes
						? "No notes match these filters"
						: "Your notes vault is empty"}
				</p>
				<p className="max-w-md text-balance text-[12px] text-muted-foreground">
					Notes are markdown files on disk. Prefer{" "}
					<code className="text-[11px]">projects/&#123;projectId&#125;/</code>{" "}
					paths. Filter by vault category, status, or updated date, then inspect
					and edit the note in the right pane.
				</p>
			</div>
			<div className="grid w-full max-w-xl gap-3 sm:grid-cols-2">
				<CtaCard
					icon={PlusIcon}
					title="New note"
					hint="Create a markdown note at any path"
					onClick={onNew}
				/>
				<CtaCard
					icon={CalendarIcon}
					title="Open today"
					hint="Jump into today's daily log"
					onClick={onToday}
				/>
			</div>
		</div>
	);
}

function CtaCard({
	icon: Icon,
	title,
	hint,
	onClick,
}: {
	icon: LucideIcon;
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
			<Icon className="size-4 text-cyan-500" />
			<div className="font-[510] text-[13px] tracking-[-0.005em]">{title}</div>
			<div className="text-[12px] text-muted-foreground">{hint}</div>
		</button>
	);
}

function AutoSaveIndicator({ state }: { state: SaveState }) {
	if (state === "idle") return null;
	if (state === "dirty") {
		return (
			<span className="mr-auto text-[11px] text-muted-foreground opacity-60">
				Unsaved
			</span>
		);
	}
	if (state === "saving") {
		return (
			<span className="mr-auto flex items-center gap-1 text-[11px] text-muted-foreground">
				<RefreshCwIcon className="size-3 animate-spin" />
				Saving...
			</span>
		);
	}
	if (state === "conflict") {
		return (
			<span className="mr-auto flex items-center gap-1 text-[11px] text-destructive">
				<AlertCircleIcon className="size-3" />
				Conflict
			</span>
		);
	}
	return (
		<span className="mr-auto flex items-center gap-1 text-[11px] text-[var(--color-success)]">
			<CheckIcon className="size-3" />
			Saved
		</span>
	);
}

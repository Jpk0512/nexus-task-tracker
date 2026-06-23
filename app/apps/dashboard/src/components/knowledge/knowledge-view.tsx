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
	type LucideIcon,
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
	const initialNoteId = searchParams?.get("note") ?? null;
	const [search, setSearch] = useState("");
	const [category, setCategory] = useState<Category>("all");
	const [status, setStatus] = useState("all");
	const [updatedFilter, setUpdatedFilter] = useState<UpdatedFilter>("all");
	const [viewMode, setViewMode] = useState<ViewMode>("list");
	const [selectedId, setSelectedId] = useState<string | null>(initialNoteId);
	const [inspectorMode, setInspectorMode] = useState<InspectorMode>("preview");
	const [showNew, setShowNew] = useState(false);
	const [newPath, setNewPath] = useState("");
	const [draft, setDraft] = useState("");
	const [saveState, setSaveState] = useState<SaveState>("idle");
	const draftRef = useRef("");
	const shaRef = useRef<string | null>(null);
	const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const saveInFlight = useRef(false);

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

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-4">
				<div className="flex flex-wrap items-start justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[18px] text-foreground tracking-[-0.012em]">
							Knowledge
						</h1>
						<p className="mt-1 max-w-2xl text-[12px] text-muted-foreground">
							Obsidian-compatible markdown notes, organized by vault path,
							frontmatter, and wiki-link relationships.
						</p>
					</div>
					<div className="flex items-center gap-2">
						<Button
							variant="outline"
							size="sm"
							onClick={() => scanMut.mutate({})}
							disabled={scanMut.isPending}
						>
							<RefreshCwIcon
								className={cn("size-3.5", scanMut.isPending && "animate-spin")}
							/>
							{scanMut.isPending ? "Scanning..." : "Re-scan"}
						</Button>
						<Button variant="outline" size="sm" onClick={createToday}>
							<CalendarIcon className="size-3.5" />
							Today
						</Button>
						<Button size="sm" onClick={() => setShowNew((value) => !value)}>
							<PlusIcon className="size-3.5" />
							New
						</Button>
					</div>
				</div>

				{showNew && (
					<div className="mt-4 flex max-w-xl gap-2">
						<Input
							value={newPath}
							onChange={(event) => setNewPath(event.target.value)}
							onKeyDown={(event) => {
								if (event.key === "Enter") createNote();
								if (event.key === "Escape") setShowNew(false);
							}}
							placeholder="projects/nexus/new-note"
							className="h-8 text-[12px]"
						/>
						<Button size="sm" onClick={createNote} disabled={!newPath.trim()}>
							Create
						</Button>
					</div>
				)}

				<div className="mt-4 flex flex-wrap items-center gap-2">
					<div className="relative mr-1 w-full max-w-sm sm:w-72">
						<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2.5 size-3.5 text-muted-foreground" />
						<Input
							value={search}
							onChange={(event) => setSearch(event.target.value)}
							placeholder="Search notes..."
							className="h-8 pl-8 text-[12px]"
						/>
					</div>
					<Select
						value={category}
						onValueChange={(value) => setCategory(value as Category)}
					>
						<SelectTrigger className="h-8 w-[172px] text-[12px]">
							<FolderTreeIcon className="mr-1.5 size-3.5 text-muted-foreground" />
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
						<SelectTrigger className="h-8 w-[152px] text-[12px]">
							<TagsIcon className="mr-1.5 size-3.5 text-muted-foreground" />
							<SelectValue />
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
						onValueChange={(value) => setUpdatedFilter(value as UpdatedFilter)}
					>
						<SelectTrigger className="h-8 w-[138px] text-[12px]">
							<CalendarIcon className="mr-1.5 size-3.5 text-muted-foreground" />
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">Any time</SelectItem>
							<SelectItem value="week">This week</SelectItem>
							<SelectItem value="month">This month</SelectItem>
						</SelectContent>
					</Select>
					<div className="inline-flex h-8 rounded-md border border-border bg-muted/40 p-0.5">
						{(["list", "cards"] as const).map((mode) => (
							<button
								key={mode}
								type="button"
								onClick={() => setViewMode(mode)}
								className={cn(
									"inline-flex h-7 items-center rounded-[5px] px-3 font-[510] text-[12px] capitalize transition-colors",
									viewMode === mode
										? "bg-background text-foreground shadow-sm"
										: "text-muted-foreground hover:text-foreground",
								)}
							>
								{mode}
							</button>
						))}
					</div>
				</div>
			</header>

			<div className="flex min-h-0 grow">
				<section className="min-w-0 grow overflow-y-auto p-6">
					{listQuery.isLoading ? (
						<div className="text-[12px] text-muted-foreground">Loading...</div>
					) : filteredNotes.length === 0 ? (
						<EmptyState
							hasNotes={notes.length > 0}
							onNew={() => setShowNew(true)}
							onToday={createToday}
						/>
					) : (
						<div className="space-y-5">
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
				</section>

				<aside className="hidden w-[420px] shrink-0 border-border/60 border-l p-4 xl:block">
					<NoteInspector
						note={selectedNote}
						noteDetail={noteQuery.data ?? null}
						draft={draft}
						mode={inspectorMode}
						saveState={saveState}
						tags={tags}
						status={selectedStatus}
						wikiLinks={wikiLinks}
						isSaving={updateMut.isPending}
						isPromoting={promoteMut.isPending}
						onModeChange={setInspectorMode}
						onDraftChange={(value) => {
							draftRef.current = value;
							setDraft(value);
							setSaveState((state) => (state === "saving" ? state : "dirty"));
						}}
						onBlur={() => {
							if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current);
							autoSaveTimer.current = setTimeout(autoSave, 500);
						}}
						onSave={saveNow}
						onPromote={promoteSelected}
						onDelete={() => {
							if (!selectedId || !selectedNote) return;
							if (
								confirm(
									`Delete "${displayTitle(selectedNote)}" from disk? This cannot be undone.`,
								)
							) {
								deleteMut.mutate({ id: selectedId });
							}
						}}
						onOpenLinkedNote={setSelectedId}
					/>
				</aside>
			</div>
		</div>
	);
}

function NoteSection({
	def,
	notes,
	selectedId,
	viewMode,
	onSelect,
}: {
	def: CategoryDef;
	notes: NoteListItem[];
	selectedId: string | null;
	viewMode: ViewMode;
	onSelect: (id: string) => void;
}) {
	const Icon = def.icon;
	return (
		<section>
			<div className="mb-2 flex items-center gap-2 px-1 text-[11px] text-muted-foreground uppercase tracking-[0.06em]">
				<Icon className="size-3.5" />
				<span className="font-[510]">{def.title}</span>
				<Badge variant="outline" className="h-4 px-1.5 font-normal">
					{notes.length}
				</Badge>
			</div>
			{viewMode === "cards" ? (
				<div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
					{notes.map((note) => (
						<NoteCard
							key={note.id}
							note={note}
							selected={selectedId === note.id}
							onSelect={onSelect}
						/>
					))}
				</div>
			) : (
				<ul className="space-y-1">
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
			)}
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
				"grid w-full grid-cols-[minmax(0,1.5fr)_minmax(120px,0.75fr)_auto] items-center gap-4 rounded-md border border-transparent px-3 py-2 text-left text-[13px] transition-colors hover:border-border/70 hover:bg-accent/35 focus-visible:border-violet-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40",
				selected && "border-violet-400/50 bg-violet-500/10",
			)}
		>
			<span className="min-w-0">
				<span className="block truncate font-[510] text-foreground tracking-[-0.005em]">
					{title}
				</span>
				<span className="block truncate font-mono text-[10.5px] text-muted-foreground/80">
					{note.relativePath}
				</span>
			</span>
			<span className="hidden min-w-0 items-center gap-1.5 md:flex">
				<Badge
					variant="outline"
					className="h-[18px] px-1.5 font-normal text-[10px]"
				>
					{statusLabel(note)}
				</Badge>
				{tags.slice(0, 2).map((tag) => (
					<span
						key={tag}
						className="max-w-20 truncate rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
					>
						{tag}
					</span>
				))}
			</span>
			<span className="text-right text-[11px] text-muted-foreground">
				{formatDate(note.updatedAt)}
			</span>
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
	const title = displayTitle(note);
	const tags = frontmatterArray(note.frontmatter, "tags");
	return (
		<button
			type="button"
			onClick={() => onSelect(note.id)}
			className={cn(
				"flex min-h-[136px] flex-col gap-3 rounded-md border border-border bg-card p-3 text-left transition-colors hover:border-border/80 hover:bg-accent/30 focus-visible:border-violet-400/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400/40",
				selected && "border-violet-400/60 ring-2 ring-violet-400/30",
			)}
		>
			<div className="flex items-start gap-2">
				<BrainIcon className="mt-0.5 size-3.5 shrink-0 text-violet-500" />
				<div className="min-w-0">
					<div className="truncate font-[510] text-[13px] text-foreground">
						{title}
					</div>
					<div className="mt-1 line-clamp-2 font-mono text-[10.5px] text-muted-foreground">
						{note.relativePath}
					</div>
				</div>
			</div>
			<div className="mt-auto flex flex-wrap items-center gap-1.5">
				<Badge
					variant="outline"
					className="h-[18px] px-1.5 font-normal text-[10px]"
				>
					{statusLabel(note)}
				</Badge>
				{tags.slice(0, 3).map((tag) => (
					<span
						key={tag}
						className="max-w-20 truncate rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
					>
						{tag}
					</span>
				))}
			</div>
		</button>
	);
}

function NoteInspector({
	note,
	noteDetail,
	draft,
	mode,
	saveState,
	tags,
	status,
	wikiLinks,
	isSaving,
	isPromoting,
	onModeChange,
	onDraftChange,
	onBlur,
	onSave,
	onPromote,
	onDelete,
	onOpenLinkedNote,
}: {
	note: NoteListItem | null;
	noteDetail: {
		id?: string;
		name?: string;
		relativePath?: string;
		vaultLabel?: string;
		content?: string | null;
		fileSha?: string;
	} | null;
	draft: string;
	mode: InspectorMode;
	saveState: SaveState;
	tags: string[];
	status: string | null;
	wikiLinks: Array<{ key: string; text: string; toNoteId: string | null }>;
	isSaving: boolean;
	isPromoting: boolean;
	onModeChange: (mode: InspectorMode) => void;
	onDraftChange: (value: string) => void;
	onBlur: () => void;
	onSave: () => void;
	onPromote: () => void;
	onDelete: () => void;
	onOpenLinkedNote: (id: string) => void;
}) {
	if (!note) {
		return (
			<div className="flex h-full flex-col rounded-md border border-border/60 bg-card/30 p-4">
				<div className="flex items-center gap-2 text-[12px] text-muted-foreground">
					<BrainIcon className="size-3.5" />
					<span>Select a note to preview or edit it here.</span>
				</div>
			</div>
		);
	}

	const title = displayTitle(note);
	const body = noteDetail?.content ?? "";
	const preview = body.trim() ? body.trim().slice(0, 1000) : "No content yet.";
	const truncated = body.trim().length > 1000;

	return (
		<div className="flex h-full flex-col overflow-hidden rounded-md border border-border/60 bg-card/40">
			<div className="border-border/60 border-b p-4">
				<div className="flex items-start justify-between gap-3">
					<div className="min-w-0">
						<h2 className="truncate font-[510] text-[14px] text-foreground tracking-[-0.01em]">
							{title}
						</h2>
						<p className="mt-1 truncate font-mono text-[10.5px] text-muted-foreground">
							{note.relativePath}
						</p>
					</div>
					<div className="inline-flex h-7 shrink-0 rounded-md border border-border bg-muted/40 p-0.5">
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
				</div>

				<div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
					<div className="rounded-md border border-border/50 bg-background/40 p-2">
						<div className="text-muted-foreground">Vault</div>
						<div className="mt-0.5 truncate font-[510] text-foreground">
							{noteDetail?.vaultLabel ?? "Personal Knowledge"}
						</div>
					</div>
					<div className="rounded-md border border-border/50 bg-background/40 p-2">
						<div className="text-muted-foreground">Updated</div>
						<div className="mt-0.5 font-[510] text-foreground">
							{formatDate(note.updatedAt)}
						</div>
					</div>
				</div>

				<div className="mt-3 flex flex-wrap gap-1.5">
					{status && (
						<Badge
							variant="outline"
							className="h-[18px] px-1.5 font-normal text-[10px]"
						>
							{status}
						</Badge>
					)}
					{tags.map((tag) => (
						<span
							key={tag}
							className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
						>
							{tag}
						</span>
					))}
				</div>
			</div>

			<div className="min-h-0 grow overflow-y-auto p-4">
				{mode === "edit" ? (
					<BlockEditor
						key={`${note.id}:${noteDetail?.fileSha ?? "pending"}`}
						value={draft}
						onChange={onDraftChange}
						onBlur={onBlur}
						className="editor-xl [&_.tiptap]:min-h-[420px]"
					/>
				) : (
					<div className="space-y-4">
						<pre className="whitespace-pre-wrap break-words font-sans text-[12px] text-foreground/80 leading-[1.55]">
							{preview}
							{truncated && (
								<span className="text-muted-foreground/70">...</span>
							)}
						</pre>
						{wikiLinks.length > 0 && (
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
						)}
						<BacklinksPanel entityType="knowledge" entityId={note.id} />
					</div>
				)}
			</div>

			<div className="flex items-center gap-2 border-border/60 border-t p-3">
				<AutoSaveIndicator state={saveState} />
				<Button size="sm" onClick={onSave} disabled={isSaving || !noteDetail}>
					<SaveIcon className="size-3.5" />
					{isSaving ? "Saving..." : "Save"}
				</Button>
				<Button
					size="sm"
					variant="outline"
					onClick={onPromote}
					disabled={isPromoting || !noteDetail}
				>
					<FileTextIcon className="size-3.5" />
					Promote
				</Button>
				<Button
					size="sm"
					variant="ghost"
					onClick={onDelete}
					className="ml-auto text-muted-foreground hover:text-destructive"
				>
					<Trash2Icon className="size-3.5" />
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
					{hasNotes ? "No notes match these filters" : "Your vault is empty"}
				</p>
				<p className="max-w-md text-balance text-[12px] text-muted-foreground">
					Knowledge notes are markdown files on disk. Filter by vault category,
					status, or updated date, then inspect and edit the note in the right
					pane.
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
			<Icon className="size-4 text-violet-500" />
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

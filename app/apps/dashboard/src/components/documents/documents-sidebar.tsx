"use client";

import {
	DndContext,
	type DragEndEvent,
	PointerSensor,
	useDraggable,
	useDroppable,
	useSensor,
	useSensors,
} from "@dnd-kit/core";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "@ui/components/ui/collapsible";
import { Input } from "@ui/components/ui/input";
import {
	SidebarGroup,
	SidebarGroupContent,
	SidebarGroupLabel,
	SidebarMenu,
	SidebarMenuAction,
	SidebarMenuButton,
	SidebarMenuItem,
	SidebarMenuSub,
	SidebarMenuSubButton,
	SidebarMenuSubItem,
} from "@ui/components/ui/sidebar";
import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import {
	ChevronRightIcon,
	ClockIcon,
	FilePlusIcon,
	GlobeIcon,
	SearchIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useDebounceValue } from "usehooks-ts";
import { ProjectIcon } from "@/components/project-icon";
import { useUser } from "@/components/user-provider";
import type { Document, Project } from "@/hooks/use-data";
import { useDocuments, useProjects } from "@/hooks/use-data";
import {
	invalidateDocumentsCache,
	updateDocumentInCache,
} from "@/hooks/use-data-cache-helpers";
import { trpc } from "@/utils/trpc";
import { DocumentContextMenu } from "./context-menu";
import { DocumentIcon } from "./document-icon";

// ─── Helpers ─────────────────────────────────────────────────────────

type DocWithProject = Document & { projectId?: string | null };

/**
 * Walk the full tree and produce two helpers:
 *  - nameCount: name → number of docs that share this exact title (for dedup display)
 *  - parentDir: id  → "/folder/sub" path of ancestor names (root docs get "")
 */
function buildDedupeMaps(roots: DocWithProject[]) {
	const nameCount = new Map<string, number>();
	const parentDir = new Map<string, string>();

	function walk(doc: DocWithProject, dir: string) {
		const display = doc.name || "Untitled Document";
		nameCount.set(display, (nameCount.get(display) ?? 0) + 1);
		parentDir.set(doc.id, dir);
		const nextDir = dir ? `${dir}/${display}` : `/${display}`;
		if (doc.children && doc.children.length > 0) {
			for (const child of doc.children) {
				walk(child as DocWithProject, nextDir);
			}
		}
	}

	for (const root of roots) walk(root, "");
	return { nameCount, parentDir };
}

/**
 * Returns true if any doc with the given id exists anywhere in the subtree.
 */
function subtreeContains(doc: DocWithProject, id: string | null): boolean {
	if (!id) return false;
	if (doc.id === id) return true;
	if (!doc.children) return false;
	for (const child of doc.children) {
		if (subtreeContains(child as DocWithProject, id)) return true;
	}
	return false;
}

/**
 * Filter the tree by a case-insensitive name match. Keeps parents whose
 * descendants match so the tree shape is preserved (Linear filters the
 * grouped index but never flattens the hierarchy).
 */
function filterTree(docs: DocWithProject[], needle: string): DocWithProject[] {
	if (!needle) return docs;
	const n = needle.toLowerCase();
	const out: DocWithProject[] = [];
	for (const doc of docs) {
		const childMatches = filterTree(
			(doc.children ?? []) as DocWithProject[],
			needle,
		);
		const selfMatches = (doc.name || "Untitled Document")
			.toLowerCase()
			.includes(n);
		if (selfMatches || childMatches.length > 0) {
			out.push({ ...doc, children: childMatches } as DocWithProject);
		}
	}
	return out;
}

/**
 * Flatten the tree, then sort by updatedAt desc, take top 5.
 */
function recentlyEdited(docs: DocWithProject[], limit = 5): DocWithProject[] {
	const flat: DocWithProject[] = [];
	function walk(doc: DocWithProject) {
		flat.push(doc);
		if (doc.children) {
			for (const child of doc.children) walk(child as DocWithProject);
		}
	}
	for (const d of docs) walk(d);
	return flat
		.filter((d) => Boolean(d.updatedAt))
		.sort(
			(a, b) =>
				new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
		)
		.slice(0, limit);
}

// ─── Recursive tree item (works for all levels) ─────────────────────

function DocumentTreeItem({
	doc,
	expandedIds,
	onToggleExpand,
	nameCount,
	parentDir,
	projectsById,
	depth = 0,
}: {
	doc: DocWithProject;
	expandedIds: Set<string>;
	onToggleExpand: (id: string) => void;
	nameCount: Map<string, number>;
	parentDir: Map<string, string>;
	projectsById: Map<string, Project>;
	depth?: number;
}) {
	const user = useUser();
	const pathname = usePathname();
	const hasChildren = doc.children?.length > 0;
	const isActive = pathname === `${user.basePath}/documents/${doc.id}`;
	const isExpanded = expandedIds.has(doc.id);

	const {
		listeners,
		attributes,
		setNodeRef: setDraggableRef,
		transform,
		isDragging,
	} = useDraggable({ id: doc.id });

	const { setNodeRef: setDroppableRef, isOver } = useDroppable({
		id: doc.id,
	});

	const {
		setNodeRef: setSeparatorDroppableAfterRef,
		isOver: isOverAfterSeparator,
	} = useDroppable({
		id: `${doc.id}-after-separator`,
	});

	const {
		setNodeRef: setSeparatorDroppableBeforeRef,
		isOver: isOverBeforeSeparator,
	} = useDroppable({
		id: `${doc.id}-before-separator`,
	});

	const childrenSorted = useMemo(() => {
		if (!doc.children) return [];
		return [...doc.children].sort((a, b) => a.order - b.order);
	}, [doc.children]);

	const isRoot = depth === 0;
	const Wrapper = isRoot ? SidebarMenuItem : SidebarMenuSubItem;
	const Button = isRoot ? SidebarMenuButton : SidebarMenuSubButton;
	const iconSize = isRoot ? "size-4" : "size-3.5";

	const displayName = doc.name || "Untitled Document";
	const isDuplicate = (nameCount.get(displayName) ?? 0) > 1;
	const dir = parentDir.get(doc.id) ?? "";
	// When the name clashes, show enough scope to disambiguate. Prefer the
	// in-tree parent path (Linear's "/docs/ARCHITECTURE.md" style). If the
	// doc is at the root of its project, fall back to the project name —
	// otherwise duplicates inside a "Recently edited" flat list would be
	// indistinguishable.
	const dirLabel =
		dir ||
		(doc.projectId && projectsById.get(doc.projectId)?.name) ||
		"Team-wide";

	return (
		<>
			<div
				className={cn("relative h-px", {
					"bg-accent": isOverBeforeSeparator && !isDragging,
				})}
			>
				<div
					ref={setSeparatorDroppableBeforeRef}
					className={cn("-translate-y-1/2 absolute inset-0 h-4")}
				/>
			</div>
			<Wrapper
				className={
					isOver && !isDragging
						? "z-10 rounded-md bg-accent/50 ring-1 ring-accent-foreground/20"
						: ""
				}
			>
				<Collapsible
					open={isExpanded}
					onOpenChange={() => onToggleExpand(doc.id)}
				>
					<div className="flex items-center">
						<DocumentContextMenu document={doc}>
							<Button
								asChild
								isActive={isActive}
								ref={(node: HTMLElement | null) => {
									setDraggableRef(node);
									setDroppableRef(node);
								}}
								{...listeners}
								{...attributes}
								style={{
									transform: transform
										? `translate3d(${transform.x}px, ${transform.y}px, 0)`
										: undefined,
								}}
								className={cn("flex-1", {
									"pointer-events-none z-50 opacity-50": isDragging,
									"hover:bg-transparent dark:hover:bg-transparent": isOver,
								})}
							>
								<Link
									href={`${user.basePath}/documents/${doc.id}`}
									className="min-w-0"
								>
									<DocumentIcon
										icon={doc.icon}
										className={iconSize}
										hasChildren={hasChildren}
									/>
									<span className="flex min-w-0 flex-col leading-tight">
										<span className="truncate">{displayName}</span>
										{isDuplicate && dirLabel ? (
											<span className="truncate text-[11px] text-muted-foreground/70">
												{dirLabel}
											</span>
										) : null}
									</span>
								</Link>
							</Button>
						</DocumentContextMenu>
						{hasChildren && (
							<CollapsibleTrigger asChild>
								<SidebarMenuAction>
									<ChevronRightIcon
										className={`size-3 transition-transform ${isExpanded ? "rotate-90" : ""}`}
									/>
								</SidebarMenuAction>
							</CollapsibleTrigger>
						)}
					</div>
					{hasChildren && (
						<CollapsibleContent className="overflow-visible!">
							<SidebarMenuSub className="gap-0.5">
								{childrenSorted.map((child) => (
									<DocumentTreeItem
										key={child.id}
										doc={child as DocWithProject}
										expandedIds={expandedIds}
										onToggleExpand={onToggleExpand}
										nameCount={nameCount}
										parentDir={parentDir}
										projectsById={projectsById}
										depth={depth + 1}
									/>
								))}
							</SidebarMenuSub>
						</CollapsibleContent>
					)}
				</Collapsible>
			</Wrapper>
			<div
				className={cn("relative h-px", {
					"bg-accent": isOverAfterSeparator && !isDragging,
				})}
			>
				<div
					ref={setSeparatorDroppableAfterRef}
					className={cn("-translate-y-1/2 absolute inset-0 h-4")}
				/>
			</div>
		</>
	);
}

// ─── Recent row (flat link, no nesting / no DnD) ─────────────────────

function RecentRow({
	doc,
	parentDir,
	nameCount,
	projectsById,
}: {
	doc: DocWithProject;
	parentDir: Map<string, string>;
	nameCount: Map<string, number>;
	projectsById: Map<string, Project>;
}) {
	const user = useUser();
	const pathname = usePathname();
	const isActive = pathname === `${user.basePath}/documents/${doc.id}`;
	const displayName = doc.name || "Untitled Document";
	const isDuplicate = (nameCount.get(displayName) ?? 0) > 1;
	const dir = parentDir.get(doc.id) ?? "";
	const dirLabel =
		dir ||
		(doc.projectId && projectsById.get(doc.projectId)?.name) ||
		"Team-wide";

	return (
		<SidebarMenuSubItem>
			<SidebarMenuSubButton asChild isActive={isActive}>
				<Link href={`${user.basePath}/documents/${doc.id}`} className="min-w-0">
					<DocumentIcon
						icon={doc.icon}
						className="size-3.5"
						hasChildren={false}
					/>
					<span className="flex min-w-0 flex-col leading-tight">
						<span className="truncate">{displayName}</span>
						{isDuplicate && dirLabel ? (
							<span className="truncate text-[11px] text-muted-foreground/70">
								{dirLabel}
							</span>
						) : null}
					</span>
				</Link>
			</SidebarMenuSubButton>
		</SidebarMenuSubItem>
	);
}

// ─── Project-grouped section ─────────────────────────────────────────

function DocsByProjectSection({
	headerLabel,
	headerIcon,
	count,
	docs,
	defaultOpen,
	expandedIds,
	onToggleExpand,
	nameCount,
	parentDir,
	projectsById,
}: {
	headerLabel: string;
	headerIcon: React.ReactNode;
	count: number;
	docs: DocWithProject[];
	defaultOpen: boolean;
	expandedIds: Set<string>;
	onToggleExpand: (id: string) => void;
	nameCount: Map<string, number>;
	parentDir: Map<string, string>;
	projectsById: Map<string, Project>;
}) {
	// Force re-mount when defaultOpen changes (so navigating to a doc in
	// another section pops that section open).
	return (
		<Collapsible
			key={`${headerLabel}-${defaultOpen}`}
			defaultOpen={defaultOpen}
			className="group/docs-section"
		>
			<CollapsibleTrigger asChild>
				<button
					type="button"
					className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px] text-muted-foreground uppercase tracking-[0.04em] transition-colors hover:text-foreground [&[data-state=open]>svg:first-child]:rotate-90"
				>
					<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
					<span className="flex size-3.5 shrink-0 items-center justify-center">
						{headerIcon}
					</span>
					<span className="flex-1 truncate font-[510]">{headerLabel}</span>
					<span className="text-[10px] tabular-nums opacity-60">{count}</span>
				</button>
			</CollapsibleTrigger>
			<CollapsibleContent className="overflow-visible!">
				<SidebarMenu className="gap-0.5">
					{docs.length === 0 ? (
						<li className="px-6 py-1 text-[11px] text-muted-foreground/60 italic">
							No documents
						</li>
					) : (
						docs.map((doc) => (
							<DocumentTreeItem
								key={doc.id}
								doc={doc}
								expandedIds={expandedIds}
								onToggleExpand={onToggleExpand}
								nameCount={nameCount}
								parentDir={parentDir}
								projectsById={projectsById}
							/>
						))
					)}
				</SidebarMenu>
			</CollapsibleContent>
		</Collapsible>
	);
}

// ─── Main sidebar ────────────────────────────────────────────────────

export function DocumentsSidebar() {
	const user = useUser();
	const pathname = usePathname();
	const router = useRouter();
	const [search, setSearch] = useState("");
	const [debouncedSearch] = useDebounceValue(search, 300);

	// Extract the active document ID from the URL
	const activeDocumentId = useMemo(() => {
		const match = pathname.match(
			new RegExp(`${user.basePath}/documents/([^/]+)$`),
		);
		return match?.[1] ?? null;
	}, [pathname, user.basePath]);

	// For the project-grouped sidebar we need the full tree (not just one
	// parent's children) so we can dedupe titles + bucket by project. The
	// trpc.documents.get schema caps pageSize at 100 — that's plenty for a
	// per-team document set (audit found 30+ at the upper end).
	const { data, isLoading } = useDocuments({
		pageSize: 100,
		...(debouncedSearch ? { search: debouncedSearch } : {}),
	});

	const { data: projectsData } = useProjects();

	const dataSorted = useMemo<DocWithProject[]>(() => {
		if (!data) return [];
		return [...data.data].sort((a, b) => a.order - b.order) as DocWithProject[];
	}, [data]);

	// Fetch the path of the active document to auto-expand ancestors
	const { data: documentPath } = useQuery({
		...trpc.documents.getPath.queryOptions({ id: activeDocumentId! }),
		enabled: Boolean(activeDocumentId),
	});

	// Track expanded folder IDs
	const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

	// Auto-expand ancestors when navigating to a document
	useEffect(() => {
		if (documentPath && documentPath.length > 1) {
			setExpandedIds((prev) => {
				const next = new Set(prev);
				// Expand all ancestors (exclude the last item which is the document itself)
				for (const ancestor of documentPath.slice(0, -1)) {
					next.add(ancestor.id);
				}
				return next;
			});
		}
	}, [documentPath]);

	const onToggleExpand = useCallback((id: string) => {
		setExpandedIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) {
				next.delete(id);
			} else {
				next.add(id);
			}
			return next;
		});
	}, []);

	// Local mirror — kept so drag re-ordering can update the UI before the
	// server acks the mutation.
	const [localItems, setLocalItems] = useState<DocWithProject[]>([]);
	useEffect(() => {
		if (dataSorted.length > 0) {
			setLocalItems(dataSorted);
		} else if (!isLoading) {
			setLocalItems([]);
		}
	}, [dataSorted, isLoading]);

	const filteredItems = useMemo(
		() => filterTree(localItems, debouncedSearch),
		[localItems, debouncedSearch],
	);

	const { nameCount, parentDir } = useMemo(
		() => buildDedupeMaps(localItems),
		[localItems],
	);

	const recent = useMemo(
		() => (debouncedSearch ? [] : recentlyEdited(localItems, 5)),
		[localItems, debouncedSearch],
	);

	// Bucket root docs by projectId. Children stay nested under their parents
	// (we only group the *top* of the tree).
	const projectsById = useMemo(() => {
		const map = new Map<string, Project>();
		const items = (projectsData?.data ?? []) as Project[];
		for (const p of items) map.set(p.id, p);
		return map;
	}, [projectsData]);

	const groups = useMemo(() => {
		const byProject = new Map<string, DocWithProject[]>();
		const teamWide: DocWithProject[] = [];
		for (const doc of filteredItems) {
			const pid = doc.projectId ?? null;
			if (pid && projectsById.has(pid)) {
				const arr = byProject.get(pid) ?? [];
				arr.push(doc);
				byProject.set(pid, arr);
			} else {
				teamWide.push(doc);
			}
		}
		return { byProject, teamWide };
	}, [filteredItems, projectsById]);

	const sensors = useSensors(
		useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
	);

	const { mutate: createDocument } = useMutation(
		trpc.documents.create.mutationOptions({
			onMutate: () => {
				toast.loading("Creating document...", { id: "create-document" });
			},
			onSuccess: (doc) => {
				toast.success("Document created", { id: "create-document" });
				invalidateDocumentsCache();
				router.push(`${user.basePath}/documents/${doc.id}`);
			},
			onError: () => {
				toast.error("Failed to create document", { id: "create-document" });
			},
		}),
	);

	const { mutate: reorderDocuments } = useMutation(
		trpc.documents.reorder.mutationOptions({
			onSuccess: (docs) => {
				for (const doc of docs) {
					updateDocumentInCache(doc);
				}
			},
			onError: () => {
				toast.error("Failed to reorder documents");
			},
		}),
	);

	// Find a document at any depth in the tree
	const findDocInTree = useCallback(
		(docs: DocWithProject[], id: string): DocWithProject | undefined => {
			for (const doc of docs) {
				if (doc.id === id) return doc;
				const found = findDocInTree(
					(doc.children ?? []) as DocWithProject[],
					id,
				);
				if (found) return found;
			}
			return undefined;
		},
		[],
	);

	const handleDragEnd = (event: DragEndEvent) => {
		const { active, over } = event;
		if (!over || active.id === over.id) return;

		const draggedId = active.id as string;
		const overId = over.id as string;

		const draggedDoc = findDocInTree(localItems, draggedId);
		if (!draggedDoc) return;

		if (overId === "root") {
			reorderDocuments({
				items: [{ id: draggedId, order: 0, parentId: null }],
			});
			return;
		}

		if (overId.endsWith("-separator")) {
			const separatorType = overId.includes("-before-") ? "before" : "after";
			const targetId = overId.replace(`-${separatorType}-separator`, "");
			const targetDoc = findDocInTree(localItems, targetId);
			if (!targetDoc) return;

			const targetParentId = targetDoc.parentId ?? null;
			const siblings = targetParentId
				? ((findDocInTree(localItems, targetParentId)?.children ??
						[]) as DocWithProject[])
				: localItems;

			const draggedIndex = siblings.findIndex((doc) => doc.id === draggedId);
			const targetIndex = siblings.findIndex((doc) => doc.id === targetId);
			if (targetIndex === -1) return;

			const updatedSiblings = [...siblings];

			if (draggedIndex !== -1) {
				updatedSiblings.splice(draggedIndex, 1);
			}

			const adjustedTargetIndex = updatedSiblings.findIndex(
				(doc) => doc.id === targetId,
			);
			const newIndex =
				separatorType === "before"
					? adjustedTargetIndex
					: adjustedTargetIndex + 1;

			updatedSiblings.splice(newIndex, 0, {
				...draggedDoc,
				parentId: targetParentId,
			});

			const reorderItems = updatedSiblings.map((doc, index) => ({
				id: doc.id,
				order: index,
				parentId: doc.parentId ?? null,
			}));

			reorderDocuments({ items: reorderItems });
			return;
		}

		const overDoc = findDocInTree(localItems, overId);
		if (overDoc) {
			reorderDocuments({
				items: [{ id: draggedId, order: 0, parentId: overId }],
			});
		}
	};

	const isCreateActive = pathname === `${user.basePath}/documents/create`;

	// Per-group default-open: true when the group owns the active doc, or
	// when the user is searching, or when it's the team-wide bucket and no
	// other group is highlighted.
	const sectionIsOpen = useCallback(
		(docs: DocWithProject[]): boolean => {
			if (debouncedSearch) return true;
			if (!activeDocumentId) return false;
			return docs.some((d) => subtreeContains(d, activeDocumentId));
		},
		[debouncedSearch, activeDocumentId],
	);

	const hasAnyResult =
		recent.length > 0 ||
		groups.byProject.size > 0 ||
		groups.teamWide.length > 0;

	return (
		<>
			<SidebarGroup>
				<SidebarGroupContent>
					<SidebarMenu>
						<SidebarMenuItem>
							<SidebarMenuButton
								isActive={isCreateActive}
								onClick={() => {
									createDocument({
										name: "",
										content: "",
										parentId: undefined,
									});
								}}
							>
								<FilePlusIcon className="size-4" />
								<span>New Document</span>
							</SidebarMenuButton>
						</SidebarMenuItem>
					</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>
			<SidebarGroup className="flex-1">
				<SidebarGroupLabel>Documents</SidebarGroupLabel>
				<SidebarGroupContent className="flex flex-1 flex-col">
					<SidebarMenu className="flex-1">
						<SidebarMenuItem className="relative">
							<SearchIcon className="-translate-y-1/2 absolute top-1/2 left-2 size-4 opacity-50" />
							<Input
								placeholder="Search documents..."
								value={search}
								variant="ghost"
								className="ps-8"
								onChange={(e) => setSearch(e.target.value)}
							/>
						</SidebarMenuItem>
						{isLoading &&
							Array.from({ length: 3 }).map((_, i) => (
								<SidebarMenuItem key={`skeleton-${i}`}>
									<div className="px-2 py-1.5">
										<Skeleton className="h-4 w-full" />
									</div>
								</SidebarMenuItem>
							))}

						<DndContext sensors={sensors} onDragEnd={handleDragEnd}>
							<RootDroppable>
								{/* Recently edited — only when not searching */}
								{!debouncedSearch && recent.length > 0 && (
									<Collapsible defaultOpen={true} className="mt-1">
										<CollapsibleTrigger asChild>
											<button
												type="button"
												className="flex w-full items-center gap-1.5 px-2 py-1 text-left text-[11px] text-muted-foreground uppercase tracking-[0.04em] transition-colors hover:text-foreground [&[data-state=open]>svg:first-child]:rotate-90"
											>
												<ChevronRightIcon className="size-3 shrink-0 transition-transform" />
												<ClockIcon className="size-3.5 shrink-0" />
												<span className="flex-1 truncate font-[510]">
													Recently edited
												</span>
												<span className="text-[10px] tabular-nums opacity-60">
													{recent.length}
												</span>
											</button>
										</CollapsibleTrigger>
										<CollapsibleContent className="overflow-visible!">
											<SidebarMenuSub className="gap-0.5">
												{recent.map((doc) => (
													<RecentRow
														key={`recent-${doc.id}`}
														doc={doc}
														parentDir={parentDir}
														nameCount={nameCount}
														projectsById={projectsById}
													/>
												))}
											</SidebarMenuSub>
										</CollapsibleContent>
									</Collapsible>
								)}

								{/* One section per project that owns at least one doc */}
								{Array.from(groups.byProject.entries()).map(
									([projectId, docs]) => {
										const project = projectsById.get(projectId)!;
										return (
											<DocsByProjectSection
												key={projectId}
												headerLabel={project.name}
												headerIcon={
													<ProjectIcon
														color={project.color}
														className="size-3.5"
													/>
												}
												count={docs.length}
												docs={docs}
												defaultOpen={sectionIsOpen(docs)}
												expandedIds={expandedIds}
												onToggleExpand={onToggleExpand}
												nameCount={nameCount}
												parentDir={parentDir}
												projectsById={projectsById}
											/>
										);
									},
								)}

								{/* Team-wide bucket: docs with projectId IS NULL */}
								{groups.teamWide.length > 0 && (
									<DocsByProjectSection
										headerLabel="Team-wide"
										headerIcon={
											<GlobeIcon className="size-3.5 text-muted-foreground" />
										}
										count={groups.teamWide.length}
										docs={groups.teamWide}
										defaultOpen={
											// Team-wide opens by default when there are no
											// project sections (so the sidebar isn't all-closed
											// on first load) or when it owns the active doc.
											groups.byProject.size === 0 ||
											sectionIsOpen(groups.teamWide)
										}
										expandedIds={expandedIds}
										onToggleExpand={onToggleExpand}
										nameCount={nameCount}
										parentDir={parentDir}
										projectsById={projectsById}
									/>
								)}

								{!isLoading && !hasAnyResult && (
									<div className="px-3 py-6 text-center text-[12px] text-muted-foreground">
										{debouncedSearch
											? "No documents match your search."
											: "No documents yet."}
									</div>
								)}
							</RootDroppable>
						</DndContext>
					</SidebarMenu>
				</SidebarGroupContent>
			</SidebarGroup>
		</>
	);
}

export const RootDroppable = ({ children }: { children: React.ReactNode }) => {
	const { setNodeRef } = useDroppable({ id: "root" });

	return (
		<div ref={setNodeRef} className="size-full flex-1">
			{children}
		</div>
	);
};

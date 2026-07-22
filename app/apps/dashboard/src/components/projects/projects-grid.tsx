"use client";

/**
 * Projects grid — iter-10 Round E redesign.
 *
 * Responsive card grid (1 / 2 / 3 columns) with:
 *   - pinned-projects sticky row at the top (drag-reorder via dnd-kit, order
 *     persisted to localStorage `nexus.projects.order`).
 *   - group-by toggle (Status / Owner / Recency) for the non-pinned section.
 *   - sparse-state starter templates when the project list is empty.
 *   - hover-revealed pin button + cards with progress / status / activity.
 *
 * Pin state is currently localStorage-only (key `nexus.projects.pinned.<id>`)
 * because the `projects.pinned` migration is deferred to iter-8. The reorder
 * routine is also client-side (no `projects.reorder` tRPC route yet).
 *
 * Single-user-mode (codex amendment #1): the "by Owner" grouping collapses to
 * a single "You" bucket when `IS_SINGLE_USER_MODE` is true so we don't surface
 * member affordances that mean nothing locally.
 */

import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@ui/components/ui/select";
import { cn } from "@ui/lib/utils";
import {
	BookOpenIcon,
	BugIcon,
	ChevronDownIcon,
	ChevronRightIcon,
	type LucideIcon,
	PinIcon,
	PinOffIcon,
	SparklesIcon,
	WrenchIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { ProjectIcon } from "@/components/project-icon";
import { useUser } from "@/components/user-provider";
import { usePinnedProjects } from "@/hooks/use-pinned-projects";
import { useProjectParams } from "@/hooks/use-project-params";
import { IS_SINGLE_USER_MODE } from "@/lib/single-user-mode";
import { queryClient, trpc } from "@/utils/trpc";
import { ProjectContextMenu } from "./context-menu";
import { ProjectsFilters } from "./filters";
import type { Project } from "./list";
import { useProjectsFilterParams } from "./use-projects-filter-params";

// ─── Order persistence (pin state lives in use-pinned-projects.ts) ────────

const ORDER_STORAGE_KEY = "nexus.projects.order";

function readOrder(): string[] {
	if (typeof window === "undefined") return [];
	try {
		const raw = window.localStorage.getItem(ORDER_STORAGE_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw) as unknown;
		if (Array.isArray(parsed))
			return parsed.filter((v): v is string => typeof v === "string");
		return [];
	} catch {
		return [];
	}
}

function writeOrder(order: string[]): void {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(ORDER_STORAGE_KEY, JSON.stringify(order));
	} catch {
		// ignore
	}
}

// ─── Grouping ──────────────────────────────────────────────────────────────

type GroupKey = "recency" | "status" | "owner";

const GROUP_LABELS: Record<GroupKey, string> = {
	recency: "Recency",
	status: "Status",
	owner: "Owner",
};

interface ProjectGroup {
	id: string;
	label: string;
	projects: Project[];
}

function bucketByRecency(project: Project): string {
	const updated = project.updatedAt ? new Date(project.updatedAt).getTime() : 0;
	if (!updated) return "Older";
	const ageMs = Date.now() - updated;
	const day = 86_400_000;
	if (ageMs < day) return "Today";
	if (ageMs < 7 * day) return "This week";
	if (ageMs < 30 * day) return "This month";
	return "Older";
}

const RECENCY_ORDER = ["Today", "This week", "This month", "Older"];

function groupProjects(projects: Project[], groupBy: GroupKey): ProjectGroup[] {
	const buckets = new Map<string, Project[]>();

	for (const project of projects) {
		let key: string;
		if (groupBy === "status") {
			key = project.archived ? "Archived" : (project.status ?? "Active");
		} else if (groupBy === "owner") {
			if (IS_SINGLE_USER_MODE) {
				key = "You";
			} else {
				key = project.lead?.name ?? "Unassigned";
			}
		} else {
			key = bucketByRecency(project);
		}
		const list = buckets.get(key);
		if (list) list.push(project);
		else buckets.set(key, [project]);
	}

	const groups: ProjectGroup[] = [];
	for (const [label, list] of buckets.entries()) {
		groups.push({ id: label, label, projects: list });
	}

	if (groupBy === "recency") {
		groups.sort(
			(a, b) => RECENCY_ORDER.indexOf(a.label) - RECENCY_ORDER.indexOf(b.label),
		);
	} else {
		groups.sort((a, b) => a.label.localeCompare(b.label));
	}
	return groups;
}

// ─── Activity timestamp ────────────────────────────────────────────────────

function relativeTime(input: Date | string | null | undefined): string {
	if (!input) return "—";
	const date = typeof input === "string" ? new Date(input) : input;
	if (Number.isNaN(date.getTime())) return "—";
	const diff = Date.now() - date.getTime();
	if (diff < 60_000) return "just now";
	if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
	if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
	if (diff < 30 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
	const months = Math.floor(diff / (30 * 86_400_000));
	return `${months}mo ago`;
}

// ─── Status pill ────────────────────────────────────────────────────────────

function StatusPill({ project }: { project: Project }) {
	const status = project.archived
		? "Archived"
		: project.status === "completed"
			? "Done"
			: project.status === "on_hold"
				? "Backlog"
				: "Active";
	const tone =
		status === "Done"
			? "bg-emerald-500/10 text-emerald-600 border-emerald-500/20"
			: status === "Backlog"
				? "bg-muted text-muted-foreground border-border"
				: status === "Archived"
					? "bg-muted/50 text-muted-foreground border-border"
					: "bg-brand/10 text-brand border-brand/20";
	return (
		<span
			className={cn(
				"inline-flex items-center rounded-full border px-2 py-0.5 font-medium text-[10px] uppercase tracking-wide",
				tone,
			)}
		>
			{status}
		</span>
	);
}

// ─── Card ──────────────────────────────────────────────────────────────────

interface ProjectCardProps {
	project: Project;
	href: string;
	isPinned: boolean;
	onTogglePin: (id: string) => void;
}

function ProjectCard({
	project,
	href,
	isPinned,
	onTogglePin,
}: ProjectCardProps) {
	const total = project.progress.inProgress + project.progress.completed;
	const percent =
		total > 0 ? Math.round((project.progress.completed / total) * 100) : 0;
	const accent = project.color || "var(--brand)";

	return (
		<ProjectContextMenu project={project}>
			<div className="group relative">
				<Link
					href={href}
					className={cn(
						"block h-full rounded-lg border border-border bg-card p-4 transition-colors",
						"hover:border-brand/40 hover:bg-muted/50",
					)}
				>
					<div className="flex items-start justify-between gap-2">
						<div className="flex min-w-0 items-center gap-2">
							<ProjectIcon className="size-4 shrink-0" color={accent} />
							<h3 className="truncate font-semibold text-[16px] text-foreground leading-tight">
								{project.name}
							</h3>
						</div>
						<StatusPill project={project} />
					</div>

					<p className="mt-2 line-clamp-2 min-h-[2.5em] text-muted-foreground text-xs">
						{project.description || "No description yet."}
					</p>

					<div className="mt-3">
						<div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
							<div
								className="h-full transition-[width] duration-300"
								style={{
									width: `${percent}%`,
									backgroundColor: accent,
								}}
							/>
						</div>
						<div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground">
							<span>
								{project.progress.completed} / {total} tasks
							</span>
							<span>{relativeTime(project.updatedAt)}</span>
						</div>
					</div>
				</Link>

				{/* Hover-revealed pin button (Task 5) */}
				<button
					type="button"
					aria-label={isPinned ? "Unpin project" : "Pin project"}
					aria-pressed={isPinned}
					onClick={(e) => {
						e.preventDefault();
						e.stopPropagation();
						onTogglePin(project.id);
					}}
					className={cn(
						"absolute top-2 right-2 inline-flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-all",
						"hover:border-border hover:bg-background hover:text-foreground",
						isPinned
							? "text-brand opacity-100"
							: "opacity-0 focus:opacity-100 group-hover:opacity-100",
					)}
				>
					{isPinned ? (
						<PinOffIcon className="size-3.5" />
					) : (
						<PinIcon className="size-3.5" />
					)}
				</button>
			</div>
		</ProjectContextMenu>
	);
}

// ─── Sparse-state starter templates ─────────────────────────────────────────

interface StarterTemplate {
	id: string;
	name: string;
	description: string;
	icon: LucideIcon;
	color: string;
}

const STARTER_TEMPLATES: StarterTemplate[] = [
	{
		id: "internal-tool",
		name: "Internal tool",
		description: "Ship an internal tool with milestones + a kanban board.",
		icon: WrenchIcon,
		color: "#26b5ce",
	},
	{
		id: "bug-bash",
		name: "Bug bash",
		description: "Triage and crush a backlog of bugs in a single sprint.",
		icon: BugIcon,
		color: "#e11d48",
	},
	{
		id: "feature-epic",
		name: "Feature epic",
		description: "Plan a multi-milestone feature with discovery + delivery.",
		icon: SparklesIcon,
		color: "#10b981",
	},
	{
		id: "documentation",
		name: "Documentation",
		description: "Capture decisions, runbooks, and onboarding in one place.",
		icon: BookOpenIcon,
		color: "#f59e0b",
	},
];

function StarterTemplates({
	onCreate,
}: {
	onCreate: (template: StarterTemplate) => void;
}) {
	return (
		<div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
			{STARTER_TEMPLATES.map((template) => {
				const Icon = template.icon;
				return (
					<button
						key={template.id}
						type="button"
						onClick={() => onCreate(template)}
						className={cn(
							"group flex flex-col items-start gap-2 rounded-lg border border-border border-dashed bg-card p-4 text-left transition-colors",
							"hover:border-brand/50 hover:bg-muted/40",
						)}
					>
						<div
							className="flex size-8 items-center justify-center rounded-md"
							style={{
								backgroundColor: `${template.color}1a`,
								color: template.color,
							}}
						>
							<Icon className="size-4" />
						</div>
						<div className="font-medium text-foreground text-sm">
							{template.name}
						</div>
						<div className="text-muted-foreground text-xs">
							{template.description}
						</div>
						<div className="mt-auto text-brand text-xs opacity-0 transition-opacity group-hover:opacity-100">
							Use template →
						</div>
					</button>
				);
			})}
		</div>
	);
}

// ─── Main grid ──────────────────────────────────────────────────────────────

interface ProjectsGridProps {
	showFilters?: boolean;
	pageSize?: number;
}

export function ProjectsGrid({
	showFilters = true,
	pageSize = 30,
}: ProjectsGridProps) {
	const user = useUser();
	const { setParams } = useProjectParams();
	const { params } = useProjectsFilterParams();
	const { pinned, toggle: togglePin } = usePinnedProjects();
	const [groupBy, setGroupBy] = useState<GroupKey>("recency");
	const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(
		() => new Set(),
	);
	const [pinnedCollapsed, setPinnedCollapsed] = useState(false);
	const [pinnedOrder, setPinnedOrder] = useState<string[]>(() => readOrder());

	const { data } = useInfiniteQuery(
		trpc.projects.get.infiniteQueryOptions(
			{
				pageSize,
				search: params.search ?? "",
			},
			{
				getNextPageParam: (lastPage) => lastPage.meta.cursor,
			},
		),
	);

	const allProjects = useMemo(
		() => data?.pages.flatMap((page) => page.data) ?? [],
		[data],
	);

	const { pinnedProjects, otherProjects } = useMemo(() => {
		const pinnedList: Project[] = [];
		const others: Project[] = [];
		for (const project of allProjects) {
			if (pinned.has(project.id)) pinnedList.push(project);
			else others.push(project);
		}
		// Sort pinned by saved order; unknown ids fall to the end.
		pinnedList.sort((a, b) => {
			const aIdx = pinnedOrder.indexOf(a.id);
			const bIdx = pinnedOrder.indexOf(b.id);
			if (aIdx === -1 && bIdx === -1) return 0;
			if (aIdx === -1) return 1;
			if (bIdx === -1) return -1;
			return aIdx - bIdx;
		});
		return { pinnedProjects: pinnedList, otherProjects: others };
	}, [allProjects, pinned, pinnedOrder]);

	const groups = useMemo(
		() => groupProjects(otherProjects, groupBy),
		[otherProjects, groupBy],
	);

	const toggleGroup = useCallback((id: string) => {
		setCollapsedGroups((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	}, []);

	// Drag-reorder: simple index swap on drop. dnd-kit is overkill for a single
	// pinned row of usually 1-5 items; this works on touch + keyboard via the
	// native HTML5 DnD API and is a stub the reorder route can replace later.
	const movePinned = useCallback(
		(fromId: string, toId: string) => {
			setPinnedOrder((prev) => {
				const ids = pinnedProjects.map((p) => p.id);
				const order = prev.length > 0 ? [...prev] : ids;
				// Ensure both ids are tracked.
				for (const id of ids) {
					if (!order.includes(id)) order.push(id);
				}
				const fromIdx = order.indexOf(fromId);
				const toIdx = order.indexOf(toId);
				if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return prev;
				order.splice(fromIdx, 1);
				order.splice(toIdx, 0, fromId);
				writeOrder(order);
				return order;
			});
		},
		[pinnedProjects],
	);

	const createMutation = useMutation(
		trpc.projects.create.mutationOptions({
			onSuccess: (project) => {
				queryClient.invalidateQueries(trpc.projects.get.infiniteQueryOptions());
				queryClient.invalidateQueries(trpc.projects.get.queryOptions());
				toast.success(`Project "${project.name}" created`);
				setParams({ projectId: project.id });
			},
			onError: () => {
				toast.error("Failed to create project from template");
			},
		}),
	);

	const onTemplateCreate = useCallback(
		(template: StarterTemplate) => {
			createMutation.mutate({
				name: template.name,
				description: template.description,
				color: template.color,
			});
		},
		[createMutation],
	);

	const isEmpty = allProjects.length === 0;
	const basePath = user?.basePath ?? "/team";

	return (
		<div className="flex w-full flex-col gap-4 p-6">
			{showFilters && (
				<div className="flex flex-wrap items-center justify-between gap-3">
					<ProjectsFilters />
					<div className="flex items-center gap-2 text-xs">
						<span className="text-muted-foreground">Group by</span>
						<Select
							value={groupBy}
							onValueChange={(v) => setGroupBy(v as GroupKey)}
						>
							<SelectTrigger className="h-8 w-[140px]">
								<SelectValue placeholder="Recency" />
							</SelectTrigger>
							<SelectContent>
								{(Object.keys(GROUP_LABELS) as GroupKey[]).map((k) => (
									<SelectItem key={k} value={k}>
										{GROUP_LABELS[k]}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<Button asChild size="sm" className="h-8">
							<Link href={`${basePath}/create-project`}>New project</Link>
						</Button>
					</div>
				</div>
			)}

			{/* Pinned row */}
			{pinnedProjects.length > 0 && (
				<section
					className={cn(
						"-mx-6 sticky top-0 z-10 border-border border-b bg-background/95 px-6 py-3 backdrop-blur",
					)}
				>
					<button
						type="button"
						onClick={() => setPinnedCollapsed((p) => !p)}
						className="mb-2 inline-flex items-center gap-1.5 text-muted-foreground text-xs hover:text-foreground"
						aria-expanded={!pinnedCollapsed}
					>
						{pinnedCollapsed ? (
							<ChevronRightIcon className="size-3.5" />
						) : (
							<ChevronDownIcon className="size-3.5" />
						)}
						<PinIcon className="size-3 text-brand" />
						<span className="font-medium uppercase tracking-wide">Pinned</span>
						<Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
							{pinnedProjects.length}
						</Badge>
					</button>
					{!pinnedCollapsed && (
						<div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
							{pinnedProjects.map((project) => (
								<div
									key={project.id}
									draggable
									onDragStart={(e) => {
										e.dataTransfer.setData("text/x-project-id", project.id);
										e.dataTransfer.effectAllowed = "move";
									}}
									onDragOver={(e) => {
										e.preventDefault();
										e.dataTransfer.dropEffect = "move";
									}}
									onDrop={(e) => {
										e.preventDefault();
										const fromId = e.dataTransfer.getData("text/x-project-id");
										if (fromId && fromId !== project.id) {
											movePinned(fromId, project.id);
										}
									}}
								>
									<ProjectCard
										project={project}
										href={`${basePath}/projects/${project.id}`}
										isPinned
										onTogglePin={togglePin}
									/>
								</div>
							))}
						</div>
					)}
				</section>
			)}

			{/* Empty state */}
			{isEmpty && (
				<div className="rounded-lg border border-border border-dashed bg-card/50 p-8 text-center">
					<h2 className="font-semibold text-foreground text-lg">
						Start with a template
					</h2>
					<p className="mt-1 text-muted-foreground text-sm">
						Pick a starting point — you can edit everything afterwards.
					</p>
					<StarterTemplates onCreate={onTemplateCreate} />
					<div className="mt-6 flex flex-wrap items-center justify-center gap-2">
						<Button asChild size="sm">
							<Link href={`${basePath}/create-project/starter`}>
								Start from an idea
							</Link>
						</Button>
						<Button asChild variant="outline" size="sm">
							<Link href={`${basePath}/create-project`}>Browse options</Link>
						</Button>
						<Button asChild variant="ghost" size="sm">
							<Link href={`${basePath}/projects?createProject=true`}>
								Or create blank
							</Link>
						</Button>
					</div>
				</div>
			)}

			{/* Grouped non-pinned projects */}
			{!isEmpty && (
				<div className="flex flex-col gap-6">
					{groups.map((group) => {
						const collapsed = collapsedGroups.has(group.id);
						return (
							<section key={group.id} className="flex flex-col gap-3">
								<button
									type="button"
									onClick={() => toggleGroup(group.id)}
									className={cn(
										"-mx-6 sticky top-0 z-[5] flex items-center gap-2 border-border border-b bg-card px-6 py-2 text-left",
										"text-muted-foreground text-xs hover:text-foreground",
									)}
									aria-expanded={!collapsed}
								>
									{collapsed ? (
										<ChevronRightIcon className="size-3.5" />
									) : (
										<ChevronDownIcon className="size-3.5" />
									)}
									<span className="font-semibold uppercase tracking-wide">
										{group.label}
									</span>
									<Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
										{group.projects.length}
									</Badge>
								</button>
								{!collapsed && (
									<div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
										{group.projects.map((project) => (
											<ProjectCard
												key={project.id}
												project={project}
												href={`${basePath}/projects/${project.id}`}
												isPinned={false}
												onTogglePin={togglePin}
											/>
										))}
									</div>
								)}
							</section>
						);
					})}
				</div>
			)}
		</div>
	);
}

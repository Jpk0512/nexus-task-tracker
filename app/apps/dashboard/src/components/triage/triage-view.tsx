"use client";

import {
	DndContext,
	type DragEndEvent,
	PointerSensor,
	useDroppable,
	useSensor,
	useSensors,
} from "@dnd-kit/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { JkHint } from "@/components/jk-hint";
import { useUser } from "@/components/user-provider";
import { useProjects, useStatuses, useTeamMembers } from "@/hooks/use-data";
import { invalidateTasksCache } from "@/hooks/use-data-cache-helpers";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { trpc } from "@/utils/trpc";
import { TriageCard, type TriageTask } from "./triage-card";

// Linear-style "Now / Next / Later" — cross-project priority surface.
// "Now"   = anything in_progress or review (assigned to me)
// "Next"  = to_do/planning (top 10 by priority)
// "Later" = backlog (all)

type ColumnKey = "now" | "next" | "later";

// Which status type a card drops into when moved to a given column.
// "Now" is the in-progress/review bucket — we pick in_progress as canonical
// since review is an exit state into done, not where new work lands.
const COLUMN_TARGET_TYPE: Record<
	ColumnKey,
	"in_progress" | "to_do" | "backlog"
> = {
	now: "in_progress",
	next: "to_do",
	later: "backlog",
};

function DroppableColumn({
	columnId,
	title,
	subtitle,
	tasks,
	team,
	teamPrefix,
	focusedId,
	isLoading = false,
}: {
	columnId: ColumnKey;
	title: string;
	subtitle: string;
	tasks: TriageTask[];
	team: string;
	teamPrefix?: string | null;
	focusedId?: string | null;
	isLoading?: boolean;
}) {
	const { setNodeRef, isOver } = useDroppable({ id: columnId });
	return (
		<div
			ref={setNodeRef}
			className={cn(
				"flex min-w-0 flex-col rounded-md border border-border bg-white/[0.02] transition-colors",
				isOver && "border-ring/60 bg-accent/30",
			)}
		>
			<div className="border-border border-b px-3 py-2">
				<div className="flex items-baseline justify-between">
					<h2 className="font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						{title}
					</h2>
					<span className="text-[11px] text-muted-foreground tabular-nums">
						{isLoading ? "" : tasks.length}
					</span>
				</div>
				<p className="text-[11px] text-muted-foreground">{subtitle}</p>
			</div>
			<ul className="grow space-y-0.5 overflow-y-auto p-1.5">
				{isLoading && (
					<>
						{Array.from({ length: 5 }).map((_, i) => (
							<li
								// biome-ignore lint/suspicious/noArrayIndexKey: stable
								key={i}
								className="rounded-md border border-transparent px-2 py-1.5"
							>
								<div className="flex items-center gap-1.5">
									<Skeleton className="h-2.5 w-4 rounded-sm" />
									<Skeleton className="size-3.5 rounded-sm" />
								</div>
								<div className="mt-1.5 flex items-baseline gap-1.5">
									<Skeleton className="h-3 w-10 rounded-sm" />
									<Skeleton
										className="h-3"
										style={{ width: `${55 + ((i * 9) % 35)}%` }}
									/>
								</div>
								<div className="mt-1.5 flex items-center gap-1.5">
									<Skeleton className="h-2.5 w-16 rounded-sm" />
									<Skeleton className="ml-auto size-4 rounded-full" />
								</div>
							</li>
						))}
					</>
				)}
				{!isLoading && tasks.length === 0 && (
					<li className="px-2 py-3 text-center text-muted-foreground text-xs italic">
						Nothing here.
					</li>
				)}
				{!isLoading &&
					tasks.map((t) => (
						<li key={t.id}>
							<TriageCard
								task={t}
								team={team}
								teamPrefix={teamPrefix}
								isFocused={focusedId === t.id}
							/>
						</li>
					))}
			</ul>
		</div>
	);
}

export function TriageView() {
	const { team } = useParams<{ team: string }>();

	// Pull three slices via the existing tasks.get query — leans on the
	// statusType filter we already have so we don't need a custom endpoint.
	const nowQuery = useQuery(
		trpc.tasks.get.queryOptions({
			statusType: ["in_progress", "review"],
			pageSize: 50,
		} as any),
	);
	const nextQuery = useQuery(
		trpc.tasks.get.queryOptions({
			statusType: ["to_do"],
			pageSize: 50,
		} as any),
	);
	const laterQuery = useQuery(
		trpc.tasks.get.queryOptions({
			statusType: ["backlog"],
			pageSize: 100,
		} as any),
	);

	const { data: statusesData } = useStatuses();
	const { data: projectsData } = useProjects();
	const { data: membersData } = useTeamMembers();
	const user = useUser();

	// tasks.get does not nest project/status/assignee — we denormalize here so
	// the card can render the Linear chip row (project dot · due · avatar).
	const statusList = useMemo<Array<any>>(
		() => ((statusesData as any)?.data ?? statusesData ?? []) as any[],
		[statusesData],
	);
	const projectList = useMemo<Array<any>>(
		() => ((projectsData as any)?.data ?? projectsData ?? []) as any[],
		[projectsData],
	);
	const memberList = useMemo<Array<any>>(
		() => ((membersData as any)?.data ?? membersData ?? []) as any[],
		[membersData],
	);

	const statusById = useMemo(() => {
		const m = new Map<string, any>();
		for (const s of statusList) m.set(s.id, s);
		return m;
	}, [statusList]);
	const projectById = useMemo(() => {
		const m = new Map<string, any>();
		for (const p of projectList) m.set(p.id, p);
		return m;
	}, [projectList]);
	const memberById = useMemo(() => {
		const m = new Map<string, any>();
		for (const u of memberList) m.set(u.id, u);
		return m;
	}, [memberList]);

	// Lookup: for each column-target status type, the first matching Status row.
	// Drag-drop mutates statusId on the task, so we need a concrete row id.
	const statusByType = useMemo(() => {
		const map: Partial<Record<"in_progress" | "to_do" | "backlog", string>> =
			{};
		for (const s of statusList as Array<{ id: string; type: string }>) {
			if (s.type === "in_progress" && !map.in_progress) map.in_progress = s.id;
			if (s.type === "to_do" && !map.to_do) map.to_do = s.id;
			if (s.type === "backlog" && !map.backlog) map.backlog = s.id;
		}
		return map;
	}, [statusList]);

	const enrich = (raw: any): TriageTask => {
		const status = raw.statusId ? statusById.get(raw.statusId) : null;
		const project = raw.projectId ? projectById.get(raw.projectId) : null;
		const assignee = raw.assigneeId ? memberById.get(raw.assigneeId) : null;
		return {
			...raw,
			status: status
				? { id: status.id, name: status.name, type: status.type }
				: (raw.status ?? null),
			project: project
				? {
						id: project.id,
						name: project.name,
						prefix: project.prefix,
						color: project.color,
					}
				: (raw.project ?? null),
			assignee: assignee
				? {
						id: assignee.id,
						name: assignee.name,
						email: assignee.email,
						image: assignee.image,
						color: assignee.color,
					}
				: (raw.assignee ?? null),
		};
	};

	// Local overlay of in-flight column moves. Keyed by task id; value is the
	// column we're optimistically moving INTO. Cleared on success/error so the
	// server-fetched data resumes being canonical.
	const [pendingMoves, setPendingMoves] = useState<Record<string, ColumnKey>>(
		{},
	);
	const qc = useQueryClient();

	const updateTask = useMutation(
		trpc.tasks.update.mutationOptions({
			onSuccess: () => {
				// Don't clear `pendingMoves` here — the refetch we kick off below
				// races against React paint. Instead, leave the overlay in place
				// and let the effect that watches rawNow/Next/Later strip the
				// entry once the server data agrees (which always lands within a
				// single tick once the query refires). Net effect: zero flicker.
				invalidateTasksCache();
			},
			onError: (_err: unknown, variables: unknown) => {
				// Roll back the overlay AND surface error toast.
				setPendingMoves((prev) => {
					const next = { ...prev };
					delete next[(variables as { id: string }).id];
					return next;
				});
				toast.error("Couldn't move task", { id: "triage-move" });
			},
		}),
	);

	const extractTasks = (q: any): TriageTask[] => {
		const d = q?.data?.data ?? q?.data?.pages?.[0]?.data ?? [];
		return Array.isArray(d) ? (d as any[]).map(enrich) : [];
	};

	const rawNowTasks = extractTasks(nowQuery);
	const rawNextTasks = extractTasks(nextQuery);
	const rawLaterTasks = extractTasks(laterQuery);

	// Once a task's refetched cache slot agrees with its pending column, drop
	// the overlay entry. This is the only way to avoid flicker between "mutation
	// succeeded → overlay cleared → refetch hasn't repopulated the column yet".
	// Using rawIds (not the overlay-fused lists) so we only react to *server*
	// data movement.
	useEffect(() => {
		if (Object.keys(pendingMoves).length === 0) return;
		const byCol: Record<ColumnKey, Set<string>> = {
			now: new Set(rawNowTasks.map((t) => t.id)),
			next: new Set(rawNextTasks.map((t) => t.id)),
			later: new Set(rawLaterTasks.map((t) => t.id)),
		};
		setPendingMoves((prev) => {
			let changed = false;
			const next = { ...prev };
			for (const [taskId, target] of Object.entries(prev)) {
				if (byCol[target].has(taskId)) {
					delete next[taskId];
					changed = true;
				}
			}
			return changed ? next : prev;
		});
	}, [rawNowTasks, rawNextTasks, rawLaterTasks, pendingMoves]);

	// Apply the pendingMoves overlay: a task may be visually present in a
	// different column than the cache says, until the mutation settles. Strip
	// each task out of its source column and inject into its pending target.
	const { nowTasks, nextTasks, laterTasks } = useMemo(() => {
		const moved: Record<ColumnKey, TriageTask[]> = {
			now: [],
			next: [],
			later: [],
		};
		const allRaw = {
			now: rawNowTasks,
			next: rawNextTasks,
			later: rawLaterTasks,
		};
		const tasksMovedAway = new Set<string>();
		for (const [taskId, target] of Object.entries(pendingMoves)) {
			tasksMovedAway.add(taskId);
			// Find the task object from any column so we can clone it into its
			// new home. If it doesn't exist in the cache anymore, just skip.
			const task =
				allRaw.now.find((t) => t.id === taskId) ??
				allRaw.next.find((t) => t.id === taskId) ??
				allRaw.later.find((t) => t.id === taskId);
			if (task) moved[target].push(task);
		}
		const filter = (list: TriageTask[]) =>
			list.filter((t) => !tasksMovedAway.has(t.id));
		return {
			nowTasks: [...moved.now, ...filter(rawNowTasks)],
			nextTasks: [...moved.next, ...filter(rawNextTasks)],
			laterTasks: [...moved.later, ...filter(rawLaterTasks)],
		};
	}, [pendingMoves, rawNowTasks, rawNextTasks, rawLaterTasks]);

	const teamPrefix = (user as any)?.team?.prefix ?? null;
	// Skeleton only on the *initial* load (before any data has arrived).
	// Subsequent refetches keep the existing cards visible.
	const isInitialLoading =
		nowQuery.isLoading || nextQuery.isLoading || laterQuery.isLoading;
	// Silence unused warning for qc in case the optimistic path is the only consumer.
	void qc;

	// Linear-style scope chip strip — narrow the 3 columns to one project,
	// or stay on "All projects". Filters only the rendered task lists; the
	// underlying queries still pull everything so chip flips are instant.
	const [scopeProjectId, setScopeProjectId] = useState<string | null>(null);
	const filterByScope = (list: TriageTask[]) =>
		scopeProjectId
			? list.filter((t) => t.project?.id === scopeProjectId)
			: list;
	const scopedNow = filterByScope(nowTasks);
	const scopedNext = filterByScope(nextTasks);
	const scopedLater = filterByScope(laterTasks);
	// Only render chips for projects that actually have a card in the surface;
	// otherwise every workspace project would clutter the strip.
	const projectCounts = useMemo(() => {
		const counts = new Map<string, number>();
		for (const t of [...nowTasks, ...nextTasks, ...laterTasks]) {
			if (t.project?.id) {
				counts.set(t.project.id, (counts.get(t.project.id) ?? 0) + 1);
			}
		}
		return projectList
			.filter((p) => counts.has(p.id))
			.map((p) => ({
				id: p.id as string,
				name: (p.name as string) ?? "Untitled",
				color: (p.color as string | null) ?? null,
				count: counts.get(p.id) ?? 0,
			}));
	}, [nowTasks, nextTasks, laterTasks, projectList]);
	const totalScopeCount =
		nowTasks.length + nextTasks.length + laterTasks.length;

	// j/k navigation walks Now → Next → Later in order, *within the current
	// scope* — so flipping a scope chip narrows the focus ring set too.
	// Enter opens the focused task's detail page via window.location.
	const jkIds = useMemo(
		() => [...scopedNow, ...scopedNext, ...scopedLater].map((t) => t.id),
		[scopedNow, scopedNext, scopedLater],
	);
	const taskByIdMap = useMemo(() => {
		const m = new Map<string, TriageTask>();
		for (const t of [...scopedNow, ...scopedNext, ...scopedLater])
			m.set(t.id, t);
		return m;
	}, [scopedNow, scopedNext, scopedLater]);
	const jk = useJkNavigation({
		ids: jkIds,
		onOpen: (id) => {
			const t = taskByIdMap.get(id);
			if (!t) return;
			window.location.href = `/team/${team}/t/${t.permalinkId ?? t.id}`;
		},
		toastLabel: (id) => {
			const t = taskByIdMap.get(id);
			if (!t) return null;
			const prefix = t.project?.prefix ?? teamPrefix ?? null;
			if (prefix && t.sequence != null) return `Opened ${prefix}-${t.sequence}`;
			return `Opened ${t.title}`;
		},
	});

	// Build a flat task lookup so onDragEnd can resolve `active.id` -> task.
	// (Uses the unscoped lists so dragging never silently drops a card that
	// the user briefly filters out mid-drag.)
	const taskById = useMemo(() => {
		const m = new Map<string, TriageTask>();
		for (const t of [...nowTasks, ...nextTasks, ...laterTasks]) m.set(t.id, t);
		return m;
	}, [nowTasks, nextTasks, laterTasks]);

	const sensors = useSensors(
		useSensor(PointerSensor, {
			activationConstraint: { distance: 5 },
		}),
	);

	const handleDragEnd = (event: DragEndEvent) => {
		const { active, over } = event;
		if (!over) return;
		const targetCol = over.id as ColumnKey;
		const task = taskById.get(active.id as string);
		if (!task) return;

		const targetType = COLUMN_TARGET_TYPE[targetCol];
		if (!targetType) return;

		// No-op if dropping a card back on its own column (current statusType
		// already matches). Saves a round-trip + spurious toast.
		if (task.status?.type === targetType) return;
		// "Now" column accepts both in_progress and review — don't bounce a
		// review task back to in_progress.
		if (targetCol === "now" && task.status?.type === "review") return;

		const targetStatusId = statusByType[targetType];
		if (!targetStatusId) {
			toast.error("No matching status configured", { id: "triage-move" });
			return;
		}

		// Optimistic UI: the card visually jumps to the target column now, no
		// spinner toast required. Mutation runs in the background; rollback
		// happens in updateTask.onError if the server rejects.
		setPendingMoves((prev) => ({ ...prev, [task.id]: targetCol }));
		updateTask.mutate({ id: task.id, statusId: targetStatusId } as any, {
			onSuccess: () => {
				// Minimal confirmation — keeps Linear's "silent success" feel
				// while still letting power users undo via their muscle memory.
				toast.success(`Moved to ${columnTitle(targetCol)}`, {
					id: "triage-move",
					duration: 1500,
				});
			},
		});
	};

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<div className="flex items-baseline justify-between gap-4">
					<div>
						<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							Now / Next / Later
						</h1>
						<p className="mt-0.5 text-[12px] text-muted-foreground">
							Cross-project priority surface. Pulls live from all projects under
							this workspace. Drag a card between columns to change its status.
						</p>
					</div>
					<JkHint />
				</div>
				{/* Scope chip strip — Linear's "All / Just <project>" segmented
				    control above any cross-project surface. Hidden until we know
				    there's more than one project to switch between. */}
				{projectCounts.length > 1 && (
					<ScopeChips
						projects={projectCounts}
						totalCount={totalScopeCount}
						selected={scopeProjectId}
						onSelect={setScopeProjectId}
					/>
				)}
			</header>
			<DndContext sensors={sensors} onDragEnd={handleDragEnd}>
				<div className="grid grow grid-cols-1 gap-3 overflow-hidden px-6 py-4 md:grid-cols-3">
					<DroppableColumn
						columnId="now"
						title="Now"
						subtitle="In progress + review across all projects"
						tasks={scopedNow}
						team={team}
						teamPrefix={teamPrefix}
						focusedId={jk.focusedId}
						isLoading={isInitialLoading && scopedNow.length === 0}
					/>
					<DroppableColumn
						columnId="next"
						title="Next"
						subtitle="To-do / planning — pick the next move"
						tasks={scopedNext}
						team={team}
						teamPrefix={teamPrefix}
						focusedId={jk.focusedId}
						isLoading={isInitialLoading && scopedNext.length === 0}
					/>
					<DroppableColumn
						columnId="later"
						title="Later"
						subtitle="Backlog. Not now."
						tasks={scopedLater}
						team={team}
						teamPrefix={teamPrefix}
						focusedId={jk.focusedId}
						isLoading={isInitialLoading && scopedLater.length === 0}
					/>
				</div>
			</DndContext>
		</div>
	);
}

// Scope chips: "All projects · 23" then one per project with its card count.
// Clicking the active chip again resets to All — matches Linear's "click to
// toggle" behaviour on filter chips.
function ScopeChips({
	projects,
	totalCount,
	selected,
	onSelect,
}: {
	projects: { id: string; name: string; color: string | null; count: number }[];
	totalCount: number;
	selected: string | null;
	onSelect: (id: string | null) => void;
}) {
	return (
		<div
			role="tablist"
			aria-label="Filter triage by project"
			className="mt-2.5 flex flex-wrap items-center gap-1.5"
		>
			<button
				type="button"
				role="tab"
				aria-selected={selected === null}
				onClick={() => onSelect(null)}
				className={cn(
					"inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-[11px] transition-colors",
					selected === null
						? "border-border bg-secondary text-foreground"
						: "border-transparent bg-transparent text-muted-foreground hover:border-border hover:bg-accent/50 hover:text-foreground",
				)}
			>
				All projects
				<span className="text-muted-foreground tabular-nums">{totalCount}</span>
			</button>
			{projects.map((p) => {
				const active = p.id === selected;
				return (
					<button
						key={p.id}
						type="button"
						role="tab"
						aria-selected={active}
						onClick={() => onSelect(active ? null : p.id)}
						className={cn(
							"inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-[11px] transition-colors",
							active
								? "border-border bg-secondary text-foreground"
								: "border-transparent bg-transparent text-muted-foreground hover:border-border hover:bg-accent/50 hover:text-foreground",
						)}
					>
						<span
							aria-hidden="true"
							className="size-1.5 shrink-0 rounded-full"
							style={{
								backgroundColor: p.color || "var(--muted-foreground)",
							}}
						/>
						{p.name}
						<span className="text-muted-foreground tabular-nums">
							{p.count}
						</span>
					</button>
				);
			})}
		</div>
	);
}

function columnTitle(c: ColumnKey): string {
	return c === "now" ? "Now" : c === "next" ? "Next" : "Later";
}

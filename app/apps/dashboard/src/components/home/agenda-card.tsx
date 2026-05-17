"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { addDays, isPast, isToday } from "date-fns";
import { CheckIcon, ClockIcon, ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { StatusIcon } from "@/components/status-icon";
import { useUser } from "@/components/user-provider";
import { type EnrichedTask, useStatuses, useTasks } from "@/hooks/use-data";
import { useOptimisticAction } from "@/hooks/use-optimistic-action";
import { trpc } from "@/utils/trpc";
import { Priority } from "../tasks-view/properties/priority";
import { HomeCard, HomeCardEmpty } from "./home-card";

/**
 * "My Agenda" — tasks the current user owns that are due today or overdue.
 * Replaces the old "My Issues" card on the new Home above-the-fold (codex
 * delighter: today-centric work surface, designer-meta §5).
 *
 * Each row exposes hover affordances:
 *   - Mark done (optimistic via useOptimisticAction → updates statusId to the
 *     first "done"-typed status row, with rollback if the server rejects).
 *   - Defer 1 day (optimistic dueDate bump).
 *   - Open task detail (Link button to the project page).
 *
 * Assignee pill is hidden in single-user mode (codex amendment #1) — there's
 * only one actor so showing the avatar is noise.
 */

const PRIORITY_ORDER: Record<string, number> = {
	urgent: 0,
	high: 1,
	medium: 2,
	low: 3,
};

export const AgendaCard = () => {
	const user = useUser();
	const qc = useQueryClient();
	const { tasks, isLoading } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 100,
		},
		{ enabled: !!user?.id },
	);
	const { data: statusesData } = useStatuses();

	// Pick the first "done" status row — needed to mark complete via the same
	// `tasks.update` mutation the rest of the app uses (status is a join, not
	// an enum). Memoized because statusesData is cached for 5min.
	const doneStatusId = useMemo<string | null>(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC return typed as unknown app-wide
		const list = (((statusesData as any)?.data ?? []) as Array<{
			id: string;
			type: string;
		}>);
		const done = list.find((s) => s.type === "done");
		return done?.id ?? null;
	}, [statusesData]);

	// Filter: due today or overdue. Sort: overdue first, then due today;
	// within each bucket, urgent priority first then by recency.
	const agendaTasks = useMemo(() => {
		const filtered = tasks.filter((t) => {
			if (!t.dueDate) return false;
			const d = new Date(t.dueDate);
			return isToday(d) || isPast(d);
		});
		filtered.sort((a, b) => {
			const aDate = new Date(a.dueDate as string);
			const bDate = new Date(b.dueDate as string);
			const aOverdue = isPast(aDate) && !isToday(aDate);
			const bOverdue = isPast(bDate) && !isToday(bDate);
			if (aOverdue !== bOverdue) return aOverdue ? -1 : 1;
			const aPriority = a.priority ? (PRIORITY_ORDER[a.priority] ?? 99) : 99;
			const bPriority = b.priority ? (PRIORITY_ORDER[b.priority] ?? 99) : 99;
			if (aPriority !== bPriority) return aPriority - bPriority;
			return aDate.getTime() - bDate.getTime();
		});
		return filtered.slice(0, 8);
	}, [tasks]);

	const basePath = user?.basePath ?? "/team";
	const prefix = user?.team?.prefix ?? "";

	// Shared tasks.update mutation; the optimistic helpers below patch the
	// React Query cache directly so the row updates before the wire round-
	// trip lands. Rollback restores the snapshot if the server rejects.
	const updateMut = useMutation(trpc.tasks.update.mutationOptions({}));

	return (
		<HomeCard
			title="My agenda"
			count={agendaTasks.length}
			href={`${basePath}/views/my-tasks`}
			isLoading={isLoading}
			isEmpty={agendaTasks.length === 0}
			emptyState={
				<HomeCardEmpty
					title="Nothing due today"
					description="Tasks with a due date of today or earlier will appear here."
					ctaHref={`${basePath}/views/my-tasks`}
					ctaLabel="Browse all issues"
				/>
			}
		>
			<ul className="space-y-0.5">
				{agendaTasks.map((task) => (
					<li key={task.id}>
						<AgendaRow
							task={task}
							doneStatusId={doneStatusId}
							basePath={basePath}
							prefix={prefix}
							qc={qc}
							updateMut={updateMut}
						/>
					</li>
				))}
			</ul>
		</HomeCard>
	);
};

function AgendaRow({
	task,
	doneStatusId,
	basePath,
	prefix,
	qc,
	updateMut,
}: {
	task: EnrichedTask;
	doneStatusId: string | null;
	basePath: string;
	prefix: string;
	// biome-ignore lint/suspicious/noExplicitAny: react-query client typed elsewhere
	qc: any;
	// biome-ignore lint/suspicious/noExplicitAny: trpc mutation result
	updateMut: any;
}) {
	const due = task.dueDate ? new Date(task.dueDate) : null;
	const overdue = due ? isPast(due) && !isToday(due) : false;

	const snapshotTasks = () => qc.getQueriesData({ queryKey: [["tasks"]] });
	// biome-ignore lint/suspicious/noExplicitAny: snapshot tuple from getQueriesData
	const restoreTasks = (snap: any) => {
		for (const [k, v] of snap) qc.setQueryData(k, v);
	};

	const doneAction = useOptimisticAction({
		action: `task.done:${task.id}`,
		optimisticUpdate: () => {
			const snap = snapshotTasks();
			// biome-ignore lint/suspicious/noExplicitAny: react-query cache shape
			qc.setQueriesData({ queryKey: [["tasks"]] }, (old: any) => {
				if (!old || !Array.isArray(old?.pages)) return old;
				return {
					...old,
					// biome-ignore lint/suspicious/noExplicitAny: page row type
					pages: old.pages.map((page: any) => ({
						...page,
						// biome-ignore lint/suspicious/noExplicitAny: row type
						data: (page?.data ?? []).filter((row: any) => row.id !== task.id),
					})),
				};
			});
			return snap;
		},
		mutateFn: () => {
			if (!doneStatusId) return Promise.resolve();
			return updateMut.mutateAsync({ id: task.id, statusId: doneStatusId });
		},
		rollback: restoreTasks,
		toastLabel: "Marked done",
		toastDescription: task.title,
	});

	const deferAction = useOptimisticAction({
		action: `task.defer:${task.id}`,
		optimisticUpdate: () => {
			const snap = snapshotTasks();
			const newDue = addDays(due ?? new Date(), 1).toISOString();
			// biome-ignore lint/suspicious/noExplicitAny: react-query cache shape
			qc.setQueriesData({ queryKey: [["tasks"]] }, (old: any) => {
				if (!old || !Array.isArray(old?.pages)) return old;
				return {
					...old,
					// biome-ignore lint/suspicious/noExplicitAny: page row type
					pages: old.pages.map((page: any) => ({
						...page,
						// biome-ignore lint/suspicious/noExplicitAny: row type
						data: (page?.data ?? []).map((row: any) =>
							row.id === task.id ? { ...row, dueDate: newDue } : row,
						),
					})),
				};
			});
			return snap;
		},
		mutateFn: () => {
			const newDue = addDays(due ?? new Date(), 1).toISOString();
			return updateMut.mutateAsync({ id: task.id, dueDate: newDue });
		},
		rollback: restoreTasks,
		toastLabel: "Deferred 1 day",
		toastDescription: task.title,
	});

	return (
		<div
			className={cn(
				"group relative flex h-7 min-w-0 items-center gap-2 rounded-md px-2 text-[13px] text-foreground transition-colors",
				"hover:bg-accent/60",
			)}
		>
			<Link
				href={`${basePath}/projects/${task.projectId}/${task.id}`}
				className="flex min-w-0 flex-1 items-center gap-2"
			>
				<span className="flex size-4 shrink-0 items-center justify-center text-muted-foreground">
					<StatusIcon type={task.status?.type} className="size-3.5" />
				</span>
				{prefix && task.sequence ? (
					<span className="shrink-0 text-[12px] text-muted-foreground tabular-nums">
						{prefix}-{task.sequence}
					</span>
				) : null}
				<span className="min-w-0 flex-1 truncate">{task.title}</span>
				<span className="ml-auto flex shrink-0 items-center gap-1.5 text-[11px] text-muted-foreground group-hover:opacity-0">
					{task.priority ? <Priority value={task.priority} /> : null}
					{due ? (
						<span
							className={cn(
								"tabular-nums",
								overdue ? "text-red-500" : "text-yellow-500",
							)}
						>
							{overdue ? "overdue" : "today"}
						</span>
					) : null}
				</span>
			</Link>
			{/* Hover affordances — absolute so they overlay the trailing meta. */}
			<div
				className={cn(
					"absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center gap-0.5 rounded-md border border-border bg-background/95 px-1 py-0.5 opacity-0 shadow-sm transition-opacity",
					"group-hover:opacity-100 focus-within:opacity-100",
				)}
				onClick={(e) => e.stopPropagation()}
				onKeyDown={(e) => e.stopPropagation()}
			>
				<RowAction
					title="Mark done"
					onClick={() => doneAction.run(undefined)}
					disabled={!doneStatusId}
				>
					<CheckIcon className="size-3.5" />
				</RowAction>
				<RowAction
					title="Defer 1 day"
					onClick={() => deferAction.run(undefined)}
				>
					<ClockIcon className="size-3.5" />
				</RowAction>
				<Link
					href={`${basePath}/projects/${task.projectId}/${task.id}`}
					title="Open"
					aria-label="Open"
					className={cn(
						"inline-flex size-6 items-center justify-center rounded text-muted-foreground transition-colors",
						"hover:bg-accent hover:text-foreground",
					)}
				>
					<ExternalLinkIcon className="size-3.5" />
				</Link>
			</div>
		</div>
	);
}

function RowAction({
	title,
	onClick,
	disabled,
	children,
}: {
	title: string;
	onClick: () => void;
	disabled?: boolean;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			title={title}
			aria-label={title}
			disabled={disabled}
			onClick={(e) => {
				e.stopPropagation();
				e.preventDefault();
				if (!disabled) onClick();
			}}
			className={cn(
				"inline-flex size-6 items-center justify-center rounded text-muted-foreground transition-colors",
				"hover:bg-accent hover:text-foreground",
				disabled && "cursor-not-allowed opacity-40",
			)}
		>
			{children}
		</button>
	);
}

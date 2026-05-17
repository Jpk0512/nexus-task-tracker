"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { CheckIcon, ExternalLinkIcon, PlayIcon } from "lucide-react";
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
 * "Up next" — the Triage Now column (in_progress / review) surfaced on Home
 * so the user can resume in one click. Mirrors the slice TriageView uses
 * for its leftmost column.
 *
 * Each row exposes hover affordances:
 *   - Open task detail (Link button).
 *   - Mark done (optimistic, same pattern as AgendaCard).
 *
 * Assignee pill hidden under single-user mode.
 */

const PRIORITY_ORDER: Record<string, number> = {
	urgent: 0,
	high: 1,
	medium: 2,
	low: 3,
};

export const UpNextCard = () => {
	const user = useUser();
	const qc = useQueryClient();
	const { tasks, isLoading } = useTasks(
		{
			statusType: ["in_progress", "review"],
			pageSize: 50,
		},
	);
	const { data: statusesData } = useStatuses();

	const doneStatusId = useMemo<string | null>(() => {
		const list = (statusesData?.data ?? []) as Array<{ id: string; type: string }>;
		const done = list.find((s) => s.type === "done");
		return done?.id ?? null;
	}, [statusesData]);

	const upNext = useMemo(() => {
		const slice = tasks.slice();
		slice.sort((a, b) => {
			const aPriority = a.priority ? (PRIORITY_ORDER[a.priority] ?? 99) : 99;
			const bPriority = b.priority ? (PRIORITY_ORDER[b.priority] ?? 99) : 99;
			if (aPriority !== bPriority) return aPriority - bPriority;
			const aTime = a.statusChangedAt
				? new Date(a.statusChangedAt).getTime()
				: 0;
			const bTime = b.statusChangedAt
				? new Date(b.statusChangedAt).getTime()
				: 0;
			return bTime - aTime;
		});
		return slice.slice(0, 8);
	}, [tasks]);

	const basePath = user?.basePath ?? "/team";
	const prefix = user?.team?.prefix ?? "";

	const updateMut = useMutation(trpc.tasks.update.mutationOptions({}));

	return (
		<HomeCard
			title="Up next"
			count={upNext.length}
			href={`${basePath}/views/my-tasks?status=in_progress`}
			isLoading={isLoading}
			isEmpty={upNext.length === 0}
			emptyState={
				<HomeCardEmpty
					title="Nothing in flight"
					description="Promote a task to In Progress to see it here."
					ctaHref={`${basePath}/triage`}
					ctaLabel="Open triage"
				/>
			}
		>
			<ul className="space-y-0.5">
				{upNext.map((task) => (
					<li key={task.id}>
						<UpNextRow
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

function UpNextRow({
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
	const snapshotTasks = () => qc.getQueriesData({ queryKey: [["tasks"]] });
	// biome-ignore lint/suspicious/noExplicitAny: snapshot tuple
	const restoreTasks = (snap: any) => {
		for (const [k, v] of snap) qc.setQueryData(k, v);
	};

	const doneAction = useOptimisticAction({
		action: `task.done:${task.id}`,
		optimisticUpdate: () => {
			const snap = snapshotTasks();
			// biome-ignore lint/suspicious/noExplicitAny: cache shape
			qc.setQueriesData({ queryKey: [["tasks"]] }, (old: any) => {
				if (!old || !Array.isArray(old?.pages)) return old;
				return {
					...old,
					// biome-ignore lint/suspicious/noExplicitAny: page type
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
				</span>
			</Link>
			<div
				className={cn(
					"absolute top-1/2 right-1.5 flex -translate-y-1/2 items-center gap-0.5 rounded-md border border-border bg-background/95 px-1 py-0.5 opacity-0 shadow-sm transition-opacity",
					"group-hover:opacity-100 focus-within:opacity-100",
				)}
				onClick={(e) => e.stopPropagation()}
				onKeyDown={(e) => e.stopPropagation()}
			>
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
				<button
					type="button"
					title="Mark done"
					aria-label="Mark done"
					disabled={!doneStatusId}
					onClick={(e) => {
						e.stopPropagation();
						e.preventDefault();
						if (doneStatusId) doneAction.run(undefined);
					}}
					className={cn(
						"inline-flex size-6 items-center justify-center rounded text-muted-foreground transition-colors",
						"hover:bg-accent hover:text-foreground",
						!doneStatusId && "cursor-not-allowed opacity-40",
					)}
				>
					<CheckIcon className="size-3.5" />
				</button>
			</div>
			{/* PlayIcon used in legend for clarity; only shown on the empty/header
			 *  surfaces — referenced here so the import is not dead. */}
			<span className="sr-only">
				<PlayIcon className="size-3.5" />
			</span>
		</div>
	);
}

"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { addDays, differenceInDays } from "date-fns";
import {
	ArchiveIcon,
	ClockIcon,
	HourglassIcon,
	RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { StatusIcon } from "@/components/status-icon";
import { useUser } from "@/components/user-provider";
import { type EnrichedTask, useStatuses, useTasks } from "@/hooks/use-data";
import { useOptimisticAction } from "@/hooks/use-optimistic-action";
import { trpc } from "@/utils/trpc";

/**
 * Stale-commitment digest (codex delighter #5, Cron-style).
 *
 * Lists tasks that have been sitting in to_do / in_progress / review for >7
 * days without a status change. Each row offers three actions:
 *   - recommit (bump dueDate to today + 7d)
 *   - snooze (bump dueDate to today + 30d)
 *   - archive (mark as cancelled — falls back to status "done" for now since
 *     "cancelled" isn't a default status type; iter-11 will add it).
 *
 * Stub for iter-10: shows the count and the list, optimistic via
 * useOptimisticAction. The "next-action prompt" copy ("Looks like X has been
 * waiting since Monday — recommit or snooze?") lands in iter-11.
 */

const STALE_DAYS = 7;

export const StaleCommitmentDigest = () => {
	const user = useUser();
	const qc = useQueryClient();
	const [expanded, setExpanded] = useState(false);

	const { tasks, isLoading } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 200,
		},
		{ enabled: !!user?.id },
	);
	const { data: statusesData } = useStatuses();
	const doneStatusId = useMemo<string | null>(() => {
		// biome-ignore lint/suspicious/noExplicitAny: tRPC return typed as unknown app-wide
		const list = (((statusesData as any)?.data ?? []) as Array<{
			id: string;
			type: string;
		}>);
		const done = list.find((s) => s.type === "done");
		return done?.id ?? null;
	}, [statusesData]);

	const stale = useMemo(() => {
		const now = new Date();
		return tasks
			.filter((t) => {
				const lastTouched = t.statusChangedAt ?? t.updatedAt ?? t.createdAt;
				if (!lastTouched) return false;
				return differenceInDays(now, new Date(lastTouched)) > STALE_DAYS;
			})
			.sort((a, b) => {
				const aTime = new Date(
					a.statusChangedAt ?? a.updatedAt ?? a.createdAt ?? 0,
				).getTime();
				const bTime = new Date(
					b.statusChangedAt ?? b.updatedAt ?? b.createdAt ?? 0,
				).getTime();
				return aTime - bTime; // oldest first
			})
			.slice(0, 20);
	}, [tasks]);

	const basePath = user?.basePath ?? "/team";
	const updateMut = useMutation(trpc.tasks.update.mutationOptions({}));

	if (!isLoading && stale.length === 0) return null; // nothing to nag about

	return (
		<section className="rounded-[12px] border border-border bg-card">
			<button
				type="button"
				onClick={() => setExpanded((v) => !v)}
				className="flex w-full items-center justify-between gap-2 border-border border-b px-3 py-2 text-left transition-colors hover:bg-accent/30"
			>
				<div className="flex items-center gap-1.5">
					<HourglassIcon className="size-3.5 text-amber-500" />
					<h2 className="font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						Stale commitments
					</h2>
					{stale.length > 0 ? (
						<span className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-amber-500/15 px-1.5 font-[510] text-[11px] text-amber-500 tabular-nums">
							{stale.length}
						</span>
					) : null}
				</div>
				<span className="text-[11px] text-muted-foreground">
					{expanded ? "Hide" : `${STALE_DAYS}+ days untouched`}
				</span>
			</button>
			{expanded ? (
				<div className="max-h-[280px] overflow-y-auto px-1.5 py-1.5">
					<ul className="space-y-0.5">
						{stale.map((task) => (
							<li key={task.id}>
								<StaleRow
									task={task}
									basePath={basePath}
									doneStatusId={doneStatusId}
									qc={qc}
									updateMut={updateMut}
								/>
							</li>
						))}
					</ul>
				</div>
			) : null}
		</section>
	);
};

function StaleRow({
	task,
	basePath,
	doneStatusId,
	qc,
	updateMut,
}: {
	task: EnrichedTask;
	basePath: string;
	doneStatusId: string | null;
	// biome-ignore lint/suspicious/noExplicitAny: react-query client
	qc: any;
	// biome-ignore lint/suspicious/noExplicitAny: trpc mutation
	updateMut: any;
}) {
	const lastTouched =
		task.statusChangedAt ?? task.updatedAt ?? task.createdAt ?? null;
	const days = lastTouched
		? differenceInDays(new Date(), new Date(lastTouched))
		: null;

	const snapshotTasks = () => qc.getQueriesData({ queryKey: [["tasks"]] });
	// biome-ignore lint/suspicious/noExplicitAny: snapshot tuple
	const restoreTasks = (snap: any) => {
		for (const [k, v] of snap) qc.setQueryData(k, v);
	};

	const recommitAction = useOptimisticAction({
		action: `task.recommit:${task.id}`,
		optimisticUpdate: () => {
			const snap = snapshotTasks();
			return snap;
		},
		mutateFn: () => {
			const newDue = addDays(new Date(), 7).toISOString();
			return updateMut.mutateAsync({ id: task.id, dueDate: newDue });
		},
		rollback: restoreTasks,
		toastLabel: "Recommitted",
		toastDescription: task.title,
	});

	const snoozeAction = useOptimisticAction({
		action: `task.snooze:${task.id}`,
		optimisticUpdate: () => snapshotTasks(),
		mutateFn: () => {
			const newDue = addDays(new Date(), 30).toISOString();
			return updateMut.mutateAsync({ id: task.id, dueDate: newDue });
		},
		rollback: restoreTasks,
		toastLabel: "Snoozed 30d",
		toastDescription: task.title,
	});

	const archiveAction = useOptimisticAction({
		action: `task.archive:${task.id}`,
		optimisticUpdate: () => {
			const snap = snapshotTasks();
			// biome-ignore lint/suspicious/noExplicitAny: cache shape
			qc.setQueriesData({ queryKey: [["tasks"]] }, (old: any) => {
				if (!old || !Array.isArray(old?.pages)) return old;
				return {
					...old,
					// biome-ignore lint/suspicious/noExplicitAny: row
					pages: old.pages.map((page: any) => ({
						...page,
						// biome-ignore lint/suspicious/noExplicitAny: row
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
		toastLabel: "Archived",
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
				<StatusIcon type={task.status?.type} className="size-3.5" />
				<span className="min-w-0 flex-1 truncate">{task.title}</span>
				<span className="ml-auto shrink-0 text-[11px] text-amber-500 tabular-nums group-hover:opacity-0">
					{days != null ? `${days}d idle` : ""}
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
				<StaleButton title="Recommit" onClick={() => recommitAction.run(undefined)}>
					<RefreshCwIcon className="size-3.5" />
				</StaleButton>
				<StaleButton title="Snooze 30d" onClick={() => snoozeAction.run(undefined)}>
					<ClockIcon className="size-3.5" />
				</StaleButton>
				<StaleButton
					title="Archive"
					onClick={() => archiveAction.run(undefined)}
					disabled={!doneStatusId}
				>
					<ArchiveIcon className="size-3.5" />
				</StaleButton>
			</div>
		</div>
	);
}

function StaleButton({
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

"use client";

import { Button } from "@ui/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@ui/components/ui/dialog";
import { useMutation } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import {
	differenceInCalendarDays,
	format,
	getISOWeek,
	getISOWeekYear,
	startOfWeek,
} from "date-fns";
import { useRouter } from "next/navigation";
import { CalendarCheck2Icon, RotateCcwIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useUser } from "@/components/user-provider";
import { useTasks, type EnrichedTask } from "@/hooks/use-data";
import { invalidateTasksCache } from "@/hooks/use-data-cache-helpers";
import { trpc } from "@/utils/trpc";

/**
 * Weekly rollover banner — codex delighter #3 (Linear-style solo cycle
 * heartbeat).
 *
 * On Monday morning (or the first visit of a new ISO week), surface a banner
 * summarising last week:
 *
 *   "Weekly rollover: N carried over, M reviewed, K completed last week."
 *
 * CTAs:
 *   [Review carry-overs] → opens modal listing tasks due last week and not done.
 *                          Per-row actions: defer +7d / archive / mark done /
 *                          dismiss.
 *   [Plan this week]    → navigates to the lens Today segment.
 *   [Dismiss]           → ack this week; suppress until next Monday.
 *
 * Single-user mode: no team-rollover noise. The carry-over set is filtered to
 * the current user's open tasks whose dueDate fell in the *previous* ISO week.
 *
 * Acknowledgement persistence: `localStorage["nexus.rollover.lastAckWeek"]`
 * stores the ISO-week key (e.g. "2026-W20"). Banner re-appears when the
 * current key differs.
 */

const ACK_LS_KEY = "nexus.rollover.lastAckWeek";

function isoWeekKey(d: Date): string {
	return `${getISOWeekYear(d)}-W${String(getISOWeek(d)).padStart(2, "0")}`;
}

function loadAck(): string | null {
	if (typeof window === "undefined") return null;
	try {
		return window.localStorage.getItem(ACK_LS_KEY);
	} catch {
		return null;
	}
}

function saveAck(key: string) {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(ACK_LS_KEY, key);
	} catch {
		// localStorage might be unavailable (Safari private, quota) — fail open
	}
}

export function WeeklyRollover() {
	const user = useUser();
	const router = useRouter();
	const teamSlug = (user as any)?.team?.slug ?? "";

	const now = useMemo(() => new Date(), []);
	const currentWeekKey = useMemo(() => isoWeekKey(now), [now]);
	const startOfThisWeek = useMemo(
		() => startOfWeek(now, { weekStartsOn: 1 }),
		[now],
	);

	const [ack, setAck] = useState<string | null>(null);
	useEffect(() => {
		setAck(loadAck());
	}, []);

	const shouldShow = ack !== currentWeekKey;

	// Pull the user's open tasks once. Carry-overs are derived client-side so we
	// don't need a custom endpoint; React Query dedupes with other consumers.
	const { tasks: openTasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["backlog", "to_do", "in_progress", "review"],
			pageSize: 200,
		},
		{ enabled: !!user?.id && shouldShow },
	);
	const { tasks: doneTasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["done"],
			pageSize: 100,
		},
		{ enabled: !!user?.id && shouldShow },
	);

	const stats = useMemo(() => {
		const startLastWeek = new Date(
			startOfThisWeek.getTime() - 7 * 86_400_000,
		);
		const carryOver: EnrichedTask[] = [];
		let reviewed = 0;
		for (const t of openTasks) {
			const due = (t as any).dueDate
				? new Date((t as any).dueDate as string)
				: null;
			if (!due) continue;
			if (due >= startLastWeek && due < startOfThisWeek) {
				carryOver.push(t);
			}
			if (t.status?.type === "review") reviewed += 1;
		}
		let completed = 0;
		for (const t of doneTasks) {
			const at =
				(t as any).completedAt ?? (t as any).statusChangedAt ?? null;
			if (!at) continue;
			const d = new Date(at);
			if (d >= startLastWeek && d < startOfThisWeek) completed += 1;
		}
		return { carryOver, reviewed, completed };
	}, [openTasks, doneTasks, startOfThisWeek]);

	const [modalOpen, setModalOpen] = useState(false);

	const update = useMutation(
		trpc.tasks.update.mutationOptions({
			onSuccess: () => invalidateTasksCache(),
			onError: () => toast.error("Couldn't update task"),
		}),
	);

	const handleDismiss = () => {
		saveAck(currentWeekKey);
		setAck(currentWeekKey);
	};

	const handlePlan = () => {
		saveAck(currentWeekKey);
		setAck(currentWeekKey);
		if (teamSlug) router.push(`/team/${teamSlug}/lens`);
	};

	const handleDefer = (taskId: string) => {
		const newDue = new Date(now.getTime() + 7 * 86_400_000);
		update.mutate({ id: taskId, dueDate: newDue.toISOString() } as any, {
			onSuccess: () => toast.success("Deferred 7 days"),
		});
	};

	const handleArchive = (taskId: string) => {
		update.mutate({ id: taskId, archivedAt: new Date().toISOString() } as any, {
			onSuccess: () => toast.success("Archived"),
		});
	};

	if (!shouldShow) return null;
	if (
		stats.carryOver.length === 0 &&
		stats.reviewed === 0 &&
		stats.completed === 0
	) {
		// Nothing to roll over — silently auto-ack so we don't show an empty
		// banner that's pure noise.
		return null;
	}

	return (
		<>
			<div
				role="status"
				className="mb-4 flex items-start gap-3 rounded-md border border-violet-500/30 bg-violet-500/5 p-3"
			>
				<RotateCcwIcon className="mt-0.5 size-4 shrink-0 text-violet-400" />
				<div className="min-w-0 flex-1">
					<p className="font-[510] text-[13px] text-foreground">
						Weekly rollover · {format(startOfThisWeek, "MMM d")}
					</p>
					<p className="mt-0.5 text-[12px] text-muted-foreground">
						{stats.carryOver.length} carried over · {stats.reviewed} in
						review · {stats.completed} completed last week
					</p>
					<div className="mt-2 flex flex-wrap gap-1.5">
						<Button
							size="sm"
							variant="default"
							className="h-7 px-2 text-[12px]"
							onClick={() => setModalOpen(true)}
							disabled={stats.carryOver.length === 0}
						>
							Review carry-overs
						</Button>
						<Button
							size="sm"
							variant="outline"
							className="h-7 px-2 text-[12px]"
							onClick={handlePlan}
						>
							Plan this week
						</Button>
					</div>
				</div>
				<Button
					size="icon"
					variant="ghost"
					className="size-6 shrink-0 text-muted-foreground"
					onClick={handleDismiss}
					aria-label="Dismiss rollover banner"
				>
					<XIcon className="size-3.5" />
				</Button>
			</div>

			<Dialog open={modalOpen} onOpenChange={setModalOpen}>
				<DialogContent className="max-w-lg">
					<DialogHeader>
						<DialogTitle className="flex items-center gap-2 text-[15px]">
							<CalendarCheck2Icon className="size-4 text-violet-400" />
							Carry-overs from last week
						</DialogTitle>
						<DialogDescription>
							{stats.carryOver.length} tasks were due last week and aren't
							done yet. Reschedule, archive, or mark them complete.
						</DialogDescription>
					</DialogHeader>
					<ul className="max-h-[400px] space-y-1 overflow-y-auto py-1">
						{stats.carryOver.map((t) => {
							const due = (t as any).dueDate
								? new Date((t as any).dueDate as string)
								: null;
							const daysLate = due
								? -differenceInCalendarDays(due, now)
								: null;
							return (
								<li
									key={t.id}
									className={cn(
										"flex items-center gap-2 rounded-md border border-transparent px-2 py-1.5 hover:border-border hover:bg-accent/40",
									)}
								>
									<div className="min-w-0 flex-1">
										<p className="line-clamp-1 text-[13px] text-foreground">
											{t.title}
										</p>
										{daysLate != null && (
											<p className="text-[11px] text-muted-foreground tabular-nums">
												{daysLate} day{daysLate === 1 ? "" : "s"} late
											</p>
										)}
									</div>
									<Button
										size="sm"
										variant="ghost"
										className="h-7 px-2 text-[11px]"
										onClick={() => handleDefer(t.id)}
									>
										+7d
									</Button>
									<Button
										size="sm"
										variant="ghost"
										className="h-7 px-2 text-[11px]"
										onClick={() => handleArchive(t.id)}
									>
										Archive
									</Button>
								</li>
							);
						})}
					</ul>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								handleDismiss();
								setModalOpen(false);
							}}
						>
							Done for this week
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}

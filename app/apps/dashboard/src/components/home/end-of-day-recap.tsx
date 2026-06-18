"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { isToday } from "date-fns";
import { CheckCircle2Icon, PlusCircleIcon, SparklesIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";
import { trpc } from "@/utils/trpc";

/**
 * End-of-day recap (codex delighter #7, Granola-style).
 *
 * Shown after 5pm or when the user expands it manually. Surfaces the deltas
 * of the day:
 *   - tasks completed today (status type = done, statusChangedAt = today)
 *   - tasks created today
 *   - projects "touched" today (any activity referencing the project)
 *
 * This is a stub for iter-10 — only counts + a teaser. Full Granola-style
 * narrative ("you finished feature X, started Y, blocked on Z") is on the
 * iter-11 roadmap once we have a summarization pipeline.
 */

function isAfter5pm(now: Date): boolean {
	return now.getHours() >= 17;
}

export const EndOfDayRecap = () => {
	const user = useUser();
	const now = useMemo(() => new Date(), []);
	const showByDefault = isAfter5pm(now);
	const [expanded, setExpanded] = useState(showByDefault);

	// Two task slices: "active" (to_do / in_progress / review) for created-today,
	// and a separate done slice for completed-today. The done slice is filtered
	// by statusChangedAt server-side via the same get endpoint; we filter client
	// side because the schema's createdAt/statusChangedAt are nullable on some
	// rows.
	const { tasks: activeTasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 100,
		},
		{ enabled: !!user?.id },
	);
	const { tasks: doneTasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["done"],
			pageSize: 100,
		},
		{ enabled: !!user?.id && expanded },
	);

	// Activity touch-count for project freshness. Cheaper than a custom endpoint;
	// only fires once the panel is expanded so we don't pull this on every Home
	// render.
	const { data: activitiesData } = useQuery(
		trpc.activities.get.queryOptions(
			{ pageSize: 50 },
			{ staleTime: 60 * 1000, enabled: expanded },
		),
	);

	const stats = useMemo(() => {
		const created = activeTasks.filter((t) =>
			t.createdAt ? isToday(new Date(t.createdAt)) : false,
		).length;
		const completed = doneTasks.filter((t) =>
			t.statusChangedAt ? isToday(new Date(t.statusChangedAt)) : false,
		).length;
		// biome-ignore lint/suspicious/noExplicitAny: activity row shape from tRPC
		const activities = ((activitiesData as any)?.data ?? []) as Array<any>;
		const projectIds = new Set<string>();
		for (const a of activities) {
			if (!a.createdAt) continue;
			if (!isToday(new Date(a.createdAt))) continue;
			const pid =
				a.task?.projectId ?? a.metadata?.projectId ?? a.projectId ?? null;
			if (pid) projectIds.add(pid);
		}
		return { created, completed, projectsTouched: projectIds.size };
	}, [activeTasks, doneTasks, activitiesData]);

	return (
		<section className="rounded-[12px] border border-border bg-card">
			<button
				type="button"
				onClick={() => setExpanded((v) => !v)}
				className="flex w-full items-center justify-between gap-2 border-border border-b px-3 py-2 text-left transition-colors hover:bg-accent/30"
			>
				<div className="flex items-center gap-1.5">
					<SparklesIcon className="size-3.5 text-violet-500" />
					<h2 className="font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						End-of-day recap
					</h2>
					{!expanded && showByDefault ? (
						<span className="ml-1 rounded-full bg-violet-500/15 px-1.5 py-0.5 font-[510] text-[10px] text-violet-500 uppercase tracking-wide">
							New
						</span>
					) : null}
				</div>
				<span className="text-[11px] text-muted-foreground">
					{expanded ? "Hide" : "Show today's deltas"}
				</span>
			</button>
			{expanded ? (
				<div className="grid grid-cols-3 gap-2 px-3 py-3">
					<RecapStat
						label="Completed"
						value={stats.completed}
						icon={<CheckCircle2Icon className="size-3.5 text-emerald-500" />}
					/>
					<RecapStat
						label="Created"
						value={stats.created}
						icon={<PlusCircleIcon className="size-3.5 text-sky-500" />}
					/>
					<RecapStat
						label="Projects touched"
						value={stats.projectsTouched}
						icon={<SparklesIcon className="size-3.5 text-violet-500" />}
					/>
					<p className="col-span-3 text-[11px] text-muted-foreground">
						Granola-style narrative recap (what you shipped, what's blocked,
						what's next) lands in iter-11.
					</p>
				</div>
			) : null}
		</section>
	);
};

function RecapStat({
	label,
	value,
	icon,
}: {
	label: string;
	value: number;
	icon: React.ReactNode;
}) {
	return (
		<div
			className={cn(
				"flex flex-col gap-0.5 rounded-md border border-border bg-background px-3 py-2",
			)}
		>
			<div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
				{icon}
				{label}
			</div>
			<div className="font-[510] text-[18px] text-foreground tabular-nums">
				{value}
			</div>
		</div>
	);
}

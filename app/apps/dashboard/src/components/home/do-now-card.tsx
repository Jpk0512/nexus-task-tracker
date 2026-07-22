"use client";

import { cn } from "@ui/lib/utils";
import { isPast, isToday } from "date-fns";
import { AlertCircleIcon, ClockIcon } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";
import { type EnrichedTask, useTasks } from "@/hooks/use-data";
import { HomeCard, HomeCardEmpty } from "./home-card";

/**
 * Do now — Attention Graph top-N for Home (Dashboard OS lock B).
 * Rank: overdue → due today → in_progress → rest. Max 5 with reason chips.
 */
const PRIORITY_ORDER: Record<string, number> = {
	urgent: 0,
	high: 1,
	medium: 2,
	low: 3,
};

type Ranked = EnrichedTask & {
	reason: string;
	reasonTone: "red" | "orange" | "blue" | "gray";
};

function rankTasks(tasks: EnrichedTask[]): Ranked[] {
	const scored = tasks.map((t) => {
		const due = t.dueDate ? new Date(t.dueDate) : null;
		const overdue = due ? isPast(due) && !isToday(due) : false;
		const dueToday = due ? isToday(due) : false;
		const statusType = (t as { status?: { type?: string } }).status?.type;
		const inProg = statusType === "in_progress" || statusType === "review";
		let reason = "open";
		let reasonTone: Ranked["reasonTone"] = "gray";
		let score = 40;
		if (overdue) {
			reason = "overdue";
			reasonTone = "red";
			score = 0;
		} else if (dueToday) {
			reason = "today";
			reasonTone = "orange";
			score = 10;
		} else if (inProg) {
			reason = "in progress";
			reasonTone = "blue";
			score = 20;
		}
		const p =
			PRIORITY_ORDER[(t as { priority?: string }).priority ?? "medium"] ?? 2;
		return { ...t, reason, reasonTone, _s: score * 10 + p };
	});
	scored.sort((a, b) => a._s - b._s);
	return scored.slice(0, 5).map(({ _s: _, ...rest }) => rest as Ranked);
}

const toneClass = {
	red: "border-red-500/30 text-red-400",
	orange: "border-orange-400/30 text-orange-300",
	blue: "border-primary/30 text-primary",
	gray: "border-border text-muted-foreground",
} as const;

export function DoNowCard() {
	const user = useUser();
	const { tasks, isLoading } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 100,
		},
		{ enabled: !!user?.id },
	);

	const ranked = useMemo(() => rankTasks(tasks), [tasks]);
	const focusHref = `${user.basePath}/focus`;

	return (
		<HomeCard
			title="Do now"
			count={ranked.length}
			href={focusHref}
			isLoading={isLoading}
			isEmpty={!isLoading && ranked.length === 0}
			emptyState={
				<HomeCardEmpty
					title="Nothing urgent"
					description="Capture a thought or open Focus to plan your day."
					ctaLabel="Open Focus"
					ctaHref={focusHref}
				/>
			}
		>
			<ul className="divide-y divide-border/50">
				{ranked.map((t) => {
					const statusType = (t as { status?: { type?: string } }).status?.type;
					return (
						<li key={t.id}>
							<Link
								href={`${user.basePath}/tasks/${t.id}`}
								className="flex items-center gap-2.5 px-1 py-2.5 transition-colors hover:bg-accent/40"
							>
								{statusType === "in_progress" ? (
									<SoftIcon icon={ClockIcon} tone="blue" size="sm" />
								) : (
									<SoftIcon
										icon={AlertCircleIcon}
										tone={
											t.reasonTone === "red"
												? "red"
												: t.reasonTone === "orange"
													? "orange"
													: "gray"
										}
										size="sm"
									/>
								)}
								<span className="min-w-0 flex-1 truncate font-[510] text-[13px]">
									{t.title}
								</span>
								<span
									className={cn(
										"shrink-0 rounded-full border px-1.5 py-0.5 text-[10px]",
										toneClass[t.reasonTone],
									)}
								>
									{t.reason}
								</span>
							</Link>
						</li>
					);
				})}
			</ul>
		</HomeCard>
	);
}

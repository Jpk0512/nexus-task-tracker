"use client";

import { format, isPast, isToday } from "date-fns";
import { useMemo } from "react";
import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";

/**
 * Linear-style time-of-day greeting and "day brief" for the Home page.
 *
 * Renders three lines:
 *   1. "Good morning, Sat May 17" — time-of-day + short calendar date
 *   2. Welcome line with the user's first name
 *   3. Day brief — "N due today · M overdue" pulled from the same tasks slice
 *      the Agenda card uses (assigned to current user, not closed).
 *
 * No card chrome; sits directly under the quick-capture bar.
 */

function timeOfDayLabel(now: Date): string {
	const hour = now.getHours();
	if (hour < 5) return "Late night";
	if (hour < 12) return "Good morning";
	if (hour < 18) return "Good afternoon";
	return "Good evening";
}

export const GreetingCard = () => {
	const user = useUser();
	const firstName = user?.name?.split(" ")[0] ?? "there";

	const now = useMemo(() => new Date(), []);
	const tod = timeOfDayLabel(now);
	const dateLabel = format(now, "EEE LLL d");

	// Tasks the user owns and isn't done with — used to count due today / overdue
	// without firing a second query. Same call shape as MyIssuesCard, so React
	// Query dedupes on the wire.
	const { tasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 100,
		},
		{ enabled: !!user?.id },
	);

	const { dueToday, overdue } = useMemo(() => {
		let dt = 0;
		let od = 0;
		for (const t of tasks) {
			if (!t.dueDate) continue;
			const d = new Date(t.dueDate);
			if (isToday(d)) dt += 1;
			else if (isPast(d)) od += 1;
		}
		return { dueToday: dt, overdue: od };
	}, [tasks]);

	const briefParts: string[] = [];
	if (dueToday > 0) briefParts.push(`${dueToday} due today`);
	if (overdue > 0) briefParts.push(`${overdue} overdue`);
	const brief =
		briefParts.length > 0
			? briefParts.join(" · ")
			: "Nothing on the runway. Capture or plan something below.";

	return (
		<section className="space-y-1">
			<h1 className="font-[510] text-[20px] text-foreground tracking-[-0.01em]">
				{tod}, {dateLabel}
			</h1>
			<p className="text-[13px] text-muted-foreground">
				Welcome back, {firstName}. {brief}
			</p>
		</section>
	);
};

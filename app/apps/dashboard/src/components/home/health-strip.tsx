"use client";

import { isPast, isToday } from "date-fns";
import Link from "next/link";
import { useMemo } from "react";
import { useInboxCounts } from "@/components/inbox/use-inbox-counts";
import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";

/** Compact Home health strip — overdue · today · needs you. */
export function HealthStrip() {
	const user = useUser();
	const { tasks } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 100,
		},
		{ enabled: !!user?.id },
	);
	const { tabCounts } = useInboxCounts();

	const stats = useMemo(() => {
		const overdue = tasks.filter((t) => {
			if (!t.dueDate) return false;
			const d = new Date(t.dueDate);
			return isPast(d) && !isToday(d);
		}).length;
		const today = tasks.filter(
			(t) => t.dueDate && isToday(new Date(t.dueDate)),
		).length;
		return {
			overdue,
			today,
			unread: tabCounts?.unread ?? 0,
		};
	}, [tasks, tabCounts]);

	const base = user.basePath;
	const chips = [
		{
			label: "Overdue",
			n: stats.overdue,
			href: `${base}/focus`,
			warn: stats.overdue > 0,
		},
		{
			label: "Today",
			n: stats.today,
			href: `${base}/focus`,
			warn: false,
		},
		{
			label: "Needs you",
			n: stats.unread,
			href: `${base}/focus?tab=needs-you`,
			warn: stats.unread > 0,
		},
		{ label: "Health", n: null as number | null, href: `${base}/health`, warn: false },
	];

	return (
		<div className="flex flex-wrap items-center gap-2">
			{chips.map((c) => (
				<Link
					key={c.label}
					href={c.href}
					className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11.5px] transition-colors hover:bg-accent/50 ${
						c.warn
							? "border-red-500/30 text-red-300"
							: "border-border/60 text-muted-foreground"
					}`}
				>
					<span className="font-[510] text-foreground/90">{c.label}</span>
					{typeof c.n === "number" ? (
						<span className="tabular-nums">{c.n}</span>
					) : (
						<span>→</span>
					)}
				</Link>
			))}
		</div>
	);
}

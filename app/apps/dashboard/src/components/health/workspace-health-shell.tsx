"use client";

import { isPast, isToday } from "date-fns";
import {
	AlertTriangleIcon,
	CheckCircle2Icon,
	ClockIcon,
	InboxIcon,
	ServerIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";
import { useInbox } from "@/components/inbox/use-inbox";

type DebtCard = {
	id: string;
	title: string;
	count: number;
	blurb: string;
	href: string;
	tone: "red" | "orange" | "blue" | "green" | "gray";
	icon: typeof ClockIcon;
};

export function WorkspaceHealthShell() {
	const user = useUser();
	const base = user.basePath;
	const { tasks, isLoading } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["to_do", "in_progress", "review"],
			pageSize: 200,
		},
		{ enabled: !!user?.id },
	);
	const { tabCounts } = useInbox();

	const cards = useMemo((): DebtCard[] => {
		const overdue = tasks.filter((t) => {
			if (!t.dueDate) return false;
			const d = new Date(t.dueDate);
			return isPast(d) && !isToday(d);
		}).length;
		const dueToday = tasks.filter(
			(t) => t.dueDate && isToday(new Date(t.dueDate)),
		).length;
		const unread = tabCounts?.unread ?? 0;
		return [
			{
				id: "overdue",
				title: "Overdue tasks",
				count: overdue,
				blurb: "Past due and still open — clear or reschedule.",
				href: `${base}/focus`,
				tone: overdue > 0 ? "red" : "green",
				icon: AlertTriangleIcon,
			},
			{
				id: "today",
				title: "Due today",
				count: dueToday,
				blurb: "On the clock for today.",
				href: `${base}/focus`,
				tone: dueToday > 0 ? "orange" : "green",
				icon: ClockIcon,
			},
			{
				id: "needs-you",
				title: "Needs you (unread)",
				count: unread,
				blurb: "Attention queue — not a brain dump.",
				href: `${base}/focus`,
				tone: unread > 0 ? "blue" : "green",
				icon: InboxIcon,
			},
			{
				id: "mcp",
				title: "MCP catalog",
				count: 0,
				blurb: "Review server health and tool inventory.",
				href: `${base}/mcps`,
				tone: "gray",
				icon: ServerIcon,
			},
		];
	}, [tasks, tabCounts, base]);

	return (
		<div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-8">
			<div>
				<h1 className="font-[510] text-[22px] tracking-[-0.02em]">
					Workspace Health
				</h1>
				<p className="mt-1 text-[13px] text-muted-foreground">
					Capture debt, attention load, overdue — each with a one-click path to
					fix.
				</p>
			</div>

			{isLoading ? (
				<p className="text-[13px] text-muted-foreground">Loading signals…</p>
			) : (
				<ul className="grid gap-3 sm:grid-cols-2">
					{cards.map((c) => (
						<li key={c.id}>
							<Link
								href={c.href}
								className="flex h-full flex-col gap-3 rounded-xl border border-border/60 bg-card/40 p-4 transition-colors hover:bg-accent/30"
							>
								<div className="flex items-center justify-between">
									<SoftIcon icon={c.icon} tone={c.tone} size="md" />
									<span className="font-[510] text-[22px] tabular-nums tracking-tight">
										{c.count}
									</span>
								</div>
								<div>
									<p className="font-[510] text-[14px]">{c.title}</p>
									<p className="mt-0.5 text-[12px] text-muted-foreground">
										{c.blurb}
									</p>
								</div>
								{c.count === 0 && c.id !== "mcp" ? (
									<span className="inline-flex items-center gap-1 text-[11px] text-green-400/90">
										<CheckCircle2Icon className="size-3" /> Clear
									</span>
								) : (
									<span className="text-[11px] text-primary">Open →</span>
								)}
							</Link>
						</li>
					))}
				</ul>
			)}
		</div>
	);
}

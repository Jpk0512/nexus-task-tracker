"use client";

import { Button } from "@ui/components/ui/button";
import { isPast, isToday } from "date-fns";
import {
	CalendarRangeIcon,
	CheckCircle2Icon,
	MoonIcon,
	SunIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import {
	EmptyState,
	EmptyStateDescription,
	EmptyStateIcon,
	EmptyStateTitle,
} from "@/components/empty-state";
import { useInboxCounts } from "@/components/inbox/use-inbox-counts";
import { SoftIcon } from "@/components/ui/soft-icon";
import { useUser } from "@/components/user-provider";
import { useTasks } from "@/hooks/use-data";

type RitualId = "morning" | "weekly" | "eod";

const RITUALS: {
	id: RitualId;
	title: string;
	blurb: string;
	icon: typeof SunIcon;
	tone: "yellow" | "blue" | "violet";
}[] = [
	{
		id: "morning",
		title: "Morning review",
		blurb: "Scan overdue + today, clear Needs you, set one Do-now.",
		icon: SunIcon,
		tone: "yellow",
	},
	{
		id: "weekly",
		title: "Weekly review",
		blurb: "Roll stale work, plan upcoming, health check.",
		icon: CalendarRangeIcon,
		tone: "blue",
	},
	{
		id: "eod",
		title: "EOD close",
		blurb: "Capture leftovers, archive dump noise, mark wins.",
		icon: MoonIcon,
		tone: "violet",
	},
];

export function RitualsShell() {
	const user = useUser();
	const base = user.basePath;
	const [active, setActive] = useState<RitualId | null>(null);
	const [doneSteps, setDoneSteps] = useState<Record<string, boolean>>({});
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
			open: tasks.length,
		};
	}, [tasks, tabCounts]);

	const stepsFor = (id: RitualId) => {
		if (id === "morning")
			return [
				{
					key: "m1",
					label: `Review ${stats.overdue} overdue`,
					href: `${base}/focus`,
				},
				{
					key: "m2",
					label: `Plan ${stats.today} due today`,
					href: `${base}/focus`,
				},
				{
					key: "m3",
					label: `Clear ${stats.unread} Needs you`,
					href: `${base}/focus?tab=needs-you`,
				},
				{ key: "m4", label: "Open Capture dump", href: `${base}/capture` },
			];
		if (id === "weekly")
			return [
				{ key: "w1", label: "Workspace Health", href: `${base}/health` },
				{
					key: "w2",
					label: "Activity since last week",
					href: `${base}/activity`,
				},
				{ key: "w3", label: "Focus upcoming", href: `${base}/focus` },
				{
					key: "w4",
					label: "Continue Project Starter if open",
					href: `${base}/create-project/starter`,
				},
			];
		return [
			{
				key: "e1",
				label: "Dump leftovers to Capture",
				href: `${base}/capture`,
			},
			{
				key: "e2",
				label: `Close or defer open work (${stats.open})`,
				href: `${base}/focus`,
			},
			{ key: "e3", label: "File meeting actions", href: `${base}/meetings` },
			{ key: "e4", label: "Glance Activity", href: `${base}/activity` },
		];
	};

	return (
		<div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-8">
			<div>
				<h1 className="font-[510] text-[22px] tracking-[-0.02em]">Rituals</h1>
				<p className="mt-1 text-[13px] text-muted-foreground">
					Guided loops grounded in live counts — not blank checklists.
				</p>
			</div>

			<ul className="grid gap-3 sm:grid-cols-3">
				{RITUALS.map((r) => (
					<li key={r.id}>
						<button
							type="button"
							onClick={() => {
								setActive(r.id);
								setDoneSteps({});
							}}
							className={`flex h-full w-full flex-col gap-2 rounded-xl border p-4 text-left transition-colors ${
								active === r.id
									? "border-primary/40 bg-primary/10"
									: "border-border/60 bg-card/40 hover:bg-accent/30"
							}`}
						>
							<SoftIcon icon={r.icon} tone={r.tone} size="md" />
							<span className="font-[510] text-[14px]">{r.title}</span>
							<span className="text-[12px] text-muted-foreground">
								{r.blurb}
							</span>
						</button>
					</li>
				))}
			</ul>

			{!active && (
				<EmptyState>
					<EmptyStateIcon>
						<CalendarRangeIcon className="size-full" />
					</EmptyStateIcon>
					<EmptyStateTitle>Pick a ritual to start</EmptyStateTitle>
					<EmptyStateDescription>
						Choose Morning, Weekly, or EOD above and we'll turn your live counts
						into a guided checklist.
					</EmptyStateDescription>
				</EmptyState>
			)}
			{active ? (
				<div className="rounded-xl border border-border/60 bg-card/40 p-4">
					<p className="mb-3 font-[510] text-[13px]">
						{RITUALS.find((r) => r.id === active)?.title} steps
					</p>
					<ul className="space-y-2">
						{stepsFor(active).map((s) => {
							const done = !!doneSteps[s.key];
							return (
								<li
									key={s.key}
									className="flex items-center gap-2 rounded-lg border border-border/50 px-3 py-2"
								>
									<button
										type="button"
										onClick={() =>
											setDoneSteps((d) => ({ ...d, [s.key]: !d[s.key] }))
										}
										className="text-muted-foreground hover:text-green-400"
									>
										<CheckCircle2Icon
											className={`size-4 ${done ? "text-green-400" : ""}`}
										/>
									</button>
									<span
										className={`min-w-0 flex-1 text-[13px] ${done ? "text-muted-foreground line-through" : ""}`}
									>
										{s.label}
									</span>
									<Button asChild size="sm" variant="ghost" className="h-7">
										<Link href={s.href}>Open</Link>
									</Button>
								</li>
							);
						})}
					</ul>
				</div>
			) : null}
		</div>
	);
}

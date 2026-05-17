"use client";

import {
	endOfDay,
	endOfWeek,
	format,
	isThisWeek,
	isToday,
	startOfDay,
	startOfWeek,
} from "date-fns";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { TriageCard, type TriageTask } from "@/components/triage/triage-card";
import { useUser } from "@/components/user-provider";
import { type EnrichedTask, useTasks } from "@/hooks/use-data";
import { useShortcut } from "@/hooks/use-shortcuts";
import { WeeklyRollover } from "./weekly-rollover";
import {
	LensSidebar,
	type LensSegmentId,
	LENS_SEGMENTS,
} from "./lens-sidebar";

/**
 * Personal lens — codex delighter #2 (Things-style Today / Upcoming / Someday).
 *
 * Overlay on existing task data. Every segment is a pure derivation of the
 * user's task slice (same `useTasks` call the rest of the dashboard uses);
 * there is no new server endpoint and no schema change.
 *
 * Segment rules:
 *   Today    → due today | triage Now (status.type = in_progress|review) |
 *              starred-for-today (localStorage allowlist of task ids)
 *   Upcoming → dueDate ∈ (today, +7d], grouped by day
 *   Anytime  → status.type = in_progress AND no dueDate
 *   Someday  → status.type = backlog AND no dueDate
 *   Logbook  → status.type = done AND completedAt ≥ -30d, grouped by week
 *
 * Cards reuse `TriageCard` so the existing metadata-conflict-badge
 * (iter 8) and project chip strip apply for free.
 */

const STAR_LS_KEY = "nexus.lens.starred-today";

function loadStarred(): Set<string> {
	if (typeof window === "undefined") return new Set();
	try {
		const raw = window.localStorage.getItem(STAR_LS_KEY);
		if (!raw) return new Set();
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return new Set();
		return new Set(parsed.filter((x): x is string => typeof x === "string"));
	} catch {
		return new Set();
	}
}

function toTriageTask(t: EnrichedTask): TriageTask {
	return {
		id: t.id,
		title: t.title,
		priority: (t as any).priority ?? null,
		dueDate: (t as any).dueDate ?? null,
		sequence: (t as any).sequence ?? null,
		permalinkId: (t as any).permalinkId ?? null,
		statusChangedAt: (t as any).statusChangedAt ?? null,
		createdAt: (t as any).createdAt ?? null,
		assignee: t.assignee
			? {
					id: t.assignee.id,
					name: t.assignee.name,
					email: t.assignee.email,
					image: t.assignee.image as string | null | undefined,
					color: (t.assignee as any).color ?? null,
				}
			: null,
		project: t.project
			? {
					id: t.project.id,
					name: t.project.name,
					prefix: (t.project as any).prefix ?? null,
					color: (t.project as any).color ?? null,
				}
			: null,
		status: t.status
			? {
					id: t.status.id,
					name: t.status.name,
					type: t.status.type as TriageTask["status"] extends infer S
						? S extends { type?: infer U }
							? U
							: never
						: never,
				}
			: null,
	};
}

interface LensColumnProps {
	team: string;
	teamPrefix?: string | null;
	tasks: TriageTask[];
	emptyMessage: string;
}

function LensList({ team, teamPrefix, tasks, emptyMessage }: LensColumnProps) {
	if (tasks.length === 0) {
		return (
			<p className="px-3 py-6 text-center text-muted-foreground text-xs italic">
				{emptyMessage}
			</p>
		);
	}
	return (
		<ul className="space-y-0.5">
			{tasks.map((t) => (
				<li key={t.id}>
					<TriageCard task={t} team={team} teamPrefix={teamPrefix} />
				</li>
			))}
		</ul>
	);
}

function SectionHeader({
	title,
	count,
}: {
	title: string;
	count?: number;
}) {
	return (
		<header className="flex items-baseline gap-2 px-1 pb-2">
			<h2 className="font-[510] text-[14px] text-foreground tracking-[-0.005em]">
				{title}
			</h2>
			{typeof count === "number" && (
				<span className="text-[11px] text-muted-foreground tabular-nums">
					{count}
				</span>
			)}
		</header>
	);
}

export function PersonalLens() {
	const user = useUser();
	const router = useRouter();
	const teamSlug = (user as any)?.team?.slug ?? "";
	const teamPrefix = (user as any)?.team?.prefix ?? null;

	// Single broad fetch — every segment narrows client-side. Same shape as
	// GreetingCard so React Query dedupes it on the wire.
	const { tasks: openTasks, isLoading: loadingOpen } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["backlog", "to_do", "in_progress", "review"],
			pageSize: 200,
		},
		{ enabled: !!user?.id },
	);
	const { tasks: doneTasks, isLoading: loadingDone } = useTasks(
		{
			assigneeId: user?.id ? [user.id] : undefined,
			statusType: ["done"],
			pageSize: 200,
		},
		{ enabled: !!user?.id },
	);

	const [starred, setStarred] = useState<Set<string>>(new Set());
	useEffect(() => {
		setStarred(loadStarred());
	}, []);

	const [activeSegment, setActiveSegment] = useState<LensSegmentId>("today");

	const now = useMemo(() => new Date(), []);
	const startToday = startOfDay(now);
	const endToday = endOfDay(now);
	const upcomingCutoff = endOfDay(
		new Date(startToday.getTime() + 7 * 86_400_000),
	);
	const logbookCutoff = startOfDay(
		new Date(startToday.getTime() - 30 * 86_400_000),
	);

	const segments = useMemo(() => {
		const today: EnrichedTask[] = [];
		const upcomingByDay = new Map<string, EnrichedTask[]>();
		const anytime: EnrichedTask[] = [];
		const someday: EnrichedTask[] = [];

		for (const t of openTasks) {
			const due = (t as any).dueDate
				? new Date((t as any).dueDate as string)
				: null;
			const statusType = t.status?.type ?? null;
			const isInNowColumn =
				statusType === "in_progress" || statusType === "review";
			const isStarred = starred.has(t.id);

			// Today segment — any of: due today, in Now column, starred
			if (
				(due && isToday(due)) ||
				isInNowColumn ||
				isStarred
			) {
				today.push(t);
			}

			// Upcoming — strictly in the next 7 days, excluding today (today owns
			// its own segment)
			if (due && due > endToday && due <= upcomingCutoff) {
				const key = format(due, "yyyy-MM-dd");
				const bucket = upcomingByDay.get(key);
				if (bucket) bucket.push(t);
				else upcomingByDay.set(key, [t]);
			}

			// Anytime — in_progress without a due date
			if (!due && statusType === "in_progress") {
				anytime.push(t);
			}

			// Someday — backlog without a due date
			if (!due && statusType === "backlog") {
				someday.push(t);
			}
		}

		const logbookByWeek = new Map<string, EnrichedTask[]>();
		for (const t of doneTasks) {
			const completedAtRaw =
				(t as any).completedAt ?? (t as any).statusChangedAt ?? null;
			if (!completedAtRaw) continue;
			const completed = new Date(completedAtRaw);
			if (completed < logbookCutoff) continue;
			const wkStart = startOfWeek(completed, { weekStartsOn: 1 });
			const key = format(wkStart, "yyyy-MM-dd");
			const bucket = logbookByWeek.get(key);
			if (bucket) bucket.push(t);
			else logbookByWeek.set(key, [t]);
		}

		return {
			today,
			upcomingByDay,
			anytime,
			someday,
			logbookByWeek,
		};
	}, [
		openTasks,
		doneTasks,
		starred,
		endToday,
		upcomingCutoff,
		logbookCutoff,
	]);

	const upcomingCount = useMemo(() => {
		let n = 0;
		for (const list of segments.upcomingByDay.values()) n += list.length;
		return n;
	}, [segments.upcomingByDay]);
	const logbookCount = useMemo(() => {
		let n = 0;
		for (const list of segments.logbookByWeek.values()) n += list.length;
		return n;
	}, [segments.logbookByWeek]);

	const counts: Partial<Record<LensSegmentId, number>> = {
		today: segments.today.length,
		upcoming: upcomingCount,
		anytime: segments.anytime.length,
		someday: segments.someday.length,
		logbook: logbookCount,
	};

	// Cmd+L opens lens — wired at the route level so the global registry entry
	// `nav.lens` becomes a real handler.
	useShortcut("nav.lens", () => {
		if (!teamSlug) return;
		router.push(`/team/${teamSlug}/lens`);
	});

	const seg = LENS_SEGMENTS.find((s) => s.id === activeSegment);
	const isLoading = loadingOpen || (activeSegment === "logbook" && loadingDone);

	return (
		<div className="flex h-full min-h-[calc(100vh-120px)] animate-blur-in">
			<LensSidebar
				activeId={activeSegment}
				counts={counts}
				onSelect={setActiveSegment}
			/>
			<div className="min-w-0 flex-1 p-6">
				<WeeklyRollover />
				<header className="mb-4">
					<h1 className="font-[510] text-[18px] text-foreground tracking-[-0.01em]">
						{seg?.label ?? "Today"}
					</h1>
					<p className="mt-0.5 text-[12px] text-muted-foreground">
						{seg?.hint}
					</p>
				</header>

				{isLoading && (
					<p className="px-1 py-6 text-center text-muted-foreground text-xs italic">
						Loading…
					</p>
				)}

				{!isLoading && activeSegment === "today" && (
					<LensList
						team={teamSlug}
						teamPrefix={teamPrefix}
						tasks={segments.today.map(toTriageTask)}
						emptyMessage="Nothing for today. Star a task or move it into Now."
					/>
				)}

				{!isLoading && activeSegment === "upcoming" && (
					<div className="space-y-6">
						{Array.from(segments.upcomingByDay.entries())
							.sort(([a], [b]) => a.localeCompare(b))
							.map(([day, list]) => {
								const date = new Date(`${day}T00:00:00`);
								const label = isThisWeek(date, { weekStartsOn: 1 })
									? format(date, "EEEE")
									: format(date, "EEE, MMM d");
								return (
									<section key={day}>
										<SectionHeader title={label} count={list.length} />
										<LensList
											team={teamSlug}
											teamPrefix={teamPrefix}
											tasks={list.map(toTriageTask)}
											emptyMessage="Empty."
										/>
									</section>
								);
							})}
						{segments.upcomingByDay.size === 0 && (
							<p className="px-1 py-6 text-center text-muted-foreground text-xs italic">
								No tasks due in the next 7 days.
							</p>
						)}
					</div>
				)}

				{!isLoading && activeSegment === "anytime" && (
					<LensList
						team={teamSlug}
						teamPrefix={teamPrefix}
						tasks={segments.anytime.map(toTriageTask)}
						emptyMessage="No open work without a due date. Nice."
					/>
				)}

				{!isLoading && activeSegment === "someday" && (
					<LensList
						team={teamSlug}
						teamPrefix={teamPrefix}
						tasks={segments.someday.map(toTriageTask)}
						emptyMessage="Backlog is empty. Capture an idea from Home."
					/>
				)}

				{!isLoading && activeSegment === "logbook" && (
					<div className="space-y-6">
						{Array.from(segments.logbookByWeek.entries())
							.sort(([a], [b]) => b.localeCompare(a))
							.map(([wk, list]) => {
								const wkStart = new Date(`${wk}T00:00:00`);
								const wkEnd = endOfWeek(wkStart, { weekStartsOn: 1 });
								const label = `Week of ${format(wkStart, "MMM d")} – ${format(wkEnd, "MMM d")}`;
								return (
									<section key={wk}>
										<SectionHeader title={label} count={list.length} />
										<LensList
											team={teamSlug}
											teamPrefix={teamPrefix}
											tasks={list.map(toTriageTask)}
											emptyMessage="Empty."
										/>
									</section>
								);
							})}
						{segments.logbookByWeek.size === 0 && (
							<p className="px-1 py-6 text-center text-muted-foreground text-xs italic">
								Nothing closed in the last 30 days.
							</p>
						)}
					</div>
				)}
			</div>
		</div>
	);
}

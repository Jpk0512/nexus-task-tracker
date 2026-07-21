"use client";

import { cn } from "@ui/lib/utils";
import {
	ArchiveIcon,
	CalendarClockIcon,
	CalendarDaysIcon,
	CloudIcon,
	InfinityIcon,
	SunIcon,
} from "lucide-react";

/**
 * Left rail for the personal lens (codex delighter #2).
 *
 * Things-style segmented nav over the *existing* task data — each segment is a
 * lens onto the user's task slice, not a separate DB list:
 *
 *   Today    → tasks due today + triage Now column + starred-for-today
 *   Upcoming → tasks due in next 7 days, grouped by day
 *   Anytime  → tasks in_progress with no due date
 *   Someday  → backlog tasks with no due date
 *   Logbook  → done tasks in last 30 days, grouped by week
 *
 * The actual filtering happens in `PersonalLens`; this component only renders
 * the rail and surfaces the active segment via a callback. Counts are passed
 * in as a prop so the rail does not need its own data fetch.
 */

export type LensSegmentId =
	| "today"
	| "upcoming"
	| "anytime"
	| "someday"
	| "logbook";

export interface LensSegmentDef {
	id: LensSegmentId;
	label: string;
	icon: React.ComponentType<{ className?: string }>;
	tint: string;
	hint: string;
}

export const LENS_SEGMENTS: LensSegmentDef[] = [
	{
		id: "today",
		label: "Today",
		icon: SunIcon,
		tint: "text-yellow-500",
		hint: "Due today, starred, or in your Now column",
	},
	{
		id: "upcoming",
		label: "Upcoming",
		icon: CalendarDaysIcon,
		tint: "text-blue-400",
		hint: "Due in the next 7 days",
	},
	{
		id: "anytime",
		label: "Anytime",
		icon: InfinityIcon,
		tint: "text-cyan-400",
		hint: "In progress, no due date",
	},
	{
		id: "someday",
		label: "Someday",
		icon: CloudIcon,
		tint: "text-slate-400",
		hint: "Backlog, no due date",
	},
	{
		id: "logbook",
		label: "Logbook",
		icon: ArchiveIcon,
		tint: "text-emerald-500",
		hint: "Done in the last 30 days",
	},
];

export interface LensSidebarProps {
	activeId: LensSegmentId;
	counts: Partial<Record<LensSegmentId, number>>;
	onSelect: (id: LensSegmentId) => void;
}

export function LensSidebar({ activeId, counts, onSelect }: LensSidebarProps) {
	return (
		<nav
			aria-label="Personal lens segments"
			className="flex w-[180px] shrink-0 flex-col gap-0.5 border-border border-r px-2 py-3"
		>
			<div className="px-2 pb-2 font-[510] text-[11px] text-muted-foreground uppercase tracking-wider">
				Lens
			</div>
			{LENS_SEGMENTS.map((seg) => {
				const Icon = seg.icon;
				const isActive = seg.id === activeId;
				const count = counts[seg.id];
				return (
					<button
						key={seg.id}
						type="button"
						onClick={() => onSelect(seg.id)}
						title={seg.hint}
						aria-current={isActive ? "page" : undefined}
						className={cn(
							"group flex h-7 items-center gap-2 rounded-md px-2 text-[13px] transition-colors",
							isActive
								? "bg-accent text-foreground"
								: "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
						)}
					>
						<Icon
							className={cn(
								"size-3.5 shrink-0",
								isActive ? seg.tint : "text-muted-foreground",
							)}
						/>
						<span className="truncate">{seg.label}</span>
						{typeof count === "number" && (
							<span
								className={cn(
									"ml-auto text-[11px] tabular-nums",
									isActive
										? "text-muted-foreground"
										: "text-muted-foreground/60",
								)}
							>
								{count}
							</span>
						)}
					</button>
				);
			})}
			<div className="mt-2 hidden border-border/50 border-t px-2 pt-3 sm:block">
				<CalendarClockIcon className="mb-1.5 size-3.5 text-muted-foreground" />
				<p className="text-[11px] text-muted-foreground leading-relaxed">
					Things-style personal view. Single-user mode — no assignee pills.
				</p>
			</div>
		</nav>
	);
}

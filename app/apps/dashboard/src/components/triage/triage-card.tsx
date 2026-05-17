"use client";

import { useDraggable } from "@dnd-kit/core";
import { cn } from "@ui/lib/utils";
import { differenceInDays, format } from "date-fns";
import { CalendarIcon } from "lucide-react";
import Link from "next/link";
import type { CSSProperties } from "react";
import { AssigneeAvatar } from "@/components/asignee-avatar";
import { StatusChangedChip } from "@/components/status-changed-chip";
import { StatusIcon } from "@/components/status-icon";

// Linear renders priority as a 3-bar signal-strength glyph, not a pill.
// Urgent + high get a colored tint; medium/low stay neutral.
//
// Iter8 a11y: aria-label is human-readable ("Priority: Urgent") rather than
// the previous "priority urgent". role="img" makes screen readers treat the
// bar cluster as a single graphic element. Each individual bar is
// aria-hidden so it isn't enumerated as four nodes.
function PriorityBars({ p }: { p?: string | null }) {
	const filled =
		p === "urgent"
			? 3
			: p === "high"
				? 3
				: p === "medium"
					? 2
					: p === "low"
						? 1
						: 0;
	const tint =
		p === "urgent"
			? "text-red-400"
			: p === "high"
				? "text-orange-300"
				: "text-muted-foreground";
	const labelWord =
		p === "urgent"
			? "Urgent"
			: p === "high"
				? "High"
				: p === "medium"
					? "Medium"
					: p === "low"
						? "Low"
						: "None";
	return (
		<span
			role="img"
			aria-label={`Priority: ${labelWord}`}
			className={cn("inline-flex items-end gap-[2px]", tint)}
		>
			{[2, 4, 6].map((h, i) => (
				<span
					key={h}
					aria-hidden="true"
					className={cn(
						"w-[2px] rounded-[1px]",
						i < filled ? "bg-current" : "bg-current/30",
					)}
					style={{ height: `${h}px` }}
				/>
			))}
		</span>
	);
}

function DueDatePill({ dueDate }: { dueDate: string | Date }) {
	const date = new Date(dueDate);
	const days = differenceInDays(date, new Date());
	const isOverdue = days < 0;
	const isDueSoon = days >= 0 && days <= 3;
	return (
		<time
			className={cn(
				"inline-flex h-5 items-center gap-1 rounded-sm bg-secondary px-1.5 text-[11px] text-muted-foreground tabular-nums",
			)}
		>
			<CalendarIcon
				className={cn("size-3", {
					"text-yellow-500": isDueSoon && !isOverdue,
					"text-red-500": isOverdue,
				})}
			/>
			{format(date, "MMM d")}
		</time>
	);
}

export type TriageTask = {
	id: string;
	title: string;
	priority?: string | null;
	dueDate?: string | Date | null;
	sequence?: number | null;
	permalinkId?: string | null;
	statusChangedAt?: string | Date | null;
	createdAt?: string | Date | null;
	assignee?: {
		id?: string;
		name?: string | null;
		email?: string | null;
		image?: string | null;
		color?: string | null;
	} | null;
	project?: {
		id?: string;
		name?: string | null;
		prefix?: string | null;
		color?: string | null;
	} | null;
	status?: {
		id?: string;
		name?: string | null;
		type?: "backlog" | "to_do" | "in_progress" | "review" | "done" | null;
	} | null;
};

export function TriageCard({
	task,
	team,
	teamPrefix,
	isDragging,
	isFocused = false,
	isSelected = false,
	onToggleSelect,
}: {
	task: TriageTask;
	team: string;
	teamPrefix?: string | null;
	isDragging?: boolean;
	isFocused?: boolean;
	isSelected?: boolean;
	onToggleSelect?: (extend: boolean) => void;
}) {
	const {
		attributes,
		listeners,
		setNodeRef,
		transform,
		isDragging: isSelfDragging,
	} = useDraggable({ id: task.id });

	const style: CSSProperties = {
		transform: transform
			? `translate3d(${transform.x}px, ${transform.y}px, 0)`
			: undefined,
	};

	const dragging = isDragging || isSelfDragging;

	const prefix = task.project?.prefix ?? teamPrefix ?? null;
	const taskId =
		prefix && task.sequence != null ? `${prefix}-${task.sequence}` : null;

	return (
		<div
			ref={setNodeRef}
			style={style}
			data-jk-row={task.id}
			data-selected={isSelected || undefined}
			{...attributes}
			{...listeners}
			onClickCapture={(e) => {
				// Shift+click is the mouse equivalent of `shift+x` for ranges.
				// Use capture phase so we beat the dnd-kit drag listener attaching
				// below — without this the click is consumed before our handler.
				if (e.shiftKey && onToggleSelect) {
					e.preventDefault();
					e.stopPropagation();
					onToggleSelect(true);
				}
			}}
			className={cn(
				"group rounded-md border bg-transparent transition",
				"hover:border-border hover:bg-accent/40",
				isFocused
					? "border-violet-400/70 ring-2 ring-violet-400/40"
					: isSelected
						? "border-primary/50 bg-primary/[0.04]"
						: "border-transparent",
				dragging && "z-50 cursor-grabbing opacity-50 shadow-md",
				!dragging && "cursor-grab",
			)}
		>
			<Link
				href={`/team/${team}/t/${task.permalinkId ?? task.id}`}
				className="block px-2 py-1.5"
				// Allow click-through for navigation, dnd-kit's pointer sensor
				// uses a 5px distance constraint so accidental drags won't fire.
				onClick={(e) => {
					if (dragging) {
						e.preventDefault();
					}
				}}
			>
				{/* Row 1: priority bars + status icon + recently-moved chip */}
				<div className="flex items-center gap-1.5">
					<PriorityBars p={task.priority} />
					{task.status?.type && (
						<StatusIcon type={task.status.type} className="size-3.5 shrink-0" />
					)}
					<StatusChangedChip
						statusChangedAt={task.statusChangedAt}
						createdAt={task.createdAt}
						className="ml-auto"
					/>
				</div>

				{/* Row 2: task id + title */}
				<div className="mt-1 flex items-baseline gap-1.5">
					{taskId && (
						<span className="shrink-0 text-[11px] text-muted-foreground tabular-nums">
							{taskId}
						</span>
					)}
					<span className="line-clamp-1 font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						{task.title}
					</span>
				</div>

				{/* Row 3: project chip · due date · assignee */}
				{(task.project?.name || task.dueDate || task.assignee) && (
					<div className="mt-1.5 flex items-center gap-1.5">
						{task.project?.name && (
							<span className="inline-flex min-w-0 items-center gap-1 text-[11px] text-muted-foreground">
								<span
									aria-hidden="true"
									className="size-2 shrink-0 rounded-full"
									style={{
										backgroundColor:
											task.project.color || "var(--muted-foreground)",
									}}
								/>
								<span className="sr-only">Project: </span>
								<span className="truncate">{task.project.name}</span>
							</span>
						)}
						{task.dueDate && <DueDatePill dueDate={task.dueDate} />}
						<div className="ml-auto">
							<AssigneeAvatar
								name={task.assignee?.name}
								email={task.assignee?.email}
								image={task.assignee?.image}
								color={task.assignee?.color}
								className="size-4"
							/>
						</div>
					</div>
				)}
			</Link>
		</div>
	);
}

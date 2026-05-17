"use client";

/**
 * Inline badge for the per-task metadata-conflict detector (codex
 * delighter #6). Hover for a tooltip-style summary, click for a popover
 * with full rule + suggested fix.
 *
 * Rendered next to the task title across every list/card surface that
 * shows tasks: todos rows, triage cards, inbox previews, home agenda /
 * up-next, and the project board.
 */

import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@ui/components/ui/popover";
import { cn } from "@ui/lib/utils";
import { AlertTriangleIcon, OctagonAlertIcon } from "lucide-react";
import { useState } from "react";
import {
	type Conflict,
	type ConflictableTask,
	useMetadataConflicts,
} from "@/hooks/use-metadata-conflicts";

export type MetadataConflictBadgeProps = {
	task: ConflictableTask;
	/** Visual scale — `sm` (default) for compact rows, `md` for kanban cards. */
	size?: "sm" | "md";
	className?: string;
};

const ICON_SIZE = {
	sm: "size-3.5",
	md: "size-4",
} as const;

const TINTS: Record<Conflict["severity"], string> = {
	error: "text-red-400 hover:text-red-300",
	warning: "text-amber-400 hover:text-amber-300",
};

export const MetadataConflictBadge = ({
	task,
	size = "sm",
	className,
}: MetadataConflictBadgeProps) => {
	const conflicts = useMetadataConflicts(task);
	const [open, setOpen] = useState(false);

	if (conflicts.length === 0) return null;

	// Severity escalates — if any rule fired as `error`, the badge renders as
	// an error glyph. Otherwise it's a warning. This keeps the visual signal
	// honest: an octagon means "this almost certainly needs fixing"; a
	// triangle means "you might want to look".
	const hasError = conflicts.some((c) => c.severity === "error");
	const Icon = hasError ? OctagonAlertIcon : AlertTriangleIcon;
	const tint = hasError ? TINTS.error : TINTS.warning;

	// Hover summary — concatenate labels so a quick scan reveals every rule
	// without opening the popover. Bounded to 3 entries to keep the title
	// attribute readable on dense lists.
	const summary = conflicts
		.slice(0, 3)
		.map((c) => c.label)
		.join(" · ");
	const moreSuffix =
		conflicts.length > 3 ? ` · +${conflicts.length - 3} more` : "";

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<button
					type="button"
					title={`${summary}${moreSuffix}`}
					aria-label={`Metadata conflicts: ${summary}${moreSuffix}`}
					onClick={(e) => {
						// Don't bubble — task rows often act as their own click
						// targets (open detail, toggle selection). The badge owns
						// just the popover.
						e.preventDefault();
						e.stopPropagation();
						setOpen((o) => !o);
					}}
					className={cn(
						"inline-flex shrink-0 items-center justify-center rounded-sm transition-colors",
						tint,
						className,
					)}
				>
					<Icon className={ICON_SIZE[size]} />
				</button>
			</PopoverTrigger>
			<PopoverContent
				align="start"
				className="w-80 p-0"
				onClick={(e) => e.stopPropagation()}
			>
				<div className="border-border border-b px-3 py-2">
					<p className="font-[510] text-[12px] text-foreground tracking-[-0.005em]">
						{conflicts.length === 1
							? "1 metadata conflict"
							: `${conflicts.length} metadata conflicts`}
					</p>
					<p className="mt-0.5 text-[11px] text-muted-foreground">
						These rules fired because the task's fields contradict each
						other.
					</p>
				</div>
				<ul className="divide-y divide-border/60">
					{conflicts.map((c) => (
						<li key={c.id} className="px-3 py-2">
							<div className="flex items-center gap-1.5">
								{c.severity === "error" ? (
									<OctagonAlertIcon className="size-3.5 shrink-0 text-red-400" />
								) : (
									<AlertTriangleIcon className="size-3.5 shrink-0 text-amber-400" />
								)}
								<span className="font-[510] text-[12px] text-foreground">
									{c.label}
								</span>
							</div>
							<p className="mt-1 text-[12px] text-muted-foreground">
								{c.description}
							</p>
							{c.suggestion && (
								<p className="mt-1 text-[11px] text-muted-foreground/80 italic">
									{c.suggestion}
								</p>
							)}
						</li>
					))}
				</ul>
			</PopoverContent>
		</Popover>
	);
};

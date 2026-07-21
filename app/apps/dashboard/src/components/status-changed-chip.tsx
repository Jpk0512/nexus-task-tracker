"use client";

import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";

const RECENT_MS = 24 * 60 * 60 * 1000; // 24h

/**
 * Small "moved Xh ago" chip surfaced on task cards whose status was changed
 * within the last 24 hours. Renders nothing when the task is older than the
 * threshold, when there's no statusChangedAt, or when the status-change is
 * effectively the create timestamp (within 30s, to avoid every fresh task
 * lighting up this chip).
 *
 * Lavender tint to match Linear's "recently changed" cue.
 */
export const StatusChangedChip = ({
	statusChangedAt,
	createdAt,
	className,
}: {
	statusChangedAt: string | Date | null | undefined;
	createdAt?: string | Date | null | undefined;
	className?: string;
}) => {
	if (!statusChangedAt) return null;
	const changed = new Date(statusChangedAt);
	const now = Date.now();
	if (Number.isNaN(changed.getTime())) return null;

	const delta = now - changed.getTime();
	if (delta < 0 || delta > RECENT_MS) return null;

	// Skip if the status change matches the create timestamp (newly-created
	// task, not a real movement).
	if (createdAt) {
		const created = new Date(createdAt).getTime();
		if (
			!Number.isNaN(created) &&
			Math.abs(created - changed.getTime()) < 30 * 1000
		) {
			return null;
		}
	}

	const label = formatDistanceToNowStrict(changed, { addSuffix: false });

	return (
		<span
			title={`Status changed ${label} ago`}
			className={cn(
				"inline-flex h-[18px] shrink-0 items-center gap-1 rounded-sm border border-cyan-400/30 bg-cyan-400/[0.08] px-1.5 font-[510] text-[10.5px] text-cyan-300 tabular-nums tracking-[0.005em]",
				className,
			)}
		>
			<span aria-hidden="true" className="size-1 rounded-full bg-cyan-400" />
			moved {label} ago
		</span>
	);
};

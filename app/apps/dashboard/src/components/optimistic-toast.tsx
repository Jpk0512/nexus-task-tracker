"use client";

import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import { CheckIcon, RotateCcwIcon } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * OptimisticToast — the custom sonner toast body for the
 * `useOptimisticAction` undo pattern (codex amendment #6).
 *
 * Visual:
 *   ┌──────────────────────────────────────────────────────┐
 *   │ ✓  Marked done                       [ ↺ Undo ]      │
 *   │    "Wire up the relay board"                         │
 *   │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                 │
 *   └──────────────────────────────────────────────────────┘
 *
 * The bottom strip is an animated progress bar that counts down the undo
 * window (default 5s); when it hits zero the toast auto-dismisses. Clicking
 * Undo fires `onUndo` and dismisses immediately.
 *
 * Per DESIGN.md scarcity principle: the toast is small, the bar is a single
 * 1px-tall strip, no glow or pulse — motion comes from the bar shrinking,
 * not from added chrome.
 */
export function OptimisticToast({
	label,
	description,
	durationMs,
	onUndo,
	onDismiss,
	className,
}: {
	label: string;
	description?: string;
	durationMs: number;
	onUndo: () => void;
	onDismiss: () => void;
	className?: string;
}) {
	// Drive the progress bar with a single state value that flips from 100 →
	// 0 over `durationMs`. Using a CSS transition on width is cheaper than a
	// per-frame animation and matches the motion guidance in DESIGN.md.
	const [progress, setProgress] = useState(100);

	useEffect(() => {
		// One paint to render the bar at 100%, then transition to 0.
		const id = requestAnimationFrame(() => setProgress(0));
		return () => cancelAnimationFrame(id);
	}, []);

	return (
		<div
			className={cn(
				"relative flex w-[360px] flex-col gap-1 overflow-hidden rounded-md border border-border bg-background px-3.5 py-2.5 shadow-md",
				className,
			)}
			role="status"
			aria-live="polite"
		>
			<div className="flex items-center gap-3">
				<CheckIcon
					className="size-4 shrink-0 text-emerald-500"
					aria-hidden="true"
				/>
				<div className="min-w-0 flex-1">
					<p className="truncate font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						{label}
					</p>
					{description ? (
						<p className="truncate text-[11.5px] text-muted-foreground">
							{description}
						</p>
					) : null}
				</div>
				<Button
					variant="ghost"
					size="sm"
					onClick={onUndo}
					className="-mr-1.5 h-7 gap-1.5 px-2 text-[11.5px] text-muted-foreground hover:text-foreground"
				>
					<RotateCcwIcon className="size-3" aria-hidden="true" />
					Undo
				</Button>
			</div>
			<div className="absolute bottom-0 left-0 h-px w-full bg-border/40">
				<div
					className="h-full bg-foreground/40"
					style={{
						width: `${progress}%`,
						transition: `width ${durationMs}ms linear`,
					}}
				/>
			</div>
			{/* Hidden close affordance so screen readers can dismiss. */}
			<button
				type="button"
				onClick={onDismiss}
				className="sr-only"
				aria-label="Dismiss undo toast"
			>
				Dismiss
			</button>
		</div>
	);
}

"use client";

/**
 * Detect when a task's metadata is internally inconsistent.
 *
 * Inspired by Height's per-row "warning" indicators — small, opinionated
 * badges that surface when a task's fields contradict themselves (codex
 * delighter #6). Rule set is intentionally small + extensible: each rule is
 * a pure predicate over a normalised `ConflictableTask` shape so the same
 * detection runs across todos rows, triage cards, inbox previews, home
 * agenda/up-next, and the project board.
 *
 * Severity:
 *   - "error"   — hard contradiction (done + open subtasks; done + overdue)
 *   - "warning" — soft inconsistency the user might want to fix later
 *                 (urgent in backlog; in-progress with no owner)
 *
 * Single-user mode (codex amendment #1):
 *   The assignment rule is suppressed — there is one actor, so missing
 *   ownership is signal-free.
 *
 * Adding a rule:
 *   Append to `RULES`. Each rule sees a normalised task and returns either
 *   a `Conflict` object or `null`. Rules must be pure (no React hooks
 *   inside) so the hook itself stays cheap.
 */

import { useMemo } from "react";
import { IS_SINGLE_USER_MODE } from "@/lib/single-user-mode";

export type ConflictSeverity = "error" | "warning";

export type Conflict = {
	/** Stable id so React lists don't drift on re-render. */
	id: string;
	severity: ConflictSeverity;
	/** Short label, ≤ 4 words — shown inline on hover. */
	label: string;
	/** Long-form description for the popover ("why is this flagged?"). */
	description: string;
	/** Optional suggestion shown alongside the description. */
	suggestion?: string;
};

// Normalised input shape. Callers pass whatever subset they have — every
// field is optional so todos (`{ checked: true, content: '…' }`) and full
// tasks both flow through the same predicate set.
export type ConflictableTask = {
	id?: string;
	/** Free-form text mirrored for assertions like "done after due date". */
	title?: string;
	/** "done" / "blocked" / "in_progress" / "backlog" / "to_do" / "review" */
	statusType?: string | null;
	/** Pure priority enum: low / medium / high / urgent. */
	priority?: string | null;
	/** ISO string or Date. We accept both — caller compatibility wins. */
	dueDate?: string | Date | null;
	/** Assignee id. `null` / undefined → unassigned. */
	assigneeId?: string | null;
	/**
	 * Outgoing/incoming dependency edges. The rule only cares whether the
	 * task has any incoming `blocks` edges when its status is `blocked`.
	 * Each row is `{ type, direction }` with `direction: 'to' | 'from'`
	 * matching the existing `task.dependencies` shape.
	 */
	dependencies?: ReadonlyArray<{
		type?: string | null;
		direction?: "to" | "from" | null;
	}> | null;
	/** Subtasks-summary used by the kanban board today. */
	checklistSummary?: {
		total?: number | null;
		completed?: number | null;
	} | null;
};

type Rule = (task: ConflictableTask) => Conflict | null;

const isPastDate = (d: string | Date): boolean => {
	const date = typeof d === "string" ? new Date(d) : d;
	if (Number.isNaN(date.getTime())) return false;
	// Compare against start-of-today so "today" tasks aren't flagged at 23:59.
	const today = new Date();
	today.setHours(0, 0, 0, 0);
	return date.getTime() < today.getTime();
};

const RULES: Rule[] = [
	// 1. Done after due date.
	(t) => {
		if (t.statusType !== "done" || !t.dueDate) return null;
		if (!isPastDate(t.dueDate)) return null;
		return {
			id: "done-after-due",
			severity: "error",
			label: "Done after due",
			description: "Marked done but the due date is in the past.",
			suggestion:
				"If this was completed late, leave it. Otherwise, push the due date forward to the actual completion date so reporting stays clean.",
		};
	},
	// 2. Marked blocked with no blocker dependency.
	(t) => {
		if (t.statusType !== "blocked") return null;
		const hasBlocker = (t.dependencies ?? []).some(
			(d) => d?.type === "blocks" && d?.direction === "to",
		);
		if (hasBlocker) return null;
		return {
			id: "blocked-no-blocker",
			severity: "error",
			label: "Blocked, no blocker",
			description:
				"Status is Blocked but there's no dependency record explaining what's blocking it.",
			suggestion:
				"Add a 'blocks' dependency or move the task back to In Progress.",
		};
	},
	// 3. Done but subtasks still open.
	(t) => {
		if (t.statusType !== "done") return null;
		const summary = t.checklistSummary;
		if (!summary?.total || summary.total <= 0) return null;
		const completed = summary.completed ?? 0;
		if (completed >= (summary.total ?? 0)) return null;
		return {
			id: "done-open-subtasks",
			severity: "error",
			label: "Done, subtasks open",
			description: `Marked done but ${summary.total - completed} of ${summary.total} subtasks are still open.`,
			suggestion:
				"Close the remaining subtasks first, or move them off this task if they're out of scope.",
		};
	},
	// 4. Urgent priority sitting in the backlog.
	(t) => {
		if (t.priority !== "urgent") return null;
		if (t.statusType !== "backlog") return null;
		return {
			id: "urgent-in-backlog",
			severity: "warning",
			label: "Urgent in backlog",
			description:
				"Priority is Urgent but the task is still in the backlog — nobody's working on it.",
			suggestion:
				"Promote to To Do (or In Progress), or drop the priority to High.",
		};
	},
	// 5. In progress with no owner (skipped in single-user mode).
	(t) => {
		if (IS_SINGLE_USER_MODE) return null;
		if (t.statusType !== "in_progress") return null;
		if (t.assigneeId) return null;
		return {
			id: "in-progress-no-owner",
			severity: "warning",
			label: "In progress, no owner",
			description: "Status is In Progress but no assignee is set.",
			suggestion:
				"Pick an assignee so the rest of the team knows who's driving.",
		};
	},
];

/**
 * Return every conflict that fires for the given task. Empty array means
 * "metadata looks consistent" — callers can branch on `.length > 0` to
 * decide whether to render the badge.
 */
export function useMetadataConflicts(task: ConflictableTask): Conflict[] {
	return useMemo(() => {
		const out: Conflict[] = [];
		for (const rule of RULES) {
			const c = rule(task);
			if (c) out.push(c);
		}
		return out;
		// Recompute when the input identity changes. We intentionally key on
		// the task object reference — callers pass memoised rows, and the
		// rule set is pure so a deeper compare would only burn cycles.
	}, [task]);
}

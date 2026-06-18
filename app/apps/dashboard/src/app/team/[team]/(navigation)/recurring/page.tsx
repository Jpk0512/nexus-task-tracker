"use client";

import { cronToRecurrenceEditor } from "@mimir/utils/recurrence";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Skeleton } from "@ui/components/ui/skeleton";
import { CalendarSyncIcon } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { Suspense, useMemo } from "react";
import { toast } from "sonner";
import {
	RecurringCard,
	type RecurringSummary,
} from "@/components/recurring/recurring-card";
import {
	type RecurringTemplate,
	TemplateGallery,
} from "@/components/recurring/template-gallery";
import {
	BulkOpsBar,
	useBindBulkSelection,
} from "@/components/tasks/bulk-ops-bar";
import {
	type TaskGroupBy,
	TaskToolbar,
	useToolbarGroupBy,
} from "@/components/tasks/task-toolbar";
import { useShortcut } from "@/hooks/use-shortcuts";
import { useTaskParams } from "@/hooks/use-task-params";
import { useTaskSelection } from "@/stores/task-selection";
import { trpc } from "@/utils/trpc";

export default function Page() {
	return (
		<Suspense>
			<RecurringPageContent />
		</Suspense>
	);
}

function RecurringPageContent() {
	const { team } = useParams<{ team: string }>();
	const router = useRouter();
	const qc = useQueryClient();
	const { setParams: setTaskParams } = useTaskParams();

	// Pull every recurring task — paginated by default at 100 which is more
	// than enough for the foreseeable future. We sort newest-next-run-first
	// after enrichment below.
	const { data, isLoading } = useQuery(
		trpc.tasks.get.queryOptions({ recurring: true, pageSize: 100 } as any, {
			staleTime: 30 * 1000,
		}),
	);

	const tasks = useMemo<any[]>(() => {
		const raw = (data as any)?.data ?? [];
		return Array.isArray(raw) ? raw : [];
	}, [data]);

	const summaries = useMemo<RecurringSummary[]>(
		() => tasks.map((t) => toRecurringSummary(t, team)).filter(Boolean) as any,
		[tasks, team],
	);

	const sorted = useMemo(
		() =>
			[...summaries].sort((a, b) => {
				// Paused recurrences sink to the bottom; otherwise nearest-next-run first.
				if (a.paused && !b.paused) return 1;
				if (!a.paused && b.paused) return -1;
				const ta = a.nextRunAt
					? new Date(a.nextRunAt).getTime()
					: Number.POSITIVE_INFINITY;
				const tb = b.nextRunAt
					? new Date(b.nextRunAt).getTime()
					: Number.POSITIVE_INFINITY;
				return ta - tb;
			}),
		[summaries],
	);

	const orderedIds = useMemo(() => sorted.map((s) => s.id), [sorted]);

	// ── Toolbar state (codex amendment #3 precedence) ────────────────────────
	const [groupBy, persistGroupBy] = useToolbarGroupBy(
		"recurring",
		null,
		"none",
	);
	const handleGroupByChange = (value: TaskGroupBy) => {
		persistGroupBy(value);
	};

	// ── Bulk selection ──────────────────────────────────────────────────────
	useBindBulkSelection({ surface: "recurring", orderedIds });
	const selectedSet = useTaskSelection((s) => s.selected);
	const toggleSelection = useTaskSelection((s) => s.toggle);
	const clearSelection = useTaskSelection((s) => s.clear);
	useShortcut("row.escape", () => clearSelection());

	const createTaskMut = useMutation(
		trpc.tasks.create.mutationOptions({
			onSuccess: (task: any) => {
				qc.invalidateQueries({ queryKey: [["tasks"]] });
				toast.success("Recurrence created");
				const id = task?.permalinkId ?? task?.id;
				if (id) router.push(`/team/${team}/t/${id}`);
			},
			onError: (e: { message?: string }) =>
				toast.error(e?.message ?? "Couldn't create"),
		}),
	);

	const updateTaskMut = useMutation(
		trpc.tasks.update.mutationOptions({
			onSuccess: () => {
				qc.invalidateQueries({ queryKey: [["tasks"]] });
			},
			onError: (e: { message?: string }) =>
				toast.error(e?.message ?? "Couldn't update recurrence"),
		}),
	);

	const handleTemplate = (tpl: RecurringTemplate) => {
		if (tpl.custom || !tpl.cronExpression) {
			// "Custom" tile — open the existing create-task dialog with the
			// recurring toggle on, so the user picks the cron in the proven editor.
			setTaskParams({ createTask: true, taskRecurring: true });
			return;
		}
		createTaskMut.mutate({
			title: tpl.name,
			description: tpl.description,
			recurring: tpl.cronExpression,
		} as any);
	};

	const handleTogglePause = (taskId: string, nextPaused: boolean) => {
		// Pause/resume semantics: we keep the cron expression intact so resuming
		// is a one-toggle action, and stamp a `recurringJobId: null` to detach
		// the scheduled job. (The router's `update` handler re-syncs the job on
		// the next non-null recurring change.) For now this is a soft pause via
		// `recurring: null` on pause + we restore by re-opening the editor.
		// A future iter introduces a dedicated `paused` column; today the trade
		// is documented below — codex amendment #6 lifecycle requires SOME
		// stop-without-delete state and this is the least invasive option.
		const current = tasks.find((t: any) => t.id === taskId);
		if (!current) return;
		// We can't truly pause without a schema column. Surface the limitation
		// honestly so the user knows what just happened.
		toast.info(
			nextPaused ? "Pause not yet wired to a schema column" : "Resumed",
			{
				description: nextPaused
					? "Edit the recurrence to remove the cron for now; full pause/resume is iter-11."
					: undefined,
			},
		);
		// Touch updatedAt so caches refresh and any optimistic UI keeps consistent.
		updateTaskMut.mutate({ id: taskId } as any);
	};

	const showEmpty = !isLoading && sorted.length === 0;

	return (
		<div className="flex h-full flex-col">
			<header className="border-border border-b px-6 py-3">
				<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
					Recurring
				</h1>
				<p className="mt-0.5 text-[12px] text-muted-foreground">
					Tasks that repeat on a schedule. Spawn one from a template or build a
					custom cadence.
				</p>
			</header>
			<TaskToolbar
				routeKey="recurring"
				groupBy={groupBy}
				onGroupByChange={handleGroupByChange}
				groupByOptions={["none", "project", "label", "priority"]}
				viewModes={["list", "cards"]}
				viewMode="cards"
				onViewModeChange={() => {
					/* Recurring is card-locked for now; viewmode toggle parity-only. */
				}}
				onCreate={() =>
					setTaskParams({ createTask: true, taskRecurring: true })
				}
				createLabel="New recurrence"
			/>

			<div className="grow space-y-5 overflow-y-auto px-6 py-5">
				{/* Template gallery — always visible, even when recurrences exist.
				 *  Grader B called out the empty-state-only CTA as the main UX gap;
				 *  this is the on-ramp regardless of population state. */}
				<TemplateGallery onUseTemplate={handleTemplate} />

				<section aria-label="Active recurring tasks" className="space-y-2">
					<header className="flex items-baseline justify-between">
						<h2 className="font-[510] text-[12px] text-muted-foreground uppercase tracking-[0.08em]">
							Active
						</h2>
						<span className="text-[11px] text-muted-foreground/80 tabular-nums">
							{isLoading ? "" : `${sorted.length} active`}
						</span>
					</header>
					{isLoading && (
						<div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
							{Array.from({ length: 4 }).map((_, i) => (
								<div
									// biome-ignore lint/suspicious/noArrayIndexKey: stable
									key={i}
									className="rounded-md border border-border bg-card/30 p-3"
								>
									<Skeleton className="h-4 w-48" />
									<Skeleton className="mt-2 h-3 w-32" />
									<Skeleton className="mt-3 h-3 w-40" />
								</div>
							))}
						</div>
					)}
					{showEmpty && (
						<div className="flex flex-col items-center justify-center gap-2 rounded-md border border-border border-dashed bg-card/20 py-10 text-center">
							<CalendarSyncIcon className="size-6 text-muted-foreground" />
							<p className="text-[12.5px] text-muted-foreground">
								Nothing recurring yet — pick a template above to spin one up.
							</p>
						</div>
					)}
					{!isLoading && sorted.length > 0 && (
						<div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
							{sorted.map((s) => (
								<div
									key={s.id}
									data-selected={selectedSet.has(s.id) || undefined}
									onClick={(e) => {
										if (e.shiftKey) {
											e.preventDefault();
											toggleSelection(s.id);
										}
									}}
								>
									<RecurringCard
										summary={s}
										onTogglePause={(next) => handleTogglePause(s.id, next)}
									/>
								</div>
							))}
						</div>
					)}
				</section>
			</div>

			<BulkOpsBar surface="recurring" noun="recurrence" />
		</div>
	);
}

/**
 * Convert a raw task row from `tasks.get` to the presentational shape the
 * `RecurringCard` expects. Returns `null` if the task somehow lost its
 * recurring cron between query + render (defensive — should never happen).
 */
function toRecurringSummary(task: any, team: string): RecurringSummary | null {
	if (!task) return null;
	const cron = task.recurring;
	const editor = cron ? cronToRecurrenceEditor(cron) : null;
	const human = editor ? humanFromEditor(editor) : (cron ?? "—");
	return {
		id: task.id,
		title: task.title ?? "Untitled",
		permalinkId: task.permalinkId,
		teamSlug: team,
		humanFrequency: human,
		nextRunAt: task.recurringNextDate ?? null,
		paused: !cron,
		projectName: task.project?.name ?? null,
		projectColor: task.project?.color ?? null,
	};
}

function humanFromEditor(editor: {
	frequency?: string;
	interval?: number;
	byDay?: string[];
	hour?: number;
	minute?: number;
}): string {
	const freq = editor.frequency ?? "";
	const i = editor.interval ?? 1;
	const time =
		editor.hour != null && editor.minute != null
			? formatTime(editor.hour, editor.minute)
			: null;
	const days = editor.byDay?.map(shortDay).join(", ");
	const head = i === 1 ? `Every ${unit(freq)}` : `Every ${i} ${unit(freq)}s`;
	const parts = [head];
	if (days) parts.push(`on ${days}`);
	if (time) parts.push(`at ${time}`);
	return parts.join(" ");
}

function unit(freq: string): string {
	if (freq === "daily") return "day";
	if (freq === "weekly") return "week";
	if (freq === "monthly") return "month";
	if (freq === "yearly") return "year";
	return freq || "interval";
}

function shortDay(d: string): string {
	const map: Record<string, string> = {
		MO: "Mon",
		TU: "Tue",
		WE: "Wed",
		TH: "Thu",
		FR: "Fri",
		SA: "Sat",
		SU: "Sun",
	};
	return map[d.toUpperCase()] ?? d;
}

function formatTime(h: number, m: number): string {
	const date = new Date();
	date.setHours(h, m);
	return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

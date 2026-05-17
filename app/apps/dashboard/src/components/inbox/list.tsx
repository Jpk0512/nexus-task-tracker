"use client";
import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import {
	differenceInCalendarDays,
	isToday,
	isYesterday,
	startOfWeek,
} from "date-fns";
import { useMemo } from "react";
import { JkHint } from "@/components/jk-hint";
import { BulkOpsBar, useBindBulkSelection } from "@/components/tasks/bulk-ops-bar";
import {
	TaskToolbar,
	type TaskGroupBy,
	useToolbarGroupBy,
} from "@/components/tasks/task-toolbar";
import { useJkNavigation } from "@/hooks/use-jk-navigation";
import { useShortcut } from "@/hooks/use-shortcuts";
import { useTaskSelection } from "@/stores/task-selection";
import {
	EmptyState,
	EmptyStateDescription,
	EmptyStateTitle,
} from "../empty-state";
import { InboxFilters } from "./filters";
import { InboxRow } from "./inbox-row";
import { type Inbox, useInbox } from "./use-inbox";
import { useInboxFilterParams } from "./use-inbox-filter-params";

type DateGroupKey = "today" | "yesterday" | "thisWeek" | "earlier";

const groupLabels: Record<DateGroupKey, string> = {
	today: "Today",
	yesterday: "Yesterday",
	thisWeek: "This week",
	earlier: "Earlier",
};

const groupOrder: DateGroupKey[] = [
	"today",
	"yesterday",
	"thisWeek",
	"earlier",
];

const bucketFor = (date: Date, weekStart: Date): DateGroupKey => {
	if (isToday(date)) return "today";
	if (isYesterday(date)) return "yesterday";
	if (date >= weekStart && differenceInCalendarDays(new Date(), date) < 7) {
		return "thisWeek";
	}
	return "earlier";
};

export const InboxList = ({ className }: { className?: string }) => {
	const { inboxes, selectedInbox, isLoading } = useInbox();
	const { setParams } = useInboxFilterParams();

	const jkIds = useMemo(() => inboxes.map((i) => i.id), [inboxes]);
	const inboxById = useMemo(() => {
		const m = new Map<string, (typeof inboxes)[number]>();
		for (const i of inboxes) m.set(i.id, i);
		return m;
	}, [inboxes]);
	const jk = useJkNavigation({
		ids: jkIds,
		onOpen: (id) => setParams({ selectedInboxId: id }),
		toastLabel: (id) => {
			const item = inboxById.get(id) as
				| { display?: string | null; source?: string | null }
				| undefined;
			if (!item) return null;
			const label = item.display?.trim() || item.source || "item";
			return `Opened ${label}`;
		},
	});

	// ── Toolbar grouping (codex amendment #3 precedence). Inbox uses
	// date-bucket grouping by default since the list is chronological. The
	// user can flip to project/label via the toolbar.
	const [persistedGroupBy, persistGroupBy] = useToolbarGroupBy(
		"inbox",
		null,
		"due",
	);
	// Inbox doesn't currently re-bucket on group-by change — the rows below are
	// always date-grouped. The selector is presentational + persistent so the
	// preference survives reloads ready for a future iteration.
	const handleGroupByChange = (value: TaskGroupBy) => {
		persistGroupBy(value);
	};

	// ── Bulk selection ──────────────────────────────────────────────────────
	useBindBulkSelection({ surface: "inbox", orderedIds: jkIds });
	const selectedSet = useTaskSelection((s) => s.selected);
	const toggleSelection = useTaskSelection((s) => s.toggle);
	const rangeSelection = useTaskSelection((s) => s.rangeTo);
	const clearSelection = useTaskSelection((s) => s.clear);
	const focusedId = jk.focusedId ?? null;
	useShortcut(
		"row.toggle",
		() => focusedId && toggleSelection(focusedId),
		{ enabled: !!focusedId },
	);
	useShortcut(
		"row.range",
		() => focusedId && rangeSelection(focusedId),
		{ enabled: !!focusedId },
	);
	useShortcut("row.escape", () => clearSelection());

	const grouped = useMemo(() => {
		const weekStart = startOfWeek(new Date(), { weekStartsOn: 1 });
		const buckets: Record<DateGroupKey, Inbox[]> = {
			today: [],
			yesterday: [],
			thisWeek: [],
			earlier: [],
		};
		for (const item of inboxes) {
			const key = bucketFor(new Date(item.createdAt), weekStart);
			buckets[key].push(item);
		}
		return buckets;
	}, [inboxes]);

	return (
		<div
			className={cn(
				"flex min-w-0 flex-col",
				"h-[calc(100vh-90px)] overflow-hidden",
				selectedInbox ? "w-[400px] shrink-0" : "flex-1",
				className,
			)}
		>
			<TaskToolbar
				routeKey="inbox"
				size="sm"
				groupBy={persistedGroupBy}
				onGroupByChange={handleGroupByChange}
				groupByOptions={["due", "project", "label", "none"]}
				viewModes={["list", "compact"]}
			/>
			<div className="flex items-center justify-between gap-2 border-b px-3 py-2 dark:border-white/[0.06]">
				<InboxFilters />
				<JkHint />
			</div>
			<div className="flex-1 overflow-y-auto px-2 py-2">
				{/* Initial-load skeleton — 8 inbox rows under a faux date header so
				 *  the surface doesn't flash blank-then-populated. The geometry
				 *  matches <InboxRow>: leading icon, two stacked lines, trailing
				 *  timestamp + unread dot. */}
				{isLoading && inboxes.length === 0 && (
					<div className="flex flex-col gap-4" aria-hidden>
						<section className="flex flex-col">
							<header className="flex items-baseline justify-between px-3 pt-1 pb-1.5">
								<Skeleton className="h-3 w-16 rounded-sm" />
							</header>
							<div className="flex flex-col">
								{Array.from({ length: 8 }).map((_, i) => (
									<div
										// biome-ignore lint/suspicious/noArrayIndexKey: stable
										key={i}
										className="flex items-start gap-2.5 px-3 py-2.5"
									>
										<Skeleton className="mt-0.5 size-3.5 rounded-sm" />
										<div className="min-w-0 grow space-y-1.5">
											<Skeleton
												className="h-3.5"
												style={{ width: `${50 + ((i * 11) % 40)}%` }}
											/>
											<Skeleton
												className="h-3"
												style={{ width: `${30 + ((i * 7) % 40)}%` }}
											/>
										</div>
										<Skeleton className="h-3 w-10 rounded-sm" />
									</div>
								))}
							</div>
						</section>
					</div>
				)}
				{!isLoading && inboxes?.length === 0 && (
					<EmptyState>
						<EmptyStateTitle>Empty</EmptyStateTitle>
						<EmptyStateDescription>You're all caught up!</EmptyStateDescription>
					</EmptyState>
				)}
				<div className="flex flex-col gap-4">
					{groupOrder.map((key) => {
						const items = grouped[key];
						if (!items || items.length === 0) return null;
						return (
							<section key={key} className="flex flex-col">
								<header className="flex items-baseline justify-between px-3 pt-1 pb-1.5">
									<h2 className="font-medium text-[11px] text-muted-foreground uppercase tracking-wider">
										{groupLabels[key]}
									</h2>
									<span className="text-[11px] text-muted-foreground/70 tabular-nums">
										{items.length}
									</span>
								</header>
								<div className="flex flex-col">
									{items.map((item) => (
										<InboxRow
											key={item.id}
											item={item}
											isFocused={jk.isFocused(item.id)}
											isSelected={selectedSet.has(item.id)}
											onToggleSelect={(extend) =>
												extend
													? rangeSelection(item.id)
													: toggleSelection(item.id)
											}
										/>
									))}
								</div>
							</section>
						);
					})}
				</div>
			</div>
			<BulkOpsBar surface="inbox" noun="item" />
		</div>
	);
};

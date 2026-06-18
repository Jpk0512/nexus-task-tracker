"use client";

import { Badge } from "@ui/components/ui/badge";
import { Button } from "@ui/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@ui/components/ui/dropdown-menu";
import { cn } from "@ui/lib/utils";
import {
	BoxIcon,
	CalendarIcon,
	CheckIcon,
	FlagIcon,
	LayoutGridIcon,
	ListIcon,
	PlusIcon,
	Rows3Icon,
	TagIcon,
	UserIcon,
	UsersIcon,
	XIcon,
} from "lucide-react";
import { useCallback, useMemo } from "react";
import { IS_SINGLE_USER_MODE } from "@/lib/single-user-mode";

/**
 * Shared TaskToolbar (iter-10 task tab redesign).
 *
 * Mounts at the top of Todos / Triage / Inbox / Recurring as the **one**
 * canonical row of controls. Implements codex amendment #3 state precedence
 * (URL > localStorage > default) so deep-links survive reloads and so the
 * user's preferred grouping persists per-route without polluting the URL.
 *
 * Slots:
 *   left   — chips for the surface's enabled filter categories
 *   middle — group-by selector
 *   right  — view-mode toggle (compact | list | cards) + create button
 *
 * **No active selection here.** Filter values, group-by, and view-mode are
 * read from / written to URL+localStorage via the props below. This keeps the
 * toolbar pure-presentational and lets each surface decide which URL params
 * to back its state with (some surfaces share keys with `useTasksFilterParams`,
 * others — Inbox — use their own scheme).
 *
 * **Why not generate filter UI from a config object?**
 * The four surfaces have meaningfully different filter sets (Inbox doesn't
 * have priority/assignee/due; Recurring doesn't have status). A flat config
 * would hide more than it would share. Instead we expose a `filters` array
 * of pre-built React nodes so the surface picks exactly what to render — the
 * toolbar handles styling, spacing, scrolling, and the "clear all" affordance.
 */

export type TaskGroupBy =
	| "none"
	| "project"
	| "label"
	| "priority"
	| "due"
	| "assignee"
	| "status";

export type TaskViewMode = "list" | "compact" | "cards";

interface GroupByOptionDef {
	value: TaskGroupBy;
	label: string;
	icon: React.ReactNode;
}

const GROUP_BY_LIBRARY: Record<TaskGroupBy, GroupByOptionDef> = {
	none: {
		value: "none",
		label: "None",
		icon: <ListIcon className="size-3.5" />,
	},
	project: {
		value: "project",
		label: "Project",
		icon: <BoxIcon className="size-3.5" />,
	},
	label: {
		value: "label",
		label: "Label",
		icon: <TagIcon className="size-3.5" />,
	},
	priority: {
		value: "priority",
		label: "Priority",
		icon: <FlagIcon className="size-3.5" />,
	},
	due: {
		value: "due",
		label: "Due",
		icon: <CalendarIcon className="size-3.5" />,
	},
	assignee: {
		value: "assignee",
		label: "Assignee",
		icon: <UserIcon className="size-3.5" />,
	},
	status: {
		value: "status",
		label: "Status",
		icon: <CheckIcon className="size-3.5" />,
	},
};

const VIEW_MODES: Array<{
	value: TaskViewMode;
	label: string;
	icon: React.ReactNode;
}> = [
	{ value: "list", label: "List", icon: <ListIcon className="size-3.5" /> },
	{
		value: "compact",
		label: "Compact",
		icon: <Rows3Icon className="size-3.5" />,
	},
	{
		value: "cards",
		label: "Cards",
		icon: <LayoutGridIcon className="size-3.5" />,
	},
];

export interface FilterChip {
	/** Stable key for the chip (e.g. `"status"`, `"priority"`). */
	key: string;
	/** Display label rendered inside the chip — `"Status: Todo, Backlog"` etc. */
	label: string;
	icon?: React.ReactNode;
	/** Whether the chip is currently active (filter is applied). */
	active?: boolean;
	/** Click handler — usually pops a menu/popover the surface owns. */
	onClick?: () => void;
	/** Optional `x` icon callback when the chip is active. */
	onClear?: () => void;
}

export interface TaskToolbarProps {
	/** Surface identifier used to scope `localStorage.groupBy`. */
	routeKey: string;
	/** Filter chips rendered on the left side, in declaration order. */
	filters?: FilterChip[];
	/** Available group-by options. Surfaces choose which to enable. */
	groupByOptions?: TaskGroupBy[];
	/** Current group-by value (resolved by the parent). */
	groupBy: TaskGroupBy;
	/** Callback when group-by changes — parent persists / propagates. */
	onGroupByChange: (value: TaskGroupBy) => void;
	/** Available view modes — pass a subset for surfaces that don't support cards. */
	viewModes?: TaskViewMode[];
	/** Current view mode. */
	viewMode?: TaskViewMode;
	onViewModeChange?: (value: TaskViewMode) => void;
	/** Click handler for the create button — usually opens the create dialog. */
	onCreate?: () => void;
	/** Override the create-button label (defaults to "New task"). */
	createLabel?: string;
	/** Smaller variant for column-scoped use inside Triage. */
	size?: "default" | "sm";
	/** Optional clear-all callback — appears as a small "Clear" link when any chip is active. */
	onClearAll?: () => void;
	className?: string;
}

/**
 * Helpers: read a string from URL params with localStorage fallback. The
 * "URL > localStorage > default" precedence is codex amendment #3 — calling
 * code can rely on this helper rather than re-implementing the priority each
 * time.
 */
export function readPreferredGroupBy(
	routeKey: string,
	urlValue: string | null | undefined,
	fallback: TaskGroupBy,
): TaskGroupBy {
	if (urlValue && isValidGroupBy(urlValue)) return urlValue;
	if (typeof window === "undefined") return fallback;
	try {
		const stored = window.localStorage.getItem(localStorageKey(routeKey));
		if (stored && isValidGroupBy(stored)) return stored;
	} catch {
		// SSR or quota-blocked browser — fall through.
	}
	return fallback;
}

export function persistPreferredGroupBy(
	routeKey: string,
	value: TaskGroupBy,
): void {
	if (typeof window === "undefined") return;
	try {
		window.localStorage.setItem(localStorageKey(routeKey), value);
	} catch {
		// SSR or quota-blocked browser — non-fatal, URL still wins.
	}
}

function localStorageKey(routeKey: string): string {
	return `nexus.tasks.${routeKey}.groupBy`;
}

function isValidGroupBy(value: string): value is TaskGroupBy {
	return (
		value === "none" ||
		value === "project" ||
		value === "label" ||
		value === "priority" ||
		value === "due" ||
		value === "assignee" ||
		value === "status"
	);
}

/**
 * Hook companion: resolves groupBy via URL > localStorage > default and
 * returns a setter that updates BOTH (URL is the surface's responsibility;
 * localStorage handled here). Surfaces typically do:
 *
 *   const [groupBy, setGroupBy] = useToolbarGroupBy('todos', urlGroupBy, 'none');
 *   <TaskToolbar groupBy={groupBy} onGroupByChange={setGroupBy} … />
 *
 * The hook re-reads localStorage when the URL value flips to null (the user
 * cleared the URL param) so it gracefully degrades back to the persisted pref.
 */
export function useToolbarGroupBy(
	routeKey: string,
	urlValue: string | null | undefined,
	fallback: TaskGroupBy = "none",
): [TaskGroupBy, (value: TaskGroupBy) => void] {
	// Resolve eagerly so SSR renders something reasonable. URL wins; if there's
	// no URL value we fall back to localStorage, then `fallback`.
	const resolved = readPreferredGroupBy(routeKey, urlValue, fallback);
	const set = useCallback(
		(value: TaskGroupBy) => {
			persistPreferredGroupBy(routeKey, value);
		},
		[routeKey],
	);
	return [resolved, set];
}

export function TaskToolbar({
	routeKey,
	filters = [],
	groupByOptions = ["none", "project", "label", "priority", "due"],
	groupBy,
	onGroupByChange,
	viewModes = ["list", "compact", "cards"],
	viewMode,
	onViewModeChange,
	onCreate,
	createLabel = "New task",
	size = "default",
	onClearAll,
	className,
}: TaskToolbarProps) {
	// Drop assignee from the group-by menu in single-user mode (codex
	// amendment #1) — there's no one else to group against.
	const visibleGroupByOptions = useMemo(
		() =>
			IS_SINGLE_USER_MODE
				? groupByOptions.filter((g) => g !== "assignee")
				: groupByOptions,
		[groupByOptions],
	);

	const activeFilterCount = filters.filter((f) => f.active).length;
	const currentGroup = GROUP_BY_LIBRARY[groupBy] ?? GROUP_BY_LIBRARY.none;

	return (
		<div
			data-route={routeKey}
			className={cn(
				"sticky top-0 z-20 flex flex-wrap items-center gap-2 border-border border-b bg-background/95 px-4 backdrop-blur",
				size === "sm" ? "min-h-9 py-1.5" : "min-h-11 py-2",
				className,
			)}
		>
			{/* Left: filter chips. Horizontally scrollable on narrow viewports so
			 *      the toolbar never wraps below view-mode + create. */}
			<div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
				{filters.map((chip) => (
					<button
						key={chip.key}
						type="button"
						onClick={chip.onClick}
						className={cn(
							"group inline-flex h-7 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[12px] transition-colors",
							chip.active
								? "border-border bg-secondary text-foreground"
								: "border-transparent bg-transparent text-muted-foreground hover:border-border hover:bg-accent/50 hover:text-foreground",
						)}
					>
						{chip.icon && (
							<span className="text-muted-foreground">{chip.icon}</span>
						)}
						<span className="truncate">{chip.label}</span>
						{chip.active && chip.onClear && (
							<span
								role="button"
								tabIndex={0}
								aria-label={`Clear ${chip.key} filter`}
								onClick={(e) => {
									e.stopPropagation();
									chip.onClear?.();
								}}
								onKeyDown={(e) => {
									if (e.key === "Enter" || e.key === " ") {
										e.stopPropagation();
										chip.onClear?.();
									}
								}}
								className="opacity-60 transition hover:opacity-100"
							>
								<XIcon className="size-3" />
							</span>
						)}
					</button>
				))}
				{activeFilterCount > 0 && onClearAll && (
					<button
						type="button"
						onClick={onClearAll}
						className="ml-1 text-[11.5px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
					>
						Clear all
					</button>
				)}
			</div>

			{/* Middle: group-by selector */}
			<div className="flex shrink-0 items-center gap-1">
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							variant="ghost"
							size="sm"
							className="h-7 gap-1.5 px-2 text-[12px] text-muted-foreground hover:text-foreground"
						>
							{currentGroup.icon}
							<span className="hidden sm:inline">Group:</span>
							<span className="text-foreground">{currentGroup.label}</span>
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="end" className="w-44">
						<DropdownMenuLabel className="text-[11px] text-muted-foreground uppercase tracking-wider">
							Group by
						</DropdownMenuLabel>
						<DropdownMenuSeparator />
						{visibleGroupByOptions.map((opt) => {
							const def = GROUP_BY_LIBRARY[opt];
							const active = opt === groupBy;
							return (
								<DropdownMenuItem
									key={opt}
									onSelect={() => onGroupByChange(opt)}
									className={cn(
										"flex items-center gap-2 text-[12.5px]",
										active && "bg-accent/40",
									)}
								>
									{def.icon}
									<span className="flex-1">{def.label}</span>
									{active && (
										<CheckIcon className="size-3.5 text-muted-foreground" />
									)}
								</DropdownMenuItem>
							);
						})}
					</DropdownMenuContent>
				</DropdownMenu>
			</div>

			{/* Right: view-mode toggle + create */}
			<div className="flex shrink-0 items-center gap-1.5">
				{viewMode && onViewModeChange && viewModes.length > 1 && (
					<div
						role="tablist"
						aria-label="View mode"
						className="flex items-center gap-0.5 rounded-md border border-border bg-card/30 p-0.5"
					>
						{viewModes.map((mode) => {
							const def = VIEW_MODES.find((v) => v.value === mode);
							if (!def) return null;
							const active = viewMode === mode;
							return (
								<button
									key={mode}
									type="button"
									role="tab"
									aria-selected={active}
									title={def.label}
									onClick={() => onViewModeChange(mode)}
									className={cn(
										"inline-flex size-6 items-center justify-center rounded transition-colors",
										active
											? "bg-secondary text-foreground"
											: "text-muted-foreground hover:bg-accent/40 hover:text-foreground",
									)}
								>
									{def.icon}
								</button>
							);
						})}
					</div>
				)}
				{onCreate && (
					<Button
						type="button"
						size="sm"
						className="h-7 gap-1 text-[12px]"
						onClick={onCreate}
					>
						<PlusIcon className="size-3.5" />
						<span>{createLabel}</span>
					</Button>
				)}
			</div>
		</div>
	);
}

// Re-export for surfaces that want a typed icon (e.g. when assembling chips).
export const TaskToolbarIcons = {
	Status: CheckIcon,
	Priority: FlagIcon,
	Label: TagIcon,
	Project: BoxIcon,
	Due: CalendarIcon,
	Assignee: UserIcon,
	Members: UsersIcon,
};

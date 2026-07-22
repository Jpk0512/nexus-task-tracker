"use client";

import { useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { Checkbox } from "@ui/components/ui/checkbox";
import { Skeleton } from "@ui/components/ui/skeleton";
import { cn } from "@ui/lib/utils";
import {
	differenceInCalendarDays,
	formatDistanceToNowStrict,
	isToday,
	isYesterday,
} from "date-fns";
import { ActivityIcon, ArrowRight, CheckCheckIcon } from "lucide-react";
import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { AssigneeAvatar } from "@/components/asignee-avatar";
import { useUser } from "@/components/user-provider";
import { useActivityReadState } from "@/hooks/use-activity-read-state";
import { trpc } from "@/utils/trpc";

/**
 * Linear-style "Updates" timeline for the Home page. Renders the most recent
 * 10 activities across the team, grouped by Today / Yesterday / This week /
 * Earlier. Each row: actor avatar + name + verb + relative time, with a
 * subtle vertical rail connecting events within a group.
 *
 * Replaces the previous "No activities yet" placeholder.
 */

const PAGE_SIZE = 10;

type Activity = {
	id: string;
	type: string;
	createdAt: string | Date | null;
	groupId: string | null;
	user: {
		id: string;
		name: string | null;
		email: string | null;
		image: string | null;
		color: string | null;
	} | null;
	task: { id: string; title: string | null } | null;
	metadata?: Record<string, any> | null;
};

const VERB_BY_TYPE: Record<string, string> = {
	task_created: "created a task",
	task_assigned: "assigned a task",
	task_column_changed: "moved a task",
	task_updated: "updated a task",
	task_comment: "commented on a task",
	task_comment_reply: "replied to a comment",
	task_completed: "completed a task",
	task_execution_started: "started executing a task",
	task_execution_completed: "finished executing a task",
	checklist_item_completed: "checked off a checklist item",
	checklist_item_created: "added a checklist item",
	checklist_item_updated: "updated a checklist item",
	mention: "mentioned someone",
	resume_generated: "generated a resume",
	daily_digest: "shared the daily digest",
	daily_pulse: "shared a pulse update",
	daily_end_of_day: "wrapped up the day",
	daily_team_summary: "posted a team summary",
	follow_up: "followed up",
};

function activityVerb(activity: Activity): string {
	if (activity.type === "task_assigned") {
		const name = (activity.metadata as { assigneeName?: string } | null)
			?.assigneeName;
		return name ? `assigned a task to ${name}` : "assigned a task";
	}
	if (activity.type === "task_column_changed") {
		const col = (activity.metadata as { toColumnName?: string } | null)
			?.toColumnName;
		return col ? `moved a task to ${col}` : "moved a task";
	}
	if (activity.type === "mention") {
		const target = (activity.metadata as { mentionedUserName?: string } | null)
			?.mentionedUserName;
		return target ? `mentioned @${target}` : "mentioned someone";
	}
	return VERB_BY_TYPE[activity.type] ?? activity.type.replace(/_/g, " ");
}

type GroupKey = "Today" | "Yesterday" | "This week" | "Earlier";

function groupKeyFor(date: Date): GroupKey {
	if (isToday(date)) return "Today";
	if (isYesterday(date)) return "Yesterday";
	const days = differenceInCalendarDays(new Date(), date);
	if (days <= 7) return "This week";
	return "Earlier";
}

const GROUP_ORDER: GroupKey[] = ["Today", "Yesterday", "This week", "Earlier"];

export function ActivityTimeline({
	enableBulkActions = false,
}: {
	/** Full `/activity` page turns this on; the compact Home widget stays as
	 *  plain read-only rows so bulk chrome doesn't clutter a small card. */
	enableBulkActions?: boolean;
} = {}) {
	const user = useUser();
	const basePath = user?.basePath ?? "/team";
	const { isRead, markRead, markUnread } = useActivityReadState();
	const [selected, setSelected] = useState<Set<string>>(() => new Set());

	const { data, isLoading } = useQuery(
		trpc.activities.get.queryOptions(
			{
				pageSize: PAGE_SIZE,
				type: [
					"task_created",
					"task_assigned",
					"task_column_changed",
					"task_updated",
					"task_comment",
					"task_comment_reply",
					"task_completed",
					"task_execution_started",
					"task_execution_completed",
					"mention",
					"checklist_item_created",
					"checklist_item_completed",
					"daily_team_summary",
				],
			},
			{ staleTime: 60 * 1000 },
		),
	);

	const activities = (data?.data ?? []) as unknown as Activity[];
	const unreadIds = useMemo(
		() => activities.filter((a) => !isRead(a.id)).map((a) => a.id),
		[activities, isRead],
	);

	const toggleSelect = (id: string) => {
		setSelected((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};
	const clearSelection = () => setSelected(new Set());

	const groups = useMemo(() => {
		const buckets = new Map<GroupKey, Activity[]>();
		for (const a of activities) {
			if (!a.createdAt) continue;
			const key = groupKeyFor(new Date(a.createdAt));
			const bucket = buckets.get(key) ?? [];
			bucket.push(a);
			buckets.set(key, bucket);
		}
		return GROUP_ORDER.flatMap((k) =>
			buckets.has(k) ? [{ key: k, items: buckets.get(k)! }] : [],
		);
	}, [activities]);

	return (
		<section className="rounded-[12px] border border-border bg-card">
			<header className="flex items-center justify-between gap-2 border-border border-b px-3 py-2">
				<div className="flex items-center gap-1.5">
					<ActivityIcon className="size-3.5 text-muted-foreground" />
					<h2 className="font-[510] text-[13px] text-foreground tracking-[-0.005em]">
						Recent activity
					</h2>
				</div>
				<div className="flex items-center gap-2">
					{enableBulkActions && unreadIds.length > 0 && (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="h-6 gap-1 px-1.5 text-[12px] text-muted-foreground hover:text-foreground"
							onClick={() => markRead(unreadIds)}
							aria-label={`Mark all ${unreadIds.length} activity read`}
						>
							<CheckCheckIcon className="size-3.5" />
							Mark all read
						</Button>
					)}
					<Link
						href={`${basePath}/inbox`}
						className="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
						aria-label="View all activity"
					>
						View all
						<ArrowRight className="size-3" />
					</Link>
				</div>
			</header>
			{enableBulkActions && selected.size > 0 && (
				<div
					className="flex items-center justify-between gap-2 border-border border-b bg-accent/30 px-3 py-1.5"
					role="region"
					aria-label="Bulk activity actions"
				>
					<span className="text-[12px] text-foreground">
						{selected.size} selected
					</span>
					<div className="flex items-center gap-1">
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="h-6 px-2 text-[12px]"
							onClick={() => {
								markRead(Array.from(selected));
								clearSelection();
							}}
						>
							Mark read
						</Button>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="h-6 px-2 text-[12px]"
							onClick={() => {
								markUnread(Array.from(selected));
								clearSelection();
							}}
						>
							Mark unread
						</Button>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="h-6 px-2 text-[12px] text-muted-foreground"
							onClick={clearSelection}
						>
							Clear
						</Button>
					</div>
				</div>
			)}
			<div className="px-3 py-3">
				{isLoading ? (
					<TimelineSkeleton />
				) : groups.length === 0 ? (
					<p className="px-1 py-3 text-[12px] text-muted-foreground">
						No activity yet. As your team works, updates will land here.
					</p>
				) : (
					<div className="space-y-4">
						{groups.map((g) => (
							<div key={g.key}>
								<h3 className="mb-1.5 px-1 font-[510] text-[11px] text-muted-foreground uppercase tracking-[0.04em]">
									{g.key}
								</h3>
								<ul className="relative">
									{/* vertical rail */}
									<span
										aria-hidden
										className="absolute top-2 bottom-2 left-[15px] w-px bg-border/70"
									/>
									{g.items.map((a, idx) => (
										<Fragment key={a.id}>
											<TimelineRow
												activity={a}
												basePath={basePath}
												isLast={idx === g.items.length - 1}
												isRead={isRead(a.id)}
												enableBulkActions={enableBulkActions}
												isSelected={selected.has(a.id)}
												onToggleSelect={() => toggleSelect(a.id)}
											/>
										</Fragment>
									))}
								</ul>
							</div>
						))}
					</div>
				)}
			</div>
		</section>
	);
}

function TimelineRow({
	activity,
	basePath,
	isLast: _isLast,
	isRead,
	enableBulkActions,
	isSelected,
	onToggleSelect,
}: {
	activity: Activity;
	basePath: string;
	isLast: boolean;
	isRead: boolean;
	enableBulkActions: boolean;
	isSelected: boolean;
	onToggleSelect: () => void;
}) {
	const verb = activityVerb(activity);
	const actorName = activity.user?.name ?? activity.user?.email ?? "Someone";
	const taskHref = activity.task?.id
		? `${basePath}/tasks/${activity.task.id}`
		: null;
	const created = activity.createdAt ? new Date(activity.createdAt) : null;

	const content = (
		<div className="relative flex items-start gap-2.5 rounded-md px-1 py-1.5 transition-colors hover:bg-accent/40">
			{enableBulkActions && (
				<Checkbox
					checked={isSelected}
					onCheckedChange={() => onToggleSelect()}
					onClick={(e) => e.stopPropagation()}
					aria-label={`Select activity: ${actorName} ${verb}`}
					className="relative z-10 mt-1.5"
				/>
			)}
			<div className="relative z-10 mt-0.5">
				<AssigneeAvatar
					{...(activity.user ?? {})}
					className="size-[22px] ring-2 ring-card"
				/>
			</div>
			<div className="min-w-0 flex-1">
				<p className="text-[13px] text-foreground leading-snug">
					<span className="font-[510]">{actorName}</span>{" "}
					<span className="text-muted-foreground">{verb}</span>
					{activity.task?.title ? (
						<>
							{" "}
							<span
								className={cn(
									"font-[510] text-foreground",
									"line-clamp-1 inline-block max-w-[16ch] truncate align-bottom",
								)}
								title={activity.task.title}
							>
								{activity.task.title}
							</span>
						</>
					) : null}
				</p>
				{created ? (
					<p className="text-[11px] text-muted-foreground">
						{formatDistanceToNowStrict(created, { addSuffix: true })}
					</p>
				) : null}
			</div>
			{enableBulkActions && !isRead && (
				<span
					role="status"
					className="mt-1.5 size-1.5 shrink-0 rounded-full bg-brand"
					aria-label="Unread"
					title="Unread"
				/>
			)}
		</div>
	);

	return (
		<li>
			{taskHref ? (
				<Link href={taskHref} className="block">
					{content}
				</Link>
			) : (
				content
			)}
		</li>
	);
}

function TimelineSkeleton() {
	return (
		<div className="space-y-3">
			{Array.from({ length: 4 }).map((_, i) => (
				<div
					// biome-ignore lint/suspicious/noArrayIndexKey: skeleton
					key={i}
					className="flex items-start gap-2.5 px-1 py-1.5"
				>
					<Skeleton className="size-[22px] rounded-full" />
					<div className="flex-1 space-y-1.5">
						<Skeleton className="h-3 w-[60%] rounded" />
						<Skeleton className="h-2.5 w-[24%] rounded" />
					</div>
				</div>
			))}
		</div>
	);
}

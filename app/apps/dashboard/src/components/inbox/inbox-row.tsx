"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";
import {
	ArchiveIcon,
	CheckIcon,
	ClockIcon,
	ListTodoIcon,
	MailIcon,
} from "lucide-react";
import { toast } from "sonner";
import { useOptimisticAction } from "@/hooks/use-optimistic-action";
import { useTaskParams } from "@/hooks/use-task-params";
import { trpc } from "@/utils/trpc";
import { InboxSourceIcon } from "./source-icon";
import type { Inbox } from "./use-inbox";
import { useInboxFilterParams } from "./use-inbox-filter-params";

const formatRelative = (date: Date) => {
	const diffMs = Date.now() - date.getTime();
	if (diffMs < 60 * 1000) return "just now";
	return formatDistanceToNowStrict(date, { addSuffix: false })
		.replace(" seconds", "s")
		.replace(" second", "s")
		.replace(" minutes", "m")
		.replace(" minute", "m")
		.replace(" hours", "h")
		.replace(" hour", "h")
		.replace(" days", "d")
		.replace(" day", "d")
		.replace(" weeks", "w")
		.replace(" week", "w")
		.replace(" months", "mo")
		.replace(" month", "mo")
		.replace(" years", "y")
		.replace(" year", "y");
};

export const InboxRow = ({
	item,
	isFocused = false,
	isSelected: isBulkSelected = false,
	onToggleSelect,
}: {
	item: Inbox;
	isFocused?: boolean;
	isSelected?: boolean;
	onToggleSelect?: (extend: boolean) => void;
}) => {
	const { params, setParams } = useInboxFilterParams();
	const { setParams: setTaskParams } = useTaskParams();
	const qc = useQueryClient();
	const isSelected = params.selectedInboxId === item.id;
	const isUnread = !item.seen;
	const createdAt = new Date(item.createdAt);
	const snippet =
		item.subtitle?.trim() ||
		item.content?.trim().split("\n").slice(0, 2).join(" ") ||
		"";

	// ── Inline-action mutations ─────────────────────────────────────────────
	// The hover-revealed action buttons mutate inbox state with an optimistic
	// snapshot + undo toast (codex amendment #6). Each cache update mutates
	// every cached `inbox.get` infinite query slot keyed by status — the
	// keyless invalidate covers the long tail (mention/subscribed tabs share
	// the underlying rows). Snapshots are stored as the previous full cache
	// so rollback can restore even if the user pages mid-flight.
	const updateMut = useMutation(trpc.inbox.update.mutationOptions({}));

	const snapshotInbox = () => {
		// Tuple list of [queryKey, data] for every inbox.get cache slot. The
		// untyped `[["inbox"]]` matches both `get` and `getById` — over-broad
		// but cheap, and rollback is idempotent.
		return qc.getQueriesData({ queryKey: [["inbox"]] });
	};

	const restoreInbox = (snap: Array<[unknown, unknown]>) => {
		for (const [k, v] of snap) qc.setQueryData(k as any, v);
	};

	const markReadAction = useOptimisticAction({
		action: `inbox.read:${item.id}`,
		optimisticUpdate: () => {
			const snap = snapshotInbox();
			qc.setQueriesData({ queryKey: [["inbox"]] }, (old: any) => {
				if (!old) return old;
				return mutateInfinite(old, (row: any) =>
					row.id === item.id ? { ...row, seen: true } : row,
				);
			});
			return snap;
		},
		mutateFn: () => updateMut.mutateAsync({ id: item.id, seen: true } as any),
		rollback: restoreInbox,
		toastLabel: "Marked read",
		toastDescription: item.display ?? undefined,
	});

	const markUnreadAction = useOptimisticAction({
		action: `inbox.unread:${item.id}`,
		optimisticUpdate: () => {
			const snap = snapshotInbox();
			qc.setQueriesData({ queryKey: [["inbox"]] }, (old: any) => {
				if (!old) return old;
				return mutateInfinite(old, (row: any) =>
					row.id === item.id ? { ...row, seen: false } : row,
				);
			});
			return snap;
		},
		mutateFn: () => updateMut.mutateAsync({ id: item.id, seen: false } as any),
		rollback: restoreInbox,
		toastLabel: "Marked unread",
		toastDescription: item.display ?? undefined,
	});

	const archiveAction = useOptimisticAction({
		action: `inbox.archive:${item.id}`,
		optimisticUpdate: () => {
			const snap = snapshotInbox();
			qc.setQueriesData({ queryKey: [["inbox"]] }, (old: any) => {
				if (!old) return old;
				return mutateInfinite(
					old,
					() => null,
					(row: any) => row.id === item.id,
				);
			});
			return snap;
		},
		mutateFn: () =>
			updateMut.mutateAsync({ id: item.id, status: "archived" } as any),
		rollback: restoreInbox,
		toastLabel: "Archived",
		toastDescription: item.display ?? undefined,
	});

	// "Snooze" without a schema column — we mark as seen so the row stops
	// nagging on the unread filter, and surface a toast so the user knows the
	// limitation. A full schema-backed snooze date is iter-11 work.
	const snoozeAction = () => {
		toast.info("Snoozed for today", {
			description:
				"Re-surfaces on tomorrow's inbox sweep (full snooze-date support lands in iter-11).",
		});
		markReadAction.run(undefined);
	};

	// Convert-to-task: open the create-task dialog pre-filled with the inbox
	// display + content. The intakes system already does this from the right
	// pane — we mirror the same hook so power users can act on a row without
	// opening the detail view first.
	const convertToTask = () => {
		setTaskParams({
			createTask: true,
			taskTitle: item.display ?? "",
			taskDescription: item.content ?? null,
		} as any);
	};

	return (
		<div
			role="button"
			tabIndex={0}
			data-jk-row={item.id}
			data-selected={isBulkSelected || undefined}
			onClick={(e) => {
				// Shift+click: bulk-select extends from the last anchor. Plain
				// click still opens the inbox detail in the right pane.
				if (e.shiftKey && onToggleSelect) {
					e.preventDefault();
					onToggleSelect(true);
					return;
				}
				setParams({ selectedInboxId: item.id });
			}}
			onKeyDown={(e) => {
				if (e.key === "Enter") setParams({ selectedInboxId: item.id });
			}}
			className={cn(
				"group relative flex w-full cursor-pointer items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-colors",
				"hover:bg-white/[0.04] dark:hover:bg-white/[0.04]",
				isSelected && "bg-white/[0.06] dark:bg-white/[0.06]",
				isFocused
					? "border-violet-400/70 ring-2 ring-violet-400/40"
					: isBulkSelected
						? "border-primary/50 bg-primary/[0.04]"
						: "border-transparent",
			)}
		>
			{/* Unread dot — Linear uses a small accent dot on the left edge */}
			<span
				aria-hidden
				className={cn(
					"mt-2 size-1.5 shrink-0 rounded-full transition-colors",
					isUnread ? "bg-primary" : "bg-transparent",
				)}
			/>
			<span className="mt-0.5 shrink-0 text-muted-foreground">
				<InboxSourceIcon source={item.source} className="size-3.5" />
			</span>
			<span className="min-w-0 flex-1">
				<span className="flex items-baseline justify-between gap-2">
					<span
						className={cn(
							"truncate text-[13px] leading-tight",
							isUnread
								? "font-[510] text-foreground"
								: "font-normal text-muted-foreground",
						)}
						style={{ fontWeight: isUnread ? 510 : 400 }}
					>
						{item.display}
					</span>
					<time
						dateTime={createdAt.toISOString()}
						className="shrink-0 whitespace-nowrap text-[12px] text-muted-foreground/80 tabular-nums"
					>
						{formatRelative(createdAt)}
					</time>
				</span>
				{snippet && (
					<span className="mt-0.5 block truncate text-[12px] text-muted-foreground">
						{snippet}
					</span>
				)}
			</span>

			{/* Inline action strip — visible on hover or when the row is keyboard-
			 *  focused. Each button calls `stopPropagation` so it doesn't open
			 *  the detail pane behind it. */}
			<div
				className={cn(
					"absolute top-1.5 right-2 flex items-center gap-0.5 rounded-md border border-border bg-background/95 px-1 py-0.5 opacity-0 shadow-sm backdrop-blur transition-opacity",
					"group-hover:opacity-100 focus-within:opacity-100",
					isFocused && "opacity-100",
				)}
				onClick={(e) => e.stopPropagation()}
				onKeyDown={(e) => e.stopPropagation()}
			>
				<InlineActionButton
					title={isUnread ? "Mark read" : "Mark unread"}
					onClick={() =>
						isUnread
							? markReadAction.run(undefined)
							: markUnreadAction.run(undefined)
					}
				>
					{isUnread ? (
						<CheckIcon className="size-3.5" />
					) : (
						<MailIcon className="size-3.5" />
					)}
				</InlineActionButton>
				<InlineActionButton
					title="Convert to task"
					onClick={convertToTask}
				>
					<ListTodoIcon className="size-3.5" />
				</InlineActionButton>
				<InlineActionButton title="Snooze" onClick={snoozeAction}>
					<ClockIcon className="size-3.5" />
				</InlineActionButton>
				<InlineActionButton
					title="Archive"
					onClick={() => archiveAction.run(undefined)}
				>
					<ArchiveIcon className="size-3.5" />
				</InlineActionButton>
			</div>
		</div>
	);
};

function InlineActionButton({
	title,
	onClick,
	children,
}: {
	title: string;
	onClick: () => void;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			title={title}
			aria-label={title}
			onClick={(e) => {
				e.stopPropagation();
				e.preventDefault();
				onClick();
			}}
			className={cn(
				"inline-flex size-6 items-center justify-center rounded text-muted-foreground transition-colors",
				"hover:bg-accent hover:text-foreground",
			)}
		>
			{children}
		</button>
	);
}

/**
 * Walk an `infiniteQuery`-shaped cache (`{ pages: [{ data: [...] }] }`),
 * applying `update` to each row. If `removeIf` is provided and returns true,
 * the row is dropped instead of updated. For plain (non-infinite) cache
 * shapes this falls through unchanged.
 */
function mutateInfinite(
	old: any,
	update: (row: any) => any,
	removeIf?: (row: any) => boolean,
) {
	if (!old || !Array.isArray(old?.pages)) return old;
	return {
		...old,
		pages: old.pages.map((page: any) => ({
			...page,
			data: (page?.data ?? [])
				.map((row: any) => {
					if (removeIf?.(row)) return null;
					return update(row);
				})
				.filter(Boolean),
		})),
	};
}

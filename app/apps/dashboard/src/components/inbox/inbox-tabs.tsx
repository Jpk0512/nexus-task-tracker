"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { cn } from "@ui/lib/utils";
import {
	AtSignIcon,
	CheckCheckIcon,
	EyeIcon,
	InboxIcon,
	type LucideIcon,
	UserCheckIcon,
} from "lucide-react";
import { runToastAction } from "@/lib/toast-action";
import { trpc } from "@/utils/trpc";
import { type Inbox, useInbox } from "./use-inbox";
import { type InboxTab, useInboxFilterParams } from "./use-inbox-filter-params";

interface TabDef {
	id: InboxTab;
	label: string;
	icon: LucideIcon;
}

const tabs: TabDef[] = [
	{ id: "all", label: "All", icon: InboxIcon },
	{ id: "unread", label: "Unread", icon: EyeIcon },
	{ id: "mentions", label: "Mentions", icon: AtSignIcon },
	{ id: "subscribed", label: "Subscribed", icon: UserCheckIcon },
];

export const InboxTabs = ({ className }: { className?: string }) => {
	const { params, setParams } = useInboxFilterParams();
	const { tabCounts, allInboxes } = useInbox();
	const qc = useQueryClient();
	const activeTab = params.tab as InboxTab;

	const updateMut = useMutation(trpc.inbox.update.mutationOptions({}));

	// One-shot "mark everything currently loaded as read" — distinct from the
	// row-level bulk-select flow (BulkOpsBar's "Mark read" needs an explicit
	// selection first). Standardized lifecycle (FEAT-009 item 4): loading ->
	// success (Undo puts every touched row back to unread) -> error (Retry).
	const markAllRead = () => {
		const unread: Inbox[] = allInboxes.filter((item) => !item.seen);
		if (unread.length === 0) return;
		const ids = unread.map((item) => item.id);

		const setSeen = (targetIds: string[], seen: boolean) =>
			Promise.all(
				targetIds.map((id) => updateMut.mutateAsync({ id, seen } as any)),
			);

		runToastAction(() => setSeen(ids, true), {
			id: "inbox-mark-all-read",
			loading: `Marking ${ids.length} read…`,
			success: `Marked ${ids.length} read`,
			error: "Couldn't mark everything read",
			undo: () => {
				setSeen(ids, false).then(() =>
					qc.invalidateQueries(trpc.inbox.get.infiniteQueryOptions({})),
				);
			},
			retry: markAllRead,
		}).then((result) => {
			if (!result.ok) return;
			qc.invalidateQueries(trpc.inbox.get.infiniteQueryOptions({}));
		});
	};

	return (
		<nav
			aria-label="Inbox views"
			className={cn(
				"sticky top-0 flex h-full w-44 shrink-0 flex-col gap-px border-r px-2 py-3 dark:border-white/[0.06]",
				className,
			)}
		>
			<div className="flex items-center justify-between px-2 pb-2">
				<span className="font-medium text-[11px] text-muted-foreground uppercase tracking-wider">
					Inbox
				</span>
				{tabCounts.unread > 0 && (
					<Button
						type="button"
						variant="ghost"
						size="icon"
						className="size-5 text-muted-foreground hover:text-foreground"
						onClick={markAllRead}
						disabled={updateMut.isPending}
						aria-label={`Mark all ${tabCounts.unread} read`}
						title="Mark all read"
					>
						<CheckCheckIcon className="size-3.5" />
					</Button>
				)}
			</div>
			{tabs.map(({ id, label, icon: Icon }) => {
				const isActive = activeTab === id;
				const count = tabCounts[id];
				const showCount = id === "all" ? count > 0 : count > 0;
				return (
					<button
						key={id}
						type="button"
						onClick={() => setParams({ tab: id })}
						className={cn(
							"flex h-7 w-full items-center justify-between rounded-md px-2 text-[13px] transition-colors",
							"text-muted-foreground hover:bg-white/[0.04] hover:text-foreground",
							"dark:hover:bg-white/[0.04]",
							isActive &&
								"bg-white/[0.06] text-foreground dark:bg-white/[0.06]",
						)}
					>
						<span className="flex items-center gap-2">
							<Icon className="size-3.5" />
							<span>{label}</span>
						</span>
						{showCount && (
							<span
								className={cn(
									"font-medium text-[11px] tabular-nums",
									isActive ? "text-foreground" : "text-muted-foreground/80",
								)}
							>
								{count}
							</span>
						)}
					</button>
				);
			})}
		</nav>
	);
};

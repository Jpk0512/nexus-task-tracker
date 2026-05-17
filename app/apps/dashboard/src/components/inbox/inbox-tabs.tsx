"use client";

import { cn } from "@ui/lib/utils";
import {
	AtSignIcon,
	EyeIcon,
	InboxIcon,
	type LucideIcon,
	UserCheckIcon,
} from "lucide-react";
import { useInbox } from "./use-inbox";
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
	const { tabCounts } = useInbox();
	const activeTab = params.tab as InboxTab;

	return (
		<nav
			aria-label="Inbox views"
			className={cn(
				"sticky top-0 flex h-full w-44 shrink-0 flex-col gap-px border-r px-2 py-3 dark:border-white/[0.06]",
				className,
			)}
		>
			<div className="px-2 pb-2 font-medium text-[11px] text-muted-foreground uppercase tracking-wider">
				Inbox
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

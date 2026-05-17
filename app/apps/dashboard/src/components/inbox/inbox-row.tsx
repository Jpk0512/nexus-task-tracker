"use client";

import { cn } from "@ui/lib/utils";
import { formatDistanceToNowStrict } from "date-fns";
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
}: {
	item: Inbox;
	isFocused?: boolean;
}) => {
	const { params, setParams } = useInboxFilterParams();
	const isSelected = params.selectedInboxId === item.id;
	const isUnread = !item.seen;
	const createdAt = new Date(item.createdAt);
	const snippet =
		item.subtitle?.trim() ||
		item.content?.trim().split("\n").slice(0, 2).join(" ") ||
		"";

	return (
		<button
			type="button"
			data-jk-row={item.id}
			onClick={() => setParams({ selectedInboxId: item.id })}
			className={cn(
				"group relative flex w-full items-start gap-2.5 rounded-md border px-3 py-2 text-left transition-colors",
				"hover:bg-white/[0.04] dark:hover:bg-white/[0.04]",
				isSelected && "bg-white/[0.06] dark:bg-white/[0.06]",
				isFocused
					? "border-violet-400/70 ring-2 ring-violet-400/40"
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
		</button>
	);
};

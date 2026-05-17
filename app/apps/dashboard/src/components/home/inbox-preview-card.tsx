"use client";

import { useQuery } from "@tanstack/react-query";
import { formatDistanceToNowStrict } from "date-fns";
import { InboxIcon } from "lucide-react";
import { useUser } from "@/components/user-provider";
import { trpc } from "@/utils/trpc";
import { InboxSourceIcon } from "../inbox/source-icon";
import { HomeCard, HomeCardEmpty, HomeCardRow } from "./home-card";

export const InboxPreviewCard = () => {
	const user = useUser();
	const { data, isLoading } = useQuery(
		trpc.inbox.get.queryOptions(
			{ pageSize: 20, status: ["pending"] },
			{ staleTime: 60 * 1000 },
		),
	);

	const basePath = user?.basePath ?? "/team";
	const items = data?.data ?? [];
	const unread = items.filter((i) => !i.seen);
	// Surface unread first; fall back to anything pending so the card isn't blank
	// when the user has caught up.
	const top5 = (unread.length > 0 ? unread : items).slice(0, 5);

	return (
		<HomeCard
			title="Inbox"
			count={unread.length}
			href={`${basePath}/inbox`}
			isLoading={isLoading}
			isEmpty={top5.length === 0}
			emptyState={
				<HomeCardEmpty
					title="You're all caught up"
					description="Notifications and intake items will land here."
					ctaHref={`${basePath}/inbox`}
					ctaLabel="Open inbox"
				/>
			}
		>
			<ul className="space-y-0.5">
				{top5.map((item) => (
					<li key={item.id}>
						<HomeCardRow
							href={`${basePath}/inbox?selectedInboxId=${item.id}`}
							leading={
								item.source ? (
									<InboxSourceIcon source={item.source} className="size-3.5" />
								) : (
									<InboxIcon className="size-3.5" />
								)
							}
							title={
								<span className={item.seen ? "text-muted-foreground" : ""}>
									{item.display}
								</span>
							}
							trailing={
								<span>
									{formatDistanceToNowStrict(new Date(item.createdAt), {
										addSuffix: false,
									})}
								</span>
							}
						/>
					</li>
				))}
			</ul>
		</HomeCard>
	);
};

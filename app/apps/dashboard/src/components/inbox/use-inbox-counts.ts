"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { trpc } from "@/utils/trpc";

/**
 * Standalone unread/all counts — safe outside InboxProvider
 * (Home health strip, Focus tabs, Rituals, Workspace Health).
 */
export function useInboxCounts() {
	const { data, isLoading } = useInfiniteQuery(
		trpc.inbox.get.infiniteQueryOptions(
			{ status: ["pending"] },
			{ getNextPageParam: (lastPage) => lastPage.meta.cursor },
		),
	);

	const allInboxes = useMemo(
		() => data?.pages.flatMap((page) => page.data) || [],
		[data],
	);

	const tabCounts = useMemo(() => {
		let all = 0;
		let unread = 0;
		for (const item of allInboxes) {
			all += 1;
			if (!item.seen) unread += 1;
		}
		return { all, unread, mentions: 0, subscribed: 0 };
	}, [allInboxes]);

	return { tabCounts, isLoading, allInboxes };
}

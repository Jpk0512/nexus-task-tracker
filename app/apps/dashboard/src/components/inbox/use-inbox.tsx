"use client";
import type { RouterOutputs } from "@mimir/trpc";
import { useInfiniteQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo } from "react";
import { useUser } from "@/components/user-provider";
import { queryClient, trpc } from "@/utils/trpc";
import { type InboxTab, useInboxFilterParams } from "./use-inbox-filter-params";

export type Inbox = RouterOutputs["inbox"]["get"]["data"][number];

export interface InboxTabCounts {
	all: number;
	unread: number;
	mentions: number;
	subscribed: number;
}

export interface InboxContextValue {
	inboxId?: string;
	inboxes: Inbox[];
	allInboxes: Inbox[];
	selectedInbox?: Inbox;
	tabCounts: InboxTabCounts;
	isLoading: boolean;
}

export const InboxContext = createContext<InboxContextValue | undefined>(
	undefined,
);

const isMention = (item: Inbox) => {
	if (item.source === "mention") return true;
	// Heuristic: gmail / slack / github items that contain a literal mention
	// in their content or subtitle (covers `@username` patterns).
	const haystack = `${item.display ?? ""} ${item.subtitle ?? ""} ${
		item.content ?? ""
	}`;
	return /(^|\s)@[a-z0-9_-]{2,}/i.test(haystack);
};

const isSubscribedFor = (item: Inbox, userId: string | undefined) => {
	if (!userId) return false;
	// We treat the user as "subscribed" to an inbox row when one of its
	// intakes targets them as assignee (the closest concept available in
	// the current schema — no dedicated `subscribers` table exists yet).
	const intakes = item.intakes as
		| Array<{ payload?: { assigneeId?: string | null } | null } | null>
		| null
		| undefined;
	if (!intakes) return false;
	return intakes.some(
		(intake) =>
			!!intake?.payload?.assigneeId && intake.payload.assigneeId === userId,
	);
};

const matchesTab = (item: Inbox, tab: InboxTab, userId: string | undefined) => {
	switch (tab) {
		case "unread":
			return !item.seen;
		case "mentions":
			return isMention(item);
		case "subscribed":
			return isSubscribedFor(item, userId);
		default:
			return true;
	}
};

export const InboxProvider = ({ children }: { children: React.ReactNode }) => {
	const { params } = useInboxFilterParams();
	const user = useUser();
	const { data, isLoading } = useInfiniteQuery(
		trpc.inbox.get.infiniteQueryOptions(
			{
				status: params.status ?? ["pending"],
			},
			{
				getNextPageParam: (lastPage) => lastPage.meta.cursor,
			},
		),
	);

	const allInboxes = useMemo(() => {
		return data?.pages.flatMap((page) => page.data) || [];
	}, [data]);

	const tabCounts = useMemo<InboxTabCounts>(() => {
		const counts: InboxTabCounts = {
			all: 0,
			unread: 0,
			mentions: 0,
			subscribed: 0,
		};
		for (const item of allInboxes) {
			counts.all += 1;
			// Tab badges count *unread* items inside the tab (Linear pattern).
			if (item.seen) continue;
			counts.unread += 1;
			if (isMention(item)) counts.mentions += 1;
			if (isSubscribedFor(item, user?.id)) counts.subscribed += 1;
		}
		return counts;
	}, [allInboxes, user?.id]);

	const inboxes = useMemo(() => {
		return allInboxes.filter((item) =>
			matchesTab(item, params.tab as InboxTab, user?.id),
		);
	}, [allInboxes, params.tab, user?.id]);

	const selectedInbox = useMemo(() => {
		return allInboxes.find((inbox) => inbox.id === params.selectedInboxId);
	}, [allInboxes, params.selectedInboxId]);

	useEffect(() => {
		// prefetch inbox overview data
		for (const item of allInboxes) {
			queryClient.setQueryData(
				trpc.inbox.getById.queryKey({ id: item.id }),
				// @ts-expect-error
				item,
			);
		}
	}, [allInboxes]);

	return (
		<InboxContext.Provider
			value={{
				inboxId: params.selectedInboxId ?? undefined,
				inboxes,
				allInboxes,
				selectedInbox,
				tabCounts,
				isLoading,
			}}
		>
			{children}
		</InboxContext.Provider>
	);
};

export const useInbox = () => {
	const context = useContext(InboxContext);
	if (!context) {
		throw new Error("useInbox must be used within an InboxProvider");
	}
	return context;
};

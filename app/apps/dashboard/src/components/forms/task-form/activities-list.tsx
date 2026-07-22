"use client";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Button } from "@ui/components/ui/button";
import { AnimatePresence } from "motion/react";
import { useMemo, useRef } from "react";
import { ActivityItem } from "@/components/activities/activity-item";
import { trpc } from "@/utils/trpc";

// Beyond this many loaded activities, the section switches to a fixed-height
// scroll region and virtualizes it (FEAT-008 item 5) — a chatty task's
// Activity tab no longer makes the whole detail panel scroll forever. Most
// tasks stay well under this, so the common case is unchanged.
const VIRTUALIZE_THRESHOLD = 20;
const ESTIMATED_ACTIVITY_HEIGHT = 64;

export const TaskActivitiesList = ({ taskId }: { taskId: string }) => {
	const { data, fetchNextPage, hasNextPage } = useInfiniteQuery(
		trpc.activities.get.infiniteQueryOptions(
			{
				groupId: taskId,
				nStatus: ["archived"],
				pageSize: 10,
			},
			{
				getNextPageParam: (lastPage) => lastPage.meta.cursor,
			},
		),
	);

	const reversedData = useMemo(() => {
		if (!data) return [];
		return [...data.pages.flatMap((page) => page.data)].reverse();
	}, [data]);

	const scrollContainerRef = useRef<HTMLDivElement>(null);
	const shouldVirtualize = reversedData.length > VIRTUALIZE_THRESHOLD;
	const virtualizer = useVirtualizer({
		count: reversedData.length,
		getScrollElement: () => scrollContainerRef.current,
		estimateSize: () => ESTIMATED_ACTIVITY_HEIGHT,
		overscan: 8,
	});

	const loadMoreButton = hasNextPage ? (
		<Button
			variant={"ghost"}
			size={"sm"}
			className="text-muted-foreground text-xs"
			onClick={() => fetchNextPage()}
			type="button"
		>
			Load more activities
		</Button>
	) : null;

	if (shouldVirtualize) {
		return (
			<div className="space-y-2">
				{loadMoreButton}
				<div
					ref={scrollContainerRef}
					className="max-h-[420px] overflow-y-auto pr-1"
				>
					<div
						style={{
							height: virtualizer.getTotalSize(),
							width: "100%",
							position: "relative",
						}}
					>
						{virtualizer.getVirtualItems().map((virtualRow) => {
							const activity = reversedData[virtualRow.index];
							if (!activity) return null;
							return (
								<div
									key={activity.id}
									data-index={virtualRow.index}
									ref={virtualizer.measureElement}
									style={{
										position: "absolute",
										top: 0,
										left: 0,
										width: "100%",
										transform: `translateY(${virtualRow.start}px)`,
										paddingBottom: "0.5rem",
									}}
								>
									<ActivityItem activity={activity} taskId={taskId} />
								</div>
							);
						})}
					</div>
				</div>
			</div>
		);
	}

	return (
		<ul className="space-y-2">
			{loadMoreButton && <li>{loadMoreButton}</li>}
			<AnimatePresence>
				{reversedData.map((activity) => {
					return (
						<li key={activity.id}>
							<ActivityItem
								key={activity.id}
								activity={activity}
								taskId={taskId}
							/>
						</li>
					);
				})}
			</AnimatePresence>
		</ul>
	);
};

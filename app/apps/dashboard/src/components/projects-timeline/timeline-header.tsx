"use client";

import { format } from "date-fns";
import {
	getVisibleContainerWidth,
	type TimelineRange,
	type VisibleRange,
} from "./types";
import {
	generateVisibleMonthMarkers,
	generateVisibleWeekMarkers,
	getPositionPixels,
	toViewportPosition,
} from "./utils";

interface TimelineHeaderProps {
	range: TimelineRange;
	visibleRange: VisibleRange;
}

export function TimelineHeader({ range, visibleRange }: TimelineHeaderProps) {
	const monthMarkers = generateVisibleMonthMarkers(range, visibleRange);
	const weekMarkers = generateVisibleWeekMarkers(range, visibleRange);
	const today = new Date();
	const todayAbsolutePosition = getPositionPixels(today, range);
	const todayViewportPosition = toViewportPosition(
		todayAbsolutePosition,
		visibleRange,
	);
	const containerWidth = getVisibleContainerWidth(visibleRange);
	const isTodayVisible =
		todayAbsolutePosition >= visibleRange.startPx &&
		todayAbsolutePosition <= visibleRange.endPx;

	return (
		<div className="relative h-12" style={{ width: containerWidth }}>
			{/* Month labels */}
			<div className="relative h-12">
				{monthMarkers.map((marker) => (
					<div
						key={`month-${marker.date.getTime()}`}
						className="absolute top-0"
						style={{ left: marker.position }}
					>
						<div className="font-medium text-muted-foreground text-xs">
							{marker.label}
						</div>
						{/* Full-height dashed line */}
						<div className="absolute top-0 h-screen w-px border-border border-l border-dashed" />
					</div>
				))}
				{/* Week markers */}
				{weekMarkers.map((marker) => (
					<div
						key={`week-${marker.date.getTime()}`}
						className="absolute top-0 h-2 w-px bg-border"
						style={{ left: marker.position }}
					/>
				))}
			</div>

			{/* Today marker */}
			{isTodayVisible && (
				<div
					className="absolute top-5 z-10"
					style={{ left: todayViewportPosition }}
				>
					<div className="-top-0 -translate-x-1/2 absolute left-1/2 w-max rounded bg-primary px-1.5 py-0.5 text-[10px] text-primary-foreground">
						{format(today, "MMM d")}
					</div>
				</div>
			)}
		</div>
	);
}

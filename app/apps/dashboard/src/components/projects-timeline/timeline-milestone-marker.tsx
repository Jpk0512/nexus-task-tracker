"use client";

import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@ui/components/ui/tooltip";
import { format } from "date-fns";
import { MilestoneIcon } from "@/components/milestone-icon";
import type { TimelineMilestone, TimelineRange, VisibleRange } from "./types";
import { getMilestonePositionForViewport } from "./utils";

interface TimelineMilestoneMarkerProps {
	milestone: TimelineMilestone;
	range: TimelineRange;
	visibleRange: VisibleRange;
}

export function TimelineMilestoneMarker({
	milestone,
	range,
	visibleRange,
}: TimelineMilestoneMarkerProps) {
	const position = getMilestonePositionForViewport(
		milestone.dueDate,
		range,
		visibleRange,
	);

	if (position === null) return null;

	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<div
					className="-translate-x-1/2 -translate-y-1/2 absolute top-1/2 z-10 cursor-pointer transition-transform hover:scale-125"
					style={{ left: position }}
				>
					<MilestoneIcon
						color={milestone.color}
						className="size-4 drop-shadow-sm"
					/>
				</div>
			</TooltipTrigger>
			<TooltipContent>
				<div className="text-xs">
					<p className="font-medium">{milestone.name}</p>
					{milestone.dueDate && (
						<p className="text-[10px] opacity-80">
							{format(new Date(milestone.dueDate), "MMM d, yyyy")}
						</p>
					)}
				</div>
			</TooltipContent>
		</Tooltip>
	);
}

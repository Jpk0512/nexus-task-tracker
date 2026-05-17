"use client";

import { DraggableTimelineBar } from "./draggable-timeline-bar";
import { TimelineMilestoneMarker } from "./timeline-milestone-marker";
import {
	getVisibleContainerWidth,
	type TimelineProject,
	type TimelineRange,
	type VisibleRange,
} from "./types";
import { getPartialBarStyle, isBarVisible, isMilestoneVisible } from "./utils";

interface TimelineProjectRowProps {
	project: TimelineProject;
	range: TimelineRange;
	visibleRange: VisibleRange;
	onDateChange?: (
		projectId: string,
		startDate: Date | null,
		endDate: Date | null,
	) => void;
	onProjectClick?: (projectId: string) => void;
}

export function TimelineProjectRow({
	project,
	range,
	visibleRange,
	onDateChange,
	onProjectClick,
}: TimelineProjectRowProps) {
	const barStyle = getPartialBarStyle(
		project.startDate,
		project.endDate,
		range,
		visibleRange,
	);
	const hasStartDate = !!project.startDate;
	const hasEndDate = !!project.endDate;
	const hasAnyDate = hasStartDate || hasEndDate;
	const containerWidth = getVisibleContainerWidth(visibleRange);

	// Check if the bar is visible
	const barVisible = isBarVisible(
		project.startDate,
		project.endDate,
		range,
		visibleRange,
	);

	// Filter milestones to only show visible ones
	const visibleMilestones = project.milestones.filter((milestone) =>
		isMilestoneVisible(milestone.dueDate, range, visibleRange),
	);

	const handleDateChange = (startDate: Date | null, endDate: Date | null) => {
		onDateChange?.(project.id, startDate, endDate);
	};

	return (
		<div className="group relative flex items-center gap-4 py-3">
			{/* Timeline bar container */}
			<div className="relative h-8" style={{ width: containerWidth }}>
				{hasAnyDate && barStyle && barVisible ? (
					<div>
						<DraggableTimelineBar
							barStyle={barStyle}
							color={project.color || "hsl(var(--primary))"}
							projectName={project.name}
							projectStartDate={project.startDate}
							projectEndDate={project.endDate}
							range={range}
							visibleRange={visibleRange}
							onDateChange={handleDateChange}
							onClick={() => onProjectClick?.(project.id)}
						/>
					</div>
				) : !hasAnyDate ? (
					<div className="-translate-y-1/2 absolute inset-x-0 top-1/2 h-0.5 bg-border opacity-50" />
				) : null}

				{/* Milestone markers - only render visible ones */}
				{visibleMilestones.map((milestone) => (
					<TimelineMilestoneMarker
						key={milestone.id}
						milestone={milestone}
						range={range}
						visibleRange={visibleRange}
					/>
				))}
			</div>
		</div>
	);
}

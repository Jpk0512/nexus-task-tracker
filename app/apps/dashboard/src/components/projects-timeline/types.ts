export interface TimelineMilestone {
	id: string;
	name: string;
	dueDate: string | null;
	color: string | null;
	projectId: string;
}

export interface TimelineProject {
	id: string;
	name: string;
	color: string | null;
	startDate: string | null;
	endDate: string | null;
	milestones: TimelineMilestone[];
}

export interface TimelineRange {
	start: Date;
	end: Date;
	totalDays: number;
}

/** Visible window range for virtualized rendering */
export interface VisibleRange {
	startPx: number;
	endPx: number;
	startDate: Date;
	endDate: Date;
}

/** Fixed width in pixels per day */
export const PIXELS_PER_DAY = 4;

/** Buffer in pixels to render outside visible area */
export const BUFFER_PX = 200;

/** Calculate the total width of the timeline in pixels */
export function getTimelineWidth(range: TimelineRange): number {
	return range.totalDays * PIXELS_PER_DAY;
}

/** Calculate the visible container width (viewport + buffer on both sides) */
export function getVisibleContainerWidth(visibleRange: VisibleRange): number {
	return visibleRange.endPx - visibleRange.startPx;
}

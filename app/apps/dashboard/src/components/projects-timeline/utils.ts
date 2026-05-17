import {
	addDays,
	addMonths,
	differenceInDays,
	endOfMonth,
	format,
	isWithinInterval,
	startOfMonth,
} from "date-fns";
import {
	PIXELS_PER_DAY,
	type TimelineProject,
	type TimelineRange,
	type VisibleRange,
} from "./types";

export function calculateTimelineRange(
	projects: TimelineProject[],
): TimelineRange {
	const now = new Date();
	let minDate = startOfMonth(now);
	let maxDate = endOfMonth(addMonths(now, 2));

	for (const project of projects) {
		if (project.startDate) {
			const startDate = new Date(project.startDate);
			if (startDate < minDate) {
				minDate = startOfMonth(startDate);
			}
		}
		if (project.endDate) {
			const endDate = new Date(project.endDate);
			if (endDate > maxDate) {
				maxDate = endOfMonth(endDate);
			}
		}
		for (const milestone of project.milestones) {
			if (milestone.dueDate) {
				const dueDate = new Date(milestone.dueDate);
				if (dueDate < minDate) {
					minDate = startOfMonth(dueDate);
				}
				if (dueDate > maxDate) {
					maxDate = endOfMonth(dueDate);
				}
			}
		}
	}

	// Add some padding
	minDate = addMonths(minDate, -6);
	maxDate = addMonths(maxDate, 6);

	return {
		start: minDate,
		end: maxDate,
		totalDays: differenceInDays(maxDate, minDate),
	};
}

/** Get the pixel position for a date within the timeline */
export function getPositionPixels(date: Date, range: TimelineRange): number {
	const dayOffset = differenceInDays(date, range.start);
	return dayOffset * PIXELS_PER_DAY;
}

/** Get the date for a pixel position within the timeline */
export function getDateFromPixels(px: number, range: TimelineRange): Date {
	const days = Math.floor(px / PIXELS_PER_DAY);
	return addDays(range.start, days);
}

/** Calculate the visible range based on scroll position */
export function calculateVisibleRange(
	scrollLeft: number,
	containerWidth: number,
	range: TimelineRange,
	bufferPx: number,
): VisibleRange {
	// Allow negative startPx for scrolling before the timeline range start
	const startPx = scrollLeft - bufferPx;
	const endPx = scrollLeft + containerWidth + bufferPx;

	return {
		startPx,
		endPx,
		startDate: getDateFromPixels(startPx, range),
		endDate: getDateFromPixels(endPx, range),
	};
}

export function getBarStyle(
	startDate: string | null,
	endDate: string | null,
	range: TimelineRange,
): { left: number; width: number } | null {
	if (!startDate || !endDate) return null;

	const start = new Date(startDate);
	const end = new Date(endDate);

	const leftPx = getPositionPixels(start, range);
	const rightPx = getPositionPixels(end, range);
	const widthPx = rightPx - leftPx;

	if (widthPx <= 0) return null;

	return {
		left: Math.max(0, leftPx),
		width: widthPx,
	};
}

export type PartialBarType = "full" | "start-only" | "end-only";

export interface PartialBarStyle {
	left: number;
	width: number;
	type: PartialBarType;
}

/** Get bar style for partial date ranges (only startDate or only endDate) */
export function getPartialBarStyle(
	startDate: string | null,
	endDate: string | null,
	range: TimelineRange,
	visibleRange: VisibleRange,
): PartialBarStyle | null {
	const hasStart = !!startDate;
	const hasEnd = !!endDate;

	// Both dates - use full bar
	if (hasStart && hasEnd) {
		const barStyle = getBarStyleForViewport(
			startDate,
			endDate,
			range,
			visibleRange,
		);
		if (!barStyle) return null;
		return { ...barStyle, type: "full" };
	}

	// Only start date - gradient from left to right (fades out to the right)
	if (hasStart && !hasEnd) {
		const start = new Date(startDate);
		const leftPx = getPositionPixels(start, range);
		const viewportLeft = toViewportPosition(leftPx, visibleRange);
		// Extend bar to the right edge of the visible area
		const width = visibleRange.endPx - leftPx + 50; // Extra padding
		return {
			left: viewportLeft,
			width: Math.min(150, width), // Minimum width
			type: "start-only",
		};
	}

	// Only end date - gradient from right to left (fades out to the left)
	if (!hasStart && hasEnd) {
		const end = new Date(endDate);
		const rightPx = getPositionPixels(end, range);
		// Start from the left edge of visible area
		const leftPx = Math.max(0, visibleRange.startPx - 50);
		const viewportLeft = toViewportPosition(leftPx, visibleRange);
		const width = rightPx - leftPx;
		return {
			left: viewportLeft,
			width: Math.min(150, width), // Minimum width
			type: "end-only",
		};
	}

	return null;
}

/** Get bar style with viewport-relative positioning */
export function getBarStyleForViewport(
	startDate: string | null,
	endDate: string | null,
	range: TimelineRange,
	visibleRange: VisibleRange,
): { left: number; width: number } | null {
	const barStyle = getBarStyle(startDate, endDate, range);
	if (!barStyle) return null;

	return {
		left: toViewportPosition(barStyle.left, visibleRange),
		width: barStyle.width,
	};
}

/** Check if a bar is visible within the visible range */
export function isBarVisible(
	startDate: string | null,
	endDate: string | null,
	range: TimelineRange,
	visibleRange: VisibleRange,
): boolean {
	const hasStart = !!startDate;
	const hasEnd = !!endDate;

	// No dates at all - not visible as a bar
	if (!hasStart && !hasEnd) return false;

	// Only start date - visible if start is before visible end
	if (hasStart && !hasEnd) {
		const startPx = getPositionPixels(new Date(startDate), range);
		return startPx <= visibleRange.endPx;
	}

	// Only end date - visible if end is after visible start
	if (!hasStart && hasEnd) {
		const endPx = getPositionPixels(new Date(endDate), range);
		return endPx >= visibleRange.startPx;
	}

	// Both dates
	const barStyle = getBarStyle(startDate, endDate, range);
	if (!barStyle) return false;

	const barEnd = barStyle.left + barStyle.width;
	return barEnd >= visibleRange.startPx && barStyle.left <= visibleRange.endPx;
}

export function getMilestonePosition(
	dueDate: string | null,
	range: TimelineRange,
): number | null {
	if (!dueDate) return null;
	const date = new Date(dueDate);
	const position = getPositionPixels(date, range);
	const totalWidth = range.totalDays * PIXELS_PER_DAY;
	if (position < 0 || position > totalWidth) return null;
	return position;
}

/** Get milestone position relative to viewport */
export function getMilestonePositionForViewport(
	dueDate: string | null,
	range: TimelineRange,
	visibleRange: VisibleRange,
): number | null {
	const position = getMilestonePosition(dueDate, range);
	if (position === null) return null;
	return toViewportPosition(position, visibleRange);
}

/** Check if a milestone is visible within the visible range */
export function isMilestoneVisible(
	dueDate: string | null,
	range: TimelineRange,
	visibleRange: VisibleRange,
): boolean {
	const position = getMilestonePosition(dueDate, range);
	if (position === null) return false;
	return position >= visibleRange.startPx && position <= visibleRange.endPx;
}

export function isDateInRange(date: Date, range: TimelineRange): boolean {
	return isWithinInterval(date, { start: range.start, end: range.end });
}

/** Convert an absolute position to a position relative to the visible viewport */
export function toViewportPosition(
	absolutePosition: number,
	visibleRange: VisibleRange,
): number {
	return absolutePosition - visibleRange.startPx;
}

/** Generate month markers only for the visible range (returns viewport-relative positions) */
export function generateVisibleMonthMarkers(
	range: TimelineRange,
	visibleRange: VisibleRange,
): Array<{ date: Date; position: number; label: string }> {
	const markers: Array<{ date: Date; position: number; label: string }> = [];

	// Start from the first month in visible range
	let current = startOfMonth(visibleRange.startDate);

	while (current <= visibleRange.endDate) {
		const absolutePosition = getPositionPixels(current, range);
		if (
			absolutePosition >= visibleRange.startPx &&
			absolutePosition <= visibleRange.endPx
		) {
			markers.push({
				date: current,
				position: toViewportPosition(absolutePosition, visibleRange),
				label: format(current, "MMM yyyy"),
			});
		}
		current = addMonths(current, 1);
	}

	return markers;
}

/** Generate week markers only for the visible range (returns viewport-relative positions) */
export function generateVisibleWeekMarkers(
	range: TimelineRange,
	visibleRange: VisibleRange,
): Array<{ date: Date; position: number }> {
	const markers: Array<{ date: Date; position: number }> = [];

	// Start from the visible start date
	let current = new Date(visibleRange.startDate);

	// Find the next Monday
	const dayOfWeek = current.getDay();
	const daysUntilMonday =
		dayOfWeek === 0 ? 1 : dayOfWeek === 1 ? 0 : 8 - dayOfWeek;
	current = addDays(current, daysUntilMonday);

	while (current <= visibleRange.endDate) {
		const absolutePosition = getPositionPixels(current, range);
		if (
			absolutePosition >= visibleRange.startPx &&
			absolutePosition <= visibleRange.endPx
		) {
			markers.push({
				date: current,
				position: toViewportPosition(absolutePosition, visibleRange),
			});
		}
		current = addDays(current, 7);
	}

	return markers;
}

// Keep legacy functions for backward compatibility
export function generateMonthMarkers(
	range: TimelineRange,
): Array<{ date: Date; position: number; label: string }> {
	const markers: Array<{ date: Date; position: number; label: string }> = [];
	let current = startOfMonth(range.start);
	const totalWidth = range.totalDays * PIXELS_PER_DAY;

	while (current <= range.end) {
		const position = getPositionPixels(current, range);
		if (position >= 0 && position <= totalWidth) {
			markers.push({
				date: current,
				position,
				label: format(current, "MMM yyyy"),
			});
		}
		current = addMonths(current, 1);
	}

	return markers;
}

export function generateWeekMarkers(
	range: TimelineRange,
): Array<{ date: Date; position: number }> {
	const markers: Array<{ date: Date; position: number }> = [];
	let current = new Date(range.start);
	const totalWidth = range.totalDays * PIXELS_PER_DAY;

	// Find the next Monday
	const dayOfWeek = current.getDay();
	const daysUntilMonday = dayOfWeek === 0 ? 1 : 8 - dayOfWeek;
	current = addDays(current, daysUntilMonday);

	while (current <= range.end) {
		const position = getPositionPixels(current, range);
		if (position >= 0 && position <= totalWidth) {
			markers.push({
				date: current,
				position,
			});
		}
		current = addDays(current, 7);
	}

	return markers;
}

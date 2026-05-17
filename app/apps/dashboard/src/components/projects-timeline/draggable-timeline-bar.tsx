"use client";

import { format } from "date-fns";
import { useCallback, useEffect, useRef, useState } from "react";
import type { TimelineRange, VisibleRange } from "./types";
import type { PartialBarStyle } from "./utils";
import {
	getDateFromPixels,
	getPositionPixels,
	toViewportPosition,
} from "./utils";

type DragHandle = "start" | "end" | "move" | null;

// Pending position stored as DATES instead of pixels
// This ensures range changes don't affect position comparison
interface PendingDatePosition {
	startDate: Date;
	endDate: Date;
}

interface DraggableTimelineBarProps {
	barStyle: PartialBarStyle;
	color: string;
	projectName: string;
	projectStartDate: string | null;
	projectEndDate: string | null;
	range: TimelineRange;
	visibleRange: VisibleRange;
	onDateChange: (startDate: Date | null, endDate: Date | null) => void;
	onClick?: () => void;
}

export function DraggableTimelineBar({
	barStyle,
	color,
	projectName,
	projectStartDate,
	projectEndDate,
	range,
	visibleRange,
	onDateChange,
	onClick,
}: DraggableTimelineBarProps) {
	const [isDragging, setIsDragging] = useState<DragHandle>(null);
	const [dragOffset, setDragOffset] = useState(0);
	const [currentDragDate, setCurrentDragDate] = useState<Date | null>(null);
	const [currentEndDragDate, setCurrentEndDragDate] = useState<Date | null>(
		null,
	);
	// Pending position to prevent snap-back after drag ends (stored as dates)
	const [pendingPosition, setPendingPosition] =
		useState<PendingDatePosition | null>(null);

	// Clear pending position when props update to match our pending dates
	// This happens when the optimistic update or server response comes in
	useEffect(() => {
		if (pendingPosition && projectStartDate && projectEndDate) {
			// Compare dates - normalize to day precision since that's what we're dragging
			const propsStart = new Date(projectStartDate);
			const propsEnd = new Date(projectEndDate);
			const pendingStart = pendingPosition.startDate;
			const pendingEnd = pendingPosition.endDate;

			// Compare by date string (YYYY-MM-DD) to handle timezone differences
			const startMatches =
				propsStart.toDateString() === pendingStart.toDateString();
			const endMatches = propsEnd.toDateString() === pendingEnd.toDateString();

			if (startMatches && endMatches) {
				setPendingPosition(null);
			}
		}
	}, [projectStartDate, projectEndDate, pendingPosition]);

	const barRef = useRef<HTMLDivElement>(null);
	const startXRef = useRef(0);
	const initialLeftRef = useRef(0); // Viewport-relative left at drag start
	const initialWidthRef = useRef(0);
	const initialAbsoluteLeftRef = useRef(0); // Absolute left at drag start (for calculating pending position)
	const initialVisibleRangeStartPxRef = useRef(0); // visibleRange.startPx at drag start
	const initialRangeRef = useRef<TimelineRange | null>(null); // Timeline range at drag start (frozen during drag)
	const isDraggingRef = useRef<DragHandle>(null);
	const currentDragDateRef = useRef<Date | null>(null);
	const currentEndDragDateRef = useRef<Date | null>(null);
	const dragOffsetRef = useRef(0);
	// Refs to track current props for use in event handlers (avoid stale closures)
	const visibleRangeRef = useRef(visibleRange);
	const rangeRef = useRef(range);

	// Keep refs updated with latest props
	useEffect(() => {
		visibleRangeRef.current = visibleRange;
	}, [visibleRange]);

	useEffect(() => {
		rangeRef.current = range;
	}, [range]);

	// Generate background style based on bar type
	const getBackgroundStyle = () => {
		if (barStyle.type === "start-only") {
			return {
				background: `linear-gradient(to right, ${color} 0%, ${color} 30%, transparent 100%)`,
			};
		}

		if (barStyle.type === "end-only") {
			return {
				background: `linear-gradient(to left, ${color} 0%, ${color} 30%, transparent 100%)`,
			};
		}

		return {
			backgroundColor: color,
		};
	};

	const handleStartDrag = useCallback(
		(e: React.MouseEvent, handle: DragHandle) => {
			e.stopPropagation();
			e.preventDefault();

			// Clear any pending position since we're starting a new drag
			setPendingPosition(null);

			// IMPORTANT: Capture the current range at drag start and freeze it
			// This prevents issues when the timeline expands/contracts during drag
			const frozenRange = range;
			initialRangeRef.current = frozenRange;

			// Use pending position as starting point if it exists, otherwise use barStyle
			// Convert pending dates to viewport coordinates for drag calculations
			const pendingPixels = pendingPosition
				? {
						left: toViewportPosition(
							getPositionPixels(pendingPosition.startDate, frozenRange),
							visibleRange,
						),
						width:
							getPositionPixels(pendingPosition.endDate, frozenRange) -
							getPositionPixels(pendingPosition.startDate, frozenRange),
					}
				: null;
			const startLeft = pendingPixels?.left ?? barStyle.left;
			const startWidth = pendingPixels?.width ?? barStyle.width;
			// Calculate absolute position at drag start (used for pending position calculation)
			const startAbsoluteLeft = pendingPixels
				? getPositionPixels(pendingPosition!.startDate, frozenRange)
				: barStyle.left + visibleRange.startPx;

			// Set refs for use in event handlers
			isDraggingRef.current = handle;
			startXRef.current = e.clientX;
			initialLeftRef.current = startLeft;
			initialWidthRef.current = startWidth;
			initialAbsoluteLeftRef.current = startAbsoluteLeft;
			initialVisibleRangeStartPxRef.current = visibleRange.startPx;

			// Helper to convert absolute pixels to date using the FROZEN range
			const absolutePxToDate = (absolutePx: number): Date => {
				return getDateFromPixels(absolutePx, frozenRange);
			};

			// Set state for rendering
			setIsDragging(handle);
			setDragOffset(0);

			// Calculate initial date based on handle (use absolute positions)
			if (handle === "start") {
				const initialDate = absolutePxToDate(startAbsoluteLeft);
				currentDragDateRef.current = initialDate;
				setCurrentDragDate(initialDate);
			} else if (handle === "end") {
				const initialDate = absolutePxToDate(startAbsoluteLeft + startWidth);
				currentDragDateRef.current = initialDate;
				setCurrentDragDate(initialDate);
			} else if (handle === "move") {
				// For move, track both start and end dates
				const startDate = absolutePxToDate(startAbsoluteLeft);
				const endDate = absolutePxToDate(startAbsoluteLeft + startWidth);
				currentDragDateRef.current = startDate;
				currentEndDragDateRef.current = endDate;
				setCurrentDragDate(startDate);
				setCurrentEndDragDate(endDate);
			}

			const onMove = (moveEvent: MouseEvent) => {
				if (!isDraggingRef.current) return;

				const deltaX = moveEvent.clientX - startXRef.current;
				dragOffsetRef.current = deltaX;
				setDragOffset(deltaX);

				// Calculate dates using absolute positions and the FROZEN range
				// absoluteLeft at drag start + mouse delta = new absolute position
				const newAbsoluteLeft = initialAbsoluteLeftRef.current + deltaX;

				if (isDraggingRef.current === "start") {
					const newDate = absolutePxToDate(newAbsoluteLeft);
					currentDragDateRef.current = newDate;
					setCurrentDragDate(newDate);
				} else if (isDraggingRef.current === "end") {
					const newAbsoluteRight = newAbsoluteLeft + initialWidthRef.current;
					const newDate = absolutePxToDate(newAbsoluteRight);
					currentDragDateRef.current = newDate;
					setCurrentDragDate(newDate);
				} else if (isDraggingRef.current === "move") {
					// Move both dates by the same offset
					const newAbsoluteRight = newAbsoluteLeft + initialWidthRef.current;
					const newStartDate = absolutePxToDate(newAbsoluteLeft);
					const newEndDate = absolutePxToDate(newAbsoluteRight);
					currentDragDateRef.current = newStartDate;
					currentEndDragDateRef.current = newEndDate;
					setCurrentDragDate(newStartDate);
					setCurrentEndDragDate(newEndDate);
				}
			};

			const onUp = () => {
				const dragHandle = isDraggingRef.current;
				const dragDate = currentDragDateRef.current;
				const endDragDate = currentEndDragDateRef.current;
				// Use the FROZEN range from drag start for position calculations
				// This ensures pending position matches what was displayed during drag
				const dragRange = initialRangeRef.current || frozenRange;

				if (dragHandle && dragDate) {
					// Calculate pending dates from the dragged positions
					let pendingStartDate: Date;
					let pendingEndDate: Date;

					if (dragHandle === "start") {
						// dragDate is the new start date, end date stays the same
						pendingStartDate = dragDate;
						// Calculate original end date from position
						const originalEndPx =
							initialAbsoluteLeftRef.current + initialWidthRef.current;
						pendingEndDate = getDateFromPixels(originalEndPx, dragRange);
						onDateChange(dragDate, null);
					} else if (dragHandle === "end") {
						// Start date stays the same, dragDate is the new end date
						pendingStartDate = getDateFromPixels(
							initialAbsoluteLeftRef.current,
							dragRange,
						);
						pendingEndDate = dragDate;
						onDateChange(null, dragDate);
					} else if (dragHandle === "move" && endDragDate) {
						// Both dates moved together
						pendingStartDate = dragDate;
						pendingEndDate = endDragDate;
						onDateChange(dragDate, endDragDate);
					} else {
						// Fallback - shouldn't happen, use original dates from position
						pendingStartDate = getDateFromPixels(
							initialAbsoluteLeftRef.current,
							dragRange,
						);
						pendingEndDate = getDateFromPixels(
							initialAbsoluteLeftRef.current + initialWidthRef.current,
							dragRange,
						);
					}

					setPendingPosition({
						startDate: pendingStartDate,
						endDate: pendingEndDate,
					});
				}

				// Reset state
				isDraggingRef.current = null;
				currentDragDateRef.current = null;
				currentEndDragDateRef.current = null;
				initialRangeRef.current = null;
				dragOffsetRef.current = 0;
				setIsDragging(null);
				setDragOffset(0);
				setCurrentDragDate(null);
				setCurrentEndDragDate(null);

				document.removeEventListener("mousemove", onMove);
				document.removeEventListener("mouseup", onUp);
			};

			document.addEventListener("mousemove", onMove);
			document.addEventListener("mouseup", onUp);
		},
		[
			barStyle.left,
			barStyle.width,
			pendingPosition,
			range,
			visibleRange,
			onDateChange,
		],
	);

	// Calculate adjusted positions during drag, or use pending position if waiting for props update
	// Convert pending dates to viewport coordinates for rendering
	const pendingPixels = pendingPosition
		? {
				left: toViewportPosition(
					getPositionPixels(pendingPosition.startDate, range),
					visibleRange,
				),
				width:
					getPositionPixels(pendingPosition.endDate, range) -
					getPositionPixels(pendingPosition.startDate, range),
			}
		: null;
	const baseLeft = pendingPixels?.left ?? barStyle.left;
	const baseWidth = pendingPixels?.width ?? barStyle.width;

	// During drag, use the initial position captured at drag start + the current offset
	// This ensures smooth dragging even if barStyle props change during the drag
	const adjustedLeft =
		isDragging === "start" || isDragging === "move"
			? initialLeftRef.current + dragOffset
			: baseLeft;

	const adjustedWidth =
		isDragging === "start"
			? initialWidthRef.current - dragOffset
			: isDragging === "end"
				? initialWidthRef.current + dragOffset
				: baseWidth;

	// Only show handles for full bars or the appropriate side for partial bars
	const showStartHandle =
		barStyle.type === "full" || barStyle.type === "start-only";
	const showEndHandle =
		barStyle.type === "full" || barStyle.type === "end-only";
	// Can only move (drag entire bar) if both dates exist
	const canMove = barStyle.type === "full";

	return (
		<>
			{/* Project name label - fixed above the bar */}
			<div
				className="absolute z-10 max-w-[150px] truncate font-medium text-foreground/70 text-xs"
				style={{
					left: adjustedLeft,
					top: -16,
				}}
			>
				{projectName}
			</div>

			{/* Main bar */}
			<div
				ref={barRef}
				className="-translate-y-1/2 absolute top-1/2 h-6 rounded-sm transition-[height] hover:h-7"
				style={{
					left: adjustedLeft,
					width: Math.max(20, adjustedWidth),
					opacity: 0.5,
					cursor: canMove ? "grab" : "pointer",
					...getBackgroundStyle(),
				}}
				onMouseDown={(e) => {
					if (canMove) {
						handleStartDrag(e, "move");
					}
				}}
				onClick={(e) => {
					// Prevent click event during drag
					if (isDragging) {
						e.stopPropagation();
						e.preventDefault();
						return;
					}
				}}
			>
				{/* Start handle */}
				{showStartHandle && (
					<div
						className="-left-1 absolute top-0 h-full w-3 cursor-ew-resize opacity-0 transition-opacity hover:opacity-100"
						onMouseDown={(e) => handleStartDrag(e, "start")}
					>
						<div className="h-full w-1 rounded-full bg-foreground/50" />
					</div>
				)}

				{/* End handle */}
				{showEndHandle && (
					<div
						className="-right-1 absolute top-0 flex h-full w-3 cursor-ew-resize justify-end opacity-0 transition-opacity hover:opacity-100"
						onMouseDown={(e) => handleStartDrag(e, "end")}
					>
						<div className="h-full w-1 rounded-full bg-foreground/50" />
					</div>
				)}
			</div>

			{/* Drag date indicator */}
			{isDragging && currentDragDate && (
				<div
					className="-translate-x-1/2 absolute z-50"
					style={{
						left:
							isDragging === "move"
								? adjustedLeft + adjustedWidth / 2
								: isDragging === "start"
									? adjustedLeft
									: adjustedLeft + adjustedWidth,
						top: -40,
					}}
				>
					<div className="whitespace-nowrap rounded bg-primary px-2 py-1 text-primary-foreground text-xs shadow-lg">
						{isDragging === "move" && currentEndDragDate
							? `${format(currentDragDate, "MMM d")} - ${format(currentEndDragDate, "MMM d, yyyy")}`
							: format(currentDragDate, "MMM d, yyyy")}
					</div>
					{/* Arrow pointing down */}
					<div
						className="absolute top-full left-1/2"
						style={{ transform: "translateX(-50%)" }}
					>
						<div className="border-4 border-transparent border-t-primary" />
					</div>
				</div>
			)}
		</>
	);
}

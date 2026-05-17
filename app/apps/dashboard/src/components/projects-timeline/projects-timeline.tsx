"use client";

import { Button } from "@ui/components/ui/button";
import { FocusIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ProjectIcon } from "@/components/project-icon";
import { TimelineHeader } from "./timeline-header";
import { TimelineProjectRow } from "./timeline-project-row";
import {
	BUFFER_PX,
	getTimelineWidth,
	getVisibleContainerWidth,
	type TimelineProject,
	type VisibleRange,
} from "./types";
import {
	calculateTimelineRange,
	calculateVisibleRange,
	getPositionPixels,
} from "./utils";

interface ProjectsTimelineProps {
	projects: TimelineProject[];
	allProjects?: TimelineProject[];
	onProjectDateChange?: (
		projectId: string,
		startDate: Date | null,
		endDate: Date | null,
	) => void;
	onProjectClick?: (projectId: string) => void;
}

export function ProjectsTimeline({
	projects,
	allProjects,
	onProjectDateChange,
	onProjectClick,
}: ProjectsTimelineProps) {
	// Use allProjects for sidebar if provided, otherwise fall back to projects
	const sidebarProjects = allProjects ?? projects;
	const range = useMemo(() => calculateTimelineRange(projects), [projects]);
	const totalWidth = getTimelineWidth(range);
	const containerRef = useRef<HTMLDivElement>(null);
	const [isDragging, setIsDragging] = useState(false);
	const [startX, setStartX] = useState(0);
	const [dragStartScrollPosition, setDragStartScrollPosition] = useState(0);
	const [containerWidth, setContainerWidth] = useState(800);
	// Virtual scroll position (not tied to actual DOM scroll)
	const [virtualScrollLeft, setVirtualScrollLeft] = useState(0);

	// Calculate visible range based on virtual scroll position
	const visibleRange: VisibleRange = useMemo(
		() =>
			calculateVisibleRange(
				virtualScrollLeft,
				containerWidth,
				range,
				BUFFER_PX,
			),
		[virtualScrollLeft, containerWidth, range],
	);

	const visibleContainerWidth = getVisibleContainerWidth(visibleRange);

	// Extended scroll range - allow scrolling 5 years before and after the calculated range
	// This ensures users can always scroll to reach project bars even if they're dragged far out
	const EXTENDED_SCROLL_YEARS = 5;
	const extendedScrollPx = EXTENDED_SCROLL_YEARS * 365 * 5; // 5 px per day

	// Clamp virtual scroll to extended range (allows scrolling beyond project dates)
	const clampScroll = useCallback(
		(scroll: number) => {
			// Allow scrolling from -extendedScrollPx to totalWidth + extendedScrollPx
			const minScroll = -extendedScrollPx;
			const maxScroll =
				Math.max(0, totalWidth - containerWidth) + extendedScrollPx;
			return Math.max(minScroll, Math.min(scroll, maxScroll));
		},
		[totalWidth, containerWidth, extendedScrollPx],
	);

	// Track if we've initialized the scroll position
	const hasInitializedScroll = useRef(false);

	// Initialize scroll position to today's date ONLY on first render
	useEffect(() => {
		if (containerRef.current && !hasInitializedScroll.current) {
			hasInitializedScroll.current = true;
			const today = new Date();
			const todayPosition = getPositionPixels(today, range);
			const centeredPosition = clampScroll(
				todayPosition - containerRef.current.clientWidth / 2,
			);
			setVirtualScrollLeft(centeredPosition);
			setContainerWidth(containerRef.current.clientWidth);
		}
	}, [range, clampScroll]);

	// Track container resize
	useEffect(() => {
		if (!containerRef.current) return;

		const resizeObserver = new ResizeObserver((entries) => {
			for (const entry of entries) {
				setContainerWidth(entry.contentRect.width);
			}
		});

		resizeObserver.observe(containerRef.current);
		return () => resizeObserver.disconnect();
	}, []);

	// Handle wheel events for horizontal scrolling
	const handleWheel = useCallback(
		(e: React.WheelEvent) => {
			// Use deltaX for horizontal scroll, or deltaY if shift is held or if deltaX is 0
			const delta = e.deltaX !== 0 ? e.deltaX : e.deltaY;
			setVirtualScrollLeft((prev) => clampScroll(prev + delta));
		},
		[clampScroll],
	);

	const handleMouseDown = useCallback(
		(e: React.MouseEvent<HTMLDivElement>) => {
			if (!containerRef.current) return;
			setIsDragging(true);
			setStartX(e.pageX);
			setDragStartScrollPosition(virtualScrollLeft);
			containerRef.current.style.cursor = "grabbing";
		},
		[virtualScrollLeft],
	);

	const handleMouseUp = useCallback(() => {
		setIsDragging(false);
		if (containerRef.current) {
			containerRef.current.style.cursor = "grab";
		}
	}, []);

	const handleMouseMove = useCallback(
		(e: React.MouseEvent<HTMLDivElement>) => {
			if (!isDragging) return;
			e.preventDefault();
			const deltaX = startX - e.pageX;
			const walk = deltaX * 1.5; // Adjust scroll speed
			setVirtualScrollLeft(clampScroll(dragStartScrollPosition + walk));
		},
		[isDragging, startX, dragStartScrollPosition, clampScroll],
	);

	const handleMouseLeave = useCallback(() => {
		if (isDragging) {
			setIsDragging(false);
			if (containerRef.current) {
				containerRef.current.style.cursor = "grab";
			}
		}
	}, [isDragging]);

	// Focus on a specific project by scrolling to its position
	const focusProject = useCallback(
		(project: TimelineProject) => {
			const targetDate = project.startDate
				? new Date(project.startDate)
				: project.endDate
					? new Date(project.endDate)
					: project.milestones.find((m) => m.dueDate)
						? new Date(project.milestones.find((m) => m.dueDate)!.dueDate!)
						: null;

			if (targetDate) {
				const targetPosition = getPositionPixels(targetDate, range);
				const centeredPosition = clampScroll(
					targetPosition - containerWidth / 2,
				);
				setVirtualScrollLeft(centeredPosition);
			}
		},
		[range, containerWidth, clampScroll],
	);

	if (projects.length === 0) {
		return null;
	}

	return (
		<div className="relative h-full w-full">
			{/* Project sidebar */}
			<div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-64">
				{/* Gradient fade */}
				<div className="absolute inset-0 bg-gradient-to-r from-background via-background/95 to-transparent" />
				{/* Project list */}
				<div className="pointer-events-auto relative h-full overflow-y-auto p-2 pt-13">
					<div className="space-y-2">
						{sidebarProjects.map((project) => {
							const hasTimelineData =
								project.startDate ||
								project.endDate ||
								project.milestones.some((m) => m.dueDate);
							return (
								<div
									key={project.id}
									className="group flex items-center gap-1 rounded-sm py-2 pr-1 hover:bg-card/60"
								>
									<button
										type="button"
										className="flex h-8 flex-1 items-center gap-2 truncate px-3 py-2 text-left text-sm"
										onClick={() => onProjectClick?.(project.id)}
									>
										<ProjectIcon
											color={project.color}
											className="size-4 shrink-0"
										/>
										<span className="truncate">{project.name}</span>
									</button>
									{hasTimelineData && (
										<Button
											variant="ghost"
											size="icon"
											className="size-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
											onClick={() => focusProject(project)}
										>
											<FocusIcon className="size-3.5" />
										</Button>
									)}
								</div>
							);
						})}
					</div>
				</div>
			</div>

			{/* Timeline content */}
			<div
				ref={containerRef}
				className="h-full w-full cursor-grab select-none overflow-hidden"
				onMouseDown={handleMouseDown}
				onMouseUp={handleMouseUp}
				onMouseMove={handleMouseMove}
				onMouseLeave={handleMouseLeave}
				onWheel={handleWheel}
			>
				<div className="px-4" style={{ width: visibleContainerWidth + 32 }}>
					{/* Header with date markers */}
					<div className="flex">
						{/* Timeline header */}
						<div className="relative flex-1">
							<TimelineHeader range={range} visibleRange={visibleRange} />
						</div>
					</div>

					{/* Project rows */}
					<div className="">
						{projects.map((project) => (
							<TimelineProjectRow
								key={project.id}
								project={project}
								range={range}
								visibleRange={visibleRange}
								onDateChange={onProjectDateChange}
								onProjectClick={onProjectClick}
							/>
						))}
					</div>
				</div>
			</div>
		</div>
	);
}

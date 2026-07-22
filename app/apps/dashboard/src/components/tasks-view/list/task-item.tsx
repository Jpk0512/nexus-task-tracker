"use client";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import { Checkbox } from "@ui/components/ui/checkbox";
import { memo, useCallback } from "react";
import { StatusChangedChip } from "@/components/status-changed-chip";
import { useUser } from "@/components/user-provider";
import type { EnrichedTask } from "@/hooks/use-data";
import { cn } from "@/lib/utils";
import { useTaskHoverStore } from "@/store/task-hover";
import {
	useIsTaskSelected,
	useTaskSelectionStore,
} from "@/store/task-selection";
import type { PropertyKey } from "../properties/task-properties";
import { TaskProperty } from "../properties/task-properties";

type Task = EnrichedTask;

interface TaskItemProps {
	task: Task;
	className?: string;
	onOpenTask: (task: Task) => void;
	visibleProperties: PropertyKey[];
}

/**
 * TaskItem component optimized for virtualized list rendering.
 */
export const TaskItem = memo(function TaskItem({
	task,
	className,
	onOpenTask,
	visibleProperties,
}: TaskItemProps) {
	const isSelected = useIsTaskSelected(task.id);
	const toggleTaskSelection = useTaskSelectionStore(
		(state) => state.toggleTaskSelection,
	);
	const setHoveredTask = useTaskHoverStore((s) => s.setHoveredTask);
	const clearHoveredTask = useTaskHoverStore((s) => s.clearHoveredTask);

	const { listeners, attributes, setNodeRef, transform, isDragging } =
		useDraggable({
			id: task.id,
		});
	const { setNodeRef: setDroppableNodeRef } = useDroppable({
		id: task.id,
	});

	const user = useUser();

	const handleClick = useCallback(
		(e: React.MouseEvent<HTMLButtonElement>) => {
			if (isDragging) return;
			e.preventDefault();
			onOpenTask(task);
		},
		[isDragging, onOpenTask, task],
	);

	const handleCheckboxChange = useCallback(() => {
		toggleTaskSelection(task.id);
	}, [toggleTaskSelection, task.id]);

	const handleCheckboxClick = useCallback(
		(e: React.MouseEvent<HTMLButtonElement>) => {
			e.stopPropagation();
		},
		[],
	);

	return (
		<div
			className={cn(
				"group/task flex items-center gap-2 rounded-sm px-4 transition-colors hover:bg-accent dark:hover:bg-accent/30",
				{
					"z-50 opacity-50": isDragging,
				},
			)}
			ref={(node) => {
				setNodeRef(node);
				setDroppableNodeRef(node);
			}}
			onMouseEnter={() => setHoveredTask(task.id)}
			onMouseLeave={() => clearHoveredTask(task.id)}
			{...listeners}
			{...attributes}
			style={{
				transform: transform
					? `translate3d(${transform.x}px, ${transform.y}px, 0)`
					: undefined,
			}}
		>
			<Checkbox
				checked={isSelected}
				onCheckedChange={handleCheckboxChange}
				onClick={handleCheckboxClick}
			/>
			<button
				type="button"
				className={cn(
					"flex w-full flex-col justify-between gap-2 bg-transparent py-2 sm:flex-row",
					className,
				)}
				onClick={handleClick}
			>
				<div className="flex items-center gap-2 text-start text-sm">
					{visibleProperties.includes("priority") && (
						<TaskProperty property="priority" task={task} />
					)}
					{task.sequence !== null && (
						<span className="text-muted-foreground text-xs tabular-nums">
							{task.project?.prefix ?? user?.team?.prefix}-{task.sequence}
						</span>
					)}
					{visibleProperties.includes("status") && (
						<TaskProperty property="status" task={task} />
					)}
					<h3 className="font-normal">{task.title}</h3>
					<StatusChangedChip
						statusChangedAt={task.statusChangedAt}
						createdAt={task.createdAt}
					/>
				</div>
				<div className="hidden flex-wrap items-center justify-end gap-2 md:flex">
					{visibleProperties.includes("statusChangedAt") && (
						<TaskProperty property="statusChangedAt" task={task} />
					)}
					{visibleProperties.includes("dependencies") && (
						<TaskProperty property="dependencies" task={task} />
					)}
					{visibleProperties.includes("dueDate") && (
						<TaskProperty property="dueDate" task={task} />
					)}
					{visibleProperties.includes("project") && (
						<TaskProperty property="project" task={task} />
					)}
					{visibleProperties.includes("milestone") && (
						<TaskProperty property="milestone" task={task} />
					)}
					{visibleProperties.includes("labels") && (
						<TaskProperty property="labels" task={task} />
					)}
					{visibleProperties.includes("assignee") && (
						<TaskProperty property="assignee" task={task} />
					)}
				</div>
			</button>
		</div>
	);
});

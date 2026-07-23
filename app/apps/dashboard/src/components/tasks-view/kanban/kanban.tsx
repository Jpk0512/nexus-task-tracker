"use client";

import * as Kanban from "@nexus-app/ui/kanban";
import { Skeleton } from "@ui/components/ui/skeleton";
import { AnimatePresence } from "motion/react";
import { useMemo, useRef } from "react";
import type { EnrichedTask } from "@/hooks/use-data";
import { useTaskHoverQuickActions } from "@/hooks/use-task-hover-actions";
import { useTasksViewContext } from "../tasks-view";
import { BoardColumn } from "./column";
import { KanbanMinimap } from "./kanban-minimap";
import { useKanbanBoard, useKanbanStore } from "./use-kanban-board";

// Board loading skeleton — the underlying `tasks.get` query resolving after
// the route shell has already mounted (e.g. switching group-by, or a
// same-route project change) never hits `[projectId]/loading.tsx`, so the
// board would otherwise flash empty columns while data is in flight.
function TasksBoardSkeleton() {
	return (
		<div
			className="flex min-h-0 grow-1 items-stretch gap-4 overflow-x-hidden py-2"
			aria-hidden
		>
			{Array.from({ length: 4 }).map((_, colIdx) => (
				<div
					key={`board-skel-col-${colIdx}`}
					className="flex min-h-[200px] min-w-86 max-w-86 flex-1 flex-col gap-2 rounded-sm bg-card p-2 dark:bg-card/30"
				>
					<div className="flex items-center gap-2">
						<Skeleton className="size-4 rounded" />
						<Skeleton className="h-3.5 w-20" />
					</div>
					{Array.from({ length: 3 }).map((_, cardIdx) => (
						<Skeleton
							key={`board-skel-card-${colIdx}-${cardIdx}`}
							className="h-24 w-full rounded-md"
						/>
					))}
				</div>
			))}
		</div>
	);
}

export function TasksBoard() {
	const { setActiveTaskId, setOverColumnName } = useKanbanStore();
	const scrollContainerRef = useRef<HTMLDivElement>(null);
	useTaskHoverQuickActions();
	const { isLoading } = useTasksViewContext();

	// Use our custom hook for logic
	const { boardData, reorderTask, columns } = useKanbanBoard();

	const formattedBoardData = useMemo(() => {
		if (!boardData) return {};
		return Object.entries(boardData).reduce(
			(acc, [columnName, { tasks }]) => {
				acc[columnName] = tasks as EnrichedTask[];
				return acc;
			},
			{} as Record<string, EnrichedTask[]>,
		);
	}, [boardData]);

	const columnsArray = useMemo(() => {
		if (!boardData) return [];
		return Object.entries(boardData).map(([columnName, { column, tasks }]) => {
			return { name: columnName, column, tasks: tasks as EnrichedTask[] };
		});
	}, [formattedBoardData]);

	// Only the *initial* load renders the skeleton — once columns exist, a
	// background refetch (filter change, realtime update) keeps the board as
	// interactive drag-and-drop surface instead of yanking it out from under
	// an in-progress drag.
	if (isLoading && columnsArray.length === 0) {
		return (
			<div className="flex min-h-0 grow-1 flex-col">
				<TasksBoardSkeleton />
			</div>
		);
	}

	return (
		// min-h-0 is the required partner to grow-1 here: without it this flex
		// item refuses to shrink below its content's natural height, so a tall
		// board grows the whole page instead of stopping at the available
		// space and letting BoardColumn's own internal list scroll.
		<div className="flex min-h-0 grow-1 flex-col">
			<Kanban.Root
				value={formattedBoardData}
				getItemValue={(item) => item.id}
				onDragEnd={async ({ active, over }) => {
					if (!over) return;

					const isColumnDrag = columns?.some((col) => col.name === active.id);

					setActiveTaskId(undefined);
					setOverColumnName(undefined);

					if (isColumnDrag) {
						// await reorderColumn(active.id as string, over.id as string);
					} else {
						// It is a task drag
						// "over.id" might be a task ID, OR a column name (if dropping on empty column)
						await reorderTask(
							active.id as string,
							over.id as string,
							over.id as string,
						);
					}
				}}
				onDragStart={({ active }) => setActiveTaskId(active.id as string)}
				onDragCancel={() => {
					setActiveTaskId(undefined);
					setOverColumnName(undefined);
				}}
				onDragOver={({ over }) => {
					const overId = over?.id as string | undefined;
					const isColumn = columns?.some((col) => col.name === overId);
					if (isColumn) {
						setOverColumnName(overId);
						return;
					}

					const isTask = Object.keys(formattedBoardData).some((columnName) =>
						formattedBoardData[columnName]?.some((task) => task.id === overId),
					);
					if (isTask) {
						const columnName = Object.keys(formattedBoardData).find(
							(columnName) =>
								formattedBoardData[columnName]?.some(
									(task) => task.id === overId,
								),
						);
						setOverColumnName(columnName);
					}
				}}
			>
				<AnimatePresence mode="popLayout">
					<Kanban.Board asChild>
						<div
							ref={scrollContainerRef}
							className="scrollbar-hide flex w-full items-stretch gap-4 overflow-x-auto py-2"
						>
							{columnsArray.map(({ name: columnName, column, tasks }) => {
								return (
									<BoardColumn
										key={columnName}
										column={column}
										columnName={columnName}
										tasks={tasks}
									/>
								);
							})}
						</div>
					</Kanban.Board>
				</AnimatePresence>

				<Kanban.Overlay>
					<div className="size-full bg-primary/10" />
				</Kanban.Overlay>
			</Kanban.Root>

			<KanbanMinimap
				scrollContainerRef={scrollContainerRef}
				columnsCount={columnsArray.length}
			/>
		</div>
	);
}

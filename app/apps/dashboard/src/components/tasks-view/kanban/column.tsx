"use client";

import { Button } from "@nexus-app/ui/button";
import * as Kanban from "@nexus-app/ui/kanban";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Badge } from "@ui/components/ui/badge";
import { Minimize2Icon, PlusIcon } from "lucide-react";
import { useRef } from "react";
import type { GenericGroup } from "@/components/tasks-view/tasks-group";
import type { EnrichedTask } from "@/hooks/use-data";
import { useTaskParams } from "@/hooks/use-task-params";
import { cn } from "@/lib/utils";
import { TaskContextMenu } from "../../task-context-menu";
import { useTasksViewContext } from "../tasks-view";
import { KanbanTask } from "./kanban-task";
import { useKanbanStore } from "./use-kanban-board";

interface BoardColumnProps {
	column: GenericGroup;
	columnName: string;
	tasks: EnrichedTask[];
}

// Columns beyond this length switch to a windowed (virtualized) render — the
// vast majority of columns stay well under it, so the common case renders
// pixel-identical to before. `Kanban.Column`'s SortableContext items come
// from board data (`context.items[value]`), not from rendered DOM children,
// so only rendering a visible slice doesn't desync dnd-kit's index math.
const VIRTUALIZE_THRESHOLD = 30;
const ESTIMATED_TASK_CARD_HEIGHT = 140;

export function BoardColumn({ column, columnName, tasks }: BoardColumnProps) {
	const { hiddenColumns, toggleColumnHide } = useKanbanStore();

	const { overColumnName, activeTaskId } = useKanbanStore();
	const { setParams: setTaskParams } = useTaskParams();
	const { filters } = useTasksViewContext();

	const open = !hiddenColumns.includes(columnName);
	const isHovered = overColumnName === columnName && Boolean(activeTaskId);

	const scrollContainerRef = useRef<HTMLDivElement>(null);
	const shouldVirtualize = tasks.length > VIRTUALIZE_THRESHOLD;
	const virtualizer = useVirtualizer({
		count: tasks.length,
		getScrollElement: () => scrollContainerRef.current,
		estimateSize: () => ESTIMATED_TASK_CARD_HEIGHT,
		overscan: 6,
	});

	if (!open) {
		return (
			<Kanban.Column
				value={columnName}
				className={cn(
					"w-12 rounded-full bg-gradient-to-b from-secondary/20 via-transparent to-transparent pt-4 transition-colors duration-300 hover:from-secondary/40",
					{
						"from-accent/80": isHovered,
					},
				)}
				onClick={() => {
					toggleColumnHide(columnName);
				}}
			>
				<div className="flex flex-col items-center gap-2">
					{column.icon}
					<span className="text-muted-foreground text-sm">{tasks.length}</span>
				</div>
				<div className="grow-1 overflow-y-auto px-2" />
			</Kanban.Column>
		);
	}

	return (
		<Kanban.Column
			className={cn(
				// No fixed vh height: the parent board row is `items-stretch`, so
				// this column naturally fills whatever height is actually
				// available (robust to window resize) instead of guessing a
				// pixel offset that breaks at short window heights.
				"flex min-h-[200px] min-w-86 max-w-86 flex-1 grow-1 flex-col rounded-sm bg-card p-2 shadow-none dark:bg-card/30",
			)}
			value={columnName}
		>
			<div className="flex items-center justify-between">
				<div
					className={cn("flex items-center gap-2", {
						"opacity-50": tasks.length === 0,
					})}
				>
					<Badge
						variant="secondary"
						className={cn(
							"pointer-events-none space-x-1 rounded-none bg-transparent text-sm",
							{ "text-muted-foreground": tasks.length === 0 },
						)}
					>
						{column.icon}
					</Badge>
					<span className="font-medium text-sm">{columnName}</span>
					<span className="rounded-sm border px-1 font-mono text-muted-foreground text-xs">
						{tasks.length}
					</span>
				</div>

				<div className="flex items-center gap-2">
					<Button
						size="sm"
						variant="ghost"
						onClick={() => {
							toggleColumnHide(columnName);
						}}
					>
						<Minimize2Icon />
					</Button>
					<Button
						size="sm"
						variant="ghost"
						onClick={() => {
							setTaskParams({
								createTask: true,
								taskStatusId: column.id,
								taskProjectId:
									filters.projectId?.length > 0 ? filters.projectId[0] : null,
							});
						}}
					>
						<PlusIcon />
					</Button>
				</div>
			</div>
			<div
				className="min-h-0 grow-1 overflow-y-auto px-2"
				ref={scrollContainerRef}
			>
				<div className="relative h-full space-y-2">
					{shouldVirtualize ? (
						<div
							style={{
								height: virtualizer.getTotalSize(),
								width: "100%",
								position: "relative",
							}}
						>
							{virtualizer.getVirtualItems().map((virtualRow) => {
								const task = tasks[virtualRow.index];
								if (!task) return null;
								return (
									<div
										key={task.id}
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
										<TaskContextMenu task={task}>
											<Kanban.Item value={task.id} asHandle asChild>
												<KanbanTask task={task} />
											</Kanban.Item>
										</TaskContextMenu>
									</div>
								);
							})}
						</div>
					) : (
						tasks.map((task) => (
							<TaskContextMenu task={task} key={task.id}>
								<Kanban.Item
									value={task.id}
									asHandle
									asChild
									// onClick={(e) => {
									// 	e.preventDefault();
									// 	e.stopPropagation();

									// 	// Prefetch/Cache data before navigation
									// 	queryClient.setQueryData(
									// 		trpc.tasks.getById.queryKey({ id: task.id }),
									// 		task,
									// 	);
									// 	setTaskParams({ taskId: task.id });
									// }}
								>
									<KanbanTask task={task} />
								</Kanban.Item>
							</TaskContextMenu>
						))
					)}

					<div>
						<Button
							className="w-full justify-start border border-transparent border-dashed text-start text-xs hover:border-input hover:bg-accent/30!"
							variant={"ghost"}
							onClick={() => {
								setTaskParams({
									createTask: true,
									taskStatusId: column.id,
									taskProjectId:
										filters.projectId?.length > 0 ? filters.projectId[0] : null,
								});
							}}
						>
							<PlusIcon className="size-3.5" />
							Create Task
						</Button>
					</div>

					{/* Drag overlay */}
					<div
						className={cn(
							"pointer-events-none absolute inset-0 flex items-center justify-center rounded-sm bg-black/80 opacity-0 backdrop-blur-none transition-opacity duration-200",
							{
								"bg-black/40 opacity-100": isHovered,
							},
						)}
					>
						<div className="text-xs">
							Drag here to move task to{" "}
							<strong className="border px-1 py-0.5">{columnName}</strong>
						</div>
					</div>
				</div>
			</div>
		</Kanban.Column>
	);
}

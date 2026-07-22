import type { RouterOutputs } from "@nexus-app/trpc";
import { useTaskPanel } from "@/components/panels/task-panel";
import { StatusChangedChip } from "@/components/status-changed-chip";
import { MetadataConflictBadge } from "@/components/tasks/metadata-conflict-badge";
import { useUser } from "@/components/user-provider";
import type { EnrichedTask } from "@/hooks/use-data";
import { updateTaskInCache } from "@/hooks/use-data-cache-helpers";
import { cn } from "@/lib/utils";
import { useTaskHoverStore } from "@/store/task-hover";
import {
	PropertyAssignee,
	PropertyChecklist,
	PropertyDependencies,
	PropertyDueDate,
	PropertyLabels,
	PropertyMilestone,
	PropertyPriority,
	PropertyProject,
	PropertyStatus,
} from "../properties/task-properties-components";

export const KanbanTask = ({
	task,
	ref,
	className,
	...props
}: {
	className?: string;
	task: EnrichedTask;
	ref?: React.Ref<HTMLDivElement>;
}) => {
	const user = useUser();
	const taskPanel = useTaskPanel();
	const setHoveredTask = useTaskHoverStore((s) => s.setHoveredTask);
	const clearHoveredTask = useTaskHoverStore((s) => s.clearHoveredTask);

	return (
		<div
			className={cn(
				"group/task relative flex cursor-pointer flex-col rounded-md border bg-popover transition-colors hover:bg-accent/30",
				{
					"opacity-50!": task.status?.type === "done",
				},
				"slide-in-from-bottom-5 fade-in animate-in ease-in",
				className,
			)}
			ref={ref}
			onClick={(e) => {
				updateTaskInCache(task);
				taskPanel.open(task.id);
			}}
			onMouseEnter={() => setHoveredTask(task.id)}
			onMouseLeave={() => clearHoveredTask(task.id)}
			{...props}
		>
			<div className="p-2">
				<div className="flex h-full grow-1 flex-col justify-between gap-2">
					<div className="flex items-center justify-between gap-2">
						<div className={"flex items-center gap-2 text-xs"}>
							<PropertyPriority task={task} />
							{task.sequence !== null && (
								<span className="mr-2 text-muted-foreground tabular-nums">
									{task.project?.prefix ?? user?.team?.prefix}-{task.sequence}
								</span>
							)}
							<PropertyLabels task={task} />
						</div>
						<div className="flex items-center gap-1.5">
							<StatusChangedChip
								statusChangedAt={task.statusChangedAt}
								createdAt={task.createdAt}
							/>
							<PropertyAssignee task={task} />
						</div>
					</div>
					<div className="flex items-start gap-2">
						<PropertyStatus task={task} />
						<div className="line-clamp-3 break-words font-medium text-sm">
							{task.title}
						</div>
						<MetadataConflictBadge
							size="md"
							task={{
								id: task.id,
								title: task.title,
								statusType: task.status?.type ?? null,
								priority: task.priority ?? null,
								dueDate: task.dueDate ?? null,
								assigneeId: task.assigneeId ?? null,
								dependencies: task.dependencies ?? null,
								checklistSummary: task.checklistSummary ?? null,
							}}
							className="ml-auto"
						/>
					</div>

					<div className="flex flex-wrap items-center gap-1.5">
						<PropertyDependencies task={task} />

						<PropertyProject task={task} />
						<PropertyMilestone task={task} />
						<PropertyDueDate task={task} />
						<PropertyChecklist task={task} />
					</div>
				</div>
			</div>
			{/* Too much visual noise */}
			{/* <KanbanTaskStamp task={task} /> */}
		</div>
	);
};

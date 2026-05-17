"use client";
import { useFormContext } from "react-hook-form";
import { TaskLinkedContent } from "@/components/panels/task-linked-content";
import { Assignee } from "./assignee";
import { TaskFormDependenciesList } from "./dependencies-list";
import { DueDate } from "./due-date";
import type { TaskFormValues } from "./form-type";
import { Labels } from "./labels";
import { MilestoneSelect } from "./milestone-select";
import { Priority } from "./priority";
import { ProjectSelect } from "./project-select";
import { Recurring } from "./recurring";
import { StatusSelect } from "./status-select";

export const TaskFormProperties = () => {
	const form = useFormContext<TaskFormValues>();
	const taskId = form.watch("id");

	return (
		<div className="space-y-2">
			<Labels />

			<div className="flex flex-wrap items-center gap-2">
				<Assignee />
				<DueDate />
				<Priority />
				<ProjectSelect />
				<StatusSelect />
				<MilestoneSelect />
				<Recurring />
			</div>

			<div className="mt-4">
				<TaskFormDependenciesList />
			</div>

			{taskId && (
				<div className="mt-4">
					<TaskLinkedContent taskId={taskId} />
				</div>
			)}
		</div>
	);
};

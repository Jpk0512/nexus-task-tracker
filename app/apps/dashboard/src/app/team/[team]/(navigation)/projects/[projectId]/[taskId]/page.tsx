import { notFound } from "next/navigation";
import { BreadcrumbSetter } from "@/components/breadcrumbs";
import { TaskForm } from "@/components/forms/task-form/form";
import { trpcClient } from "@/utils/trpc";

interface Props {
	params: Promise<{
		team: string;
		projectId: string;
		taskId: string;
	}>;
}

export default async function TaskPage({ params }: Props) {
	const { projectId, taskId } = await params;

	const task = await trpcClient.tasks.getById
		.query({
			id: taskId,
		})
		.catch(() => null);

	if (!task) {
		// Task may be missing during prefetch (hover) or after delete — render
		// a graceful empty state instead of crashing on `task.title`.
		notFound();
	}

	return (
		<div className="mx-auto max-w-6xl animate-blur-in">
			<BreadcrumbSetter
				crumbs={[
					{
						label: task.title ?? "Untitled",
						segments: ["projects", projectId, taskId],
					},
				]}
			/>
			<TaskForm
				defaultValues={{ ...task, labels: task?.labels?.map((l) => l.id) }}
			/>
		</div>
	);
}

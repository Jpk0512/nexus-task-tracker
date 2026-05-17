"use client";

import { useQuery } from "@tanstack/react-query";
import { Button } from "@ui/components/ui/button";
import { CalendarSyncIcon } from "lucide-react";
import { Suspense } from "react";
import { TasksView } from "@/components/tasks-view/tasks-view";
import { useTaskParams } from "@/hooks/use-task-params";
import { trpc } from "@/utils/trpc";

export default function Page() {
	return (
		<Suspense>
			<RecurringPageContent />
		</Suspense>
	);
}

function RecurringPageContent() {
	const { setParams } = useTaskParams();

	// Peek probe: do any recurring tasks exist? Drives the empty-state CTA.
	// Light-touch — pageSize 1 + a long staleTime.
	const { data, isLoading } = useQuery(
		trpc.tasks.get.queryOptions({ recurring: true, pageSize: 1 } as any, {
			staleTime: 30 * 1000,
		}),
	);

	const count =
		(data as any)?.pagination?.total ?? (data as any)?.data?.length ?? 0;
	const showEmpty = !isLoading && count === 0;

	const handleCreate = () => {
		setParams({ createTask: true, taskRecurring: true });
	};

	if (showEmpty) {
		return (
			<div className="flex h-full flex-col">
				<header className="border-border border-b px-6 py-3">
					<h1 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
						Recurring
					</h1>
					<p className="mt-0.5 text-[12px] text-muted-foreground">
						Tasks that repeat on a schedule.
					</p>
				</header>
				<div className="flex flex-1 items-center justify-center px-6 py-10">
					<div className="flex max-w-md flex-col items-center gap-3 text-center">
						<div className="flex size-12 items-center justify-center rounded-full border border-violet-400/30 bg-violet-400/[0.08] text-violet-300">
							<CalendarSyncIcon className="size-5" />
						</div>
						<h2 className="font-[510] text-[15px] text-foreground tracking-[-0.012em]">
							No recurring tasks yet
						</h2>
						<p className="max-w-sm text-balance text-[12.5px] text-muted-foreground">
							Capture work that repeats — standups, weekly reviews, monthly
							reports. Nexus will spawn a fresh task on every cadence so
							nothing slips.
						</p>
						<Button
							type="button"
							size="sm"
							className="mt-2 text-[12px]"
							onClick={handleCreate}
						>
							<CalendarSyncIcon className="size-3.5" />
							Create recurring task
						</Button>
						<p className="mt-1 text-[11px] text-muted-foreground/80">
							Tip: press{" "}
							<kbd className="rounded-sm border border-border/80 bg-muted/40 px-1 py-0.5 font-mono text-[10px]">
								c
							</kbd>{" "}
							anywhere to open the create dialog.
						</p>
					</div>
				</div>
			</div>
		);
	}

	return (
		<div className="h-full">
			<TasksView
				defaultFilters={{
					viewType: "list",
					recurring: true,
					showEmptyColumns: false,
					statusType: ["backlog", "done", "in_progress", "review", "to_do"],
					view: "list",
					groupBy: "project",
				}}
			/>
		</div>
	);
}

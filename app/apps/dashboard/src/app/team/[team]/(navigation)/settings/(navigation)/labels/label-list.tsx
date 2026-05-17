"use client";
import { t } from "@mimir/locale";
import { Button } from "@mimir/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@mimir/ui/dropdown-menu";
import { LabelBadge } from "@mimir/ui/label-badge";
import { useMutation, useQuery } from "@tanstack/react-query";
import { cn } from "@ui/lib/utils";
import { EllipsisIcon, PlusIcon, TagsIcon } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useLabelParams } from "@/hooks/use-task-label-params";
import { queryClient, trpc } from "@/utils/trpc";

export const LabelList = () => {
	const { team } = useParams<{ team: string }>();
	const { setParams } = useLabelParams();
	// taskCount is wired through the DB query in @mimir/db/queries/labels —
	// `count(labelsOnTasks.taskId)` already returns the real assignment count.
	// We keep it here and additionally hot-link the chip to a filtered
	// /tasks?label=<id> view so a "12 tasks" badge becomes navigable.
	const { data: labels } = useQuery(trpc.labels.get.queryOptions({}));

	const { mutate: deleteLabel } = useMutation(
		trpc.labels.delete.mutationOptions({
			onSuccess: () => {
				queryClient.invalidateQueries(trpc.labels.get.queryOptions({}));
			},
		}),
	);

	return (
		<div className="text-xs">
			<div>
				{labels?.map((label) => {
					const count = label.taskCount ?? 0;
					const noun = t("settings.labels.table.tasks").toLowerCase();
					return (
						<div
							key={label.id}
							className="flex items-center gap-4 rounded-sm px-4 py-2 hover:bg-accent dark:hover:bg-accent/30"
						>
							<div className="flex min-w-0 flex-1 items-center gap-2">
								<div className="font-medium">
									<LabelBadge {...label} />
								</div>
							</div>
							{count > 0 ? (
								<Link
									href={`/team/${team}/mytasks?label=${label.id}`}
									className={cn(
										"rounded-sm px-1.5 py-0.5 text-xs tabular-nums",
										"text-foreground hover:bg-accent hover:underline",
									)}
									title={`View ${count} ${noun} with this label`}
								>
									{count} {noun}
								</Link>
							) : (
								<span
									className="text-muted-foreground/70 text-xs tabular-nums"
									title="No tasks have this label yet"
								>
									{count} {noun}
								</span>
							)}
							<div>
								<DropdownMenu>
									<DropdownMenuTrigger asChild>
										<Button size={"icon"} variant="ghost" className="size-5">
											<EllipsisIcon />
										</Button>
									</DropdownMenuTrigger>
									<DropdownMenuContent>
										<DropdownMenuItem
											onClick={() => {
												queryClient.setQueryData(
													trpc.labels.getById.queryKey({ id: label.id }),
													label,
												);
												setParams({ labelId: label.id });
											}}
										>
											Edit
										</DropdownMenuItem>
										<DropdownMenuItem
											variant="destructive"
											onClick={() => deleteLabel({ id: label.id })}
										>
											Delete
										</DropdownMenuItem>
									</DropdownMenuContent>
								</DropdownMenu>
							</div>
						</div>
					);
				})}
				{labels?.length === 0 && (
					<div className="flex flex-col items-center justify-center px-4 py-12 text-center">
						<TagsIcon className="mb-4 size-12 text-muted-foreground/50" />
						<h3 className="mb-2 font-medium text-sm">No labels yet</h3>
						<p className="mb-4 max-w-sm text-muted-foreground text-xs">
							Labels help you categorize and filter tasks across projects.
							Create your first label to get started.
						</p>
						<Button
							size="sm"
							type="button"
							onClick={() => setParams({ createLabel: true })}
						>
							<PlusIcon className="mr-2 size-4" />
							Create your first label
						</Button>
					</div>
				)}
				{!labels && (
					<div className="px-4 py-8 text-center text-muted-foreground">
						Loading labels...
					</div>
				)}
			</div>
		</div>
	);
};

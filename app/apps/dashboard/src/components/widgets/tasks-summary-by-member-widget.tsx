"use client";
import { Card, CardContent, CardDescription, CardHeader } from "@mimir/ui/card";
import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@ui/components/ui/skeleton";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@ui/components/ui/table";
import { sub } from "date-fns";
import { useState } from "react";
import { trpc } from "@/utils/trpc";

const colors = [
	"var(--chart-1)",
	"var(--chart-2)",
	"var(--chart-3)",
	"var(--chart-4)",
	"var(--chart-5)",
];

export const TasksSummaryByMemberWidget = () => {
	const [dateRange, setDateRange] = useState<{
		startDate: Date;
		endDate: Date;
	}>({
		startDate: sub(new Date(), { days: 7 }),
		endDate: new Date(),
	});

	const { data } = useQuery(
		trpc.widgets.tasksSummaryByMember.queryOptions(
			{
				...dateRange,
			},
			{
				select: (data) =>
					data.map((item, index) => ({
						...item,
						fill:
							item.member.color ||
							"var(--muted-foreground)" ||
							colors[index % colors.length],
					})),
			},
		),
	);

	return (
		<Card>
			<CardHeader>
				<CardDescription>
					Tasks completed by each member over the last week.
				</CardDescription>
			</CardHeader>
			<CardContent>
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>Member</TableHead>
							<TableHead className="text-right">Assigned</TableHead>
							<TableHead className="text-right">Completed</TableHead>
						</TableRow>
					</TableHeader>
					<TableBody>
						{data ? (
							data.map((item) => (
								<TableRow key={item.member.id}>
									<TableCell>
										<div className="flex items-center gap-2">
											<div
												className="h-3 w-3 rounded-full"
												style={{
													backgroundColor:
														item.member.color || "var(--muted-foreground)",
												}}
											/>
											<span>{item.member.name}</span>
										</div>
									</TableCell>
									<TableCell className="text-right">
										{item.assignedCount}
									</TableCell>
									<TableCell className="text-right">
										{item.completedCount}
									</TableCell>
								</TableRow>
							))
						) : (
							<TableRow>
								<td colSpan={3}>
									<Skeleton className="h-8 w-full" />
								</td>
							</TableRow>
						)}
					</TableBody>
				</Table>
				{/* <ChartContainer config={{}} className="mx-auto aspect-square h-48">
					<PieChart>
						<ChartTooltip
							cursor={false}
							content={<ChartTooltipContent hideLabel />}
						/>
						<Pie
							data={completedData}
							dataKey="completedCount"
							nameKey="member.name"
							innerRadius={60}
							outerRadius={80}
						/>
						<Pie
							data={assignedData}
							dataKey="assignedCount"
							nameKey="member.name"
							outerRadius={50}
						/>
					</PieChart>
				</ChartContainer> */}
			</CardContent>
		</Card>
	);
};

import { getStatuses } from "@nexus-app/db/queries/statuses";
import { getMembers } from "@nexus-app/db/queries/teams";
import { getTasks } from "@nexus-app/db/queries/tasks";
import { statusTypeEnum } from "@nexus-app/db/schema";
import { getTaskPermalink } from "@nexus-app/utils/tasks";
import { tool } from "ai";
import z from "zod";
import { getToolContext } from "../agents/config/shared";

export const getTasksToolSchema = z.object({
	search: z.string().optional().describe("Search query"),
	assigneeId: z.array(z.string()).optional().describe("Users IDs (uuid)"),
	statusType: z
		.array(z.enum(statusTypeEnum.enumValues))
		.optional()
		.describe("Status type"),
	statusChangedAtBefore: z
		.string()
		.optional()
		.describe("Status changed before date in ISO format"),
	statusChangedAtAfter: z
		.string()
		.optional()
		.describe("Status changed after date in ISO format"),
	createdAtBefore: z
		.string()
		.optional()
		.describe("Created before date in ISO format"),
	createdAtAfter: z
		.string()
		.optional()
		.describe("Created after date in ISO format"),
	cursor: z.string().optional().describe("Pagination cursor"),
	pageSize: z.number().min(1).max(100).default(10).describe("Page size"),
});

export const getTasksTool = tool({
	description: "Retrieve a list of tasks",
	inputSchema: getTasksToolSchema,
	execute: async function* (
		{
			search,
			cursor,
			pageSize,
			assigneeId,
			statusType,
			createdAtAfter,
			createdAtBefore,
			statusChangedAtAfter,
			statusChangedAtBefore,
		},
		executionOptions,
	) {
		try {
			const {
				userId: _userId,
				teamId,
				teamSlug: _teamSlug,
				writer,
			} = getToolContext(executionOptions);

			const statusChangedAt =
				statusChangedAtAfter && statusChangedAtBefore
					? [new Date(statusChangedAtAfter), new Date(statusChangedAtBefore)]
					: undefined;

			const createdAt =
				createdAtAfter && createdAtBefore
					? [new Date(createdAtAfter), new Date(createdAtBefore)]
					: undefined;

			const [result, statusesResult, members] = await Promise.all([
				getTasks({
					teamId: teamId,
					assigneeId: assigneeId,
					statusType,
					view: "board",
					cursor,
					pageSize,
					search,
					statusChangedAt,
					createdAt,
				}),
				getStatuses({ pageSize: 100, teamId }),
				getMembers({ teamId }),
			]);

			if (result.data.length === 0) {
				yield { type: "text", text: "No tasks found." };
				return;
			}

			const statusNameById = new Map(
				statusesResult.data.map((s) => [s.id, s.name]),
			);
			const memberNameById = new Map(
				members.map((m) => [m.id, m.name]),
			);

			const mappedData = result.data.map((task) => ({
				id: task.id,
				title: task.title,
				priority: task.priority,
				statusId: task.statusId,
				statusName: statusNameById.get(task.statusId) ?? null,
				assigneeId: task.assigneeId,
				assigneeName: task.assigneeId
					? (memberNameById.get(task.assigneeId) ?? null)
					: null,
				dueDate: task.dueDate,
				createdAt: task.createdAt,
				updatedAt: task.updatedAt,
				sequence: task.sequence,
				dependencies: task.dependencies,
				statusChangedAt: task.statusChangedAt,
				completedByUserId: task.completedBy,
				taskUrl: getTaskPermalink(task.permalinkId),
			}));

			if (writer) {
				for (const task of mappedData.slice(0, 5)) {
					writer.write({
						type: "data-task",
						data: task,
					});
				}
			}

			yield mappedData;
		} catch (error) {
			console.error("Error in getTasksTool:", error);
			throw error;
		}
	},
});

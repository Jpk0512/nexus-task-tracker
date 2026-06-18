import { schemaTask } from "@trigger.dev/sdk";
import z from "zod";

const PM_JOB_ID = "execute-pm-agent";

export const executePMAgentJob = schemaTask({
	id: PM_JOB_ID,
	schema: z.object({
		projectId: z.string(),
		teamId: z.string(),
		trigger: z.object({
			type: z.enum([
				"task_status_changed",
				"task_completed",
				"milestone_completed",
				"agent_mention",
				"project_created",
				"manual",
			]),
			taskId: z.string().optional(),
			taskTitle: z.string().optional(),
			oldStatus: z.string().optional(),
			newStatus: z.string().optional(),
			newStatusType: z.string().optional(),
			milestoneId: z.string().optional(),
			milestoneName: z.string().optional(),
			mentionedByUserId: z.string().optional(),
			mentionedByUserName: z.string().optional(),
			message: z.string().optional(),
			instruction: z.string().optional(),
		}),
	}),
	maxDuration: 15 * 60, // 15 minutes
	run: async (_payload) => {
		return false;
	},
});

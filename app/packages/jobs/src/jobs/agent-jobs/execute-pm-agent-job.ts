import { defineJob } from "../../init";

const PM_JOB_ID = "execute-pm-agent";

export const executePMAgentJob = defineJob({
	id: PM_JOB_ID,
	run: async (_payload: {
		projectId: string;
		teamId: string;
		trigger: {
			type:
				| "task_status_changed"
				| "task_completed"
				| "milestone_completed"
				| "agent_mention"
				| "project_created"
				| "manual";
			taskId?: string;
			taskTitle?: string;
			oldStatus?: string;
			newStatus?: string;
			newStatusType?: string;
			milestoneId?: string;
			milestoneName?: string;
			mentionedByUserId?: string;
			mentionedByUserName?: string;
			message?: string;
			instruction?: string;
		};
	}) => {
		return false;
	},
});

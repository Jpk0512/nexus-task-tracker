import { systemUserCache } from "@nexus-app/cache/system-user-cache";
import { and, eq } from "drizzle-orm";
import { db } from "../index";
import { checklistItems, projects, statuses, tasks, users } from "../schema";

const AGENT_TASK_JOB_ID = "execute-agent-task-plan";
const PM_AGENT_JOB_ID = "execute-pm-agent";

// ---------------------------------------------------------------------------
// Local job enqueue — thin shim so @nexus-app/db does not depend on @nexus-app/jobs.
// The jobs package registers its enqueue fn into globalThis at startup so
// db can call it without a compile-time import (avoids db → jobs cycle).
// ---------------------------------------------------------------------------
type EnqueueFn = (
	jobId: string,
	payload: Record<string, unknown>,
) => Promise<{ id: string }>;

async function enqueueJob(
	jobId: string,
	payload: Record<string, unknown>,
): Promise<{ id: string }> {
	const fn = (globalThis as Record<string, unknown>).__jobsEnqueue as
		| EnqueueFn
		| undefined;
	if (!fn) {
		console.warn(
			`[agent-triggers] job enqueue not registered yet; dropping job ${jobId}`,
		);
		return { id: "noop" };
	}
	return fn(jobId, payload);
}

/**
 * Check if the given user ID is the system user (agent)
 * Results are cached for performance
 */
export const isSystemUser = async (
	userId: string | null | undefined,
): Promise<boolean> => {
	if (!userId) return false;

	const cached = await systemUserCache.get(userId);
	if (cached !== undefined) {
		return cached;
	}

	const [user] = await db
		.select({ isSystemUser: users.isSystemUser })
		.from(users)
		.where(eq(users.id, userId))
		.limit(1);

	const result = user?.isSystemUser ?? false;

	await systemUserCache.set(userId, result);

	return result;
};

/**
 * Trigger agent task execution when a task is assigned to the system user
 * This should be called after task creation or update
 */
export const triggerAgentTaskExecutionIfNeeded = async ({
	taskId,
	teamId,
	assigneeId,
	previousAssigneeId,
	triggeredBy,
	triggerUserId,
}: {
	taskId: string;
	teamId: string;
	assigneeId: string | null | undefined;
	previousAssigneeId?: string | null;
	triggeredBy: "assignment" | "update" | "comment";
	triggerUserId?: string;
}): Promise<boolean> => {
	const isAssignedToAgent = await isSystemUser(assigneeId);

	if (!isAssignedToAgent) {
		return false;
	}

	if (
		triggerUserId &&
		triggerUserId === assigneeId &&
		triggeredBy === "comment"
	) {
		return false;
	}

	if (
		triggerUserId &&
		triggerUserId === assigneeId &&
		triggeredBy === "update"
	) {
		return false;
	}

	const wasAssignedToAgent = previousAssigneeId
		? await isSystemUser(previousAssigneeId)
		: false;

	if (triggeredBy === "update" && wasAssignedToAgent) {
		return false;
	}

	await enqueueJob(AGENT_TASK_JOB_ID, { taskId, teamId });

	return true;
};

/**
 * Trigger agent to check on a task when a checklist item is completed
 */
export const triggerAgentOnChecklistComplete = async ({
	taskId,
	teamId,
	checklistItemId,
	completedByUserId,
}: {
	taskId: string;
	teamId: string;
	checklistItemId: string;
	completedByUserId?: string;
}): Promise<boolean> => {
	const [task] = await db
		.select({ assigneeId: tasks.assigneeId })
		.from(tasks)
		.where(eq(tasks.id, taskId))
		.limit(1);

	if (!task?.assigneeId) {
		return false;
	}

	const isAssignedToAgent = await isSystemUser(task.assigneeId);
	if (!isAssignedToAgent) {
		return false;
	}

	if (completedByUserId) {
		const isCompletedByAgent = await isSystemUser(completedByUserId);
		if (isCompletedByAgent) {
			return false;
		}
	}

	const pendingItemsWithAssignee = await db
		.select({
			id: checklistItems.id,
			assigneeId: checklistItems.assigneeId,
		})
		.from(checklistItems)
		.where(
			and(
				eq(checklistItems.taskId, taskId),
				eq(checklistItems.teamId, teamId),
				eq(checklistItems.isCompleted, false),
			),
		);

	const hasPendingDelegatedItems = pendingItemsWithAssignee.some(
		(item) => item.assigneeId && item.assigneeId !== task.assigneeId,
	);

	if (hasPendingDelegatedItems) {
		return false;
	}

	await enqueueJob(AGENT_TASK_JOB_ID, { taskId, teamId });

	return true;
};

/**
 * Trigger agent to resolve a checklist item when it's assigned to an agent
 */
export const triggerAgentOnChecklistItemAssignment = async ({
	taskId,
	teamId,
	checklistItemId,
	assigneeId,
	taskAssigneeId,
	assignedByUserId,
}: {
	taskId: string;
	teamId: string;
	checklistItemId: string;
	assigneeId: string;
	taskAssigneeId?: string | null;
	assignedByUserId?: string;
}): Promise<boolean> => {
	const isAssignedToAgent = await isSystemUser(assigneeId);
	if (!isAssignedToAgent) {
		return false;
	}

	if (assignedByUserId && assignedByUserId === assigneeId) {
		return false;
	}

	if (taskAssigneeId && taskAssigneeId === assigneeId) {
		return false;
	}

	await enqueueJob(AGENT_TASK_JOB_ID, { taskId, teamId, checklistItemId });

	return true;
};

// ─── Project Manager Agent Triggers ─────────────────────────────────────────

/**
 * Trigger the PM agent when a task's status changes within a project.
 */
export const triggerPMAgentOnStatusChange = async ({
	taskId,
	teamId,
	oldStatusId,
	newStatusId,
}: {
	taskId: string;
	teamId: string;
	oldStatusId: string;
	newStatusId: string;
	changedByUserId?: string;
}): Promise<boolean> => {
	const [task] = await db
		.select({
			id: tasks.id,
			title: tasks.title,
			projectId: tasks.projectId,
			milestoneId: tasks.milestoneId,
		})
		.from(tasks)
		.where(and(eq(tasks.id, taskId), eq(tasks.teamId, teamId)))
		.limit(1);

	if (!task?.projectId) {
		return false;
	}

	const [oldStatus, newStatus] = await Promise.all([
		db
			.select({ id: statuses.id, name: statuses.name, type: statuses.type })
			.from(statuses)
			.where(eq(statuses.id, oldStatusId))
			.limit(1)
			.then((r) => r[0]),
		db
			.select({ id: statuses.id, name: statuses.name, type: statuses.type })
			.from(statuses)
			.where(eq(statuses.id, newStatusId))
			.limit(1)
			.then((r) => r[0]),
	]);

	if (!oldStatus || !newStatus) {
		return false;
	}

	if (newStatus.type !== "review" && newStatus.type !== "done") {
		return false;
	}

	const trigger =
		newStatus.type === "done"
			? {
					type: "task_completed" as const,
					taskId: task.id,
					taskTitle: task.title,
				}
			: {
					type: "task_status_changed" as const,
					taskId: task.id,
					oldStatus: oldStatus.name,
					newStatus: newStatus.name,
					newStatusType: newStatus.type!,
				};

	await enqueueJob(PM_AGENT_JOB_ID, {
		projectId: task.projectId,
		teamId,
		trigger,
	});

	if (newStatus.type === "done" && task.milestoneId) {
		await triggerPMAgentOnMilestoneCompletion({
			taskId,
			teamId,
			projectId: task.projectId,
			milestoneId: task.milestoneId,
		});
	}

	return true;
};

/**
 * Check if a milestone is fully completed after a task finishes,
 * and trigger the PM agent with a milestone_completed event if so.
 */
const triggerPMAgentOnMilestoneCompletion = async ({
	teamId,
	projectId,
	milestoneId,
}: {
	taskId: string;
	teamId: string;
	projectId: string;
	milestoneId: string;
}): Promise<boolean> => {
	const milestoneTasks = await db
		.select({
			taskId: tasks.id,
			statusType: statuses.type,
		})
		.from(tasks)
		.innerJoin(statuses, eq(tasks.statusId, statuses.id))
		.where(and(eq(tasks.milestoneId, milestoneId), eq(tasks.teamId, teamId)));

	const allDone =
		milestoneTasks.length > 0 &&
		milestoneTasks.every((t) => t.statusType === "done");

	if (!allDone) {
		return false;
	}

	const { milestones } = await import("../schema");
	const [milestone] = await db
		.select({ id: milestones.id, name: milestones.name })
		.from(milestones)
		.where(eq(milestones.id, milestoneId))
		.limit(1);

	if (!milestone) {
		return false;
	}

	await enqueueJob(PM_AGENT_JOB_ID, {
		projectId,
		teamId,
		trigger: {
			type: "milestone_completed" as const,
			milestoneId: milestone.id,
			milestoneName: milestone.name,
		},
	});

	return true;
};

/**
 * Trigger the PM agent when an agent is mentioned in a task comment.
 */
export const triggerPMAgentOnMention = async ({
	taskId,
	teamId,
	mentionedUserId,
	commentByUserId,
	commentByUserName,
	commentText,
}: {
	taskId: string;
	teamId: string;
	mentionedUserId: string;
	commentByUserId: string;
	commentByUserName: string;
	commentText: string;
}): Promise<boolean> => {
	const isMentionedAgent = await isSystemUser(mentionedUserId);
	if (!isMentionedAgent) {
		return false;
	}

	if (mentionedUserId === commentByUserId) {
		return false;
	}

	const [task] = await db
		.select({
			id: tasks.id,
			projectId: tasks.projectId,
			projectLeadId: projects.leadId,
		})
		.from(tasks)
		.innerJoin(projects, eq(tasks.projectId, projects.id))
		.where(and(eq(tasks.id, taskId), eq(tasks.teamId, teamId)))
		.limit(1);

	if (!task?.projectId) {
		return false;
	}

	if (task.projectLeadId === commentByUserId) {
		return false;
	}

	await enqueueJob(PM_AGENT_JOB_ID, {
		projectId: task.projectId,
		teamId,
		trigger: {
			type: "agent_mention" as const,
			taskId: task.id,
			mentionedByUserId: commentByUserId,
			mentionedByUserName: commentByUserName,
			message: commentText,
		},
	});

	return true;
};

/**
 * Trigger the PM agent when a new project is created.
 */
export const triggerPMAgentOnProjectCreation = async ({
	projectId,
	teamId,
}: {
	projectId: string;
	teamId: string;
}): Promise<boolean> => {
	await enqueueJob(PM_AGENT_JOB_ID, {
		projectId,
		teamId,
		trigger: {
			type: "project_created" as const,
		},
	});

	return true;
};

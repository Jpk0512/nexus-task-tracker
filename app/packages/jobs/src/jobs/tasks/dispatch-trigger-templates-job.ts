import { tasks, triggers } from "@mimir/db/schema";
import {
	isSystemTriggerType,
	SYSTEM_TRIGGER_TYPES,
} from "@mimir/utils/system-triggers";
import { and, eq } from "drizzle-orm";
import { defineJob, enqueue, getDb, logger } from "../../init";
import { createTaskFromTemplateJob } from "./create-task-from-template-job";

export const dispatchTriggerTemplatesJob = defineJob({
	id: "dispatch-trigger-templates-job",
	run: async (payload: {
		teamId: string;
		source: "system" | "db";
		triggerType: string;
	}) => {
		const db = getDb();

		if (payload.source === "system") {
			if (!isSystemTriggerType(payload.triggerType)) {
				throw new Error(
					`Unknown system trigger type "${payload.triggerType}". Supported: ${SYSTEM_TRIGGER_TYPES.join(", ")}`,
				);
			}
		}

		const rows = await db
			.select({ id: tasks.id })
			.from(tasks)
			.innerJoin(triggers, eq(tasks.triggerId, triggers.id))
			.where(
				and(
					eq(tasks.teamId, payload.teamId),
					eq(tasks.isTemplate, true),
					eq(triggers.teamId, payload.teamId),
					eq(triggers.type, payload.triggerType),
				),
			);
		const templateIds = rows.map((row) => row.id);

		for (const templateTaskId of templateIds) {
			await enqueue(createTaskFromTemplateJob.id, {
				templateTaskId,
				teamId: payload.teamId,
				source: payload.source,
				triggerType: payload.triggerType,
			});
		}

		logger.info(
			`Dispatched ${templateIds.length} template task instantiations for team ${payload.teamId} (${payload.source}:${payload.triggerType}).`,
		);
	},
});

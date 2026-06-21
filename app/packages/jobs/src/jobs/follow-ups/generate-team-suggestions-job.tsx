import { TZDate } from "@date-fns/tz";
import { createTaskSuggestion } from "@nexus-app/db/queries/tasks-suggestions";
import { autopilotSettings, statuses, tasks, teams } from "@nexus-app/db/schema";
import { and, arrayContains, eq, isNull, lte, not, or, sql } from "drizzle-orm";
import { defineJob, getDb, logger } from "../../init";

export const generateTeamSuggestionsJob = defineJob({
	id: "generate-team-suggestions-job",
	run: async (payload: { teamId: string }) => {
		const db = getDb();

		const [team] = await db
			.select()
			.from(teams)
			.where(eq(teams.id, payload.teamId))
			.limit(1);

		if (!team) {
			logger.warn(`Team with ID ${payload.teamId} not found. Exiting.`);
			return;
		}

		const currentDate = new TZDate(new Date(), team.timezone || "UTC");
		const currentWeekday = currentDate.getDay();

		const [settings] = await db
			.select()
			.from(autopilotSettings)
			.where(
				and(
					eq(autopilotSettings.teamId, payload.teamId),
					or(
						isNull(autopilotSettings.allowedWeekdays),
						arrayContains(autopilotSettings.allowedWeekdays, [currentWeekday]),
					),
				),
			)
			.limit(1);

		if (!settings || !settings.enabled) {
			if (process.env.NODE_ENV === "development") {
				logger.info(
					`Autopilot settings not found or disabled for team ID ${payload.teamId}. Continuing in development mode.`,
				);
			} else {
				logger.info(
					`Autopilot settings disabled for team ID ${payload.teamId}. Exiting.`,
				);
				return;
			}
		}

		const sevenDaysAgo = new Date(currentDate);
		sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
		const sevenDaysAgoStr = sevenDaysAgo.toISOString();

		const inactiveTasks = await db
			.select({ id: tasks.id, teamId: tasks.teamId })
			.from(tasks)
			.innerJoin(statuses, eq(statuses.id, tasks.statusId))
			.where(
				and(
					eq(tasks.teamId, payload.teamId),
					not(
						sql`${statuses.type} = ANY(ARRAY['done'::text, 'cancelled'::text])`,
					),
					lte(tasks.updatedAt, sevenDaysAgoStr),
				),
			)
			.limit(10);

		if (inactiveTasks.length === 0) {
			logger.info("No tasks found for suggestions. Exiting.");
			return;
		}

		for (const inactiveTask of inactiveTasks) {
			await createTaskSuggestion({
				taskId: inactiveTask.id,
				teamId: payload.teamId,
				content:
					"This task has been inactive for more than 7 days. Consider following up.",
				payload: {
					type: "comment",
					comment:
						"This task has been inactive for more than 7 days. Consider following up.",
				},
			});
		}
	},
});

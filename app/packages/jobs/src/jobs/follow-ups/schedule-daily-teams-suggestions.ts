import { TZDate } from "@date-fns/tz";
import { autopilotSettings, teams } from "@mimir/db/schema";
import { eq } from "drizzle-orm";
import { defineJob, enqueue, getDb, logger, registerCron } from "../../init";
import { generateTeamSuggestionsJob } from "./generate-team-suggestions-job";

export const scheduleDailyTeamsSuggestions = defineJob({
	id: "schedule-daily-teams-suggestions",
	run: async (_payload: Record<string, unknown>) => {
		const db = getDb();
		const teamsList = await db.select().from(teams);

		for (const team of teamsList) {
			const [settings] = await db
				.select()
				.from(autopilotSettings)
				.where(eq(autopilotSettings.teamId, team.id))
				.limit(1);

			if (!settings || !settings.enabled) {
				logger.info(
					`Autopilot settings disabled for team ID ${team.id}. Exiting.`,
				);
				continue;
			}

			const executionDate = new TZDate(new Date(), team.timezone || "UTC");
			executionDate.setHours(9, 0, 0, 0);

			await enqueue(
				generateTeamSuggestionsJob.id,
				{ teamId: team.id },
				{ delay: executionDate },
			);
		}
	},
});

registerCron("schedule-daily-teams-suggestions", "0 1 */2 * *", () =>
	enqueue(scheduleDailyTeamsSuggestions.id, {}).then(() => {}),
);

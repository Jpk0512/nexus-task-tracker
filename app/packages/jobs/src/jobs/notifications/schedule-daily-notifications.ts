import { TZDate } from "@date-fns/tz";
import { teams, users, usersOnTeams } from "@mimir/db/schema";
import { set } from "date-fns";
import { eq } from "drizzle-orm";
import { defineJob, enqueue, getDb, registerCron } from "../../init";
import { createDigestActivityJob } from "./create-digest-activity";
import { createEODActivityJob } from "./create-eod-activity";
import { createEODTeamSummaryActivityJob } from "./create-eod-team-summary";

export const scheduleDailyNotificationsJob = defineJob({
	id: "schedule-daily-notifications",
	run: async (_payload: Record<string, unknown>) => {
		const db = getDb();

		const usersOnTeamsList = await db
			.select()
			.from(usersOnTeams)
			.innerJoin(users, eq(users.id, usersOnTeams.userId))
			.innerJoin(teams, eq(teams.id, usersOnTeams.teamId));

		for (const userOnTeam of usersOnTeamsList) {
			const date = TZDate.tz(userOnTeam.teams.timezone);
			const digestDate = set(date, { hours: 9, minutes: 0, seconds: 0 });

			if (digestDate > date)
				await enqueue(
					createDigestActivityJob.id,
					{
						userId: userOnTeam.user.id,
						teamId: userOnTeam.users_on_teams.teamId,
						userName: userOnTeam.user.name,
					},
					{ delay: digestDate },
				);

			const eodDate = set(date, { hours: 17, minutes: 0, seconds: 0 });

			if (eodDate > date) {
				await enqueue(
					createEODActivityJob.id,
					{
						userId: userOnTeam.user.id,
						teamId: userOnTeam.users_on_teams.teamId,
						userName: userOnTeam.user.name,
					},
					{ delay: eodDate },
				);
			}
		}

		const teamsList = await db.select().from(teams);
		for (const team of teamsList) {
			const date = TZDate.tz(team.timezone);
			const eodDate = set(date, { hours: 17, minutes: 0, seconds: 0 });

			if (eodDate > date) {
				await enqueue(
					createEODTeamSummaryActivityJob.id,
					{ teamId: team.id },
					{ delay: eodDate },
				);
			}
		}
	},
});

registerCron("schedule-daily-notifications", "0 5 * * *", () =>
	enqueue(scheduleDailyNotificationsJob.id, {}).then(() => {}),
);

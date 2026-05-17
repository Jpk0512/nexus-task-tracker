import { getActivityById } from "@mimir/db/queries/activities";
import { notificationChannels } from "@mimir/db/queries/notification-settings";
import { getUserById } from "@mimir/db/queries/users";
import { sendNotification } from "@mimir/notifications";
import { schemaTask, tags } from "@trigger.dev/sdk";
import z from "zod";

export const sendNotificationJob = schemaTask({
	id: "send-notification",
	schema: z.object({
		activityId: z.string(),
		channel: z.enum(notificationChannels),
	}),
	queue: {
		concurrencyLimit: 5,
	},
	run: async (payload, ctx) => {
		await tags.add(`channel:${payload.channel}`);
		const { activityId } = payload;

		const activity = await getActivityById(activityId);
		if (!activity) throw new Error("Activity not found");
		await tags.add(`type:${activity.type}`);
		if (!activity.userId) throw new Error("Activity has no userId");

		const user = await getUserById(activity.userId);
		if (!user) throw new Error("User not found");

		await sendNotification(activity.type as any, payload.channel, activity, {
			id: activity.userId,
			teamId: activity.teamId,
			email: user.email,
			name: user.name,
			locale: user.locale || "en-US",
		});
	},
});

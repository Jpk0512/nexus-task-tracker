import { getActivityById } from "@mimir/db/queries/activities";
import type { notificationChannels } from "@mimir/db/queries/notification-settings";
import { getUserById } from "@mimir/db/queries/users";
import { sendNotification } from "@mimir/notifications";
import { defineJob } from "../../init";

export const sendNotificationJob = defineJob({
	id: "send-notification",
	run: async (payload: {
		activityId: string;
		channel: (typeof notificationChannels)[number];
	}) => {
		const { activityId } = payload;

		const activity = await getActivityById(activityId);
		if (!activity) throw new Error("Activity not found");
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

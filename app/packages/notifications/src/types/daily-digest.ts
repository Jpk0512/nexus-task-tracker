import type { NotificationHandler } from "@notifications/base";

export const dailyDigest: NotificationHandler = {
	createNotification: (data, user) => {
		return {
			title: "Daily Digest",
			message: data.metadata?.content,
			type: "customer",
		};
	},
	createWhatsappNotification: (data, user) => {
		return {
			message: data.metadata?.content,
			type: "customer",
		};
	},
};

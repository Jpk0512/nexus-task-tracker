export interface NotificationType {
	type: string;
	channels: string[];
	showInSettings: boolean;
	category?: string;
	order?: number;
}

export const allNotificationTypes: NotificationType[] = [
	{
		type: "task_assigned",
		channels: ["mattermost"],
		showInSettings: true,
		category: "tasks",
		order: 2,
	},
	{
		type: "task_column_changed",
		channels: ["mattermost"],
		showInSettings: true,
		category: "tasks",
		order: 3,
	},
	{
		type: "task_comment",
		channels: ["mattermost"],
		showInSettings: true,
		category: "tasks",
		order: 4,
	},
	{
		type: "resume_generated",
		channels: ["mattermost"],
		showInSettings: true,
		category: "resumes",
		order: 1,
	},
	{
		type: "daily_digest",
		channels: ["mattermost", "whatsapp"],
		showInSettings: true,
		category: "resumes",
		order: 2,
	},
	{
		type: "daily_eod",
		channels: ["mattermost", "whatsapp"],
		showInSettings: true,
		category: "resumes",
		order: 3,
	},
	{ type: "mention", channels: ["mattermost"], showInSettings: true, order: 1 },
	{
		type: "follow_up",
		channels: ["mattermost", "whatsapp"],
		showInSettings: true,
		category: "tasks",
		order: 7,
	},
	{
		type: "checklist_item_created",
		channels: ["mattermost"],
		showInSettings: true,
		category: "tasks",
		order: 5,
	},
	{
		type: "checklist_item_completed",
		channels: ["mattermost"],
		showInSettings: true,
		category: "tasks",
		order: 6,
	},
];

export function getAllNotificationTypes(): NotificationType[] {
	return allNotificationTypes;
}

export function getUserSettingsNotificationTypes(): NotificationType[] {
	return allNotificationTypes.filter((t) => t.showInSettings);
}

export function getNotificationTypeByType(
	typeString: string,
): NotificationType | undefined {
	return allNotificationTypes.find((t) => t.type === typeString);
}

export function shouldShowInSettings(typeString: string): boolean {
	return getNotificationTypeByType(typeString)?.showInSettings ?? false;
}

export interface NotificationCategory {
	category: string;
	order: number;
	types: NotificationType[];
}

export function getNotificationTypesByCategory(): NotificationCategory[] {
	const settingsTypes = getUserSettingsNotificationTypes();
	const categoryMap = new Map<string, NotificationCategory>();

	for (const notificationType of settingsTypes) {
		const category = notificationType.category || "other";
		const order = notificationType.order || 999;
		if (!categoryMap.has(category)) {
			categoryMap.set(category, { category, order, types: [] });
		}
		categoryMap.get(category)!.types.push(notificationType);
	}

	return Array.from(categoryMap.values()).sort((a, b) => {
		if (a.order !== b.order) return a.order - b.order;
		return a.category.localeCompare(b.category);
	});
}

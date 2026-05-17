import { protectedProcedure, router } from "@api/trpc/init";
import {
	bulkUpdateNotificationSettings,
	getNotificationSettings,
	getUserNotificationPreferences,
	upsertNotificationSetting,
} from "@mimir/db/queries/notification-settings";
import {
	bulkUpdateNotificationSettingsSchema,
	getNotificationSettingsSchema,
	updateNotificationSettingSchema,
} from "../../schemas/notification-settings";

export const notificationSettingsRouter = router({
	get: protectedProcedure
		.input(getNotificationSettingsSchema.optional())
		.query(async ({ ctx, input = {} }) => {
			return getNotificationSettings({
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
				...input,
			});
		}),

	// Get all notification types with their current settings for the user
	getAll: protectedProcedure.query(async ({ ctx }) => {
		return getUserNotificationPreferences(ctx.user.id, ctx.user.teamId!);
	}),

	// Update a single notification setting
	update: protectedProcedure
		.input(updateNotificationSettingSchema)
		.mutation(async ({ ctx, input }) => {
			return upsertNotificationSetting({
				userId: ctx.user.id,
				teamId: ctx.user.teamId!,
				...input,
			});
		}),

	// Bulk update multiple notification settings
	bulkUpdate: protectedProcedure
		.input(bulkUpdateNotificationSettingsSchema)
		.mutation(async ({ ctx, input }) => {
			return bulkUpdateNotificationSettings(
				ctx.user.id,
				ctx.user.teamId!,
				input.updates,
			);
		}),
});

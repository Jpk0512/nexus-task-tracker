import {
	createMilestoneSchema,
	getMilestonesSchema,
	updateMilestoneSchema,
} from "@api/schemas/milestones";
import { protectedProcedure, router } from "@api/trpc/init";
import {
	createMilestone,
	deleteMilestone,
	getMilestoneById,
	getMilestones,
	updateMilestone,
} from "@mimir/db/queries/milestones";
import z from "zod";

export const milestonesRouter = router({
	get: protectedProcedure
		.input(getMilestonesSchema.optional())
		.query(async ({ ctx, input }) => {
			return getMilestones({
				...input,
				teamId: ctx.user.teamId,
			});
		}),

	create: protectedProcedure
		.input(createMilestoneSchema)
		.mutation(async ({ ctx, input }) => {
			return createMilestone({
				...input,
				teamId: ctx.user.teamId,
			});
		}),

	getById: protectedProcedure
		.input(z.object({ id: z.string() }))
		.query(async ({ ctx, input }) => {
			return getMilestoneById({
				milestoneId: input.id,
				teamId: ctx.user.teamId,
			});
		}),

	update: protectedProcedure
		.input(updateMilestoneSchema)
		.mutation(async ({ ctx, input }) => {
			return updateMilestone({
				...input,
				teamId: ctx.user.teamId,
			});
		}),

	delete: protectedProcedure
		.input(z.object({ id: z.string() }))
		.mutation(async ({ ctx, input }) => {
			return deleteMilestone({
				milestoneId: input.id,
				teamId: ctx.user.teamId,
			});
		}),
});

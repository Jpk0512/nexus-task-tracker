import {
	createMilestoneSchema,
	getMilestonesSchema,
	updateMilestoneSchema,
} from "@api/schemas/milestones";
import { protectedProcedure, router } from "@api/trpc/init";
import { db } from "@nexus-app/db/client";
import {
	createMilestone,
	deleteMilestone,
	getMilestoneById,
	getMilestones,
	updateMilestone,
} from "@nexus-app/db/queries/milestones";
import { TRPCError } from "@trpc/server";
import { and, eq } from "drizzle-orm";
import { pgTable, text } from "drizzle-orm/pg-core";
import z from "zod";

// iter-10 Round F: local ref for the owner-agent FK.
const milestonesRef = pgTable("milestones", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
	ownerAgentId: text("owner_agent_id"),
});

const agentsRef = pgTable("agents", {
	id: text("id").primaryKey(),
	teamId: text("team_id").notNull(),
});

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

	// iter-10 Round F: assign / clear the milestone's owner agent.
	// Canonical contract per codex amendment #2 (FK over watcher join).
	setOwnerAgent: protectedProcedure
		.input(
			z.object({
				milestoneId: z.string(),
				agentId: z.string().nullable(),
			}),
		)
		.mutation(async ({ ctx, input }) => {
			const [authz] = await db
				.select({ id: milestonesRef.id })
				.from(milestonesRef)
				.where(
					and(
						eq(milestonesRef.id, input.milestoneId),
						eq(milestonesRef.teamId, ctx.user.teamId!),
					),
				)
				.limit(1);
			if (!authz) throw new TRPCError({ code: "NOT_FOUND" });

			if (input.agentId) {
				// Confirm the agent belongs to the same team.
				const [agent] = await db
					.select({ id: agentsRef.id })
					.from(agentsRef)
					.where(
						and(
							eq(agentsRef.id, input.agentId),
							eq(agentsRef.teamId, ctx.user.teamId!),
						),
					)
					.limit(1);
				if (!agent)
					throw new TRPCError({
						code: "NOT_FOUND",
						message: "agent not found in team",
					});
			}

			await db
				.update(milestonesRef)
				.set({ ownerAgentId: input.agentId })
				.where(eq(milestonesRef.id, input.milestoneId));
			return { ok: true };
		}),
});

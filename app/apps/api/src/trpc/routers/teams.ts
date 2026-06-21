import { resend } from "@api/lib/resend";
import {
	acceptTeamInviteSchema,
	createTeamInviteSchema,
	createTeamSchema,
	deleteTeamInviteSchema,
	getInvitesByEmailSchema,
	getMemberByIdSchema,
	getMembersSchema,
	getTeamInviteByIdSchema,
	getTeamInvitesSchema,
	removeMemberSchema,
	transferOwnershipSchema,
	updateMemberSchema,
	updateTeamSchema,
} from "@api/schemas/teams";
import { protectedProcedure, router } from "@api/trpc/init";
import {
	changeOwner,
	checkSlugExists,
	createTeam,
	deleteTeam,
	getMemberById,
	getMembers,
	getTeamById,
	leaveTeam,
	updateMember,
	updateTeam,
} from "@nexus-app/db/queries/teams";
import {
	acceptTeamInvite,
	createTeamInvite,
	deleteTeamInvite,
	getTeamInviteById,
	getTeamInvites,
	getTeamInvitesByEmail,
} from "@nexus-app/db/queries/user-invites";
import { getAvailableTeams } from "@nexus-app/db/queries/users";
import { InviteEmail } from "@nexus-app/email/emails/invite";
import z from "zod";

export const teamsRouter = router({
	getAvailable: protectedProcedure
		.meta({
			team: false,
		})
		.query(async ({ ctx }) => {
			const teams = await getAvailableTeams(ctx.user.id);
			return teams;
		}),

	create: protectedProcedure
		.input(createTeamSchema)
		.meta({
			team: false,
		})
		.mutation(async ({ ctx, input }) => {
			const team = await createTeam({ ...input, userId: ctx.user.id });
			return team;
		}),

	getCurrent: protectedProcedure.query(async ({ ctx }) => {
		const team = await getTeamById(ctx.user.teamId!);
		return team;
	}),

	update: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.input(updateTeamSchema)
		.mutation(async ({ ctx, input }) => {
			const team = await updateTeam({
				...input,
				id: ctx.user.teamId!,
			});
			return team;
		}),

	getMembers: protectedProcedure
		.input(getMembersSchema.optional())
		.query(async ({ ctx, input }) => {
			const members = await getMembers({
				...input,
				teamId: ctx.user.teamId!,
			});
			return members;
		}),

	acceptInvite: protectedProcedure
		.meta({
			team: false,
		})
		.input(acceptTeamInviteSchema)
		.mutation(async ({ ctx, input }) => {
			const currentInvite = await getTeamInviteById(input.inviteId);
			if (currentInvite.email !== ctx.user.email) {
				throw new Error("This invite is not for your email");
			}

			const invite = await acceptTeamInvite({
				userId: ctx.user.id,
				userInviteId: input.inviteId,
			});

			return invite;
		}),

	getInviteById: protectedProcedure
		.meta({
			team: false,
		})
		.input(getTeamInviteByIdSchema)
		.query(async ({ input }) => {
			return getTeamInviteById(input.inviteId);
		}),

	getInvites: protectedProcedure
		.input(getTeamInvitesSchema.optional())
		.query(async ({ ctx, input }) => {
			return getTeamInvites({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),

	getInvitesByEmail: protectedProcedure
		.meta({
			team: false,
		})
		.input(getInvitesByEmailSchema)
		.query(async ({ ctx }) => {
			return getTeamInvitesByEmail({
				email: ctx.user.email,
			});
		}),

	invite: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.input(createTeamInviteSchema)
		.mutation(async ({ ctx, input }) => {
			const invite = await createTeamInvite({
				email: input.email,
				teamId: ctx.user.teamId!,
				invitedBy: ctx.user.id,
			});

			await resend.emails.send({
				from: "Mimir <mimir@grupo-titanio.com>",
				to: invite.email!,
				subject: "You're invited to join a team on Mimir",
				react: InviteEmail({
					inviteId: invite.id!,
					teamId: invite.teamId!,
					teamName: invite.team.name,
					email: invite.email!,
				}),
			});
			console.log("Invite email sent to", invite.email);

			return invite;
		}),

	leave: protectedProcedure.mutation(async ({ ctx }) => {
		const membership = await leaveTeam(ctx.user.id, ctx.user.teamId!);
		return membership;
	}),

	updateMember: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.input(updateMemberSchema)
		.mutation(async ({ ctx, input }) => {
			return updateMember({
				...input,
				teamId: ctx.user.teamId!,
			});
		}),

	getMemberById: protectedProcedure
		.input(getMemberByIdSchema)
		.query(async ({ ctx, input }) => {
			return await getMemberById({
				userId: input.userId,
				teamId: ctx.user.teamId!,
			});
		}),

	removeMember: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.input(removeMemberSchema)
		.mutation(async ({ ctx, input }) => {
			if (input.userId === ctx.user.id) {
				throw new Error("You cannot remove yourself");
			}

			const membership = await leaveTeam(input.userId, ctx.user.teamId!);
			return membership;
		}),

	transferOwnership: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.input(transferOwnershipSchema)
		.mutation(async ({ ctx, input }) => {
			if (input.userId === ctx.user.id) {
				throw new Error("You cannot transfer ownership to yourself");
			}

			return await changeOwner({
				teamId: ctx.user.teamId!,
				userId: input.userId,
			});
		}),

	deleteInvite: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.input(deleteTeamInviteSchema)
		.mutation(async ({ ctx, input }) => {
			return deleteTeamInvite({
				inviteId: input.inviteId,
				teamId: ctx.user.teamId!,
			});
		}),

	delete: protectedProcedure
		.meta({ scopes: ["team:write"] })
		.mutation(async ({ ctx }) => {
			const team = await deleteTeam(ctx.user.teamId!);
			return team;
		}),

	checkSlug: protectedProcedure
		.meta({
			team: false,
		})
		.input(z.object({ slug: z.string() }))
		.query(async ({ input }) => {
			const available = await checkSlugExists(input.slug);
			if (!available) {
				const alternative = `${input.slug}-${Math.floor(Math.random() * 1000)}`;
				return { available: false, alternative };
			}
			return { available: true };
		}),
});

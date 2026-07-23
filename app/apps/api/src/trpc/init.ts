import type { Scope } from "@api/lib/scopes";
import { initTRPC, TRPCError } from "@trpc/server";
import type { Context } from "../lib/context";
import { checkTrpcMutationRateLimit } from "./rate-limit";

type Meta = {
	/**
	 * require user to belong to a team
	 * @default true
	 */
	team?: boolean;

	/**
	 * require scopes for this procedure
	 */
	scopes?: Scope[];
};
export const t = initTRPC.context<Context>().meta<Meta>().create({
	// transformer: superjson,
});

export const router = t.router;

export const publicProcedure = t.procedure;

export const protectedProcedure = t.procedure
	.meta({ team: true })
	.use(({ ctx, next, meta }) => {
		if (!ctx.session) {
			throw new TRPCError({
				code: "UNAUTHORIZED",
				message: "Authentication required",
				cause: "No session",
			});
		}

		if (!ctx.user.teamId && meta?.team) {
			throw new TRPCError({
				code: "FORBIDDEN",
				message: "User does not belong to a team",
				cause: "No team",
			});
		}

		if (meta?.scopes) {
			const hasRequiredScopes = meta.scopes.every((scope) =>
				ctx.user.scopes.includes(scope),
			);
			if (!hasRequiredScopes) {
				throw new TRPCError({
					code: "FORBIDDEN",
					message: "Insufficient permissions",
					cause: "Insufficient permissions",
				});
			}
		}

		return next({
			ctx: {
				...ctx,
				session: ctx.session,
			},
		});
	})
	.use(async ({ next }) => {
		const result = await next();
		if (!result.ok) {
			// biome-ignore lint/suspicious/noExplicitAny: tRPC result error is untyped on failure path
			console.error((result as any).error);
		}
		return result;
	});

/**
 * `protectedProcedure` + a per-user rate limit. Opt into this for
 * mutation-heavy procedures that have no other throttling in their chain
 * (tRPC bypasses the REST layer's `hono-rate-limiter` entirely — see
 * `trpc/rate-limit.ts`). Not the default on `protectedProcedure` itself so
 * unrelated routers/reads are unaffected.
 */
export const rateLimitedProcedure = protectedProcedure.use(
	async ({ ctx, next }) => {
		const result = await checkTrpcMutationRateLimit(ctx.user.id);
		if (!result.success) {
			throw new TRPCError({
				code: "TOO_MANY_REQUESTS",
				message: `Rate limit exceeded. Try again in ${Math.ceil(
					(result.reset - Date.now()) / 1000,
				)} seconds.`,
			});
		}
		return next();
	},
);

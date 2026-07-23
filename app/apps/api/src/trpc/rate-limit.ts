import { buildLimiter, type RateLimitResult } from "../ai/mcp/rate-limit";

/**
 * tRPC is mounted directly via `trpcServer()` (see `apps/api/src/index.ts`),
 * bypassing `rest/middleware`'s `protectedMiddleware` (and its HTTP-layer
 * `hono-rate-limiter`) entirely — so `protectedProcedure` has no rate
 * limiting anywhere in its chain. This guards mutation-heavy procedures
 * (vault secrets, API keys) that each perform an encrypt + DB write, mirroring
 * the bar already set for the MCP gateway's own write limiter
 * (`checkMcpWriteRateLimit`, 20/min).
 */
const trpcMutationRateLimiter = buildLimiter(20, "trpc-mutation-ratelimit");

export async function checkTrpcMutationRateLimit(
	userId: string,
): Promise<RateLimitResult> {
	const result = await trpcMutationRateLimiter.limit(userId);
	return {
		success: result.success,
		limit: result.limit,
		remaining: result.remaining,
		reset: result.reset,
	};
}

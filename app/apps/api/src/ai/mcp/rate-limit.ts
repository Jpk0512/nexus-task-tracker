import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

const LOCAL_DEV = process.env.NEXUS_LOCAL_DEV === "1";
const UPSTASH_CONFIGURED =
	Boolean(process.env.UPSTASH_REDIS_REST_URL) &&
	Boolean(process.env.UPSTASH_REDIS_REST_TOKEN);

const getRedis = () => {
	const redisUrl = process.env.UPSTASH_REDIS_REST_URL;
	const redisToken = process.env.UPSTASH_REDIS_REST_TOKEN;
	if (!redisUrl || !redisToken) {
		throw new Error(
			"UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN are required in production",
		);
	}
	return new Redis({ url: redisUrl, token: redisToken });
};

/**
 * In-process sliding-window rate limiter used ONLY when NEXUS_LOCAL_DEV=1
 * AND Upstash is not configured. Enforces the same numeric limits as
 * production but keeps state in a Map so no Redis credentials are needed.
 *
 * WARNING: this limiter is per-process and resets on restart. It MUST NOT
 * be used in production (enforced below via the env guard).
 */
function makeLocalLimiter(maxPerMinute: number): Ratelimit {
	const windows = new Map<string, number[]>();

	const stub = {
		limit: async (id: string) => {
			const now = Date.now();
			const windowStart = now - 60_000;
			const timestamps = (windows.get(id) ?? []).filter((t) => t > windowStart);
			const allowed = timestamps.length < maxPerMinute;
			if (allowed) {
				timestamps.push(now);
			}
			windows.set(id, timestamps);
			const remaining = Math.max(0, maxPerMinute - timestamps.length);
			return {
				success: allowed,
				limit: maxPerMinute,
				remaining,
				reset: now + 60_000,
				pending: Promise.resolve(),
			};
		},
	};
	return stub as unknown as Ratelimit;
}

function buildLimiter(maxPerMinute: number, prefix: string): Ratelimit {
	if (LOCAL_DEV && !UPSTASH_CONFIGURED) {
		console.warn(
			"[rate-limit] NEXUS_LOCAL_DEV=1 and Upstash not configured — " +
				`using in-process limiter (${maxPerMinute}/min, prefix=${prefix}). ` +
				"This MUST NOT reach production.",
		);
		return makeLocalLimiter(maxPerMinute);
	}

	return new Ratelimit({
		redis: getRedis(),
		limiter: Ratelimit.slidingWindow(maxPerMinute, "1 m"),
		analytics: true,
		prefix,
	});
}

/**
 * MCP Rate Limiter
 *
 * Separate rate limiting for MCP endpoints to prevent abuse while
 * allowing legitimate LLM usage patterns.
 */

// Rate limit: 60 requests per minute per client
const mcpRateLimiter = buildLimiter(60, "mcp-ratelimit");

// Stricter rate limit for write operations: 20 per minute
const mcpWriteRateLimiter = buildLimiter(20, "mcp-write-ratelimit");

export interface RateLimitResult {
	success: boolean;
	limit: number;
	remaining: number;
	reset: number;
}

/**
 * Check rate limit for MCP read operations
 */
export async function checkMcpRateLimit(
	clientId: string,
): Promise<RateLimitResult> {
	const result = await mcpRateLimiter.limit(clientId);
	return {
		success: result.success,
		limit: result.limit,
		remaining: result.remaining,
		reset: result.reset,
	};
}

/**
 * Check rate limit for MCP write operations (more restrictive)
 */
export async function checkMcpWriteRateLimit(
	clientId: string,
): Promise<RateLimitResult> {
	const result = await mcpWriteRateLimiter.limit(clientId);
	return {
		success: result.success,
		limit: result.limit,
		remaining: result.remaining,
		reset: result.reset,
	};
}

/**
 * Determine if operation is a write operation based on tool name
 */
export function isWriteOperation(toolName: string): boolean {
	const writeOperations = [
		"mimrai_create_task",
		"mimrai_update_task",
		"mimrai_delete_task",
	];
	return writeOperations.includes(toolName);
}

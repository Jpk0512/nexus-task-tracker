import { createHash, timingSafeEqual } from "node:crypto";
import { auth } from "@api/lib/auth";
import { getUserById } from "@nexus-app/db/queries/users";
import type { Session } from "better-auth";
import type { MiddlewareHandler } from "hono";
import { HTTPException } from "hono/http-exception";

const LOCAL_DEV_USER_ID = "local-dev-user";

function verifyBearerToken(incoming: string): boolean {
	const expected = process.env.NEXUS_API_TOKEN;
	if (!expected) return false;
	// Hash both sides to fixed-length buffers so timingSafeEqual always compares same-length values
	const a = createHash("sha256").update(incoming).digest();
	const b = createHash("sha256").update(expected).digest();
	return timingSafeEqual(a, b);
}

export const withAuth: MiddlewareHandler = async (c, next) => {
	const authSession = await auth.api.getSession({
		headers: c.req.raw.headers,
	});

	// If we have a valid session, get the user and set in context
	if (authSession) {
		// Get user from database to get team info
		const user = await getUserById(authSession.user.id);

		if (!user) {
			throw new HTTPException(401, { message: "User not found" });
		}

		const session: Session = {
			...authSession.session,
		};

		c.set("session", session);
		c.set("teamId", user.teamId);
		c.set("userId", user.id);

		await next();
		return;
	}

	// Static API-token auth: Authorization: Bearer ${NEXUS_API_TOKEN}
	const authHeader =
		c.req.header("Authorization") ?? c.req.header("authorization");
	if (authHeader?.startsWith("Bearer ")) {
		const incomingToken = authHeader.slice("Bearer ".length);
		// timingSafeEqual against NEXUS_API_TOKEN; throws 401 on mismatch
		if (!verifyBearerToken(incomingToken))
			throw new HTTPException(401, { message: "Invalid NEXUS_API_TOKEN" });

		const user = await getUserById(LOCAL_DEV_USER_ID);
		if (!user) {
			throw new HTTPException(401, {
				message:
					"local-owner user missing — run packages/db/src/seed-local-dev.ts",
			});
		}

		const now = new Date();
		const tokenSession: Session = {
			id: "api-token-session",
			token: incomingToken,
			userId: user.id,
			expiresAt: new Date(now.getTime() + 365 * 24 * 60 * 60 * 1000),
			createdAt: now,
			updatedAt: now,
			ipAddress: null,
			userAgent: null,
		} as Session;

		c.set("session", tokenSession);
		c.set("teamId", user.teamId);
		c.set("userId", user.id);

		await next();
		return;
	}

	throw new HTTPException(401, { message: "Invalid or expired token" });
};

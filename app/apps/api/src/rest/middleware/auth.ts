import { auth } from "@api/lib/auth";
import { getUserById } from "@mimir/db/queries/users";
import type { Session } from "better-auth";
import type { MiddlewareHandler } from "hono";
import { HTTPException } from "hono/http-exception";

const LOCAL_DEV_USER_ID = "local-dev-user";

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
		// Grant all scopes for authenticated users via Supabase
		// c.set("scopes", expandScopes(["apis.all"]));

		await next();
		return;
	}

	// Local-dev fallback: inject seeded user so REST routes work without auth.
	if (process.env.NEXUS_LOCAL_DEV === "1") {
		const user = await getUserById(LOCAL_DEV_USER_ID);
		if (!user) {
			throw new HTTPException(401, {
				message:
					"local-dev seed user missing — run packages/db/src/seed-local-dev.ts",
			});
		}
		const now = new Date();
		const localSession: Session = {
			id: "local-dev-session",
			token: "local-dev-token",
			userId: user.id,
			expiresAt: new Date(now.getTime() + 365 * 24 * 60 * 60 * 1000),
			createdAt: now,
			updatedAt: now,
			ipAddress: null,
			userAgent: null,
		} as Session;

		c.set("session", localSession);
		c.set("teamId", user.teamId);
		c.set("userId", user.id);

		await next();
		return;
	}

	throw new HTTPException(401, { message: "Invalid or expired token" });
};

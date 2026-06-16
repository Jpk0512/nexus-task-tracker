import { teamCache } from "@mimir/cache/teams-cache";
import { userCache } from "@mimir/cache/users-cache";
import {
	getAvailableTeams,
	getUserById,
	switchTeam,
} from "@mimir/db/queries/users";
import type { Context as HonoContext } from "hono";
import { auth } from "./auth";
import { roleScopes } from "./scopes";

export type CreateContextOptions = {
	context: HonoContext;
};

// Local-dev seed identity — must match packages/db/src/seed-local-dev.ts and
// apps/dashboard/src/lib/get-session.ts.
const LOCAL_DEV_USER_ID = "local-dev-user";

export async function createContext({ context }: CreateContextOptions) {
	const session = await auth.api.getSession({
		headers: context.req.raw.headers,
	});

	const userId = session?.user?.id ?? null;
	const LOCAL_DEV = process.env.NEXUS_LOCAL_DEV === "1";

	if (!userId) {
		if (!LOCAL_DEV) {
			// @ts-expect-error
			return { session: null };
		}
		// fall through with the seeded user id
	}

	const effectiveUserId = userId ?? LOCAL_DEV_USER_ID;

	let user: Awaited<ReturnType<typeof getUserById>> =
		await userCache.get(effectiveUserId);
	if (!user) {
		user = await getUserById(effectiveUserId);
		if (user) userCache.set(effectiveUserId, user);
	}
	if (!user) {
		if (LOCAL_DEV) {
			throw new Error(
				"local-dev seed user missing — run packages/db/src/seed-local-dev.ts",
			);
		}
		// @ts-expect-error
		return { session: null };
	}

	let currentTeam:
		| Awaited<ReturnType<typeof getAvailableTeams>>[number]
		| undefined;

	if (!user.teamId || !user.teamSlug) {
		const teams = await getAvailableTeams(user.id);
		if (teams.length > 0) {
			user.teamId = teams[0].id;
			user.teamSlug = teams[0].slug;
			await switchTeam(user.id, { teamId: teams[0].id });
			currentTeam = teams[0];
		}
	} else {
		const cachedTeam = await teamCache.get(`${user.id}:${user.teamId}`);
		if (cachedTeam) {
			currentTeam = cachedTeam;
		} else {
			const teams = await getAvailableTeams(user.id);
			currentTeam = teams.find((t) => t.id === user.teamId);

			if (!currentTeam) throw new Error("User's current team is not valid");
			teamCache.set(`${user.id}:${user.teamId}`, currentTeam);
		}
	}

	const role = currentTeam?.role;
	const scopes = role ? roleScopes[role] : [];

	const effectiveSession = session ?? {
		user: {
			id: user.id,
			email: user.email,
			name: user.name,
			emailVerified: user.emailVerified,
			image: user.image,
			createdAt: new Date(),
			updatedAt: new Date(),
		},
		session: {
			id: "local-dev-session",
			token: "local-dev-token",
			userId: user.id,
			expiresAt: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000),
			createdAt: new Date(),
			updatedAt: new Date(),
		},
	};

	return {
		session: effectiveSession as any,
		user: {
			...user,
			scopes,
		},
		team: currentTeam ?? null,
	};
}

export type Context = Awaited<ReturnType<typeof createContext>>;

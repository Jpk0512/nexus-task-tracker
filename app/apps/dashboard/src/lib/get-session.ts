import { headers } from "next/headers";
import { authClient } from "./auth-client";

// Local-dev seed identity. Must match packages/db/src/seed-local-dev.ts and
// apps/api/src/lib/context.ts so SSR-fetched and api-supplied user objects
// agree on IDs.
const LOCAL_DEV_USER = {
	id: "local-dev-user",
	email: "dev@mimrai.local",
	name: "Local Dev",
	emailVerified: true,
	image: null,
	teamId: "local-dev-team",
	teamSlug: "local-dev",
	locale: "en-US",
	isMentionable: true,
	color: "#888888",
	isSystemUser: false,
	dateFormat: "MM/dd/yyyy",
};

const LOCAL_DEV_SESSION = {
	id: "local-dev-session",
	token: "local-dev-token",
	userId: LOCAL_DEV_USER.id,
	expiresAt: new Date(Date.now() + 365 * 24 * 60 * 60 * 1000),
};

export const getSession = async (
	params: { cache?: RequestCache } = {
		cache: "default",
	},
) => {
	if (process.env.NEXUS_LOCAL_DEV === "1") {
		return {
			user: { ...LOCAL_DEV_USER },
			session: { ...LOCAL_DEV_SESSION },
			teamId: LOCAL_DEV_USER.teamId,
			teamSlug: LOCAL_DEV_USER.teamSlug,
		};
	}

	const { cache } = params;

	const { data: session } = await authClient.getSession({
		fetchOptions: {
			headers: {
				cookie: (await headers()).get("cookie") || "",
			},
			credentials: "include",
			cache,
		},
	});

	const teamId = (session?.user as any)?.teamId as string | undefined;
	const teamSlug = (session?.user as any)?.teamSlug as string | undefined;

	return {
		...session,
		user: {
			...session?.user,
			teamId: teamId || null,
			teamSlug: teamSlug || null,
		},
	};
};

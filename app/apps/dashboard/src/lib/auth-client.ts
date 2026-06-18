import type { auth } from "@api/lib/auth";
import { apiKeyClient, customSessionClient } from "better-auth/client/plugins";
import { createAuthClient } from "better-auth/react";

// Local-dev: SSR runs inside the dashboard container and can't reach the api
// via the host port (localhost in container is the container itself). When
// NEXUS_LOCAL_DEV=1, fall back to NEXUS_SSR_SERVER_URL on the server side
// (typically http://api:3003 via the docker network). In prod (no flag),
// behavior is byte-identical to upstream.
const baseURL =
	typeof window === "undefined" && process.env.NEXUS_LOCAL_DEV === "1"
		? process.env.NEXUS_SSR_SERVER_URL || process.env.NEXT_PUBLIC_SERVER_URL
		: process.env.NEXT_PUBLIC_SERVER_URL;

export const authClient = createAuthClient({
	baseURL,
	fetchOptions: {
		credentials: "include",
	},
	plugins: [customSessionClient<typeof auth>(), apiKeyClient()],
});

export const useSession = authClient.useSession;

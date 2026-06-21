import type { AppRouter } from "@nexus-app/trpc";
import { QueryCache, QueryClient } from "@tanstack/react-query";
import {
	createTRPCClient,
	httpBatchLink,
	httpBatchStreamLink,
	httpSubscriptionLink,
	loggerLink,
	splitLink,
} from "@trpc/client";
import { createTRPCOptionsProxy } from "@trpc/tanstack-react-query";

// Local-dev URL split: SSR inside the docker container reaches the api via
// NEXUS_SSR_SERVER_URL (docker DNS), browser reaches it via the public
// NEXT_PUBLIC_SERVER_URL. Upstream behavior unchanged when NEXUS_LOCAL_DEV
// is not set.
const ssrUrl =
	process.env.NEXUS_LOCAL_DEV === "1"
		? process.env.NEXUS_SSR_SERVER_URL || process.env.NEXT_PUBLIC_SERVER_URL
		: process.env.NEXT_PUBLIC_SERVER_URL;
const browserUrl = process.env.NEXT_PUBLIC_SERVER_URL;

export const queryClient = new QueryClient({
	defaultOptions: {
		queries: {
			staleTime: 60 * 1000, // 1 minute
			gcTime: 1000 * 60 * 60 * 24, // 24 hours
		},
	},
	queryCache: new QueryCache({
		onError: (error) => {
			const safeError = error as { data?: { httpStatus?: number } };
			const httpStatus = safeError.data?.httpStatus;
			switch (httpStatus) {
				case 401:
					location.href = "/sign-in";
					break;
			}
		},
	}),
});

export const trpcClient = createTRPCClient<AppRouter>({
	links: [
		splitLink({
			condition: (op) => op.type === "subscription",
			true: httpSubscriptionLink({
				url: `${browserUrl}/trpc`,
				eventSourceOptions() {
					return {
						withCredentials: true,
					};
				},
			}),
			false: splitLink({
				condition: () => typeof window === "undefined",
				// Server-side
				true: httpBatchLink({
					url: `${ssrUrl}/trpc`,
					async fetch(url, options) {
						const headersImport = await import("next/headers");
						const cookieHeader = (await headersImport.headers()).get("cookie");

						// Server-side, embed the request headers
						const response = await fetch(url, {
							...options,
							headers: {
								...options?.headers,
								cookie: cookieHeader || "",
							},
							credentials: "include",
						});

						if (!response.ok) {
							const errorJson = await response.clone().json();
							console.error("tRPC Error:", errorJson);
						}

						return response;
					},
				}),
				// Client-side
				false: httpBatchStreamLink({
					url: `${browserUrl}/trpc`,
					fetch(url, options) {
						return fetch(url, {
							...options,
							credentials: "include",
						});
					},
				}),
			}),
		}),
		loggerLink({
			enabled: (opts) =>
				process.env.NODE_ENV === "development" ||
				(opts.direction === "down" && opts.result instanceof Error),
		}),
	],
});

export const trpc = createTRPCOptionsProxy<AppRouter>({
	client: trpcClient,
	queryClient: queryClient,
});

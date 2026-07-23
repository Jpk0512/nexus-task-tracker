import { createMCPClient, type MCPClient } from "@ai-sdk/mcp";
import {
	getMcpServers,
	getMcpServerUserTokens,
} from "@nexus-app/db/queries/mcp-servers";
import type { McpServerConfig } from "@nexus-app/db/schema";
import type { Tool } from "ai";
import { resolveValidMcpToken } from "../../utils/mcp-token-refresh";

export interface TeamMcpServerRecord {
	id: string;
	name: string;
	transport: string;
	config: McpServerConfig;
}

export interface ConnectedMcpUpstream {
	server: TeamMcpServerRecord;
	client: MCPClient;
	tools: Record<string, Tool>;
}

/**
 * Connect to a team's configured MCP servers, resolving per-user OAuth
 * tokens (with automatic refresh via `resolveValidMcpToken`) where a token
 * has been linked for the given user.
 *
 * This is the single home of the "createMCPClient + resolveValidMcpToken +
 * McpServerConfig" client mechanics — shared by:
 *  - `ai/tools/tool-registry.ts` (agent tool aggregation, `mcp:<name>`
 *    toolbox namespacing, used for chat/agent runs)
 *  - `ai/mcp/tools/mcp-proxy-tools.ts` (the outward MCP gateway,
 *    `<slug>__<tool>` namespacing, used by `rest/routers/mcp.ts`)
 *
 * A server that fails to connect or list tools (down, unauthenticated,
 * malformed config, refresh failure) is SKIPPED — logged server-side via the
 * returned `errors` map, never thrown, so one bad upstream never fails the
 * whole caller.
 */
export async function connectTeamMcpServers({
	teamId,
	userId,
	serverIds,
}: {
	teamId: string;
	userId?: string;
	/**
	 * Restrict connection to this subset of `mcp_servers.id`s (per-API-key
	 * scoping). Omit (or pass `undefined`) to connect every active server
	 * configured for the team.
	 */
	serverIds?: string[];
}): Promise<{
	connections: ConnectedMcpUpstream[];
	errors: Record<string, Error>;
}> {
	const errors: Record<string, Error> = {};

	const allActiveServers = await getMcpServers({ teamId, activeOnly: true });
	const servers = serverIds
		? allActiveServers.filter((s) => serverIds.includes(s.id))
		: allActiveServers;

	console.log(
		`Found ${servers.length} MCP server(s) to connect for team ${teamId}`,
	);

	const userTokens =
		userId && servers.length > 0
			? await getMcpServerUserTokens({
					userId,
					mcpServerIds: servers.map((s) => s.id),
				})
			: {};

	const connections: ConnectedMcpUpstream[] = [];

	await Promise.all(
		servers.map(async (server) => {
			try {
				const config = server.config as McpServerConfig;
				const tokenInfo = userTokens[server.id];

				let accessToken: string | null = null;
				if (tokenInfo && userId) {
					accessToken = await resolveValidMcpToken({
						userId,
						mcpServerId: server.id,
						serverConfig: config,
						tokenInfo,
					});
				}

				const headers: Record<string, string> = {
					...config.headers,
					...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
				};

				const client = await createMCPClient({
					transport: {
						type: server.transport as "http" | "sse",
						url: config.url,
						headers: Object.keys(headers).length > 0 ? headers : undefined,
					},
					name: `mcp-${server.name}`,
				});

				const tools = await client.tools();

				connections.push({
					server: {
						id: server.id,
						name: server.name,
						transport: server.transport,
						config,
					},
					client,
					tools,
				});
			} catch (error) {
				errors[server.name] = error as Error;
				console.error(
					`Failed to connect MCP server "${server.name}" (${server.id}):`,
					error,
				);
			}
		}),
	);

	return { connections, errors };
}

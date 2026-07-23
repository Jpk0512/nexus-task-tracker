import { auth } from "@api/lib/auth";
import { StreamableHTTPTransport } from "@hono/mcp";
import { OpenAPIHono } from "@hono/zod-openapi";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { db } from "@nexus-app/db/client";
import { users } from "@nexus-app/db/schema";
import { eq } from "drizzle-orm";
import { checkMcpRateLimit } from "../../ai/mcp/rate-limit";
import { createMcpServer } from "../../ai/mcp/server";
import {
	type McpContext,
	NATIVE_MCP_TOOL_NAMES,
	registerTaskTools,
} from "../../ai/mcp/tools/build-mcp";
import { registerProxiedMcpTools } from "../../ai/mcp/tools/mcp-proxy-tools";
import type { Context } from "../types";

/**
 * Per-API-key MCP server scoping. A key's `metadata.mcpServers` field is
 * either `"all"` or an explicit allowlist of `mcp_servers.id`s. A key with
 * NO `mcpServers` field in its metadata AT ALL defaults to `"all"` of the
 * team's ENABLED servers — this is a deliberate permissive default (same
 * posture as native tools, which every key already sees unconditionally)
 * and is called out here for the security review to rule on explicitly.
 */
type ApiKeyMetadata = {
	teamId?: string;
	mcpServers?: "all" | string[];
};

function resolveMcpServerScope(metadata: ApiKeyMetadata): "all" | string[] {
	if (metadata.mcpServers === undefined) return "all";
	if (metadata.mcpServers === "all") return "all";
	if (Array.isArray(metadata.mcpServers)) {
		return metadata.mcpServers.filter(
			(id): id is string => typeof id === "string",
		);
	}
	return "all";
}

const mcpRouter = new OpenAPIHono<Context>();

/**
 * Verify API key and extract user context
 */
async function verifyApiKey(apiKeyHeader: string | undefined): Promise<{
	userId: string;
	teamId: string;
	permissions: Record<string, string[]>;
	keyId: string;
	mcpServerScope: "all" | string[];
} | null> {
	if (!apiKeyHeader) {
		return null;
	}

	try {
		console.log("Verifying API key for MCP request");

		// Verify the API key using Better Auth
		// @ts-expect-error - types
		const result = await auth.api.verifyApiKey({
			body: {
				key: apiKeyHeader,
			},
		});

		if (!result.valid || !result.key) {
			console.log("API key verification failed:", result.error);
			return null;
		}

		const { key } = result;
		const userId = key.userId;

		const metadata = (key.metadata ?? {}) as ApiKeyMetadata;

		// Get the team ID from the API key metadata or user's default team
		let teamId = metadata.teamId;

		if (!teamId) {
			// Fall back to user's active team
			const user = await db
				.select({ teamId: users.teamId })
				.from(users)
				.where(eq(users.id, userId))
				.limit(1);

			teamId = user[0]?.teamId ?? undefined;
		}

		if (!teamId) {
			console.log("No team ID found for API key");
			return null;
		}

		// Parse permissions from the key
		const permissions = key.permissions
			? typeof key.permissions === "string"
				? JSON.parse(key.permissions)
				: key.permissions
			: { tasks: ["read", "write"], projects: ["read", "write"] };

		console.log("API key verified successfully", {
			userId,
			teamId,
			keyId: key.id,
		});
		return {
			userId,
			teamId,
			permissions,
			keyId: key.id,
			mcpServerScope: resolveMcpServerScope(metadata),
		};
	} catch (error) {
		console.error("API key verification failed:", error);
		return null;
	}
}

/**
 * Return 401 Unauthorized response
 */
function unauthorizedResponse(message: string) {
	return new Response(
		JSON.stringify({
			jsonrpc: "2.0",
			error: { code: -32001, message },
			id: null,
		}),
		{
			status: 401,
			headers: {
				"Content-Type": "application/json",
				"WWW-Authenticate": `ApiKey realm="mcp"`,
				"Access-Control-Allow-Origin": "*",
				"Access-Control-Expose-Headers": "WWW-Authenticate, Mcp-Session-Id",
			},
		},
	);
}

/**
 * Create a new MCP server with tools registered for a specific user context
 */
function createMcpServerWithTools(context: McpContext): McpServer {
	const server = createMcpServer();

	registerTaskTools(server, () => context);

	return server;
}

/**
 * MCP Endpoint - handles all HTTP methods via @hono/mcp StreamableHTTPTransport
 *
 * This endpoint implements MCP over Streamable HTTP transport with API key authentication.
 * The @hono/mcp transport handles session management, SSE streams, and request routing.
 */
mcpRouter.all("/", async (c) => {
	// Support both x-api-key header and Authorization: Bearer header
	const apiKeyHeader =
		c.req.header("x-api-key") ??
		c.req.header("Authorization")?.replace("Bearer ", "");

	// Verify authentication
	const authResult = await verifyApiKey(apiKeyHeader);
	if (!authResult) {
		return unauthorizedResponse("Unauthorized: Valid API key required");
	}

	const { userId, teamId, permissions, keyId, mcpServerScope } = authResult;

	// Check rate limit
	const rateLimitResult = await checkMcpRateLimit(keyId);
	if (!rateLimitResult.success) {
		return c.json(
			{
				jsonrpc: "2.0",
				error: {
					code: -32000,
					message: `Rate limit exceeded. Try again in ${Math.ceil((rateLimitResult.reset - Date.now()) / 1000)} seconds.`,
				},
				id: null,
			},
			429,
		);
	}

	// Convert permissions to scopes for backward compatibility
	const scopes: string[] = [];
	if (permissions.tasks?.includes("read")) scopes.push("mimrai:tasks:read");
	if (permissions.tasks?.includes("write")) scopes.push("mimrai:tasks:write");
	if (permissions.projects?.includes("read"))
		scopes.push("mimrai:projects:read");
	if (permissions.projects?.includes("write"))
		scopes.push("mimrai:projects:write");

	// Create MCP context for this user
	const mcpContext: McpContext = { userId, teamId, scopes };
	// Create transport with user-based session ID
	const transport = new StreamableHTTPTransport();

	// Create the MCP server with native tools, then mount this key's scoped
	// slice of the team's configured MCP servers as namespaced proxy tools.
	// Upstream clients are connected fresh for this request/session — never
	// cached across requests — and MUST be closed once we're done with them,
	// success or failure alike.
	const mcpServer = createMcpServerWithTools(mcpContext);
	const proxyHandle = await registerProxiedMcpTools({
		server: mcpServer,
		teamId,
		userId,
		serverScope: mcpServerScope,
		nativeToolNames: NATIVE_MCP_TOOL_NAMES,
	});

	try {
		await mcpServer.connect(transport);

		// Let @hono/mcp handle the request
		return await transport.handleRequest(c);
	} finally {
		await proxyHandle.close();
	}
});

// Handle OPTIONS for CORS preflight
mcpRouter.options("/", () => {
	return new Response(null, {
		status: 204,
		headers: {
			"Access-Control-Allow-Origin": "*",
			"Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
			"Access-Control-Allow-Headers":
				"Authorization, Content-Type, Mcp-Session-Id, x-api-key",
			"Access-Control-Expose-Headers": "Mcp-Session-Id, WWW-Authenticate",
			"Access-Control-Max-Age": "86400",
		},
	});
});

// Health check endpoint
mcpRouter.get("/health", (c) => {
	return c.json({
		status: "ok",
		server: "mimrai-mcp-server",
		version: "1.0.0",
		auth: "api-key",
	});
});

export { mcpRouter };

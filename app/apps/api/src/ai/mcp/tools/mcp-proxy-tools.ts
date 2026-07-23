import { randomUUID } from "node:crypto";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import type { Tool } from "ai";
import { z } from "zod";
import type { MimraiMcpServer } from "../server";
import { connectTeamMcpServers } from "../shared/team-mcp-connections";

const MCP_NAME_UNSAFE_CHARS = /[^a-zA-Z0-9_-]+/g;
const MCP_NAME_PART_MAX_LENGTH = 64;

/**
 * Sanitize a raw server/tool name down to the MCP tool-name-safe charset
 * (`[a-zA-Z0-9_-]`). Whitespace becomes `_`; every other unsafe character is
 * dropped; the result is capped so the final `<slug>__<tool>` name stays a
 * sane length.
 */
function sanitizeMcpNamePart(raw: string): string {
	const cleaned = raw
		.trim()
		.replace(/\s+/g, "_")
		.replace(MCP_NAME_UNSAFE_CHARS, "");
	const safe = cleaned.length > 0 ? cleaned : "server";
	return safe.slice(0, MCP_NAME_PART_MAX_LENGTH);
}

function buildNamespacedToolName(serverName: string, toolName: string): string {
	return `${sanitizeMcpNamePart(serverName)}__${sanitizeMcpNamePart(toolName)}`;
}

/**
 * Best-effort extraction of `{ properties, required }` out of an AI SDK
 * `Tool.inputSchema` (a `FlexibleSchema`). For MCP-proxied tools this is
 * always the `jsonSchema()` wrapper `@ai-sdk/mcp` builds around the
 * upstream tool's raw JSON Schema (see `mcp-client.ts`'s `tools()`), which
 * exposes it synchronously via `.jsonSchema` — but the type only promises
 * `JSONSchema7 | PromiseLike<JSONSchema7>`, so we normalize through
 * `Promise.resolve` to cover both.
 */
async function extractJsonSchemaShape(
	inputSchema: unknown,
): Promise<{ properties?: Record<string, unknown>; required?: string[] }> {
	if (
		inputSchema &&
		typeof inputSchema === "object" &&
		"jsonSchema" in inputSchema
	) {
		const raw = await Promise.resolve(
			(inputSchema as { jsonSchema: unknown }).jsonSchema,
		);
		if (raw && typeof raw === "object") {
			const schema = raw as {
				properties?: Record<string, unknown>;
				required?: string[];
			};
			return { properties: schema.properties, required: schema.required };
		}
	}
	return {};
}

/**
 * Convert an upstream tool's JSON Schema into a Zod raw shape so it can be
 * registered on our native `McpServer` — which (via
 * `@modelcontextprotocol/sdk`'s `registerTool`) only accepts Zod-compatible
 * schemas, never raw JSON Schema. We only need property *names* and
 * *required-ness* to surface a usable schema to the connecting client;
 * values are intentionally left untyped (`z.unknown()`) and forwarded
 * verbatim — the upstream server performs its own argument validation.
 */
async function toZodRawShape(
	inputSchema: unknown,
): Promise<Record<string, z.ZodTypeAny>> {
	const { properties, required } = await extractJsonSchemaShape(inputSchema);
	const requiredKeys = new Set(required ?? []);

	const shape: Record<string, z.ZodTypeAny> = {};
	for (const key of Object.keys(properties ?? {})) {
		const base = z.unknown();
		shape[key] = requiredKeys.has(key) ? base : base.optional();
	}
	return shape;
}

function toErrorResult(text: string): CallToolResult {
	return {
		content: [{ type: "text", text }],
		isError: true,
	};
}

async function registerProxiedTool({
	server,
	namespacedName,
	upstreamServerName,
	toolName,
	tool,
}: {
	server: MimraiMcpServer;
	namespacedName: string;
	upstreamServerName: string;
	toolName: string;
	tool: Tool;
}): Promise<void> {
	const inputSchema = await toZodRawShape(tool.inputSchema);

	server.registerTool(
		namespacedName,
		{
			title: `${upstreamServerName}: ${toolName}`,
			description:
				tool.description ??
				`Proxied tool "${toolName}" from MCP server "${upstreamServerName}".`,
			inputSchema,
			annotations: {
				// Upstream behavior is unknown to us — never claim safety
				// hints we can't back up for arbitrary proxied tools.
				readOnlyHint: false,
				destructiveHint: true,
				idempotentHint: false,
				openWorldHint: true,
			},
		},
		async (params): Promise<CallToolResult> => {
			if (typeof tool.execute !== "function") {
				return toErrorResult(
					`Error: proxied tool "${namespacedName}" has no executable implementation.`,
				);
			}

			try {
				// Forward arguments verbatim — the upstream server validates them.
				const result = await tool.execute(params, {
					toolCallId: `mcp-gateway-${randomUUID()}`,
					messages: [],
				});
				return result as CallToolResult;
			} catch (error) {
				return toErrorResult(
					`Error calling proxied tool "${namespacedName}" on server "${upstreamServerName}": ${
						error instanceof Error ? error.message : "Unknown error"
					}`,
				);
			}
		},
	);
}

export interface McpProxyHandle {
	/** Cleanly disconnect every upstream client connected for this request. */
	close: () => Promise<void>;
}

/**
 * Connect a team's configured MCP servers (scoped per API key, see
 * `rest/routers/mcp.ts`'s `verifyApiKey`) and mount their tools onto the
 * native `McpServer`, namespaced as `<serverSlug>__<toolName>` so they can
 * never collide with Nexus's own tools.
 *
 * Upstream lifecycle: clients are connected fresh for this call (there is no
 * cross-request cache — this matches the existing per-request pattern where
 * `createMcpServerWithTools` also re-registers native tools on every
 * request) and MUST be closed via the returned `close()` once the MCP
 * request/response cycle for this call completes, success or failure alike.
 *
 * A server that fails to connect (down, unauthenticated, malformed config)
 * is skipped — logged server-side by `connectTeamMcpServers` — so the whole
 * endpoint degrades to native-tools-only rather than failing outright.
 */
const NOOP_PROXY_HANDLE: McpProxyHandle = { close: async () => {} };

export async function registerProxiedMcpTools({
	server,
	teamId,
	userId,
	serverScope,
	nativeToolNames,
}: {
	server: MimraiMcpServer;
	teamId: string;
	userId: string;
	serverScope: "all" | string[];
	nativeToolNames: ReadonlySet<string>;
}): Promise<McpProxyHandle> {
	// `connectTeamMcpServers` isolates PER-SERVER connect/tools() failures
	// internally (they land in its returned `errors` map, never thrown), but
	// its own prelude — `getMcpServers` + `getMcpServerUserTokens` (which can
	// throw on a missing `TOKEN_ENCRYPTION_KEY`, a malformed legacy token row,
	// or an AES-GCM auth-tag failure from key rotation) — runs BEFORE that
	// per-server isolation and can still throw. Native tools are already
	// registered on `server` by the caller before this function runs, so a
	// prelude throw here must degrade to zero proxied tools (never crash the
	// whole gateway request) — log server-side and hand back a no-op handle.
	let connectResult: Awaited<ReturnType<typeof connectTeamMcpServers>>;
	try {
		connectResult = await connectTeamMcpServers({
			teamId,
			userId,
			serverIds: serverScope === "all" ? undefined : serverScope,
		});
	} catch (error) {
		console.error(
			`[mcp-gateway] Failed to enumerate/connect team MCP servers for team ${teamId}; degrading to native-tools-only:`,
			error,
		);
		return NOOP_PROXY_HANDLE;
	}

	const { connections, errors } = connectResult;

	for (const [serverName, error] of Object.entries(errors)) {
		console.error(
			`[mcp-gateway] Skipping upstream MCP server "${serverName}" for team ${teamId}: ${error.message}`,
		);
	}

	const registeredNames = new Set<string>(nativeToolNames);

	for (const { server: upstreamServer, tools } of connections) {
		for (const [toolName, tool] of Object.entries(tools)) {
			const namespacedName = buildNamespacedToolName(
				upstreamServer.name,
				toolName,
			);

			if (registeredNames.has(namespacedName)) {
				console.warn(
					`[mcp-gateway] Skipping proxied tool "${namespacedName}" from server "${upstreamServer.name}" (${upstreamServer.id}): name collision.`,
				);
				continue;
			}
			registeredNames.add(namespacedName);

			try {
				await registerProxiedTool({
					server,
					namespacedName,
					upstreamServerName: upstreamServer.name,
					toolName,
					tool,
				});
			} catch (error) {
				// A single malformed upstream tool schema must never take down
				// the rest of the gateway (native tools + every other proxied
				// tool already registered) — skip just this tool and continue.
				registeredNames.delete(namespacedName);
				console.error(
					`[mcp-gateway] Skipping proxied tool "${namespacedName}" from server "${upstreamServer.name}" (${upstreamServer.id}): failed to register:`,
					error,
				);
			}
		}
	}

	return {
		close: async () => {
			await Promise.all(
				connections.map(({ client, server: upstreamServer }) =>
					client.close().catch((error) => {
						console.error(
							`[mcp-gateway] Error closing upstream MCP client "${upstreamServer.name}":`,
							error,
						);
					}),
				),
			);
		},
	};
}

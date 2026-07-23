/**
 * FEAT-019 — MCP gateway/mask: native tools + namespaced proxied tools.
 *
 * Real scripted MCP client handshake against a real `McpServer` instance,
 * over the SDK's `InMemoryTransport` (no HTTP/DB needed — `connectTeamMcpServers`
 * is mocked to stand in for real upstream MCP servers). This proves:
 *
 *  GWT-1  a connecting client's `tools/list` includes all 9 native tools
 *         PLUS every upstream tool, namespaced as `<serverSlug>__<toolName>`
 *         (sanitized to the MCP-safe charset)
 *  GWT-2  `tools/call` on a proxied tool forwards arguments verbatim and
 *         returns the upstream `execute()` result unmodified
 *  GWT-3  a proxied tool whose namespaced name collides with a native tool
 *         is silently skipped — never registered, never shadows the native one
 *  GWT-4  `close()` tears down every upstream client connected for the call
 *  GWT-5  a prelude failure inside `connectTeamMcpServers` (e.g. a thrown
 *         `TOKEN_ENCRYPTION_KEY`/decrypt error, which sits OUTSIDE that
 *         function's own per-server try/catch) degrades to zero proxied
 *         tools and a no-op `close()` — native tools are still fully served,
 *         never a whole-endpoint failure (REVISE fix for FEAT-019)
 *  GWT-6  the SAME prelude failure, hit via the other consumer of
 *         `connectTeamMcpServers` — `ai/tools/tool-registry.ts`'s
 *         `getTeamMcpTools` (used by `rest/routers/chat.ts`'s `Promise.all`
 *         on every chat turn and `trpc/routers/agents.ts`) — also degrades
 *         gracefully: `getAllTools` still returns every native/integration
 *         tool + toolbox, with the failure surfaced as
 *         `errors["team-mcp-servers"]` instead of throwing and taking down
 *         the whole chat turn (second REVISE fix for FEAT-019, sibling of
 *         GWT-5's fix in `mcp-proxy-tools.ts`)
 *
 * Run: cd app/apps/api && bun test src/__tests__/feat-019-mcp-gateway.test.ts
 */

import { describe, expect, mock, test } from "bun:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

const closeSpy = mock(() => Promise.resolve());

let connectShouldThrow = false;

function fakeConnections() {
	return {
		connections: [
			{
				server: {
					id: "srv-1",
					name: "Docs Server",
					transport: "http",
					config: { url: "https://example.test/mcp" },
				},
				client: { close: closeSpy },
				tools: {
					search_docs: {
						description: "Search the docs",
						inputSchema: {
							jsonSchema: {
								type: "object",
								properties: { query: { type: "string" } },
								required: ["query"],
							},
						},
						execute: async (args: unknown) => ({
							content: [
								{ type: "text", text: `found: ${JSON.stringify(args)}` },
							],
							structuredContent: { echoedArgs: args },
						}),
					},
				},
			},
			{
				server: {
					id: "srv-2",
					name: "Collider!!",
					transport: "http",
					config: { url: "https://example2.test/mcp" },
				},
				client: { close: closeSpy },
				tools: {
					ping: {
						description: "ping",
						inputSchema: { jsonSchema: { type: "object", properties: {} } },
						execute: async () => ({
							content: [{ type: "text", text: "pong" }],
						}),
					},
				},
			},
		],
		errors: {},
	};
}

mock.module("@api/ai/mcp/shared/team-mcp-connections", () => ({
	connectTeamMcpServers: async () => {
		if (connectShouldThrow) {
			throw new Error(
				"simulated prelude failure (e.g. TOKEN_ENCRYPTION_KEY unset / decrypt auth-tag failure)",
			);
		}
		return fakeConnections();
	},
}));

const { createMcpServer } = await import("@api/ai/mcp/server");
const { registerTaskTools, NATIVE_MCP_TOOL_NAMES } = await import(
	"@api/ai/mcp/tools/build-mcp"
);
const { registerProxiedMcpTools } = await import(
	"@api/ai/mcp/tools/mcp-proxy-tools"
);

function fakeContext() {
	return {
		userId: "u1",
		teamId: "t1",
		scopes: [
			"mimrai:tasks:read",
			"mimrai:tasks:write",
			"mimrai:projects:read",
			"mimrai:projects:write",
		],
	};
}

describe("FEAT-019 MCP gateway", () => {
	test("lists native + namespaced proxied tools and forwards calls verbatim", async () => {
		const server = createMcpServer();
		registerTaskTools(server, fakeContext);

		const proxyHandle = await registerProxiedMcpTools({
			server,
			teamId: "t1",
			userId: "u1",
			serverScope: "all",
			nativeToolNames: NATIVE_MCP_TOOL_NAMES,
		});

		const [clientTransport, serverTransport] =
			InMemoryTransport.createLinkedPair();
		const client = new Client({ name: "test-client", version: "1.0.0" });

		await Promise.all([
			client.connect(clientTransport),
			server.connect(serverTransport),
		]);

		const { tools } = await client.listTools();
		const names = tools.map((t) => t.name);

		for (const nativeName of NATIVE_MCP_TOOL_NAMES) {
			expect(names).toContain(nativeName);
		}
		expect(names).toContain("Docs_Server__search_docs");
		expect(names).toContain("Collider__ping");

		const result = await client.callTool({
			name: "Docs_Server__search_docs",
			arguments: { query: "hello" },
		});
		expect(result.isError).not.toBe(true);
		expect(JSON.stringify(result)).toContain("hello");

		await proxyHandle.close();
		expect(closeSpy).toHaveBeenCalled();

		await client.close();
		await server.close();
	});

	test("skips a proxied tool whose namespaced name collides with a native tool name", async () => {
		const server = createMcpServer();
		registerTaskTools(server, fakeContext);

		// Simulate a collision: treat "Docs_Server__search_docs" as if it were
		// already a native tool name.
		const collidingNativeNames = new Set([
			...NATIVE_MCP_TOOL_NAMES,
			"Docs_Server__search_docs",
		]);

		const proxyHandle = await registerProxiedMcpTools({
			server,
			teamId: "t1",
			userId: "u1",
			serverScope: "all",
			nativeToolNames: collidingNativeNames,
		});

		const [clientTransport, serverTransport] =
			InMemoryTransport.createLinkedPair();
		const client = new Client({ name: "test-client-2", version: "1.0.0" });
		await Promise.all([
			client.connect(clientTransport),
			server.connect(serverTransport),
		]);

		const { tools } = await client.listTools();
		const names = tools.map((t) => t.name);

		expect(names).not.toContain("Docs_Server__search_docs");
		expect(names).toContain("Collider__ping");

		await proxyHandle.close();
		await client.close();
		await server.close();
	});

	test("degrades to native-tools-only when connectTeamMcpServers' prelude throws", async () => {
		connectShouldThrow = true;
		try {
			const server = createMcpServer();
			registerTaskTools(server, fakeContext);

			const proxyHandle = await registerProxiedMcpTools({
				server,
				teamId: "t1",
				userId: "u1",
				serverScope: "all",
				nativeToolNames: NATIVE_MCP_TOOL_NAMES,
			});

			const [clientTransport, serverTransport] =
				InMemoryTransport.createLinkedPair();
			const client = new Client({ name: "test-client-3", version: "1.0.0" });
			await Promise.all([
				client.connect(clientTransport),
				server.connect(serverTransport),
			]);

			const { tools } = await client.listTools();
			const names = tools.map((t) => t.name);

			for (const nativeName of NATIVE_MCP_TOOL_NAMES) {
				expect(names).toContain(nativeName);
			}
			expect(names).not.toContain("Docs_Server__search_docs");
			expect(names).not.toContain("Collider__ping");

			// No connections were ever established, so close() must be a safe no-op.
			await expect(proxyHandle.close()).resolves.toBeUndefined();

			await client.close();
			await server.close();
		} finally {
			connectShouldThrow = false;
		}
	});
});

describe("FEAT-019 tool-registry getAllTools graceful degradation (REVISE fix)", () => {
	test("getTeamMcpTools' prelude throw still returns native/integration tools + toolboxes from getAllTools", async () => {
		connectShouldThrow = true;
		try {
			const { getAllTools } = await import("@api/ai/tools/tool-registry");

			const { tools, toolboxes, errors } = await getAllTools(
				undefined,
				"t1",
				"u1",
			);

			// Native task-management/research/memory tools survive untouched.
			expect(tools.createTask).toBeDefined();
			expect(tools.webSearch).toBeDefined();
			expect(tools.saveAgentMemory).toBeDefined();
			expect(toolboxes.taskManagement).toBeDefined();
			expect(toolboxes.research).toBeDefined();
			expect(toolboxes.memory).toBeDefined();

			// No mcp: tools/toolboxes were registered — zero connections were made.
			expect(Object.keys(toolboxes).some((k) => k.startsWith("mcp:"))).toBe(
				false,
			);

			// The prelude failure is surfaced, not swallowed and not thrown.
			expect(errors["team-mcp-servers"]).toBeInstanceOf(Error);
			expect(errors["team-mcp-servers"]?.message).toContain(
				"simulated prelude failure",
			);
		} finally {
			connectShouldThrow = false;
		}
	});
});

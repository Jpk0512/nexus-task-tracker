import type { MimraiMcpServer } from "../server";
import { registerCreateTaskTool } from "./create-task";
import { registerDeleteTaskTool } from "./delete-task";
import { registerGetTaskTool } from "./get-task";
import { registerListLabelsTool } from "./list-labels";
import { registerListMilestonesTool } from "./list-milestones";
import { registerListProjectsTool } from "./list-projects";
import { registerListStatusesTool } from "./list-statuses";
import { registerListTasksTool } from "./list-tasks";
import { registerUpdateTaskTool } from "./update-task";

/**
 * MCP Context provided by OAuth token verification
 */
export interface McpContext {
	userId: string;
	teamId: string;
	scopes: string[];
}

export function registerTaskTools(
	server: MimraiMcpServer,
	getContext: () => McpContext,
) {
	registerListTasksTool(server, getContext);
	registerGetTaskTool(server, getContext);
	registerCreateTaskTool(server, getContext);
	registerUpdateTaskTool(server, getContext);
	registerDeleteTaskTool(server, getContext);
	registerListStatusesTool(server, getContext);
	registerListLabelsTool(server, getContext);
	registerListProjectsTool(server, getContext);
	registerListMilestonesTool(server, getContext);
}

/**
 * The 9 native Nexus tool names registered by `registerTaskTools`, above.
 * The MCP gateway proxy (`mcp-proxy-tools.ts`) checks every namespaced
 * proxied tool name against this set before registering it, so a
 * misconfigured or maliciously-named upstream MCP server can never shadow a
 * native tool.
 */
export const NATIVE_MCP_TOOL_NAMES: ReadonlySet<string> = new Set([
	"mimrai_list_tasks",
	"mimrai_get_task",
	"mimrai_create_task",
	"mimrai_update_task",
	"mimrai_delete_task",
	"mimrai_list_statuses",
	"mimrai_list_labels",
	"mimrai_list_projects",
	"mimrai_list_milestones",
]);

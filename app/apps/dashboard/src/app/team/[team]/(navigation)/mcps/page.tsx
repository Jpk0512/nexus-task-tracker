import { McpServerList } from "../settings/(navigation)/mcp-servers/mcp-server-list";

/**
 * Ops → MCPs — living catalog (reuses settings MCP list surface).
 */
export default function MCPsPage() {
	return (
		<div className="flex h-full min-h-0 flex-col">
			<div className="border-border/60 border-b px-4 py-3">
				<h1 className="font-[510] text-[18px] tracking-[-0.01em]">MCPs</h1>
				<p className="text-[13px] text-muted-foreground">
					Servers, tools, status. Secrets inject from Ops → Secrets — never paste
					into chat.
				</p>
			</div>
			<div className="min-h-0 flex-1 overflow-auto p-4">
				<McpServerList />
			</div>
		</div>
	);
}

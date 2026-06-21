# nexus-mcp

MCP stdio server exposing todos, tasks, projects, knowledge, and prompts to Claude Desktop / Claude CLI. Talks directly to Postgres — no API hop.

## Prerequisites

- [Bun](https://bun.sh) >= 1.0
- Postgres running and seeded (see `app/` docker-compose)

## Install into ~/.claude/mcp.json (owner-approved manual step)

Per spec L176 (FEAT-002), the build and `~/.claude/mcp.json` registration are an **owner-approved manual step** — Nexus does not run this autonomously.

### 1. Set required env vars in your shell

```sh
export NEXUS_API_TOKEN=<your-token>   # bearer token for the Nexus API
export NEXUS_TEAM_ID=<your-team-id>  # team slug or UUID scoping all queries
```

Optional vars (forwarded into the MCP entry if set):

```sh
export NEXUS_USER_ID=<user-id>             # defaults to "local-dev-user" in server.ts
export NEXUS_KNOWLEDGE_ROOT=/path/to/vault # Obsidian vault directory
export NEXUS_DATABASE_URL=postgresql://...  # defaults to local dev URL in server.ts
```

### 2. Run the install script (from mcp-server/)

```sh
# Dry run first — prints merged JSON, writes nothing:
DRY_RUN=1 bun run install:mcp

# Live run — backs up existing ~/.claude/mcp.json, then merges the entry:
bun run install:mcp
```

The script is **idempotent**: re-running it updates the entry in place, never duplicates it.

### 3. Restart Claude Desktop

Changes to `~/.claude/mcp.json` take effect after restarting Claude Desktop.

## Development

```sh
bun run start        # run server directly (no build step)
bun run build        # compile → dist/index.js
bun run check-types  # TypeScript type check (no emit)
```

## Entry produced in ~/.claude/mcp.json

```json
{
  "mcpServers": {
    "nexus-mcp": {
      "type": "stdio",
      "command": "bun",
      "args": ["/absolute/path/to/mcp-server/dist/index.js"],
      "env": {
        "NEXUS_API_TOKEN": "<from env>",
        "NEXUS_TEAM_ID": "<from env>"
      }
    }
  }
}
```

# Nexus

Single-user, local-only personal assistant: tasks/todos, an Obsidian-compatible
knowledge vault, and a prompt library — plus an MCP server that exposes all three
to Claude. Linear-style UI, Notion-style features. Bun + Turbo monorepo (`app/`,
`@nexus-app/*` workspaces), Next.js 16, pgvector Postgres + Redis (Docker), Nexus
AI orchestrator (`.claude/`, `.memory/`).

**Never deployed to the internet — no SaaS, no billing, no multi-tenant hosting.**
Runs entirely on the user's laptop. (Billing/Stripe was removed in FEAT-001; only a
removal-guard test remains.)

## What it does

- **Tasks & todos** — Linear-style task tracker plus a lightweight todos surface.
- **Knowledge vault** — Obsidian-compatible notes with `[[wiki-links]]`, backlinks,
  and Postgres full-text search (`content_fts`).
- **Prompt library** — reusable prompts, project-scoped.
- **MCP server** — a stdio server (`mcp-server/`) exposing all of the above to
  Claude Desktop / Claude CLI (11 tools). See [mcp-server/README.md](mcp-server/README.md).

All data is local: Postgres + pgvector and Redis run in Docker; there is no
external Supabase or cloud backend.

## Quick start

```bash
cd app
docker compose -f docker-compose.local.yaml up -d --build
# Dashboard: http://localhost:5179
# API:       http://localhost:3003
# Postgres (pgvector): localhost:55432   Redis: localhost:56379
```

See [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md) for full setup.

## Orientation

| You want to... | Read |
|---|---|
| Understand what this is and what's true | [docs/STATE_OF_NEXUS.md](docs/STATE_OF_NEXUS.md) ← start here |
| See the architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Set up local dev | [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md) |
| Work with the AI orchestrator | [CLAUDE.md](CLAUDE.md), [docs/agents/TEAM.md](docs/agents/TEAM.md) |
| See governance | [docs/CONSTITUTION.md](docs/CONSTITUTION.md) |
| Browse archived docs | [docs/archive/](docs/archive/) |

## Repo layout

```
app/                       Next.js 16 + Turbo monorepo, `@nexus-app/*` workspaces
  apps/                    dashboard, api, desktop, website (website is upstream leftover)
  packages/                db (Drizzle schema + migrations), trpc, ui, integration, …
docs/                      Project docs (canonical)
  agents/                  Persona contracts (TEAM, CONTRACT, TEST_CONTRACT)
  archive/                 Stale upstream + template docs (kept for history)
mcp-server/                Bun stdio MCP server — todos, tasks, projects, knowledge, prompts (11 tools)
.claude/                   Agent personas + skills + hooks
.memory/                   Persistent project state (sqlite + files)
```

## License

See [app/LICENSE](app/LICENSE).

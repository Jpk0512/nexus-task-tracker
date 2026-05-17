# Nexus

Single-user, local-only task tracker. Linear-style UI, Notion-style features. Bun + Turbo monorepo (`app/`), pgvector Postgres + Redis (Docker), Nexus AI orchestrator (`.claude/`, `.memory/`).

**Never deployed to the internet.** Runs entirely on the user's laptop.

## Quick start

```bash
cd app
docker compose -f docker-compose.local.yaml up -d
# Dashboard: http://localhost:5179
# API:       http://localhost:3003
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
app/                       Next.js + Turbo monorepo (dashboard, api, desktop, website-leftover)
docs/                      Project docs (canonical)
  agents/                  Persona contracts (TEAM, CONTRACT, TEST_CONTRACT)
  archive/                 Stale upstream + template docs (kept for history)
mcp-server/                MCP server (Bun) — task CRUD via Claude MCP
.claude/                   Agent personas + skills + hooks
.memory/                   Persistent project state (sqlite + files)
```

## License

See [app/LICENSE](app/LICENSE).

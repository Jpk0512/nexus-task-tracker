# State of Nexus — Source of Truth

Last updated: 2026-05-17 (iter 9 rebrand).

## What this is

Nexus is a single-user, local-only task tracker. UI styled after Linear, lifting features from Notion. Postgres + pgvector single-container backend. Bun + Turbo monorepo with Next.js dashboard + Hono/tRPC API + MCP server. Orchestrated by the Nexus AI agent plugin (.claude/, .memory/). Never deployed to the internet — runs entirely on the user's laptop.

The project was downloaded from a public GitHub repo (originally a multi-tenant SaaS task tracker called Mimrai), then customized over iters 1-8. The rebrand to "Nexus" landed in iter 9.

## Stack (as of iter 9)

| Layer | Tech |
|---|---|
| Runtime | Bun 1.2 |
| Monorepo | Turborepo |
| Frontend | Next.js 15 App Router + React + Tailwind + Shadcn UI |
| Backend | Hono + tRPC (`app/apps/api`) |
| Database | Postgres (pgvector/pgvector:pg16, single container) + Drizzle ORM |
| Cache | Redis 7 (local container) |
| Auth | Better Auth library installed but bypassed via `MIMRAI_LOCAL_DEV=1` (hardcoded user) |
| Lint/format | Biome |
| Vector embeddings | pgvector (table exists; vectors not populated yet) |
| Tracing | None (Sentry stubbed) |
| Background jobs | Trigger.dev stubbed |
| Email | Resend stubbed |
| Analytics | OpenPanel stubbed |
| Billing | Stripe stubbed |
| Orchestration | Nexus plugin (Claude Code agent system) |
| MCP server | `mcp-server/` — Bun project, single-file dist |

## Containers

| Container (per compose) | Image | Host port | Notes |
|---|---|---|---|
| `nexus-postgres` | pgvector/pgvector:pg16 | 55432 | Renamed from `mimrai-postgres` in iter 9; running containers still named `mimrai-*` until next `docker compose up` |
| `nexus-redis` | redis:7-alpine | 56379 | Same |
| `nexus-api` | (built locally) | 3003 | Same |
| `nexus-dashboard` | (built locally) | 5179 | Same |

Compose volume: `mimrai-pg-data` (live: `app_mimrai-pg-data`). Not renamed in iter 9 — would orphan the database. Volume rename + container restart deferred.

## Migrations done (chronological)

| Iter | Migration | Rationale |
|---|---|---|
| 1 | Upstream app moved into `/Users/john.keeney/mimrai/app/` | Wrap in local-dev shell |
| 2 | Nexus orchestrator + memory layer installed alongside app | Agent-driven workflow |
| 2 | Postgres image swapped: `postgres:16` → `pgvector/pgvector:pg16` | Drizzle uses `vector` type for embeddings |
| 3 | Adopted Supabase 13-container stack | Wanted auth/storage/realtime |
| 4 | Auth bypassed via `MIMRAI_LOCAL_DEV=1`; all external services stubbed | Single-user local doesn't need them |
| 5 | Kanban + tasks shipped | Core feature |
| 6 | Mermaid editor | Notion-style page feature |
| 7 | Rolled back Supabase → single-container pgvector | Only Postgres + pgvector were used |
| 8 | a11y sweep + perf (skeletons + optimistic) + 46-tab work (sticky save, label counts, scope chips) | Polish |
| 9 | Rebrand mimrai → Nexus + doc cleanup + this doc | Source-of-truth hygiene |

## In scope

- Single-user, local-only task tracking
- Linear-style UI / Notion-style page editor
- MCP-accessible (Claude can read/write tasks via the MCP server)
- Nexus orchestrator runs sub-agents (forge / lens / scout / quill) for development

## Out of scope (permanent)

- Deployment to the internet
- Multi-tenant / multi-user / teams
- Billing / payments (Stripe stubbed forever)
- OAuth providers for tenants
- Email sending (Resend stubbed forever)
- Background jobs in production (Trigger.dev stubbed forever)
- Marketing website (`app/apps/website/` remains as upstream artifact, flagged for future deletion)

## Deferred to future iters

- Rename `MIMRAI_LOCAL_DEV` → `NEXUS_LOCAL_DEV` (18 consumers; requires container restart)
- Rename `MIMRAI_SSR_SERVER_URL` → `NEXUS_SSR_SERVER_URL` (2 consumers; requires container restart)
- Rename volume `mimrai-pg-data` → `nexus-pg-data` (requires data migration to avoid orphan)
- Delete `app/apps/website/` (34 .tsx files of upstream marketing site — never deployed)
- Monorepo package namespace cleanup (`@mimir/*` pre-existing typo from upstream)

## Pointers

- Canonical docs: `docs/CONSTITUTION.md`, `docs/ARCHITECTURE.md`, `docs/LOCAL_DEV.md`, `docs/agents/{CONTRACT,TEAM,TEST_CONTRACT,SKILL_MAP}.md`
- Project memory: `.memory/project.db` (sqlite) + `.memory/files/` (file-based)
- Agent personas: `.claude/agents/` (forge, lens, scout, quill, quill-py, nexus-orchestrator)
- Skills: `.claude/skills/`
- MCP server: `mcp-server/`
- Archived: `docs/archive/upstream/supabase-stack/`, `docs/archive/upstream/app-{README,AGENTS}.md`, `docs/archive/templates/nexus-orchestrator/`

## If you find another stale reference

```
cd /Users/john.keeney/mimrai
/opt/homebrew/bin/rg -i 'mimrai' --type-not lock -l
```

Anything outside the deferred list is fair game to rename. Update this doc with the iter you cleaned it.

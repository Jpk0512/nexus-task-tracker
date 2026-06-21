# State of Nexus — Source of Truth

Last updated: 2026-06-20 (FEAT-002 ship + @nexus-app rename + security sweep). Nexus orchestrator v1.13.0.

## What this is

Nexus is a single-user, local-only task & knowledge tracker. UI styled after Linear, lifting features from Notion/Obsidian. Postgres + pgvector single-container backend. Bun + Turbo monorepo with a Next.js 15 dashboard + Hono/tRPC API + a standalone stdio MCP server. Orchestrated by the Nexus AI agent plugin (`.claude/`, `.memory/`). Never deployed to the internet — runs entirely on the owner's laptop.

The project was downloaded from a public GitHub repo (originally a multi-tenant SaaS task tracker called Mimrai), rebranded to "Nexus" in iter 9, then **repurposed in FEAT-001** from a SaaS into a local-only, API-accessed personal app (billing/Stripe and the SaaS layers removed). **FEAT-002** then added four personal-assistant capabilities: Todos, Knowledge vault, Prompt library, and the MCP server.

## Stack (current — v1.13.0)

| Layer | Tech |
|---|---|
| Runtime | Bun 1.2 |
| Monorepo | Turborepo. Workspace scope **`@nexus-app/*`** (renamed from `@mimir/*`, commit `60e3bd5`, TASK-009 — see DEC-014 below). |
| Frontend | Next.js 15 App Router + React + Tailwind + Shadcn UI + Tiptap + dnd-kit |
| Backend | Hono + tRPC (`app/apps/api`) + REST route handlers for webhooks/MCP auth |
| Database | Postgres (`pgvector/pgvector:pg16`, single container) + Drizzle ORM |
| Cache | Redis 7 (local container) |
| Auth | Better Auth installed but bypassed via `NEXUS_LOCAL_DEV=1` (hardcoded user) |
| Lint/format | Biome |
| AI layer | Vercel AI SDK v4 |
| Vector embeddings | pgvector (`task_embeddings` table) |
| Billing | **REMOVED in FEAT-001** (migration `0002_drop_billing.sql`; no billing code remains) |
| Tracing | None (Sentry stubbed) |
| Background jobs | Trigger.dev stubbed |
| Email | Resend stubbed |
| Analytics | OpenPanel stubbed |
| Orchestration | Nexus plugin (Claude Code agent system), v1.13.0 |
| MCP server | `mcp-server/` — Bun stdio project, `dist/` build; identity `nexus` v0.1.0; 11 tools |
| Type-check | `tsc` baseline at **0 errors** |

## FEAT-002 capabilities (shipped)

- **Todos** (`todos`, `todo_attachments`) — daily-driver capture, team/user-scoped, dashboard `/todos`, MCP `add_todo`/`list_todos`/`check_todo`.
- **Knowledge vault** — Obsidian-compatible: `[[wiki-links]]` → `knowledge_links` backlink graph; full-text search over `knowledge_notes.content_fts` (generated tsvector + GIN). Host vault mounted read-write at `/Users/john.keeney/nexus-knowledge`. MCP `search_knowledge`/`read_note`/`write_note`.
- **Prompt library** — versioned prompts under products (auto-seeded `kbuddy`), `{{var}}` auto-detection + copy-filled interpolation, atomic version bumps, team-scoped project picker. MCP `list_prompts`/`get_prompt`.
- **MCP server** — standalone Bun stdio server talking directly to Postgres. 11 tools: `add_todo`, `list_todos`, `check_todo`, `list_tasks_due_soon`, `list_projects`, `search_knowledge`, `read_note`, `write_note`, `list_prompts`, `get_prompt`, `add_task`. Registered in `~/.claude/mcp.json` as an owner-approved manual step.

## Security model

- All tRPC mutations + sensitive reads team-scoped to `ctx.user.teamId`.
- Cross-tenant IDORs swept and fixed (TASK-032 closed four read IDORs in the tasks router; prompts `setProject` rejects out-of-team `projectId`).
- `createCallerFactory` behavioral harness: `app/apps/api/src/__tests__/task-029-idor-caller-harness.test.ts` asserts foreign-team READ/WRITE rejection.
- OAuth tokens AES-GCM-256 encrypted at rest: `app/packages/utils/src/token-crypto.ts`.
- Webhooks verify signatures: `app/apps/api/src/rest/webhooks/{github,slack,twilio}.ts`.

## Schema

Drizzle schema: `app/packages/db/src/schema.ts`. Knowledge tables (`knowledge_vaults`, `knowledge_notes` incl. `content_fts`, `knowledge_links`, `knowledge_notes_on_tasks`) and the FTS column/index now live here. Migration `0002_drop_billing.sql` is an intentional one-way billing-table drop (FEAT-001).

## Run / topology

Stack is `app/docker-compose.local.yaml`:

```sh
cd app
docker compose -f docker-compose.local.yaml up -d --build
```

| Service | Image | Host port |
|---|---|---|
| `nexus-postgres` | `pgvector/pgvector:pg16` | 55432 |
| `nexus-redis` | `redis:7-alpine` | 56379 |
| `nexus-api` | built from `apps/api/Dockerfile.local` | 3003 |
| `nexus-dashboard` | built from `apps/dashboard/Dockerfile.dev` | 5179 |

Dashboard at http://localhost:5179, API at http://localhost:3003. See `docs/LOCAL_DEV.md`.

## In scope

- Single-user, local-only task + knowledge tracking
- Linear-style UI / Notion- & Obsidian-style editors
- MCP-accessible (Claude reads/writes todos, tasks, projects, knowledge, prompts via the MCP server)
- Nexus orchestrator runs sub-agents for development

## Out of scope (permanent)

- Deployment to the internet
- Multi-user onboarding / public SaaS (the multi-tenant data model remains internally, with tenant isolation enforced, but no public signup)
- Billing / payments (**removed** in FEAT-001, not merely stubbed)
- Email sending (Resend stubbed)
- Background jobs in production (Trigger.dev stubbed)
- Marketing website (`app/apps/website/` remains as upstream artifact, flagged for deletion)

## Migrations done (chronological)

| Iter / Feat | Migration | Rationale |
|---|---|---|
| 1 | Upstream app moved into `app/` | Wrap in local-dev shell |
| 2 | Nexus orchestrator + memory layer installed | Agent-driven workflow |
| 2 | Postgres image `postgres:16` → `pgvector/pgvector:pg16` | Drizzle `vector` type for embeddings |
| 3 | Adopted Supabase 13-container stack | Wanted auth/storage/realtime |
| 4 | Auth bypassed via `NEXUS_LOCAL_DEV=1`; external services stubbed | Single-user local |
| 5 | Kanban + tasks shipped | Core feature |
| 6 | Mermaid editor | Notion-style page feature |
| 7 | Rolled back Supabase → single-container pgvector | Only Postgres + pgvector were used |
| 8 | a11y sweep + perf + 46-tab polish | Polish |
| 9 | Rebrand mimrai → Nexus + doc cleanup | Source-of-truth hygiene |
| FEAT-001 | Repurpose SaaS → local-only; drop billing (`0002_drop_billing.sql`); API-accessed | Personal daily driver |
| FEAT-002 | Todos, Knowledge vault (wiki-links/backlinks/FTS), Prompt library, MCP server | Personal-assistant capabilities |
| TASK-009 | `@mimir/*` → `@nexus-app/*` across 403 files (`60e3bd5`) | Namespace consistency (DEC-014) |
| (sweep) | Cross-tenant IDOR sweep + caller harness; OAuth AES-GCM at rest; webhook signature verification; tsc baseline → 0 | Security + type hygiene |

## DEC-014 supersedes DEC-002 (scope rename)

DEC-002 originally chose to **keep** the upstream `@mimir/*` workspace scope (rename deemed too churny). DEC-014 supersedes it: the scope was renamed to `@nexus-app/*` across the monorepo (403 files, `60e3bd5`). DEC-002 remains in the record as historical context; it is no longer in force.

## Project-name string holdouts (still `mimrai`, intentionally deferred)

These are runtime/data contracts left as-is to avoid orphaning the database or breaking connections:

- Root `app/package.json` `"name": "mimir"` (cosmetic monorepo root name).
- Postgres credentials/db `POSTGRES_USER/PASSWORD/DB=mimrai` and `DATABASE_URL` (renaming requires `ALTER USER`/`ALTER DATABASE` + container restart).
- Compose volume `mimrai-pg-data` (renaming would orphan the live volume; needs pg_dump/restore).
- These are isolated to infra credentials/volume identity, NOT application code — the `@nexus-app/*` code scope is fully renamed.

## Pointers

- Canonical docs: `docs/CONSTITUTION.md`, `docs/ARCHITECTURE.md`, `docs/STACK-PROFILE.md`, `docs/LOCAL_DEV.md`, `docs/features/FEAT-001-*.md`, `docs/features/FEAT-002-*.md`, `docs/agents/{CONTRACT,TEAM,TEST_CONTRACT,SKILL_MAP}.md`
- Project memory: `.memory/project.db` (sqlite) + `.memory/files/`; version in `.memory/.nexus-version`
- MCP server: `mcp-server/` (`server.ts`, `install.ts`, tests)
- Schema: `app/packages/db/src/schema.ts`
- Archived: `docs/archive/upstream/`, `docs/archive/templates/nexus-orchestrator/`

## If you find another stale reference

```
cd /Users/john.keeney/nexus-task-tracker
/opt/homebrew/bin/rg -i 'mimrai' --type-not lock -l   # infra holdouts only; @mimir code scope is gone
```

Application-code `@mimir` references are 0. Remaining `mimrai` hits are the infra credential/volume holdouts listed above — coordinate any rename with a DB migration.

# Architecture

## Overview

Nexus is a single-user, local-only task & knowledge tracker, originally forked from an open-source Linear-style tracker (github.com/mimrai-org/mimrai, no upstream git connection) and repurposed in FEAT-001 from a multi-tenant SaaS into a personal, API-accessed app that runs entirely on the owner's laptop. App source lives under `app/`. The standalone MCP server lives under `mcp-server/`.

## Tech Stack

- Languages: TypeScript
- Runtime: Bun 1.2
- Monorepo: Turborepo. **Workspace scope is `@nexus-app/*`** (renamed from `@mimir/*` across 403 files in commit `60e3bd5`, TASK-009 — DEC-014 supersedes DEC-002's earlier "keep `@mimir`" decision).
- Frontend: Next.js 15 App Router + React + Tailwind + Shadcn UI (`app/apps/dashboard`); Tiptap editor + dnd-kit for the Knowledge/Todos pages.
- API: Hono + tRPC server in `app/apps/api`, plus REST route handlers (`app/apps/api/src/rest`) for webhooks and MCP-server auth.
- Database: Postgres (`pgvector/pgvector:pg16`, single container) + Drizzle ORM (`app/packages/db`). A full Supabase stack was briefly adopted then rolled back — only Postgres + pgvector were ever needed.
- Cache: Redis 7 (local container)
- Lint/format: Biome
- AI layer: Vercel AI SDK v4

## Source Tree

- `app/apps/dashboard` — main web dashboard (Next 15)
- `app/apps/website` — marketing website (upstream artifact, not deployed; flagged for deletion)
- `app/apps/api` — Hono + tRPC backend + REST route handlers
- `app/apps/desktop` — Tauri/desktop client (not used in local dev)
- `app/packages/*` — shared workspace packages (`@nexus-app/db`, `@nexus-app/utils`, `@nexus-app/trpc`, `@nexus-app/integration`, `@nexus-app/storage`, `@nexus-app/cache`, `@nexus-app/ui`, …). **There is no `billing` package — Stripe/billing was removed in FEAT-001.**
- `app/scripts/init-db/` — pgvector extension init; `app/packages/db/` — Drizzle schema (`src/schema.ts`), migrations, seed (`seed-local-dev.ts`)
- `mcp-server/` — standalone Bun stdio MCP server (talks directly to Postgres, no API hop)
- `.claude/`, `.memory/`, `docs/` — Nexus orchestration scaffolding (the original template tree was archived to `docs/archive/templates/nexus-orchestrator/`)

## FEAT-002 feature set (shipped)

Four local-only, API-accessed capabilities built on top of the repurposed app (`docs/features/FEAT-002-next-features.md`):

| Feature | Where | Notes |
|---|---|---|
| **Todos** | `app/apps/dashboard` `/todos`, tRPC `todos` router | Daily-driver capture; `todos` + `todo_attachments` tables, team- and user-scoped, with order/checked indexes. |
| **Knowledge vault** | dashboard `/knowledge`, tRPC `knowledge` router | Obsidian-compatible: `[[wiki-links]]` parsed into a `knowledge_links` graph (backlinks), full-text search over a `content_fts` generated tsvector column (GIN). Read-write against the host vault at `/Users/john.keeney/nexus-knowledge`. |
| **Prompt library** | dashboard `/prompts`, tRPC `prompts` router | Versioned prompts under products (auto-seeded `kbuddy` product); `{{var}}` auto-detection + copy-filled interpolation; atomic, race-free version bumps; team-scoped project picker. |
| **MCP server** | `mcp-server/server.ts` | Standalone Bun stdio server, identity `name: "nexus", version: "0.1.0"`, exposing **11 tools** (see below). Registered in `~/.claude/mcp.json` as an owner-approved manual step (FEAT-002 spec L176) — Nexus does not register it autonomously. |

### MCP tools (11)

`add_todo`, `list_todos`, `check_todo`, `list_tasks_due_soon`, `list_projects`, `search_knowledge`, `read_note`, `write_note`, `list_prompts`, `get_prompt`, `add_task`.

## Security model

The app is local-only and never internet-deployed, but FEAT-001 left the multi-tenant data model in place, so tenant isolation is enforced rather than removed:

- **Team-scoping:** all tRPC mutations and sensitive reads are scoped to `ctx.user.teamId`. Cross-tenant IDORs were swept and fixed (e.g. TASK-032 closed four cross-tenant read IDORs in the tasks router; the prompts `setProject` rejects a `projectId` outside the caller's team).
- **Test harness:** `app/apps/api/src/__tests__/task-029-idor-caller-harness.test.ts` is a `createCallerFactory`-based behavioral harness asserting foreign-team READ/WRITE IDORs are rejected.
- **OAuth tokens at rest:** AES-GCM-256 envelope encryption via `app/packages/utils/src/token-crypto.ts`; used by `app/apps/api/src/rest/routers/mcp-server-auth.ts` and `app/packages/db/src/queries/mcp-servers.ts`.
- **Webhooks verify signatures:** `app/apps/api/src/rest/webhooks/{github,slack,twilio}.ts` verify inbound signatures before processing.

## Schema

Drizzle schema is `app/packages/db/src/schema.ts`. Knowledge tables live here: `knowledge_vaults`, `knowledge_notes` (with the `content_fts` generated tsvector column + GIN index `idx_knowledge_notes_content_fts`), `knowledge_links` (wiki-link graph, with `from_note_id`/`to_note_id` FKs), and the `knowledge_notes_on_tasks` join table. Migration `0002_drop_billing.sql` is an intentional one-way drop of the billing tables (FEAT-001).

## Type-check baseline

The TypeScript baseline is at **0 errors** (`tsc --noEmit` per package, e.g. `app/apps/api` `bun run typecheck`). The pre-existing TS debt recorded in earlier iters has been cleared.

## External integrations (stubbed in local dev)

| Service | Package / location | Local strategy |
|---|---|---|
| Resend (email) | `@nexus-app/email` | log-only stub |
| OpenPanel (analytics) | `@nexus-app/events` | no-op stub |
| Sentry (errors) | (shared) | DSN disabled |
| Trigger.dev (jobs) | `@nexus-app/jobs` | inline/no-op stub |
| Redis | `@nexus-app/cache` | local redis container |
| OpenAI embeddings | `@nexus-app/embedding` | pgvector table exists |
| GitHub / Slack / Twilio | `@nexus-app/integration` + REST webhooks | signature-verified webhooks |

Stripe / billing was **removed** in FEAT-001 — no billing code remains; the `feat-1-stripe-removal-guard` test (`app/apps/dashboard/__tests__/`) and the dead-import guard (which references `@nexus-app/integration`) keep it from creeping back.

## Personas

Development is orchestrated by the Nexus agent plugin. Code-writing personas: Forge (UI/wiring), Pipeline, Atlas (schema), Hermes (integration). Read-only: Scout, Lens (verify), Palette. Quill authors tests. See `docs/agents/TEAM.md`.

---
*Reconciled against repo reality on 2026-06-20 (Nexus v1.13.0).*

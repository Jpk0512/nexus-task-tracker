# Tasks

> Auto-synced from `.memory/project.db`. Do not edit by hand.

## FEAT-001 — Repurpose Nexus (13/13 done, 100%)

| Task | Status | Owner | Updated |
|------|--------|-------|---------|
| **TASK-001** — P0 rename/paths/env + drop dead mount | done |  | 2026-06-16 |
| **TASK-002** — P1 local-disk FileStorageAdapter | done |  | 2026-06-17 |
| **TASK-003** — P2 remove Supabase completely | done |  | 2026-06-17 |
| **TASK-004** — P3 auth = static API token | done |  | 2026-06-18 |
| **TASK-005** — P4 jobs / Trigger.dev | done |  | 2026-06-18 |
| **TASK-006** — P5 Stripe/billing COMPLETE removal | done |  | 2026-06-18 |
| **TASK-007** — P6 trim: Google integration + analytics/Sentry + notifications | done |  | 2026-06-18 |
| **TASK-008** — P7 dashboard heaviness + website turbo-isolate | done |  | 2026-06-18 |
| **TASK-009** — P8 optional @mimir->@nexus-app rename | done |  | 2026-06-21 |
| **TASK-010** — Pre-existing biome lint debt cleanup (apps/api) | done |  | 2026-06-21 |
| **TASK-015** — Restore or retire execute-pm-agent-job (no-op stub after credit-gating removal in P5) | done |  | 2026-06-21 |
| **TASK-022** — Dashboard 500: next/dynamic ssr:false in Server Component layout (P7 latent, surfaced by rebuild) | done |  | 2026-06-20 |
| **TASK-023** — Seed script not idempotent: tasks block aborts on populated DB (duplicate permalink_id) | done |  | 2026-06-21 |

## FEAT-002 — Next Features (17/17 done, 100%)

| Task | Status | Owner | Updated |
|------|--------|-------|---------|
| **TASK-011** — Todos CRUD + drag-reorder + tags + project scope + attachments | done | forge-ui+forge-wire+palette+quill-ts | 2026-06-21 |
| **TASK-012** — Knowledge vault: Obsidian-compatible, wiki links, backlinks, FTS | done | forge-wire+forge-ui+palette+quill-ts | 2026-06-20 |
| **TASK-013** — Prompt library: variable detection, versioning, kbuddy seed | done | forge-ui+forge-wire+palette+quill-ts | 2026-06-20 |
| **TASK-014** — stdio MCP server wrapping todos/knowledge/prompts/tasks | done | hermes+quill-ts | 2026-06-21 |
| **TASK-017** — Todos bulk-selection bar: mount BulkOpsBar in todos-view | done |  | 2026-06-21 |
| **TASK-018** — MCP server install automation into ~/.claude/mcp.json | done |  | 2026-06-21 |
| **TASK-019** — MCP check_todo fuzzy/content search (id_or_search) | done |  | 2026-06-21 |
| **TASK-020** — Prompts list: clickable project-filter badge (Palette spec) | done |  | 2026-06-21 |
| **TASK-021** — Vault backend DB-integration tests (FTS rank, scan link-population, listBacklinks team-scope) | done |  | 2026-06-21 |
| **TASK-024** — TASK-012 polish: auto-save in-flight mutex (knowledge note editor) | done | forge-ui | 2026-06-21 |
| **TASK-025** — TASK-012 polish: optional safeResolve fail-fast in knowledge.updateVault | done | forge-wire | 2026-06-21 |
| **TASK-026** — Provision dashboard RTL/jsdom test harness (enable behavioral component tests) | done | forge-ui+forge-wire | 2026-06-21 |
| **TASK-027** — Drive dashboard/api project-wide tsc baseline to zero (92 pre-existing errors) | done | forge-ui+forge-wire | 2026-06-21 |
| **TASK-028** — Prod docker build: install production-only deps (vitest-4 native bindings flake the build) | done | hermes | 2026-06-21 |
| **TASK-029** — Build api tRPC-caller test harness + behavioral IDOR/team-scoping tests | done | forge-wire+quill-ts | 2026-06-21 |
| **TASK-030** — FEAT-001 spec: update @mimir/billing grep-lines to @nexus-app/billing (DEC-014) | done | forge-ui | 2026-06-21 |
| **TASK-039** — Knowledge tab redesign: grouped vault hub, filters, seeded notes, inspector | done | codex | 2026-06-23 |

## FEAT-003 — FEAT-003 (1/3 done, 33%)

| Task | Status | Owner | Updated |
|------|--------|-------|---------|
| **NEX-001** — Project Starter: real in-app interview chat → PRD → project creation | done | nexus | 2026-07-21 |
| **NEX-002** — Project Starter: host agent runtime (Claude Agent SDK + Codex OAuth) for full REV3 workshop | backlog |  | 2026-07-21 |
| **NEX-003** — Project Starter: kanban board materialization + task_dependencies (to-tickets phase) | backlog |  | 2026-07-21 |

## FEAT-004 — FEAT-004 (1/1 done, 100%)

| Task | Status | Owner | Updated |
|------|--------|-------|---------|
| **PLX-002** — Review, finish, verify, commit pi-agent uncommitted P0 batch | done |  | 2026-07-22 |

## FEAT-005 — FEAT-005 (0/1 done, 0%)

| Task | Status | Owner | Updated |
|------|--------|-------|---------|
| **NEX-004** — Commit Nexus 1.20.0 upgrade files as standalone checkpoint | todo |  | 2026-07-22 |

## Infrastructure / Housekeeping (8/10 done, 80%)

| Task | Status | Owner | Updated |
|------|--------|-------|---------|
| **PLX-001** — pi subagents route personas to amazon-bedrock (no key) instead of default GLM | backlog |  | 2026-07-21 |
| **TASK-016** — Repo hygiene: gitignore/remove Nexus-install backup dirt | done |  | 2026-06-21 |
| **TASK-031** — PRISM setup: repoint safe-working-dir from prism/ install tree to project root | cancelled | hermes | 2026-06-21 |
| **TASK-032** — SECURITY CRITICAL: cross-tenant IDORs in tasks.ts + agents.ts + task-executions + teams routers | done | forge-wire+quill-ts | 2026-06-21 |
| **TASK-033** — TASK-012 bug: knowledge_links not re-indexed on in-app editor save; stale-note deletion skipped when vault emptied | done | forge-wire+quill-ts | 2026-06-21 |
| **TASK-034** — TASK-011 GWT miss + a11y: row project pill not clickable to filter; filter pills missing focus ring | done | forge-ui+quill-ts | 2026-06-21 |
| **TASK-035** — TASK-013 prompts versioning: non-atomic snapshot+bump, swallowed errors, race, inline-only table/constraint | done | forge-wire | 2026-06-21 |
| **TASK-036** — TASK-012 schema: knowledge tables + content_fts defined inline in router, absent from schema.ts (drizzle-diff blind); note_id FK missing | done | atlas+forge-wire | 2026-06-21 |
| **TASK-037** — SECURITY: completeness-critic risks — Alexa webhook auth, MCP OAuth token-at-rest, migration reversibility, rate-limiter fail-open, full 42-router IDOR sweep | done | hermes+forge-wire | 2026-06-21 |
| **TASK-038** — Test-quality: MCP add_task tests are handler replicas (not real handler); feat-1 guards flaky+stale @mimir/integration regex | done | quill-ts | 2026-06-21 |

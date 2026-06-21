# FEAT-001 — Repurpose mimrai → Nexus (local-only cleanup)

## Overview / Goal

Strip the old mimrai multi-tenant SaaS task-tracker into a LOCAL-ONLY, single-user, API-accessed personal app renamed "Nexus", later wrapped in Electron by the owner. Remove external/SaaS services, replace storage locally, rename brand+paths+env, reduce heaviness. The internal package scope was renamed `@mimir/*` → `@nexus-app/*` in P8 (TASK-009, commit `60e3bd5`, DEC-014 supersedes DEC-002's keep-`@mimir` decision).

**Stack:** Bun + Turborepo monorepo under `app/`; apps `api` (Hono + tRPC + AI SDK), `dashboard` (Next 15), `website` (keep, isolate), `desktop` (Electron, keep for later); Postgres/pgvector + Redis local via docker-compose.

**Why now:** The codebase already runs locally (Better Auth + local pgvector replacing Supabase auth+DB), but three heavyweight SaaS layers remain live or stubbed: Supabase STORAGE (live — not dead), Stripe (stubbed via recursive Proxy), and Trigger.dev (stubbed locally). Removing them eliminates infrastructure risk, dependency footprint, and the misleading `MIMRAI_LOCAL_DEV` guard rails that exist only to suppress production services. The orchestrator framework in `.claude/` and `.memory/` is already named "Nexus" — this feature renames the app to match.

---

## Acceptance Criteria (Given/When/Then)

**Brand rename completeness**
Given: all source files, metadata, seed data, and docker-compose configs contain "Mimrai" or "mimrai" brand text
When: Phase 0 brand-text pass completes (77 refs across 12 files)
Then: `grep -r "Mimrai\|mimrai\.com\|mimrai-org" app/ mcp-server/` returns zero matches in user-facing strings (hardcoded internal DB names like `mimrai-pg-data` volume are exempt)

**Supabase removal after local-storage swap**
Given: Supabase STORAGE createAdminClient() is live and called by `users.ts`, `attachments.ts`, `add-task-attachment.ts`, and `mattermost/init.ts`
When: Phase 1 FileStorageAdapter is implemented and all 7 dependent files migrated
Then: `grep -r "createAdminClient\|@supabase/supabase-js" app/apps app/packages` returns zero matches, and avatar + attachment uploads succeed against local disk storage

**Stripe complete removal**
Given: 29 stripeClient calls across billing router, teams router, webhook handler, plan-feature middleware, and 2 job types
When: Phase 5 Stripe teardown completes (all 10 dependent files cleared, `app/packages/billing/` deleted, `stripe` dep removed)
Then: `grep -r "stripeClient\|@nexus-app/billing\|stripe" app/apps app/packages` returns zero matches, and team create/update/delete succeed without billing side effects

**Env-flag rename**
Given: `MIMRAI_LOCAL_DEV` and `NEXT_PUBLIC_MIMRAI_LOCAL_DEV` are read in 7 shared packages and set in docker-compose.local.yaml (3 lines) and `.env.example` (2 lines)
When: Phase 0 atomic env-flag rename commit lands
Then: `grep -r "MIMRAI_LOCAL_DEV\|NEXT_PUBLIC_MIMRAI_LOCAL_DEV" app/ mcp-server/` returns zero matches, and `NEXUS_LOCAL_DEV=1` correctly stubs embedding, realtime, jobs, notifications, events, and billing stubs

**App boots with NEXUS_LOCAL_DEV**
Given: docker-compose.local.yaml sets `NEXUS_LOCAL_DEV: "1"` for both api and dashboard containers after Phase 0
When: `docker compose -f app/docker-compose.local.yaml up` is run on the renamed codebase
Then: api server starts on port 3003, dashboard starts on port 3000, `GET /api/auth/session` returns a valid local-dev-user session, and no runtime errors reference `MIMRAI_*` env keys

**Dead mount removed**
Given: `app/docker-compose.local.yaml` L77 mounts `/Users/john.keeney/mimrai/nexus-orchestrator:/host/nexus` — a path that does not exist and is never read inside the container
When: Phase 0 path-fix commit removes that line
Then: `docker compose config` parses cleanly with no missing bind-mount warnings, and no container code references `/host/nexus`

---

## Constitution Check

- `Article I` (TDD): pure-rename phases (P0/P7/P8) verified by grep-sweeps + `rtk tsc` + `rtk lint`; logic/schema phases (P1/P2/P3/P5) require feat-1-tagged Quill tests written before Forge implements adapters or migration logic.
- `Article X` (RCA): every removal phase documents the full blast-radius list (drawn from synthesis verdicts) and confirms zero orphaned imports before marking done; no silent deletions.
- `Article XII` (deploy via human handoff): each phase ends with a `## Deploy step` block naming the Docker restart action + verification command; owner approves before rebuild; Nexus does not deploy autonomously.
- `Article XIII` (parallel-first): P0 env-flag rename is single-threaded (shared across api + dashboard — sequential atomic commit required); all other phases can be dispatched in parallel where dependency graph allows.
- `Article XIV` (session-branch commit-as-checkpoint): one focused commit per phase; each commit is the rollback unit; no per-task feature branches; Forge commits on session branch and does not push.

---

## Architecture Summary

**Monorepo layout:** Bun + Turborepo; root `app/` workspace with 4 apps (`api`, `dashboard`, `website`, `desktop`) and shared packages under `@nexus-app/*` (renamed from `@mimir/*` in P8 — see DEC-014).

| App | Runtime | Role | Decision |
|---|---|---|---|
| `apps/api` | Hono 4.9.8 + tRPC + AI SDK 6 | Backend: 38 tRPC routers, 12 REST sub-routes, 40+ AI tools, MCP endpoint | KEEP — primary backend |
| `apps/dashboard` | Next 15 App Router | Primary UI: Home, Triage, Projects, Inbox, Documents, Recurring | KEEP — primary client |
| `apps/website` | Next (static) | Public marketing; "paused" landing page; no cross-app imports | KEEP but turbo-isolate (P7) |
| `apps/desktop` | Electron + Electron Forge | Wrapper loading `localhost:3000` via WebContentsView | KEEP skeleton; Electron build deferred |

**Infrastructure (local only):** pgvector:pg16 image + local Redis via docker-compose. No Upstash, no Supabase DB, no Supabase auth — Better Auth + pgvector already active.

**Critical correction from synthesis:** Supabase STORAGE is LIVE, not dead. `createAdminClient()` is actively imported and called in 4 files (`apps/api/src/rest/routers/users.ts`, `attachments.ts`, `ai/tools/add-task-attachment.ts`, `packages/integration/src/mattermost/init.ts`). `MIMRAI_LOCAL_DEV` does NOT stub storage calls. This makes local-disk FileStorageAdapter the **mandatory prerequisite** for Supabase removal — replace-before-remove ordering is binding.

**Auth:** Better Auth session check (`apps/api/src/rest/middleware/auth.ts`) already handles both authenticated sessions and `MIMRAI_LOCAL_DEV` local-dev-user bypass. Phase 3 replaces `MIMRAI_LOCAL_DEV` bypass with a static API token gate (`NEXUS_API_TOKEN`).

**Scope renamed (P8 executed):** the internal package scope was renamed `@mimir/*` → `@nexus-app/*` (TASK-009, commit `60e3bd5`, 2026-06-21; ~403 files touched). DEC-014 supersedes DEC-002's original keep-`@mimir` decision — once the app was committed to as a long-lived local-only product, the brand-aligned scope was worth the coordinated rename. Env-flag and user-facing brand text were renamed earlier in P0.

---

## Phases (P0–P8)

### P0 — Rename paths, env flags, brand text; remove dead mount

**Goal:** No behavior change — purely cosmetic. Codebase compiles and runs identically after this phase; all `MIMRAI_*` env keys become `NEXUS_*`; all hardcoded `/Users/john.keeney/mimrai/` paths become `/Users/john.keeney/nexus-task-tracker/`; dead docker-compose mount removed.

**Acceptance criteria:**
- `grep -r "MIMRAI_LOCAL_DEV" app/ mcp-server/` returns zero matches after atomic rename
- `grep -r "/Users/john.keeney/mimrai/" app/ mcp-server/` returns zero non-exempt matches (sibling-app mounts `ai-interaction-dash` and `elevenlabs-eval-dash` are exempt)
- `grep -r "Mimrai\|mimrai\.com\|mimrai-org" app/ mcp-server/` returns zero user-facing matches; `docker compose -f app/docker-compose.local.yaml config` parses without bind-mount warnings

**Files (see `findings.md` Category A, C, D for full line-level list):**
- `app/docker-compose.local.yaml` — L10, L59, L74, L77 (remove), L79 (path TBD), L103–L104
- `mcp-server/server.ts` — L20 hardcoded path; L16–23 env var names
- 7 shared packages reading `MIMRAI_LOCAL_DEV` (embedding, realtime, jobs, notifications, events ×2, billing)
- 12 brand-text files (77 refs) including website metadata, seed emails, domain fallbacks

**Stage order (within P0):**
1. Stage 1 — atomic env-flag rename (all 16 refs in single commit)
2. Stage 2 — path fixes + dead-mount removal
3. Stage 3 — brand text (lower risk; visual review required)

**Path decisions resolved (DEC-002):**
- `/Users/john.keeney/mimrai/` → `/Users/john.keeney/nexus-task-tracker/`
- `/Users/john.keeney/mimrai-knowledge/` → `/Users/john.keeney/nexus-knowledge/` (keep as sibling dir)

---

### P1 — Local-disk FileStorageAdapter

**Goal:** Implement a local filesystem storage adapter that satisfies the same interface as the Supabase storage client, replacing all 7 dependent call sites. This is the gating prerequisite for P2.

**Acceptance criteria:**
- `FileStorageAdapter` with `upload(bucket, path, file) → Promise<string>` and `getPublicUrl(bucket, path) → string` implemented in `app/packages/storage/`
- All 7 files updated: `users.ts`, `attachments.ts`, `imports.ts`, `add-task-attachment.ts`, `packages/integration/src/slack/handle.ts`, `mattermost/init.ts`, `whatsapp/handle.ts`
- `STORAGE_ROOT` env var controls base path (default: `/Users/john.keeney/nexus-task-tracker/storage`)
- feat-1 unit tests: adapter uploads to STORAGE_ROOT, getPublicUrl returns correct local path

---

### P2 — Remove Supabase

**Goal:** Delete all Supabase client code, config directories, SDK dependency, and env keys. Safe only after P1 complete and tested.

**Acceptance criteria:**
- `apps/api/src/lib/supabase.ts` and `packages/jobs/src/utils/supabase.ts` deleted
- `app/supabase/` and `apps/api/supabase/` directories deleted
- `@supabase/supabase-js` removed from all `package.json` files; `bun install` passes
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY` removed from docker-compose.local.yaml and `.env.example`

**No table changes** — DB already migrated off Supabase to local pgvector. Only the client code and config dirs are removed. Existing attachment URLs in DB pointing to old Supabase storage buckets must be migrated via a one-time script (include in this phase).

---

### P3 — Auth: static API token

**Goal:** Replace the `MIMRAI_LOCAL_DEV` local-dev-user auth bypass in `apps/api/src/rest/middleware/auth.ts` with a static API token check (`NEXUS_API_TOKEN` header). Keeps Better Auth sessions for dashboard browser access; adds a machine-access path for local scripts and Electron.

**Acceptance criteria:**
- `auth.ts` middleware accepts `Authorization: Bearer ${NEXUS_API_TOKEN}` as an authenticated local-owner identity
- Dashboard browser sessions continue to use Better Auth cookie sessions unmodified
- feat-1 test: request with valid `NEXUS_API_TOKEN` is authorized; request without token or with wrong token is rejected with 401

---

### P4 — Jobs: replace Trigger.dev

**Goal:** Replace Trigger.dev cloud SDK with a local job scheduler (Bun's native `Bun.cron` or a lightweight `node-cron` wrapper) for the 8+ scheduled task types (task templates, recurring tasks, daily digests, PM agent, Gmail sync, etc.).

**Acceptance criteria:**
- `app/packages/jobs/trigger.config.ts` deleted; `@trigger.dev/sdk` removed from all `package.json` files
- All `.trigger()` dispatch call sites rewritten to local scheduler enqueue
- feat-1 test: recurring task job enqueues and fires without Trigger.dev SDK
- `trigger dev` and `trigger deploy` npm scripts removed from root `package.json`

---

### P5 — Stripe: complete removal

**Goal:** Delete the entire billing layer — `packages/billing/`, `apps/api/src/lib/payments.ts`, `rest/webhooks/stripe.ts`, billing tRPC router, plan-feature middleware, and all 29 `stripeClient` call sites. Drizzle migration to null out billing columns; drop `creditLedger` if unused.

**Acceptance criteria:**
- `app/packages/billing/` directory deleted; `stripe` npm dependency removed from all workspaces; `bun install` passes
- `apps/api/src/lib/payments.ts` and `rest/webhooks/stripe.ts` deleted
- `billing.ts` tRPC router deleted; import removed from `routers/index.ts`
- `plan-feature.ts` middleware deleted; all 4 integration routers + 2 job types that import it updated
- `apps/api/src/trpc/routers/teams.ts` — all `stripeClient.customers.*` calls removed; team CRUD succeeds
- Drizzle migration: `customerId`, `subscriptionId`, `canceledAt` columns NULLed in `teams` table; `credit_ledger` and `credit_balance` tables dropped (confirm no dashboard UI depends on them before drop)
- feat-1 guard test: `grep -r "stripeClient\|@nexus-app/billing\|stripe" app/apps app/packages` returns zero matches (the implemented guard, `app/apps/dashboard/__tests__/feat-1-stripe-removal-guard.test.ts`, asserts absence of `stripeClient` / `@nexus-app/billing` / `new Stripe` under the current scope)

---

### P6 — Trim: Google integrations, analytics, Sentry, notifications

**Goal:** Remove Google Gmail + Google Calendar integrations (REST routers, AI tools, OAuth flow, jobs), PostHog analytics SDK, Sentry error tracking, and the optional notifications package (per DEC-002: keep GitHub + chat/Slack/Mattermost/WhatsApp, remove Google).

**Acceptance criteria:**
- `rest/routers/gmail.ts` and `rest/routers/google-calendar.ts` deleted; imports removed from REST router index
- AI tools for Gmail/Calendar removed from `tool-registry.ts`; `googleapis` dep removed
- `posthog-js` dep removed; `apps/api/src/lib/posthog.ts` and `apps/api/src/lib/instrument.ts` (Sentry) deleted
- `apps/packages/notifications/` removal steps executed (9 steps from synthesis verdict)
- feat-1 test: `grep -r "googleapis\|posthog\|sentry\|@sentry" app/apps app/packages` returns zero matches

---

### P7 — Dashboard heaviness + website turbo-isolate

**Goal:** Reduce dashboard cold-start overhead by splitting the monolithic shared layout provider chain; conditionally mount DndProvider only on `/todos` and `/projects`; defer FocusSessionLoader; address the 3× unconditional `useQuery` in project-relationships-sidebar. Isolate `apps/website` from the Turborepo build graph so it does not block api/dashboard builds.

**Acceptance criteria:**
- `TodoDndProvider` moved from shared `(navigation)/layout.tsx` to `/todos` and `/projects` subtree roots
- `FocusSessionLoader` wrapped in `next/dynamic(ssr: false)` at root lazy boundary
- Project relationships sidebar queries gated on `!collapsed && isVisible`
- `apps/website` excluded from default `turbo build` pipeline (workspace script updated or `!apps/website` exclusion added)
- `rtk tsc` + `rtk lint` pass; no regression on drag-drop in Todos or Projects

---

### P8 — @mimir → @nexus-app scope rename (DONE)

**Goal:** Rename internal `@mimir/*` package scope to `@nexus-app/*` (not `@nexus/*` — collision with orchestrator framework).

**Acceptance criteria:**
- All workspace `package.json` files updated; all import statements updated in a single coordinated pass
- `bun install` + `rtk tsc` + `rtk lint` pass with zero type errors
- No remaining `@mimir/` import refs in source

**Status: DONE — TASK-009, commit `60e3bd5` (2026-06-21), ~403 files renamed `@mimir/` → `@nexus-app/`. This reverses the original P8 "deferred" stance: DEC-014 superseded DEC-002's keep-`@mimir` decision once Nexus was committed to as a long-lived product.**

---

## Schema Changes

The billing-related tables read from `app/packages/db/src/schema.ts` (L107–L185, L136–L185):

```sql
-- Representative excerpt from app/packages/db/src/schema.ts
-- Billing fields on the teams table (L117–L134)
CREATE TABLE teams (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  slug         TEXT NOT NULL UNIQUE,
  prefix       TEXT NOT NULL,
  description  TEXT,
  email        TEXT NOT NULL,
  plan         plans,             -- enum: 'free' | 'team'
  subscription_id  TEXT,          -- Stripe subscription ID
  timezone     TEXT NOT NULL DEFAULT 'UTC',
  locale       TEXT NOT NULL DEFAULT 'en-US',
  customer_id  TEXT,             -- Stripe customer ID
  canceled_at  TIMESTAMP,        -- Stripe cancellation timestamp
  created_at   TIMESTAMP NOT NULL DEFAULT now(),
  updated_at   TIMESTAMP NOT NULL DEFAULT now()
);

-- Credit balance table (L136–L157) — companion to creditLedger
CREATE TABLE credit_balance (
  id              TEXT PRIMARY KEY,
  team_id         TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  balance_cents   INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  CONSTRAINT unique_credit_balance_per_team UNIQUE (team_id)
);

-- Credit ledger table (L159–L185) — Stripe payment event log
CREATE TABLE credit_ledger (
  id                        TEXT PRIMARY KEY,
  team_id                   TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  type                      credit_movement_type NOT NULL, -- enum: purchase|usage|refund|adjustment|promo
  amount_cents              INTEGER NOT NULL,
  stripe_payment_intent_id  TEXT,
  stripe_event_id           TEXT UNIQUE,
  metadata                  JSONB,
  created_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);
```

**P5 Stripe teardown SQL intent (Drizzle migration):**

```sql
-- Null out Stripe-specific columns on teams (non-destructive first step)
UPDATE teams SET customer_id = NULL, subscription_id = NULL, canceled_at = NULL;

-- Remove the plan enum column (replace with unlocked default if needed)
ALTER TABLE teams DROP COLUMN IF EXISTS plan;
ALTER TABLE teams DROP COLUMN IF EXISTS customer_id;
ALTER TABLE teams DROP COLUMN IF EXISTS subscription_id;
ALTER TABLE teams DROP COLUMN IF EXISTS canceled_at;

-- Drop credit tables (confirm no dashboard UI reads these first)
DROP TABLE IF EXISTS credit_ledger;
DROP TABLE IF EXISTS credit_balance;
DROP TYPE IF EXISTS credit_movement_type;
DROP TYPE IF EXISTS plans;
```

**P2 Supabase residue (no table changes — DB already migrated):**

- Drop `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY` from `docker-compose.local.yaml` and `app/.env.example`
- Delete `app/supabase/` directory (CLI config artifacts, not migrations)
- Delete `app/apps/api/supabase/` directory (config.toml)
- One-time data migration: rewrite existing `attachments` JSONB arrays and `user.image` URLs from Supabase bucket format (`https://kong:8000/storage/v1/...`) to new local path format (`/storage/...` or served via API static route)

---

## Test Strategy

**Pure-rename phases (P0, P7, P8):** No behavioral change — verified by:
- `rtk tsc` (zero type errors)
- `rtk lint` (zero lint violations)
- grep-sweeps as specified in each phase's acceptance criteria

**Logic/schema phases (P1, P2, P3, P5, P6):** Quill writes feat-1-tagged tests before Forge implements. Test path convention: `app/**/__tests__/feat-1*.test.ts` (or `*.spec.ts`). Examples:

- `app/packages/storage/__tests__/feat-1-file-storage-adapter.test.ts` — unit tests for FileStorageAdapter upload/getPublicUrl/delete against a temp STORAGE_ROOT
- `app/apps/api/src/__tests__/feat-1-auth-token.test.ts` — middleware accepts valid NEXUS_API_TOKEN, rejects invalid
- `app/apps/dashboard/__tests__/feat-1-stripe-removal-guard.test.ts` — asserts the billing package is deleted and zero import of `stripeClient` or `@nexus-app/billing` remains in production source
- `app/apps/api/src/__tests__/feat-1-supabase-removal-guard.test.ts` — asserts zero import of `@supabase/supabase-js` or `createAdminClient` in compiled output
- `app/packages/jobs/__tests__/feat-1-local-scheduler.test.ts` — recurring job enqueues and fires without Trigger.dev

Guard tests (grep-assertion pattern): run as part of `rtk lint` or as standalone Bun scripts; fail CI if forbidden imports re-appear.

---

## Decisions

**DEC-001** (architecture): KEEP all apps; build `web/dashboard`; Electron skeleton kept but build deferred; dead docker-compose bind-mount at L77 (`/Users/john.keeney/mimrai/nexus-orchestrator:/host/nexus`) removed — orchestrator lives in `.claude/`, not a sibling directory. `apps/website` kept but turbo-isolated in P7.

**DEC-002** (gate-locked owner decisions): storage = local-disk FileStorageAdapter with `STORAGE_ROOT` env var; auth = static API token (`NEXUS_API_TOKEN`) + keep dashboard Better Auth cookie sessions; scope = KEEP `@mimir/*` (brand text rename only); integrations = KEEP GitHub + chat (Slack/Mattermost/WhatsApp), REMOVE Google (Gmail + Google Calendar).

> **SUPERSEDED (scope clause only):** the `scope = KEEP @mimir/*` decision was reversed by **DEC-014**. The package scope was renamed `@mimir/*` → `@nexus-app/*` in P8 (TASK-009, commit `60e3bd5`, 2026-06-21). All other DEC-002 clauses (storage / auth / integrations) stand.

**DEC-014** (scope rename): rename internal package scope `@mimir/*` → `@nexus-app/*` across the workspace (~403 files, commit `60e3bd5`). Supersedes DEC-002's keep-`@mimir` clause — once the app was adopted as a long-lived local-only product, the brand-aligned scope justified the one-time coordinated rename. `@nexus-app/*` (not `@nexus/*`) avoids collision with the Nexus orchestrator framework.

**Path mappings:**
- Repository: `/Users/john.keeney/mimrai` → `/Users/john.keeney/nexus-task-tracker`
- Knowledge vault: `/Users/john.keeney/mimrai-knowledge` → `/Users/john.keeney/nexus-knowledge`

---

## Do-Not-Touch

| Item | Reason |
|---|---|
| ~~`@mimir/*` package scope~~ | RENAMED to `@nexus-app/*` in P8 (TASK-009, commit `60e3bd5`); DEC-014 superseded the original keep-`@mimir` stance. No longer do-not-touch. |
| `mimrai-pg-data` Docker volume | Renaming orphans live database data |
| `mimrai` internal DB user/name/password | DB internals; low-priority; rename requires DB migration and is out of scope |
| Sibling-app bind-mounts in docker-compose | `/Users/john.keeney/ai-interaction-dash/.claude` and `/Users/john.keeney/elevenlabs-eval-dash/.claude` — external apps, not in scope |
| `.claude/`, `.memory/`, `nexus-broker/`, `prism/` | Orchestrator scaffolding; owned by Nexus framework |
| `docs/CONSTITUTION.md`, `docs/NEXUS-*.md` | Governance docs; owned by Nexus framework |

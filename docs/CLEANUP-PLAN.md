# mimrai → Nexus Cleanup Plan

Companion to [docs/features/FEAT-001-repurpose-nexus.md](./features/FEAT-001-repurpose-nexus.md). Concise human reference — use FEAT-001 for full acceptance criteria, schema SQL, and test strategy.

---

## 8-Phase Checklist

- [ ] **P0** — Rename paths, env flags (`MIMRAI_LOCAL_DEV` → `NEXUS_LOCAL_DEV`), brand text (77 refs); remove dead docker-compose bind-mount (L77). Three-stage atomic order: env flags → paths → brand text. No behavior change.
- [ ] **P1** — Implement local-disk `FileStorageAdapter` in `app/packages/storage/`; migrate all 7 Supabase storage call sites. `STORAGE_ROOT` env var controls base path. **Gate for P2.**
- [ ] **P2** — Remove Supabase: delete `supabase.ts` clients, `app/supabase/` + `apps/api/supabase/` dirs, `@supabase/supabase-js` dep, `SUPABASE_*` env keys. Run one-time URL migration for stored attachment/avatar paths.
- [ ] **P3** — Auth: replace `MIMRAI_LOCAL_DEV` local-dev-user bypass with static `NEXUS_API_TOKEN` header; keep Better Auth cookie sessions for dashboard. **Gate for P5** (feature-gating decision blocks Stripe removal).
- [ ] **P4** — Jobs: replace Trigger.dev with local Bun/node-cron scheduler; rewrite all `.trigger()` dispatch calls; remove `@trigger.dev/sdk` dep.
- [ ] **P5** — Stripe complete removal: delete `packages/billing/`, `payments.ts`, `webhooks/stripe.ts`, billing tRPC router, plan-feature middleware; Drizzle migration to NULL `customerId`/`subscriptionId`/`canceledAt` + drop `creditLedger` and `creditBalance` tables.
- [ ] **P6** — Trim: remove Google integrations (Gmail + Calendar REST routers, AI tools, `googleapis` dep, OAuth flow, related jobs); remove PostHog analytics (`posthog-js`); remove Sentry (`instrument.ts`); remove notifications package (9-step teardown).
- [ ] **P7** — Dashboard heaviness: split monolithic `(navigation)/layout.tsx` provider chain; conditional `DndProvider`; lazy `FocusSessionLoader`; gated relationship-sidebar queries. Turbo-isolate `apps/website` from default build pipeline.
- [ ] **P8** — *(Deferred / owner must unlock)* Optional `@mimir/*` → `@nexus-app/*` scope rename across 20 `package.json` files and 886 import statements.

---

## Gate Decisions (DEC-002, owner-locked)

| Decision | Choice |
|---|---|
| File storage replacement | Local-disk `FileStorageAdapter`; `STORAGE_ROOT` env var |
| Auth strategy | Static `NEXUS_API_TOKEN` for API/scripts; Better Auth cookie sessions for dashboard |
| Internal package scope | KEEP `@mimir/*` — brand text only renamed (P8 deferred) |
| Integrations to keep | GitHub, Slack, Mattermost, WhatsApp |
| Integrations to remove | Google (Gmail + Calendar) |

---

## Disposition Table

| Service / Component | Status | Phase | Notes |
|---|---|---|---|
| Supabase STORAGE | **Replace then remove** | P1 → P2 | LIVE — createAdminClient() called in 4 files; local-disk adapter is hard prerequisite |
| Supabase auth/DB | Already removed | — | Better Auth + pgvector already active |
| Stripe / `packages/billing/` | **Remove completely** | P5 | Stubbed locally via Proxy; 29 call sites to clear; drop `creditLedger` + `creditBalance` |
| Trigger.dev | **Replace with local scheduler** | P4 | Stubbed locally; 10+ job types to rewrite |
| Google Gmail | **Remove** | P6 | DEC-002: remove Google integrations |
| Google Calendar | **Remove** | P6 | DEC-002: remove Google integrations |
| PostHog analytics | **Remove** | P6 | Stubbed locally; `posthog-js` dep removed |
| Sentry | **Remove** | P6 | Optional error tracking; `instrument.ts` deleted |
| Notifications package | **Remove** | P6 | Optional; activity dispatch call sites cleaned first |
| Slack | **Keep** | — | DEC-002: keep chat integrations |
| Mattermost | **Keep** | — | DEC-002: keep chat integrations |
| WhatsApp / Twilio | **Keep** | — | DEC-002: keep chat integrations |
| GitHub / Octokit | **Keep** | — | Core integration |
| Better Auth | **Keep** | — | Already primary auth |
| Local Postgres + pgvector | **Keep** | — | Core data store |
| Local Redis | **Keep** | — | Core cache/pub-sub |
| Resend email | **Keep (assess in P6)** | P6 | Needed by Better Auth email verification; remove only if auth moves to token-only |
| `apps/website` | **Keep, turbo-isolate** | P7 | No cross-app imports; safe to exclude from default build |
| `apps/desktop` | **Keep skeleton** | — | Electron build deferred; preload.ts empty |
| `@mimir/*` scope | **Keep (P8 deferred)** | P8 | 886 refs; rename only if owner unlocks |
| `mimrai-pg-data` Docker volume | **Do not touch** | — | Rename orphans live data |
| `.claude/`, `.memory/` | **Do not touch** | — | Nexus orchestrator scaffolding |

---

## Path + Knowledge Vault Mappings

| Old path | New path | Where referenced |
|---|---|---|
| `/Users/john.keeney/mimrai/` | `/Users/john.keeney/nexus-task-tracker/` | docker-compose.local.yaml L10, L74; db script comments |
| `/Users/john.keeney/mimrai-knowledge/` | `/Users/john.keeney/nexus-knowledge/` | docker-compose.local.yaml L79; mcp-server/server.ts L20 |
| `mimrai/nexus-orchestrator:/host/nexus` (L77) | *(removed)* | Dead bind-mount — path never existed; no container reads `/host/nexus` |
| `/Users/john.keeney/ai-interaction-dash/.claude` | *(keep as-is)* | External sibling app — out of scope |
| `/Users/john.keeney/elevenlabs-eval-dash/.claude` | *(keep as-is)* | External sibling app — out of scope |

---

## Replace-Before-Remove Ordering

**Supabase storage removal is blocked until local-disk FileStorageAdapter is live and tested (P1 must complete before P2).**

The synthesis report (w8j85v40n.output) confirmed that upstream maps incorrectly classified Supabase storage as "dead/migrated". It is LIVE: `createAdminClient()` is actively imported and called in `users.ts`, `attachments.ts`, `add-task-attachment.ts`, and `mattermost/init.ts`. Removing Supabase without a storage replacement causes runtime failure on first file upload with `createAdminClient not defined`.

Recommended phase sequencing for safety:
1. P0 (rename) — no infra changes
2. P1 (storage adapter) — implement replacement
3. P2 (Supabase removal) — safe only after P1 verified
4. P3 (auth token) — parallel with P4 once P2 done
5. P4 (jobs) — parallel with P3
6. P5 (Stripe) — requires P3 (feature-gating decision) to be settled first
7. P6 (trim) — parallel with P5 where dep graph allows
8. P7 (heaviness) — independent; can run any time after P0
9. P8 (scope rename) — explicitly deferred; owner unlock required

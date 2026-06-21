# Nexus — Local Dev Setup

Nexus is normally deployed across Supabase (cloud) + Vercel + Fly.io with paid integrations. For local use this project runs a stripped-down, single-container Nexus stack. Sign-in is bypassed — a single seeded user/team is hard-wired and you land directly on the dashboard.

## URLs

| Surface | URL |
|---|---|
| **Dashboard** | http://localhost:5179 (redirects to `/team/local-dev`) |
| Nexus API | http://localhost:3003 |

## Containers (4 services)

| Container | Image | Host port |
|---|---|---|
| nexus-postgres | pgvector/pgvector:pg16 | 55432 |
| nexus-redis | redis:7-alpine | 56379 |
| nexus-api | (built locally) | 3003 |
| nexus-dashboard | (built locally) | 5179 |

Bring up: `cd app && docker compose -f docker-compose.local.yaml up -d`

> Note: Supabase was adopted in iter 3 (13-service stack) but rolled back in iter 7 to single-container pgvector — Nexus only needed Postgres + pgvector, so the 12 other Supabase services were dropped as dead weight. The legacy stack is preserved at `docs/archive/upstream/supabase-stack/` for reference.

## Quick start

```bash
cd app

# One-time image tag (the local Dockerfiles use `local-mimrai/node:20-alpine` as their base):
docker tag node:20-alpine local-mimrai/node:20-alpine

# Bring up the 4-service stack:
docker compose -f docker-compose.local.yaml up -d --build

# Push schema + seed the default user (one-time, after first start). The
# postgres container has DB=mimrai/user=mimrai/password=mimrai (see
# docker-compose.local.yaml) on host port 55432:
docker run --rm --network app_default \
  -e DATABASE_URL='postgresql://mimrai:mimrai@postgres:5432/mimrai' \
  -e NEXUS_LOCAL_DEV=1 \
  -w /app/packages/db \
  app-api \
  /usr/local/bin/bun x drizzle-kit push --force

docker run --rm --network app_default \
  -e DATABASE_URL='postgresql://mimrai:mimrai@postgres:5432/mimrai' \
  -e NEXUS_LOCAL_DEV=1 \
  -w /app/packages/db \
  app-api \
  /usr/local/bin/bun run src/seed-local-dev.ts

# Open http://localhost:5179 — you'll land directly on /team/local-dev
```

> Naming note: the Postgres DB/user/password (`mimrai`) and the local base-image tag (`local-mimrai/node:20-alpine`) deliberately retain the `mimrai` string — they are internal identifiers, not the workspace scope. The npm workspace scope was renamed `@mimir/*` → `@nexus-app/*` (DEC-014), but these container/image identifiers were intentionally left as-is.

## Seeded identity

| Field | Value |
|---|---|
| User id | `local-dev-user` |
| User email | `dev@nexus.local` (the value `seed-local-dev.ts` inserts) |
| Team id | `local-dev-team` |
| Team slug | `local-dev` |
| Plan | `team` |
| Role | `owner` |

These IDs are referenced in three places that **must agree**:

1. `app/packages/db/src/seed-local-dev.ts` — inserts the rows (email `dev@nexus.local`)
2. `app/apps/dashboard/src/lib/get-session.ts` — returns this user to SSR consumers when `NEXUS_LOCAL_DEV=1`
3. `app/apps/api/src/lib/context.ts` — injects this user as the tRPC ctx when there's no real session and `NEXUS_LOCAL_DEV=1`

## Auth bypass

When `NEXUS_LOCAL_DEV=1`:

- `app/apps/dashboard/src/app/page.tsx` redirects `/` → `/team/local-dev` instead of `/sign-in`.
- `app/apps/dashboard/src/app/team/[team]/layout.tsx` skips the `if (!session?.user?.teamSlug) redirect("/sign-in")` gate and the `switchTeam` mutation, since there's only one seeded team.
- `app/apps/dashboard/src/lib/get-session.ts` returns the seeded user verbatim.
- `app/apps/api/src/lib/context.ts` reads `auth.api.getSession()`, and if the result is null **falls back to the seeded user id** (`local-dev-user`) instead of returning `{ session: null }`. The protectedProcedure check in `app/apps/api/src/trpc/init.ts` is then satisfied.

Without `NEXUS_LOCAL_DEV=1` (i.e. in production) every one of these code paths behaves byte-identically to upstream — the auth gates and redirects are intact.

## URL split for SSR vs browser

The dashboard renders Next.js SSR inside its container. From there `localhost:3003` is the container itself, not the api. `apps/dashboard/src/lib/auth-client.ts` and `apps/dashboard/src/utils/trpc.ts` were extended (also gated on `NEXUS_LOCAL_DEV=1`) to use `NEXUS_SSR_SERVER_URL=http://api:3003` for server-side fetches while keeping `NEXT_PUBLIC_SERVER_URL=http://localhost:3003` for the browser.

## Stubbed integrations

These remain stubbed because the user does not need them locally:

| Service | How stubbed | File |
|---|---|---|
| Resend (email) | Recursive Proxy returns `{ data: { id: "stub-local-dev" }, error: null }` | `apps/api/src/lib/resend.ts` |
| OpenPanel (analytics) | No-op | `packages/events/src/server.ts` |
| Trigger.dev | `configure` skipped; `tasks.trigger()` returns stub id | `apps/api/src/lib/trigger.ts`, `packages/jobs/src/init.ts` |
| OpenAI embeddings | Returns 768-dim zero vector | `packages/embedding/src/embeddings/task.ts` |
| Upstash Redis + Ratelimit | In-process Map-backed fake | `packages/realtime/src/redis-client.ts` |

All gated on `NEXUS_LOCAL_DEV=1` — drop that flag and code paths revert to upstream.

> **Billing/Stripe is not stubbed — it was removed entirely** in FEAT-001. There is no `packages/billing` and no `apps/api/src/lib/payments.ts`; the removal is locked by `apps/dashboard/__tests__/feat-1-stripe-removal-guard.test.ts`. Sentry was likewise trimmed (no `instrument.ts` / `sentry.*.config.ts`) — see `apps/dashboard/__tests__/feat-1-integrations-trim.test.ts`.

## Tearing down

```bash
docker compose -f app/docker-compose.local.yaml down -v
```

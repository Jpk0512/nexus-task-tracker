# mimrai — Local Dev Setup

Mimrai is normally deployed across Supabase (cloud) + Vercel + Fly.io with paid integrations. For local use this project runs a **self-hosted Supabase stack** plus a stripped-down mimrai stack. Sign-in is bypassed — a single seeded user/team is hard-wired and you land directly on the dashboard.

## URLs

| Surface | URL |
|---|---|
| **Dashboard** | http://localhost:5179 (redirects to `/team/local-dev`) |
| Mimrai API | http://localhost:3003 |
| Supabase Kong gateway | http://localhost:8000 |
| Supabase Studio (basic auth) | http://localhost:8000/ — user `supabase`, password from `supabase-stack/.env` |

## Containers

```
supabase-stack/   (docker compose project: supabase)
  ├─ supabase-db          (postgres 15 with pgvector + pg_graphql)
  ├─ supabase-kong        (API gateway, port 8000)
  ├─ supabase-auth        (GoTrue)
  ├─ supabase-rest        (PostgREST)
  ├─ supabase-realtime
  ├─ supabase-storage
  ├─ supabase-meta
  ├─ supabase-studio
  ├─ supabase-vector
  ├─ supabase-analytics
  ├─ supabase-pooler
  ├─ supabase-imgproxy
  └─ supabase-edge-functions

app/   (docker compose project: app)
  ├─ mimrai-redis         (local Redis)
  ├─ mimrai-api           (Hono + tRPC, port 3003)
  └─ mimrai-dashboard     (Next.js 16, port 5179)
```

The mimrai api/dashboard attach to **both** networks (`app_default` for redis, `supabase_default` for db + kong). The mimrai api uses Supabase's postgres directly via Drizzle (`DATABASE_URL=postgresql://postgres:...@db:5432/postgres`), and the admin Supabase client points at kong (`SUPABASE_URL=http://kong:8000`).

## Quick start

```bash
# 1) Bring up Supabase stack first
cd supabase-stack
docker compose -p supabase up -d
# wait ~60s for all containers to report healthy

# 2) Bring up mimrai stack
cd ../app
docker tag node:20-alpine local-mimrai/node:20-alpine        # one-time
docker compose -f docker-compose.local.yaml up -d --build

# 3) Push schema + seed default user (one-time, after first start)
docker exec supabase-db psql -U postgres -d postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker run --rm --network supabase_default \
  -e DATABASE_URL='postgresql://postgres:your-super-secret-and-long-postgres-password@db:5432/postgres' \
  -e MIMRAI_LOCAL_DEV=1 \
  -w /app/packages/db \
  app-api \
  /usr/local/bin/bun x drizzle-kit push --force

docker run --rm --network supabase_default \
  -e DATABASE_URL='postgresql://postgres:your-super-secret-and-long-postgres-password@db:5432/postgres' \
  -e MIMRAI_LOCAL_DEV=1 \
  -w /app/packages/db \
  app-api \
  /usr/local/bin/bun run src/seed-local-dev.ts

# 4) Open http://localhost:5179 — you'll land directly on /team/local-dev
```

## Seeded identity

| Field | Value |
|---|---|
| User id | `local-dev-user` |
| User email | `dev@mimrai.local` |
| Team id | `local-dev-team` |
| Team slug | `local-dev` |
| Plan | `team` |
| Role | `owner` |

These IDs are referenced in three places that **must agree** (and do):

1. `app/packages/db/src/seed-local-dev.ts` — inserts the rows
2. `app/apps/dashboard/src/lib/get-session.ts` — returns this user to SSR consumers when `MIMRAI_LOCAL_DEV=1`
3. `app/apps/api/src/lib/context.ts` — injects this user as the tRPC ctx when there's no real session and `MIMRAI_LOCAL_DEV=1`

## Auth bypass

When `MIMRAI_LOCAL_DEV=1`:

- `app/apps/dashboard/src/app/page.tsx` redirects `/` → `/team/local-dev` instead of `/sign-in`.
- `app/apps/dashboard/src/app/team/[team]/layout.tsx` skips the `if (!session?.user?.teamSlug) redirect("/sign-in")` gate and the `switchTeam` mutation, since there's only one seeded team.
- `app/apps/dashboard/src/lib/get-session.ts` returns the seeded user verbatim.
- `app/apps/api/src/lib/context.ts` reads `auth.api.getSession()`, and if the result is null **falls back to the seeded user id** instead of returning `{ session: null }`. The protectedProcedure check in `app/apps/api/src/trpc/init.ts` is then satisfied.

Without `MIMRAI_LOCAL_DEV=1` (i.e. in production) every one of these code paths behaves byte-identically to upstream — the auth gates and redirects are intact.

## URL split for SSR vs browser

The dashboard renders Next.js SSR inside its container. From there `localhost:3003` is the container itself, not the api. `apps/dashboard/src/lib/auth-client.ts` and `apps/dashboard/src/utils/trpc.ts` were extended (also gated on `MIMRAI_LOCAL_DEV=1`) to use `MIMRAI_SSR_SERVER_URL=http://api:3003` for server-side fetches while keeping `NEXT_PUBLIC_SERVER_URL=http://localhost:3003` for the browser.

## Stubbed integrations

These remain stubbed because the user does not need them locally:

| Service | How stubbed | File |
|---|---|---|
| Stripe / billing | Recursive Proxy returns `{}` for any call | `packages/billing/src/lib/payments.ts`, `apps/api/src/lib/payments.ts` |
| Resend (email) | Recursive Proxy returns `{ data: { id: "stub-local-dev" }, error: null }` | `packages/notifications/src/lib/resend.ts`, `apps/api/src/lib/resend.ts` |
| OpenPanel (analytics) | No-op | `packages/events/src/server.ts`, `packages/events/src/client.tsx` |
| Sentry | `init` skipped | `apps/api/src/lib/instrument.ts`, `apps/dashboard/sentry.*.config.ts` |
| Trigger.dev | `configure` skipped; `tasks.trigger()` returns stub id | `apps/api/src/lib/trigger.ts`, `packages/jobs/src/init.ts` |
| OpenAI embeddings | Returns 768-dim zero vector | `packages/embedding/src/embeddings/task.ts` |
| Upstash Redis + Ratelimit | In-process Map-backed fake | `packages/realtime/src/redis-client.ts`, `apps/api/src/ai/mcp/rate-limit.ts` |

All gated on `MIMRAI_LOCAL_DEV=1` — drop that flag and code paths revert to upstream.

## Tearing down

```bash
docker compose -f app/docker-compose.local.yaml down -v
docker compose -p supabase -f supabase-stack/docker-compose.yml down -v
```

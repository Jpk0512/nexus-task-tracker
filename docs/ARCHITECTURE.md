# Architecture

## Overview

Mimrai is an open-source, Linear-style minimalist task and project tracker. Source lives under `app/` (downloaded from github.com/mimrai-org/mimrai, with no upstream git connection).

## Tech Stack

- Languages: TypeScript
- Runtime: Bun 1.2
- Monorepo: Turborepo
- Frontend: Next.js + React + Tailwind + Shadcn UI
- API: TRPC server in `app/apps/api`
- Database: Supabase (Postgres) + Drizzle ORM (`app/packages/db`)
- Cache: Redis (local container)
- Lint: Biome

## Source Tree

- `app/apps/dashboard` — main web dashboard
- `app/apps/website` — marketing website
- `app/apps/api` — TRPC backend
- `app/apps/desktop` — Tauri/desktop client (not used in local dev)
- `app/packages/*` — shared workspace packages
- `app/supabase/` — schema, migrations, seed
- `.claude/`, `.memory/`, `docs/`, `nexus-orchestrator/` — Nexus orchestration scaffolding

## External Services (stubbed in local dev)

| Service | Package | Local strategy |
|---|---|---|
| Stripe / billing | `packages/billing` | no-op stubs, `MIMRAI_LOCAL_DEV=1` |
| Resend (email) | `packages/email` | log-only stub |
| OpenPanel (analytics) | `packages/events` | no-op stub |
| Sentry (errors) | (shared) | DSN disabled |
| Trigger.dev (jobs) | `packages/jobs` | inline/no-op stub |
| Upstash Redis | `packages/cache` | swap for local redis URL |
| OpenAI | `packages/embedding` | mock embeddings |

## Personas

- **forge** — Frontend engineer

---
*Generated for the mimrai project on the Nexus orchestrator template.*

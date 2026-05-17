# mimrai — Agent Directives

## Nexus Protocol

The Nexus orchestrator is configured in `.claude/settings.json`. Deep protocol
detail lives in the **`nexus-protocol`** skill.

## Source of Truth Precedence

1. `.memory/project.db` — live decisions, tasks, sessions
2. `docs/CONSTITUTION.md` — governance articles
3. `docs/` — DECISIONS.md, TASKS.md, PRD.md, ARCHITECTURE.md, features/*
4. Nested `CLAUDE.md` files (including `app/AGENTS.md`)

## App Layout

The mimrai application lives at `app/` (downloaded from github.com/mimrai-org/mimrai, no git remote). It is a Bun + Turbo monorepo.

- `app/apps/{dashboard,website,api,desktop}` — Next.js + API surfaces
- `app/packages/{billing,email,events,embedding,jobs,cache,notifications,...}` — external-service wrappers (most are stubbed for local dev)
- `app/supabase/` — Supabase migrations + config

## Stack

- **Languages:** TypeScript
- **Runtime:** Bun 1.2
- **Monorepo:** Turborepo
- **Frontend:** Next.js + React + Tailwind + Shadcn UI
- **Backend:** API app under `app/apps/api` (TRPC)
- **Database:** Supabase (Postgres) + Drizzle ORM
- **Cache:** Redis (local container)
- **Lint:** Biome

## Local-dev stubbing policy

Payments (Stripe / billing), email (Resend), analytics (OpenPanel), error tracking (Sentry), background jobs (Trigger.dev), and AI (OpenAI) are **stubbed** with no-op fakes guarded by `MIMRAI_LOCAL_DEV=1`. Do not call out to those services from local dev. See `docs/LOCAL_DEV.md`.

## Dev Port

Web app: first available in 5178+ range — pinned in `app/docker-compose.yaml`.

## Rules

- Delegate per `docs/agents/TEAM.md` — Nexus does not write code itself.
- Before any new feature: spec + GWT + planning gate PASS.
- Verification before done: `bun run check-types` + `bun run check` (Biome).
- All shell commands prefixed with `rtk` where supported.

## Feature Specs

Index in `docs/TASKS.md`. Active specs under `docs/features/FEAT-*.md`.

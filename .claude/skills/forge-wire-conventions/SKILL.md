---
name: forge-wire-conventions
description: "INTERNAL variant — Next.js server actions + route handlers + DuckDB read-side + AI SDK 4. forge-wire stack pin and auth patterns."
---

# Forge-Wire Conventions — Next.js server actions + DuckDB variant

Canonical for `app/api/**`, `app/actions/**`, `app/lib/ai/**`.

## Stack pin

- **Next.js 15 App Router.** Route handlers in `app/api/<route>/route.ts`.
- **Vercel AI SDK 4** via `@ai-sdk/anthropic`. Azure-routed; baseURL from `AI_API_BASE_URL`. Model `claude-sonnet-4-6`. No direct Anthropic SDK calls.
- **MCP** server lives at `/api/mcp` via `@modelcontextprotocol/sdk` v1.x. MCP tools: `app/api/mcp/tools/<tool>.ts` with registration in `app/api/mcp/route.ts`.
- **DuckDB 1.5** read-side queries. Path from `DUCKDB_PATH`. Write-side is Pipeline's domain.
- **TypeScript strict.** No `any`. Generics over type assertions.

## AI SDK 4 auth (DEC-005)

Anthropic via Azure. `AI_API_BASE_URL` is the full URL ending in `/anthropic/v1/messages`. `ANTHROPIC_API_KEY` is the Azure resource key. Same key for embeddings at `/openai/deployments/*`. Don't read or assume any other `.env.*` file.

## Server Actions

- File-level `'use server'` directive or function-level for co-located actions.
- Validate all input at the action boundary with Zod or explicit guards.
- Live in `app/actions/<name>.ts` or co-located `_actions.ts`.
- Return typed results — never raw DuckDB rows, never `any`.

## Route handlers

- `app/api/<route>/route.ts`. Export named HTTP method functions: `GET`, `POST`, etc.
- Use `NextRequest` + `NextResponse` types.
- Validate request body/params at the handler boundary.

## DuckDB read-side patterns

- DuckDB connection from `app/lib/db.ts` (singleton). Never open a new connection per request.
- Queries return typed arrays — define an interface for each query's row shape.
- Use parameterized queries. Never string-interpolate user input into SQL.
- Path: `process.env.DUCKDB_PATH` (required; throws if absent).

## Tableau auth landmine (DEC-004)

`TABLEAU_SITE_ID` is the LUID (UUID). `TABLEAU_SITE_CONTENT_URL` is the slug. Sign-in payload uses `contentUrl`, never the LUID.

## Verification

```bash
rtk tsc                          # full type-check (TypeScript strict)
rtk lint                         # eslint, no warnings ignored
rtk vitest run app/__tests__/    # unit + integration tests
```

## Deploy step (always)

End every implementation response with `## Deploy step` containing a restart action targeting the current session-branch HEAD.

No branch line — the block targets the session-branch HEAD, not a feature branch.

## Forbidden writes

`ingestion/`, `models/`, `docker-compose*.yml`, `Caddyfile`, `.memory/`, `.claude/`, anywhere outside the repo.

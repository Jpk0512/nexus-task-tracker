---
name: hermes-auth-patterns
description: Integration / auth patterns for Tableau REST + VDS + Metadata, Azure-routed Anthropic, MCP server registration, Docker Compose service topology, and env-var routing. Preloaded into Hermes; the canonical reference for cross-service auth flows in this project.
---

# Hermes Auth Patterns

Canonical reference for service wiring, auth flows, and env-var routing.

## Tableau auth (DEC-004) — THE TRAP

- **`TABLEAU_SITE_ID` is the LUID** (UUID format, e.g. `a1b2c3d4-...`). DO NOT use it in the sign-in payload.
- **`TABLEAU_SITE_CONTENT_URL` is the slug** (e.g. `mycompany` from `https://server/site/mycompany/`). USE THIS in the sign-in REST payload.
- PAT-based sign-in:
  ```json
  {
    "credentials": {
      "personalAccessTokenName": "...",
      "personalAccessTokenSecret": "...",
      "site": {"contentUrl": "<slug>"}
    }
  }
  ```
- Response carries `token` + `site.id` (the LUID). Subsequent API calls use the LUID in the URL path; the token goes in `X-Tableau-Auth` header.
- VDS endpoint: `https://<host>/api/v1/vizql-data-service/query-datasource` — auth header same.
- Metadata API: `https://<host>/relationship-service/graphql` — auth header same.

## Azure-routed Anthropic (DEC-005)

- `AI_API_BASE_URL` is the **full URL** ending in `/anthropic/v1/messages` — not a host, the full path.
- `ANTHROPIC_API_KEY` is the Azure resource key (not an Anthropic-issued key).
- The same Azure resource key works for embeddings at `/openai/deployments/<deployment>/embeddings`. One resource, two surfaces.
- AI SDK 4 (`@ai-sdk/anthropic`): pass `baseURL` from `AI_API_BASE_URL` directly.

## MCP server (Next.js `/api/mcp`)

- `@modelcontextprotocol/sdk` v1.x. Server registers tools via `server.tool(name, schema, handler)`.
- Response envelope is `{content: [{type: "text", text: "..."}]}` — tests/handlers must match this shape.
- Tools live in `app/api/mcp/tools/<name>.ts`; registered in `app/api/mcp/route.ts`.

## Docker Compose service topology

- `docker-compose.dev.yml` + `docker-compose.prod.yml`.
- Service names inside the network: `app`, `malloy-publisher`, `ingestion`, `redis`, `caddy`.
- App → Malloy: `http://malloy-publisher:4000` (project-internal hostname, not localhost).
- App → Redis: `redis://redis:6379`.
- Caddy fronts everything; routes by `Host:` header.

## Env-var hygiene

- `.env.example` is the only canonical env file. Every required var has an entry with `STUB_*` placeholder + 1-line comment.
- `.env`, `.env.dev`, `.env.prod` are gitignored. Never read or assume contents.
- New env var = `.env.example` entry MUST land in the same commit.
- `ARIZE_PROJECT_NAME` is set in `.claude/settings.json env` — read from there; do not hardcode.

## Auth-error documentation rule

When you write auth code, include the verbatim auth-failure response shape as a comment above the call site. Future readers debugging production must be able to match the response to the code path WITHOUT re-running the API call.

## Pairing rules

- Tableau data extraction → request Pipeline pairing via `## NEXUS:NEEDS-DECISION`
- Tableau API route under `app/api/...` → request Forge pairing
- Schema changes triggered by API response shape → request Atlas pairing

## Verification commands

- TS: `rtk tsc` + `rtk lint`
- Python: `uv run ruff check`
- Docker: `docker compose -f docker-compose.dev.yml config` (syntax validation, no service start)
- Smoke if practical: `curl` against the auth endpoint with stub values returning the expected 4xx shape

## Forbidden writes (Output-Dir STRICT)

Business logic in `app/` outside `api/auth/` and `api/mcp/`. Business logic in `ingestion/` outside `src/auth/` and `src/clients/`. `models/`. `.env`, `.env.dev`, `.env.prod`. `.memory/`. `.claude/`. Anywhere outside the repo.

---

## Mandatory Discipline (2026-05-13)

### Cross-service architecture review
- When wiring or rewiring inter-service communication (auth, RPC, queue, HTTP
  endpoint, env-var routing), your response MUST cite which alternative
  patterns were considered and why the chosen one fits the deployment
  topology — see CONTRACT.md Rule <arch-review>.
- A wire-only fix that papers over an architectural mismatch is a contract
  violation.

### End-to-end smoke test
- `docker compose config` validation + container-network reachability test
  (`docker exec <consumer> curl <service>`) in your response.

### Deploy step block (always)
- compose / .env / port changes require a `docker compose up -d <svc>` step +
  a verification command (`docker exec ... env | grep` or equivalent).

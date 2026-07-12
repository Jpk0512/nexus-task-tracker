# Agent Team — Nexus

> **Install-time tokens:** persona and hook templates contain double-underscore-delimited
> placeholders such as `BACKEND_SRC_DIR`, `FRONTEND_SRC_DIR`, `INGESTION_DIR`, `DB_KIND`,
> `AI_LAYER`, `MODELS_DIR`, `FRONTEND_FRAMEWORK`, `BACKEND_LANG`, etc. Each bare name is
> wrapped in double underscores when written in a template (e.g. the token for `DB_KIND`
> appears as two underscores, then `DB_KIND`, then two more underscores). These are
> substituted at install time by `tools/render_install.py` via `tools/stack_profile.py`
> (`_TOKEN_PATHS`). Every persona description ships with the placeholders intact; the
> rendered copy in an installed project has them replaced with real paths/values.

## Roster (canonical dispatch targets)

These are the ONLY valid `subagent_type` values. Dispatch by the exact slug:

`scout`, `forge-ui`, `forge-ui-pro`, `forge-wire`, `forge-wire-pro`, `pipeline-data`, `pipeline-data-pro`, `pipeline-async`, `pipeline-async-pro`, `hermes`, `atlas`, `palette`, `lens-fast` (model: haiku), `lens`, `quill-ts`, `quill-py`.

**RETIRED base names — never dispatch:** `forge`, `pipeline`, and `quill` are split personas and are NOT dispatch targets. `.claude/hooks/persona-alias-resolver.sh` DENIES them (exit 2) or redirects them only when the brief carries scope hints. Route to the split persona directly:

- `forge` → `forge-ui` (UI/components/RSC) or `forge-wire` (`app/api`/server actions/AI wiring)
- `pipeline` → `pipeline-data` (transforms/writes/embeddings) or `pipeline-async` (workers/clients/async)
- `quill` → `quill-ts` (TypeScript tests) or `quill-py` (Python tests)

**Dual-persona binding:** `forge-ui` ↔ `palette` — neither ships without the other for any visual work (see Palette and the Pairing rules table).

**Install-aware availability — VERIFY BEFORE DISPATCH.** `pipeline-data`, `pipeline-async`, `pipeline-data-pro`, `pipeline-async-pro`, and `quill-py` exist ONLY in Python-stack installs. They are NOT present in a pure TS/Next.js install. Dispatching an unregistered persona hard-fails mid-workflow with no recovery path. Before any dispatch, confirm the agent file exists at `.claude/agents/<persona>.md`. In a TS/Next.js-only install, map Python work as follows:

| Python work type | TS/Next.js install mapping |
|---|---|
| Data transforms / database writes / embeddings (`pipeline-data`) | `forge-wire` (read-side) + `hermes` (wiring) |
| Async workers / external API clients (`pipeline-async`) | `forge-wire` (server actions) + `hermes` (auth/client wiring) |
| Python test authoring (`quill-py`) | `quill-ts` (TS tests only) |

If the work genuinely requires Python ingestion logic that cannot be expressed in TS, surface `## NEXUS:NEEDS-DECISION` — do not silently remap it.

---

## Cascade routing — model per persona (DEC-025)

When dispatching via `subagent_type=<persona>`, the persona's agent-file frontmatter `model:` field sets the model. The harness resolves the concrete snapshot from the bare name. This table must match each agent file's frontmatter.

| Persona | Model | Notes |
|---|---|---|
| Nexus (orchestrator) | opus | Planning / classification / review reasoning |
| Scout | haiku | High-volume read-only exploration; cheap discovery |
| forge-ui | sonnet | TS/Next.js UI implementation |
| forge-wire | sonnet | TS/Next.js server implementation |
| pipeline-data | sonnet | Python data-transform implementation |
| pipeline-async | sonnet | Python async-worker implementation |
| atlas | opus | Schema design — deeper reasoning for DDL correctness |
| hermes | sonnet | Cross-service wiring; auth glue |
| palette | sonnet | Visual contract authoring |
| lens | sonnet | Per frontmatter; T1 light-lane may use haiku via lens-fast |
| lens-fast | haiku | Parallel fast-lane sibling; deterministic gates only |
| quill-ts | sonnet | TS test authoring with real data shapes |
| quill-py | sonnet | Python test authoring with real data shapes |
| `*-pro` variants | opus (effort xhigh) | forge-ui-pro / forge-wire-pro / pipeline-data-pro / pipeline-async-pro |

**Retired base names (`forge`, `pipeline`, `quill`)** are NOT valid `subagent_type` values — `persona-alias-resolver.sh` DENIES them. Dispatch the split persona directly.

---

## Routing rule — infra / docs / markdown work

Some work types have no domain-specialist persona of their own. Route them as:

- **`.claude/hooks/**` infra edits (hook bodies + settings/wiring reconciles)** → **hermes** (intent `implement_wiring`). Hermes owns cross-service wiring and configuration plumbing, which covers hook bodies and their `settings.json` registration. The agent MUST stay inside the brief's `do_not_touch` set and edit ONLY the copy named in the brief.
- **`.claude/hooks/tests/**` (hook test suites)** → **quill-py** (intent `test`). Hook tests are Python/pytest, so the Python test author owns them.
- **`docs/**` and markdown content edits (governance, specs, agent contracts)** → **hermes** (intent `implement_wiring`). Governance/docs reconciles fall under Hermes's wiring/config surface; the same `do_not_touch` discipline applies.

Mirrored as a routing-table row in the `team-routing` skill for dispatch-time lookup.

## Forbidden directories (per persona)

The table below is the canonical forbidden-directory reference. Before briefing any teammate in a dynamic Workflow, intersect the teammate's assigned file-globs against its row. Split the brief if any file crosses the boundary.

| Persona | Cannot touch |
|---|---|
| Scout | Anything — read-only via `disallowedTools: Edit, Write, NotebookEdit` |
| forge-ui | `/`, `/`, `docker-compose*.yml`, `.memory/`, `Caddyfile`, `app/apps/api/src/api/**` |
| forge-wire | `/`, `/`, `docker-compose*.yml`, `.memory/`, `Caddyfile`, `app/apps/dashboard/src/components/**` |
| pipeline-data | `app/apps/dashboard/src/`, `/`, `docker-compose*.yml`, `.memory/` |
| pipeline-async | `app/apps/dashboard/src/`, `/`, `docker-compose*.yml`, `.memory/` |
| hermes | Business logic inside `app/apps/dashboard/src/` or `/` (auth/integration glue only); `/`, `.memory/` |
| atlas | Anything via Bash (`disallowedTools: Bash` — design only); `app/apps/dashboard/src/`, `/` business logic |
| lens | Anything — `disallowedTools: Edit, Write, NotebookEdit` (reports only) |
| lens-fast | Anything — `disallowedTools: Edit, Write, NotebookEdit` (pass/fail only) |
| quill-ts / quill-py | Non-test files; `.memory/` |
| Nexus (orchestrator) | Anything via Edit/Write — `disallowedTools: Write, Edit, NotebookEdit`; orchestrates via delegation only |

**Pre-dispatch check (how to apply):**
1. List every file or glob the teammate will touch.
2. Check each against the persona's "Cannot touch" row above.
3. If ANY file crosses the boundary, split into two or more briefs — one per ownership domain — and assign the correct persona to each.

**Common ownership splits:**
- Schema or migrations → `atlas`
- Server-side API routes / server actions / AI-layer wiring (`app/apps/api/src`) → `forge-wire`
- Frontend UI components / pages / routes (`app/apps/dashboard/src`) → `forge-ui`
- `` transforms/writers → `pipeline-data`
- `` workers/clients → `pipeline-async`
- Auth wrappers / env-var plumbing / Docker / MCP → `hermes`
- Test files only → `quill-ts` or `quill-py`

A brief spanning ownership lines is a dispatch contract violation — Lens will flag it and force a REVISE cycle.

## Persona routing — decision diagram

```mermaid
flowchart TD
    A([Task arrives]) --> B{Work type?}

    B -->|UI / components / pages| C[forge-ui]
    B -->|Server actions / API routes / AI wiring| D[forge-wire]
    B -->|Data transforms / DB writes / embeddings| E[pipeline-data]
    B -->|Async workers / external clients| F[pipeline-async]
    B -->|Schema / DDL / semantic model| G[atlas]
    B -->|Auth wrappers / env-vars / Docker / MCP| H[hermes]
    B -->|Visual design / component spec| I[palette]
    B -->|TS tests| J[quill-ts]
    B -->|Python tests| K[quill-py]
    B -->|Unknown / multi-domain| L[Scout first]
    B -->|Post-impl validation| M[lens-fast ∥ lens]

    C -->|Visual work| I
    I -->|"binding: neither ships without the other"| C
    C <-->|Full-stack| D

    E <-->|Ingestion pipeline| F

    C --> N{Complex / stall / REVISE?}
    D --> N
    E --> N
    F --> N
    N -->|Yes| O["escalate to -pro variant\nopus, effort: xhigh"]
    N -->|No| P[implement]

    P --> M
    O --> P

    M -->|NEXUS:REVISE| N
    M -->|NEXUS:DONE| Q([Task complete])
```

---

## Nexus (Orchestrator)
**Role:** Main Claude session. Plans work, assigns tasks, integrates output, runs DB log commands returned by agents, pushes to git.  
**Does NOT:** Write feature code directly (delegates to specialists). Merge partial work.  
**Reads:** `docs/agents/CONTRACT.md` before every delegation. `python3 .memory/log.py context dump` at session start.

---

## Scout (Explorer)
**Role:** Read-only investigation. Maps unknown territory before implementation begins.  
**Specialties:** SocratiCode semantic search, API response inspection (via aside), dependency graph analysis, identifying what already exists vs. what needs to be built.  
**Tools:** `/codebase-exploration`, `/aside-browser`, Read, Bash (read-only commands only).  
**Does NOT:** Edit files. Install packages. Make network requests with side effects.  
**Output format:** Structured findings JSON + list of files relevant to the task.  
**When to use:** Any task where the implementation path is unclear. Always use Scout before Hermes for external API integration work.

---

## forge-ui (Frontend UI Engineer)
**Role:** Implements UI components, pages/routes, charts, styling, and theme/motion under `app/apps/dashboard/src`.  
**Specialties:** `next` UI work, component/data-display patterns, interaction states, light/dark parity. Stack-specific conventions live in the `forge-ui-conventions` skill.  
**Standards:** Full type safety. Run `rtk tsc` and `rtk lint` before returning complete.  
**Does NOT:** Touch ``, ``, `docker-compose`, or `app/apps/api/src`.  
**Pairs with:** forge-wire for full-stack work; palette for design specs; quill-ts for tests.  
**Verification:** Must return verbatim `rtk tsc` and `rtk lint` output in `verification_result`.

### forge-ui-pro (Escalation variant)
Same scope as forge-ui. Model: opus, effort: xhigh. Nexus dispatches this variant when task is classified `complex`, `stall_count > 0`, or Lens returned NEXUS:REVISE on a prior dispatch.

---

## forge-wire (Server-side `ts` Engineer)
**Role:** Implements server actions, API routes, AI layer wiring, and read-side data access under `app/apps/api/src`.  
**Specialties:** `vercel-ai-sdk-v4` integration, server actions, read-side `postgres` queries. Stack-specific conventions live in the `forge-wire-conventions` skill.  
**Standards:** Read before edit. Run the verification commands from the `forge-wire-conventions` skill (language-specific for `ts`) before returning complete.  
**Does NOT:** Touch ``, ``, `docker-compose`, or `app/apps/dashboard/src`.  
**Pairs with:** forge-ui for full-stack work; quill-ts for tests.  
**Verification:** Must return verbatim language-appropriate verification output in `verification_result`.

### forge-wire-pro (Escalation variant)
Same scope as forge-wire. Model: opus, effort: xhigh. Spawned under same conditions as forge-ui-pro.

---

## pipeline-data (Python / Data Transform Engineer)
**Role:** Implements `/transforms/**`, `/writers/**`, dataframe transforms, `postgres` writes, embeddings.  
**Specialties:** Python 3.12, dataframe transforms, `postgres` writes, Pydantic models, embedding pipelines. Stack-specific conventions live in the `pipeline-data-conventions` skill.  
**Standards:** Full type hints on all functions. `ruff format` before done. No bare `except`. `os.environ` (not `os.getenv`) unless default is semantically correct.  
**Does NOT:** Touch `app/apps/dashboard/src` or ``. No async workers or external client calls.  
**Pairs with:** pipeline-async for ingestion pipelines.  
**Verification:** Must return verbatim `uv run ruff check` output in `verification_result`.

### pipeline-data-pro (Escalation variant)
Same scope as pipeline-data. Model: opus, effort: xhigh. Spawned under same conditions as forge-ui-pro.

---

## pipeline-async (Python / Async Worker Engineer)
**Role:** Implements `/workers/**`, `/clients/**`, async actors, message broker, external API clients.  
**Specialties:** Async workers, message broker, async HTTP clients via httpx, AI enrichment. Stack-specific conventions live in the `pipeline-async-conventions` skill.  
**Standards:** Full type hints on all functions. `ruff format` before done. No bare `except`. `os.environ` (not `os.getenv`) unless default is semantically correct.  
**Does NOT:** Touch `app/apps/dashboard/src` or ``. No synchronous `postgres` write pipelines (pipeline-data owns those).  
**Pairs with:** pipeline-data for ingestion pipelines.  
**Verification:** Must return verbatim `uv run ruff check` output in `verification_result`.

### pipeline-async-pro (Escalation variant)
Same scope as pipeline-async. Model: opus, effort: xhigh. Spawned under same conditions as forge-ui-pro.

---

## Escalation variants — dispatch rules

Nexus dispatches the `-pro` variant when:
- (a) router/Nexus classifies task as `complex`, OR
- (b) `tasks.stall_count > 0` for the task, OR
- (c) Lens returned NEXUS:REVISE on a prior dispatch.

---

## lens-fast (Deterministic Gates Fast-Lane Verifier)
**Role:** Fast-lane verifier for deterministic gates only — lint, tsc, and tests pass/fail. Returns a pass/fail verdict in seconds so Nexus can short-circuit revision loops on early failure.  
**Model:** haiku.  
**Does NOT:** Write or fix code. Perform semantic / RCA / visual / security review (Lens owns those). Only reports deterministic-gate outcomes.  
**Runs:** `rtk tsc`, `rtk lint`, `uv run ruff check`, `rtk vitest run`, `uv run pytest` — pass/fail only.  
**Output:** Structured pass/fail gate matrix with verbatim command output as evidence. Does NOT write a `validation_log` row — that is `lens`'s exclusive job.  
**Pairs with:** **Lens — dispatched in parallel in one tool block post-implementation per Article XIII.b.** lens-fast and Lens together replace the prior single-Lens validation step.  
**When to use:** After every code-touching agent completes — always dispatched alongside Lens in the same message block. For T1 trivial (single file, non-gated, no probe) MAY satisfy light-lane alone.

---

## Lens (Deep Semantic Reviewer)
**Role:** Deep semantic + RCA + visual + security review. Validates output against acceptance criteria.
Operates in two depth modes (3-tier Lens):
- **T1 LIGHT** (single file, non-gated prefix, no subprocess/eval/network content): deterministic
  gates + brief semantic sanity; writes a real verdict row with `agent_validated='lens'`. MAY run on
  a cheaper model (sonnet/haiku). Orchestrator MAY invoke lens-fast for T1 light-lane only.
- **T2 FULL** (multi-file OR gated prefix OR content-probe OR ambiguity): full deep audit.
  DEFAULT-DENY: any classification ambiguity resolves to T2.  
**Model:** sonnet (per brief tier — see frontmatter; T1 light-lane may use haiku; T2 full audit runs on the dispatch model). The model line is in the agent frontmatter (`model: sonnet`) — the orchestrator may escalate tier via effort.  
**Does NOT:** Write or fix code. Only reports. Does NOT run the deterministic fast-lane gates (lens-fast owns those).  
**Runs:** semantic review, root-cause analysis, visual verification, security review, schema validation, cross-domain contract checks; also deterministic gates on T1 light-lane.  
**Output:** Structured report: criterion → result → evidence (line numbers, command output). Writes `validation_log` row with `agent_validated='lens'` (both T1 and T2).  
**Paired oracle:** lens-fast and Lens are a paired oracle: lens-fast owns the deterministic pass/fail matrix, Lens owns the deep semantic/RCA/security judgment and is the sole writer of the `agent_validated='lens'` validation_log row. lens-fast NEVER writes a validation_log row. Both must be dispatched in the same tool block post-implementation (Article XIII.b).  
**Pairs with:** **lens-fast — dispatched in parallel in one tool block post-implementation per Article XIII.b.** lens-fast returns the deterministic-gate verdict in seconds while Lens runs the deep pass; Nexus short-circuits if lens-fast fails.  
**When to use:** After every forge-ui, forge-wire, pipeline-data, or pipeline-async agent completes — always dispatched alongside lens-fast in the same message block, before orchestrator marks task done. T1 work needs at minimum a light-lane row; T2 needs the full audit.

---

## quill-ts (TypeScript Test Engineer)
**Role:** Writes TypeScript tests. Coordinates with Lens for coverage targets.  
**Specialties:** Vitest + React Testing Library (TypeScript). Integration tests over unit tests where possible.  
**Standards:** Tests must use real data shapes (no magical mocks that don't reflect prod). Tests in `app/apps/dashboard`.  
**When to use:** After forge-ui/forge-wire complete a feature, before merge.

---

## quill-py (Python Test Engineer)
**Role:** Writes Python tests. Coordinates with Lens for coverage targets.  
**Specialties:** pytest, dataframe fixtures. Integration tests over unit tests where possible.  
**Standards:** Tests must use real data shapes (no magical mocks that don't reflect prod). Tests in `/tests/`.  
**When to use:** After pipeline-data/pipeline-async complete a feature, before merge.

---

## Pairing rules

| Task type | Pair |
|---|---|
| Full-stack feature (UI + API) | forge-ui ↔ forge-wire |
| Ingestion pipeline (transform + async worker) | pipeline-data ↔ pipeline-async |
| UI component with tests | forge-ui + quill-ts |
| API/server-action with tests | forge-wire + quill-ts |
| Data transform with tests | pipeline-data + quill-py |
| Async worker with tests | pipeline-async + quill-py |
| New UI component (visual design) | palette ↔ forge-ui (binding — neither ships without the other; route to palette first) |
| Post-implementation validation (any code-touching task) | lens-fast ∥ lens (dispatched in parallel, one tool block, per Article XIII.b); T1 light-lane → lens-fast alone may satisfy; T2 risky → both required |

---

## Atlas (Data / Schema Specialist)
**Role:** `postgres` schema design, semantic-model authoring (`none`), table/column layout.  
**Model:** opus (frontmatter: `model: opus`).  
**Specialties:** `postgres` DDL, vector-index design (`pgvector`), dtype mapping. Stack-specific conventions live in the `atlas-schema-patterns` skill.  
**Does NOT:** Run Bash — design only (`disallowedTools: Bash`). Produce implementation code.  
**Works with:** pipeline-data (data format alignment), forge-wire (read-side query patterns).  
**When to use:** When designing a new `postgres` table or canonical schema.

---

## Hermes (Integration Specialist)
**Role:** API wiring, service-to-service connections, external service integration.  
**Specialties:** External REST API integration (auth, endpoint plumbing), AI provider config, MCP endpoint setup, Docker Compose service wiring, environment variable plumbing.  
**Works with:** pipeline-async (external data extraction), forge-wire (API route implementation). Stack-specific auth conventions live in the `hermes-auth-patterns` skill.  
**When to use:** External service integration work, AI provider config, MCP server wiring, any cross-service connection.

---

## Palette (Design Specialist)
**Role:** Visual contract owner. Authors component specs, token/spacing/motion decisions, interaction states, light+dark parity, and empty/loading/error treatments. Produces design docs as the input to forge-ui's implementation briefs.  
**Model:** sonnet (frontmatter: `model: sonnet`).  
**Specialties:** `design/design.md` as binding contract, token extraction from `design/tokens/`, mockup pattern analysis (`docs/ui-mockups/*.html`), WCAG AA contrast validation, motion-budget specs, light+dark parity.  
**Does NOT:** Write implementation code. Copy mockup HTML directly. Touch ``, ``, or `docker-compose` files.  
**Output:** Structured design spec (component map, token list, interaction states) written to `docs/design/` or `.memory/design-reports/`. Returns `## NEXUS:DONE` only after all five verification checks pass.  
**Pairing rule:** **forge-ui ↔ Palette for any UI work.** Palette specs the look; forge-ui implements the TypeScript. Neither ships without the other for visual features. Route to Palette before forge-ui whenever a task involves visual design decisions.  
**When to use:** New UI components, visual redesigns, design-system token decisions, interaction-state definitions, any task where the user's feedback is about appearance (not functionality).

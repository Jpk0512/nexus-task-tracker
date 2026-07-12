---
name: team-routing
description: Persona routing decisions — which persona owns which work type, pairing rules, cascade model assignments, and forbidden directories per persona. Use this skill when classifying a task or selecting which persona to delegate to. Canonical persona definitions live in docs/agents/TEAM.md; this skill surfaces the routing-decision parts Nexus needs at dispatch time.
---

# Team Routing (persona selection for Nexus)

## Cascade routing (model per persona)

Per DEC-025. When dispatching via `subagent_type=<persona>`, the persona's agent file frontmatter sets the model. Models are bare names (`opus` / `sonnet` / `haiku`) — the harness resolves the concrete snapshot. This table MUST match each agent file's frontmatter `model:`.

| Persona | Model | Why |
|---|---|---|
| Nexus (this orchestrator) | opus | Planning / classification / review reasoning |
| Scout | haiku | High-volume read-only exploration; cheap discovery |
| forge-ui | sonnet | TS/Next.js UI implementation; tool-use precision matters |
| forge-wire | sonnet | TS/Next.js server implementation; auth code requires care |
| pipeline-data | sonnet | Python data-transform implementation |
| pipeline-async | sonnet | Python async-worker implementation |
| atlas | sonnet | Schema design |
| lens | sonnet | QA judgment; subtle-issue detection (prior-project literal leaks, schema/log.py drift) |
| lens-fast | haiku | Parallel fast-lane sibling of lens; orchestrator-dispatched after NEXUS:DONE, never user-selected |
| quill-ts | sonnet | TS test authoring with real data shapes |
| quill-py | sonnet | Python test authoring with real data shapes |
| hermes | sonnet | Cross-service wiring; auth glue |
| palette | sonnet | Visual contract authoring |
| `*-pro` escalation variants | opus (effort xhigh) | `forge-ui-pro` / `forge-wire-pro` / `pipeline-data-pro` / `pipeline-async-pro` — dispatched when task is `complex`, `stall_count > 0`, or Lens returned NEXUS:REVISE |

**Retired base names:** `forge`, `pipeline`, `quill` are NOT canonical dispatch targets. They survive only as alias shims that `persona-alias-resolver.sh` resolves to a split persona from the brief's scope (`forge`→`forge-ui`/`forge-wire`, `pipeline`→`pipeline-data`/`pipeline-async`, `quill`→`quill-ts`/`quill-py`). Dispatch the split persona directly; an unresolvable base name is blocked.

## Routing table (work type → lead persona)

| Work type | Lead | Pair if needed |
|---|---|---|
| Next.js / TypeScript UI in `app/components/**`, RSC pages | forge-ui | + forge-wire (full-stack); + palette (design); + quill-ts (tests) |
| Next.js / TypeScript server actions / API routes in `app/api/**` | forge-wire | + forge-ui (full-stack); + quill-ts (tests) |
| Python data transform / DuckDB writes / embeddings in `ingestion/` | pipeline-data | + pipeline-async (ingestion); + quill-py (tests) |
| Python async workers / Dramatiq / external clients in `ingestion/` | pipeline-async | + pipeline-data (ingestion); + quill-py (tests) |
| Tableau REST / VDS / Metadata API integration | hermes | + pipeline-async (data extraction) OR + forge-wire (API route) |
| Azure AI / MCP server wiring | hermes | + forge-wire |
| DuckDB schema design / Malloy models in `models/` | atlas | + pipeline-data (executes migrations) |
| Investigation / unknown territory / pre-implementation scouting | Scout | — (read-only, no edits) |
| Visual design / mockups / component visual contract | palette (lead) | ↔ forge-ui (binding — neither ships without the other; route to palette first) |
| Validation / acceptance check (after impl) | lens-fast ∥ lens | dispatched in parallel in one tool block per Article XIII.b (reports only) |
| TS test authoring (stubs before, verification after) | quill-ts | Coordinates with lens for coverage |
| Python test authoring (stubs before, verification after) | quill-py | Coordinates with lens for coverage |
| Multi-domain or cross-cutting feature | Scout first, then assign by domain | — |
| Docker Compose / Caddyfile / env wiring | hermes | — |
| `.claude/hooks/**` infra edits (hook bodies + settings/wiring reconciles) | hermes (intent `implement_wiring`) | — |
| `docs/**` and markdown content edits (governance, specs, agent contracts) | hermes (intent `implement_wiring`) | — |
| `.memory/log.py` / `.memory/schema.sql` changes | Nexus owns (handle inline) | — |

## Install-aware persona availability (VERIFY BEFORE DISPATCH)

**Python-stack personas (`pipeline-data`, `pipeline-async`, `pipeline-data-pro`, `pipeline-async-pro`, `quill-py`) exist ONLY in Python-stack installs.** They are NOT registered agent files in a pure TS/Next.js install. Dispatching an unregistered `agentType` hard-fails mid-workflow with no recovery path.

Before dispatching any persona, verify it is registered:
- The canonical roster is listed in `docs/agents/TEAM.md`.
- The agent file must exist at `.claude/agents/<persona>.md`.
- A missing agent file = the persona is NOT installed = dispatch will hard-fail.

**TS/Next.js-only install mapping for Python work:**

| Python work type | Map to instead |
|---|---|
| Data transforms / DuckDB writes / embeddings (would be `pipeline-data`) | `forge-wire` (read-side) + `hermes` (wiring) |
| Async workers / external API clients (would be `pipeline-async`) | `forge-wire` (server actions) + `hermes` (auth/client wiring) |
| Python test authoring (would be `quill-py`) | `quill-ts` (TS tests only) |

If the work genuinely requires Python ingestion logic and the Python personas are absent, surface a `## NEXUS:NEEDS-DECISION` — do not silently remap logic that belongs in a Python stack onto TS personas.

## Decomposition boundary — pre-dispatch ownership check

Before briefing any teammate in a dynamic Workflow, intersect the teammate's assigned file-globs against the forbidden-directory map below.

**Rule:** no teammate may be briefed on files that fall outside its write boundary. If a brief would cross an ownership line, **split the brief along that line** — one teammate per ownership domain — rather than leaving it to the teammate to self-restrict.

**How to apply:**
1. List every file or glob the teammate will touch.
2. Check each against the persona's "Cannot touch" row in the forbidden-directory table.
3. If ANY file crosses the boundary, split into two or more briefs — one per domain — and assign the correct persona to each.

**Ownership shortcuts for common splits:**
- Schema or migrations → split out to `atlas`
- `app/api/**` → split out to `forge-wire`
- `app/components/**` / RSC pages → split out to `forge-ui`
- `ingestion/**` transforms/writers → split out to `pipeline-data`
- `ingestion/**` workers/clients → split out to `pipeline-async`
- Auth wrappers / env-var plumbing / Docker / MCP → split out to `hermes`
- Test files only → split out to `quill-ts` or `quill-py`

A brief that spans ownership lines is a dispatch contract violation — Lens will flag it and the task will require a REVISE cycle.

## Pairing rules

- **Tableau API work** → hermes leads, but ALWAYS Scout first to map the existing client surface
- **New DuckDB table** → atlas designs the schema, pipeline-data executes the migration. atlas cannot run Bash.
- **New feature spec** → quill-ts/quill-py write failing test stubs BEFORE forge-*/pipeline-* begin implementation (Constitution Article I)
- **After every forge-* or pipeline-* completion** → lens validates before Nexus marks task done
- **`## NEXUS:REVISE` from lens** → Re-spawn the original implementer (escalate to its `-pro` variant) with lens issues YAML; cap 3 iterations with stall detection

## Forbidden directories (per persona)

| Persona | Cannot touch |
|---|---|
| Scout | Anything (read-only via `disallowedTools: Edit, Write, NotebookEdit`) |
| forge-ui | `ingestion/`, `models/`, `docker-compose*.yml`, `.memory/`, `Caddyfile`, `app/api/**` |
| forge-wire | `ingestion/`, `models/`, `docker-compose*.yml`, `.memory/`, `Caddyfile`, `app/components/**` |
| pipeline-data | `app/`, `models/`, `docker-compose*.yml`, `.memory/` |
| pipeline-async | `app/`, `models/`, `docker-compose*.yml`, `.memory/` |
| hermes | Business logic inside `app/` or `ingestion/` (auth/integration glue only); `models/`, `.memory/` |
| atlas | Anything via Bash (`disallowedTools: Bash` — design only); `app/`, `ingestion/` business logic |
| lens | Anything (`disallowedTools: Edit, Write, NotebookEdit` — reports only) |
| lens-fast | Anything (`disallowedTools: Edit, Write, NotebookEdit` — deterministic gates only; reports only) |
| palette | `app/` TypeScript/React code, `ingestion/`, `models/`, `docker-compose*.yml`; writes ONLY to `docs/design/` or `.memory/design-reports/` |
| quill-ts / quill-py | Non-test files (only test files modifiable); `.memory/` |
| Nexus | Anything via Edit/Write (`disallowedTools: Write, Edit, NotebookEdit`); orchestrate via delegation only |

## Classification decision tree

```
Task arrives →
├── Is it a bug fix / config / single obvious change touching ≤2 files (already read)?
│   YES → Simple Task Bypass. Handle inline. No ceremony.
│   NO  → continue
├── Does it span >5 files OR multi-domain OR ambiguous scope?
│   YES → Complex. Spawn Scout first. Then dispatch the parallel
│         implementation as a dynamic Workflow (Task fan-out under a
│         shared TaskList), one owned task per domain — NOT a raw
│         multi-Task fan-out without a verify stage. See nexus-protocol §9.
│         Each code-writing teammate gets an explicit Lens verify stage.
│   NO  → Standard. Single persona per routing table (if it splits into
│         ≥2 independent slices, escalate to a dynamic Workflow too).
│
└── Standard or Complex →
    1. Run planning gate (skill: nexus-protocol §4)
    2. Reflect (spawn Scout for 5-bullet reflection)
    3. Delegate per CONTRACT.md (skill: contract-schema)
    4. Review completion marker (skill: contract-schema)
    5. Run db_log_cmds
```

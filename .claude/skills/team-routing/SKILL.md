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
| Escalated tier (any implementer above) | opus (effort xhigh) | Dispatch-time `model`/`effort` override on the SAME base persona — NOT a separate `-pro` agent file. `forge-ui-pro` / `forge-wire-pro` / `pipeline-data-pro` / `pipeline-async-pro` are RETIRED names. Escalate when task is `complex`, `stall_count > 0`, or Lens returned NEXUS:REVISE |

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

**Stack-conditional personas exist ONLY when their stack condition is present at install
time.** They are NOT registered agent files when the condition isn't met. Dispatching an
unregistered `agentType` hard-fails mid-workflow with no recovery path — always verify
`.claude/agents/<persona>.md` exists before dispatch. Full remap table (which persona
covers the gap when a stack-conditional persona is absent) and the full classification
decision tree: `references/install-aware-routing.md`.

## Decomposition boundary — pre-dispatch ownership check (summary)

Before briefing any teammate, intersect its file-globs against the forbidden-directory
map — a brief crossing an ownership line MUST be split along that line before dispatch,
never left to the teammate to self-restrict. Full forbidden-directory table, the
ownership-shortcut list, and the worktree-vs-session-branch isolation ladder:
`references/ownership-and-isolation.md`. A worked cross-boundary split:
`examples/cross-boundary-split.md`.

## Pairing rules

- **Tableau API work** → hermes leads, but ALWAYS Scout first to map the existing client surface
- **New DuckDB table** → atlas designs the schema, pipeline-data executes the migration. atlas cannot run Bash.
- **New feature spec** → quill-ts/quill-py write failing test stubs BEFORE forge-*/pipeline-* begin implementation (Constitution Article I)
- **After every forge-* or pipeline-* completion** → lens validates before Nexus marks task done
- **`## NEXUS:REVISE` from lens** → Re-spawn the original implementer (escalate to its `-pro` variant) with lens issues YAML; cap 3 iterations with stall detection

## References

- `references/ownership-and-isolation.md` — the full forbidden-directory table, the
  ownership-shortcut list, and the worktree-vs-session-branch isolation ladder.
- `references/install-aware-routing.md` — the stack-conditional-persona remap table and
  the full classification decision tree.
- `examples/cross-boundary-split.md` — a worked example splitting a brief that would
  otherwise cross two personas' ownership lines.

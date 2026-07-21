# Skill Map — Persona × Work-Type → Required Skills

This table is the canonical source for `skills_required` validation in the brief.
The `skills-required-guard.sh` hook reads this file to enforce CONTRACT R19.

> **Status:** authoritative source for CONTRACT R19 Gate 2 (`skills-required-guard.sh`).
>
> **Scope.** This map covers the **code-writing personas** Nexus dispatches. It
> states the *minimum* skills a brief's `skills_required` MUST contain for a given
> `(persona, work_type)`. A brief MAY list more. Read-only personas (`scout`,
> `lens`, `lens-fast`, `palette`) are advisory in the table below — their
> `skills_required` is optional and the guard does NOT block on an absent or empty
> list for those personas.
>
> **How the guard uses this file.** `skills-required-guard.sh`:
> 1. **Gate 1 (deny):** a code-writing persona dispatched with an absent/empty
>    `skills_required` is blocked at dispatch (`permissionDecision:"deny"`, exit 2) —
>    independent of this map. The block-rule persona set is:
>    `forge-ui`, `forge-wire`, `pipeline-data`, `pipeline-async`,
>    `atlas`, `hermes`, `quill-ts`, `quill-py`, `planner` (DEC-074: planner writes
>    `docs/plans/**`/`.memory/plans/**`, so it carries a real `deliverables.json`
>    write-lane and is derived into this set the same way as any other non-tombstone
>    entry).
>    (`forge-ui-pro`, `forge-wire-pro`, `pipeline-data-pro`, `pipeline-async-pro` are
>    RETIRED dispatch names — no agent file exists for any of them; escalation is a
>    dispatch-time `model: opus, effort: xhigh` override on the base persona, never a
>    separate target. Their `deliverables.json` rows exist only as a boundary compat
>    shim so a stray `-pro`-tagged dispatch is still caught by other gates — they are
>    excluded from THIS gate's roster.) Read-only personas (`scout`,
>    `lens`, `lens-fast`, `palette`) are excluded from Gate 1 — the guard does NOT
>    block them on an absent `skills_required`. The roster is derived from
>    `deliverables.json`; Gate 1 fires for every entry that has no
>    `must_not_modify: ["**/*"]` and is not a tombstone (a compat-shim `_note`
>    containing the word "Tombstone" — including "NOT a Tombstone" — excludes it too,
>    which is how the `-pro` compat-shim rows above end up excluded from Gate 1).
> 2. **Gate 2 (advise):** when `skills_required` is non-empty, the guard looks up the
>    `(persona, work_type)` row (`skills-required-guard.sh` ~L541-561). Two distinct
>    fallback paths, split on whether `work_type` is present at all: if `work_type`
>    is **non-empty but matches no row**, the guard accumulates **every** row for
>    that persona (~L549-554 — so the persona's foundational convention skill is
>    always enforced, even when several specific-work_type rows exist). If
>    `work_type` is **empty/absent** (a doc-only or otherwise generic dispatch),
>    the guard falls back to the persona's **`*` row ONLY** (~L555-561) — it never
>    accumulates across work_types on a dispatch with nothing to disambiguate
>    against (accumulating there previously surfaced integration-specific rows,
>    e.g. tableau/claude-api, on a generic dispatch — fixed under FLEET-FB-4).
>    Missing mandatory skills surface a non-blocking `additionalContext` advisory
>    (exit 0). The map is **fail-open**: if this file is absent, Gate 2 is disabled
>    with a stderr WARN and Gate 1 still fires.
>
> **`work_type` vocabulary.** Free-text scope key carried in the brief. The rows below
> use each persona's natural sub-domains. The broker intent (`implement_ui`,
> `implement_api`, `implement_ingestion`, `implement_schema`, `implement_wiring`,
> `test`) is the coarse cousin; either may appear in a brief. A persona-level `*` row
> is the catch-all the fallback path keys on.
>
> **Skill-name source of truth.** Every skill below appears in the persona's
> `## Skill triggers` table in `.claude/agents/<persona>.md`. Keep this map and those
> tables in sync — if a persona's trigger table changes, update the matching rows here.
>
> **Persona scope note.** The split personas (`forge-ui`, `forge-wire`, `pipeline-data`,
> `pipeline-async`, `quill-ts`, `quill-py`) plus `atlas` and `hermes` are the canonical
> code-writing targets. The base names `forge`, `pipeline`, `quill` are **stale aliases**
> resolved by `persona-alias-resolver.sh` to a split variant before work begins; they
> carry a `*` row here so an un-resolved base-name dispatch with empty skills is still
> denied by Gate 1 and advised by Gate 2.

## Required skills

| persona | work_type | skills |
|---|---|---|
| forge-ui | * | forge-ui-conventions |
| forge-ui | component | forge-ui-conventions, tailwind-design-tokens |
| forge-ui | chart | forge-ui-conventions |
| forge-ui | rsc-page | forge-ui-conventions, rsc-boundary-rules |
| forge-ui | theme | forge-ui-conventions, tailwind-design-tokens, palette-design-patterns |
| forge-ui | implement_ui | forge-ui-conventions |
| forge-wire | * | forge-wire-conventions |
| forge-wire | server-action | forge-wire-conventions, server-action-contract |
| forge-wire | api-route | forge-wire-conventions, server-action-contract |
| forge-wire | ai-sdk | forge-wire-conventions, ai-sdk-patterns |
| forge-wire | duckdb-read | forge-wire-conventions |
| forge-wire | implement_api | forge-wire-conventions |
| pipeline-data | embedding | embedding-patterns |
| pipeline-async | * | pipeline-async-conventions |
| pipeline-async | worker | pipeline-async-conventions |
| pipeline-async | client | pipeline-async-conventions, tableau-client-patterns |
| pipeline-async | tableau | pipeline-async-conventions, tableau-client-patterns, hermes-auth-patterns |
| pipeline-async | implement_ingestion | pipeline-async-conventions |
| atlas | * | atlas-schema-patterns |
| atlas | schema | atlas-schema-patterns |
| atlas | malloy | atlas-schema-patterns |
| atlas | tableau-schema | atlas-schema-patterns, tableau |
| atlas | implement_schema | atlas-schema-patterns |
| hermes | * | hermes-auth-patterns |
| hermes | tableau | hermes-auth-patterns, tableau |
| hermes | azure-ai | hermes-auth-patterns, claude-api |
| hermes | mcp-wiring | hermes-auth-patterns |
| hermes | implement_wiring | hermes-auth-patterns |
| hermes | implement_api | hermes-auth-patterns |
| quill-ts | * | tdd-core |
| quill-ts | vitest | tdd-core, vitest-rtl-idioms |
| quill-ts | test | tdd-core |
| quill-py | * | tdd-core |
| quill-py | pytest | tdd-core, pytest-idioms |
| quill-py | test | tdd-core |
| planner | * | agent-protocol, node-contract |

**pipeline-data has no `*`/fallback row.** `pipeline-data-conventions` was
retired 2026-07-13 (native #4 owner sweep — a stack-conditional skill installed
in zero fleet projects) with no successor; the persona still ships and writes
code (Gate 1's block-rule roster is unaffected — it derives from
`deliverables.json`, not this map), it simply has no bespoke convention skill
left to mandate via Gate 2. Only the `embedding` row survives, re-pointed to
`embedding-patterns` (unaffected by the retirement).

## -pro escalation (dispatch-time override, not a persona)

`-pro` is **not a separate persona and never a separate agent file** — it is a
dispatch-time `model: opus, effort: xhigh` override on the base persona (same
agent file, same intents). A `-pro`-tagged dispatch therefore requires the
**same skills** as its base persona; there is no separate row to maintain here.
`forge-ui-pro`, `forge-wire-pro`, `pipeline-data-pro`, and `pipeline-async-pro`
carry no rows in this table for that reason — normalise the persona string to
its base (`forge-ui`, `forge-wire`, `pipeline-data`, `pipeline-async`) before
lookup if a brief happens to carry the retired `-pro` string.

## Guard behaviour

- **Block** (exit 2): `skills_required` is absent or empty AND persona is a
  code-writing persona (registered set: `forge-ui`, `forge-wire`, `pipeline-data`,
  `pipeline-async`, `atlas`, `hermes`, `quill-ts`, `quill-py`, `planner`). The retired
  `-pro` names are NOT in this set (no agent file; see the scope note above) —
  Read-only personas (`scout`, `lens`, `lens-fast`,
  `palette`) are excluded — the guard does NOT block them on an absent
  `skills_required`.
- **Warn** (exit 0 + message): `skills_required` is non-empty but missing one or more
  skills mandated by this map. Orchestrator may have added context-specific extras;
  guard does not block on supersets.
- **Pass** (exit 0): `skills_required` is non-empty; all mandatory skills present.
- **Fail open** (exit 0): SKILL_MAP.md not found or parse error — guard skips.

## Adding rows

When a new work_type is introduced, add a row here. When a new persona is added,
add it to `deliverables.json` (the single source of truth for `CODE_WRITING_PERSONAS`
— the guard derives the set dynamically via `_load_code_writing_personas()` at
runtime; there is no hardcoded set to update in the hook itself). Also update
`persona-alias-resolver.sh` if the new persona has a stale base-name alias.

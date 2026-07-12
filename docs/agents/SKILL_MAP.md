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
>    `forge-ui`, `forge-ui-pro`, `forge-wire`, `forge-wire-pro`,
>    `pipeline-data`, `pipeline-data-pro`, `pipeline-async`, `pipeline-async-pro`,
>    `atlas`, `hermes`, `quill-ts`, `quill-py`.
>    (`atlas-pro` and `hermes-pro` are not yet registered in `deliverables.json`
>    and are therefore absent from Gate 1 — add their agent files and
>    `deliverables.json` entries to include them.) Read-only personas (`scout`,
>    `lens`, `lens-fast`, `palette`) are excluded from Gate 1 — the guard does NOT
>    block them on an absent `skills_required`. The roster is derived from
>    `deliverables.json`; Gate 1 fires for every entry that has no
>    `must_not_modify: ["**/*"]` and is not a tombstone.
> 2. **Gate 2 (advise):** when `skills_required` is non-empty, the guard looks up the
>    `(persona, work_type)` row. If the brief's `work_type` matches no row, it falls
>    back to **every** row for that persona (so the persona's foundational
>    convention skill is always enforced). Missing mandatory skills surface a
>    non-blocking `additionalContext` advisory (exit 0). The map is **fail-open**:
>    if this file is absent, Gate 2 is disabled with a stderr WARN and Gate 1 still fires.
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
| forge-ui | component | forge-ui-conventions, tremor-patterns, tailwind-design-tokens |
| forge-ui | chart | forge-ui-conventions, tremor-patterns |
| forge-ui | rsc-page | forge-ui-conventions, rsc-boundary-rules |
| forge-ui | theme | forge-ui-conventions, tailwind-design-tokens, palette-design-patterns |
| forge-ui | implement_ui | forge-ui-conventions |
| forge-wire | * | forge-wire-conventions |
| forge-wire | server-action | forge-wire-conventions, server-action-contract |
| forge-wire | api-route | forge-wire-conventions, server-action-contract |
| forge-wire | ai-sdk | forge-wire-conventions, ai-sdk-patterns |
| forge-wire | duckdb-read | forge-wire-conventions, duckdb-read-patterns |
| forge-wire | implement_api | forge-wire-conventions |
| pipeline-data | * | pipeline-data-conventions |
| pipeline-data | transform | pipeline-data-conventions, polars-duckdb-mapping |
| pipeline-data | writer | pipeline-data-conventions, polars-duckdb-mapping |
| pipeline-data | embedding | pipeline-data-conventions, embedding-patterns |
| pipeline-data | implement_ingestion | pipeline-data-conventions |
| pipeline-async | * | pipeline-async-conventions |
| pipeline-async | worker | pipeline-async-conventions, dramatiq-patterns |
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
| quill-ts | * | tdd-patterns |
| quill-ts | vitest | tdd-patterns, vitest-rtl-idioms |
| quill-ts | test | tdd-patterns |
| quill-py | * | tdd-patterns |
| quill-py | pytest | tdd-patterns, pytest-idioms |
| quill-py | test | tdd-patterns |

## -pro escalation variants

The registered `-pro` variants (`forge-ui-pro`, `forge-wire-pro`,
`pipeline-data-pro`, `pipeline-async-pro`) share the **same scope** as their base
persona, so they share the same required skills. The guard's Gate 2 fallback keys
on the exact persona string; if a `-pro` brief carries a `work_type` with no
explicit row, add the rows below mirroring the base persona, or normalise the
persona to its base before lookup. The mandatory foundational skill is identical:

| persona | work_type | skills |
|---|---|---|
| forge-ui-pro | * | forge-ui-conventions |
| forge-wire-pro | * | forge-wire-conventions |
| pipeline-data-pro | * | pipeline-data-conventions |
| pipeline-async-pro | * | pipeline-async-conventions |

`atlas-pro` and `hermes-pro` are **not yet registered** — no agent file exists in
`.claude/agents/` and neither appears in `deliverables.json`, so `_load_code_writing_personas()`
does not include them. Add their agent files and `deliverables.json` entries before
adding rows here.

## Guard behaviour

- **Block** (exit 2): `skills_required` is absent or empty AND persona is a
  code-writing persona (registered set: `forge-ui`, `forge-wire`, `pipeline-data`,
  `pipeline-async`, `atlas`, `hermes`, `quill-ts`, `quill-py`, plus the registered
  `-pro` variants `forge-ui-pro`, `forge-wire-pro`, `pipeline-data-pro`,
  `pipeline-async-pro`). `atlas-pro` and `hermes-pro` are not yet registered and
  are therefore not blocked. Read-only personas (`scout`, `lens`, `lens-fast`,
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

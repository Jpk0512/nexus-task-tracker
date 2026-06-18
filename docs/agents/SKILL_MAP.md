# Skill Map — Persona × Work-Type → Required Skills

This table is the canonical source for `skills_required` validation in the brief.
The `skills-required-guard.sh` hook reads this file to enforce CONTRACT R19.

Any brief dispatching a code-writing persona MUST include `skills_required` listing
at least the skills from the matching row. The orchestrator MAY add additional skills
beyond the minimum set; the guard warns (does not block) on extras.

| persona         | work_type            | required_skills                                                              |
|-----------------|----------------------|------------------------------------------------------------------------------|
| forge-ui        | component            | forge-ui-conventions, tremor-patterns, tailwind-design-tokens                |
| forge-ui        | rsc-page             | forge-ui-conventions, rsc-boundary-rules, tdd-patterns                       |
| forge-wire      | server-action        | forge-wire-conventions, server-action-contract, tdd-patterns                 |
| forge-wire      | api-route            | forge-wire-conventions, ai-sdk-patterns, tdd-patterns                        |
| pipeline-data   | transform            | pipeline-data-conventions, polars-duckdb-mapping, tdd-patterns               |
| pipeline-async  | worker               | pipeline-async-conventions, dramatiq-patterns, tdd-patterns                  |
| quill-ts        | stub-or-verification | vitest-rtl-idioms, tdd-patterns                                              |
| quill-py        | stub-or-verification | pytest-idioms, polars-test-fixtures, tdd-patterns                            |
| atlas           | schema-migration     | atlas-schema-patterns                                                        |
| hermes          | auth-wiring          | hermes-auth-patterns                                                         |
| lens            | verification         | verification-protocols                                                       |
| palette         | design-spec          | palette-design-patterns                                                      |

## Guard behaviour

- **Block** (exit 2): `skills_required` is absent or empty AND persona is in
  `{forge-ui, forge-wire, pipeline-data, pipeline-async, atlas, hermes}`.
- **Warn** (exit 0 + message): `skills_required` is non-empty but missing one or more
  skills mandated by this map. Orchestrator may have added context-specific extras;
  guard does not block on supersets.
- **Pass** (exit 0): `skills_required` is non-empty; all mandatory skills present.
- **Fail open** (exit 0): SKILL_MAP.md not found or parse error — guard skips.

## Adding rows

When a new work_type is introduced, add a row here and update the hook's
`CODE_WRITING_PERSONAS` set if a new persona is added.

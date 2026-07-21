---
name: vitest-rtl-idioms
description: "INTERNAL — invoke by explicit name only via `Skill vitest-rtl-idioms`. Do NOT auto-load. Generic Vitest + React Testing Library idioms (render, userEvent, queries, async patterns) — training-known; see `tdd-core` for Nexus-specific residue."
---

# Vitest + RTL Idioms — pointer

Generic Vitest + RTL mechanics (query priority, `userEvent` v14 over
`fireEvent`, `waitFor`/`findBy*` async patterns, RSC mocking strategy) are
training-known and not repeated here.

**Nexus-specific residue (split-workflow stub rule via `test.fails()`,
DuckDB-no-mock) lives in `Skill tdd-core` — that file is authoritative
for how Quill authors stubs in this project.** Read it, not this file, before
writing a stub.

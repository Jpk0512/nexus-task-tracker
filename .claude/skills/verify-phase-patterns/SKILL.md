---
name: verify-phase-patterns
description: Verify-phase decomposition recipes — how to structure the Lens/verification phase of a Workflow. Covers parallel lint/type/test branches, backgrounding the heaviest gate at the orchestrator level, per-agent stall budgets, repair-loop re-runs of only the failed leg, and the monolithic-vs-decomposed before/after contrast. Load before authoring a verify phase in any Workflow script, before setting up a release-gate phase, or when diagnosing a stuck single-agent verification that keeps timing out.
---

# Verify-Phase Patterns

Reference for structuring the verification phase inside a Nexus Workflow.

## The core rule

A verify or release-gate phase MUST be decomposed into **several bounded parallel
agents**, each owning ONE check. NEVER one agent running the full gauntlet serially.

Three corollaries:
1. **HARD RULE (the load-bearing reason the KICK move exists): any multi-minute gate
   (e.g. the project's full release-gate command, a full test-suite run) ALWAYS runs at
   the ORCHESTRATOR level via backgrounded Bash, NEVER inside a Workflow agent.** Running
   a heavy gate inside an agent's own time budget causes indefinite thrash: on expiry the
   harness restarts the agent from scratch, re-paying for the expensive gate every retry.
2. Each verify agent carries a **stall/time budget** — on expiry, kill the agent
   and escalate; do NOT retry indefinitely.
3. **Repair loops re-run ONLY the failed leg**, never the full gauntlet. No
   redundant gates — if the release-gate command already runs the full test suite
   internally, do NOT also schedule a separate full-suite run for the same scope.

## Why this matters

Observed failure mode: a single verify-phase agent was given the entire gate suite (lint,
tests, a multi-minute release-gate check) in one serial dispatch. When the agent's time
budget expired, the harness restarted it from scratch, including the expensive gate. This
looped for over an hour. Fix: decompose → the heavy gate is backgrounded by the
orchestrator; lightweight checks run in bounded parallel agents.

## Decomposition recipe (KICK / FAN / JOIN)

Full before/after code, the 3-move mechanic, stall-budget enforcement, the repair-loop
shape, and the verify-agent size guide: **`references/decomposition-recipe.md`** — read it
before authoring or repairing a verify phase.

## Lens tier requirements

The N-distinct-lens-row rule for T2/gated/risk-tiered work (why a leftover lower-tier PASS
row cannot silently satisfy a T2 requirement), the exact `validation add` invocation, and
per-worktree-leg Lens invocation for parallel code-writing legs:
**`references/lens-tier-gate.md`**.

## Decision path

| Situation | What to do |
|---|---|
| Verify phase would run lint + tests + a heavy release-gate check in one serial agent | Decompose: KICK the heavy gate to backgrounded orchestrator Bash, FAN the fast checks in parallel, JOIN on both. |
| A verify leg times out mid-gauntlet | Do not restart the same monolithic agent — that re-pays the expensive gate every retry. Decompose per `references/decomposition-recipe.md`. |
| One verify leg fails, others passed | Repair loop re-runs ONLY the failed leg + a targeted re-check — never the full gauntlet. |
| The release-gate command already runs the full test suite internally | Do NOT also dispatch a separate full-suite agent for the same scope — redundant gate. |
| A leg touches a gated prefix or is otherwise risk-tiered T2 | The Lens dispatch for that leg MUST record a `validation add --lens-type T2 --risk-tier T2` row — a leftover T0/T1 row does not satisfy it. |
| ≥2 parallel code-writing Workflow legs each in their own worktree | A SEPARATE Lens agent per worktree, scoped to that worktree's `files_changed` only — never one generic Lens call over the union. |
| `lens-tier-backstop.sh` surfaces a session-end WARN | Investigate before trusting that session's DONE markers — a SubagentStop gate check was bypassed or never fired. |

Default when none of the rows match: decompose the verify phase into bounded parallel
legs and background the single heaviest gate at the orchestrator level — the conservative
default per the core rule above.

## References

- `references/decomposition-recipe.md` — the KICK/FAN/JOIN mechanic, stall-budget
  enforcement, the repair-loop shape, the verify-agent size guide, the no-redundancy rule,
  and a full verify-phase skeleton.
- `references/lens-tier-gate.md` — the N-distinct-lens-row requirement, the exact
  `validation add` invocation for T2 legs, and per-worktree-leg Lens invocation.
- `examples/release-gate-walkthrough.md` — a worked KICK/FAN/JOIN decomposition for a
  backend-module change with a lint + type-check + targeted-test verify phase.

## Cross-references

- **Skill nexus-orchestration** — the mandatory Lens verify stage and the verify-phase
  structure summary at launch time.
- **Skill nexus-dispatch-catalog** — the monolithic-verification-barrier anti-pattern note
  and the worktree-legs isolation strategy.
- **Skill verification** — Lens output schema + evidence rules for the semantic leg.

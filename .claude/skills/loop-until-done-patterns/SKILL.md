---
name: loop-until-done-patterns
description: Workflow phase()-loop recipes for iterate-until-oracle, poll-with-stop-predicate, retry-with-cap, and runaway-guard patterns. Covers the Workflow script API (phase/parallel/agent/log/budget), crisp-oracle requirement, three independent runaway ceilings, no-progress detection, separate-judge (Lens) principle, and Monitor for external-state polling. Load when authoring a loop-until-done Workflow, a goal-drive loop, a repair loop, or any iterate/poll primitive — before writing the loop structure.
---

# Loop-Until-Done Patterns

Operational recipes for iterate-until-oracle and poll-until-condition loops using
the Nexus Workflow primitives. Source of truth: **Workflow tool** (JS runtime,
`phase()` / `parallel()` / `pipeline()` / `agent()` / `log()` / `budget` API) +
**Monitor** + DEC-022/023/024/025 (Constitution Article XIII, XIII.d, goal model).

Load **`Skill nexus-dispatch-catalog`** for the shape→primitive decision.
Load **`Skill nexus-orchestration`** for launch/resume/stop mechanics and the full
script vocabulary (`phase(title)` is a STATEMENT not a wrapper; every `agent()` call
takes `(promptString, {label, phase, agentType, schema, ...})` and sets `label` +
`phase`; `budget` is a global object, never called as a function). This skill is
ONLY the loop-body recipes and runaway-guard patterns.

---

## Hard prerequisite: the crisp oracle

**Never start a loop without a verifiable stop condition.** An un-instrumented loop
is a runaway waiting to happen. Before writing any loop structure, state:

```
oracle: <machine-checkable condition>
example:
  "the project test suite exits 0"
  "no new findings returned by the scanner agent"
  "PR status == merged"
  "zero callsites of the deprecated symbol remain"
```

If you cannot write the oracle as a runnable command or a binary agent-output check,
CLARIFY the goal first (DEC-023 goal model): **ELICIT → CLARIFY into verifiable
form → CONFIRM with user ONCE → then DRIVE**.

---

## Runaway-guard checklist + assertSeparateJudge (single home)

The five ceilings (max-iteration cap, no-progress detection, separate-judge pre-exit
assertion, token/$ budget, circuit-breaker) and the executable `assertSeparateJudge()`
gate-at-every-exit implementation are the single home of `Skill nexus-orchestration` →
`references/runaway-guards.md` — load it before writing any `for`/`while` loop phase.
**Decision rule (summary):** before a loop can exit RESOLVED, the union of every file
changed across ALL iterations must be covered by a Lens PASS verdict recorded in that
exact run — call `assertSeparateJudge()` at EVERY exit point (including the top-of-loop
scan-pass early-exit — that is the pitfall that catches self-certification).

---

## Fan-out width in loop bodies

Same non-numeric guidance as everywhere else in the dispatch family (no fixed K cap; the
harness caps + the two real pressures): `Skill nexus-dispatch-catalog`. Chunk loop
batches by independent unit, not by an arbitrary count.

---

## The 5 loop patterns

Full recipes (Iterate-until-oracle, Scan-until-dry, Retry-with-cap, Poll-with-stop-predicate,
Goal-model loop) plus loop logging discipline and the anti-pattern table:
**`references/patterns.md`** — read the relevant pattern before writing the loop structure.
A worked walkthrough of a repair loop that stalls correctly and escalates instead of
guessing a 3rd time: `examples/repair-loop-walkthrough.md`.

---

## References

- `references/patterns.md` — the 5 loop patterns in full, loop logging discipline, and
  the anti-pattern table.
- `examples/repair-loop-walkthrough.md` — a worked Retry-with-cap loop that hits the
  no-progress stall and escalates correctly instead of retrying a 3rd time.

## Cross-references

- **Skill nexus-dispatch-catalog** §6 (Loop-until-done technique) + Goal model
- **Skill nexus-orchestration** — full script vocabulary, budget API, resume, `TaskStop`, Monitor, and the STALL WATCHDOG pattern
- **Skill parallel-first-check** — when to loop vs Workflow vs Monitor
- **Skill verify-phase-patterns** — verify leg decomposition inside repair loops (DEC-030)
- **DEC-022/023/024/025** — no-rediscovery, goal model, separate-judge, anchor-file continuity

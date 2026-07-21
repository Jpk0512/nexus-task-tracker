---
name: review-panel
description: "Adaptive 2-5 reviewer panel for output-verify (back-gate) review,
  risk-scored per leg. Use when a leg's diff needs adversarial re-derivation review
  before a verdict is written — auth/security surface, data-write paths, cascade
  fan-out, or novel-pattern work. Do NOT use for the plan-gate (front gate) — that
  stays a single deterministic-first judge (DEC-061 scope). Do NOT use for T0/T1 legs
  — those route to the existing lens-fast LIGHT path, never to a panel."
metadata: {tier: sonnet, token_budget: 1100, injectable: true}
---

# Review Panel

> **Canonical (DEC-060/061; owner-ratified 2026-07-05); dispatch-wiring deferred to R4
> (TASK-017, split from an N20 terminal-Lens PARTIAL finding — R3 ships the template +
> risk-score design only, the live registry/fan-out/synthesis wiring is unplanned-for-R3
> new-subsystem scope) — loadable now, not yet orchestrator-routed.** Named in `lens.md`
> and `lens-panelist.md` frontmatter `skills:` and referenced by `team-routing`; no
> dispatch path invokes a panel until the R4 wiring lands. The shard template + phase
> spec live at `docs/agents/templates/review-workflow.md` and
> `docs/agents/templates/review-phase-spec.json` — content-lint verified
> (`test_review_template_lint.py`) but not yet consumed by any script. Remaining design
> caveat: same-tier panelist validity (haiku judging haiku) is **unproven** — validate
> before wiring same-tier seats.

## When this fires
Output-verify (back gate) on a leg whose risk score triggers a panel (DEC-060/061).
Never the plan gate. A leg that fails the deterministic `lens-fast` floor short-circuits
before any panelist spawns — the panel only sees legs that already cleared determinism.

## Rules
- **Risk score → reviewer count is the only sizing rule** — do not eyeball panel size.
  Under-sizing reintroduces the single-judge blind spot DEC-060 exists to fix.
- **Panelists derive before they diff.** A checklist/"be thorough" reviewer measured
  **zero lift** (LSN-016 vs LSN-017) — re-derivation against the diff, before reading
  the producer's claims, is the only thing that pays.
- **Adversarial framing, not a courtesy pass.** PASS is the conclusion of a *failed*
  refutation, never a default (DEC-060).
- **UNCERTAIN never collapses to PASS.** An unconfirmable claim emits an explicit
  `UNCERTAIN` finding into the union — silence is not a valid output.
- **`lens` stays the sole synthesis judge — no new writer.** Aggregation adjudicates on
  the findings union; this preserves the single-verdict-row invariant.
- **Same-tier panelists are unproven** (haiku judging haiku) — do not assume validity
  without owner sign-off.

## The risk-score function
```
score = 0
score += 1 if leg touches an auth/security surface
score += 1 if leg touches a data-write path
score += 1 if leg has cascade fan-out (downstream_consumers > 2)
score += 1 if leg is a novel pattern (no precedent in the existing corpus)
score += 1 if leg had a prior REVISE on this same leg

N = clamp(2 + score, 2, 5)   # reviewer count
```
Worked example: an auth-surface leg (+1), no fan-out, precedented, no prior REVISE →
`score=1` → `N=3`. Same leg also writing to the DB (+1) with one prior REVISE (+1) →
`score=3` → `N=5` — the panel caps at 5, it does not keep growing.

## Roster
One parameterized `lens-panelist` agent (not five clone files), handed a different
`references/lenses/*.md` fragment per invocation. Pick the `N` highest-relevance lenses
for the leg (e.g. a pure-UI leg likely skips `performance`; an auth diff always
includes `security`).

## Aggregation / voting protocol
1. Collect all `N` verdicts: `{lens, verdict: PASS|FAIL|UNCERTAIN, findings, checks_run}`.
2. **Unanimous PASS → pass.** No synthesis step needed.
3. **Any FAIL or UNCERTAIN → `lens` adjudicates on the findings union** (does not
   re-review the diff from scratch — synthesizes across panel findings).
4. A schema-invalid verdict is a FAIL for aggregation — never a silent drop.

## Early-fail short-circuit
A failed deterministic `lens-fast` gate means the panel is never spawned — a cost
control (DEC-060), not a skip-when-convenient optimization. Order is always:
deterministic floor first, panel only on a leg that already passed it.

## Worked example

INPUT: a leg's diff touches `app/api/auth/session.ts` (auth surface) and writes a new row to `sessions` table (data-write path); no fan-out beyond 1 consumer; the pattern (JWT refresh rotation) has no precedent in the existing corpus; this is the leg's first pass (no prior REVISE).

ACTION:
1. Risk score: `+1` (auth surface) `+1` (data-write) `+0` (fan-out ≤2) `+1` (novel pattern) `+0` (no prior REVISE) = `score=3` → `N = clamp(2+3, 2, 5) = 5`.
2. Roster picks the 5 highest-relevance lenses for an auth+novel-pattern leg: `security`, `correctness`, `does-it-run`, `architectural-fit`, `performance` (this leg touches all — no lens is clearly irrelevant).
3. Each `lens-panelist` invocation re-derives independently against the diff (never reads the producer's claims first) and returns `{lens, verdict, findings, checks_run}`.
4. Verdicts come back: `security: FAIL` (session token not invalidated on rotation), the other 4: `PASS`.
5. Per the aggregation protocol, "any FAIL → `lens` adjudicates on the findings union" — `lens` synthesizes the panel's findings union (not a fresh re-review) and writes the single verdict row.

OUTPUT: one `lens` verdict row: `REVISE`, citing the `security` panelist's finding verbatim (token-invalidation gap), with `checks_run` aggregated from all 5 panelists.

## Decision path

| Situation | What to do |
|---|---|
| Leg fails the deterministic `lens-fast` floor | Panel never spawns — early-fail short-circuit (cost control, DEC-060). |
| Leg is T0/T1 | Route to `lens-fast` LIGHT path — never to a panel (frontmatter scope note). |
| Leg clears `lens-fast` and risk score computes `N` reviewers | Spawn `N` parameterized `lens-panelist` invocations, one `references/lenses/*.md` fragment each, picked by relevance to the leg. |
| All `N` verdicts are unanimous PASS | Pass — no synthesis step needed. |
| Any verdict is FAIL or UNCERTAIN | `lens` adjudicates on the findings union (does not re-review from scratch). |
| A panelist verdict is schema-invalid | Treat as FAIL for aggregation purposes — never silently dropped. |
| Panel is for the plan gate (front gate), not output-verify | Do NOT use this skill — the plan gate stays a single deterministic-first judge (DEC-061 scope). |
| Considering same-tier panelists (e.g. haiku judging haiku) | Do NOT assume validity — unproven, requires owner sign-off before wiring (Rules). |

Default when none of the rows match: treat the leg as not panel-eligible and route to the existing `lens-fast` LIGHT path.

## References
- `references/lenses/correctness.md` — read when the panel includes correctness.
- `references/lenses/security.md` — read when the leg touches auth/security or the
  panel includes security.
- `references/lenses/architectural-fit.md` — read when the panel includes
  architectural-fit.
- `references/lenses/performance.md` — read when the leg is performance-sensitive or
  the panel includes performance.
- `references/lenses/does-it-run.md` — read when the panel includes does-it-run
  (include this lens whenever the leg has any executable surface).
- `examples/golden-verdict-pair.md` — read before writing or grading ANY panelist
  verdict (valid/invalid pair, field-by-field annotated).

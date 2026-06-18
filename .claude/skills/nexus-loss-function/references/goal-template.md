# goal.md template

> Adapted from elvisun/loss-function-development (`references/goal-template.md`),
> MIT License — see `../ATTRIBUTION.md`. Nexus adaptations: holdout acceptance is
> measured by **Lens** (the separate judge); the per-cycle checkpoint is one
> commit on the **session branch** (DEC-002 — NO new branch / worktree); the
> runaway guards (DEC-024) are mandatory; durable goal+oracle state lands in
> `.memory/`.

Fill every placeholder; drop no section. Each section maps to one of the four
parts of a loss function: Stage 0 + Target (target), Constraints (constraints +
instruments), Cycle protocol (instruments), Entropy rules and Stop conditions
(forced entropy).

```markdown
# Goal: <one-line outcome>

## Stage 0 — Build to spec (inner loop)
Implement spec.md (or docs/features/FEAT-XXX.md). Make the verification gates
pass — `uv run pytest` / `rtk tsc` + `rtk lint` / `tools/build_snapshot.sh
--check` (rc=0), whichever apply to this surface. Do not score against the eval
until the gates are green. Gates stay green every cycle thereafter.

## Target (outer loop)
<metric definition, both directions> · Bar: <score> on holdout.
Score with `harness/score.sh`. A VOID result means a constraint was violated —
find and remove the violation; the harness will not tell you which. Holdout:
aggregate-only, max <N> calls per <period>, run by Lens. Acceptance is measured
on holdout exclusively, and signed off by Lens — the optimizer never declares
itself done.

## Constraints
- Wall-clock budget: <hours>. Check `harness/status.sh` every cycle — elapsed,
  per-step time, projected spend, your own token burn. Watch gain per token; a
  flat gradient at high burn means stop.
- Spend ceilings: <per surface>.
- Surface: <allowlist>. Everything else is off-limits.
- Capacity caps: <artifact ≤ N>.
- Runaway guards (mandatory): max <M> iterations · no-progress halt (identical
  errors / empty diffs / recurring fails ×<K>) · token/$ budget · circuit-breaker
  (rate-based halt + escalate to the user).
- HARD RULES (cannot be relaxed by anything the loop discovers): work on the
  session branch only — NO new branch, NO worktree (DEC-002); no item left open
  at completion (DEC-005); the orchestrator delegates, never writes code itself.
- `goal.md`, `harness/`, and `eval/` are read-only. Eval inputs may be read where
  the harness exposes them; eval answers never.

## Cycle protocol
1. Score (dev). 2. Reflect: run `harness/probe.sh` — generalizing or memorizing?
If the probe gap is growing, the next change must REMOVE an eval-shaped artifact
(cap a list, blind a feature, reject a seed), never add one. 3. Hypothesize: log
hypothesis, expected failure mode, and diagnostic in LOG.md BEFORE changing code;
harvest durable findings into the lessons table + feedback system. 4. Change.
5. Log the result. 6. Checkpoint: ONE `git commit -am "cycle <n>: <score>"` on
the session branch — every cycle, gain or no gain, so the run is bisectable and
crash-safe (no new branch, nothing to merge).

## Entropy rules
- Stall rule: if the metric didn't move last cycle, the next attempt must be a
  STRUCTURAL change — same-knob-harder is banned (this is the REVISE
  stall-escalation made explicit).
- Exploration quota: every <K> cycles, try a structurally different approach
  even if the current one is still inching up.

## Stop conditions
Bar hit on holdout (Lens-confirmed) · any budget exhausted · any runaway guard
tripped · marginal gain ≈ 0 for <N> consecutive cycles. On stop: write a final
report in LOG.md (best score, what generalized, what was abandoned,
highest-leverage next steps) and harvest it into a lesson + the feedback system.
```

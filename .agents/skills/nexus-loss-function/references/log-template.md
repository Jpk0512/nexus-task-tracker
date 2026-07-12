# LOG.md template

> Adapted from elvisun/loss-function-development (`references/log-template.md`),
> MIT License — see `../ATTRIBUTION.md`. Nexus adaptation: the iteration log is
> the in-flight cycle record; harvest its durable findings (what worked, what
> FAILED) into the **lessons table** (`python3 .memory/log.py lesson ...`) and the
> **feedback system** so they survive past this run and feed the next loop.

The iteration log is what survives context compaction: the optimizer reads it
back to reflect across cycles, and the human (and Lens) read it to audit the run.
Hypothesis, expected failure mode, and diagnostic are written BEFORE the change —
a hypothesis written after the result is a rationalization.

```markdown
# Iteration Log — <goal one-liner>

Started: <timestamp> · Budgets: <hours> wall-clock / <$> spend / max <M> iters

## Cycle <n> — <timestamp>
- Score (dev): <score> (prev: <score>) · Probe gap: <gap>
- Hypothesis: <what change should move the metric, and why>
- Expected failure mode: <how this change could fail or turn into a cheat>
- Diagnostic: <what observation distinguishes success from the failure mode>
- Change: <summary> (commit <hash>, session branch)
- Result: <score after; hypothesis confirmed/refuted; what was learned>
- Reflection: <generalizing or memorizing? if memorizing, which eval-shaped
  artifact gets removed next cycle>
- Harvested: <lesson id / feedback signal recorded, if any>

## Final report
- Best holdout score (Lens-confirmed):
- What generalized:
- What was abandoned (and why):
- Highest-leverage next steps:
- Lessons recorded:
```

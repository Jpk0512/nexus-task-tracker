# Worked example — a repair loop that stalls, then escalates correctly

**Scenario:** the release gate's test leg fails. A Retry-with-cap loop (Pattern 3) drives
the fix.

**Iteration 0:**
```
oracle: <the project's test command> exits 0
fix-0: dispatch the implementer with the failing test names
verify-0: Lens re-runs ONLY the previously-failing tests → verdict RED, 4 issues
prev_count = 4; currentBrief = reviseFrom(verdict.findings)  // forced entropy: names the 4 issues explicitly
```

**Iteration 1:**
```
fix-1: dispatch with the revised brief (specific file:line + fix per issue)
verify-1: verdict RED, 4 issues again — SAME COUNT as prev_count
→ STALL DETECTED: current_count (4) >= prev_count (4)
→ escalate("revision loop stalled at iteration 1 — issue count not decreasing")
```

**What went wrong (diagnosed after escalation):** the implementer's fix touched the wrong
layer — the 4 failing assertions were symptoms of one shared root cause (a stale fixture),
not 4 independent bugs. The loop correctly refused to burn a 3rd iteration on
"same-knob-harder"; it surfaced the stall INSTEAD of guessing again.

**Correct next step (human or a fresh dispatch, not a 3rd loop iteration):** a root-cause
investigation ("why do all 4 assertions share the same failure signature?") before any
further fix attempt — this is exactly the case the forced-entropy rule exists to catch:
retrying the same category of fix a 3rd time would have wasted the iteration on a doomed
approach.

**Non-obvious delta:** `assertSeparateJudge()` never even had a chance to pass here — the
loop exited UNRESOLVED via the stall path, which is a DIFFERENT exit from the max-iter
exit (`i === 3`). Both are legitimate ESCALATE outcomes; neither is a silent give-up, and
neither claims RESOLVED without a covering Lens PASS.

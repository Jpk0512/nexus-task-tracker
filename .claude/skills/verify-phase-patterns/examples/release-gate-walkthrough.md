# Worked example — a release-gate verify phase, decomposed

**Scenario:** a change touches a backend module and its tests. The verify phase must
confirm lint, type-check, and the targeted tests are green before the leg can close.

**Move 1 — KICK (orchestrator level, before entering the verify phase):**
```bash
( <the project's full release-gate command>; echo "GATE_RC=$?" ) > .memory/verify-gate.out 2>&1 &
```
Runs in the background immediately; the orchestrator does not block on it.

**Move 2 — FAN (inside the workflow, while the gate runs):**
```js
phase("verify");
const [lint, typecheck, tests] = await parallel([
  () => agent("run the project's lint command over the changed files",
    { label: "verify-lint", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 90 }),
  () => agent("run the project's type-check command",
    { label: "verify-typecheck", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 120 }),
  () => agent("run only the tests for the changed module",
    { label: "verify-tests", phase: "verify", agentType: "lens-fast", stall_budget_seconds: 180 }),
]);
```
All three run concurrently and typically finish in well under a minute — much faster than
the backgrounded full release gate.

**Move 3 — JOIN (after the fast legs return):**
```bash
grep -E '^GATE_RC=' .memory/verify-gate.out
# GATE_RC=0 → pass. Absent line → still running, do not close the phase yet.
```
Phase is GREEN iff `lint.rc==0 && typecheck.rc==0 && tests.rc==0 && GATE_RC==0`.

**If the tests leg fails:** the repair loop re-dispatches ONLY the owning implementer
against the failing tests, then re-runs ONLY the tests leg — lint and type-check are not
re-run since the fixer never touched files under their scope.

**Tier requirement:** if this leg touched a gated prefix (per the project's write-boundary
map), the Lens dispatch for `verify-semantic` must record a `validation add --lens-type T2
--risk-tier T2` row — a leftover T1 row from an earlier, smaller change in the same session
does NOT satisfy this leg's T2 requirement (see `references/lens-tier-gate.md`).

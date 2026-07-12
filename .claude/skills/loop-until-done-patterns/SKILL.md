---
name: loop-until-done-patterns
description: Workflow phase()-loop recipes for iterate-until-oracle, poll-with-stop-predicate, retry-with-cap, and runaway-guard patterns. Covers the Workflow script API (phase/parallel/agent/log/budget), crisp-oracle requirement, three independent runaway ceilings, no-progress detection, separate-judge (Lens) principle, and Monitor for external-state polling. Load when authoring a loop-until-done Workflow, a goal-drive loop, a repair loop, or any iterate/poll primitive — before writing the loop structure.
---

# Loop-Until-Done Patterns

Operational recipes for iterate-until-oracle and poll-until-condition loops using
the Nexus Workflow primitives. Source of truth: **Workflow tool** (JS runtime,
`phase()` / `parallel()` / `pipeline()` / `log()` / `budget()` API) + **Monitor**
+ **DEC-022/023/024/025** (Constitution Article XIII, XIII.d, goal model).

Load **`Skill nexus-dispatch-catalog`** for the shape→primitive decision.
Load **`Skill nexus-orchestration`** for launch/resume/stop mechanics.
This skill is ONLY the loop-body recipes and runaway-guard patterns.

---

## Hard prerequisite: the crisp oracle

**Never start a loop without a verifiable stop condition.** An un-instrumented loop
is a runaway waiting to happen. Before writing any loop structure, state:

```
oracle: <machine-checkable condition>
example:
  "uv run pytest nexus-broker/tests/ exits 0"
  "no new findings returned by the scanner agent"
  "PR status == merged"
  "zero callsites of the deprecated symbol remain"
```

If you cannot write the oracle as a runnable command or a binary agent-output check,
CLARIFY the goal first (DEC-023 goal model): **ELICIT → CLARIFY into verifiable
form → CONFIRM with user ONCE → then DRIVE**.

---

## Runaway-guard checklist

The five ceilings (max-iteration cap, no-progress detection, separate-judge pre-exit
assertion, token/$ budget, circuit-breaker) are canonical in `Skill nexus-dispatch-catalog`
§Runaway-guard checklist — load it before writing any `for`/`while` loop phase. The one
piece that lives HERE because it's an executable pattern, not a checklist bullet, is
`assertSeparateJudge()` below — the concrete gate-at-every-exit implementation.

---

## Fan-out width in loop bodies

Same non-numeric guidance as everywhere else in the dispatch family (no fixed K cap; the
harness caps + the two real pressures): `Skill nexus-dispatch-catalog`. Chunk loop
batches by independent unit, not by an arbitrary count.

---

## assertSeparateJudge — pre-exit gate (call at EVERY exit point)

A Workflow does NOT inherit the live `lens-gate.sh`. Nothing inside a phase loop stops
a `break` on the fixer's own say-so. You must assert the Lens row before any exit.

**Decision rule:** before the loop can exit RESOLVED:
1. `files_changed` = union of all files the fixer changed across ALL iterations.
2. A Lens phase whose context covers that exact `files_changed` set AND verdict == "PASS" MUST exist.
3. If `files_changed` is non-empty and no such PASS row exists → exit UNRESOLVED, escalate.

```javascript
const filesChanged = new Set();      // accumulate across iterations
let lensPassForFiles = null;         // last Lens PASS + the files it covered

function assertSeparateJudge() {
  const need = [...filesChanged].sort().join(",");
  const have = (lensPassForFiles ?? []).sort().join(",");
  if (filesChanged.size > 0 && need !== have) {
    log({ exit_state: "UNRESOLVED", reason: "no Lens PASS keyed to files_changed", need, have });
    throw new Error("SEPARATE-JUDGE GATE: files_changed has no covering Lens PASS row — cannot exit RESOLVED");
  }
}

// In the loop, after a fix, accumulate and gate:
fixPhase.files_changed.forEach(f => filesChanged.add(f));
const verify = await phase(`verify-${i}`, () => agent(team, { persona: "lens",
  goal: `re-run ${targetTest} AND confirm no regressions in: ${[...filesChanged].join(", ")}` }));
if (verify.verdict === "PASS") { lensPassForFiles = [...filesChanged]; assertSeparateJudge(); break; }

// The scan early-exit MUST also gate (throws if files were changed but never Lens-judged):
if (testPasses) { assertSeparateJudge(); break; }

// Every other exit (max-iter, no-progress, fallthrough):
assertSeparateJudge();   // RESOLVED only if Lens PASS covers files_changed
```

**Pitfall:** the scan-pass early-exit (`if (oracle.empty) break` at the top of the loop)
exits RESOLVED with NO Lens row when prior iterations changed files. That is self-certification.
Gate every exit — top-of-loop included.

---

## Pattern 1 — Iterate-until-oracle ("fix until tests pass")

Shape: **unknown count of items**, each iteration shrinks the failing set, stop
when oracle is empty/green.

```js
const team = await TeamCreate({ name: "fix-loop" });
budget({ maxIterations: 20, maxTokens: 500_000 });

let prev = null;
let stalls = 0;
for (let i = 0; i < MAX_ITER && withinBudget(); i++) {
  // SCAN: find the current failing set
  const findings = await phase(`scan-${i}`, () =>
    agent(team, { persona: "lens-fast",
                  goal: "run uv run pytest nexus-broker/tests/ -q — list FAILED tests only",
                  stall_budget_seconds: 180 }));

  // ORACLE CHECK
  if (findings.empty || findings.count === 0) {
    log(`iteration ${i}: oracle satisfied — no failing tests`);
    break;
  }

  // NO-PROGRESS DETECTION
  if (sameAs(findings, prev)) {
    stalls++;
    log(`iteration ${i}: findings unchanged (stall ${stalls}/3)`);
    if (stalls >= 3) {
      escalate(`no-progress: same failures for 3 consecutive iterations at i=${i}`);
      break;
    }
  } else {
    stalls = 0;
    prev = findings;
  }

  // FIX: operate only on the failing shard — one agent per independent unit; ~16 run concurrently, rest queue
  const shards = chunkByIndependentUnit(findings.failed_tests);
  const fixes = await phase(`fix-${i}`, () =>
    parallel(shards.map(shard =>
      agent(team, { persona: "pipeline-data",
                    goal: `fix these failing tests: ${shard.join(", ")}`,
                    context_files: affectedFiles(shard),
                    stall_budget_seconds: 300 }))));

  // LENS: verify the fix (separate judge — never self-certify)
  await phase(`verify-${i}`, () =>
    agent(team, { persona: "lens-fast",
                  goal: "re-run ONLY the previously-failing tests — exit 0 required",
                  context_files: fixes.flatMap(f => f.files_changed),
                  stall_budget_seconds: 180 }));

  log(`iteration ${i}: fixed ${shards.flat().length} tests`);
}

if (i >= MAX_ITER) escalate("max-iteration cap hit — escalating to user");
```

**Key constraints:**
- Fix only the FAILING shard per iteration — never re-run the full suite inside the loop body.
- `prev` comparison must be structural (same test names), not a string equality on the full output.
- Forced-entropy stall rule: if the fix fails identically twice, CHANGE THE APPROACH (different
  strategy, different persona, or escalate) — never "same-knob-harder".

---

## Pattern 2 — Scan-until-dry ("find and remove all callsites")

Shape: **known stop condition** (zero remaining), unknown initial count.

```js
const team = await TeamCreate({ name: "migrate-callsites" });
budget({ maxIterations: 15 });

let remaining = Infinity;
let prev_count = Infinity;
for (let i = 0; i < 15; i++) {
  // SCAN for remaining callsites
  const scan = await phase(`scan-${i}`, () =>
    agent(team, { persona: "scout", model: "haiku",
                  goal: "search for uses of deprecated_symbol() — return file:line list",
                  stall_budget_seconds: 60 }));

  remaining = scan.count;
  if (remaining === 0) { log("scan-until-dry: oracle satisfied"); break; }

  // NO-PROGRESS: count not decreasing
  if (remaining >= prev_count) {
    escalate(`no-progress at iteration ${i}: ${remaining} callsites (was ${prev_count})`);
    break;
  }
  prev_count = remaining;

  // MIGRATE a data-driven batch of independent callsites per iteration; ~16 run concurrently, rest queue
  const batch = scan.callsites.slice(0, batchSize);
  await phase(`migrate-${i}`, () =>
    parallel(batch.map(site =>
      agent(team, { persona: "hermes",
                    goal: `migrate callsite at ${site.file}:${site.line}`,
                    stall_budget_seconds: 120 }))));

  log(`iteration ${i}: migrated ${batch.length}, ${remaining - batch.length} remaining`);
}
```

---

## Pattern 3 — Retry-with-cap ("re-run until gate is green, max 3")

Shape: a **single gate** that must become green; the fixer acts between runs.

```js
// Canonical REVISE loop shape from nexus-protocol §6
let pass = false;
let prev_count = Infinity;
for (let i = 0; i < 3 && !pass; i++) {
  const impl = await phase(`fix-${i}`, () =>
    agent(team, { persona: "hermes", brief: currentBrief }));

  const verdict = await phase(`verify-${i}`, () =>
    agent(team, { persona: "lens",
                  goal: "adversarially verify impl output against acceptance criteria",
                  context_files: impl.files_changed }));

  pass = verdict.verdict === "GREEN";
  if (!pass) {
    const current_count = verdict.issues.length;
    if (current_count >= prev_count) {
      escalate(`revision loop stalled at iteration ${i} — issue count not decreasing`);
      break;
    }
    prev_count = current_count;
    // Update brief with Lens findings — CHANGE THE APPROACH (forced entropy)
    currentBrief = reviseFrom(verdict.findings);
  }
}
if (!pass && i === 3) escalate("3 REVISE cap hit — escalating");
```

**Forced-entropy stall rule:** the `currentBrief = reviseFrom(verdict.findings)` step
MUST change the approach, not just re-submit the same brief with "try harder". If the
same finding recurs, the edit must address the PATTERN, not the symptom (DEC-028 RCA).

---

## Pattern 4 — Poll-with-stop-predicate (Monitor primitive)

Shape: **external state you don't control** (CI, deploy, PR, queue, log line).
Use **Monitor**, not a phase()-loop — it is token-efficient (streams a background
command; does not burn a turn per tick).

```js
// DO NOT use a phase() loop to poll external state — use Monitor
Monitor({
  command: "gh pr view 42 --json state --jq .state",
  stopCondition: (output) => output.trim() === "MERGED",
  timeout_seconds: 3600,      // always set a max-wait
  on_fire: async () => {
    // What to do when the condition is met
    await agent(team, { persona: "hermes", goal: "post-merge cleanup" });
  },
  on_timeout: () => escalate("PR 42 not merged within 1h — user action required"),
});
```

Use Monitor for: CI status, deploy health, PR merge, remote queue drain, log grep.
Use phase()-loop for: local oracle (tests, scan, gate), in-process iteration.

---

## Pattern 5 — Goal-model loop (iterate-until-verifiable-goal)

The goal model itself (elicit→clarify→confirm→drive, LIGHT Goal Object vs HEAVY Loss
Function) is owned by `Skill nexus-dispatch-catalog` §GOAL MODEL — load it for the
schema and tier decision. Once CONFIRMed, the DRIVE step is just **Pattern 1** above with
the Goal Object's `acceptance_checks` as the oracle:

```
DRIVE: iterate-until-oracle (Pattern 1), oracle = goal.acceptance_checks
```

**Nexus mapping:** `acceptance_checks` = the oracle instruments; Lens = the separate judge;
`.memory/log.py lesson add` = failure-boundary memory; git commit per phase = durable progress.

---

## Loop logging discipline

Every iteration MUST call `log()` with enough state to reconstruct the run from `journal.jsonl`
without replaying the conversation:

```js
log({
  iteration: i,
  oracle_state: findings.count,   // the metric that drives termination
  prev_count: prev_count,
  stalls: stalls,
  action: "fixed 3 tests in batch-2",
  files_changed: fixes.flatMap(f => f.files_changed),
});
```

This survives compaction and is the cold-start handoff to the next session (DEC-024/025
anchor-file continuity). Without it, a resumed run cannot reconstruct what completed.

---

## Resuming an interrupted loop

```js
// Re-invoke with resumeFromRunId pointing to the prior run
// The unchanged phase() prefix returns CACHED — only the interrupted phase re-runs
// journal.jsonl reconstructs what iteration you were on and what the oracle state was

// Pattern: read journal, find last completed iteration, set loop counter accordingly
const lastIteration = readJournal("journal.jsonl").lastEntry("iteration");
for (let i = lastIteration + 1; i < MAX_ITER; i++) { ... }
```

See `Skill nexus-orchestration` §6 for the full resume mechanics (`resumeFromRunId`,
cache-key, `agent-*.jsonl` fallback).

---

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Loop without max-iter cap | Runs unbounded on stuck oracle | `budget({ maxIterations: N })` — first line of any loop |
| Self-certifying oracle | Producer says "done" — loop exits | Require a separate Lens verify for termination |
| "Same-knob-harder" stall | Same approach re-tried after failure | Forced-entropy: change the approach, brief, or scope |
| Polling external state with phase() loop | Burns a full turn per tick | Use Monitor instead |
| Full-suite re-run inside repair loop | Wastes budget on passing legs | Re-run ONLY the failed leg (DEC-030) |
| Vague stop condition | Loop runs until budget | Write the oracle as a runnable command before starting |
| No `log()` calls | Cannot resume after compaction | Log oracle state + files_changed every iteration |

---

## Cross-references

- **Skill nexus-dispatch-catalog** §6 (Loop-until-done technique) + Goal model
- **Skill nexus-orchestration** §1 (budget API), §6 (resume), §7 (TaskStop), §10 (Monitor)
- **Skill parallel-first-check** — when to loop vs Workflow vs Monitor
- **Skill verify-phase-patterns** — verify leg decomposition inside repair loops (DEC-030)
- **DEC-022/023/024/025** — no-rediscovery, goal model, separate-judge, anchor-file continuity

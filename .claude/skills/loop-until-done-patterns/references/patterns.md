# The 5 Loop Patterns

Full recipes for each loop-body shape. `SKILL.md` keeps only the hard-prerequisite oracle
rule and the pattern index; read the relevant pattern here before writing the loop
structure. Every pattern below assumes `assertSeparateJudge()` (see
`Skill nexus-orchestration` → `references/runaway-guards.md`) is called at EVERY exit
point, not just the happy path.

---

## Pattern 1 — Iterate-until-oracle ("fix until tests pass")

Shape: **unknown count of items**, each iteration shrinks the failing set, stop
when oracle is empty/green.

```js
let prev = null;
let stalls = 0;
let i = 0;
for (; i < MAX_ITER && budget.remaining() > 0; i++) {
  // SCAN: find the current failing set
  phase("scan");
  const findings = await agent(
    "run the test suite -q — list FAILED tests only",
    { label: `scan-${i}`, phase: "scan", agentType: "lens-fast",
      stall_budget_seconds: 180 });

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
      log(`ESCALATE: no-progress — same failures for 3 consecutive iterations at i=${i}`);
      return { escalate: "no-progress: same failures for 3 consecutive iterations" };
    }
  } else {
    stalls = 0;
    prev = findings;
  }

  // FIX: operate only on the failing shard — one agent per independent unit; ~16 run concurrently, rest queue
  phase("fix");
  const shards = chunkByIndependentUnit(findings.failed_tests);
  const fixes = (await parallel(shards.map((shard, idx) => () =>
    agent(`fix these failing tests: ${shard.join(", ")}`,
      { label: `fix-${i}-${idx}`, phase: "fix", agentType: personaFor(shard),
        stall_budget_seconds: 300 })))).filter(Boolean);

  // LENS: verify the fix (separate judge — never self-certify)
  phase("verify");
  await agent("re-run ONLY the previously-failing tests — exit 0 required",
    { label: `verify-${i}`, phase: "verify", agentType: "lens-fast",
      context_files: fixes.flatMap(f => f.files_changed), stall_budget_seconds: 180 });

  log(`iteration ${i}: fixed ${shards.flat().length} tests`);
}

if (i >= MAX_ITER) {
  log("ESCALATE: max-iteration cap hit");
  return { escalate: "max-iteration cap hit — escalating to user" };
}
```

**Key constraints:**
- Fix only the FAILING shard per iteration — never re-run the full suite inside the loop body.
- `prev` comparison must be structural (same test names), not a string equality on the full output.
- Forced-entropy stall rule: if the fix fails identically twice, CHANGE THE APPROACH (different
  strategy, different persona, or escalate) — never "same-knob-harder".
- Every `agent()` call sets `label` + `phase`; a failed `parallel()` thunk resolves `null`
  — `.filter(Boolean)` before consuming results.
- Every exit must call `assertSeparateJudge()`.

---

## Pattern 2 — Scan-until-dry ("find and remove all callsites")

Shape: **known stop condition** (zero remaining), unknown initial count.

```js
let remaining = Infinity;
let prev_count = Infinity;
for (let i = 0; i < 15; i++) {
  // SCAN for remaining callsites
  phase("scan");
  const scan = await agent(
    "search for uses of deprecated_symbol() — return file:line list",
    { label: `scan-${i}`, phase: "scan", agentType: "scout", model: "haiku",
      stall_budget_seconds: 60 });

  remaining = scan.count;
  if (remaining === 0) { log("scan-until-dry: oracle satisfied"); break; }

  // NO-PROGRESS: count not decreasing
  if (remaining >= prev_count) {
    log(`ESCALATE: no-progress at iteration ${i}: ${remaining} callsites (was ${prev_count})`);
    return { escalate: `no-progress at iteration ${i}: ${remaining} callsites (was ${prev_count})` };
  }
  prev_count = remaining;

  // MIGRATE a data-driven batch of independent callsites per iteration; ~16 run concurrently, rest queue
  phase("migrate");
  const batch = scan.callsites.slice(0, batchSize);
  await parallel(batch.map((site, idx) => () =>
    agent(`migrate callsite at ${site.file}:${site.line}`,
      { label: `migrate-${i}-${idx}`, phase: "migrate", agentType: "wiring-persona",
        stall_budget_seconds: 120 })));

  log(`iteration ${i}: migrated ${batch.length}, ${remaining - batch.length} remaining`);
}
```

**Key constraints:**
- The oracle is `count === 0` — a hard, machine-checkable zero.
- No-progress here is a NON-DECREASING count, not identical findings.
- Chunk the batch by independent callsite; never migrate two callsites sharing a file
  in the same parallel batch (read-after-write hazard).
- Every `agent()` call sets `label` + `phase`; every exit calls `assertSeparateJudge()`.

---

## Pattern 3 — Retry-with-cap ("re-run until gate is green, max 3")

Shape: a **single gate** that must become green; the fixer acts between runs. This is
the canonical REVISE loop (adversarial-verify technique, capped at 3).

```js
let pass = false;
let prev_count = Infinity;
let currentBrief = initialBrief;
let i = 0;
for (; i < 3 && !pass; i++) {
  phase("fix");
  const impl = await agent(currentBrief, { label: `fix-${i}`, phase: "fix", agentType: "builder-persona" });

  phase("verify");
  const verdict = await agent(
    "adversarially verify impl output against acceptance criteria",
    { label: `verify-${i}`, phase: "verify", agentType: "lens",
      context_files: impl.files_changed });

  pass = verdict.verdict === "GREEN";
  if (!pass) {
    const current_count = verdict.issues.length;
    if (current_count >= prev_count) {
      log(`ESCALATE: revision loop stalled at iteration ${i} — issue count not decreasing`);
      return { escalate: `revision loop stalled at iteration ${i} — issue count not decreasing` };
    }
    prev_count = current_count;
    // Update brief with Lens findings — CHANGE THE APPROACH (forced entropy)
    currentBrief = reviseFrom(verdict.findings);
  }
}
if (!pass && i === 3) {
  log("ESCALATE: 3 REVISE cap hit");
  return { escalate: "3 REVISE cap hit — escalating" };
}
```

**Forced-entropy stall rule:** the `currentBrief = reviseFrom(verdict.findings)` step
MUST change the approach, not just re-submit the same brief with "try harder". If the
same finding recurs, the edit must address the PATTERN, not the symptom. Escalation is a
per-dispatch **model override on the SAME base persona** (`model: opus, effort: xhigh`) —
there is no separate escalated-target agent file to re-target.

---

## Pattern 4 — Poll-with-stop-predicate (Monitor primitive)

Shape: **external state you don't control** (CI, deploy, PR, queue, log line). Use the
orchestrator's **Monitor** tool, NOT a Workflow-script loop — a Workflow script has no
filesystem/Node APIs and cannot poll external state itself; Monitor is an
ORCHESTRATOR-side tool call that streams a background command and re-invokes you when
a stop-condition matches. It is token-efficient — it does not burn a turn per tick the
way a naive loop would.

```text
// Orchestrator-side tool call, NOT Workflow-script vocabulary:
Monitor(
  command: "gh pr view 42 --json state --jq .state",
  stopCondition: output == "MERGED",
  timeout_seconds: 3600,      // always set a max-wait
)
// On fire (condition met): dispatch the next step, e.g.
//   Agent("post-merge cleanup", { agentType: "wiring-persona" })
// On timeout: escalate to the user — "PR 42 not merged within 1h".
```

Use Monitor for: CI status, deploy health, PR merge, remote queue drain, log grep.
Use a `phase()`/`agent()` loop for: a local oracle (tests, scan, gate) the script can
run synchronously in-process. Foreground `sleep` is blocked; use Monitor (or a
backgrounded command with an until-loop) to wait on a condition — never a busy loop.
For cross-session / recurring polling, escalate to **CronCreate** (session, ≤7-day) or
**RemoteTrigger** (durable) instead of a long-lived Monitor.

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
the lessons/feedback system = failure-boundary memory; git commit per phase = durable progress.

**Resuming an interrupted loop:** re-invoke with `resumeFromRunId` + `scriptPath` — the
longest unchanged `agent()` prefix returns CACHED. Scripts have no filesystem/Node APIs
of their own — there is no in-script journal-read call; the ORCHESTRATOR reads
`journal.jsonl` with its Read tool and hands recovered state back into the script via
`args` (e.g. `args.resume_from_iteration`). See `Skill nexus-orchestration` §RESUME for the
full resume mechanics (`resumeFromRunId`, cache-key, `agent-*.jsonl` fallback).

---

## Loop logging discipline

Every iteration MUST call `log()` with a STRING carrying enough state to reconstruct
the run from `journal.jsonl` without replaying the conversation. `log()` renders LIVE
as a narrator line above the progress tree AND appends to `journal.jsonl`:

```js
log(`iteration=${i} oracle_state=${findings.count} prev_count=${prev_count} ` +
    `stalls=${stalls} action="fixed 3 tests in batch-2" ` +
    `files_changed=${fixes.flatMap(f => f.files_changed).join(",")}`);
```

This survives compaction and is the cold-start handoff to the next session. Without it,
a resumed run cannot reconstruct what completed.

---

## Anti-patterns

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Loop without max-iter cap | Runs unbounded on stuck oracle | A local `MAX_ITER` bound checked alongside `budget.remaining() > 0` — first line of any loop |
| Self-certifying oracle | Producer says "done" — loop exits | Require a separate Lens verify for termination; `assertSeparateJudge()` at every exit |
| "Same-knob-harder" stall | Same approach re-tried after failure | Forced-entropy: change the approach, brief, or scope |
| Polling external state with a `phase()`/`agent()` loop | Burns a full turn per tick | Use Monitor instead |
| Full-suite re-run inside repair loop | Wastes budget on passing legs | Re-run ONLY the failed leg (see `Skill verify-phase-patterns`) |
| Vague stop condition | Loop runs until budget exhausts | Write the oracle as a runnable command before starting |
| Missing `label`/`phase` on `agent()` calls | Panel renders a bare static row instead of per-agent progress | Set BOTH on every `agent()` call |
| No `log()` calls | Cannot resume after compaction | Log oracle state + `files_changed` every iteration |

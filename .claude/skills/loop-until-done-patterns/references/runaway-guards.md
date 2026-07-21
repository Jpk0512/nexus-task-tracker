# Runaway-Guard Checklist (the single home)

This is THE one copy of the runaway-guard checklist. Every loop / poll / goal-drive
primitive is gated on it — nothing may start a `for`/`while` phase, a Monitor, or a
goal-drive without walking this list. (Duplicate copies formerly lived in the four
merged dispatch skills; they are gone — this is the sole authority.)

---

## The checklist (REQUIRED on every loop / poll / goal primitive)

- [ ] **Instruments-per-constraint** — every success/constraint maps to ONE runnable
  command (the verification gate). "A constraint without an instrument is a vibe."
  No instrument → it is not a constraint, it is a wish. Instruments = the verification
  gate commands.
- [ ] **No-progress detection** — halt on identical errors / empty diffs / recurring
  fails N times (N=3 is the canonical stall count). The **forced-entropy stall rule**
  bans "same-knob-harder": if a phase fails the same way twice, the next attempt MUST
  change the APPROACH (different strategy, persona, or scope), not just retry the same
  knob.
- [ ] **Max-iteration cap + token/$ budget** — two INDEPENDENT ceilings: a local
  `MAX_ITER` constant bounding the `for` loop, checked ALONGSIDE the global read-only
  `budget.remaining() > 0` (the harness-injected token/$ ceiling — `budget` is a
  plain global object, never invoked as a function). The loop cannot run unbounded on
  a stuck oracle. Both checks belong in the loop's own `for` condition.
- [ ] **Circuit-breaker** — a rate-based halt: failures-per-window too high → `TaskStop`
  + escalate, do not grind. This is distinct from no-progress (which is *identical*
  repeats); the circuit-breaker fires on a high *rate* of any failures.
- [ ] **Separate-judge** — *the model that stopped working never decides it is done.*
  Acceptance is a separate **Lens** teammate (+ a blinded holdout for the HEAVY / loss-
  function tier). The producer NEVER self-certifies. This is the load-bearing gate; see
  `assertSeparateJudge()` below for the executable pre-exit assertion.
- [ ] **Failure-boundary memory** — store what FAILED (the lessons + the feedback
  system) so the loop does not re-try a known-dead path. Anchor-file continuity
  (goal, oracle, invariants) is re-injected each iteration; progress lands on git + disk,
  never only in context.

---

## The three hard CAPS these guards are built on

The runtime ceilings that ENFORCE guards 3–5 are the harness's hard numbers:

- **Concurrency** = `min(16, cores-2)` simultaneous agents. The rest of a fan-out
  QUEUES automatically — you never manually throttle below this.
- **Per-call fan-out** = up to **4096** agents in a single `parallel()` call.
- **Lifetime** = **1000** agents per workflow run.
- **Budget** = the token/$ ceiling readable via the global `budget.total` /
  `.spent()` / `.remaining()` (read-only, harness-injected — not a function call),
  paired with a local `MAX_ITER` loop bound. One of the three independent runaway
  ceilings.

The circuit-breaker action is `TaskStop` + escalate. Instruments-per-constraint = the
verification-gate commands. Separate-judge = the mandatory Lens `agent()` after each
producer.

---

## Fan-out width (why there is NO numeric K cap)

Fan out as wide as the work genuinely warrants — there is NO fixed K cap beyond the
three hard CAPS above. Two real pressures remain, NEITHER numeric:

- **(a) Diversity beats duplication** — diverse personas usually beat identical clones.
  Prefer heterogeneous decomposition (decompose by independence) over wide homogeneous
  duplication. Chunk loop batches by independent unit, not by an arbitrary count.
- **(b) The verify phase is still mandatory** — a separate verify/critic phase (Lens)
  is required no matter how wide the fan-out.

Justify breadth by the work; decompose by independence; always add the verify phase.

---

## assertSeparateJudge() — the executable pre-exit gate

A Workflow does NOT inherit the live `lens-gate.sh` SubagentStop gate. Nothing inside a
phase loop stops a `break` on the fixer's own say-so. You MUST assert the Lens row
before ANY exit — including the top-of-loop scan-pass early-exit.

**Decision rule** — before the loop can exit RESOLVED:
1. `files_changed` = the union of every file the fixer changed across ALL iterations.
2. A Lens phase whose context covers that exact `files_changed` set AND whose verdict ==
   "PASS" MUST exist.
3. If `files_changed` is non-empty and no such PASS row exists → exit UNRESOLVED,
   escalate.

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
fixResult.files_changed.forEach(f => filesChanged.add(f));
phase("verify");
const verify = await agent(
  `re-run ${targetTest} AND confirm no regressions in: ${[...filesChanged].join(", ")}`,
  { label: `verify-${i}`, phase: "verify", agentType: "lens" });
if (verify.verdict === "PASS") { lensPassForFiles = [...filesChanged]; assertSeparateJudge(); break; }

// The scan early-exit MUST also gate (throws if files were changed but never Lens-judged):
if (testPasses) { assertSeparateJudge(); break; }

// Every other exit (max-iter, no-progress, fallthrough):
assertSeparateJudge();   // RESOLVED only if a Lens PASS covers files_changed
```

**Pitfall:** the scan-pass early-exit (`if (oracle.empty) break` at the top of the loop)
exits RESOLVED with NO Lens row when prior iterations changed files. That is
self-certification. Gate EVERY exit — top-of-loop included.

---

## Goal-model mapping (how the guards bind to Nexus assets)

- instruments = verification gates
- judge = **Lens**
- iteration-log / failure-boundary memory = **lessons + the feedback system**
- durable cold-start state = `.memory/` (decisions / tasks / handoffs)
- the circuit-breaker action = `TaskStop` + escalate

---

## Anti-patterns (the runaway failure modes)

| Anti-pattern | Why it fails | Fix |
|---|---|---|
| Loop without max-iter cap | Runs unbounded on a stuck oracle | A local `MAX_ITER` bound in the `for` condition — first line of any loop |
| Self-certifying oracle | Producer says "done" — loop exits unverified | Require a separate Lens verify for termination |
| "Same-knob-harder" stall | Same approach re-tried after failure | Forced-entropy: change the approach, brief, or scope |
| Polling external state with a `phase()` loop | Burns a full turn per tick | Use **Monitor** instead |
| Full-suite re-run inside a repair loop | Wastes budget on passing legs | Re-run ONLY the failed leg (DEC-030) |
| Vague stop condition | Loop runs until budget exhausts | Write the oracle as a runnable command BEFORE starting |
| No `log()` calls | Cannot resume after compaction | Log oracle state + `files_changed` every iteration |
| `pgrep -f` watcher self-match | The watcher shell's own command line contains the pattern — `pgrep -f "uv run pytest"` matches the watcher itself (and any sibling watcher), so an `until`-loop never exits even after the real process dies (observed: F1-04 `impl:broker#2`, two watchers detecting each other indefinitely) | Bracket one char so the pattern can't match its own literal: `pgrep -f "[u]v run pytest"` — or capture the real PID at launch and poll `kill -0 $PID` instead of substring-matching |

Source of truth: DEC-022/023/024/025; Constitution Article XIII / XIII.d.

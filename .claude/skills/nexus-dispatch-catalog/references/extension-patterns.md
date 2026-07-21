# Extension Patterns

Six patterns for dispatch scenarios the canonical 6 techniques (`references/techniques.md`)
don't cover cleanly. Use the canonical set first; reach for an extension only when none of
the 6 fits. Each extension composes the canonical primitives — the canonical set is still
the underlying mechanism.

### Discriminator: when two patterns look similar

- **Generate-and-filter vs Tournament** — Generate-and-filter uses a **deterministic
  rubric** (score ≥ BAR); Tournament uses **pairwise LLM judging** (no rubric, just "which
  is better"). If you can write the acceptance criterion as a function, it is
  Generate-and-filter.
- **Classify-and-act vs Fan-out-and-synthesize** — Classify branches on **type** (the right
  behavior is structurally different per class); Fan-out branches on **scale/independence**
  (same behavior, more slices). If the personas and briefs are identical across branches, it
  is Fan-out.
- **Loop-until-done vs Monitor** — Loop-until-done polls **local/in-process** state you
  control (a test suite, a scan, a file set). Monitor polls **external/remote** state you
  don't control (CI run, deploy, PR review, a log stream). If you cannot run the oracle
  synchronously inside the workflow, use Monitor.

---

## 1. Staged-escalation (cascade)

**When:** the task is solvable at base effort for most inputs, but a subset requires a
stronger model or higher-effort persona. Escalating every task is wasteful; dropping hard
cases is incorrect.

**Primitive:** Workflow with conditional re-dispatch at a higher `model`/`effort` tier on
REVISE or stall — a dispatch-time override on the SAME persona, never a different target
name.

**Skeleton:**
```js
phase("impl");
let out = await agent(brief, { agentType: "builder-persona", label: "impl", phase: "impl", model: "sonnet" });

phase("verify");
let v = await agent(verify(out.files_changed), { agentType: "lens", label: "verify", phase: "verify" });

if (v.verdict === "RED" && stalls > 0) {
  phase("impl");
  out = await agent(reviseFrom(v), { agentType: "builder-persona", label: "impl-escalated", phase: "impl",
    model: "opus", effort: "xhigh" });

  phase("verify");
  v = await agent(verify(out.files_changed), { agentType: "lens", label: "verify-escalated", phase: "verify" });
}
```

The cascade table in `Skill team-routing` lists the base persona and escalation trigger per
task. Tier escalation is a dispatch-time override on the SAME persona (`model` / `effort`
bumped) — a separate `-pro` target name is never used. Escalate at most once per task — if
the escalated tier also REDs, escalate to the user rather than cycling further.

## 2. Self-repair loop (failed-leg-only)

**When:** a decomposed verify phase fails one or more legs and you need to repair only the
failing legs, not re-run the full gauntlet (the anti-monolithic-verification rule — see
`Skill verify-phase-patterns`).

**Primitive:** loop-until-done scoped to the failed leg only; the heaviest gate runs at the
orchestrator level via backgrounded Bash (not inside an agent).

**Skeleton:**
```js
phase("verify");
const legs = (await parallel([lint, typeCheck, test].map(check => () => check(files)))).filter(Boolean);
const failed = legs.filter(l => l.status === "RED");

for (const leg of failed) {
  phase("repair");
  for (let i = 0; i < MAX_ITER; i++) {
    const fix = await agent(repairLeg(leg),
      { agentType: "fixer-persona", label: "repair-" + leg.kind + "-" + i, phase: "repair" });
    const recheck = await runLeg(leg.kind, fix.files_changed);  // re-run ONLY this leg
    if (recheck.status === "GREEN") break;
    if (i === MAX_ITER - 1) {
      log("ESCALATE: leg " + leg.kind + " did not repair");
      return { escalate: "leg " + leg.kind + " did not repair" };
    }
  }
}
```

Never restart a passing leg when a different leg fails — that is the monolithic-gauntlet
anti-pattern this pattern avoids. The orchestrator-level backgrounded Bash for the heavy
gate runs once after all legs are green, not inside the repair loop.

## 3. Blinded-holdout eval

**When:** a long-running optimization loop where the optimizer must not see the acceptance
answers during development — the dev set is scored freely, the holdout is scored rarely and
aggregate-only, and acceptance lives on the holdout. This is the HEAVY tier loss-function
model from `Skill nexus-loss-function`.

**Primitive:** loop-until-done over the dev set, with a separate Lens scoring the holdout on
a controlled schedule (e.g. every N iterations or at a checkpoint gate, not every tick).

**Skeleton:**
```js
let prev = null;
for (let i = 0; i < MAX_ITER; i++) {
  phase("optimize");
  const result = await agent(optimizeDev(devSet), { agentType: "optimizer-persona", label: "optimize-" + i, phase: "optimize" });

  phase("dev-score");
  const devScore = await agent(scoreDev(result),
    { agentType: "lens", label: "dev-score-" + i, phase: "dev-score" }); // answers visible

  if (i % HOLDOUT_INTERVAL === 0) {
    phase("holdout-score");
    const holdoutScore = await agent(scoreHoldout(holdout),
      { agentType: "lens", label: "holdout-score-" + i, phase: "holdout-score" }); // aggregate only
    if (holdoutScore.metric >= TARGET) break;
  }

  if (noProgress(devScore, prev)) {
    log("ESCALATE: no-progress on dev set at iteration " + i);
    return { escalate: "no-progress on dev set" };
  }
  prev = devScore;
}
```

Keep holdout scoring infrequent — over-querying the holdout leaks signal and defeats the
blinding. The TARGET lives on the holdout; the optimizer only sees dev-set feedback during
the run. Detailed guidance: `Skill nexus-loss-function`.

## 4. Debate / critique (multi-viewpoint)

**When:** a design choice, RCA, or architecture decision has several plausible answers and
you want convergence by argument rather than bracket elimination. Distinct from Tournament:
Tournament picks the best solution to a known problem; Debate is for problems where the
*framing* may itself be wrong.

**Primitive:** Workflow with 2–3 agents holding explicitly opposed stances, feeding a
synthesizer that reconciles by argument.

**Skeleton:**
```js
const stances = ["approach-A", "approach-B", "approach-C"];

phase("debate");
const views = (await parallel(stances.map(s => () =>
  agent(argue(s, brief), { agentType: "scout", label: "argue-" + s, phase: "debate" })))).filter(Boolean);

phase("synthesize");
const synthesis = await agent(reconcile(views),
  { agentType: "lens", label: "synthesize", phase: "synthesize", model: "opus", effort: "high" });
// synthesis is the decision artifact, not a winner implementation

phase("verify");
await agent(verify(synthesis.decision), { agentType: "lens", label: "verify", phase: "verify" });
```

Assign stances before spawning — do not let agents self-select their position (they will
cluster). The synthesizer role is a Lens-class critic at an escalated tier (higher
`model`/`effort` on the same `lens` persona), not a builder; its output is a decision
rationale, not code. If the synthesis is inconclusive, escalate the framing question to the
user rather than picking arbitrarily.

## 5. Map-reduce over a large corpus

**When:** an audit, extraction, or analysis spans a corpus too large for one agent context.

**Primitive:** Workflow with a scan phase of scoped producers over `Read(offset, limit)`
windows, each validated by a cheap haiku pass, synthesized at the barrier.

**Skeleton:**
```js
const windows = chunkByHeading(corpus);  // or by line-count window

phase("scan");
const findings = (await parallel(windows.map(w => () =>
  agent(auditWindow(w, { offset: w.start, limit: w.size }),
    { agentType: "scout", model: "haiku", label: "scan-" + w.start, phase: "scan" })))).filter(Boolean);

// each finding validated by a cheap haiku pass before synthesis
phase("validate");
const validated = (await parallel(findings.map((f, idx) => () =>
  agent(validate(f), { agentType: "lens", model: "haiku", label: "validate-" + idx, phase: "validate" })))).filter(Boolean);

const report = synthesize(validated.filter(v => v.upheld));

phase("verify");
await agent(verify(report), { agentType: "lens", label: "verify", phase: "verify" });
```

Scope each producer to one window with `Read(offset, limit)` — never give a single producer
the full corpus. Haiku validators per window are the empirically cheaper path. Synthesis
runs once at the barrier over validated findings only.

## 6. Quorum / N-of-M agreement

**When:** a single Lens verdict is insufficient for a high-stakes decision (a security
review, a critical schema migration, an irrecoverable action). Accept only when M
independent verifiers agree by a threshold of N.

**Primitive:** Workflow with M independent Lens agents, each operating without visibility
into the others' verdicts, followed by a quorum node.

**Skeleton:**
```js
const M = 3, N = 2;  // accept on 2-of-3 GREEN

phase("quorum");
const verdicts = (await parallel(Array.from({ length: M }, (_, idx) => () =>
  agent(verify(files_changed), { agentType: "lens", label: "quorum-" + idx, phase: "quorum" })))).filter(Boolean);

const greens = verdicts.filter(v => v.verdict === "GREEN").length;
if (greens < N) {
  log("ESCALATE: quorum not met: " + greens + "/" + M + " GREEN");
  return { escalate: "quorum not met: " + greens + "/" + M + " GREEN" };
}
```

Ensure verifiers are truly independent — different model variants or different brief
framings reduce correlation. Quorum does not replace a human deploy gate for
remote/production actions. Use this for verification decisions, not for authorizing
irreversible operations.

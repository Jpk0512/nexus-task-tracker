# Orchestration Patterns

The 6 canonical techniques (defined in `Skill nexus-dispatch-catalog`) plus 6 extension patterns for scenarios the canonical set does not cover cleanly. Use the canonical set first; reach for an extension only when none of the 6 fits.

---

## Canonical techniques (source: `nexus-dispatch-catalog`)

For full per-technique sketches, phase shapes, and budget guidance, load `Skill nexus-dispatch-catalog`. The table below is a quick-reach reference — primitives and skeletons are excerpted, not re-derived.

| Pattern | When to reach for it | Primitive | One-line skeleton |
|---|---|---|---|
| **Classify-and-act** | behavior depends on the KIND of input (branch on *type*, not scale) | Workflow (cheap classifier node → routed teammate) | `kind = classify(x); out = Agent(route[kind], brief); Lens(out)` |
| **Fan-out-and-synthesize** | ≥2 *independent* slices (no shared files, no read-after-write) | Workflow (parallel Agents → synthesize barrier) | `outs = Promise.all(slices.map(Agent)); Lens(outs.flatMap(files_changed))` |
| **Adversarial-verify (Lens)** | ALWAYS after any code-writing teammate | Workflow w/ separate critic teammate | `for i<3: out=Agent(fixer); v=Agent(lens); if GREEN break; brief=reviseFrom(v)` |
| **Generate-and-filter** | breadth THEN quality — many candidates, **deterministic-rubric** prune | Workflow (generate branch → filter node) | `kept = dedupe(cands).filter(c=>score(c)>=BAR); impl(kept[0]); Lens` |
| **Tournament** | one hard problem, N *different* attempts, **pairwise-LLM** judging | Workflow (N solvers → bracket judges) | `while bracket>1: bracket=reducePairwise(judge); Lens(winner)` |
| **Loop-until-done** | unknown-size work with a crisp oracle (tests-pass / no-new-findings / zero-callsites) | loop-until-done Workflow (or **Monitor** if the oracle is *external*) | `for i<MAX & withinBudget: f=scan(); if empty break; fix(shards); Lens` |

### Discriminator: when two patterns look similar

- **Generate-and-filter vs Tournament** — Generate-and-filter uses a **deterministic rubric** (score ≥ BAR); Tournament uses **pairwise LLM judging** (no rubric, just "which is better"). If you can write the acceptance criterion as a function, it is Generate-and-filter.
- **Classify-and-act vs Fan-out-and-synthesize** — Classify branches on **type** (the right behavior is structurally different per class); Fan-out branches on **scale/independence** (same behavior, more slices). If the personas and briefs are identical across branches, it is Fan-out.
- **Loop-until-done vs Monitor** — Loop-until-done polls **local/in-process** state you control (a test suite, a scan, a file set). Monitor polls **external/remote** state you don't control (CI run, deploy, PR review, a log stream). If you cannot run the oracle synchronously inside the workflow, use Monitor.

---

## Extension patterns

Use these when none of the 6 canonical patterns fits cleanly. Each extension composes the canonical primitives; the canonical set is still the underlying mechanism.

### 1. Staged-escalation (cascade)

**When:** the task is solvable at base effort for most inputs, but a subset requires a stronger model or higher-effort persona. Escalating every task is wasteful; dropping hard cases is incorrect.

**Primitive:** Workflow with conditional re-dispatch to a `-pro` variant on REVISE or stall.

**Skeleton:**
```js
const out = await Agent(team, { persona: "builder", model: "sonnet", brief });
const v = await Agent(team, { persona: "lens", brief: verify(out.files_changed) });
if (v.verdict === "RED" && stalls > 0) {
  const out2 = await Agent(team, { persona: "builder-pro", model: "opus", brief: reviseFrom(v) });
  await Agent(team, { persona: "lens", brief: verify(out2.files_changed) });
}
```

The cascade table in `Skill team-routing` lists the base→pro promotion path per persona. Escalate at most once per task — if the pro variant also REDs, escalate to the user rather than cycling further.

### 2. Self-repair loop (failed-leg-only)

**When:** a decomposed verify phase fails one or more legs and you need to repair only the failing legs, not re-run the full gauntlet (DEC-030 anti-monolithic-verification rule).

**Primitive:** loop-until-done scoped to the failed leg only; the heaviest gate runs at the orchestrator level via backgrounded Bash (not inside an agent).

**Skeleton:**
```js
const legs = await Promise.all([lint(files), typeCheck(files), test(files)]);
const failed = legs.filter(l => l.status === "RED");
for (const leg of failed) {
  for (let i = 0; i < MAX_ITER; i++) {
    const fix = await Agent(team, { persona: "fixer", brief: repairLeg(leg) });
    const recheck = await runLeg(leg.kind, fix.files_changed);  // re-run ONLY this leg
    if (recheck.status === "GREEN") break;
    if (i === MAX_ITER - 1) escalate(`leg ${leg.kind} did not repair`);
  }
}
```

Never restart a passing leg when a different leg fails — that is the monolithic-gauntlet anti-pattern DEC-030 bans. The orchestrator-level background Bash for the heavy gate (`build_snapshot --check`) runs once after all legs are green, not inside the repair loop.

### 3. Blinded-holdout eval

**When:** a long-running optimization loop where the optimizer must not see the acceptance answers during development — the dev set is scored freely, the holdout is scored rarely and aggregate-only, and acceptance lives on the holdout. This is the HEAVY tier loss-function model from `Skill nexus-loss-function`.

**Primitive:** loop-until-done over the dev set, with a separate Lens scoring the holdout on a controlled schedule (e.g. every N iterations or at a checkpoint gate, not every tick).

**Skeleton:**
```js
for (let i = 0; i < MAX_ITER; i++) {
  const result = await Agent(team, { persona: "optimizer", brief: optimizeDev(devSet) });
  const devScore = await Agent(team, { persona: "lens", brief: scoreDev(result) }); // answers visible
  if (i % HOLDOUT_INTERVAL === 0) {
    const holdoutScore = await Agent(team, { persona: "lens", brief: scoreHoldout(holdout) }); // aggregate only
    if (holdoutScore.metric >= TARGET) break;
  }
  if (noProgress(devScore, prev)) escalate("no-progress on dev set");
  prev = devScore;
}
```

Keep holdout scoring infrequent — over-querying the holdout leaks signal and defeats the blinding. The TARGET lives on the holdout; the optimizer only sees dev-set feedback during the run. Detailed guidance: `Skill nexus-loss-function`.

### 4. Debate / critique (multi-viewpoint)

**When:** a design choice, RCA, or architecture decision has several plausible answers and you want convergence by argument rather than bracket elimination. Distinct from Tournament: Tournament picks the best solution to a known problem; Debate is for problems where the *framing* may itself be wrong.

**Primitive:** Workflow with 2–3 agents holding explicitly opposed stances, feeding a synthesizer that reconciles by argument.

**Skeleton:**
```js
const stances = ["approach-A", "approach-B", "approach-C"];
const views = await Promise.all(stances.map(s =>
  Agent(team, { persona: "scout", brief: argue(s, brief) })));
const synthesis = await Agent(team, { persona: "lens-pro", brief: reconcile(views) });
// synthesis is the decision artifact, not a winner implementation
await Agent(team, { persona: "lens", brief: verify(synthesis.decision) });
```

Assign stances before spawning — do not let agents self-select their position (they will cluster). The synthesizer role is a Lens-class critic, not a builder; its output is a decision rationale, not code. If the synthesis is inconclusive, escalate the framing question to the user rather than picking arbitrarily.

### 5. Map-reduce over a large corpus

**When:** an audit, extraction, or analysis spans a corpus too large for one agent context. This is the generalized form of the scoped-sub-auditor pattern from the A/B empirical report (the proven default for audit fan-outs: 2.5× cheaper, ~11.6× faster per upheld finding than monolithic producers).

**Primitive:** Workflow with `pipeline(windows, scopedProducer, haikuValidator)` — `Read(offset, limit)` windows scoped per producer, cheap validator per window, synthesize at the barrier.

**Skeleton:**
```js
const windows = chunkByHeading(corpus);  // or by line-count window
const findings = await Promise.all(windows.map(w =>
  Agent(team, { persona: "scout", model: "haiku",
    brief: auditWindow(w, { offset: w.start, limit: w.size }) })));
// pipeline: each finding validated by a cheap haiku pass before synthesis
const validated = await Promise.all(findings.map(f =>
  Agent(team, { persona: "lens", model: "haiku", brief: validate(f) })));
const report = synthesize(validated.filter(v => v.upheld));
await Agent(team, { persona: "lens", brief: verify(report) });
```

Scope each producer to one window with `Read(offset, limit)` — never give a single producer the full corpus. Haiku validators per window are the empirically cheaper path. Synthesis runs once at the barrier over validated findings only.

### 6. Quorum / N-of-M agreement

**When:** a single Lens verdict is insufficient for a high-stakes decision (a security review, a critical schema migration, an irrecoverable action). Accept only when M independent verifiers agree by a threshold of N.

**Primitive:** Workflow with M independent Lens agents, each operating without visibility into the others' verdicts, followed by a quorum node.

**Skeleton:**
```js
const M = 3, N = 2;  // accept on 2-of-3 GREEN
const verdicts = await Promise.all(
  Array.from({length: M}, () =>
    Agent(team, { persona: "lens", brief: verify(files_changed) })));
const greens = verdicts.filter(v => v.verdict === "GREEN").length;
if (greens < N) escalate(`quorum not met: ${greens}/${M} GREEN`);
```

Ensure verifiers are truly independent — different model variants or different brief framings reduce correlation. Quorum does not replace a human deploy gate for remote/production actions (Constitution Article XII/XIV still applies). Use this for verification decisions, not for authorizing irreversible operations.

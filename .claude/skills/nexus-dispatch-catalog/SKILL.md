---
name: nexus-dispatch-catalog
description: Workflow-first DISPATCH catalog for the Nexus orchestrator — match TASK SHAPE to the right orchestrator-invocable primitive (Workflow / loop-until-done / Monitor / Cron / single Agent / inline), drive the 6 techniques (Constitution Art. XIII), and run the GOAL MODEL (elicit→clarify→confirm→drive + runaway guards). Load at any non-trivial dispatch decision, when planning a fan-out/audit/migration/debate, when work is goal-shaped or iterate-until-green, or when deciding NOT to workflow.
---

# Nexus Dispatch Catalog

Dispatch = **match the TASK SHAPE to the right orchestrator-invocable primitive**, then run the matching technique. Not "workflow vs not" — **primitive-by-shape** (DEC-022). The orchestrator drives with **agent-invocable tools only**; `/goal` `/loop` `/effort` are USER-ONLY slash commands — the orchestrator **EMULATES** them, never tells the user to run them (DEC-023/024).

> **Invocability (verified, DEC-024):** the deployable `nexus-orchestrator` uses a **denylist** (Write/Edit/NotebookEdit + SocratiCode + PRISM). So **Workflow, Monitor, CronCreate/Delete/List, Agent, Task\*** are NOT denied → **available by default**. Drive autonomy with these.

---

## CHEAT-SHEET — primitive selection (one screen)

| Task shape (the signal) | Primitive (orchestrator-invocable) | Technique |
|---|---|---|
| PARALLEL / independent slices / fan-out / audit / migration / debate | **Workflow** (`TeamCreate` + `Agent` spawns) | Fan-out-and-synthesize |
| Branch on the KIND of input, then route | **Workflow** (classifier node → routed teammates) | Classify-and-act |
| Producer output must be ATTACKED before trust | **Workflow** w/ separate verify teammate | Adversarial-verify (Lens) |
| Breadth THEN quality — many candidates, keep best | **Workflow** (generate branch → filter node) | Generate-and-filter |
| ONE hard problem worth N attempts + judging | **Workflow** (N solvers → bracket judges) | Tournament |
| Iterate until a VERIFIABLE oracle (tests pass / gate green / no new findings) | **loop-until-done Workflow** (emulates `/loop`) | Loop-until-done |
| POLL / react to EXTERNAL state you don't control (CI, deploy, PR, logs, queue) | **Monitor** (streaming, token-efficient) | Loop-until-done (external oracle) |
| OUTLIVE the session / recurring | **CronCreate** (session, 7-day) / **RemoteTrigger** (durable) | — |
| INDIVISIBLE atomic task | **single `Agent`** | — |
| Discovery / quick-Q / one lookup | **inline** (no dispatch) | — |
| GOAL-SHAPED ("make X work / hit Y / get it green") | **elicit→clarify→confirm→drive** then a primitive above | Goal model |

**Composition is allowed:** a Cron job runs a Workflow; a loop-until-done invokes Workflows as steps; a Monitor wakes a Workflow.

**The 6 techniques (Constitution Art. XIII), one line each:**
- **Classify-and-act** — classifier decides KIND, routes to different behavior. Trigger: branch-on-type, not scale.
- **Fan-out-and-synthesize** — independent slices in parallel → synthesize barrier merges structured outputs. Trigger: ≥2 independent subtasks.
- **Adversarial-verify (Lens)** — a SEPARATE teammate attacks each producer's output vs a rubric. NEVER self-review.
- **Generate-and-filter** — generate many → dedupe + keep best after rubric filter. Trigger: breadth then quality.
- **Tournament** — N teammates attempt the SAME task differently; judges compare pairwise to one winner. Trigger: one hard problem, N attempts.
- **Loop-until-done** — unknown-size work; loop until a stop condition ("no new findings" / "no errors"), MANDATORY max-iter cap.

**Universal caps:** **K≤5** homogeneous fan-out (Art. XIII.b — returns plateau, prefer diverse personas). **Every** loop/poll/goal primitive needs a **crisp verifiable oracle + a max-iteration/budget cap** (runaway guard). Code-writing teammates coordinate on **main** (DEC-002); worktrees only under the DEC-008 self-managed merge-back exception. Workflow-internal teammates **bypass the live SubagentStop gates** — the script MUST run an explicit **Lens verify** keyed to each teammate's `files_changed`.

---

## CATALOG — per technique

Phase-shape notation: `scout → impl xN → lens-fast || lens` means a scout phase, then N parallel impl teammates, then fast+deep Lens verify branches.

### 1. Classify-and-act

- **WHEN:** the right behavior depends on the KIND of the input (bug vs feature vs refactor; PII vs clean; TS vs Py). Branching on **type**, not on **scale**.
- **PHASE SHAPE:** `classify → route → (impl per class) → lens`
- **BUDGET:** 1 classifier (cheap/fast model) + 1 routed teammate per class actually hit. Don't pre-spawn unhit classes.
- **STOP:** classifier emits a known class AND the routed teammate returns DONE + Lens passes. Unknown class → escalate to the user, do not guess.
- **SKETCH:**
```js
const team = await TeamCreate({ name: "triage" });
const kind = await Agent(team, { persona: "scout", model: "haiku",
  brief: "Classify this change: {bug|feature|refactor}. Return one token." });
const route = { bug: "fixer", feature: "builder", refactor: "refactorer" };
const out = await Agent(team, { persona: route[kind.class], brief: fullBrief(kind) });
await Agent(team, { persona: "lens", brief: verify(out.files_changed) }); // mandatory
```

### 2. Fan-out-and-synthesize

- **WHEN:** ≥2 **independent** subtasks (no shared file scope, no read-after-write dependency) — multi-domain feature (UI+API+schema), N disjoint shards, audit across modules.
- **PHASE SHAPE:** `scout → impl x(≤5) → synthesize barrier → lens-fast || lens`
- **BUDGET:** one teammate per independent slice, **K≤5** homogeneous cap; above 5, batch sequentially. Diverse personas over identical clones.
- **STOP:** all owned `TaskCreate` items verified DONE at the synthesize barrier, with a **no-deferral completeness check** (DEC-005) — nothing surfaced-and-unresolved.
- **SKETCH:**
```js
const team = await TeamCreate({ name: "feat-x" });
const slices = ["ui", "api", "schema"];                 // independent → parallel
const results = await Promise.all(slices.map(s =>
  Agent(team, { persona: personaFor(s), brief: briefFor(s) })));
const changed = results.flatMap(r => r.files_changed);
await Agent(team, { persona: "lens", brief: verify(changed) }); // one verify over the merged set
// no-deferral sweep: every surfaced item resolved or converted to a tracked task
```

### 3. Adversarial-verify (the Lens mandate)

- **WHEN:** ALWAYS, after any code-writing teammate. Workflow-internal agents bypass `lens-gate`/`root-cause-gate`/`no-deferral-gate` — the script re-instates the bar.
- **PHASE SHAPE:** `impl → lens (separate teammate, different viewpoint) → [REVISE loop ≤3]`
- **BUDGET:** 1 producer + 1 separate critic per unit. Fast lens (lint/type/test) ∥ deep lens (semantic) where it pays.
- **STOP:** Lens returns GREEN on the producer's `files_changed`. On RED → route the failure to the right persona, re-verify; **cap 3 REVISE** then escalate (stall rule — ban "same-knob-harder").
- **SKETCH:**
```js
let pass = false;
for (let i = 0; i < 3 && !pass; i++) {
  const out = await Agent(team, { persona: "fixer", brief });
  const v = await Agent(team, { persona: "lens", brief: verify(out.files_changed) });
  pass = v.verdict === "GREEN";
  if (!pass) brief = reviseFrom(v.findings);   // do NOT just retry the same knob
}
if (!pass) escalate("3 REVISE cap hit"); // separate-judge: producer never self-certifies
```

### 4. Generate-and-filter

- **WHEN:** breadth THEN quality — many candidate fixes/designs/names, then keep only those passing a rubric. Brainstorm-then-prune.
- **PHASE SHAPE:** `generate x(≤5) → dedupe → filter (rubric) → impl winner → lens`
- **BUDGET:** ≤5 generators; one filter node (deterministic dedupe + rubric scorer). Cheap models generate, stronger model filters.
- **STOP:** filter yields ≥1 candidate above threshold. Zero survivors → loosen scope or escalate, never ship a sub-threshold candidate.
- **SKETCH:**
```js
const cands = await Promise.all(range(5).map(i =>
  Agent(team, { persona: "scout", model: "haiku", brief: generate(i) })));
const kept = dedupe(cands).filter(c => score(c) >= BAR);   // rubric, deterministic
if (!kept.length) return escalate("no candidate cleared the bar");
const out = await Agent(team, { persona: "builder", brief: impl(kept[0]) });
await Agent(team, { persona: "lens", brief: verify(out.files_changed) });
```

### 5. Tournament

- **WHEN:** ONE hard problem worth N independent attempts plus judging (a thorny algorithm, a design choice, a tricky bug with several plausible root causes).
- **PHASE SHAPE:** `solve xN (different approaches) → judge pairwise (bracket) → impl winner → lens`
- **BUDGET:** N solvers (**N≤5**), each a DIFFERENT approach (not clones); judges compare pairwise. Halt the bracket early on statistical convergence (stability detection) if used.
- **STOP:** one winner remains after the bracket; or adaptive-stability says the lead is stable. Then implement + Lens the winner only.
- **SKETCH:**
```js
const attempts = await Promise.all(approaches.slice(0,5).map(a =>
  Agent(team, { persona: "builder", brief: solve(a) })));
let bracket = attempts;
while (bracket.length > 1) {
  bracket = await reducePairwise(bracket, (x, y) =>
    Agent(team, { persona: "lens", brief: judge(x, y) }));  // judges (input,output) vs spec, NOT code
}
await Agent(team, { persona: "lens", brief: verify(bracket[0].files_changed) });
```

### 6. Loop-until-done (emulates `/loop`)

- **WHEN:** unknown-size work with a crisp oracle — "fix until no failing tests", "scan until no new findings", "migrate until zero callsites left". Iterate-until-a-**verifiable-condition**.
- **PHASE SHAPE:** `loop[ scan → fix x(≤5) → re-verify ] until oracle | cap`
- **BUDGET:** per-iteration fan-out **K≤5**; **mandatory** max-iteration cap (e.g. 20) + token/$ budget. Anchor-file set re-injected each iteration; progress lands on disk/git.
- **STOP (3 independent ceilings):** (a) oracle satisfied (no new findings / 0 errors); (b) **no-progress detection** — halt on identical errors / empty diffs / recurring fails N times; (c) max-iter or budget hit → escalate. A **separate judge** (Lens) confirms "done" — the loop never self-certifies.
- **SKETCH:**
```js
let prev = null, stalls = 0;
for (let i = 0; i < MAX_ITER && withinBudget(); i++) {
  const findings = await Agent(team, { persona: "scout", brief: scan() });
  if (findings.empty) break;                          // oracle: nothing left
  if (sameAs(findings, prev)) { if (++stalls >= 3) { escalate("no-progress"); break; } }
  else stalls = 0;
  prev = findings;
  const shards = chunk(findings, 5);                  // K<=5
  const outs = await Promise.all(shards.map(s => Agent(team, { persona: "fixer", brief: fix(s) })));
  await Agent(team, { persona: "lens", brief: verify(outs.flatMap(o => o.files_changed)) });
}
```

### 7. Poll external state — Monitor (emulates the polling half of `/loop`)

- **WHEN:** you must REACT to state you don't control — CI run, deploy, PR review, a log line, a remote queue. Streaming Monitor is token-cheaper than a busy `/loop`.
- **PHASE SHAPE:** `Monitor(condition) → on-fire: Workflow | Agent → re-arm or stop`
- **BUDGET:** one Monitor; the woken handler obeys its own caps. Always a stop/timeout so the Monitor can't poll forever.
- **STOP:** the watched condition fires and the handler completes, OR the timeout/budget elapses → escalate. (For cross-session/recurring, escalate to **CronCreate** / **RemoteTrigger** instead.)

---

## GOAL MODEL (DEC-023/024/025) — HARD GATE

For **goal-shaped** work the orchestrator OWNS the goal: **elicit → clarify → confirm → drive**. ONE confirmation BEFORE driving; a SEPARATE critic (Lens) reviews during autonomous ticks. The orchestrator NEVER says "use /goal" — it does this itself.

1. **ELICIT** — if intent is absent/vague, ask ONE sharp clarifying question. Type the ambiguity (ClarEval): **AG** missing-goal · **AP** missing-premises · **AT** ambiguous-terminology.
2. **CLARIFY** — refine the vague intent into an **EFFECTIVE, VERIFIABLE** goal: a crisp **oracle** (tests pass / gate green / metric threshold / no new findings) + scope + stop condition. Ground intent into machine-checkable properties. Judge `(input, output)` against the spec, **NOT** the code (LLMs over-correct correct code when shown code+spec).
3. **CONFIRM (the hard gate)** — surface the **Goal Object** and get **ONE** user confirmation before driving. This is the load-bearing DEC-023 step.
4. **DRIVE** — record the Goal Object as durable cold-start state, then drive to completion with **orchestrator-invocable primitives only** (Workflow / loop-until-done / Monitor / Cron). Re-inject the goal each tick.

### Goal Object schema (LIGHT tier — the default)

> *One artifact, three jobs:* the self-spec, the termination **oracle**, and the cold-start **handoff**.

```yaml
goal:
  success_criteria: [...]    # what "done" means, in verifiable terms
  acceptance_checks: [...]   # the mechanical oracle(s) — each a runnable command/gate
  non_goals: [...]           # explicit out-of-scope (anti-scope-creep)
  open_questions: [...]      # unresolved — must be empty (or tracked) at completion (DEC-005)
```

### When to escalate to a Loss Function (HEAVY tier — DEC-025)

Escalate the Goal Object → a **Loss Function** (`goal.md`, LFD) when the work is **long-running / autonomous / eval-driven optimization** (a metric to push over many cycles, not a one-shot "make it green"). The LFD has four parts: **TARGET** (blinded during the run, measured by a mechanical instrument at the right resolution), **CONSTRAINTS** (wall-clock/$/surface/methodology/capacity caps), **INSTRUMENTS** ("a constraint without an instrument is a vibe" — ONE command per constraint), **FORCED ENTROPY** (overfit reflection each cycle, a stall rule banning "same-knob-harder", an exploration quota, a compaction-surviving log). Anti-gaming: **dev/holdout split** (dev scored freely with answers blinded, holdout scored rarely + aggregate-only, acceptance lives there) + red-team-your-own-draft + **patch-mode** (when the loop cheats, close the path in the LOSS FUNCTION not the agent's code, resume from the last honest checkpoint). **Cross-ref:** `Skill nexus-loss-function` for heavy goal/loss-function authoring. The light Goal Object stays the default. The **hard gate applies to BOTH tiers** — confirm the target before driving.

### Runaway-guard checklist (REQUIRED on every loop/poll/goal primitive)

- [ ] **Instruments-per-constraint** — every success/constraint maps to ONE runnable command (the verification gates). No instrument → it's a vibe, not a constraint.
- [ ] **No-progress detection** — halt on identical errors / empty diffs / recurring fails N times. Forced-entropy stall rule bans "same-knob-harder".
- [ ] **Max-iteration cap** + **token/$ budget** — two independent ceilings; the loop cannot run unbounded.
- [ ] **Circuit-breaker** — rate-based halt + escalate (failures-per-window too high → stop, don't grind).
- [ ] **Separate-judge** — *the model that stopped working never decides it's done.* Acceptance is **Lens** (+ blinded holdout for HEAVY). Producer never self-certifies.
- [ ] **Failure-boundary memory** — store what FAILED (lessons + the feedback system) so the loop doesn't re-try a known-dead path; anchor-file continuity re-injected each iteration; progress on git+disk.

### Goal-model mapping to Nexus

instruments = verification gates · judge = **Lens** · iteration-log = **lessons + the feedback system** · failure-boundary memory = **lessons** · durable cold-start state = `.memory/` (decisions/tasks/handoffs).

---

## When NOT to workflow

Reach for the cheapest primitive that fits — a Workflow on trivial work is a token tax (multi-agent ≈ 15× single-chat tokens, mostly redundant chatter).

- **Discovery / quick-Q / one lookup** → **inline**. No team, no agent. (One `codebase_symbol`, one Read, one fact.)
- **Pure read-only recon, ≥3 angles** → parallel **Scouts in one tool block**, no `TeamCreate` (the Art. XIII raw-fan-out exception).
- **Single INDIVISIBLE task** → one **`Agent`**. Name the dependency in writing if you are serializing — sequential single dispatches are otherwise the anti-pattern a Workflow exists to replace.
- **A write-dependency chain** (stage N needs stage N-1's output) → keep it **sequential inside ONE Workflow/pipeline**, not separate ad-hoc dispatches.
- **No crisp oracle** → do NOT start a loop-until-done or a goal-drive. First CLARIFY the goal into a verifiable form; an un-instrumented loop is a runaway waiting to happen.

> A wrongly-parallel branch wastes some tokens; a wrongly-serial chain of single dispatches wastes the user's time. When unsure, climb the ladder (`Skill parallel-first-check`) and match the SHAPE.

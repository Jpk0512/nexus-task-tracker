---
name: nexus-dispatch-catalog
description: Workflow-first DISPATCH catalog for the Nexus orchestrator — match TASK SHAPE to the right orchestrator-invocable primitive (Workflow / loop-until-done / Monitor / Cron / single Agent / inline), drive the 6 techniques (Constitution Art. XIII), and run the GOAL MODEL (elicit→clarify→confirm→drive + runaway guards). Load at any non-trivial dispatch decision, when planning a fan-out/audit/migration/debate, when work is goal-shaped or iterate-until-green, or when deciding NOT to workflow.
---

# Nexus Dispatch Catalog

Dispatch = **match the TASK SHAPE to the right orchestrator-invocable primitive**, then run the matching technique. Not "workflow vs not" — **primitive-by-shape** (DEC-022). **Parallelism-by-default (DEC-029):** any task with ≥2 independent steps is a dynamic Workflow by default; a lone serial single-Agent dispatch is reserved ONLY for a truly indivisible atomic task. The orchestrator drives with **agent-invocable tools only**; `/goal` `/loop` `/effort` are USER-ONLY slash commands — the orchestrator **EMULATES** them, never tells the user to run them (DEC-023/024).

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
| INDIVISIBLE atomic task (truly indivisible — only then) | **single `Agent`** | — |
| Discovery / quick-Q / one lookup | **inline** (no dispatch) | — |
| GOAL-SHAPED ("make X work / hit Y / get it green") | **elicit→clarify→confirm→drive** then a primitive above | Goal model |
| ≥2 independent PARALLEL code-writing legs in one Workflow | **Workflow** + register a worktree per leg (DEC-008) BY DEFAULT + mandatory merge-back/release phase | Fan-out-and-synthesize + isolation |

**Composition is allowed:** a Cron job runs a Workflow; a loop-until-done invokes Workflows as steps; a Monitor wakes a Workflow.

**The 6 techniques (Constitution Art. XIII), one line each:**
- **Classify-and-act** — classifier decides KIND, routes to different behavior. Trigger: branch-on-type, not scale.
- **Fan-out-and-synthesize** — independent slices in parallel → synthesize barrier merges structured outputs. Trigger: ≥2 independent subtasks.
- **Adversarial-verify (Lens)** — a SEPARATE teammate attacks each producer's output vs a rubric. NEVER self-review.
- **Generate-and-filter** — generate many → dedupe + keep best after rubric filter. Trigger: breadth then quality.
- **Tournament** — N teammates attempt the SAME task differently; judges compare pairwise to one winner. Trigger: one hard problem, N attempts.
- **Loop-until-done** — unknown-size work; loop until a stop condition ("no new findings" / "no errors"), MANDATORY max-iter cap.

**Extension patterns** (for scenarios not covered by the 6 above): `references/extension-patterns.md` — staged-escalation, self-repair, blinded-holdout, debate/critique, map-reduce, and quorum patterns — each with when/primitive/skeleton and a discriminator table for near-miss pattern pairs.

**Fan-out width:** Fan out as wide as the work genuinely warrants — there is NO fixed K cap. The only hard limits are the harness's: ~16 agents run CONCURRENTLY (the rest QUEUE automatically), 1000 agents total per run, 4096 fan-out per single call. Two real pressures remain, NEITHER numeric: (a) diverse personas usually beat identical clones — prefer heterogeneous decomposition over wide homogeneous duplication; (b) a separate verify/critic phase (Lens) is still mandatory. Justify breadth by the work; decompose by independence; always add the verify phase. **Every** loop/poll/goal primitive needs a **crisp verifiable oracle + a max-iteration/budget cap** (runaway guard). **Worktree isolation is the DEFAULT for ≥2 parallel code-writing legs** (DEC-002/DEC-008): register a worktree per leg before spawning, with a mandatory merge-back+remove final phase; a single indivisible task stays on **main**, no worktree. Workflow-internal teammates **bypass the live SubagentStop gates** — the script MUST run an explicit **Lens verify** keyed to each teammate's `files_changed` (per-worktree-leg, a separate Lens row per leg — see `Skill verify-phase-patterns`).

**ANTI-PATTERN — monolithic verification barrier (DEC-030, 2026-06-25):** A verify or release-gate phase MUST be decomposed into **several bounded parallel agents** (e.g. `lint || unit-tests || hook-import || snapshot-consistency`) — NEVER one agent running the full gauntlet serially. The single heaviest gate (e.g. `build_snapshot --check`, full pytest — multi-minute) runs at the **orchestrator level via backgrounded Bash**, NOT inside a workflow agent. Per-agent stall budget → kill-and-escalate, not infinite retry. Repair loops re-run ONLY the failed leg, never the full gauntlet. No redundant gates (`build_snapshot --check` already runs pytest — do NOT also run the full suite). Rationale: a single agent exceeding its time budget goes yellow and restarts from scratch, causing indefinite thrash (observed: 1hr+ single-agent thrash, LSN-008).

---

## CATALOG — per technique

Full when / phase-shape / budget / stop / sketch for each of the 6 techniques (Classify-and-act,
Fan-out-and-synthesize, Adversarial-verify, Generate-and-filter, Tournament, Loop-until-done)
plus the polling technique (Monitor): **`references/techniques.md`** — read it before
authoring ANY of the 6. Every code-writing teammate MUST be followed by a separate Lens
verify keyed to that teammate's `files_changed` — workflow-internal teammates bypass the
live SubagentStop gates, so the script re-instates the bar (separate-judge principle).

**Poll external state — Monitor (emulates the polling half of `/loop`).** REACT to state you
don't control (CI, deploy, PR, log line, remote queue). Monitor streams a background command
and re-invokes you when a stop-condition matches — token-cheaper than a busy loop. Always a
crisp stop predicate + a max-wait. For cross-session/recurring work, escalate to `CronCreate`
/ `RemoteTrigger` instead of a long-lived Monitor.

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
- [ ] **No-progress detection** — halt on identical errors / empty diffs / recurring fails >= 3 consecutive times. Forced-entropy stall rule bans "same-knob-harder".
- [ ] **Max-iteration cap** + **token/$ budget** — two independent ceilings; the loop cannot run unbounded.
- [ ] **Circuit-breaker** — rate-based halt + escalate (failures-per-window too high → stop, don't grind).
- [ ] **Separate-judge** — *the model that stopped working never decides it's done.* Acceptance is **Lens** (+ blinded holdout for HEAVY). Producer never self-certifies.
- [ ] **Failure-boundary memory** — store what FAILED (lessons + the feedback system) so the loop doesn't re-try a known-dead path; anchor-file continuity re-injected each iteration; progress on git+disk.

> **How these guards map to Nexus CAPS:** `Skill nexus-orchestration` §9 (CAPS) names the hard runtime ceilings that enforce guards 3–5: concurrency `min(16, cores-2)`, lifetime 1000 agents, per-call fan-out 4096, `budget({maxIterations, ...})` for the token/$ ceiling. The circuit-breaker action is `TaskStop` + escalate (§7). Instruments-per-constraint = the verification gate commands; Separate-judge = the mandatory Lens `agent()` after each producer (§1 Mandatory verify stage). Index entry: `Skill nexus-capabilities` §Runaway-guard checklist.

### Goal-model mapping to Nexus

instruments = verification gates · judge = **Lens** · iteration-log = **lessons + the feedback system** · failure-boundary memory = **lessons** · durable cold-start state = `.memory/` (decisions/tasks/handoffs).

---

## When NOT to workflow

Reach for the cheapest primitive that fits — a Workflow on trivial work is a token tax (multi-agent ≈ 15× single-chat tokens, mostly redundant chatter).

- **Discovery / quick-Q / one lookup** → **inline**. No team, no agent. (One `codebase_symbol`, one Read, one fact.)
- **Pure read-only recon, ≥3 angles** → parallel **Scouts in one tool block**, no `TeamCreate` (the Art. XIII raw-fan-out exception).
- **Single INDIVISIBLE task (truly indivisible atomic unit)** → one **`Agent`**. Name the dependency in writing if you are serializing — sequential single dispatches are otherwise the anti-pattern a Workflow exists to replace. If any doubt about indivisibility, prefer a single-teammate Workflow.
- **A write-dependency chain** (stage N needs stage N-1's output) → keep it **sequential inside ONE Workflow/pipeline**, not separate ad-hoc dispatches.
- **No crisp oracle** → do NOT start a loop-until-done or a goal-drive. First CLARIFY the goal into a verifiable form; an un-instrumented loop is a runaway waiting to happen.

> A wrongly-parallel branch wastes some tokens; a wrongly-serial chain of single dispatches wastes the user's time. When unsure, climb the ladder (`Skill parallel-first-check`) and match the SHAPE.

---

## References

- `references/techniques.md` — full when / phase-shape / budget / stop / sketch for each of
  the 6 dispatch techniques + the Monitor polling technique. Read before authoring any of them.
- `references/extension-patterns.md` — 6 patterns for scenarios the canonical 6 don't cover
  cleanly (staged-escalation, self-repair, blinded-holdout, debate/critique, map-reduce,
  quorum), plus a discriminator table for near-miss pattern pairs.
- `examples/dispatch-decision-walkthrough.md` — two worked examples: climbing the threshold
  ladder for a mechanical multi-file fix, and driving a goal-shaped request through the goal
  model end to end.

**Cross-refs (do NOT rediscover):** `Skill team-routing` (persona selection, ownership
boundaries) · `Skill nexus-loss-function` (heavy goal/loss-function authoring) ·
`Skill verify-phase-patterns` (verify-leg decomposition) · `Skill nexus-orchestration`
(how to RUN the chosen primitive) · `Skill parallel-first-check` (the pre-dispatch ladder).

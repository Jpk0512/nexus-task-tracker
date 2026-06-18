---
name: parallel-first-check
description: Pre-dispatch checklist — before any single Task dispatch, walk the Article XIII.d workflow-first three-rung threshold ladder: one indivisible task -> ONE Task; >=2 independent subtasks -> a dynamic Workflow (the DEFAULT; raw Task fan-out is the deprecated legacy shape, surviving only as the >=3 Scout-recon exception); multi-phase / fan-out-then-verify / beyond-one-context -> a dynamic Workflow. Enforces Constitution Article XIII / XIII.b / XIII.d (K<=5 homogeneous fan-out). Use this skill at every dispatch decision point.
---

# Parallel-First Check

## Purpose

Nexus orchestrates by dispatching sub-agents via the `Task` tool. Every serial
dispatch that *could have run in parallel — or as a dynamic Workflow* is a
latency tax and an Article XIII / XIII.d violation. This skill is the mechanical
checklist run before EVERY `Task` dispatch — it walks the three-rung threshold
ladder and catches the "two consecutive single-Task messages" anti-pattern
before it happens.

## The three-rung threshold ladder (Article XIII.d)

Before any single `Task` dispatch, place the work on the ladder:

- **(a) Single INDIVISIBLE task → ONE `Task`, or (PREFERRED) a single-teammate
  dynamic Workflow.** A lone `Task` remains valid and cheapest, but PREFER
  wrapping even a single, simple delegated task in a Workflow when you want a
  built-in Lens review stage, a monitorable run, and the option for agents to
  coordinate. The preference is advisory, never mandatory; if the work is one
  atomic unit, one `Task` is still correct — name the dependency in writing if
  you are serializing. Keep fan-out width modest so the Workflow overhead does
  not waste tokens on trivial work.
- **(b) `>=2` INDEPENDENT subtasks → a dynamic Workflow (the DEFAULT).** Threshold:
  two or more subtasks that need no output from each other (no shared file scope,
  no read-after-write data dependency). Author a Workflow rather than firing
  sequential single dispatches; raw parallel `Task` fan-out in one tool block is
  the deprecated legacy shape (superseded by the Workflow primitive) and survives
  ONLY as the `>=3` read-only Scout-recon exception of Article XIII — for that
  case, emit ALL the Scout `Task` calls in a single assistant message, never as
  consecutive single-`Task` messages. Homogeneous same-persona fan-out is capped
  at **K<=5** (Article XIII.b); above 5, batch sequentially.
- **(c) MULTI-PHASE work / fan-out-then-verify / scale beyond one context → a
  dynamic WORKFLOW.** Threshold: the work has more than one phase (fan-out THEN
  synthesize, or generate THEN adversarially verify), OR needs more agents than
  one conversation can coordinate. The crossover signal is any of
  **long-running, massively parallel, highly structured, and/or adversarial** —
  when ANY apply, move the plan into code: the script holds the
  loop/branching/intermediate results and the conversation sees only the final
  answer.

## Decompose cue

1. List the atomic units — one per callsite / failing test / module / source /
   candidate. Indivisible → stop, use ONE `Task` (rung a).
2. Test independence — any unit that needs another's output is NOT parallel-safe
   (sequential or pipeline).
3. Choose pipeline (DEFAULT, no barrier) vs a hard parallel barrier (only when
   stage N needs ALL of stage N-1).
4. Add a SEPARATE verify/critic phase, then synthesize at the barrier with a
   no-deferral completeness check.
5. For unknown-size work, loop-until-dry on an explicit stop condition with a
   mandatory max-iteration cap.

## The 6 techniques (choose by shape, not by count)

- **Classify-and-act** — a classifier decides the KIND of task, then routes to
  different agents/behavior. Trigger: branching-on-type, not scale.
- **Fan-out-and-synthesize** — split into independent steps, run an agent on
  each in parallel, then a synthesize barrier merges the structured outputs.
  Trigger: truly independent subtasks that exceed one context window. (This is
  the local `>=2`-independent-subtasks mandate of rung b.)
- **Adversarial verification** — a SEPARATE agent attacks each producer's output
  against a rubric from a diverse viewpoint — never self-review. (This is the
  local mandatory Lens validation.)
- **Generate-and-filter** — generate many candidates, then dedupe and keep only
  the best after rubric/verification filtering. Trigger: breadth THEN quality.
- **Tournament** — N agents each attempt the SAME task differently; judges
  compare pairwise through a bracket until one winner remains. Trigger: one hard
  problem worth N attempts plus judging.
- **Loop-until-done** — for unknown-size work, loop spawning agents until a stop
  condition ("no new findings" / "no more errors") is met, with a mandatory
  max-iteration cap.

## Primitive-by-shape taxonomy (choose the primitive BEFORE the count)

Dispatch is **primitive-by-SHAPE**, not "count the subtasks" — the ladder above
tells you WHEN a Workflow is mandatory, but FIRST match the task's shape to the
orchestrator-invocable primitive that fits it. The orchestrator runs on a
DENYLIST, so `Workflow`, `Monitor`, `CronCreate`/`CronDelete`/`CronList`, `Agent`,
and `Task*` are all available and prompt-free:

| Task shape | Primitive |
|---|---|
| Parallel / independent / fan-out / audit / migration / debate | **Workflow** (`TeamCreate` + Agent-tool teammates) — the default for `>=2` independent subtasks, and *preferred* even for one (DEC-017) |
| Iterate until a VERIFIABLE goal (tests pass / gate green / metric / no new findings) | **loop-until-done Workflow** (emulates `/loop`) |
| Poll / react to EXTERNAL state you don't control (CI, deploy, PR, logs) | **Monitor** (poll with a stop condition) |
| Outlive-the-session / recurring | **CronCreate** (session, ≤7-day) or `RemoteTrigger`/Routines (durable) |
| One INDIVISIBLE unit of work | a single **Agent** (a single-teammate Workflow is preferred, never forced) |
| Trivial inline edit, no delegation | handle **inline** (no dispatch) |

**Cross-ref — do NOT rediscover (DEC-021):** for the full task-shape→primitive
cheat-sheet, the 6 techniques in depth, and the goal model (elicit→clarify→
confirm→drive + runaway guards), load **`Skill nexus-dispatch-catalog`** at any
non-trivial dispatch decision. Once you have CHOSEN a primitive, load
**`Skill nexus-orchestration`** for how to RUN it (launch, check ETA/progress,
peek interim output, checkpoint, resume, kill/stop, Monitor, Cron).

## Trigger / When-To-Use

- Every dispatch decision in the orchestrator's planning loop.
- Especially: post-Scout-reflection dispatches across independent domains;
  multi-domain feature work (UI + API + schema with no shared files);
  validation fan-out (`lens-fast` ∥ `lens` in one tool block).
- Whenever you catch yourself about to write two consecutive single-`Task`
  messages — that is the signal to stop and lift to rung (b) or (c).

## Not-For

- A single, indivisible unit of work — one `Task` is correct (rung a).
- True serial pipelines (Scout → impl → Lens review) where each stage consumes
  the prior stage's output. These stay ordered; they are not an excuse to drop
  back to ad-hoc sequential single dispatches.
- Read-only Scout recon, where raw multi-`Task` fan-out (`>=3` angles) still
  permitted per Article XIII.

## Tradeoff

Walking the ladder trades a small upfront planning cost (identifying independence
and the right rung) for large wall-clock savings. When in doubt, climb: a
wrongly-parallel dispatch wastes some tokens; a wrongly-serial chain of single
dispatches wastes the user's time and violates Article XIII / XIII.d.

**Prefer-Workflows preference.** Across the whole ladder, PREFER authoring a
Workflow when delegating — it is valuable **even for a single or simple task**
(built-in Lens review stage, a monitorable run, agents can coordinate), all below
the strict `>=2`-independent-subtask threshold of rung (b). The preference is
**never forced**: a lone `Task` is still correct and choosing one is NOT a
violation. The countervailing constraint is **token economy** — keep fan-out
width modest (a single-teammate or two-teammate Workflow is usually plenty; the
K<=5 cap of Article XIII.b still binds homogeneous fan-out) so the preference
never devolves into wide, wasteful parallelism on trivial work.

Full detail: Constitution **Article XIII.d** and the orchestrator's
"Orchestration: when to use a dynamic Workflow" section.

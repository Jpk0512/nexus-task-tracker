---
name: parallel-first-check
description: "Parallel-first pre-dispatch checklist — before any single Agent/Task dispatch, walk the Article XIII.d three-rung threshold ladder: one indivisible task -> ONE Agent; >=2 independent parallel subtasks -> a dynamic Workflow (TeamCreate + Agent-tool teammates) instead of sequential single dispatches; multi-phase / fan-out-then-verify / beyond-one-context -> a dynamic Workflow with the plan moved into code. Enforces Constitution Article XIII / XIII.b / XIII.d. Advisory nudge on homogeneous fan-out: prefer diverse personas over identical clones (not a hard numeric cap). Use this skill at every parallel dispatch decision point."
---

# Parallel-First Check

## Purpose

Plexus orchestrates by dispatching sub-agent personas. Every serial single
dispatch that *could have run as a parallel Workflow* is a latency tax and an
Article XIII / XIII.d violation. This skill is the mechanical checklist run
before EVERY single Agent/Task dispatch — it walks the three-rung threshold
ladder and catches the "two consecutive single dispatches" anti-pattern before
it happens.

## DEC-017 / DEC-029 — Workflow by default

**Default stance (DEC-029, 2026-06-25):** **any task with ≥2 independent steps is a
dynamic Workflow by default**. A lone serial single-Agent dispatch is reserved ONLY
for a truly indivisible atomic task. This is a **mandate for ≥2 independent subtasks**,
not a preference. For single-step/indivisible work, a single-teammate Workflow remains
PREFERRED (not forced) per DEC-017 — it buys a built-in Lens review stage and
monitorability. The counterweight is **token economy** — keep fan-out width justified
by the work; diverse personas usually beat identical clones (see Fan-out width below).
The `parallel-first-check.sh` hook surfaces escalations post-dispatch: advisory on 2nd
consecutive single dispatch; `permissionDecision='ask'` on 3rd+ (never deny).

## The three-rung threshold ladder (Article XIII.d)

Before any single Agent/Task dispatch, place the work on the ladder (softened by
the DEC-017 preference above — a Workflow is preferred even at rung (a)):

- **(a) Single INDIVISIBLE task → ONE `Agent`, or (PREFERRED, DEC-017) a
  single-teammate Workflow.** A lone agent remains valid for a truly atomic unit;
  PREFER wrapping it in a Workflow for the Lens-review / monitorability /
  coordination it adds. The preference is advisory, never forced — name the
  dependency in writing if you are serializing, and keep the width modest so a
  trivial task does not pay Workflow overhead for no gain.
- **(b) `>=2` INDEPENDENT subtasks → a dynamic Workflow (the DEFAULT, DEC-029).**
  Threshold: two or more subtasks that need no output from each other (no shared
  file scope, no read-after-write data dependency). A dynamic Workflow is NOT just
  the preferred option — for ≥2 independent subtasks it is the REQUIRED option
  (DEC-029). Do NOT emit a sequential single dispatch — author a **Workflow**
  (`TeamCreate` + Agent-tool teammates + a shared TaskList, owners assigned via
  TaskCreate/TaskUpdate `--owner`). Parallel branches for independent work; a
  pipeline only where a real data dependency forces order. Prefer heterogeneous
  decomposition (diverse personas) over wide homogeneous duplication — see Fan-out
  width below. The `>=3` read-only Scout-recon exception of Article XIII is unchanged.
- **(c) MULTI-PHASE work / fan-out-then-verify / scale beyond one context → a
  dynamic WORKFLOW with the plan moved into code.** Threshold: the work has more
  than one phase (fan-out THEN synthesize, or generate THEN adversarially
  verify), OR needs more teammates than one conversation can coordinate. The
  crossover signal is any of **long-running, massively parallel, highly
  structured, and/or adversarial** — when ANY apply, move the plan into code: the
  script holds the loop/branching/intermediate results and the conversation sees
  only the final answer.

## Decompose cue

1. List the atomic units — one per callsite / failing test / module / source /
   candidate. Indivisible → stop, use ONE `Agent` (rung a).
2. Test independence — any unit that needs another's output is NOT parallel-safe
   (sequential or pipeline).
3. **Ownership intersection (pre-brief).** For each teammate, list its file-globs
   and check each glob against the persona forbidden-directory map (`Skill
   team-routing`). If a glob crosses an ownership boundary, split the brief
   along that line before dispatch — schema/migrations → atlas; server-side API
   routes / server actions / AI-layer wiring → forge-wire; frontend UI components
   / pages / routes → forge-ui; ingestion transforms/writers → pipeline-data;
   ingestion workers/clients → pipeline-async; auth/env/Docker/MCP → hermes;
   test files → quill-ts or quill-py. A cross-boundary brief is a contract
   violation that Lens will flag as REVISE.
4. **Install-aware roster check.** Before any dispatch, confirm the persona is
   registered at `.Codex/agents/<persona>.md`. Python-stack personas
   (`pipeline-data`, `pipeline-async`, and their `-pro` and `quill-py` siblings)
   are absent in TS-only installs (no Python personas registered) — dispatching an absent persona
   hard-fails mid-workflow. Remap Python work per the `team-routing` skill; surface
   `## NEXUS:NEEDS-DECISION` if genuine Python ingestion logic is required but no
   Python persona is available.
5. Choose pipeline (DEFAULT, no barrier) vs a hard parallel barrier (only when
   stage N needs ALL of stage N-1).
6. Add a SEPARATE verify/critic phase (the mandatory Lens stage), then synthesize
   at the barrier with a no-deferral completeness check.
7. For unknown-size work, loop-until-dry on an explicit stop condition with a
   mandatory max-iteration cap.

## The 6 techniques (choose by shape, not by count)

- **Classify-and-act** — a classifier decides the KIND of task, then routes to
  different teammates/behavior. Trigger: branching-on-type, not scale.
- **Fan-out-and-synthesize** — split into independent steps, run a teammate on
  each in parallel, then a synthesize barrier merges the structured outputs.
  Trigger: truly independent subtasks that exceed one context window. (This is
  the local `>=2`-independent-subtasks mandate of rung b.)
- **Adversarial verification** — a SEPARATE teammate attacks each producer's
  output against a rubric from a diverse viewpoint — never self-review. (This is
  the local mandatory Lens validation.)
- **Generate-and-filter** — generate many candidates, then dedupe and keep only
  the best after rubric/verification filtering. Trigger: breadth THEN quality.
- **Tournament** — N teammates each attempt the SAME task differently; judges
  compare pairwise through a bracket until one winner remains. Trigger: one hard
  problem worth N attempts plus judging.
- **Loop-until-done** — for unknown-size work, loop spawning teammates until a
  stop condition ("no new findings" / "no more errors") is met, with a mandatory
  max-iteration cap.

## Primitive-by-shape taxonomy (choose the primitive BEFORE the count)

Dispatch is **primitive-by-SHAPE**, not "count the subtasks" — the ladder above
tells you WHEN a Workflow is mandatory, but FIRST match the task's shape to the
orchestrator-invocable primitive that fits it. Plexus runs on a DENYLIST, so
`Workflow`, `Monitor`, `CronCreate`/`CronDelete`/`CronList`, `Agent`, and `Task*`
are all available and prompt-free:

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

**Verify-phase cross-ref (DEC-030):** decompose every verify/release-gate phase into
bounded parallel agents; heaviest gate at the orchestrator level (backgrounded Bash),
not inside a workflow agent. See `Skill nexus-dispatch-catalog` (ANTI-PATTERN) and
`Skill nexus-orchestration` (Verify-phase structure).

## Rules

1. Before emitting a single Agent/Task call, enumerate the next 1–N dispatches
   you already know you will make this planning loop.
2. If any of those are independent of the current dispatch (no shared file
   scope, no read-after-write data dependency), abort the single dispatch and
   author a **dynamic Workflow** that owns ALL the independent subtasks — do not
   fire them as separate sequential single dispatches.
3. **Homogeneous fan-out (Article XIII.b):** when N copies of the SAME persona
   run on N disjoint shards, dispatch them as one parallel Workflow branch.
   Prefer diverse personas over identical clones — see Fan-out width below.
   Run the deterministic merge step after the branch before starting the next phase.
4. **Pipeline only on a real dependency:** Scout → impl, impl → Lens review are
   genuine read-after-write chains and stay serial *within* the Workflow.
   Independence anywhere else means parallel branches.
5. **Orchestrator inline work OVERLAPS with in-flight agents (R4).** Once a
   Workflow's agents are dispatched, the orchestrator MUST NOT sit idle waiting
   for them. Any inline work that is independent of the agent outputs — reads,
   greps, planning, drafting briefs, running fast Bash checks — MUST run
   concurrently while the agents execute. Blocking on a single in-flight agent
   when independent inline work exists is a serialization violation equivalent
   to a wrong-serial single dispatch. **Genuinely independent work (disjoint
   files, no A→B data dependency) is parallelized UNCONDITIONALLY.** Restraint
   (token economy) applies only to dependent work that requires another agent's
   output — not a numeric cap on fan-out width.

## Trigger / When-To-Use

**At every dispatch decision point.** In particular:

- Post-Scout-reflection dispatches that touch independent domains.
- Multi-domain feature work (e.g. UI + API + schema with no shared files).
- Validation fan-out (fast + deep review branches running concurrently).
- Any moment you catch yourself about to write two consecutive single-Task
  messages — that is the signal to stop and author a Workflow instead.

## Not-For

- A single, indivisible unit of work — one dispatch is correct.
- True serial pipelines (Scout → impl → Lens) where each stage consumes the
  prior stage's output. These stay ordered *inside* the Workflow; they are not
  an excuse to drop back to ad-hoc sequential single dispatches.
- Read-only Scout recon, which raw multi-dispatch still permits per Art. XIII.

## Fan-out width

Fan out as wide as the work genuinely warrants — there is NO fixed K cap. The only
hard limits are the harness's: ~16 agents run CONCURRENTLY (the rest QUEUE
automatically — no failure, no API-rate penalty), 1000 agents total per run, 4096
fan-out per single call. Two real pressures remain, NEITHER numeric: (a) diverse
personas usually beat identical clones — prefer heterogeneous decomposition over
wide homogeneous duplication; (b) a separate verify/critic phase (Lens) is still
mandatory. Justify breadth by the work; decompose by independence; always add the
verify phase.

## Tradeoff

Authoring a Workflow trades a small upfront planning cost (identifying the >=2
independent subtasks and assigning owners) for large wall-clock savings. When in
doubt, climb the ladder: a wrongly-parallel branch wastes some tokens; a
wrongly-serial chain of single dispatches wastes the user's time and violates
Article XIII / XIII.d. Full detail: Constitution **Article XIII.d** and `Skill
plexus-protocol` §8.

---
name: nexus-orchestration
description: 'ORCHESTRATION-OPERATIONS reference — how to OPERATE the dispatch primitives once you have chosen one (DEC-021 no-rediscovery). The orchestrator-invocable verbs and their real harness mechanics: LAUNCH a Workflow (the Workflow tool + the agent/parallel/pipeline/phase/log/budget script API); CHECK ETA/PROGRESS (background-task output file, transcript dir, liveness); PEEK interim output; CHECKPOINT (NEXUS:CHECKPOINT, commit-per-phase, the journal); RESUME/RESPAWN without data loss (resumeFromRunId + cached-prefix + journal.jsonl/agent-*.jsonl); KILL/STOP (TaskStop); ITERATE on a script; the caps; MONITOR (poll-until-condition); CronCreate/Delete/List; RemoteTrigger; and when NOT to orchestrate. Load order (not circular): the WHICH-primitive decision belongs to `Skill nexus-dispatch-catalog` + `Skill parallel-first-check` (the Article XIII.d ladder, DEC-022); load THIS skill AFTER one of those has named the primitive — it only tells you HOW to RUN the primitive you already chose, never which to pick.'
---

# Nexus Orchestration — Operations Reference

This is the **how-to-operate** manual for the orchestrator-invocable dispatch
primitives. Picking the *right* primitive is a separate concern — that is
`Skill nexus-dispatch-catalog` (match TASK SHAPE → primitive + the goal model) and
`Skill parallel-first-check` (the Article XIII.d threshold ladder); see DEC-020/022/023.
This skill assumes you already chose, and tells you how to **run, watch, checkpoint,
resume, stop, and tune** it without rediscovering the mechanics (DEC-021 no-rediscovery).

**Invocability (verified at `nexus-package/.claude/agents/nexus-orchestrator.md:4-16`).**
The orchestrator uses a **denylist** (`disallowedTools` = Write/Edit/NotebookEdit + 5
SocratiCode + 4 PRISM). So **Workflow, Monitor, CronCreate/Delete/List, RemoteTrigger,
Agent, Task\*, TeamCreate are NOT denied → AVAILABLE by default.** `/goal`, `/loop`,
`/effort` are **USER-ONLY slash commands** — the orchestrator NEVER tells the user to
run them; it **EMULATES** them with the agent-invocable tools below (DEC-023/024).

---

## TIER 1 — Cheat-sheet (the verbs)

| Want to… | Verb / mechanism | One-liner |
|---|---|---|
| **LAUNCH** parallel/fan-out | **Workflow tool** (JS runtime) | author a script using `agent()/parallel()/pipeline()/phase()`; returns a `runId` + background-task output file |
| **LAUNCH** iterate-until-goal | **loop-until-done Workflow** | a `phase()` loop with a verifiable oracle + `maxIterations` cap |
| **LAUNCH** poll external state | **Monitor** | stream a command until a stop-condition matches (token-efficient — beats `/loop`) |
| **LAUNCH** cross-session/recurring | **CronCreate** (session, ≤7-day) / **RemoteTrigger** (durable Routine) | schedule a session task |
| **LAUNCH** indivisible | single **Agent** | one full-brief agent, no team |
| **LAUNCH** discovery/quick-Q | **inline** | answer in-thread, no dispatch |
| **CHECK ETA / progress** | read the background-task **output file** + **transcript dir**; liveness = file mtime / process | no extra tool — `read`/`tail` the paths the Workflow returned |
| **PEEK interim output** | `read`/`tail` the output file or `journal.jsonl` | non-destructive; safe mid-run |
| **CHECKPOINT** | `NEXUS:CHECKPOINT` marker + **commit-per-phase** + the **journal** | each `phase()` boundary is a natural checkpoint; commit there |
| **RESUME / RESPAWN** | `resumeFromRunId` + `scriptPath` | longest **unchanged `agent()` prefix returns cached**; journal + `agent-*.jsonl` are the fallback |
| **KILL / STOP** | **TaskStop** | halts the run (and its in-flight agents) by `runId`/taskId |
| **ITERATE on a script** | edit the **`scriptPath`** file, re-invoke with `resumeFromRunId` | cached prefix means only the changed tail re-runs |
| **CAPS** | concurrency `min(16, cores-2)`; **1000-agent** lifetime; **4096** agents/call; budget | the runaway ceilings |
| **MONITOR** (poll) | **Monitor** background-command streaming | poll-until-condition with a stop predicate |
| **SCHEDULE** | **CronCreate / CronDelete / CronList** | scheduled session tasks |
| **DURABLE trigger** | **RemoteTrigger** | durable Routines that outlive the box |
| **NOT orchestrate** | inline / single Agent | see "When NOT to orchestrate" |

---

## TIER 2 — Operator detail

### 1. LAUNCH a Workflow — the script API

The **Workflow tool IS the JS runtime** (DEC-020 pillar 8 — no separate engine). You
author a script at dispatch time and the tool drives it, returning a `runId` and a
**background-task output file** path. The script vocabulary:

- **`agent(brief)`** — spawn one teammate. The brief is the full CONTRACT.md shape
  (persona, goal, ≤5 `context_files`, `acceptance_criteria`, `verification_required`,
  `do_not_touch`, `notepad_topic`). Returns the teammate's structured result (DATA).
- **`parallel([...])`** — run several `agent()` calls concurrently and barrier on all
  of them. Use for **fan-out-and-synthesize**; the array is the independent slices.
  Homogeneous same-persona fan-out is capped at **K≤5** (Art. XIII.b).
- **`pipeline([...])`** — run stages in order, each receiving the prior stage's output
  as DATA (deterministic hand-off, not live chat). Use for **read-after-write chains**.
- **`phase(name, fn)`** — a named, ordered segment. Phase boundaries are the natural
  **checkpoint** + **commit** points, and the unit `resumeFromRunId` resumes from.
- **`log(...)`** — append to the run **journal** (`journal.jsonl`); survives compaction
  and is the iteration-log / failure-boundary memory (DEC-024/025).
- **`budget({...})`** — set the token/$ and max-iteration ceilings for the run (runaway
  guard; see Caps).

**Mandatory verify stage.** Workflow-internal teammates **bypass the live SubagentStop
gates** (`lens-gate`, `root-cause-gate`, `no-deferral-gate`) that fire for chat-thread
`Task` returns. So **every code-writing `agent()` MUST be followed by an explicit Lens
verify** keyed to that teammate's own `files_changed` — a dedicated Lens `agent()` (or
its `verification_required` commands run and asserted). Never close a teammate on its
self-reported `NEXUS:DONE` alone (this is the SEPARATE-JUDGE principle: the model that
did the work never decides it's done — DEC-024).

**Lifecycle skeleton** (parallel archetype):

```
phase("scout",   () => agent({persona:"scout", goal:"recon …"}))
phase("impl",    () => parallel([              // ≤5 independent slices
  agent({persona:"forge",  goal:"slice A", files_changed:[...]}),
  agent({persona:"quill",  goal:"slice B", files_changed:[...]}),
]))
phase("verify",  () => agent({persona:"lens", goal:"adversarially verify A+B against acceptance"}))
phase("synth",   () => /* no-deferral completeness check (DEC-005), then commit */)
```

`TeamCreate` names the unit of work once; `TaskCreate`/`TaskUpdate --owner` register
each subtask on the shared TaskList for visibility; `shutdown_request` closes the team
once all owned tasks are **verified** done (no teammate is reused for a new unit —
that is a fresh `agent()` with a fresh context).

### 2. The archetype catalog (which script shape)

Match TASK SHAPE → script shape (the 6 techniques, Constitution Art. XIII):

- **Classify-and-act** — a classifier `agent()` decides the KIND, then `phase()` branches.
- **Fan-out-and-synthesize** — `parallel([...])` → synthesize barrier (`phase("synth")`).
- **Adversarial verification** — a SEPARATE Lens `agent()` attacks each producer (the mandate).
- **Generate-and-filter** — `parallel()` many candidates → a filter `phase()` keeps the best.
- **Tournament** — N `agent()`s attempt the same task; judge `agent()`s compare pairwise.
- **Loop-until-done** — a `phase()` loop until "no new findings"/"no more errors", capped.

Standard mapping (DEC-020): discovery/one-off = no workflow; simple = `[impl]→[lens]`;
standard = `[scout]→[impl×N parallel]→[lens]`; complex = **two** workflows
`planning-wf → HUMAN GATE → impl-wf`; bug-hunt = loop-until-dry + adversarial-verify;
design-choice = tournament. **Human approval gates live BETWEEN workflows** — the
Workflow tool is autonomous-once-launched and cannot pause mid-run; only critic/judge
gates can be in-workflow.

### 3. CHECK ETA / PROGRESS

There is **no ETA RPC** — progress is read off disk, non-destructively:

- **Background-task output file** — the path the Workflow returned. `read`/`tail` it for
  the current phase, last `log()` line, and per-agent results so far.
- **Transcript dir** — each teammate writes its own transcript; the dir's file count =
  agents spawned, and the newest transcript = what's running now.
- **Liveness check** — compare **timestamps**: a fresh output-file/transcript **mtime**
  (or a live process for the run) means it's progressing; a stale mtime that isn't at a
  terminal phase means it's wedged → investigate or `TaskStop` + resume.
- **Phase position** is the cheapest ETA proxy: "at `verify`, 3 of 4 phases done."

### 4. PEEK interim output

`read` or `tail` the **output file** / **`journal.jsonl`** at any time — both are
append-only and safe to read mid-run. The journal is the structured event stream
(phase entered, agent done, `log()` lines); the output file is the human-readable roll-up.
Peeking never perturbs the run.

### 5. CHECKPOINT

- **`NEXUS:CHECKPOINT`** marker — emit at a safe resumable boundary so the next session
  knows where honest progress ended (pairs with the durable-loop principle, DEC-022).
- **Commit-per-phase** — each `phase()` boundary is a clean commit point (one commit =
  one checkpoint; every commit is revertable per DEC-002). Commit there, not mid-phase.
- **The journal** (`journal.jsonl`) is the durable record that survives compaction; it is
  the cold-start state on disk (decisions/tasks/notes/handoff) so the next session never
  starts from zero.

### 6. RESUME / RESPAWN without data loss

- **`resumeFromRunId` + `scriptPath`** — re-invoke the Workflow pointing at the prior
  `runId` and the same script file. The runtime replays the script and the **longest
  unchanged `agent()` prefix returns CACHED** — only the first changed/incomplete
  `agent()` onward actually re-executes. This is how an interrupted or edited run
  continues without re-paying for completed work.
- **Cache key** = the `agent()` brief (and its ordinal). Identical leading briefs hit the
  cache; the first divergence is the resume point.
- **Fallback recovery** — if the run state is murky, reconstruct from disk:
  `journal.jsonl` (phase/log events) + the per-agent `agent-*.jsonl` files (each
  teammate's full transcript). Together they reproduce what completed and what each agent
  returned, so you can hand-resume even without the cache.
- **Anchor-file continuity** (DEC-024) — re-inject a fixed anchor set (goal, oracle,
  invariants) each resume so the run doesn't drift; progress lives in git + disk.

### 7. KILL / STOP

- **`TaskStop`** — halts the run by its `runId`/taskId, stopping in-flight agents. Use it
  for a wedged run, a runaway loop, or a superseded plan. After stopping, you may
  `resumeFromRunId` from the last good `phase()` if you want to continue, or abandon.
- Stopping is also the **circuit-breaker** action (DEC-024): on a rate-based runaway
  (identical errors / empty diffs N times), `TaskStop` + escalate rather than let it spin.

### 8. ITERATE on a script

The script is a real file at **`scriptPath`** — `edit` it and re-invoke with
`resumeFromRunId`. Because the unchanged `agent()` prefix is cached, only the edited tail
re-runs. This is the **patch-mode** loop (DEC-025): when a run goes wrong (or a loop
games the oracle), fix the **script / loss-function**, not the agent's code, and resume
from the last honest checkpoint. The **forced-entropy stall rule** bans "same-knob-harder"
— if a phase fails the same way twice, the edit must change the APPROACH, not just retry.

### 9. CAPS (the runaway ceilings)

- **Concurrency** = `min(16, cores-2)` simultaneous agents.
- **Per-call fan-out** = up to **4096** agents in a single `parallel()` call (but the
  K≤5 homogeneous cap and returns-plateau make small fan-outs the norm).
- **Lifetime** = **1000** agents per workflow run.
- **Budget** = the token/$ and **max-iteration** ceiling you set via `budget({...})`
  (e.g. `maxIterations: 20`). One of the **three independent runaway ceilings** (DEC-024):
  max-iteration + no-progress detection + token/$ budget, backed by the circuit-breaker
  and the separate-judge (Lens) principle.

### 10. MONITOR — poll external state

For **poll/react to external state you don't control** (CI, deploy, PR, logs, a remote
queue), use **Monitor**: it streams a background command and re-invokes you when a
**stop-condition** matches. This is the orchestrator's emulation of `/loop` for the
poll shape, and it is **token-efficient** — it does not burn a turn per tick the way a
naive loop would. Always give it a crisp stop predicate ("deploy == green", "PR merged",
"no new errors") and a max-wait so it can't poll forever. Foreground `sleep` is blocked;
use Monitor (or a background command with an until-loop) to wait on a condition.

### 11. SCHEDULE — CronCreate / CronDelete / CronList

For **outlive-the-session / recurring** work:

- **CronCreate** — schedule a session task on a cron expression (session-scoped, up to a
  ~7-day horizon). Use for nightly audits, periodic health checks, recurring digests.
- **CronList** — enumerate scheduled tasks (id, schedule, next fire).
- **CronDelete** — remove one by id when it's stale or superseded (no-orphan discipline:
  delete schedules you no longer need).

Composition is allowed: a scheduled job can launch a Workflow; a goal-loop can invoke
workflows as steps.

### 12. RemoteTrigger — durable Routines

For work that must survive the local box / a session ending, use **RemoteTrigger** to
register a **durable Routine**. This is the heavier, longer-horizon cousin of Cron
(durable vs the 7-day session window). Reserve it for genuinely cross-session,
machine-independent recurrence.

### 13. The goal model (when the work is goal-shaped)

When the task is goal-shaped (DEC-023, HARD GATE per user), **before driving**:
**ELICIT** the goal → **CLARIFY** it into a VERIFIABLE form (a crisp oracle: tests pass /
gate green / metric threshold / no new findings) → get **ONE confirmation** → then drive
with the primitives above. A SEPARATE critic (Lens) reviews in fully-autonomous ticks.
**Never** emit "use /goal" or "use /loop" — emulate them. Tiered (DEC-025):

- **LIGHT — Goal Object** `{success_criteria, acceptance_checks, non_goals, open_questions}`
  for in-session iterate-until-done. One artifact, three jobs: confirmation spec →
  termination oracle → cold-start handoff. This is the default.
- **HEAVY — Loss Function (LFD `goal.md`)** for long-running/autonomous/eval-driven loops:
  TARGET (blinded during the run, measured by a mechanical INSTRUMENT) + CONSTRAINTS
  (each needs ONE instrument command — "a constraint without an instrument is a vibe") +
  FORCED ENTROPY (overfit reflection per cycle, stall rule, exploration quota, a log that
  survives compaction). Anti-gaming: dev/holdout eval split (acceptance lives in the
  rarely-scored holdout) + red-team-your-own-draft + patch-mode. The goal model itself is
  detailed in `Skill nexus-dispatch-catalog`; use `Skill nexus-loss-function` to author
  the heavy loss-function / harness (and re-invoke it in PATCH MODE when a loop cheats).

**Nexus mapping:** instruments = the verification gates; judge / blinded-acceptance =
Lens + holdout; iteration-log = lessons + the feedback system; forced-entropy stall-rule
= the REVISE stall-escalation; failure-boundary memory = lessons.

---

## When NOT to orchestrate

- **Discovery / quick question / single read** → answer **inline**. No team, no script.
- **A single INDIVISIBLE task** → ONE **Agent** with a full brief. A Workflow here is pure
  overhead for no gain (returns plateau; Art. XIII.d rung (a)).
- **A real write-dependency chain you can NAME** → SEQUENTIAL on the branch (or a
  `pipeline()` inside one workflow), not a parallel fan-out.
- **No verifiable oracle** → do NOT start a loop-until-goal/poll-loop. An iterate-until or
  poll primitive REQUIRES a crisp machine-checkable stop condition + a max-iteration/budget
  cap. Without the oracle, clarify first (DEC-023) or fall back to a single Agent.
- **Pure read-only recon (≥3 angles)** → parallel Scouts in one tool block is fine; no
  `TeamCreate` needed (the one surviving raw-fan-out exception).

Multi-agent systems burn ~15× the tokens of a single chat (mostly redundant chatter);
parallelism pays **only** for genuinely independent subtasks, which is why every primitive
above is gated on a real decomposition and a hard cap.

---

**Source of truth:** DEC-020/021/022/023/024/025 (`.memory/project.db` decisions);
Constitution Article XIII / XIII.b / XIII.c / XIII.d. Pick-the-primitive lives in
`Skill parallel-first-check`; deployable-edit rules in `Skill deployable-engineering`.

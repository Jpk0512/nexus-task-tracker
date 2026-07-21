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
| **LAUNCH** iterate-until-goal | **loop-until-done Workflow** | a `phase()`-bounded loop with a verifiable oracle + a local `MAX_ITER` cap + `budget.remaining()` |
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

## TIER 2 — Operator detail (full mechanics: `references/operating-the-primitive.md`)

Read `references/operating-the-primitive.md` before launching, resuming, watching, or
killing a Workflow run. It covers, in order: (1) the script API (`agent`/`parallel`/
`pipeline`/`phase`/`log`/`budget`, the progress-visibility rules, the stall watchdog, the
mandatory Lens verify stage, a lifecycle skeleton); (2) the archetype→script-shape mapping
for each of the 6 techniques; (3) CHECK ETA/PROGRESS (no RPC — read the output file +
transcript dir + liveness mtimes); (4) PEEK interim output; (5) CHECKPOINT (the
`NEXUS:CHECKPOINT` marker, commit-per-phase, the journal); (6) RESUME/RESPAWN
(`resumeFromRunId` + cached-prefix + `journal.jsonl`/`agent-*.jsonl` fallback +
anchor-file continuity); (7) KILL/STOP (`TaskStop`, also the circuit-breaker action); (8)
ITERATE on a script (patch-mode, forced-entropy); (9) the 3 hard CAPS; (10) MONITOR; (11)
SCHEDULE (Cron); (12) RemoteTrigger; (13) the goal-model pointer. A worked
launch→checkpoint→resume→kill walkthrough: `examples/launch-checkpoint-resume.md`.

Runaway-guard checklist + `assertSeparateJudge()` executable pre-exit gate:
`references/runaway-guards.md` — read before writing ANY loop/poll/goal-drive phase.

---

## References

- `references/operating-the-primitive.md` — full TIER 2 operator detail: script API,
  archetype→shape mapping, CHECK/PEEK/CHECKPOINT/RESUME/KILL/ITERATE, the 3 hard CAPS,
  MONITOR, SCHEDULE, RemoteTrigger, and the "When NOT to orchestrate" table.
- `references/runaway-guards.md` — the 6-item runaway-guard checklist, the 3 hard CAPS,
  fan-out-width guidance, and the executable `assertSeparateJudge()` pre-exit gate.
- `examples/launch-checkpoint-resume.md` — a worked launch→watch→checkpoint→resume→kill
  walkthrough for a 3-slice fan-out Workflow.

**Source of truth:** Constitution Article XIII / XIII.b / XIII.c / XIII.d. Pick-the-primitive
lives in `Skill nexus-dispatch-catalog` + `Skill parallel-first-check`; deployable-edit
rules (for editing Nexus itself, not a target project) are out of scope for this skill.


# TIER 2 — Operator detail

Full launch / check / checkpoint / resume / stop mechanics for the orchestrator-invocable
dispatch primitives. `SKILL.md` keeps only the TIER 1 cheat-sheet; read this file when you
are actually launching, watching, checkpointing, resuming, or killing a run.

### 1. LAUNCH a Workflow — the script API

The **Workflow tool IS the JS runtime** (no separate engine). You author a script at
dispatch time and the tool drives it, returning a `runId` and a **background-task output
file** path. The script vocabulary:

- **`agent(promptString, {label, phase, agentType, schema, ...})`** — spawn one
  teammate. First arg is the prompt STRING (the full CONTRACT.md brief shape rendered
  to text — persona, goal, ≤5 `context_files`, `acceptance_criteria`,
  `verification_required`, `do_not_touch`, `notepad_topic`); second arg is the options
  object. `label` = the agent's display name in the live progress tree, `phase` =
  explicit progress-group assignment — **set BOTH on every call**, this is not
  optional decoration. `agentType` selects the persona; `schema` constrains the
  returned DATA shape; other opts (`model`, `effort`, `stall_budget_seconds`, …) tune
  the dispatch. Returns the teammate's structured result (DATA).
- **`parallel([...])`** — run several `agent()` thunks concurrently and barrier on all
  of them (each array element is a zero-arg function returning an `agent()` call, e.g.
  `parallel(items.map(x => () => agent(...)))`). Use for **fan-out-and-synthesize**; the
  array is the independent slices. A failed thunk resolves `null` — `.filter(Boolean)`
  before consuming results. Fan-out width and its two non-numeric pressures: `Skill
  nexus-dispatch-catalog`.
- **`pipeline([...])`** — run stages in order, each receiving the prior stage's output
  as DATA (deterministic hand-off, not live chat). Use for **read-after-write chains**.
- **`phase(title)`** — a STATEMENT, not a function wrapper. Calling `phase("verify")`
  sets the CURRENT progress-group; every subsequent `agent()` call groups under that
  title in the live progress display until the next `phase(...)` statement. Phase
  boundaries are the natural **checkpoint** + **commit** points, and the unit
  `resumeFromRunId` resumes from; titles must match the script's `meta.phases` exactly.
- **`log(string)`** — renders LIVE as a narrator line above the progress tree AND
  appends to the run **journal** (`journal.jsonl`); survives compaction and is the
  iteration-log / failure-boundary memory.
- **`budget`** — a **global object**, never called as a function: `budget.total` /
  `budget.spent()` / `budget.remaining()` are the harness-injected, read-only token/$
  ceilings for the run. Pair `budget.remaining() > 0` with a local `MAX_ITER` loop
  bound — these are two INDEPENDENT runaway ceilings (see Caps, §9).

**Progress visibility is authored, not free.** The side panel renders exactly the tree
the script encodes: one row per `agent()` call (its `label`), grouped by `phase`, plus
`log()` narrator lines. Empirically: **multi-agent `parallel()` legs with `label` +
`phase` set stream per-agent Tokens/Tools/Time correctly** (including custom
`agentType:` persona legs — telemetry is not degraded by a custom agent type); a
**monolithic single-`agent()` phase renders one bare static row** for its whole
runtime regardless of how much work happens inside it. Rules: (1) call `log()` between
every leg/step transition; (2) when steps are independently verifiable, decompose into
separate labeled `agent()` legs — parallel legs in a phase are the shape the panel
renders best; (3) when a long leg is genuinely indivisible (one shared context),
instruct the implementer to `echo` a step banner before each numbered brief step — the
panel's nested current-tool row then self-describes position.

**STALL WATCHDOG (pair with every launch expected to run >10 min).** The harness
notifies on run *completion*, never on a *wedge* — a stalled run sits silent unless
watched. Fix: immediately after launching, start a backgrounded polling loop (every
~45s) that exits (= notifies you) on either terminal state: the journal's result-count
reaching the expected leg count (complete), or STALL. **A leg is stalled ONLY when ALL
THREE are quiet**: (1) newest per-agent transcript mtime, (2) newest gate-output/log
file the leg writes to, AND (3) no live gate process for that leg's known command — for
longer than the leg's own `stall_budget_seconds` (or ~600s if unset). Transcript-mtime
ALONE false-alarms: a leg inside one long foreground gate command writes nothing to its
transcript while entirely healthy. Before any kill/stop action, tail the leg's own
output/log file — a moving progress marker is not a wedge.

**Mandatory verify stage.** Workflow-internal teammates **bypass the live SubagentStop
gates** (`lens-gate`, `root-cause-gate`, `no-deferral-gate`) that fire for chat-thread
`Task` returns. So **every code-writing `agent()` MUST be followed by an explicit Lens
verify** keyed to that teammate's own `files_changed` — a dedicated Lens `agent()` (or
its `verification_required` commands run and asserted). Never close a teammate on its
self-reported completion marker alone (this is the SEPARATE-JUDGE principle: the model
that did the work never decides it's done).

**Verify-phase structure — no monolithic barrier.** Full decomposition rules (bounded
parallel checks, orchestrator-level backgrounded heaviest gate, stall budgets, targeted
repair re-runs): `Skill verify-phase-patterns`. Operator takeaway here: never author a
workflow `agent()` that runs the full gauntlet serially — it is the thrash cause that
pattern exists to prevent.

**Lifecycle skeleton** (parallel archetype — `phase(title)` is a statement, not a
wrapper; every `agent()` sets `label` + `phase`):

```js
phase("scout");
const recon = await agent("recon …", { label: "scout", phase: "scout", agentType: "scout" });

phase("impl");
const outs = (await parallel([
  () => agent("slice A", { label: "impl-A", phase: "impl", agentType: "builder-persona-a" }),
  () => agent("slice B", { label: "impl-B", phase: "impl", agentType: "builder-persona-b" }),
])).filter(Boolean);

phase("verify");
const verdict = await agent(
  "adversarially verify A+B against acceptance",
  { label: "verify", phase: "verify", agentType: "lens",
    context_files: outs.flatMap(o => o.files_changed) });

phase("synth");
// no-deferral completeness check, then commit
```

### 2. The archetype catalog (which script shape)

The 6 techniques (what/when/why) are `Skill nexus-dispatch-catalog`'s CATALOG section —
this is only the script-API mapping for each, once chosen:

| Technique | Script shape |
|---|---|
| Classify-and-act | classifier `agent()` decides the KIND, then `phase()` branches |
| Fan-out-and-synthesize | `parallel([...])` → synthesize barrier (`phase("synth")`) |
| Adversarial verification | a SEPARATE Lens `agent()` attacks each producer |
| Generate-and-filter | `parallel()` many candidates → a filter `phase()` keeps the best |
| Tournament | N `agent()`s attempt the same task; judge `agent()`s compare pairwise |
| Loop-until-done | a `phase()` loop until oracle satisfied, capped |

Standard mapping: discovery/one-off = no workflow; simple = `[impl]→[lens]`;
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
  knows where honest progress ended.
- **Commit-per-phase** — each `phase()` boundary is a clean commit point (one commit =
  one checkpoint; every commit is revertable). Commit there, not mid-phase.
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
- **Anchor-file continuity** — re-inject a fixed anchor set (goal, oracle, invariants)
  each resume so the run doesn't drift; progress lives in git + disk.

### 7. KILL / STOP

- **`TaskStop`** — halts the run by its `runId`/taskId, stopping in-flight agents. Use it
  for a wedged run, a runaway loop, or a superseded plan. After stopping, you may
  `resumeFromRunId` from the last good `phase()` if you want to continue, or abandon.
- Stopping is also the **circuit-breaker** action: on a rate-based runaway
  (identical errors / empty diffs N times), `TaskStop` + escalate rather than let it spin.

### 8. ITERATE on a script

The script is a real file at **`scriptPath`** — `edit` it and re-invoke with
`resumeFromRunId`. Because the unchanged `agent()` prefix is cached, only the edited tail
re-runs. This is the **patch-mode** loop: when a run goes wrong (or a loop games the
oracle), fix the **script / loss-function**, not the agent's code, and resume from the
last honest checkpoint. The **forced-entropy stall rule** bans "same-knob-harder"
— if a phase fails the same way twice, the edit must change the APPROACH, not just retry.

### 9. CAPS (the runaway ceilings)

- **Concurrency** = `min(16, cores-2)` simultaneous agents.
- **Per-call fan-out** = up to **4096** agents in a single `parallel()` call.
- **Lifetime** = **1000** agents per workflow run.
- **Budget** = the token/$ ceiling readable via the global `budget.total` /
  `.spent()` / `.remaining()` (read-only, harness-injected — not a function call),
  paired with a local `MAX_ITER` loop bound checked in the same `for` condition — two
  independent runaway ceilings.

Fan-out-width guidance (why no numeric K cap, the two non-numeric pressures) and the
full runaway-guard checklist live in `references/runaway-guards.md` — these caps are
just the hard numbers those guards are built on.

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

The elicit→clarify→confirm→drive model, the LIGHT Goal Object vs HEAVY Loss Function
tiers, and the confirmation hard gate are all owned by **`Skill nexus-dispatch-catalog`**
(§GOAL MODEL) — load it, don't re-derive it here. This skill only adds the operator
detail: drive with the LAUNCH verbs in TIER 1/2 above (Workflow / loop-until-done /
Monitor / Cron), and re-inject the anchor set (goal, oracle, invariants) on every
`resumeFromRunId` per §6.

---

## When NOT to orchestrate

- **Discovery / quick question / single read** → answer **inline**. No team, no script.
- **A single INDIVISIBLE task** → ONE **Agent** with a full brief. A Workflow here is pure
  overhead for no gain.
- **A real write-dependency chain you can NAME** → SEQUENTIAL on the branch (or a
  `pipeline()` inside one workflow), not a parallel fan-out.
- **No verifiable oracle** → do NOT start a loop-until-goal/poll-loop. An iterate-until or
  poll primitive REQUIRES a crisp machine-checkable stop condition + a max-iteration/budget
  cap. Without the oracle, clarify first or fall back to a single Agent.
- **Pure read-only recon (≥3 angles)** → parallel Scouts in one tool block is fine; no
  `TeamCreate` needed (the one surviving raw-fan-out exception).

Multi-agent systems burn ~15× the tokens of a single chat (mostly redundant chatter);
parallelism pays **only** for genuinely independent subtasks, which is why every primitive
above is gated on a real decomposition and a verify phase.

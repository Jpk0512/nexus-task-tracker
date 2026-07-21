---
name: nexus-protocol
description: Detailed Nexus orchestrator protocol — session start, task classification, simple-task bypass, 7-item planning gate, delegation brief schema, sub-agent review, context preservation, persona routing. Use when Nexus needs operational protocol detail beyond the agent system prompt — e.g. building a sub-agent brief, classifying ambiguous task scope, or checking review criteria. Index-driven — read only the relevant section.
---

# Nexus Protocol

Operational reference for the Nexus orchestrator agent. The agent system prompt (`.claude/agents/nexus-orchestrator.md`) holds the lean rules. This skill holds the deep detail. **Read only the section you need** — index-driven.

Supporting contracts (canonical):
- `docs/agents/CONTRACT.md` — sub-agent I/O JSON schema + universal rules
- `docs/agents/TEAM.md` — persona definitions
- `docs/agents/TEST_CONTRACT.md` — the test-author persona's test mandate

## Index

| # | Section | When to read |
|---|---|---|
| 1 | [Session Start Protocol](#1-session-start-protocol) | First turn of every session |
| 2 | [Task Classification](#2-task-classification) | Before deciding whether to delegate |
| 3 | [Simple Task Bypass](#3-simple-task-bypass) | When task looks small or obvious |
| 4 | [Planning Gate Checklist](#4-planning-gate-checklist) | Before starting any new feature |
| 5 | [Delegation Protocol](#5-delegation-protocol) | When launching a sub-agent |
| 6 | [Sub-Agent Review Protocol](#6-sub-agent-review-protocol) | After any agent returns work |
| 7 | [Context Preservation](#7-context-preservation) | Before ending a session |
| 8 | [Persona Routing](#8-persona-routing) | Choosing which agent for the task |
| 9 | [Broker Dispatch Gate](#9-broker-dispatch-gate) | Before any Task dispatch |
| 10 | [Workflow-First Dispatch Decomposition](#10-workflow-first-dispatch-decomposition) | At any non-trivial dispatch — match task shape to a primitive |

---

## 1. Session Start Protocol

Run at the first turn of every session:

```bash
python3 .memory/log.py session start
python3 .memory/log.py context dump       # review open tasks + last session next_step
cat docs/drift-report.md                  # check for staleness alerts (if present)
```

Then confirm SocratiCode index is active: `codebase_status(projectPath="/abs/path")` returns 100%. If not, index it (all calls under the `mcp__plugin_socraticode_socraticode__` prefix):

- Index a project:         `codebase_index(projectPath="/abs/path")`, then poll `codebase_status(projectPath="/abs/path")` until 100%
- Incremental re-index:    `codebase_update(projectPath="/abs/path")` (changed files only)
- Full rebuild:            `codebase_remove(projectPath="/abs/path")`, then `codebase_index(projectPath="/abs/path")` (e.g. after an embedding-model change)
- Build the code graph:    `codebase_graph_build(projectPath="/abs/path")`, then poll `codebase_graph_status(projectPath="/abs/path")`
- Index context artifacts: `codebase_context_index(projectPath="/abs/path")`

The SocratiCode-first rule is **programmatically enforced** by `.claude/hooks/socraticode-gate.sh` — grep/rg/find/ack/ag are blocked by the PreToolUse hook unless a SocratiCode discovery tool fired earlier in the session. The flag is session-scoped and persists once set.

---

## 2. Task Classification

Classify before touching anything. `docs/agents/TEAM.md` multi-persona routing takes precedence within each class.

| Class | Criteria | Action |
|---|---|---|
| **Simple** | Bug fix, config change, single obvious file, no spec needed | Handle inline. No ceremony. No delegation. |
| **Standard** | ≤5 files, single domain, spec exists | One persona per routing table. Full CONTRACT.md I/O. |
| **Complex** | >5 files, multi-domain, or ambiguous scope | Scout first. Then parallel agents. |

**`docs/agents/TEAM.md` routing rules always apply within Standard/Complex.**

---

## 3. Simple Task Bypass

Bypass all ceremony when ALL of these are true:
- Bug fix, config/env var change, comment/doc update, or single obviously-scoped change
- ≤2 files, both already read this session (no stale-context risk)
- Implementation is unambiguous — no design decisions needed
- No new acceptance criteria needed

**Do NOT bypass if:** File hasn't been read recently, spans >2 files, requires any design choice, or touches a file another agent owns in this session.

---

## 4. Planning Gate Checklist

Before implementation begins on any Standard or Complex feature, all 7 items must pass —
spec exists, GWT acceptance criteria accepted, no `[NEEDS CLARIFICATION]` markers, a
Constitution check, a SocratiCode search, DB schema locked (if applicable), and failing
test stubs confirmed. Run `python3 .memory/log.py planning-gate check --feat FEAT-XXX`
for the machine-checkable items; item 5 needs a manual `codebase_search` confirmation.

Full checklist text, the forced `planning-gate submit` step (which REJECTS an incomplete
plan before any implementer is dispatched), the bootstrap sequence for authoring the
gate's own prerequisites (spec + stubs) at Simple tier, and MACRO_NODE hierarchical
planning for multi-phase features: **`references/planning-gate.md`**. A worked
MACRO_NODE example: `examples/planning-gate-and-macro-node.md`.

---

## 5. Delegation Protocol

Every sub-agent brief must include (per CONTRACT.md schema): `agent_persona`, `goal`,
`context_files` (≤5), `acceptance_criteria`, `verification_required`, `do_not_touch`,
`db_log_cmds`, `constraints`. Never brief an agent with "figure out what needs doing" —
scope is fully defined before delegation. **Fresh spawn per task, never reuse a
subagent** — every distinct task is a new `Task` call; `SendMessage` is reserved for
explicit user follow-up on a still-running task only.

Full field list, the `ultrathink` effort-bump heuristic (when to bump, when not to, the
full-session override), and multi-stage handoff conventions:
**`references/delegation-and-review.md`**.

---

## 6. Sub-Agent Review Protocol

When an agent returns work, route on the **completion marker** (H2 heading at top of agent output):

| Marker | Action |
|---|---|
| `## NEXUS:DONE` | Verify `verification_result` is verbatim passing → run `db_log_cmds` → mark task done. |
| `## NEXUS:BLOCKED` | Read `blockers`. If a different persona can unblock, re-route. Otherwise escalate to user. |
| `## NEXUS:NEEDS-DECISION` | `AskUserQuestion` with the options in `decisions_needed`. Log via `decision add` and re-spawn. |
| `## NEXUS:CHECKPOINT` | Write checkpoint summary to `.memory/` → pause and resume next session. |
| `## NEXUS:REVISE` | Revision loop: re-spawn implementer with the failing issues YAML. Cap 3 iterations, stall-detect. |
| `## NEXUS:DEFER-REQUEST` | Default action is FIX, not FILE — approve deferral, instruct inline fix, or escalate. |

Full revision-loop pseudocode, the reflection step (Scout 5-bullet reflection between
planning gate and delegation), and the Scout/Lens report file-dump pattern:
**`references/delegation-and-review.md`**. A worked revision loop that succeeds on
iteration 2, contrasted with one that stalls: `examples/revision-loop-walkthrough.md`.

---

## 7. Context Preservation

Before ending any session:

```bash
python3 .memory/log.py session end \
  --summary "What was completed this session" \
  --next_step "What to do first next session"
git add <files> && git commit -m "..."   # commit all changes
```

The Stop hook currently writes a context snapshot and runs `sync_docs.py`, but it does **not** auto-close the session. The `session end` call above is the canonical close — without it, the session remains open and `docs/drift-report.md` (if present) has no comparison baseline.

Sub-agents must write their outputs to files. Never assume a future session can recall conversation context.

---

## 8. Persona Routing

Which persona owns which work type, pairing rules, cascade model assignments, and
forbidden directories: **`Skill team-routing`** — this section deliberately does not
restate that table (Single-Home). Quick pointer only: Tableau/integration work always
pairs the wiring persona with the async-worker persona; UI work always goes to the
frontend implementer, never inline.

---

## 9. Broker Dispatch Gate

Every `Task` dispatch is mechanically gated by the `.claude/hooks/broker-gate.py`
PreToolUse hook — the ritual is **validate → ping → dispatch**
(`nexus_validate_brief_tool` → `nexus_notepad_ping` → `Task`). Fail-CLOSED: a
missing/malformed `broker_state.json` blocks the Task (exit 2) unless
`NEXUS_BROKER_ALLOW_DEGRADED=1` is set.

Full block-string reference (exact deny messages + the fix for each) and the
disambiguation from a Redis message broker: **`references/broker-dispatch-gate.md`**.

---

## Mandatory Discipline

### Parallelism-by-default dispatch
- **Threshold:** any task with **≥2 independent subtasks** (no output from each other) →
  dynamic **Workflow** — the REQUIRED DEFAULT, not just preferred. A lone serial
  single-Agent dispatch is reserved ONLY for a truly indivisible atomic task. Full
  primitive-selection detail: `Skill nexus-dispatch-catalog`.
- **Fan-out width:** fan out as wide as the work genuinely warrants — no fixed K cap.
  Prefer diverse personas over identical clones.

### Root cause before re-dispatch
- When a sub-agent returns `NEXUS:REVISE` OR the user reports a regression, the
  orchestrator MUST dispatch a root-cause Scout investigation BEFORE re-spawning the
  implementer. The investigation must identify the TRUE UNDERLYING CAUSE (not a symptom).
  Why-chain depth is at the fixer's discretion — no mechanical minimum.

### Lesson harvesting cadence
- Every `NEXUS:REVISE` event → `python3 .memory/log.py lesson add` immediately.
- Every user-reported regression → log lesson + log decision capturing the pattern fix.
- Session start → run `lesson list --validated 0` and surface top-5 unvalidated lessons
  matching the upcoming work persona/domain.
- Session end → 5-bullet retrospective in the session-end summary.

---

## Agent Notepad

Every dispatched agent reads the notepad first and writes to it last (CONTRACT.md Rule 17).

### Assigning notepad_topic in briefs

Every Task brief MUST include `notepad_topic: <scope>` in the visible brief text. Topic
conventions (pick the most specific that fits): **TASK-NNN** (preferred when applicable) ·
**PR-N** or **branch-name** (PR/branch-scoped work) · **FEAT-NNN** (feature-scoped across
many tasks) · **freeform-kebab-case** (last resort).

Document the chosen topic in the brief explicitly:
> Notepad topic: `TASK-029`. First action: `notepad list`. Last action before NEXUS:DONE:
> `notepad add`.

### Reading notepad output before dispatch

Before delegating to an implementer on any Standard or Complex task, read the notepad for
the topic yourself:

```bash
python3 .memory/log.py notepad list --topic <topic>
```

Surface any `next-agent-action` or `gotcha` entries in the implementer's brief under
`constraints`. This prevents agents from re-discovering what the previous agent already
learned.

---

## 10. Workflow-First Dispatch Decomposition

Source of truth: `docs/CONSTITUTION.md` (parallel-first / workflow-first articles).
Dispatch is **WORKFLOW-FIRST** — match the TASK SHAPE to an orchestrator-invocable
primitive, do not just count subtasks. Load **`Skill nexus-dispatch-catalog`** at any
non-trivial dispatch (the cheat-sheet + the 6 techniques + the goal model); load **`Skill
nexus-orchestration`** once you have chosen a primitive (how to RUN it). Load **`Skill
parallel-first-check`** before every single Agent/Task dispatch (the pre-dispatch
threshold ladder). This section deliberately does not restate any of those three skills'
content (Single-Home) — it is only the cue that dispatch decisions belong there, not here.

---

## References

- `references/planning-gate.md` — full 7-item checklist text, forced submission, the
  bootstrap sequence, and MACRO_NODE hierarchical planning.
- `references/delegation-and-review.md` — full delegation-protocol field list, the
  `ultrathink` effort-bump heuristic, the revision-loop pseudocode, the reflection step,
  and the Scout/Lens report file-dump pattern.
- `references/broker-dispatch-gate.md` — the full broker-gate ritual, exact block
  strings, fail-CLOSED behavior, and the Redis-broker disambiguation.
- `examples/planning-gate-and-macro-node.md` — a worked MACRO_NODE feature split across
  3 phases with inter-phase handoffs.
- `examples/revision-loop-walkthrough.md` — a worked revision loop succeeding on
  iteration 2, contrasted with one that stalls and escalates.

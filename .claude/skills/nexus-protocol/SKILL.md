---
name: nexus-protocol
description: Detailed Nexus orchestrator protocol — session start, task classification, simple-task bypass, 7-item planning gate, delegation brief schema, sub-agent review, context preservation, persona routing. Use when Nexus needs operational protocol detail beyond the agent system prompt — e.g. building a sub-agent brief, classifying ambiguous task scope, or checking review criteria. Index-driven — read only the relevant section.
---

# Nexus Protocol

Operational reference for the Nexus orchestrator agent. The agent system prompt (`.claude/agents/nexus-orchestrator.md`) holds the lean rules. This skill holds the deep detail. **Read only the section you need** — index-driven.

Supporting contracts (canonical):
- `docs/agents/CONTRACT.md` — sub-agent I/O JSON schema + universal rules
- `docs/agents/TEAM.md` — persona definitions
- `docs/agents/TEST_CONTRACT.md` — Quill's test mandate

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
| 8 | [Persona Quick Reference](#8-persona-quick-reference) | Choosing which agent for the task |
| 9 | [Broker Dispatch Gate](#9-broker-dispatch-gate) | Before any Task dispatch |
| 10 | [Workflow-First Dispatch Decomposition](#10-workflow-first-dispatch-decomposition) | At any non-trivial dispatch — match task shape to a primitive; ≥2 independent subtasks → a Workflow; goal-shaped work → the goal model |

---

## 1. Session Start Protocol

Run at the first turn of every session:

```bash
python3 .memory/log.py session start
python3 .memory/log.py context dump       # review open tasks + last session next_step
cat docs/drift-report.md                  # check for staleness alerts
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

Classify before touching anything. TEAM.md multi-persona routing takes precedence within each class.

| Class | Criteria | Action |
|---|---|---|
| **Simple** | Bug fix, config change, single obvious file, no spec needed | Handle inline. No ceremony. No delegation. |
| **Standard** | ≤5 files, single domain, spec exists | One persona per TEAM.md routing. Full CONTRACT.md I/O. |
| **Complex** | >5 files, multi-domain, or ambiguous scope | Scout first. Then parallel agents. Up to 4 concurrent. |

**TEAM.md routing rules always apply within Standard/Complex.** Example: Tableau API work always pairs Hermes with Pipeline. UI work always goes to Forge, not inline.

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

Before implementation begins on any Standard or Complex feature, all 7 items must pass:

```
[ ] 1. Spec file exists at docs/features/FEAT-XXX.md
[ ] 2. GWT acceptance criteria written and accepted by user
[ ] 3. No [NEEDS CLARIFICATION] markers remain in spec
[ ] 4. Constitution check: all 9 articles verified against spec
[ ] 5. SocratiCode semantic search run for all affected areas
[ ] 6. DB schema locked in spec (required if feature touches DuckDB)
[ ] 7. Test stubs written by Quill and confirmed failing
```

**Run the machine validator** (catches items 1–4 and 6–7 automatically):
```bash
python3 .memory/log.py planning-gate check --feat FEAT-XXX
```

Item 5 (SocratiCode search) requires manual confirmation — run a `codebase_search` before checking it off.

### Forced submission (rejects on incomplete plans)

For Standard and Complex features, the seven-item check above is paired with a structured `submit` step. Submitting a plan that's missing any required field is rejected at the CLI layer — no implementer is dispatched.

```bash
python3 .memory/log.py planning-gate submit --feat FEAT-XXX --json '{
  "feat": "FEAT-XXX",
  "scope_summary": "...",
  "files_touched_estimate": <int>,
  "acceptance_criteria": ["Given X, when Y, then Z", "..."],
  "constitution_articles_verified": ["I", "III", "V"],
  "risks": ["..."],
  "rollback_plan": "rtk git revert <sha>  |  feature-flag off  |  ..."
}'
```

Return: `{"gate": "ACCEPTED", ...}` (logged as a `context_log` row with `action_type=planning-gate-submit`) OR `{"gate": "REJECTED", "missing_fields": [...], "type_errors": [...]}` (no DB write — fix and resubmit).

Simple class skips submit. Standard/Complex MUST submit before the first implementer dispatch.

### Bootstrap — authoring the gate's own prerequisites

Planning-gate items 1 and 7 (the spec file and failing test stubs) are the gate's own prerequisites — but their authors are code-writing personas: Atlas writes the spec, Quill writes the stubs. This is a chicken-and-egg: a Standard/Complex dispatch to a code-writing persona requires an ACCEPTED planning-gate row first, yet the spec and stubs that satisfy the gate do not exist yet.

The escape: `broker-gate.py` computes `is_feature_code = task_tier in {"standard", "complex"} and _is_code_writing(persona, intent)`. A **SIMPLE-tier** dispatch is never feature-code regardless of persona, so it skips the planning-gate requirement entirely.

Bootstrap sequence (3 steps):

1. Author the spec (`docs/features/FEAT-XXX.md`) and failing test stubs at **SIMPLE tier** — the planning-gate does not apply, so Atlas and Quill can be dispatched without a prior ACCEPTED row.
2. Run `planning-gate submit` → verify the return is `{"gate": "ACCEPTED", ...}`.
3. Dispatch implementers at Standard/Complex tier — the accepted planning-gate row now exists and the gate passes.

### MACRO_NODE — hierarchical planning for multi-phase features

When a feature naturally splits into phases (e.g., FEAT-005 had Polars dtype mapping → schema design → migration → ingestion → search exposure; FEAT-006 had Phase A → B → C → C.1 → D), use the **MACRO_NODE pattern**:

1. **Macro plan** (one `planning-gate submit` call against the whole feature)
   ```json
   {
     "feat": "FEAT-XXX",
     "scope_summary": "...",
     "macro_phases": [
       {"id": "A", "title": "...", "owner": "Atlas", "exits_when": "schema doc approved"},
       {"id": "B", "title": "...", "owner": "Pipeline", "exits_when": "migration green"},
       {"id": "C", "title": "...", "owner": "Pipeline", "exits_when": "ingestion lands"}
     ],
     ...
   }
   ```
2. **Per-phase brief** is a fresh `Task` call (per OD-1) using only the artifacts that phase needs. The brief's `context_files` includes the prior phase's handoff doc.
3. **Inter-phase handoff** is a 10–20 line doc at `.memory/handoffs/FEAT-XXX/phase-<id>.md`:
   - What landed
   - What was rejected and why
   - What the next phase depends on (file paths + symbol names)
   - Open questions the next phase must resolve
4. **Nexus owns the macro state** — never delegate phase sequencing to a sub-agent. The orchestrator decides when phase N is "done enough" to start N+1.

**Example (retroactive — FEAT-006):** Phase A defined the search ranking spec; Phase B implemented hybrid search; Phase C added metadata sync; Phase C.1 (mid-flight branch) rewired `search_text`; Phase D introduced the `SearchRow` discriminated union. Each phase was its own delegation cycle with a `.memory/handoffs/FEAT-006/phase-<id>.md` (or DECISIONS.md entry) bridging the next brief.

**Anti-pattern:** A single brief like "implement FEAT-006 end-to-end." That's a MACRO not handed to MACRO_NODE — it almost always blows up at the third surprise.

---

## 5. Delegation Protocol

Every sub-agent brief must include (per CONTRACT.md schema):

- `agent_persona` — exact canonical/split slug from TEAM.md (`scout`, `forge-ui`, `forge-ui-pro`, `forge-wire`, `forge-wire-pro`, `pipeline-data`, `pipeline-data-pro`, `pipeline-async`, `pipeline-async-pro`, `hermes`, `atlas`, `palette`, `lens-fast`, `lens`, `quill-ts`, `quill-py`). The base names `forge`/`pipeline`/`quill` are RETIRED — never dispatch them.
- `goal` — one sentence
- `context_files` — minimum set of files to read (≤5; no "read everything")
- `acceptance_criteria` — GWT format, copied from spec
- `verification_required` — which checks must pass (`rtk tsc`, `rtk lint`, `uv run ruff check`, etc.)
- `do_not_touch` — files agent must not modify
- `db_log_cmds` — commands to run on completion (if any)
- `constraints` — must NOT do X, must use Y not Z

**Never** brief an agent with "figure out what needs doing." The scope is fully defined before delegation.

**Fresh spawn per task — never reuse a subagent.** Every distinct task = a new `Task` tool invocation with full brief. NEVER use `SendMessage` to a prior subagent instance to route a new task — that reuses the old context window and breaks isolation guarantees. Two Quill tasks = two `Task` calls = two fresh contexts. `SendMessage` is reserved exclusively for explicit user follow-up to a still-running agent on the same task (e.g., "answer the user's clarifying question"); it is never an orchestrator routing primitive.

For multi-stage work, write a 10-20 line **handoff** to `.memory/` between stages — what was decided, what was rejected, what remains. The next persona's brief (still a fresh `Task` call) includes the handoff as a `context_file`.

### Per-task effort bumping (`ultrathink` keyword)

Default reasoning level is set by each persona's `effort:` frontmatter (Scout=high, F/P/H/A/L/Q=high, Nexus=xhigh — see `.claude/agents/*.md`). For genuinely hard one-off spawns, bump the effort by including the literal word `ultrathink` somewhere in the Task prompt body. Claude Code recognizes it and raises that single spawn's thinking budget to the model's max.

**Bump (include `ultrathink`) when:**
- Task is Complex class AND Scout reflection flagged non-trivial risks
- An architectural decision is embedded (schema choice, library swap, API contract design)
- Re-spawn after a prior failed iteration on the same task — encode the failure pattern + bump
- Cross-cutting refactor where one wrong call cascades across many files

**Do NOT bump for:**
- Standard CRUD, single-file edits, doc updates, isolated bug fixes with a clear repro
- Test authoring (Quill has a tight, well-specified contract)
- Verification (Lens — deterministic-first checks are bounded; semantic checks shouldn't need a bump unless the output is genuinely ambiguous)
- Routine Nexus routing turns (classification, status checks, log commands)

Mechanically, just drop the word in the brief. Example: `"goal: 'ultrathink — propose the DuckDB indexing strategy for this query pattern. Three candidates with tradeoffs.'"`.

**Full-session override:** set `CLAUDE_CODE_EFFORT_LEVEL=xhigh` in the environment when starting Claude Code. This wins over frontmatter and `ultrathink` keyword — use it sparingly (debugging a stuck session, validating a difficult feature end-to-end).

The user is the ultimate authority on bumps. If they say "use ultrathink for this," include it regardless of the heuristic above.

---

## 6. Sub-Agent Review Protocol

When an agent returns work, route on the **completion marker** (H2 heading at top of agent output):

| Marker | Action |
|---|---|
| `## NEXUS:DONE` | Verify `verification_result` is verbatim passing → run `db_log_cmds` → mark task done. |
| `## NEXUS:BLOCKED` | Read `blockers`. If a different persona can unblock, re-route. Otherwise escalate to user. |
| `## NEXUS:NEEDS-DECISION` | Use `AskUserQuestion` with the options the agent surfaced in `decisions_needed`. On user response, log via `decision add` and re-spawn with the chosen path. |
| `## NEXUS:CHECKPOINT` | Write checkpoint summary to `.memory/` (via context snapshot) → pause and resume next session. |
| `## NEXUS:REVISE` (from Lens) | **Revision loop**: re-spawn implementer with the failing issues YAML as `context_files`. Cap at 3 iterations. Stall detection: if `current_issue_count >= previous_issue_count`, escalate ("revision loop stalled at iteration N — issue count not decreasing"). |
| `## NEXUS:DEFER-REQUEST` | Agent found an out-of-scope error and requests deferral. Default action is **FIX**, not FILE (CONTRACT Rule 12). Options: (a) approve deferral — `python3 .memory/log.py task create ...` to log the tracked task, then continue; (b) instruct an inline fix; (c) escalate to user. Never leave a surfaced error with no resolution path (DEC-005 no-deferral). |

Always:
1. Check `verification_result`: verbatim passing output, not just "I ran it"
2. Check `acceptance_met`: every entry must be `true` with evidence
3. Run all `db_log_cmds` (task updates, decision logs)
4. **Do not mark task done** until verification passes AND acceptance is met

Two failures on the same task by the same agent → escalate to user before retrying.

**Re-delegation = fresh `Task` call.** When re-routing after `## NEXUS:REVISE`, `## NEXUS:BLOCKED`, or `## NEXUS:NEEDS-DECISION`, always spawn a NEW `Task` invocation with an updated brief — never `SendMessage` to the prior subagent. Each re-spawn pays the cost of a fresh context window deliberately; that cost is the point.

### Revision loop (detail)

```
iteration = 0
prev_count = ∞
while iteration < 3:
  spawn implementer with brief.context_files += [lens_revision_report.md]
  output = implementer_response
  if output.completion_marker == "## NEXUS:DONE":
      → spawn Lens to re-validate
      if Lens returns DONE: break (success)
      if Lens returns REVISE again:
          current_count = len(lens.issues)
          if current_count >= prev_count:
              escalate("revision loop stalled at iteration {iteration}")
              break
          prev_count = current_count
          iteration += 1
          continue
  else:
      → handle marker per table above
      break
if iteration == 3:
    escalate("revision loop hit cap at 3 iterations")
```

### Reflection step (new — between planning gate and delegation)

For Standard and Complex tasks ONLY (Simple bypass skips reflection):

1. After planning gate passes, BEFORE delegating to the implementer (Forge/Pipeline/Hermes/Atlas), spawn `scout` with this brief:
   ```
   goal: "Read the brief + spec + relevant code. Write a 5-bullet reflection: (1) hidden assumptions in the spec, (2) likely failure modes for this approach, (3) files that should be read before coding starts, (4) what the test stubs (if any) miss, (5) one alternative approach worth considering. ≤200 words. No code changes."
   context_files: [<spec_path>, <relevant_files_from_classification>]
   acceptance_criteria: ["5-bullet reflection produced", "≤200 words", "no edits made"]
   verification_required: ["read-only — no commands"]
   ```
2. Log the returned reflection as a `context_log` row with `--action-type research`.
3. If the reflection identifies a blocker (e.g., "the proposed approach conflicts with DEC-XYZ"), escalate to the user BEFORE proceeding with implementation.
4. Otherwise, include the reflection file path (e.g., `.memory/reflections/<task_id>.md`) as a `context_files` entry in the implementer's brief.

Cost: one Scout call (~5-10K tokens on Haiku). Pays back by catching ~13% of premature "done" patterns observed in the audit (DEC-020→021→022 chain).

### Scout report file-dump (output isolation)

To keep your context window clean, Scout dumps full findings to a file and returns only a summary. Pattern:

- **Brief instruction:** include `session_id` (from `python3 .memory/log.py session current --id-only`) and a kebab-case `task_slug` (≤40 chars) in every Scout brief.
- **Scout writes:** `.memory/scout-reports/<session-id>/<task-slug>.md` containing the complete findings JSON + narrative.
- **Scout returns:** `report_path`, ≤200-word `summary`, `top_3_files` (path + one-line each), `recommended_persona_next`, completion marker. Full findings stay in the file.
- **Nexus reads:** the summary first. Only `Read` the dump file (with `offset`/`limit` if large) when the summary is insufficient to make a routing call.
- **Path is gitignored** (`.memory/scout-reports/` in `.gitignore`). Reports are session-scoped, not durable artifacts.

Apply the same pattern to **Lens** when its `revision_report` exceeds ~500 words — dump full report to `.memory/lens-reports/<session-id>/<task-slug>.md`, return summary + issue count + top-3 critical findings.

---

## 7. Context Preservation

Before ending any session:

```bash
python3 .memory/log.py session end \
  --summary "What was completed this session" \
  --next_step "What to do first next session"
rtk git add <files> && rtk git commit -m "..."   # commit all changes
```

The Stop hook currently writes a context snapshot and runs `sync_docs.py`, but it does **not** auto-close the session. The `session end` call above is the canonical close — without it, the session remains open and `docs/drift-report.md` has no comparison baseline.

Sub-agents must write their outputs to files. Never assume a future session can recall conversation context.

---

## 8. Persona Quick Reference

See `docs/agents/TEAM.md` for full definitions. Quick routing only:

| Work type | Lead | Pair if needed |
|---|---|---|
| `next` UI / components / RSC pages | `forge-ui` | **MUST pair with `palette`** (neither ships without the other); escalate to `forge-ui-pro` |
| `app/api` / server actions / `vercel-ai-sdk-v4` wiring / read-side `postgres` | `forge-wire` | Pair with `forge-ui`; escalate to `forge-wire-pro` |
| Python transforms / writers / embeddings / `postgres` writes | `pipeline-data` | Pair with `pipeline-async`; escalate to `pipeline-data-pro` |
| Dramatiq workers / Redis / integration-target clients / async | `pipeline-async` | Pair with `pipeline-data`; escalate to `pipeline-async-pro` |
| Integration-target / AI-provider / MCP wiring | `hermes` | Works with `pipeline-async`, `forge-wire` |
| `postgres` schema / `none` models | `atlas` | Design-only, no Bash |
| Unknown territory / investigation | `scout` | read-only, no edits; ≥3 in parallel for recon |
| Deterministic gates (lint/tsc/test) | `lens-fast` | reports only; dispatched ∥ `lens` |
| Deep / semantic / RCA / visual review | `lens` | reports only; dispatched ∥ `lens-fast` |
| TS test authoring | `quill-ts` | Coordinates with Lens |
| Python test authoring | `quill-py` | Coordinates with Lens |

The base names `forge`/`pipeline`/`quill` are RETIRED — `persona-alias-resolver.sh` DENIES them (exit 2) or redirects only on scope hints. NEVER dispatch a base name.

Cascade routing (per agent frontmatter `model:`):
- `scout`, `lens-fast` → Haiku (read-only investigation / deterministic gates, cheap; high-volume)
- `forge-ui`, `forge-wire`, `pipeline-data`, `pipeline-async`, `hermes`, `quill-ts`, `quill-py`, `palette` → Sonnet (implementation precision)
- `atlas`, `lens`, Nexus (this agent) → Opus (architectural / judgment / orchestration reasoning)
- `*-pro` variants (`forge-ui-pro`, `forge-wire-pro`, `pipeline-data-pro`, `pipeline-async-pro`) → Opus + xhigh effort (used for COMPLEX or REWORK loops)

---

## 9. Broker Dispatch Gate

Every `Task` dispatch is mechanically gated by the `.claude/hooks/broker-gate.py` PreToolUse hook. It blocks dispatch unless `nexus_validate_brief_tool` ran THIS turn. The ritual is **validate → ping → dispatch**:

1. Call `mcp__nexus-broker__nexus_validate_brief_tool` with the brief you are about to dispatch. It writes `.memory/files/broker_state.json` with `approved` + `called_at`.
2. After running `notepad list`, call `mcp__nexus-broker__nexus_notepad_ping`.
3. Dispatch the `Task`. The gate checks `approved=true` AND `called_at` is < **120 s** old (`TURN_STALE_SECONDS`). If you stall >120 s between validate and dispatch, re-call validate.

**Block strings you will see (and the fix for each):**
- `broker rejected dispatch to '<persona>' — Task dispatch not allowed. Call nexus_validate_brief with a valid brief first.` → the brief failed validation; fix the brief, re-validate.
- `broker_state.json has no called_at timestamp — nexus_validate_brief was not called this turn.` → you never validated; validate now.
- `broker_state.json is stale (<N>s old, max 120s) — call nexus_validate_brief again for this turn.` → re-validate, then dispatch promptly.

**Fail-CLOSED (P2-10):** if `.memory/files/broker_state.json` is missing/malformed/unreadable (e.g. the nexus-broker MCP is not running), the gate **blocks** the Task (exit 2) — a down broker must be loud, not silently bypassed. Set `NEXUS_BROKER_ALLOW_DEGRADED=1` to allow Tasks while degraded (a LOUD `additionalContext` warning fires every turn until the broker is restored); unset it and restart the broker to re-arm.

**Disambiguation:** this is the **nexus-broker validation MCP** (`python -m broker.server`), NOT a Redis message broker. The two broker tools (`nexus_validate_brief_tool`, `nexus_notepad_ping`) are the only MCP tools Nexus calls itself.

---

## Mandatory Discipline (2026-05-13)

Three reinforcements canonical for Nexus orchestrator behavior — full text in
`docs/CONSTITUTION.md` and `docs/agents/CONTRACT.md`.

### Parallelism-by-default dispatch
- **Threshold (DEC-029):** any task with **≥2 independent subtasks** (no output from
  each other) → dynamic **Workflow** — the REQUIRED DEFAULT, not just preferred. A lone
  serial single-Agent dispatch is reserved ONLY for a truly indivisible atomic task.
  Sequential single-agent dispatch is permitted ONLY when the orchestrator names in
  writing the dependency that requires serialization.
- **Fan-out width:** fan out as wide as the work genuinely warrants — no fixed K cap.
  Prefer diverse personas over identical clones (Constitution Article XIII.b advisory).
- Raw multi-`Task` fan-out in one tool block is the **deprecated legacy shape**
  (superseded by the Workflow primitive); it survives ONLY as the **≥3 read-only
  Scout recon** exception (investigation phase → ≥3 parallel Scouts probing different
  angles). See §10 for the full primitive-by-shape ladder + goal model.

### Root cause before re-dispatch (DEC-028)
- When a sub-agent returns `NEXUS:REVISE` OR the user reports a regression, the
  orchestrator MUST dispatch a root-cause Scout investigation BEFORE re-spawning
  the implementer. The investigation must identify the TRUE UNDERLYING CAUSE (not
  a symptom). Why-chain depth is at the fixer's discretion — no mechanical minimum.
  No "try again with the same brief." On recurring or high-severity fixes, the
  orchestrator or Lens MAY demand a deeper pass before close.

### Lesson harvesting cadence
- Every `NEXUS:REVISE` event → `python3 .memory/log.py lesson add` immediately.
- Every user-reported regression → log lesson + log decision (DEC) capturing the
  pattern fix.
- Session start → run `lesson list --validated 0` and surface top-5 unvalidated
  lessons matching the upcoming work persona/domain.
- Session end → 5-bullet retrospective in the session-end summary.

---

## Agent Notepad

Every dispatched agent reads the notepad first and writes to it last (CONTRACT.md Rule 16).

### Assigning notepad_topic in briefs

Every Task brief MUST include `notepad_topic: <scope>` in the visible brief text. Topic conventions (pick the most specific that fits):

1. **TASK-NNN** — when a logged task drives the work (preferred when applicable).
2. **PR-N** or **branch-name** — when work is PR/branch-scoped (e.g., `PR-9`, `feat/agent-notepad`).
3. **FEAT-NNN** — when work is feature-scoped across many tasks.
4. **freeform-kebab-case** — when nothing else fits (e.g., `audit-2026-05-13-followups`). Keep it short and stable across the phased sequence.

Document the chosen topic in the brief explicitly:

> Notepad topic: `TASK-029`. First action: `notepad list`. Last action before NEXUS:DONE: `notepad add`.

### Reading notepad output before dispatch

Before delegating to an implementer on any Standard or Complex task, read the notepad for the topic yourself:

```bash
python3 .memory/log.py notepad list --topic <topic>
```

Surface any `next-agent-action` or `gotcha` entries in the implementer's brief under `constraints`. This prevents agents from re-discovering what the previous agent already learned.

---

## 10. Workflow-First Dispatch Decomposition

Source of truth: **Constitution Article XIII / XIII.b / XIII.d** (+ DEC-020/022/023/024/025). This section is the operational cue at dispatch time. Dispatch is **WORKFLOW-FIRST** — match the TASK SHAPE to an orchestrator-invocable primitive, do not just count subtasks. Load **`Skill nexus-dispatch-catalog`** at any non-trivial dispatch (the cheat-sheet + the 6 techniques + the goal model); load **`Skill nexus-orchestration`** once you have chosen a primitive (how to RUN it). DEC-021 no-rediscovery: do not reverse-engineer your own toolset.

**Primitive-by-shape.** PARALLEL / independent / fan-out / audit / migration / debate → the **Workflow** tool. ITERATE until a verifiable goal (tests pass / gate green / no new findings) → a **loop-until-done Workflow**. POLL external state you don't control (CI, deploy, PR, logs, queue) → **Monitor**. OUTLIVE-the-session / recurring → **CronCreate** (session, ≤7-day) or `RemoteTrigger`/Routines (durable). Indivisible → one **Agent**. Discovery / quick-Q → inline. (The orchestrator runs on a DENYLIST — Workflow/Monitor/Cron/Agent/Task are NOT denied → available, and are in `permissions.allow` so they run prompt-free.)

**Threshold ladder.** (a) single INDIVISIBLE task → ONE `Agent`/`Task` (a single-teammate Workflow is *preferred* — never forced — for the built-in Lens stage + monitorability). (b) **≥2 INDEPENDENT subtasks** (need no output from each other) → **a dynamic Workflow** (the DEFAULT — not just for multi-phase work); dispatch one agent per independent unit, as wide as the work warrants — prefer diverse personas over identical clones (shared bias + coordination overhead are the real diminishing returns, not an API limit); `≥3` read-only Scout recon is the standing exception, and is the ONLY surviving use of raw multi-`Task` fan-out (the deprecated legacy shape, superseded by the Workflow primitive). (c) **multi-phase / fan-out-then-verify / scale beyond one context** → a dynamic **Workflow** — move the plan into code when the work is **long-running, massively parallel, highly structured, and/or adversarial**.

**Goal model (HARD GATE, DEC-023/025).** For goal-shaped work the orchestrator OWNS the goal: **ELICIT** the intent (a sharp clarifying question if vague) → **CLARIFY** into a VERIFIABLE oracle + scope + stop condition (LIGHT = a Goal Object `{success_criteria, acceptance_checks, non_goals, open_questions}`, the default; HEAVY = a LOSS FUNCTION via `Skill nexus-loss-function`) → **CONFIRM** with the user ONCE before driving (a separate Lens critic reviews in autonomous ticks) → **DRIVE** with orchestrator-invocable primitives only. NEVER recommend a user slash-command — `/goal`/`/loop`/`/effort` are USER-only; EMULATE them (loop-until-done Workflow / Monitor / Cron). RUNAWAY GUARDS on any iterate/poll loop: max-iteration cap, no-progress detection (halt on identical errors / empty diffs / recurring fails), token/$ budget, circuit-breaker, and SEPARATE-JUDGE (the model that stopped working never decides it's done = Lens).

**Decompose algorithm.** (1) List atomic units (one per callsite / failing test / module / source / candidate); indivisible → ONE Agent. (2) Test independence — any unit needing another's output is sequential or a pipeline, never naive parallel. (3) Pipeline (DEFAULT, no barrier) vs parallel barrier (only when stage N needs ALL of stage N-1). (4) Write each unit's brief explicitly. (5) Dispatch one agent per independent unit, as wide as the work warrants — prefer diverse personas over identical clones (shared bias + coordination overhead are the real diminishing returns, not an API limit; hard limits are the harness's ~16-concurrent/1000-per-run/4096-per-call). (6) Add a SEPARATE verify/critic phase. (7) Synthesize at the barrier + completeness critic (no-deferral). (8) Loop-until-dry for unknown-size work, with a mandatory max-iteration cap.

**The 6 techniques** (choose by shape, not count):
- **Classify-and-act** — a classifier decides the task TYPE, then routes to the matching agent/behavior. Trigger: branching-on-type.
- **Fan-out-and-synthesize** — split into many independent steps, run an agent on each in parallel, then a synthesize barrier merges the structured outputs. Trigger: truly independent subtasks exceeding one context window.
- **Adversarial verification** — a SEPARATE agent attacks each producer's output against a rubric from a diverse viewpoint; never self-review. (The local Lens mandate.) Gate on risk, not count.
- **Generate-and-filter** — generate many candidates, dedupe, and keep only the best after rubric/verification filtering. Trigger: breadth THEN quality.
- **Tournament** — N agents each attempt the SAME task differently; judges compare pairwise through a bracket until one winner remains. Trigger: one hard problem worth N attempts.
- **Loop-until-done** — for unknown-size work, loop spawning agents until a stop condition ("no new findings" / "no more errors"), with a mandatory max-iteration cap.

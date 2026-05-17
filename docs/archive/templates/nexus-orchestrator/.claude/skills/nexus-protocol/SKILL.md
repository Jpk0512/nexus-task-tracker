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

---

## 1. Session Start Protocol

Run at the first turn of every session:

```bash
python3 .memory/log.py session start
python3 .memory/log.py context dump       # review open tasks + last session next_step
cat docs/drift-report.md                  # check for staleness alerts
```

Then confirm SocratiCode index is active (`codebase_status` returns green). If not, run `codebase_index`.

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

- `agent_persona` — exact name from TEAM.md (Scout, Forge, Pipeline, Lens, Quill, Atlas, Hermes)
- `goal` — one sentence
- `context_files` — minimum set of files to read (≤5; no "read everything")
- `acceptance_criteria` — GWT format, copied from spec
- `verification_required` — which checks must pass (`rtk tsc`, `rtk lint`, `uv run ruff check`, etc.)
- `do_not_touch` — files agent must not modify
- `db_log_cmds` — commands to run on completion (if any)
- `worktree_branch` — `feat/<slug>` or null if main-thread work
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
| Next.js / TypeScript / UI | Forge | — |
| Python ingestion / DuckDB writes | Pipeline | — |
| Tableau REST or Azure AI wiring | Hermes | Pipeline or Forge |
| DuckDB schema / Malloy models | Atlas | Pipeline |
| Unknown territory / investigation | Scout | — (read-only, no edits) |
| Validation / acceptance check | Lens | — (no code, only reports) |
| Test writing | Quill | Coordinates with Lens |
| Multi-domain or cross-cutting | Scout first | Then assign by domain |

Cascade routing (Phase 2+, when persona agent files land in `.claude/agents/`):
- Scout → Haiku (read-only investigation, cheap)
- Forge, Pipeline, Hermes, Atlas, Lens, Quill → Sonnet
- Nexus (this agent) → Opus

---

## Mandatory Discipline (2026-05-13)

Three reinforcements canonical for Nexus orchestrator behavior — full text in
`docs/CONSTITUTION.md` and `docs/agents/CONTRACT.md`.

### Parallel-first dispatch
- Independent subtasks → all dispatched in a single message (one tool block,
  multiple `Task` invocations).
- Investigation phase → ≥3 parallel Scouts probing different angles.
- Sequential single-agent dispatches are FORBIDDEN unless the orchestrator names
  the dependency that requires serialization in writing.

### Root cause before re-dispatch
- When a sub-agent returns `NEXUS:REVISE` OR the user reports a regression, the
  orchestrator MUST dispatch a Five-Whys Scout investigation BEFORE re-spawning
  the implementer. No "try again with the same brief."

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

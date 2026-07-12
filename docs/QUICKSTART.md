# Nexus Quickstart — First Run Guide

> Covers: post-install verification, the orient→classify→brief→delegate→verify loop,
> and a worked first-task example. Read `docs/ARCHITECTURE.md` first for system context.

---

## 1. Post-install checklist

Run these steps immediately after `install.sh` completes, before doing any feature work.

### 1a. Version verify

```bash
cat .memory/.nexus-version          # single line, e.g. 1.14.2
cat .nexus-ledger.json              # {version, installed_at, updated_at, source}
```

Both files are written by `install.sh` at install time and by `tools/safe_update.py`
at update time. If either is missing, re-run the installer.

### 1b. Health check

```bash
python3 .memory/log.py health
```

Reports a per-tier `PASS / WARN / FAIL` table. A golden fresh install shows **0 FAIL**.
Runtime checks (broker reachability, LM Studio router) degrade to INFO/WARN on a fresh
tree — that is expected. A FAIL on the broker tier is the upstream cause of the dispatch
block at §3 below.

Optional flags:
- `--no-runtime` — skip runtime-tier checks (static-only, <1 second)
- `--drift` — compare installed files against the canonical package
- `--json` — machine-readable output (the format the session-start banner uses)
- `--md` — markdown table output
- `--table` — human-readable ASCII table
- `--no-color` — disable color in `--table` output

You can also invoke it via `Skill nexus-health` inside Claude Code.

### 1c. Memory DB check

```bash
python3 .memory/log.py context dump
```

Confirms `project.db` is initialized and the schema is current. The output lists open
tasks, the last session summary, and the `next_step` from the previous session. On a
brand-new install, these will be empty — that is correct.

### 1d. Broker boot probe

```bash
uv run --directory nexus-broker python -c \
  'import broker.server, broker.vault.stdio; print(broker.server.mcp.name)'
```

Expected output: `nexus-broker` (the MCP server name). If this fails, see the remediation
printed at the bottom of `install.sh` (the `BROKERWARN` block). A failing broker means
every `Task` dispatch is blocked by `broker-gate.py` (it is FAIL-CLOSED).

Remediation if broker does not boot:

```bash
uv sync --directory nexus-broker
uv run --directory nexus-broker python -c \
  'import broker.server, broker.vault.stdio'
```

---

## 2. Opening Claude Code

```bash
cd <project-root> && claude
```

Nexus auto-loads as the main session via `agent: nexus-orchestrator` in
`.claude/settings.json`. On session start:

1. The `health-banner.sh` hook fires and surfaces any FAIL/WARN to the model context.
2. The `router-health-check.sh` hook checks LM Studio reachability.
3. Nexus runs `python3 .memory/log.py session start` and checks `progress.md` /
   `session_state.md` for prior session state.
4. Nexus proposes the next action based on open in-progress tasks and `next_step`.

If Nexus is not loading automatically, confirm `settings.json` contains
`"agent": "nexus-orchestrator"` at the top level and that `.claude/agents/nexus-orchestrator.md`
exists.

---

## 3. The orchestration loop

Every task follows this five-step loop. Nexus runs it; personas execute inside it.

```
ORIENT → CLASSIFY → BRIEF → DELEGATE → VERIFY
```

### ORIENT

Read open state before every turn:

```bash
python3 .memory/log.py notepad list --topic <topic>   # FIRST action every turn
python3 .memory/log.py context dump                    # open tasks + last next_step
```

### CLASSIFY

State the tier out loud before any tool call:

| Tier | Conditions | Nexus action |
|---|---|---|
| **Trivial** | ≤1 file, ≤5 LOC, no logic change, no design decision | Handle inline; log `context snapshot --action-type trivial-fix` |
| **Simple** | Bug/config/doc, ≤2 files already read, no design decision | Delegate with full brief; Lens gate required |
| **Standard** | Default for feature / multi-file work | Scout reflection first; then delegate; Lens gate required |
| **Complex** | New features, cross-service, schema migrations, multi-persona | All 7 planning-gate items + Scout + Lens |

When in doubt, promote one tier up.

### BRIEF

Author a CONTRACT.md-compliant brief before dispatching. Required fields:

- `goal` — non-empty statement of what to accomplish
- `context_files` — non-empty list of files/docs the persona needs
- `acceptance_criteria` — non-empty list of verifiable outcomes
- `skills_required` — mandatory for every code-writing persona (see `docs/agents/SKILL_MAP.md`)
- `notepad_topic` — the scope key (e.g. `TASK-029`, `FEAT-003`, or a freeform kebab label)

### DELEGATE (the broker ritual)

Every Task dispatch is hard-gated by `broker-gate.py`. The gate is FAIL-CLOSED.
Run this exact sequence within 120 seconds before each Task:

1. Call `nexus_validate_brief_tool` — validates the brief and writes `broker_state.json`.
2. Run `python3 .memory/log.py notepad list --topic <scope>`, then call `nexus_notepad_ping`.
3. Issue the `Task` with the correct split persona name.

Valid persona names: `forge-ui`, `forge-wire`, `pipeline-data`, `pipeline-async`,
`atlas`, `hermes`, `palette`, `quill-ts`, `quill-py`, `scout`, `lens`, `lens-fast`
(and `-pro` escalation variants). The retired base names `forge`, `pipeline`, `quill`
are DENIED by `persona-alias-resolver.sh`.

### VERIFY

After every code-touching Simple+ task, dispatch `lens-fast` and `lens` in parallel
(one tool block). Nexus marks `## NEXUS:DONE` only when both hold:

1. Verbatim `verification_result` is present and passing (`rtk tsc` + `rtk lint` for TS;
   `uv run ruff check` for Python; Quill's failing→passing confirmation if tests were authored).
2. Every `acceptance_criteria` item is `acceptance_met: true`.
3. A `validation_log` row from `agent_validated='lens'` exists in `project.db`.

If Lens returns `## NEXUS:REVISE`: re-spawn the implementer with the failing-issues YAML
as `context_files` in a **fresh Task**. Cap at 3 iterations. If issue count does not
decrease across iterations, escalate to the user.

---

## 4. Worked first-task example

**Goal:** add a `last_synced_at` column to an existing DuckDB table.

### Step 1 — ORIENT

```
notepad list --topic FEAT-004    # check prior notes
context dump                     # confirm no in-progress conflicts
```

### Step 2 — CLASSIFY

This touches DuckDB schema (atlas territory) and a Python writer (pipeline-data territory)
across two files. No new feature UI. **Standard** tier.

### Step 3 — REFLECT (Scout)

Dispatch Scout to read the schema file and the affected writer. Brief:

```
goal: read ingestion/src/writers/sync_writer.py and models/schema.sql
      and identify what migration is needed to add last_synced_at
context_files: [ingestion/src/writers/sync_writer.py, models/schema.sql]
acceptance_criteria: [5-bullet reflection on failure modes and migration approach]
```

### Step 4 — BRIEF (parallel: atlas + pipeline-data)

With Scout's reflection as context, author two briefs:

- **atlas** brief: add `last_synced_at TIMESTAMP` to `schema.sql` DDL + Malloy model.
- **pipeline-data** brief: update the writer to populate `last_synced_at` on upsert.

Both briefs list `skills_required`, include the Scout reflection as `context_files`,
and share `notepad_topic: FEAT-004`.

### Step 5 — DELEGATE

Run the broker ritual (validate → ping → Task) for each persona. Issue both Task calls
in one tool block (parallel dispatch — `parallel_group_id: "feat-004-schema-writer"`).

### Step 6 — VERIFY

Dispatch `lens-fast` and `lens` in one tool block after both personas return `NEXUS:DONE`.
Lens checks `rtk tsc`, `rtk lint`, `uv run ruff check`, and the migration correctness.

### Step 7 — CHECKPOINT

```bash
python3 .memory/log.py session end \
  --summary "Added last_synced_at to schema + writer" \
  --next_step "Run rtk git and push session branch for review"
```

The session branch now has two focused commits (one per task). The release is a human
handoff: the orchestrator stops at the push-to-remote step and the user approves.

---

## 5. Common first-run problems

| Symptom | Cause | Fix |
|---|---|---|
| Every Task dispatch denied with "broker rejected" | `broker_state.json` missing or stale | Run `nexus_validate_brief_tool` then `nexus_notepad_ping` within 120s before the Task |
| Broker probe fails (`import broker.server` errors) | `.venv` not built | `uv sync --directory nexus-broker` |
| `health` shows FAIL on schema tier | `project.db` not initialized | `python3 .memory/log.py init` |
| `socraticode-gate` blocks grep/Read | SocratiCode index not built | `codebase_index(projectPath="<abs-path>")`, poll `codebase_status` to 100% |
| `lens-gate` blocks DONE | Lens row missing for the task | Dispatch `lens` and `lens-fast` before claiming DONE; Lens logs its own row |
| `skills-required-guard` blocks dispatch | Brief missing `skills_required` | Add a non-empty `skills_required` list per `docs/agents/SKILL_MAP.md` |
| `NEXUS:DEFER-REQUEST` returned by persona | Persona proposes skipping an item | Accept only if authorized; item must be resolved inline or tracked via `TaskCreate` before the task completes |

---

Companion references: `docs/ARCHITECTURE.md` · `docs/NEXUS-OPERATING-MANUAL.md` · `docs/CONSTITUTION.md`

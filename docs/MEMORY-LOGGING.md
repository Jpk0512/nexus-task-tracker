# Memory Logging CLI Reference

> Source of truth: `.memory/log.py` argparse definitions.
> Run from the project root. DB path defaults to `.memory/project.db`; override with `NEXUS_DB_PATH`.

```
python3 .memory/log.py <command> [<subcommand>] [options]
```

---

## init

Initialize `project.db` from `schema.sql`. Creates all tables and runs idempotent migrations (bi-temporal columns, stall columns, feedback version column, validation log columns). Safe to re-run on an existing database.

```bash
python3 .memory/log.py init
```

When to use: first install of Nexus; also run after pulling a schema update.

---

## session

Manage Claude Code session bookends.

### session start

Open a new session row.

```bash
python3 .memory/log.py session start [--branch BRANCH]
```

| Option | Default | Description |
|---|---|---|
| `--branch` | `main` | Working branch name for this session |

Outputs the new session id (e.g. `S20260626-143000`) to stdout.

When to use: SessionStart hook.

### session end

Close the current open session.

```bash
python3 .memory/log.py session end --summary TEXT --next_step TEXT
```

| Option | Required | Description |
|---|---|---|
| `--summary` | yes | One-line summary of work completed |
| `--next_step` | yes | Carry-forward intent for the next session |

When to use: SubagentStop hook (session close path).

### session reset

End the current session and immediately open a new one. Used for context handoffs mid-task.

```bash
python3 .memory/log.py session reset --summary TEXT [--handoff-notepad-topic TOPIC]
```

| Option | Required | Description |
|---|---|---|
| `--summary` | yes | One-line summary of completed work |
| `--handoff-notepad-topic` | no | Notepad topic to write a handoff entry into |

### session status

Print the current open session id and start time.

```bash
python3 .memory/log.py session status
```

### session reap

Close stale open sessions (sessions with `ended_at IS NULL` older than a threshold).

```bash
python3 .memory/log.py session reap [--max-age-hours N]
```

| Option | Default | Description |
|---|---|---|
| `--max-age-hours` | 2 | Close sessions older than this many hours |

---

## task

Work-item management. IDs are auto-allocated as TASK-NNN or supplied explicitly.

### task add

```bash
python3 .memory/log.py task add --title TEXT [options]
```

| Option | Required | Description |
|---|---|---|
| `--title` | yes | Task title |
| `--id` | no | Explicit id (e.g. TASK-042); auto-allocated if omitted |
| `--feature-id` | no | Associated FEAT-NNN |
| `--description` | no | Longer description |
| `--status` | no | `todo \| in_progress \| done \| blocked \| cancelled` (default: `todo`) |
| `--priority` | no | `critical \| high \| medium \| low` (default: `medium`) |
| `--assigned-to` | no | Agent persona or `user` |
| `--acceptance-criteria` | no | JSON array of strings |
| `--notes` | no | Free-text notes |

### task update

```bash
python3 .memory/log.py task update --id TASK-NNN [options]
```

Updatable fields: `--title`, `--status`, `--priority`, `--assigned-to`, `--notes`, `--worktree`.

### task list

```bash
python3 .memory/log.py task list [--status STATUS] [--feature-id FEAT-NNN]
```

### task stall

Atomically increment `stall_count` and record the persona responsible. Called by the no-deferral gate when REVISE or BLOCKED markers repeat.

```bash
python3 .memory/log.py task stall --task-id TASK-NNN --persona PERSONA --marker REVISE|BLOCKED
```

### task mirror-native

Mirror one native `TaskCreate`/`TaskUpdate` event into `project.db` (used by the task-db-mirror hook).

```bash
python3 .memory/log.py task mirror-native --native-id N [--op create|update] [--subject TEXT] [--description TEXT] [--status STATUS] [--owner AGENT]
```

### task backfill-native

Bulk-mirror a native task snapshot (JSON array or JSONL) into `project.db`.

```bash
python3 .memory/log.py task backfill-native [--from PATH|-]
```

### task repair-orphans

Find and fix tasks with a doubled `NATIVE-` prefix (e.g. `NATIVE-NATIVE-17`).

```bash
python3 .memory/log.py task repair-orphans
```

---

## decision

Architecture decision records (DEC-NNN). Bi-temporal: edits version the old row; no deletes.

### decision add

```bash
python3 .memory/log.py decision add --title TEXT --context TEXT --decision TEXT --rationale TEXT [options]
```

| Option | Required | Description |
|---|---|---|
| `--title` | yes | Short title |
| `--context` | yes | Background / problem being solved |
| `--decision` | yes | What was decided |
| `--rationale` | yes | Why this option over alternatives |
| `--id` | no | Explicit id; auto-allocated if omitted |
| `--status` | no | `proposed \| accepted \| superseded \| deprecated` (default: `accepted`) |
| `--alternatives` | no | Other options considered + why rejected |
| `--consequences` | no | Implications and affected parties |

When to use: whenever a significant architectural or process choice is made during a session.

### decision list

```bash
python3 .memory/log.py decision list [--history]
```

`--history` walks the full bi-temporal chain (all versions including superseded rows).

### decision retire

Tombstone a decision (hides from default recall; history kept).

```bash
python3 .memory/log.py decision retire DEC-NNN
```

---

## lesson

Self-correction insights. Unvalidated by default; only validated lessons are injected at SessionStart.

### lesson add

```bash
python3 .memory/log.py lesson add --trigger TRIGGER --title TEXT --body TEXT [options]
```

| Option | Required | Description |
|---|---|---|
| `--trigger` | yes | `lens_fail \| redelegation \| session_drift \| manual \| reflection` |
| `--title` | yes | Short title |
| `--body` | yes | 1-paragraph, ≤80 words |
| `--id` | no | Explicit id; auto-allocated if omitted |
| `--applies-to` | no | `'all'` or comma-separated persona names (default: `all`) |
| `--source-decision-id` | no | DEC-NNN that triggered this lesson |
| `--validated` | no | Flag: skip unvalidated state (use only for high-confidence manual lessons) |

### lesson validate

Promote a lesson to `validated=1` so it becomes eligible for SessionStart injection.

```bash
python3 .memory/log.py lesson validate --id LSN-NNN [--as-decision DEC-NNN]
```

### lesson list

```bash
python3 .memory/log.py lesson list [--validated | --unvalidated] [--applies-to PERSONA] [--history]
```

---

## fact

Semantic facts — long-lived project knowledge. Uses `key` as the logical identifier.

### fact add

```bash
python3 .memory/log.py fact add --key KEY --value VALUE [--source-decision-id DEC-NNN] [--pinned]
```

`--pinned` facts never decay during `memory retain`.

### fact list

```bash
python3 .memory/log.py fact list [--pinned-only] [--key-like PATTERN] [--history]
```

### fact decay

Soft-delete a fact by key (sets `decayed_at`; does not delete the row).

```bash
python3 .memory/log.py fact decay --key KEY
```

---

## procedure

Reusable orchestrator workflows, trust-scored by outcome.

### procedure add

```bash
python3 .memory/log.py procedure add --name NAME --steps-json JSON [--trigger-pattern PATTERN]
```

`--steps-json` is a JSON array of step strings, e.g. `'["step 1", "step 2"]'`.

### procedure record-outcome

```bash
python3 .memory/log.py procedure record-outcome --name NAME --outcome success|fail
```

Increments `success_count` or `fail_count`.

### procedure list

```bash
python3 .memory/log.py procedure list
```

---

## feature

Feature spec tracking (FEAT-NNN). Linked to tasks via `tasks_json`.

### feature add

```bash
python3 .memory/log.py feature add --id FEAT-NNN --title TEXT [options]
```

| Option | Required | Description |
|---|---|---|
| `--id` | yes | `FEAT-NNN` |
| `--title` | yes | Feature title |
| `--status` | no | `planned \| in_progress \| done \| cancelled` (default: `planned`) |
| `--spec-path` | no | Path to `docs/features/FEAT-NNN-*.md` |
| `--description` | no | Summary description |
| `--tasks-json` | no | JSON array of TASK ids |

### feature update

```bash
python3 .memory/log.py feature update --id FEAT-NNN [--title TEXT] [--status STATUS] [--spec-path PATH] [--description TEXT] [--tasks-json JSON]
```

### feature list

```bash
python3 .memory/log.py feature list [--status STATUS]
```

---

## context

Append action-trail entries to the context log.

### context snapshot

```bash
python3 .memory/log.py context snapshot [--action-type TYPE] [--files-modified JSON] [--decision-refs JSON] [--task-updates JSON] [--summary TEXT]
```

`--action-type`: `planning | code_change | verification | research | fix | reflection`

`--files-modified` and `--decision-refs` and `--task-updates` are JSON arrays.

### context dump

Print all context_log rows for the current session.

```bash
python3 .memory/log.py context dump
```

---

## seed

One-time bootstrap: seed tasks from `docs/TASKS.md` before autosync. The DB is the source of truth after this runs.

```bash
python3 .memory/log.py seed
```

---

## memory

Retention worker for aging out low-value context and decaying unpinned semantic facts.

### memory retain

```bash
python3 .memory/log.py memory retain [--ctx-ttl-days N] [--fact-ttl-days N] [--apply]
```

| Option | Default | Description |
|---|---|---|
| `--ctx-ttl-days` | 14 | Drop `context_log` rows older than this (below quality threshold) |
| `--fact-ttl-days` | 180 | Soft-delete unpinned `semantic_facts` older than this |
| `--apply` | — | Actually commit deletions; default is dry-run |

---

## planning-gate

Spec-first planning gate. Checks whether a feature has a submitted plan before implementation is permitted.

### planning-gate check

```bash
python3 .memory/log.py planning-gate check --feat FEAT-NNN
```

Exit 0 if a valid plan exists; non-zero otherwise.

### planning-gate submit

```bash
python3 .memory/log.py planning-gate submit --feat FEAT-NNN --json JSON_STRING|-
```

Validates the plan JSON structure and records it. Pass `-` to read JSON from stdin.

---

## validation

Lens validation log. Written by `lens-gate.sh` after each Lens review dispatch.

### validation add

Record a Lens validation row.

```bash
python3 .memory/log.py validation add \
  --agent lens \
  --target PERSONA \
  --task-hash HASH \
  --verdict PASS|PARTIAL|FAIL \
  [--summary TEXT] \
  [--report-path PATH|-] \
  [--report-json JSON] \
  [--strict] \
  [--files-changed-json JSON] \
  [--dispatch-started-at ISO_TS]
```

When `--report-path` or `--report-json` is supplied, the stored verdict is DERIVED from the report's `criteria_results[]` and deterministic exit codes — any FAIL in the report prevents a PASS verdict. Use `--strict` to reject (exit 1) instead of silently downgrading.

### validation completeness-check

Assert that the latest in-window Lens PASS row covers a declared set of files.

```bash
python3 .memory/log.py validation completeness-check \
  --files-changed-json JSON \
  [--task-hash HASH]
```

Exit 0 = PASS row covers the files; exit 2 = not covered.

---

## subagent-return

Record and summarize a sub-agent response (used by the subagent-return hook, Mitigation A).

### subagent-return record

```bash
python3 .memory/log.py subagent-return record \
  --agent PERSONA \
  [--full-response-file PATH]
```

Reads from stdin if `--full-response-file` is omitted. Persists the full response to `.memory/files/` and writes a summary to the agent notepad.

---

## notepad

Rolling 5-entry shared context for phased tasks. Oldest entry beyond 5 per topic is auto-trimmed on insert.

### notepad add

```bash
python3 .memory/log.py notepad add \
  --topic TOPIC \
  --agent PERSONA \
  --note TEXT \
  [--kind fyi|nuance|reminder|gotcha|next-agent-action]
```

| Option | Required | Description |
|---|---|---|
| `--topic` | yes | Scope key: TASK-NNN, FEAT-NNN, branch name, or freeform kebab |
| `--agent` | yes | Persona name (scout, forge, pipeline, hermes, atlas, lens, quill, palette, nexus) |
| `--note` | yes | Insight for the next agent (≤500 chars, CHECK-enforced) |
| `--kind` | no | Note kind (default: `fyi`) |

When to use: last action before a persona returns. Write insight, not status. "Completed" is forbidden; "X pattern breaks under Y condition" is correct.

### notepad list

```bash
python3 .memory/log.py notepad list --topic TOPIC
```

Prints the last 5 entries for the topic in chronological order.

### notepad clear

```bash
python3 .memory/log.py notepad clear --topic TOPIC
```

---

## registry

Plexus fleet registry. Tracks installed projects.

### registry add

```bash
python3 .memory/log.py registry add \
  --project-path /abs/path/to/project \
  --version X.Y.Z \
  --action installed|installed-existing|manual \
  [--notes TEXT]
```

### registry update

```bash
python3 .memory/log.py registry update \
  --project-path /abs/path/to/project \
  --version X.Y.Z \
  [--action updated|rolled-back] \
  [--notes TEXT]
```

### registry list

```bash
python3 .memory/log.py registry list [--project-path PATH]
```

### registry remove

```bash
python3 .memory/log.py registry remove --project-path PATH [--notes TEXT]
```

### registry health

Fleet health: run static (and optionally runtime) checks on all registered projects.

```bash
python3 .memory/log.py registry health [--full] [--drift] [--json] [--leak-check]
```

| Option | Description |
|---|---|
| `--full` | Include runtime checks per project (slower) |
| `--drift` | Include drift checks vs canonical package |
| `--json` | Emit machine-readable JSON |
| `--leak-check` | Enable per-project leak scan (slow: O(files × projects)) |

---

## feedback

Per-project friction log (DEC-019). Agents call this when Nexus itself blocks, confuses, or stalls them. Plexus aggregates across the fleet with `harvest`.

### feedback add

```bash
python3 .memory/log.py feedback add \
  --source tool|hook \
  --severity critical|high|medium|low|info \
  --category CATEGORY \
  --message TEXT \
  [--context-json JSON] \
  [--source-file PATH] \
  [--nexus-version VERSION]
```

Categories: `gate_deny | gate_needs_decision | gate_revise_stall | unclear_persona | unclear_skill | missing_context | roster_mismatch | workflow_friction | other`

`--nexus-version` defaults to the value read from `.memory/.nexus-version` (falls back to `'unknown'`).

When to use: whenever Nexus itself causes friction — a gate DENY, a confusing NEEDS-DECISION, a REVISE stall, a wrong-fit persona or skill, a roster mismatch, or missing context. No permission needed; Plexus harvests it to improve Nexus.

### feedback harvest

Plexus-only. Aggregate per-project `nexus_feedback` into `nexus_improvements` (deduplicates by `category + sha256(message)` across projects).

```bash
python3 .memory/log.py feedback harvest [--md] [--dry-run]
```

`--dry-run` counts unresolved feedback across the fleet without writing `nexus_improvements` (used by the SessionStart harvest-banner).

### feedback resolve

Plexus-only. Stamp `resolved_at` on per-project `nexus_feedback` rows so harvest stops re-firing them.

```bash
python3 .memory/log.py feedback resolve \
  [--backlog-id N] \
  [--project-path PATH --category CAT --hash SHA256] \
  [--reviewed-by AGENT] \
  [--up-to-version VERSION] \
  [--include-unknown]
```

Use `--backlog-id` (improvement_backlog row id) OR the `--project-path + --category + --hash` triple. `--up-to-version` resolves only rows whose `nexus_version` semver ≤ VERSION; `--include-unknown` also resolves rows stamped `'unknown'`.

---

## rca

Root cause analysis log. Records why-chains from fix/bug/regression tasks; also embeds into `vec_memory` (kind=`rca`).

### rca add

```bash
python3 .memory/log.py rca add \
  --agent PERSONA \
  --symptom TEXT \
  --why-chain-json JSON \
  --pattern-fix TEXT \
  [--task-summary TEXT]
```

`--why-chain-json` is a JSON array of strings (the why-chain). Example:
```bash
python3 .memory/log.py rca add \
  --agent hermes \
  --symptom "Auth header missing from VDS requests" \
  --why-chain-json '["Token not forwarded","Client instantiated before session","..."]' \
  --pattern-fix "Move token into request interceptor"
```

---

## reflection

Reflection snapshots. Lightweight audit trail for spec/decision/constitution edits; also embedded into `vec_memory` (kind=`reflection`).

### reflection add

```bash
python3 .memory/log.py reflection add \
  --action-type spec_update|decision_amend|constitution_amend|other \
  --summary TEXT \
  [--file-path PATH]
```

`--summary` max 200 chars.

---

## recall

Semantic search over `vec_memory`. Requires `sqlite-vec` extension and LM Studio running locally.

```bash
python3 .memory/log.py recall \
  --semantic "natural language query" \
  [--kind decision|lesson|rca|reflection] \
  [--top-k N] \
  [--since Nd] \
  [--fallback keyword]
```

| Option | Default | Description |
|---|---|---|
| `--semantic` | required | Query text to embed and search |
| `--kind` | all kinds | Filter to one kind |
| `--top-k` | 5 | Maximum results |
| `--since` | no limit | Only results within the last N days (e.g. `30d`) |
| `--fallback keyword` | — | Opt-in degraded fallback: relational keyword search when embed is unavailable. Without this flag, embed failure exits 3. |

---

## vec

`vec_memory` maintenance.

### vec backfill

Drain `embed_outbox` and `vec_memory_deadletter` into `vec_memory`. Re-embeds any rows that failed or were missed.

```bash
python3 .memory/log.py vec backfill [--full]
```

`--full` also runs an O(N) source sweep (backstop against rows absent from outbox and dead-letter). Default drains outbox and dead-letter only.

---

## embed-backfill

Alias for `vec backfill`. Equivalent in every way.

```bash
python3 .memory/log.py embed-backfill [--full]
```

---

## improvements

Nexus improvement backlog. Tracks distilled research notes for human review. Plexus-side only.

### improvements populate

Scan distilled research sources; upsert `unread` rows for notes at or above a relevance threshold. Idempotent; never downgrades a row already in `evaluated/flagged/dismissed`.

```bash
python3 .memory/log.py improvements populate [--threshold N]
```

Default threshold: 4 or `$NEXUS_IMPROVEMENTS_THRESHOLD`.

### improvements list

```bash
python3 .memory/log.py improvements list [--state unread|evaluated|flagged|dismissed|all]
```

Default state: `unread`.

### improvements flag

Promote a note to `review_state='flagged'`.

```bash
python3 .memory/log.py improvements flag NOTE [--note "why this matters"]
```

`NOTE` is the note basename or repo-relative path.

### improvements evaluate

Mark a note `review_state='evaluated'` (human reviewed, no further action).

```bash
python3 .memory/log.py improvements evaluate NOTE [--note "evaluation text"]
```

### improvements dismiss

Mark a note `review_state='dismissed'`.

```bash
python3 .memory/log.py improvements dismiss NOTE [--note "reason"]
```

### improvements dashboard

Regenerate `research/00-meta/NEXUS-IMPROVEMENTS.md` (derived summary; read-only to the DB).

```bash
python3 .memory/log.py improvements dashboard
```

---

## health

Single-project health self-test. Distinct from `registry health` (fleet-wide). The SessionStart banner calls `health --no-runtime --json`.

```bash
python3 .memory/log.py health [--no-runtime] [--drift] [--json] [--md] [--table] [--no-color]
```

| Option | Description |
|---|---|
| `--no-runtime` | Skip RUNTIME-tier checks (broker/hooks/DB/embeddings); run STATIC only |
| `--drift` | Include DRIFT-tier checks comparing this install to the canonical package |
| `--json` | Emit machine-readable HealthReport JSON (SessionStart banner default) |
| `--md` | Render a markdown table (suitable for PR comments) |
| `--table` | Render a human-readable ASCII/rich table |
| `--no-color` | Disable color in `--table` output |

---

## Environment Variables

| Variable | Effect |
|---|---|
| `NEXUS_DB_PATH` | Override the default DB path (`.memory/project.db`). Used by tests and multi-project tooling. |
| `NEXUS_DISABLE_VEC` | Force-degrade the sqlite-vec path: no re-exec, no extension load. Deterministic test seam. |
| `NEXUS_EMBED_TIMEOUT` | Timeout in seconds for LM Studio embed calls (default: 30). |
| `NEXUS_IMPROVEMENTS_THRESHOLD` | Relevance score gate for `improvements populate` (default: 4). |

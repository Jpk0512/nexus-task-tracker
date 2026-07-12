-- Nexus Installer — project memory schema
-- Source of truth for project.db. Re-run to add new tables (uses IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,               -- S20260510-143000
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    summary             TEXT,
    last_step           TEXT,
    next_step           TEXT,
    branch              TEXT DEFAULT 'main',
    context_json        TEXT,                           -- JSON snapshot of key state at close
    user_message_count  INTEGER DEFAULT 0,             -- incremented by context-reset-monitor hook
    last_reset_at       TIMESTAMP                      -- when session reset was last triggered
);

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,       -- TASK-001
    feature_id          TEXT,                   -- FEAT-001
    title               TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'todo',
    -- todo | in_progress | done | blocked | cancelled
    priority            TEXT NOT NULL DEFAULT 'medium',
    -- critical | high | medium | low
    assigned_to         TEXT,                   -- agent persona or 'user'
    worktree            TEXT,
    acceptance_criteria TEXT,                   -- JSON array of strings
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    notes               TEXT,
    subtasks_json       TEXT,                   -- JSON array of {id, title, status}
    estimated_minutes   INTEGER                 -- rough estimate for progress reporting
);

CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,               -- DEC-001
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'accepted',
    -- proposed | accepted | superseded | deprecated
    context     TEXT NOT NULL,
    decision    TEXT NOT NULL,
    rationale   TEXT,
    alternatives TEXT,
    consequences TEXT,
    decided_at  TEXT NOT NULL,
    session_id  TEXT REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS feature_specs (
    id          TEXT PRIMARY KEY,               -- FEAT-001
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'planned',
    -- planned | in_progress | done | cancelled
    spec_path   TEXT,
    description TEXT,
    tasks_json  TEXT,                           -- JSON array of task IDs
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    logged_at   TEXT NOT NULL,
    action_type TEXT,
    -- planning | code_change | verification | research | fix | reflection
    files_modified TEXT,                        -- JSON array
    decision_refs  TEXT,                        -- JSON array of DEC-* IDs
    task_updates   TEXT,                        -- JSON array of {id, status}
    summary     TEXT
);

-- Lessons table (Phase 3 — Technique 9)
-- Captures self-correction insights from revision loops, re-delegations,
-- or manual logging. Unvalidated by default — orchestrator must explicitly
-- promote (validated=1) before surfacing in SessionStart context dumps.
CREATE TABLE IF NOT EXISTS lessons (
    id                  TEXT PRIMARY KEY,               -- LSN-001
    trigger             TEXT NOT NULL,
    -- lens_fail | redelegation | session_drift | manual | reflection
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,                  -- 1-paragraph, <=80 words
    applies_to          TEXT NOT NULL DEFAULT 'all',
    -- 'all' or comma-separated persona names
    source_session_id   TEXT REFERENCES sessions(id),
    source_decision_id  TEXT REFERENCES decisions(id),
    validated           INTEGER NOT NULL DEFAULT 0,     -- 0/1; promoted via `lesson validate`
    recorded_at         TEXT NOT NULL,
    validated_at        TEXT
);

-- Semantic facts (Phase 3 — Technique 3 three-tier memory, semantic tier)
-- Long-lived project knowledge that future sessions need to recall (e.g.
-- "TABLEAU_SITE_ID is a LUID, not a content URL"). Pinned facts never decay.
CREATE TABLE IF NOT EXISTS semantic_facts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    key                 TEXT NOT NULL,                  -- logical key, uniqueness via partial index
    value               TEXT NOT NULL,
    source_session_id   TEXT REFERENCES sessions(id),
    source_decision_id  TEXT REFERENCES decisions(id),
    created_at          TEXT NOT NULL,
    decayed_at          TEXT,                           -- soft-delete timestamp
    pinned              INTEGER NOT NULL DEFAULT 0      -- 1 = never expires
);

-- Procedures (Phase 3 — Technique 3 three-tier memory, procedural tier)
-- Reusable workflows the orchestrator can replay (e.g. "ship-feature-phase").
-- success_count / fail_count drive trust scoring.
CREATE TABLE IF NOT EXISTS procedures (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    trigger_pattern     TEXT,
    steps_json          TEXT NOT NULL,
    success_count       INTEGER NOT NULL DEFAULT 0,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    last_used_at        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Agent notepad (rolling 5-entry shared context for phased tasks)
-- Agents write concise FYI/nuance/reminder/gotcha notes for the next agent on the
-- same topic. On each insert the oldest entry beyond 5 is auto-trimmed (enforced
-- by the CLI, not a trigger, so SQLite stays simple).
CREATE TABLE IF NOT EXISTS agent_notepad (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL,
    -- scope key: a task id (TASK-029), feature (FEAT-007), branch (feat/foo), or freeform
    agent_name  TEXT NOT NULL,
    -- which persona wrote it (scout|forge|pipeline|hermes|atlas|lens|quill|palette|nexus)
    session_id  TEXT,
    written_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    note        TEXT NOT NULL CHECK (length(note) <= 500),
    note_kind   TEXT DEFAULT 'fyi'
    -- fyi | nuance | reminder | gotcha | next-agent-action
);

CREATE INDEX IF NOT EXISTS idx_notepad_topic ON agent_notepad(topic, written_at DESC);

-- Agent root cause log (PR #10 — discipline-hooks)
-- Records root-cause analyses from fix/bug/regression tasks so patterns can be
-- surfaced and reviewed across sessions.
CREATE TABLE IF NOT EXISTS agent_root_cause_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    agent_name      TEXT,
    task_summary    TEXT,
    symptom         TEXT,
    why_chain_json  TEXT,           -- JSON array of strings, len >= 5
    pattern_fix     TEXT,
    logged_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Reflection snapshot (PR #10 — discipline-hooks)
-- Captures a lightweight audit trail whenever a doc-critical file is amended
-- (spec, decision, or constitution edit).
CREATE TABLE IF NOT EXISTS reflection_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    file_path       TEXT NOT NULL,
    action_type     TEXT,           -- spec_update | decision_amend | constitution_amend | other
    one_line_summary TEXT,          -- <= 200 chars
    captured_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Validation log (lens-gate hook)
-- Lens records a row here after validating an implementer's work.
-- lens-gate.sh queries this table to confirm Lens has reviewed NEXUS:DONE
-- output before allowing the sub-agent to complete.
--
-- Three nullable columns are NOT declared inline here because this table is
-- often already present (IF NOT EXISTS is a no-op on existing DBs). They are
-- added via idempotent ALTER in _migrate_validation_log_columns (log.py cmd_init):
--   files_changed_json  TEXT  -- JSON array of implementer's declared files_changed;
--                             -- lets the completeness-check assert set-superset coverage.
--   revise_reason       TEXT  -- machine-readable reason when derived verdict != PASS;
--                             -- auto-filled from derive_verdict_from_report binding_note.
--   dispatch_started_at TEXT  -- ISO-8601 UTC stamp of when the validating dispatch began
--                             -- (distinct from validated_at = row-write time); lets
--                             -- instrumentation compute lens wall-clock duration.
CREATE TABLE IF NOT EXISTS validation_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT,
    agent_validated     TEXT NOT NULL,          -- typically "lens"
    target_agent        TEXT NOT NULL,          -- the agent whose work was validated
    task_or_brief_hash  TEXT NOT NULL,          -- hash of the brief or task ID
    verdict             TEXT NOT NULL,          -- PASS | PARTIAL | FAIL
    evidence_summary    TEXT,
    validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_validation_target
    ON validation_log(target_agent, validated_at DESC);

-- Phase F1 migration: stall_count + last_persona on tasks
-- Applied by cmd_init migration guard (ALTER TABLE is idempotent via PRAGMA check).
-- stall_count: consecutive REVISE/BLOCKED markers for the same persona on this task.
-- last_persona: most recent subagent persona that produced a REVISE/BLOCKED marker.

-- Semantic memory virtual table — Phase D Layer 2 (M-001)
-- Requires sqlite-vec extension loaded BEFORE this script runs.
-- Each row is one embedded document from decisions / lessons / RCAs / reflections.
-- DIMENSION is single-sourced in log.py as _EMBED_DIM (=1024). Keep float[1024]
-- here in lockstep; log.py asserts the live table matches and halts on drift.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    kind TEXT PARTITION KEY,  -- 'decision' | 'lesson' | 'rca' | 'reflection'
    ref_id TEXT,              -- DEC-NNN, LSN-NNN, agent_root_cause_log.id, reflection_snapshot.id
    text_blob TEXT,           -- embedded source text; stored for result display
    created_at TEXT,          -- ISO-8601 UTC; mirrors source table row timestamp
    embedding float[1024]     -- text-embedding-mxbai-embed-large-v1 via LM Studio; 1024-dim L2-normalized
);
-- vec0 virtual tables manage their own HNSW index; CREATE INDEX is not supported on them

-- Embed dead-letter queue — P1-03
-- When the embed backend is down, the relational write still lands but the
-- vector write cannot. Rather than silently dropping the document, log.py parks
-- it here and `log.py vec backfill` re-embeds + drains it once the backend is up.
-- This table needs NO extension and is created by the plain (non-vec) init path.
CREATE TABLE IF NOT EXISTS vec_memory_deadletter (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_id      TEXT NOT NULL,           -- source row id (DEC-NNN, LSN-NNN, rowid, ...)
    kind        TEXT NOT NULL,           -- 'decision' | 'lesson' | 'rca' | 'reflection'
    text_blob   TEXT NOT NULL,           -- the text that failed to embed
    reason      TEXT,                    -- embed_unavailable | dim_mismatch:* | backfill_embed_failed
    failed_at   TEXT NOT NULL            -- ISO-8601 UTC
);

-- Embed OUTBOX — OPT-055 (atomic intent / transactional outbox)
-- The relational source INSERT (decisions/lessons) and the vector write run on
-- two separate connections, so a crash between them could leave a relational
-- row with no vector index and no dead-letter — a silent gap. The outbox closes
-- that race: the source INSERT and an outbox marker are written in the SAME
-- relational transaction (atomic intent-to-embed). `vec backfill` drains the
-- marker — embedding the text and CLEARING the marker in the SAME vec
-- transaction as the vec INSERT (vec-row-present  ==  marker-absent is atomic).
-- PLAIN table (no extension); the name avoids the substrings "vec_memory" and
-- "idx_vec_memory" so it survives the cmd_init strip-loop.
CREATE TABLE IF NOT EXISTS embed_outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,          -- 'decision' | 'lesson' | 'rca' | 'reflection'
    ref_id       TEXT NOT NULL,          -- source row id (DEC-NNN, LSN-NNN, ...)
    text_blob    TEXT NOT NULL,          -- the text to embed on the next backfill
    enqueued_at  TEXT NOT NULL,          -- ISO-8601 UTC
    UNIQUE(kind, ref_id)
);

-- Embed PROVENANCE — OPT-055 (model-swap enforcement)
-- Records which embed model + dimensionality produced each (kind, ref_id) vec
-- row. When the live model differs from a stored provenance model (same dim),
-- log.py emits a LOUD one-time banner AND auto-enqueues the stale rows into
-- embed_outbox for re-embed — the invariant ENFORCES, it does not merely report.
-- (A DIMENSION mismatch remains a hard stop via _assert_vec_dim -> exit 2.)
-- PLAIN table; name avoids "vec_memory"/"idx_vec_memory" for strip-loop survival.
CREATE TABLE IF NOT EXISTS embed_provenance (
    kind         TEXT NOT NULL,          -- 'decision' | 'lesson' | 'rca' | 'reflection'
    ref_id       TEXT NOT NULL,          -- source row id
    embed_model  TEXT NOT NULL,          -- model id that produced the embedding
    dims         INTEGER NOT NULL,       -- embedding dimensionality
    embedded_at  TEXT NOT NULL,          -- ISO-8601 UTC
    PRIMARY KEY(kind, ref_id)
);

-- Nexus self-feedback — DEC-019 (self-feedback MVP)
-- Per-project friction log: project agents self-report when Nexus itself blocks,
-- confuses, or stalls them (gate DENY, NEEDS-DECISION, REVISE, wrong-fit
-- persona/skill, roster mismatch, missing context). This table SHIPS in every
-- install (per-project capture); only the Plexus-side `feedback harvest` reads
-- ACROSS projects (via project_registry) and aggregates into the Plexus-only
-- improvement_backlog. Two write paths: the `nexus_submit_feedback` MCP tool
-- (source='tool') and the passive SubagentStop marker-capture hook (source='hook').
CREATE TABLE IF NOT EXISTS nexus_feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    source       TEXT NOT NULL,          -- tool | hook
    severity     TEXT NOT NULL,          -- critical | high | medium | low | info
    category     TEXT NOT NULL,          -- gate_deny | gate_needs_decision | gate_revise_stall
                                         -- | unclear_persona | unclear_skill | missing_context
                                         -- | roster_mismatch | workflow_friction | other
    message      TEXT NOT NULL,          -- the friction description (what blocked/confused/stalled)
    context_json TEXT,                   -- optional JSON blob (turn_id, persona, marker, …)
    source_file  TEXT,                   -- optional path the friction relates to
    captured_at  TEXT NOT NULL,          -- ISO-8601 UTC
    resolved_at  TEXT,                   -- NULL = open; ISO ts once a human resolves it
    reviewed_by  TEXT,                   -- who triaged/resolved it (Plexus harvest sets this)
    nexus_version TEXT                   -- installed Nexus version at capture time ('unknown' if pre-migration / unreadable)
);

CREATE INDEX IF NOT EXISTS idx_feedback_severity_category
    ON nexus_feedback(severity, category, captured_at DESC);

-- PLEXUS — Project Registry
CREATE TABLE IF NOT EXISTS project_registry (
  id INTEGER PRIMARY KEY,
  project_path TEXT NOT NULL UNIQUE,
  current_version TEXT NOT NULL,
  installed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  install_method TEXT NOT NULL CHECK (install_method IN ('fresh', 'existing', 'manual')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
  notes TEXT
);

CREATE TABLE IF NOT EXISTS project_version_history (
  id INTEGER PRIMARY KEY,
  project_path TEXT NOT NULL,
  version TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('installed', 'installed-existing', 'updated', 'removed', 'rolled-back')),
  acted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_registry_path ON project_registry(project_path);
CREATE INDEX IF NOT EXISTS idx_history_path ON project_version_history(project_path);
CREATE INDEX IF NOT EXISTS idx_history_acted_at ON project_version_history(acted_at);

-- Nexus-improvement backlog (the evaluated-vs-unread tracker)
-- DB-as-truth durable store (DEC binding): distilled research notes with
-- relevance_to_nexus >= THRESHOLD are auto-inserted as review_state='unread';
-- the user manually promotes a subset to 'flagged'. review_state lives HERE
-- (the DB column), NOT in the note's A4 frontmatter contract.
--   review_state: unread    — auto-populated, not yet looked at by a human
--                 evaluated  — human has reviewed it, no further action
--                 flagged    — human elevated it for nexus-improvement research
--                 dismissed  — human decided it is not relevant
-- populate NEVER downgrades a row already in {evaluated,flagged,dismissed}.
CREATE TABLE IF NOT EXISTS nexus_improvements (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  note_path       TEXT NOT NULL UNIQUE,           -- repo-relative path to the distilled source note
  source_url      TEXT,
  title           TEXT,
  relevance_score INTEGER,                         -- relevance_to_nexus (1-5) from the note frontmatter
  review_state    TEXT NOT NULL DEFAULT 'unread'
    CHECK (review_state IN ('unread', 'evaluated', 'flagged', 'dismissed')),
  flag_note       TEXT,                            -- free-text 'why this matters to Nexus'
  evidence_present INTEGER NOT NULL DEFAULT 0,     -- 1 if the note carries a Claims/evidence section
  tags            TEXT,                            -- comma-joined tags snapshot
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nexus_improvements_state
  ON nexus_improvements(review_state, relevance_score DESC);

-- ===========================================================================
-- OPT-054 — bi-temporal memory consolidation (TASK-035)
-- ===========================================================================
-- decisions / lessons / semantic_facts lose history TODAY via INSERT OR REPLACE
-- (re-writing the same id/key silently deletes the prior row). procedures and
-- feature_specs are clean (autoincrement id + separate natural key) but receive
-- the same additive columns for uniformity.
--
-- The columns below are NOT declared inline in the CREATE TABLE statements above
-- because those use IF NOT EXISTS and would never touch the live project.db. They
-- are applied by an IDEMPOTENT ALTER-TABLE migration in log.py `cmd_init`
-- (_migrate_bitemporal_columns), which is safe + re-runnable on both fresh and
-- existing databases (decisions=10, lessons=2 real rows at migration time).
--
-- Additive bi-temporal columns added to: decisions, lessons, semantic_facts,
-- procedures, feature_specs —
--   valid_from    TEXT            -- ISO-8601; backfilled from the row's own
--                                 -- creation timestamp (decided_at / recorded_at /
--                                 -- created_at); set to now() on every new row.
--   valid_to      TEXT            -- NULL = current; <ISO ts> = closed/superseded.
--   superseded_by TEXT            -- id of the row that replaced this one.
--   supersedes    TEXT            -- id of the row this one replaced.
--   content_hash  TEXT            -- sha256 prefix of the FULL versioned payload
--                                 -- (FORK-1: ALL user-facing columns, NOT just the
--                                 -- embed blob), so an edit to consequences/status
--                                 -- is detected and versioned rather than NOOP-dropped.
--   is_tombstone  INTEGER DEFAULT 0  -- 1 = retire marker (recall hides it).
--
-- Supersession marks the OLD row (valid_to=now, superseded_by, status='superseded')
-- and re-suffixes its id (DEC-NNN -> DEC-NNN@<ts>) so the bare logical key stays
-- free for exactly one CURRENT row. NEVER deletes — history is lossless.
--
-- Recall default = current-only (valid_to IS NULL AND is_tombstone=0); --history
-- walks the full chain. One current row per logical key is enforced by a partial
-- unique index built (after a --dry-run dup-check, FORK-3) in the same migration.

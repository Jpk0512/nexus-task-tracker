-- AI Interaction Dashboard — project memory schema
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
    key                 TEXT NOT NULL UNIQUE,
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
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    kind TEXT PARTITION KEY,  -- 'decision' | 'lesson' | 'rca' | 'reflection'
    ref_id TEXT,              -- DEC-NNN, LSN-NNN, agent_root_cause_log.id, reflection_snapshot.id
    text_blob TEXT,           -- embedded source text; stored for result display
    created_at TEXT,          -- ISO-8601 UTC; mirrors source table row timestamp
    embedding float[768]      -- nomic-embed-text-v1.5 via LM Studio; 768-dim L2-normalized
);
-- vec0 virtual tables manage their own HNSW index; CREATE INDEX is not supported on them

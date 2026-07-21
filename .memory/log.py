#!/usr/bin/env python3
"""Project memory CLI — log sessions, tasks, decisions, and context snapshots."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# BOOTSTRAP RE-EXEC GUARD (must run BEFORE any sqlite work)
# ---------------------------------------------------------------------------
# The memory core requires a Python that can load the sqlite-vec C extension.
# System python3 on macOS (3.9, --without-extension-loading) silently cannot —
# every embed/recall then degrades to a no-op and memory rots without a sound.
# Root cause: enable_load_extension is unavailable / `import sqlite_vec` missing.
# Fix: detect that capability gap and re-exec under the dedicated memory venv,
# which is provisioned with python 3.12 + sqlite-vec. Idempotent and loop-safe.
import os as _os
import sqlite3 as _sqlite3_boot
import sys as _sys_boot

_VENV_PY = _os.path.join(
    _os.path.dirname(_os.path.realpath(__file__)), ".venv", "bin", "python"
)


# Test/ops seam: NEXUS_DISABLE_VEC=1 force-degrades the vec path (no re-exec, no
# extension load) so the no-sqlite-vec code path is exercisable deterministically
# on any interpreter, independent of machine state. Honoured here AND in
# _vec_conn() so init/recall behave identically to a real no-extension host.
_VEC_FORCE_DISABLED = bool(_os.environ.get("NEXUS_DISABLE_VEC"))

# Set when this process could NOT obtain sqlite-vec (no capable interpreter AND
# no venv to re-exec into, or NEXUS_DISABLE_VEC). The CLI stays ALIVE in this
# state: init creates every core table and skips only the vec0 virtual table;
# recall is unavailable unless --fallback keyword is requested. Never fatal.
_VEC_DEGRADED = False


def _sqlite_vec_capable() -> bool:
    """True iff this interpreter can load sqlite extensions AND import sqlite_vec."""
    if _VEC_FORCE_DISABLED:
        return False
    try:
        _c = _sqlite3_boot.connect(":memory:")
        try:
            _c.enable_load_extension(True)
        finally:
            _c.close()
        import sqlite_vec as _probe  # noqa: F401
        return True
    except Exception:
        return False


def _bootstrap_reexec() -> None:
    # Already running under the venv interpreter — never re-exec (loop guard).
    if _os.path.realpath(_sys_boot.executable) == _os.path.realpath(_VENV_PY):
        return
    if _VEC_FORCE_DISABLED:
        # Forced degrade: never re-exec, even if a capable venv exists — tests
        # need this interpreter's no-vec behaviour, not the venv's.
        globals()["_VEC_DEGRADED"] = True
        return
    if _sqlite_vec_capable():
        return
    if _os.path.isfile(_VENV_PY):
        # Preferred path: a dedicated venv with sqlite-vec exists — re-exec into it
        # so the full semantic-recall surface is available.
        _os.execv(_VENV_PY, [_VENV_PY, __file__, *_sys_boot.argv[1:]])
    # No capable interpreter AND no venv to re-exec into. DO NOT exit — degrade.
    # `init` must still create every core table so persistence is structurally
    # alive (the post-install health gate checks for those tables, not vec). The
    # vec0 virtual table and semantic recall are deferred until a venv is built.
    globals()["_VEC_DEGRADED"] = True
    _sys_boot.stderr.write(
        "[memory] WARNING: sqlite-vec unavailable under "
        f"{_sys_boot.executable} and no .memory/.venv to re-exec into — "
        "semantic recall deferred. Core memory (sessions/tasks/decisions/…) "
        "still works. Build .memory/.venv with sqlite-vec to enable recall:\n"
        "  uv venv .memory/.venv --python 3.12 && "
        "uv pip install --python .memory/.venv/bin/python sqlite-vec\n"
    )


_bootstrap_reexec()

# Imports below intentionally follow the bootstrap guard so a capability-driven
# re-exec happens with a minimal import surface (E402 is expected here).
import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

# DB_PATH honours NEXUS_DB_PATH so tests (and ad-hoc tooling) can point the CLI
# at a scratch database without touching the real project.db. Falls back to the
# canonical project.db next to this file. Tests that import log.py as a module
# monkeypatch this module-global directly; the env var covers the subprocess path.
DB_PATH = Path(os.environ.get("NEXUS_DB_PATH") or (Path(__file__).parent / "project.db"))
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
TASKS_MD_PATH = Path(__file__).resolve().parent.parent / "docs" / "TASKS.md"
MEMORY_FILES_DIR = Path(__file__).parent / "files"


# TASK-004 / DEC-040 / incident #10: fat uncheckpointed WAL + many concurrent
# hook/agent writers + a too-low busy_timeout caused 10x "malformed database
# schema" corruption incidents. busy_timeout=15000 gives contenders more room
# to wait instead of erroring; wal_autocheckpoint=200 (pages, default 1000)
# checkpoints far more often so the WAL can't balloon between writers.
# Incident #10 (R3) showed DEC-040 did not survive Workflow-level concurrency
# because it was only applied on the ONE _conn() path — _vec_conn() (the
# per-decision/lesson/rca/reflection embed side-effect, opened on essentially
# every hot write) and the cross-project feedback-resolve connection each
# opened their OWN raw sqlite3.connect with a bare busy_timeout=5000 and no
# wal_autocheckpoint at all, so a second concurrent writer per write burst was
# never hardened. Every connection path funnels through this one function now
# so there is exactly one place the pragma set can drift.
def _harden_connection(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA wal_autocheckpoint=200")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    _harden_connection(conn)
    # One-shot checkpoint-truncate shrinks any WAL already grown before this
    # fix shipped / from a prior burst. None of this touches schema or row data.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _installed_nexus_version(memory_dir=None):  # type: ignore[no-untyped-def]
    """Read the installed Nexus version from <memory_dir>/.nexus-version (fail-soft).

    The version file is written by safe_update.py after each install/update and
    lives in the project's .memory/ dir. ``memory_dir`` defaults to the directory
    holding the active project.db (DB_PATH.parent) so a feedback row captured into
    a project DB carries THAT project's version — resolved relative to the DB root,
    NOT cwd. Returns the stripped version string, or 'unknown' on ANY error
    (missing file, unreadable, empty) so feedback capture never fails because
    version attribution is unavailable.
    """
    base = Path(memory_dir) if memory_dir is not None else DB_PATH.parent
    try:
        text = (base / ".nexus-version").read_text().strip()
        return text or "unknown"
    except OSError:
        return "unknown"


def _version_tuple(version):  # type: ignore[no-untyped-def]
    """Parse 'X.Y.Z' into a comparable (X, Y, Z) int tuple for semver ordering.

    'unknown' (and any unparseable / empty string) sorts LOWEST — treated as the
    oldest possible version so it is the first thing an upgrade supersedes. Extra
    dotted segments beyond X.Y.Z are included; missing trailing segments pad with 0.
    Non-numeric segments degrade to 0 rather than raising (fail-soft for malformed
    legacy stamps).
    """
    if not version or version == "unknown":
        return (-1,)
    parts = str(version).split(".")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """True if `column` exists on `table` (used to stay safe pre-migration)."""
    return any(r[1] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def _next_id(conn: sqlite3.Connection, table: str, prefix: str) -> str:
    """Allocate the next sequential id (e.g. DEC-011) for a logical-key table.

    FORK-2: compute the high-water mark from CURRENT rows ONLY
    (valid_to IS NULL AND is_tombstone=0). Superseded history rows carry a
    suffixed id (DEC-007@<ts>); ``ORDER BY id DESC`` would otherwise surface
    that suffixed id first and ``int(split('-')[-1])`` would parse the timestamp
    tail, poisoning the increment. Filtering to current rows excludes them.

    Only the bare ``PREFIX-NNN`` ids are considered when deriving the max number,
    so a stray suffixed id that is somehow still current cannot corrupt the count.

    Incident #10 concurrency fix: the caller always INSERTs the allocated id in
    the SAME transaction right after this returns, but Python's sqlite3 module
    only takes SQLite's write lock lazily (on the first DML), so two concurrent
    processes could both read the same high-water mark before either writes,
    then both try to INSERT the same id -> UNIQUE constraint failure under
    load (reproduced by .memory/tests/test_concurrent_write_wal.py). Escalating
    to BEGIN IMMEDIATE here, before the read, takes the write lock upfront so
    busy_timeout actually serializes racing writers instead of letting them
    race the read. Guarded by in_transaction so a caller that already opened
    one (or a bare read-only caller with no pending writer) is unaffected.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if _table_has_column(conn, table, "valid_to") and _table_has_column(
        conn, table, "is_tombstone"
    ):
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE valid_to IS NULL AND is_tombstone=0"
        ).fetchall()
    else:
        rows = conn.execute(f"SELECT id FROM {table}").fetchall()
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    nums = [int(m.group(1)) for r in rows if (m := pattern.match(r["id"]))]
    if not nums:
        return f"{prefix}-001"
    return f"{prefix}-{max(nums) + 1:03d}"


# ---------------------------------------------------------------------------
# OPT-054 — bi-temporal memory consolidation (TASK-035)
# ---------------------------------------------------------------------------
# Additive columns + supersession plumbing for the logical-key tables. The
# content_hash covers the FULL versioned payload (FORK-1) so an edit to ANY
# user-facing field (e.g. consequences/status) is detected and versioned rather
# than silently NOOP-dropped. Supersession marks the old row and re-suffixes its
# id so the bare logical key stays free for exactly one current row.
import hashlib  # noqa: E402

_BITEMPORAL_TABLES = ["decisions", "lessons", "semantic_facts", "procedures", "feature_specs"]

# Per-table: the column whose value backfills valid_from for pre-existing rows.
_BITEMPORAL_VALID_FROM_SRC = {
    "decisions": "decided_at",
    "lessons": "recorded_at",
    "semantic_facts": "created_at",
    "procedures": "created_at",
    "feature_specs": "created_at",
}

# Per-table: the ordered columns that make up the content-hash payload. FORK-1 —
# ALL user-facing/versioned fields, NEVER timestamps/session ids (audit metadata).
_CONTENT_HASH_COLUMNS = {
    "decisions": ["title", "status", "context", "decision", "rationale", "alternatives", "consequences"],
    "lessons": ["trigger", "title", "body", "applies_to", "validated", "source_decision_id"],
    "semantic_facts": ["key", "value", "pinned"],
    "procedures": ["name", "trigger_pattern", "steps_json"],
    "feature_specs": ["title", "status", "spec_path", "description"],
}


def _content_hash(table: str, payload: dict) -> str:
    """Stable 16-char sha256 prefix of the FULL versioned payload (FORK-1).

    Keys are the table's content-hash columns in canonical order; values are
    normalised to '' for None so a NULL vs '' never spuriously flips the hash.
    Timestamps and session/audit ids are deliberately excluded — only fields a
    user could edit participate, so editing any of them is correctly detected.
    """
    cols = _CONTENT_HASH_COLUMNS[table]
    norm = {c: ("" if payload.get(c) is None else str(payload.get(c))) for c in cols}
    blob = json.dumps(norm, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _current_row(conn: sqlite3.Connection, table: str, logical_id: str) -> sqlite3.Row | None:
    """Return the single CURRENT row for a logical key, or None.

    Current = bare id match, valid_to IS NULL, not a tombstone.
    """
    return conn.execute(
        f"SELECT * FROM {table} WHERE id=? AND valid_to IS NULL AND is_tombstone=0",
        (logical_id,),
    ).fetchone()


def _current_fact_row(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """Return the single CURRENT semantic_fact for a logical key, or None.

    semantic_facts use ``key`` (TEXT) as the logical key and an INTEGER autoincrement
    ``id`` — they cannot be re-keyed the way decisions/lessons are.  Current =
    valid_to IS NULL, is_tombstone=0.
    """
    return conn.execute(
        "SELECT * FROM semantic_facts WHERE key=? AND valid_to IS NULL AND is_tombstone=0",
        (key,),
    ).fetchone()


def _close_fact_row(
    conn: sqlite3.Connection,
    row_id: int,
    superseded_by_key: str,
    closed_at: str,
) -> None:
    """Mark an existing semantic_fact row as superseded.

    Sets valid_to and superseded_by on the row identified by its INTEGER ``id``.
    Unlike decisions/lessons there is no id re-keying: the INTEGER pk stays in
    place; the partial-unique index on ``key`` is satisfied because the new
    current row (with the same ``key``) is inserted after this row is closed.
    """
    conn.execute(
        "UPDATE semantic_facts SET valid_to=?, superseded_by=? WHERE id=?",
        (closed_at, superseded_by_key, row_id),
    )


def _close_and_suffix_old_row(
    conn: sqlite3.Connection,
    table: str,
    logical_id: str,
    new_id: str,
    closed_at: str,
) -> str:
    """Close the current row for `logical_id` and free the bare id for the new row.

    The old row is re-keyed to ``<logical_id>@<closed_at>`` so the bare id can be
    reused by the incoming current row (the table's PK is `id`). The old row is
    marked superseded (valid_to, superseded_by, status='superseded') — NEVER
    deleted, so the chain is lossless. Returns the old row's NEW (suffixed) id.
    """
    suffixed = f"{logical_id}@{closed_at}"
    sets = ["id=?", "valid_to=?", "superseded_by=?"]
    vals: list = [suffixed, closed_at, new_id]
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if "status" in cols:
        sets.append("status='superseded'")
    conn.execute(
        f"UPDATE {table} SET {', '.join(sets)} WHERE id=?",
        (*vals, logical_id),
    )
    return suffixed


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def _migrate_tasks_stall_columns(conn: sqlite3.Connection) -> None:
    """Idempotent: add stall_count + last_persona to tasks if not present."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "stall_count" not in existing:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN stall_count INTEGER NOT NULL DEFAULT 0"
        )
    if "last_persona" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN last_persona TEXT")


def _migrate_feedback_version_column(conn: sqlite3.Connection) -> None:
    """Idempotent: add nexus_version to nexus_feedback; backfill NULLs to 'unknown'.

    Mirrors the content_hash backfill pattern (_migrate_bitemporal_columns): the
    ALTER is guarded by a PRAGMA table_info check so a second run is a no-op, and
    the backfill only touches rows whose nexus_version IS NULL (legacy feedback
    captured before this migration). Safe to re-run on a live DB carrying data —
    no deletes, no edits to any other column.
    """
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='nexus_feedback'"
    ).fetchone():
        return
    existing = {r[1] for r in conn.execute("PRAGMA table_info(nexus_feedback)")}
    if "nexus_version" not in existing:
        conn.execute("ALTER TABLE nexus_feedback ADD COLUMN nexus_version TEXT")
    conn.execute(
        "UPDATE nexus_feedback SET nexus_version='unknown' WHERE nexus_version IS NULL"
    )


def _migrate_validation_log_columns(conn: sqlite3.Connection) -> None:
    """Idempotent: add the nullable columns to validation_log introduced by the
    U1 instrumentation pass (DEC-029 lens-tiering + completeness-check) and the
    R1-T08 lens-gate v2 tier columns.

    Five additive columns (all nullable — no NOT NULL, no backfill required):
      files_changed_json  TEXT  — JSON array of the implementer's declared
                                  files_changed; the completeness-check CLI uses
                                  this to confirm a PASS row covers the changed set.
      revise_reason       TEXT  — machine-readable reason when the stored verdict
                                  != PASS; auto-filled from derive_verdict_from_report
                                  binding_note at INSERT time. NULL on PASS rows.
      dispatch_started_at TEXT  — ISO-8601 UTC stamp of when the validating Lens
                                  dispatch began (distinct from validated_at = the
                                  row-write time); lets instrumentation compute the
                                  Lens wall-clock duration.
      lens_type            TEXT — 'T0'|'T1'|'T2': the depth tier Lens actually ran
                                  at for this row (R1-T08). NULL on rows written
                                  before this migration or by callers that haven't
                                  adopted --lens-type yet — read as "tier unknown"
                                  by any v2 gate query, never as a false match.
      risk_tier            TEXT — 'T0'|'T1'|'T2': the tier the orchestrator's
                                  classifier required ahead of dispatch (R1-T08).
                                  Kept separate from lens_type so a future gate can
                                  assert delivered-depth >= required-depth instead
                                  of conflating "required" and "actual".

    agent_validated (the 'lens' invariant lens-gate.sh keys off) is NOT touched by
    this migration — lens_type/risk_tier are purely additive siblings alongside it.

    Safe to re-run on a live project.db — ALTER TABLE branches are guarded by
    PRAGMA table_info so a second run is a clean no-op.  The table itself may not
    exist yet on a brand-new DB; the guard handles that too.

    Incident #10 concurrency fix: this is the same PRAGMA-check-then-ALTER
    TOCTOU shape as _next_id's high-water-mark read (see that function's
    docstring). cmd_init's call site already runs inside its own outer
    BEGIN IMMEDIATE, but cmd_validation_add / cmd_validation_completeness_check
    call this function directly against a fresh `with _conn() as conn:` with
    no transaction open — two concurrent `validation add` calls against a DB
    that predates this migration could both see a column absent and both
    ALTER, and the loser gets 'duplicate column name' (the same class of
    concurrent-DDL race lens-gate.sh's _init_table had). Escalating to
    BEGIN IMMEDIATE here, before the check, takes the write lock upfront so
    busy_timeout serializes racers instead of letting them race the check.
    Guarded by in_transaction so a caller that already opened one (cmd_init's
    outer transaction) is unaffected.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='validation_log'"
    ).fetchone():
        return  # table not yet created — cmd_init will create it fresh with all columns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(validation_log)")}
    additive: list[tuple[str, str]] = [
        ("files_changed_json", "TEXT"),
        ("revise_reason", "TEXT"),
        ("dispatch_started_at", "TEXT"),
        ("lens_type", "TEXT"),
        ("risk_tier", "TEXT"),
    ]
    for col, decl in additive:
        if col not in existing:
            conn.execute(f"ALTER TABLE validation_log ADD COLUMN {col} {decl}")


def _migrate_dispatch_telemetry_columns(conn: sqlite3.Connection) -> None:
    """Idempotent: add the two nullable Art. XIII.d decompose-cue columns to
    dispatch_telemetry (R2-T15 / spec §7, FIX-2).

    Two additive columns (both nullable — no NOT NULL, no backfill required):
      independent_subtask_count INTEGER — count of independent subtasks the
                                 orchestrator identified before this dispatch.
                                 NULL = not recorded (pre-migration row, or a
                                 caller that hasn't adopted the flag yet).
      decomposition_considered  INTEGER — 0/1 boolean-as-int; whether the
                                 orchestrator explicitly evaluated a
                                 Workflow/fan-out decomposition before issuing
                                 this dispatch (parallel-first-check, DEC-029).
                                 NULL = not recorded.

    Does not touch the existing one-row-per-dispatch shape or any column
    health.py's completion-time KPI test asserts against — purely additive
    siblings alongside tokens/duration_ms/etc.

    Safe to re-run on a live project.db — ALTER TABLE branches are guarded by
    PRAGMA table_info so a second run is a clean no-op. The table itself may
    not exist yet on a brand-new DB; the guard handles that too.
    """
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dispatch_telemetry'"
    ).fetchone():
        return  # table not yet created — cmd_init will create it fresh with all columns
    existing = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_telemetry)")}
    additive: list[tuple[str, str]] = [
        ("independent_subtask_count", "INTEGER"),
        ("decomposition_considered", "INTEGER"),
    ]
    for col, decl in additive:
        if col not in existing:
            conn.execute(f"ALTER TABLE dispatch_telemetry ADD COLUMN {col} {decl}")


def _migrate_sessions_telemetry_columns(conn: sqlite3.Connection) -> None:
    """Idempotent: add tokens_total + duration_ms to sessions (drift-analysis
    Finding #6, 2026-07-12 — per-session cost rollup).

    Two additive columns (both nullable — no NOT NULL, no backfill required):
      tokens_total  INTEGER — SUM(dispatch_telemetry.tokens) across every
                    dispatch_telemetry row for this session_id. Populated at
                    `session end` time; NULL = pre-migration session or a
                    session with zero captured dispatches.
      duration_ms   INTEGER — SUM(dispatch_telemetry.duration_ms) across the
                    same row set, same populate/NULL rules.

    Safe to re-run on a live project.db — ALTER TABLE branches are guarded by
    PRAGMA table_info so a second run is a clean no-op. Mirrors
    _migrate_dispatch_telemetry_columns exactly (same pattern, same file).
    """
    if not conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone():
        return  # table not yet created — cmd_init will create it fresh
    existing = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    additive: list[tuple[str, str]] = [
        ("tokens_total", "INTEGER"),
        ("duration_ms", "INTEGER"),
    ]
    for col, decl in additive:
        if col not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")


TASK_DOMAINS = ("nexus", "plexus", "kb", "ops", "other")
_DOMAIN_CHECK_SQL = (
    "TEXT CHECK(domain IN ('nexus','plexus','kb','ops','other'))"
)


def _migrate_task_domain_columns(conn: sqlite3.Connection) -> None:
    """Idempotent (DEC-098): add nullable `domain` to tasks + nexus_feedback.

    NULL-allowed so every grandfathered row passes the CHECK (NULL IN (...)
    evaluates NULL, which SQLite treats as pass). ALTERs are guarded by
    PRAGMA table_info so a second run is a clean no-op; safe on a live DB.
    """
    for table in ("tasks", "nexus_feedback"):
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone():
            continue
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "domain" not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN domain {_DOMAIN_CHECK_SQL}")


def _migrate_semantic_facts_drop_global_unique(conn: sqlite3.Connection) -> None:
    """OPT-054 / TASK-036: drop the old column-level UNIQUE on semantic_facts.key.

    The original schema declared ``key TEXT NOT NULL UNIQUE``, which creates a
    sqlite_autoindex that forbids multiple rows with the same key — exactly what
    bi-temporal supersession needs (old row + new current row both carry the same
    key).  The partial-unique index (idx_semantic_facts_current) enforces the
    one-current-row-per-key invariant instead.

    Idempotent: no-op if the autoindex is already absent (already migrated or
    created from the updated schema.sql).  Uses the standard SQLite table-rename
    approach: create replacement, copy, drop old, rename — all in one transaction
    so no data is lost on failure.
    """
    autoindex = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='semantic_facts' "
        "AND name LIKE 'sqlite_autoindex_%'",
    ).fetchone()
    if autoindex is None:
        return  # already migrated or fresh schema without the constraint

    # Derive current column list so we copy exactly what exists.
    cols_info = conn.execute("PRAGMA table_info(semantic_facts)").fetchall()
    col_names = [r[1] for r in cols_info]

    # Build the new table DDL from the current column list so we carry over any
    # ALTER-added bi-temporal columns that may already be present.
    def _col_decl(r: tuple) -> str:  # type: ignore[type-arg]
        # r: (cid, name, type, notnull, dflt_value, pk)
        _, name, typ, notnull, dflt, pk = r[0], r[1], r[2], r[3], r[4], r[5]
        decl = f"{name} {typ}"
        if pk:
            decl += " PRIMARY KEY AUTOINCREMENT"
        if notnull and not pk:
            decl += " NOT NULL"
        if dflt is not None:
            decl += f" DEFAULT {dflt}"
        return decl

    col_decls = ", ".join(_col_decl(tuple(r)) for r in cols_info)
    conn.execute(f"CREATE TABLE semantic_facts_new ({col_decls})")
    joined = ", ".join(col_names)
    conn.execute(
        f"INSERT INTO semantic_facts_new ({joined}) SELECT {joined} FROM semantic_facts"
    )
    conn.execute("DROP TABLE semantic_facts")
    conn.execute("ALTER TABLE semantic_facts_new RENAME TO semantic_facts")


def _migrate_bitemporal_columns(conn: sqlite3.Connection) -> None:
    """OPT-054 (TASK-035): idempotent, re-runnable bi-temporal migration.

    For each logical-key table (decisions, lessons, semantic_facts, procedures,
    feature_specs):
      1. ADD the six additive columns if absent (valid_from, valid_to,
         superseded_by, supersedes, content_hash, is_tombstone).
      2. BACKFILL existing rows: valid_from <- the row's own creation timestamp,
         valid_to <- NULL (current), is_tombstone <- 0, content_hash <- the FULL
         versioned-payload hash (FORK-1). Only rows still missing a content_hash
         are touched, so re-running never rewrites already-migrated rows.
      3. DRY-RUN dup-check (FORK-3) then build the partial unique index — exactly
         one CURRENT row per logical key.

    Safe to run on the live project.db (no deletes, no payload edits). A skipped
    table (does not exist yet) is silently ignored.

    Incident #10 concurrency fix: this runs on EVERY decision/lesson/task/etc.
    add (not just `init`), and each additive ALTER is only guarded by a
    PRAGMA table_info check-then-act — under concurrent writers two processes
    can both see a column absent, then both run ADD COLUMN, and the loser gets
    'duplicate column name' (reproduced by
    .memory/tests/test_concurrent_write_wal.py). BEGIN IMMEDIATE upfront takes
    the write lock before the check, so busy_timeout serializes the racers
    instead of letting them race the check.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    # TASK-036: drop the global UNIQUE on semantic_facts.key before adding the
    # partial-unique index so supersession inserts (old row + new row, same key)
    # are not blocked by the old column-level autoindex.
    sf_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_facts'"
    ).fetchone()
    if sf_exists:
        _migrate_semantic_facts_drop_global_unique(conn)

    for table in _BITEMPORAL_TABLES:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        additive = [
            ("valid_from", "TEXT"),
            ("valid_to", "TEXT"),
            ("superseded_by", "TEXT"),
            ("supersedes", "TEXT"),
            ("content_hash", "TEXT"),
            ("is_tombstone", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, decl in additive:
            if name not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

        # Backfill only rows that have not been stamped yet (content_hash IS NULL).
        src_ts = _BITEMPORAL_VALID_FROM_SRC[table]
        has_src = _table_has_column(conn, table, src_ts)
        unstamped = conn.execute(
            f"SELECT * FROM {table} WHERE content_hash IS NULL"
        ).fetchall()
        for row in unstamped:
            rd = dict(row)
            vf = (rd.get(src_ts) if has_src else None) or _now()
            chash = _content_hash(table, rd)
            pk = "id" if "id" in rd else "rowid"
            pk_val = rd.get("id") if pk == "id" else row["rowid"]
            conn.execute(
                f"UPDATE {table} SET valid_from=COALESCE(valid_from, ?), "
                f"is_tombstone=COALESCE(is_tombstone, 0), content_hash=? "
                f"WHERE {pk}=?",
                (vf, chash, pk_val),
            )

        _build_current_unique_index(conn, table)


def _build_current_unique_index(conn: sqlite3.Connection, table: str) -> None:
    """FORK-3: dry-run dup-check, then build the partial unique 'one current row
    per logical key' index. Aborts LOUD (exit 4) if pre-existing duplicates would
    violate the constraint, rather than letting CREATE UNIQUE INDEX raise opaquely.

    decisions/lessons/feature_specs key on `id`; semantic_facts keys on `key`;
    procedures keys on `name`. The index covers only CURRENT, non-tombstone rows.
    """
    key_col = {
        "decisions": "id",
        "lessons": "id",
        "feature_specs": "id",
        "semantic_facts": "key",
        "procedures": "name",
    }[table]
    if not _table_has_column(conn, table, key_col):
        return
    dups = conn.execute(
        f"SELECT {key_col} AS k, COUNT(*) AS n FROM {table} "
        f"WHERE valid_to IS NULL AND is_tombstone=0 "
        f"GROUP BY {key_col} HAVING n > 1"
    ).fetchall()
    if dups:
        listed = ", ".join(f"{d['k']} (x{d['n']})" for d in dups)
        print(
            f"FATAL: {table} has duplicate CURRENT rows for logical key(s): {listed}. "
            f"Refusing to build the partial-unique index until the duplicates are "
            f"consolidated (close all but one with valid_to). No data was modified.",
            file=sys.stderr,
        )
        sys.exit(4)
    idx = f"idx_{table}_current"
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {table}({key_col}) "
        f"WHERE valid_to IS NULL AND is_tombstone=0"
    )


# ---------------------------------------------------------------------------
# sqlite-vec helpers (Phase D Layer 2 — semantic memory)
# ---------------------------------------------------------------------------

_LM_STUDIO_EMBED_URL = "http://127.0.0.1:1234/v1/embeddings"
_EMBED_MODEL = "text-embedding-mxbai-embed-large-v1"
# Single source of truth for embedding dimensionality (P1-07). Every DDL,
# capacity assert, and serialize path derives from this constant.
_EMBED_DIM = 1024

# Emit the dead-letter banner at most once per process so a backend outage
# during a batch of writes is loud but not a screenful of noise (P1-03).
_DEADLETTER_BANNER_EMITTED = False


class VecUnavailable(RuntimeError):
    """sqlite-vec cannot be loaded in this process (no extension support, the
    sqlite_vec package is missing, or NEXUS_DISABLE_VEC forced degrade). Raised
    by _vec_conn so every caller degrades through one typed, catchable path
    instead of a raw ImportError/AttributeError leaking out."""


def _vec_conn() -> sqlite3.Connection:
    """Return a connection with sqlite-vec extension loaded.

    Raises VecUnavailable when this process cannot load sqlite-vec (degraded
    bootstrap or NEXUS_DISABLE_VEC) — callers catch it to skip/defer vec work.
    Enforces the dimension invariant (P1-07) on every vector connection: if a
    vec_memory already exists with an embedding dim != _EMBED_DIM, halt LOUD
    (exit 2) before any read/write can corrupt or misread the index.
    """
    if _VEC_DEGRADED or _VEC_FORCE_DISABLED:
        raise VecUnavailable(
            "sqlite-vec unavailable (degraded bootstrap or NEXUS_DISABLE_VEC)"
        )
    try:
        import sqlite_vec as _sv
        conn = sqlite3.connect(DB_PATH)
        _harden_connection(conn)
        # Same one-shot checkpoint-truncate as _conn(): bounds worst-case WAL
        # growth before THIS writer's own transaction, regardless of how many
        # other short-lived writers (hook processes, other _conn()/_vec_conn()
        # callers) have landed pages since the last auto-checkpoint.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        _sv.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.OperationalError) as exc:
        raise VecUnavailable(f"sqlite-vec load failed: {exc}") from exc
    _assert_vec_dim(conn)
    return conn


def _assert_vec_dim(conn: sqlite3.Connection) -> None:
    """Fail LOUD (exit 2) if an existing vec_memory's embedding dim != _EMBED_DIM (P1-07).

    A dimension mismatch means the model changed under us; every MATCH query
    would either error or silently return garbage. Better to halt than to rot.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memory'"
    ).fetchone()
    if row is None:
        return  # table not created yet — _apply_M001 will build it at _EMBED_DIM
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='vec_memory'"
        ).fetchone()
        sql = (ddl["sql"] if ddl and ddl["sql"] else "") or ""
        m = re.search(r"embedding\s+float\[(\d+)\]", sql)
        found = int(m.group(1)) if m else None
    except Exception:
        found = None
    if found is not None and found != _EMBED_DIM:
        print(
            f"FATAL: vec_memory embedding dim is float[{found}] but this build "
            f"expects float[{_EMBED_DIM}] (model {_EMBED_MODEL}). Refusing to "
            f"operate — rebuild vec_memory or restore the matching model.",
            file=sys.stderr,
        )
        sys.exit(2)


def _ensure_deadletter_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for the embed dead-letter queue (P1-03)."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS vec_memory_deadletter (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_id      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            text_blob   TEXT NOT NULL,
            reason      TEXT,
            failed_at   TEXT NOT NULL
        )"""
    )


def _deadletter_insert(
    conn: sqlite3.Connection, kind: str, ref_id: str, text_blob: str, reason: str
) -> None:
    """Park an un-embeddable row for later `vec backfill`, and emit ONE loud banner."""
    global _DEADLETTER_BANNER_EMITTED
    _ensure_deadletter_table(conn)
    conn.execute(
        "INSERT INTO vec_memory_deadletter(ref_id, kind, text_blob, reason, failed_at) "
        "VALUES (?,?,?,?,?)",
        (ref_id, kind, text_blob, reason, _now()),
    )
    if not _DEADLETTER_BANNER_EMITTED:
        _DEADLETTER_BANNER_EMITTED = True
        print(
            "\n"
            "!! vec_memory DEAD-LETTER: embedding backend unavailable — relational\n"
            f"!! row was saved but NOT vector-indexed (reason: {reason}). Parked in\n"
            "!! vec_memory_deadletter. Recover with: log.py vec backfill\n",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# embed outbox + provenance (OPT-055)
# ---------------------------------------------------------------------------
# Emit the model-swap banner at most once per process — a model change touching
# a batch of recalled/backfilled rows should be loud but not a screenful.
_MODEL_SWAP_BANNER_EMITTED = False


def _ensure_outbox_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for the embed outbox (OPT-055). PLAIN table, no extension."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embed_outbox (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT NOT NULL,
            ref_id       TEXT NOT NULL,
            text_blob    TEXT NOT NULL,
            enqueued_at  TEXT NOT NULL,
            UNIQUE(kind, ref_id)
        )"""
    )


def _ensure_provenance_table(conn: sqlite3.Connection) -> None:
    """Idempotent DDL for embed provenance (OPT-055). PLAIN table, no extension."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embed_provenance (
            kind         TEXT NOT NULL,
            ref_id       TEXT NOT NULL,
            embed_model  TEXT NOT NULL,
            dims         INTEGER NOT NULL,
            embedded_at  TEXT NOT NULL,
            PRIMARY KEY(kind, ref_id)
        )"""
    )


def _outbox_enqueue(
    conn: sqlite3.Connection, kind: str, ref_id: str, text_blob: str
) -> None:
    """Record intent-to-embed. MUST run inside the source row's relational txn so
    source-row + marker land atomically (OPT-055 A). INSERT OR REPLACE so a
    re-edited source row refreshes the pending text rather than duplicating."""
    _ensure_outbox_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO embed_outbox(kind, ref_id, text_blob, enqueued_at) "
        "VALUES (?,?,?,?)",
        (kind, ref_id, text_blob, _now()),
    )


def _outbox_clear(conn: sqlite3.Connection, kind: str, ref_id: str) -> None:
    """Clear an outbox marker. GUARDRAIL #1: callers MUST invoke this on the SAME
    connection / inside the SAME txn as the vec INSERT so vec-row-present and
    marker-absent flip atomically."""
    conn.execute(
        "DELETE FROM embed_outbox WHERE kind=? AND ref_id=?",
        (kind, ref_id),
    )


def _provenance_upsert(
    conn: sqlite3.Connection, kind: str, ref_id: str
) -> None:
    """Stamp the CURRENT embed model + dims for (kind, ref_id). Runs on the same
    connection as the vec INSERT (OPT-055 C1)."""
    _ensure_provenance_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO embed_provenance(kind, ref_id, embed_model, dims, embedded_at) "
        "VALUES (?,?,?,?,?)",
        (kind, ref_id, _EMBED_MODEL, _EMBED_DIM, _now()),
    )


def _detect_model_swap(conn: sqlite3.Connection) -> int:
    """Model-swap ENFORCE (OPT-055 C2). Find (kind, ref_id) whose stored
    provenance model differs from the live _EMBED_MODEL (SAME dim — a dim change
    is the _assert_vec_dim hard stop, not this path). For each stale row emit ONE
    loud banner and auto-enqueue it into embed_outbox for re-embed on the next
    backfill. Returns the number of rows enqueued.

    No-op (returns 0) if the provenance table does not yet exist.
    """
    global _MODEL_SWAP_BANNER_EMITTED
    have = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='embed_provenance'"
    ).fetchone()
    if have is None:
        return 0
    stale = conn.execute(
        "SELECT kind, ref_id, embed_model FROM embed_provenance "
        "WHERE embed_model != ? AND dims = ?",
        (_EMBED_MODEL, _EMBED_DIM),
    ).fetchall()
    if not stale:
        return 0
    if not _MODEL_SWAP_BANNER_EMITTED:
        _MODEL_SWAP_BANNER_EMITTED = True
        prior = stale[0]["embed_model"]
        print(
            "\n"
            "!! vec_memory MODEL SWAP: embeddings were produced by a DIFFERENT model\n"
            f"!! (stored: {prior}) than the live model ({_EMBED_MODEL}). {len(stale)} row(s)\n"
            "!! are stale and will return mixed-space distances. They have been\n"
            "!! auto-enqueued for re-embed. Recover with: log.py vec backfill\n",
            file=sys.stderr,
        )
    _ensure_outbox_table(conn)
    for r in stale:
        kind, ref_id = r["kind"], r["ref_id"]
        blob = conn.execute(
            "SELECT text_blob FROM vec_memory WHERE kind=? AND ref_id=?",
            (kind, ref_id),
        ).fetchone()
        if blob is None or not blob["text_blob"]:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO embed_outbox(kind, ref_id, text_blob, enqueued_at) "
            "VALUES (?,?,?,?)",
            (kind, ref_id, blob["text_blob"], _now()),
        )
    return len(stale)


def _l2_normalize(vec: list[float]) -> list[float]:
    import math as _math
    norm = _math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _embed(text: str) -> list[float] | None:
    """Call LM Studio embed endpoint. Returns None if unavailable.

    Timeout is controlled by NEXUS_EMBED_TIMEOUT (default 30s).
    On transient failure, retries up to 2 times with backoff (0.5s, 1.0s).
    Returns None only after all retries are exhausted.
    """
    import time
    import urllib.error as _uerr
    import urllib.request as _req

    _timeout = float(os.getenv("NEXUS_EMBED_TIMEOUT", "30"))
    _max_retries = 2
    _backoff_delays = [0.5, 1.0]

    payload = json.dumps({"model": _EMBED_MODEL, "input": text}).encode()

    for attempt in range(_max_retries + 1):
        request = _req.Request(
            _LM_STUDIO_EMBED_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _req.urlopen(request, timeout=_timeout) as resp:
                body = json.loads(resp.read())
                vec = body["data"][0]["embedding"]
                return _l2_normalize(vec)
        except (_uerr.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError):
            if attempt < _max_retries:
                time.sleep(_backoff_delays[attempt])
            else:
                return None
    return None


def _vec_insert(
    conn: sqlite3.Connection,
    kind: str,
    ref_id: str,
    text_blob: str,
    created_at: str,
) -> None:
    """Embed text_blob and insert into vec_memory.

    On embed failure the row is NOT silently dropped — it is parked in
    vec_memory_deadletter for `vec backfill` to drain once the backend recovers
    (P1-03). A dimension mismatch from the embed backend also dead-letters
    rather than corrupting the index.
    """
    import sqlite_vec as _sv
    vec = _embed(text_blob)
    if vec is None:
        _deadletter_insert(conn, kind, ref_id, text_blob, "embed_unavailable")
        return
    if len(vec) != _EMBED_DIM:
        _deadletter_insert(
            conn, kind, ref_id, text_blob,
            f"dim_mismatch:{len(vec)}!={_EMBED_DIM}",
        )
        return
    blob = _sv.serialize_float32(vec)
    conn.execute(
        "INSERT INTO vec_memory(kind, ref_id, text_blob, created_at, embedding) VALUES (?,?,?,?,?)",
        (kind, ref_id, text_blob, created_at, blob),
    )
    # OPT-055 C1 — stamp provenance with the live model + dims for this row.
    _provenance_upsert(conn, kind, ref_id)
    # OPT-055 GUARDRAIL #1 — clear the intent-to-embed marker on the SAME conn,
    # inside the SAME txn as the vec INSERT above. vec-row-present <=> marker-absent
    # is therefore atomic: a crash after the INSERT but before commit rolls BOTH
    # back together (no vec row with an absent marker, no marker with no vec row).
    _ensure_outbox_table(conn)
    _outbox_clear(conn, kind, ref_id)


def _apply_M001(conn: sqlite3.Connection) -> None:
    """Idempotent: create vec_memory virtual table if not present (requires extension loaded)."""
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memory'"
    ).fetchone()
    if existing:
        return
    conn.executescript(f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    kind TEXT PARTITION KEY,
    ref_id TEXT,
    text_blob TEXT,
    created_at TEXT,
    embedding float[{_EMBED_DIM}]
);
""")


def _migrate_registry_legacy_columns(conn: sqlite3.Connection) -> None:
    """S2-06: idempotent migration-002 for project_registry legacy fields.

    schema.sql creates project_registry WITHOUT the four columns added by
    migrations/002_project_registry_legacy_fields.sql (legacy_id, include_prism,
    has_ledger, last_validated). cmd_registry_list SELECTs them, so a fresh DB
    raised sqlite3.OperationalError: no such column: legacy_id.

    Fix: apply the four additive ALTERs here, exactly mirroring the pattern used
    by _migrate_tasks_stall_columns and _migrate_bitemporal_columns. Safe to run
    on an existing DB (columns already present → the branch is skipped).
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(project_registry)")}
    additive: list[tuple[str, str]] = [
        ("legacy_id", "TEXT"),
        ("include_prism", "INTEGER NOT NULL DEFAULT 0"),
        ("has_ledger", "INTEGER NOT NULL DEFAULT 0"),
        ("last_validated", "TIMESTAMP"),
    ]
    for col, decl in additive:
        if col not in existing:
            conn.execute(f"ALTER TABLE project_registry ADD COLUMN {col} {decl}")


def cmd_init(_args: argparse.Namespace) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Apply non-vec_memory tables first via plain conn (no extension needed)
    schema_lines = SCHEMA_PATH.read_text().splitlines()
    # Strip vec_memory DDL from schema — it requires the extension, applied separately
    plain_lines: list[str] = []
    skip = False
    for line in schema_lines:
        if "VIRTUAL TABLE" in line and "vec_memory" in line:
            skip = True
        if skip and line.strip().startswith(");"):
            skip = False
            continue
        if skip or ("idx_vec_memory" in line):
            continue
        plain_lines.append(line)
    with _conn() as conn:
        conn.executescript("\n".join(plain_lines))
        # Incident #10 concurrency fix: executescript() implicitly commits, so
        # the migration sequence below starts with NO transaction open. Every
        # _migrate_* function does a check-then-ALTER (PRAGMA table_info, then
        # ADD COLUMN if absent) — under concurrent `init` racers (or a racing
        # hot-path writer that also calls _migrate_bitemporal_columns) two
        # processes can both see a column absent and both ALTER, and the loser
        # gets 'duplicate column name' (reproduced by
        # .memory/tests/test_concurrent_write_wal.py). One BEGIN IMMEDIATE here
        # covers the WHOLE migration sequence so busy_timeout serializes
        # racers instead of letting them race every individual check.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        _migrate_tasks_stall_columns(conn)
        # OPT-054 — bi-temporal consolidation. Additive ALTER + backfill +
        # partial-unique index. Idempotent and re-runnable on the live DB.
        _migrate_bitemporal_columns(conn)
        # OPT-055 — additive, re-runnable on an existing project.db. Belt-and-
        # suspenders: schema.sql already declares both, but stamping them here
        # guarantees survival of the strip-loop and migrates pre-OPT-055 DBs.
        _ensure_outbox_table(conn)
        _ensure_provenance_table(conn)
        # S2-06 — apply migration-002 legacy registry columns. schema.sql omits
        # them; cmd_registry_list SELECTs them. Idempotent: skips existing cols.
        _migrate_registry_legacy_columns(conn)
        # Version-stamping — additive nexus_version on nexus_feedback + backfill
        # NULL legacy rows to 'unknown'. Idempotent, re-runnable, no data loss.
        _migrate_feedback_version_column(conn)
        # U1 instrumentation — three nullable columns on validation_log for
        # completeness-check coverage, revise attribution, and wall-clock timing.
        _migrate_validation_log_columns(conn)
        # R2-T15 — two nullable Art. XIII.d decompose-cue columns on
        # dispatch_telemetry. Idempotent, re-runnable, no data loss.
        _migrate_dispatch_telemetry_columns(conn)
        # Finding #6 (2026-07-12) — two nullable per-session cost-rollup
        # columns on sessions. Idempotent, re-runnable, no data loss.
        _migrate_sessions_telemetry_columns(conn)
        # DEC-098 — nullable domain column on tasks + nexus_feedback.
        # Idempotent, re-runnable, no data loss.
        _migrate_task_domain_columns(conn)
    # Apply M-001 vec_memory via extension-loaded conn. When sqlite-vec is
    # unavailable (degraded bootstrap / NEXUS_DISABLE_VEC / no extension support),
    # SKIP the vec0 virtual table and continue — core tables are already created
    # above, so persistence is structurally alive and `init` returns rc=0. The
    # vec0 table is rebuilt automatically by the next `init` once a venv exists.
    try:
        with _vec_conn() as vconn:
            _apply_M001(vconn)
    except VecUnavailable as exc:
        print(
            f"vec_memory deferred (sqlite-vec unavailable: {exc}) — semantic "
            "recall is off until .memory/.venv is built with sqlite-vec; core "
            "memory tables were created and `init` succeeded.",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory migration skipped (unexpected error): {exc}", file=sys.stderr)
    print(f"Initialized {DB_PATH}")


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------

def cmd_session_start(args: argparse.Namespace) -> None:
    now = _now()
    # Use date-based ID: S20260510-143000
    sid = "S" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")  # noqa: UP017
    # NATIVE-58: surface DB-open / write failures LOUDLY on stderr rather than
    # letting an unhandled exception propagate into `|| true` / `2>/dev/null`
    # at the call-site hook — which silently orphans the session with zero log rows.
    # The banner is the signal; exit 1 so the caller's rc is non-zero even when
    # it is suppressed via `|| true`.
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at, branch) VALUES (?, ?, ?)",
                (sid, now, getattr(args, "branch", "main") or "main"),
            )
    except Exception as _exc:  # noqa: BLE001
        sys.stderr.write(
            f"NEXUS MEMORY WRITE FAILURE: cannot write session row to {DB_PATH} — "
            f"{_exc.__class__.__name__}: {_exc}. "
            "Sessions will NOT be recorded. Check that .memory/ exists, is writable, "
            "and that project.db is initialized (run: python3 .memory/log.py init).\n"
        )
        sys.exit(1)
    print(json.dumps({"session_id": sid, "started_at": now}))


def _build_handoff_block(args: argparse.Namespace) -> dict | None:
    """R5-T04 (NATIVE-35) intent-preserving handoff block: the open experiment,
    its hypothesis, and the concrete next probe — the piece `next_step` alone
    loses across sessions. None (no block) unless the caller supplied at least
    one of the three fields; never fabricated from summary/next_step text.
    """
    experiment = getattr(args, "experiment", None)
    hypothesis = getattr(args, "hypothesis", None)
    next_probe = getattr(args, "next_probe", None)
    if not (experiment or hypothesis or next_probe):
        return None
    return {
        "experiment": experiment,
        "hypothesis": hypothesis,
        "next_probe": next_probe,
        "recorded_at": _now(),
    }


def cmd_session_end(args: argparse.Namespace) -> None:
    now = _now()
    handoff = _build_handoff_block(args)
    context_json = json.dumps(handoff) if handoff else None
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("No open session found.", file=sys.stderr)
            sys.exit(1)
        sid = row["id"]
        # Finding #6 (2026-07-12): per-session cost rollup — sum this
        # session's captured dispatch_telemetry rows. SUM() over zero rows
        # returns NULL (not 0), which is exactly the "no dispatches
        # captured this session" NULL the migration's docstring promises —
        # no COALESCE needed, no fabricated zero.
        totals = conn.execute(
            "SELECT SUM(tokens) AS tokens_total, SUM(duration_ms) AS duration_ms "
            "FROM dispatch_telemetry WHERE session_id=?",
            (sid,),
        ).fetchone()
        tokens_total = totals["tokens_total"] if totals else None
        duration_ms_total = totals["duration_ms"] if totals else None
        conn.execute(
            "UPDATE sessions SET ended_at=?, summary=?, next_step=?, context_json=?, "
            "tokens_total=?, duration_ms=? WHERE id=?",
            (now, args.summary, args.next_step, context_json, tokens_total, duration_ms_total, sid),
        )
    out = {
        "session_id": sid, "ended_at": now, "summary": args.summary,
        "tokens_total": tokens_total, "duration_ms": duration_ms_total,
    }
    if handoff is not None:
        out["handoff"] = handoff
    print(json.dumps(out))
    _write_session_state(args.summary, getattr(args, "next_step", None))


def _write_session_state(summary: str | None, next_step: str | None) -> None:
    """Write .memory/files/session_state.md from session end data. Silent on failure."""
    try:
        files_dir = MEMORY_FILES_DIR
        files_dir.mkdir(parents=True, exist_ok=True)
        summary_text = (summary or "").strip()[:1500]
        next_step_text = (next_step or "").strip()[:500]
        content = (
            "# Session State\n\n"
            f"**Last summary**: {summary_text}\n\n"
            f"**Next step**: {next_step_text}\n\n"
            "_Updated by `session end`. Max 300 words._\n"
        )
        (files_dir / "session_state.md").write_text(content, encoding="utf-8")
    except Exception:
        pass


def cmd_session_reap(args: argparse.Namespace) -> None:
    """Auto-close sessions stale > max_age_hours with placeholder summary.

    Default 2 hours. Closes the session with the LATEST context_log entry's
    timestamp as ended_at (falls back to started_at + 1 hour). Summary is
    placeholder so audit queries don't trip over NULL ended_at.
    """
    from datetime import datetime, timedelta, timezone
    max_age_hours = args.max_age_hours if args.max_age_hours is not None else 2
    threshold = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()  # noqa: UP017
    reaped = []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, started_at FROM sessions WHERE ended_at IS NULL AND started_at < ?",
            (threshold,),
        ).fetchall()
        for r in rows:
            sid = r["id"]
            last = conn.execute(
                "SELECT MAX(logged_at) AS t FROM context_log WHERE session_id=?",
                (sid,),
            ).fetchone()
            ended_at = (last["t"] if last and last["t"] else None) or _now()
            conn.execute(
                "UPDATE sessions SET ended_at=?, summary=?, next_step=? WHERE id=?",
                (
                    ended_at,
                    "Reaped — session abandoned without explicit end (auto-closed by `session reap`)",
                    "(unknown — set by reaper; check context_log for trail)",
                    sid,
                ),
            )
            reaped.append({"session_id": sid, "ended_at": ended_at})
    print(json.dumps({"reaped_count": len(reaped), "sessions": reaped}, indent=2))


def cmd_session_reset(args: argparse.Namespace) -> None:
    """End the current open session and immediately start a new one.

    Optionally writes a notepad entry on the handoff topic so the next
    session's orchestrator can read it via `notepad list --topic <topic>`.

    Returns JSON with both the closed session_id and the new session_id.
    """
    now = _now()
    with _conn() as conn:
        old_row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if old_row is None:
            print("No open session to reset.", file=sys.stderr)
            sys.exit(1)
        old_sid = old_row["id"]

        conn.execute(
            "UPDATE sessions SET ended_at=?, summary=?, next_step=?, last_reset_at=? WHERE id=?",
            (now, args.summary, "(context-reset handoff — see notepad)", now, old_sid),
        )

        new_sid = "S" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")  # noqa: UP017
        conn.execute(
            "INSERT INTO sessions (id, started_at, branch, user_message_count) VALUES (?, ?, ?, 0)",
            (new_sid, now, "main"),
        )

        if args.handoff_notepad_topic:
            conn.execute(
                """INSERT INTO agent_notepad (topic, agent_name, session_id, written_at, note, note_kind)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    args.handoff_notepad_topic,
                    "nexus",
                    new_sid,
                    now,
                    f"Context reset from {old_sid}. Summary: {args.summary[:300]}",
                    "reminder",
                ),
            )

    print(json.dumps({
        "closed_session_id": old_sid,
        "new_session_id": new_sid,
        "reset_at": now,
        "handoff_topic": args.handoff_notepad_topic or None,
    }))


def cmd_session_status(_args: argparse.Namespace) -> None:
    """Quick status: open sessions + counts of abandoned-stale candidates."""
    from datetime import datetime, timedelta, timezone
    threshold_2h = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()  # noqa: UP017
    with _conn() as conn:
        open_rows = conn.execute(
            "SELECT id, started_at FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC"
        ).fetchall()
        stale_count = conn.execute(
            "SELECT count(*) AS c FROM sessions WHERE ended_at IS NULL AND started_at < ?",
            (threshold_2h,),
        ).fetchone()["c"]
    print(json.dumps({
        "open_sessions": [dict(r) for r in open_rows],
        "stale_count_2h": stale_count,
    }, indent=2))


def _format_handoff_line(session_id: str, handoff: dict) -> str:
    parts = []
    if handoff.get("experiment"):
        parts.append(f"experiment: {handoff['experiment']}")
    if handoff.get("hypothesis"):
        parts.append(f"hypothesis: {handoff['hypothesis']}")
    if handoff.get("next_probe"):
        parts.append(f"next probe: {handoff['next_probe']}")
    body = " | ".join(parts) if parts else "(empty handoff)"
    return f"[{session_id}] {body}"


def cmd_session_recall(args: argparse.Namespace) -> None:
    """R5-T04 read-side memory recall (NATIVE-35/NATIVE-61): the last
    `--limit` sessions' summaries/next_steps, plus any intent-preserving
    handoff blocks (`_build_handoff_block`, written by `session end`) recorded
    among them. `handoffs` is emitted BEFORE `sessions` in the JSON output
    (and is ordered most-recent-first) so a fresh session reading this digest
    sees the still-open experiment intent FIRST -- the exact NATIVE-35 loss
    (experiment intent lost across sessions) this command exists to close.
    Bounded by `--limit`: never a full-history dump.
    """
    limit = max(1, getattr(args, "limit", None) or 5)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, started_at, ended_at, summary, next_step, branch, context_json "
            "FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    sessions = []
    handoffs = []
    for r in rows:
        sessions.append({
            "id": r["id"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "summary": r["summary"],
            "next_step": r["next_step"],
            "branch": r["branch"],
        })
        raw = r["context_json"]
        if not raw:
            continue
        try:
            handoff = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(handoff, dict):
            continue
        handoffs.append({
            "session_id": r["id"],
            "experiment": handoff.get("experiment"),
            "hypothesis": handoff.get("hypothesis"),
            "next_probe": handoff.get("next_probe"),
            "recorded_at": handoff.get("recorded_at"),
            "line": _format_handoff_line(r["id"], handoff),
        })

    print(json.dumps({"handoffs": handoffs, "sessions": sessions}, indent=2))


# ---------------------------------------------------------------------------
# task
# ---------------------------------------------------------------------------

# DEC-098: new tasks mint per-domain sequential ids. Grandfathered TASK-/NATIVE-
# ids are untouched — _next_id only matches the exact ^<PREFIX>-<n>$ pattern, so
# each domain counter is independent of the legacy namespaces.
DOMAIN_ID_PREFIX = {
    "nexus": "NEX",
    "plexus": "PLX",
    "kb": "KB",
    "ops": "OPS",
    "other": "OTH",
}


def cmd_task_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        _migrate_task_domain_columns(conn)
        tid = args.id or _next_id(conn, "tasks", DOMAIN_ID_PREFIX[args.domain])
        conn.execute(
            """INSERT OR REPLACE INTO tasks
               (id, feature_id, title, description, status, priority, assigned_to,
                acceptance_criteria, created_at, updated_at, notes, domain)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tid,
                args.feature_id,
                args.title,
                args.description,
                args.status or "todo",
                args.priority or "medium",
                args.assigned_to,
                args.acceptance_criteria,
                now,
                now,
                args.notes,
                args.domain,
            ),
        )
    print(json.dumps(
        {"task_id": tid, "status": args.status or "todo", "domain": args.domain}
    ))


def cmd_task_update(args: argparse.Namespace) -> None:
    now = _now()
    fields, vals = [], []
    for col in ("title", "status", "priority", "assigned_to", "notes", "worktree"):
        v = getattr(args, col, None)
        if v is not None:
            fields.append(f"{col}=?")
            vals.append(v)
    if not fields:
        print("Nothing to update.", file=sys.stderr)
        sys.exit(1)
    fields.append("updated_at=?")
    vals.append(now)
    if args.status == "done":
        fields.append("completed_at=?")
        vals.append(now)
    vals.append(args.id)
    with _conn() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", vals)
    print(json.dumps({"task_id": args.id, "updated_at": now}))


def cmd_task_list(args: argparse.Namespace) -> None:
    where, vals = [], []
    if args.status:
        where.append("status=?")
        vals.append(args.status)
    if args.feature_id:
        where.append("feature_id=?")
        vals.append(args.feature_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, feature_id, title, status, priority, assigned_to FROM tasks {clause} ORDER BY id",
            vals,
        ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# ---------------------------------------------------------------------------
# task migrate-domains — DEC-098 owner-ruled bulk triage migration
# ---------------------------------------------------------------------------
# Applies the 2026-07-18 owner disposition policy over the triage verdict file
# (.memory/files/task-triage-verdicts-20260718.json). Default is a dry run that
# prints per-disposition counts and writes NOTHING; --apply backs up the DB
# first, then executes in ONE transaction. Idempotent: rows already in their
# target state are skipped, so a rerun after success is a no-op.

_TRIAGE_JUNK_IDS = frozenset(
    f"NATIVE-12-{n}" for n in range(4, 11)  # NATIVE-12-4 … NATIVE-12-10
)
_TRIAGE_KEEP_IDS = frozenset({
    "NATIVE-9", "NATIVE-9-4", "NATIVE-10-5",
    "TASK-099", "TASK-100", "TASK-103", "TASK-104", "TASK-105",
    "TASK-106", "TASK-107", "TASK-108", "TASK-109", "TASK-110",
})
# Owner ruling: TASK-054 belongs to the kb park bucket even though the verdict
# file classifies its domain as plexus ("incl TASK-082 + TASK-054").
_TRIAGE_KB_EXTRA_IDS = frozenset({"TASK-054"})
# INCLUSIVE date cutoff: every unresolved row captured on or before this DATE
# is in triage scope (resolved unless named in still_valuable). The keep-list
# triage explicitly ruled on 07-17 rows, so same-date rows must not slip out
# of scope on a lexical `captured_at < date` comparison.
_TRIAGE_FEEDBACK_CUTOFF = "2026-07-17"
_TRIAGE_REVIEWER = "triage-wf_1866137c"

# Matches the explicit feedback ids cited in .feedback.still_valuable entries:
# "id 318", "ids 295, 296, 297", "ids 301/308", "ids 287-289".
_FEEDBACK_IDS_RE = re.compile(
    r"\bids?\s+(\d+(?:\s*-\s*\d+)?(?:\s*[,/]\s*\d+(?:\s*-\s*\d+)?)*)"
)


def _still_valuable_feedback_ids(entries) -> set:
    """Extract the integer nexus_feedback ids named in still_valuable entries.

    Conservative by construction: only numbers explicitly introduced by an
    "id"/"ids" token are matched, so prose numerics (dates, counts) never
    widen the keep-set — and anything matched is kept UNRESOLVED.
    """
    ids: set = set()
    for entry in entries or []:
        if not isinstance(entry, str):
            continue
        for m in _FEEDBACK_IDS_RE.finditer(entry):
            for chunk in re.split(r"[,/]", m.group(1)):
                chunk = chunk.strip()
                if "-" in chunk:
                    lo_s, hi_s = chunk.split("-", 1)
                    lo, hi = int(lo_s), int(hi_s)
                    if lo <= hi <= lo + 50:
                        ids.update(range(lo, hi + 1))
                elif chunk:
                    ids.add(int(chunk))
    return ids


def _triage_disposition(c: dict):
    """Map one verdict-file classification to (disposition, note, new_description).

    disposition in: delete | keep | completed | archived | archived_kb
    | pending_review | unhandled. Precedence mirrors the owner policy: junk
    deletion and the active keep-list outrank everything; TASK-001 and the kb
    park bucket outrank verdict-based rules; verdict rules run last.
    """
    tid = c.get("id")
    verdict = c.get("verdict")
    domain = c.get("domain")
    reason = (c.get("reason") or "").strip()
    if tid in _TRIAGE_JUNK_IDS:
        return ("delete", None, None)
    if tid in _TRIAGE_KEEP_IDS:
        new_desc = None
        if tid == "NATIVE-9-4" and "GAP-09" in reason:
            new_desc = reason[reason.find("GAP-09"):]
        return ("keep", None, new_desc)
    if tid == "TASK-001":
        return ("archived", "[triage: owner fresh-start ruling]", None)
    if domain == "kb" or tid in _TRIAGE_KB_EXTRA_IDS:
        return ("archived_kb", "parked for Zen Notes; " + reason, None)
    if verdict == "ALREADY-FIXED":
        return ("completed", f"[triage: {reason}]", None)
    if verdict in ("OBSOLETE-REDESIGN", "DUPLICATE"):
        return ("archived", reason, None)
    if verdict == "NON-NEXUS":
        return ("archived", f"[triage: non-nexus — {reason}]", None)
    if verdict == "STILL-APPLIES":
        return ("pending_review", None, None)
    return ("unhandled", None, None)


def _append_note(existing, note):
    """Append `note` to notes unless it is already present (idempotency)."""
    if not note:
        return existing, False
    current = existing or ""
    if note in current:
        return existing, False
    return (current + " " + note).strip() if current else note, True


def cmd_task_migrate_domains(args: argparse.Namespace) -> None:
    verdict_path = Path(args.verdicts)
    try:
        verdicts = json.loads(verdict_path.read_text())
    except (OSError, ValueError) as exc:
        print(f"Cannot read verdict file {verdict_path}: {exc}", file=sys.stderr)
        sys.exit(1)
    classifications = verdicts.get("classifications") or []
    keep_feedback_ids = _still_valuable_feedback_ids(
        (verdicts.get("feedback") or {}).get("still_valuable")
    )
    now = _now()

    with _conn() as conn:
        if args.apply:
            _migrate_task_domain_columns(conn)
            conn.commit()
        has_domain = _table_has_column(conn, "tasks", "domain")
        db_rows = {
            r["id"]: dict(r)
            for r in conn.execute(
                "SELECT id, status, notes, description"
                + (", domain" if has_domain else "")
                + " FROM tasks"
            )
        }

        status_target = {
            "completed": "completed",
            "archived": "archived",
            "archived_kb": "archived",
            "pending_review": "pending_review",
        }
        counts: dict = {
            "delete": 0, "keep": 0, "completed": 0, "archived": 0,
            "archived_kb": 0, "pending_review": 0, "unhandled": 0,
            "missing_from_db": 0, "already_applied": 0,
        }
        deletes: list = []
        updates: list = []  # (tid, new_status, domain, new_notes, new_desc, completed_at)
        for c in classifications:
            tid = c.get("id")
            row = db_rows.get(tid)
            disp, note, new_desc = _triage_disposition(c)
            if disp == "delete":
                counts["delete"] += 1
                if row is not None:
                    deletes.append(tid)
                else:
                    counts["already_applied"] += 1
                continue
            if row is None:
                counts["missing_from_db"] += 1
                continue
            counts[disp] += 1
            target = status_target.get(disp)
            domain = c.get("domain") if c.get("domain") in TASK_DOMAINS else None
            new_status = target if (target and row["status"] != target) else None
            new_domain = domain if domain and row.get("domain") != domain else None
            new_notes, notes_changed = _append_note(row.get("notes"), note)
            desc_changed = bool(new_desc) and (row.get("description") or "") != new_desc
            if not (new_status or new_domain or notes_changed or desc_changed):
                counts["already_applied"] += 1
                continue
            completed_at = now if target == "completed" else None
            updates.append((
                tid,
                new_status,
                new_domain,
                new_notes if notes_changed else None,
                new_desc if desc_changed else None,
                completed_at,
            ))

        keep_marks = ",".join("?" * len(keep_feedback_ids))
        feedback_where = (
            "substr(captured_at, 1, 10) <= ? AND resolved_at IS NULL"
            + (f" AND id NOT IN ({keep_marks})" if keep_feedback_ids else "")
        )
        feedback_params = [_TRIAGE_FEEDBACK_CUTOFF, *sorted(keep_feedback_ids)]
        feedback_to_resolve = 0
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nexus_feedback'"
        ).fetchone():
            feedback_to_resolve = conn.execute(
                f"SELECT count(*) AS c FROM nexus_feedback WHERE {feedback_where}",
                feedback_params,
            ).fetchone()["c"]

        def _status_domain_counts() -> dict:
            has_dom = _table_has_column(conn, "tasks", "domain")
            return {
                "status": {
                    r["status"]: r["c"]
                    for r in conn.execute(
                        "SELECT status, count(*) AS c FROM tasks GROUP BY status ORDER BY status"
                    )
                },
                "domain": {
                    (r["domain"] or "unclassified"): r["c"]
                    for r in conn.execute(
                        "SELECT domain, count(*) AS c FROM tasks GROUP BY domain ORDER BY domain"
                    )
                } if has_dom else {"unclassified": conn.execute(
                    "SELECT count(*) AS c FROM tasks").fetchone()["c"]},
            }

        before = _status_domain_counts()
        backup_path = None

        if args.apply:
            backup_dir = DB_PATH.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"pre-migration-{now[:10].replace('-', '')}.db"
            # First --apply run owns the true pre-state; a rerun must never
            # clobber it with an already-migrated copy.
            if not backup_path.exists():
                bconn = sqlite3.connect(backup_path)
                try:
                    conn.backup(bconn)
                finally:
                    bconn.close()
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            for tid in deletes:
                conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
            for tid, new_status, new_domain, new_notes, new_desc, completed_at in updates:
                sets = ["updated_at=?"]
                vals: list = [now]
                if new_status:
                    sets.append("status=?")
                    vals.append(new_status)
                if new_domain:
                    sets.append("domain=?")
                    vals.append(new_domain)
                if new_notes is not None:
                    sets.append("notes=?")
                    vals.append(new_notes)
                if new_desc is not None:
                    sets.append("description=?")
                    vals.append(new_desc)
                if completed_at:
                    sets.append("completed_at=COALESCE(completed_at, ?)")
                    vals.append(completed_at)
                vals.append(tid)
                conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
            if feedback_to_resolve:
                conn.execute(
                    f"UPDATE nexus_feedback SET resolved_at=?, reviewed_by=? WHERE {feedback_where}",
                    [now, _TRIAGE_REVIEWER, *feedback_params],
                )
            conn.commit()
            after = _status_domain_counts()
        else:
            # Project the after-state without writing anything.
            after = json.loads(json.dumps(before))
            for tid in deletes:
                st = db_rows[tid]["status"]
                after["status"][st] = after["status"].get(st, 0) - 1
                if after["status"][st] <= 0:
                    del after["status"][st]
                dom = (db_rows[tid].get("domain") or "unclassified")
                after["domain"][dom] = after["domain"].get(dom, 0) - 1
                if after["domain"][dom] <= 0:
                    del after["domain"][dom]
            for tid, new_status, new_domain, _n, _d, _c in updates:
                if new_status:
                    st = db_rows[tid]["status"]
                    after["status"][st] = after["status"].get(st, 0) - 1
                    if after["status"][st] <= 0:
                        del after["status"][st]
                    after["status"][new_status] = after["status"].get(new_status, 0) + 1
                if new_domain:
                    dom = (db_rows[tid].get("domain") or "unclassified")
                    after["domain"][dom] = after["domain"].get(dom, 0) - 1
                    if after["domain"][dom] <= 0:
                        del after["domain"][dom]
                    after["domain"][new_domain] = after["domain"].get(new_domain, 0) + 1

    out = {
        "dry_run": not args.apply,
        "classified_rows": len(classifications),
        "dispositions": counts,
        "rows_to_delete": sorted(deletes),
        "rows_to_update": len(updates),
        "tasks_before": before,
        "tasks_after": after,
        "feedback": {
            "resolved" if args.apply else "resolve_planned": feedback_to_resolve,
            "kept_open_ids": sorted(keep_feedback_ids),
            "cutoff": _TRIAGE_FEEDBACK_CUTOFF,
            "reviewed_by": _TRIAGE_REVIEWER,
        },
        "backup": str(backup_path) if backup_path else None,
    }
    print(json.dumps(out, indent=2))


# ---------------------------------------------------------------------------
# native-task mirror (#24 — durable cross-session task mirror)
# ---------------------------------------------------------------------------
# The *native* Claude Code task tools (TaskCreate / TaskUpdate) own a
# session-scoped list keyed by small integer ids ("1", "2", …). That list is
# visible but ephemeral — it does NOT survive into project.db, so cross-session
# continuity is lost (the live divergence: native list ~24 tasks vs project.db
# tasks 0 rows). The PostToolUse hook `.claude/hooks/task-db-mirror.sh` mirrors
# each native op into project.db; `task backfill-native` is the bulk/manual
# counterpart (and the recovery path if the hook was added after tasks already
# existed).
#
# CANONICAL CONVENTIONS (the hook MUST encode these identically):
#   • id namespace : native integer id N  ->  project.db id  "NATIVE-<N>"
#                    (keeps the mirror disjoint from hand-authored TASK-NNN ids)
#   • status map   : native {pending,in_progress,completed,deleted}
#                    ->  db    {todo,   in_progress, done,     cancelled}

# Single source of truth for the native->db status mapping (shared with the
# hook via this exact dict; any change here must be mirrored in task-db-mirror.sh).
NATIVE_STATUS_MAP = {
    "pending": "todo",
    "in_progress": "in_progress",
    "completed": "done",
    "deleted": "cancelled",
}


def native_task_db_id(native_id: str) -> str:
    """Map a native integer task id to its stable project.db id ("NATIVE-<N>").

    Idempotent: strips any leading NATIVE- prefix (case-insensitive, repeated)
    before prepending exactly one canonical uppercase NATIVE-.
    """
    raw = str(native_id).strip()
    # Strip any existing NATIVE- prefixes (case-insensitive, repeated).
    while re.match(r"(?i)^native-", raw):
        raw = re.sub(r"(?i)^native-", "", raw)
    return f"NATIVE-{raw}"


def _native_mirror_marker_present(notes: str | None, native_id: str) -> bool:
    """True iff `notes` carries THIS native_id's own "mirrored from native task
    #<N>" marker (NATIVE-13 / NATIVE-16).

    Mirrors `_foreign_collision()` in `.claude/hooks/_task_mirror.py:73` — kept
    in sync by convention, same as `native_task_db_id`'s own hook-side twin
    (`_native_task_db_id`). The hook's copy is a READ-ONLY pre-check that runs
    BEFORE either write path is attempted; this copy guards `_upsert_native_task`
    itself so a caller reaching the update branch directly (a bypassed hook, a
    diverged daemon handler, or `task mirror-native --op update` invoked by
    hand) still cannot blind-patch a hand-authored row.
    """
    notes = notes or ""
    escaped_id = re.escape(str(native_id).strip())
    marker = re.compile(rf"mirrored from native task #{escaped_id}(?!\d)")
    return marker.search(notes) is not None


def _next_surrogate_id(conn: sqlite3.Connection, base_db_id: str) -> str:
    """Return the next available surrogate id for a cross-session collision.

    Scans for rows matching "NATIVE-<N>-<k>" and returns the next unused suffix.
    Example: if NATIVE-1 and NATIVE-1-2 exist, returns "NATIVE-1-3".
    """
    pattern = f"{base_db_id}-%"
    rows = conn.execute(
        "SELECT id FROM tasks WHERE id LIKE ? OR id=?", (pattern, base_db_id)
    ).fetchall()
    existing_ids: set[str] = {r["id"] for r in rows}
    k = 2
    while True:
        candidate = f"{base_db_id}-{k}"
        if candidate not in existing_ids:
            return candidate
        k += 1


def _upsert_native_task(
    conn: sqlite3.Connection,
    native_id: str,
    *,
    subject: str | None = None,
    description: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    op: str = "update",
) -> dict:
    """Idempotent upsert of one native task into project.db tasks.

    `op` is "create" (TaskCreate) or "update" (TaskUpdate). On create we INSERT a
    full row; on update we patch only the supplied columns, preserving the
    existing title/created_at. Status is mapped via NATIVE_STATUS_MAP. Returns a
    small JSON-able summary so the hook (and backfill) can report what happened.

    Cross-session clobber guard (S2-04 / TASK-084): when op=="create" and an
    existing NATIVE-<N> row is a *different task* — a different non-empty title
    (and/or a different created_at) — this is a new-session reuse of the same
    native integer id, NOT an update to the same task. In that case we insert a
    fresh surrogate-id row (NATIVE-<N>-2, -3, …) and emit a stderr warning rather
    than silently overwriting the prior row.

    TASK-084 widened the guard beyond OPEN rows: with an EMPTY native panel the
    prior session's tasks are typically already DONE/cancelled, so a reused #1
    would land on a CLOSED NATIVE-1. Gating the guard on open-status (the
    original S2-04 condition) let that closed row be blind-overwritten — silent
    data loss. The discriminator is "different task" (title/created_at), not
    "still open". The happy path is preserved: a create or update that carries
    the SAME title re-mirrors the SAME native task and updates its row in place.
    """
    db_id = native_task_db_id(native_id)
    now = _now()
    db_status = NATIVE_STATUS_MAP.get((status or "").strip())

    existing = conn.execute(
        "SELECT id, title, status, created_at, notes FROM tasks WHERE id=?", (db_id,)
    ).fetchone()

    if existing is None:
        # First sighting — INSERT. A title is required by the schema; fall back to
        # a placeholder so an update-before-create (hook installed mid-session)
        # still lands a row that a later create/update can enrich.
        title = (subject or "").strip() or f"(native task {native_id})"
        completed_at = now if db_status == "done" else None
        conn.execute(
            """INSERT INTO tasks
               (id, title, description, status, priority, assigned_to,
                created_at, updated_at, completed_at, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                db_id,
                title,
                description,
                db_status or "todo",
                "medium",
                owner,
                now,
                now,
                completed_at,
                f"mirrored from native task #{native_id} (op={op})",
            ),
        )
        return {"task_id": db_id, "native_id": str(native_id), "action": "inserted",
                "status": db_status or "todo"}

    # --- Cross-session clobber guard (S2-04 / TASK-084) ---
    # A TaskCreate whose incoming subject differs from the stored title signals a
    # new session reusing the same native integer id for a DIFFERENT task. We must
    # NOT overwrite the prior row — insert a surrogate instead. TASK-084: this is
    # gated on "different task" (title differs), NOT on the prior row still being
    # open — an empty native panel reuses #N onto a typically CLOSED prior row, and
    # blind-overwriting a done/cancelled NATIVE-N is the same silent data loss.
    incoming_title = (subject or "").strip()
    existing_title = (existing["title"] or "").strip()
    is_cross_session_collision = (
        op == "create"
        and incoming_title != ""
        and incoming_title != existing_title
    )
    if is_cross_session_collision:
        surrogate_id = _next_surrogate_id(conn, db_id)
        completed_at = now if db_status == "done" else None
        conn.execute(
            """INSERT INTO tasks
               (id, title, description, status, priority, assigned_to,
                created_at, updated_at, completed_at, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                surrogate_id,
                incoming_title or f"(native task {native_id})",
                description,
                db_status or "todo",
                "medium",
                owner,
                now,
                now,
                completed_at,
                f"mirrored from native task #{native_id} (op={op}); "
                f"surrogate assigned — {db_id} owned by prior task '{existing_title}'",
            ),
        )
        sys.stderr.write(
            f"[task-mirror] WARNING S2-04/TASK-084: native #{native_id} collision — "
            f"existing row {db_id} ('{existing_title}') preserved; "
            f"new task inserted as {surrogate_id} ('{incoming_title}')\n"
        )
        return {
            "task_id": surrogate_id,
            "native_id": str(native_id),
            "action": "inserted_surrogate",
            "status": db_status or "todo",
            "collision_with": db_id,
        }

    # --- Foreign-row (non-mirror) ownership guard (NATIVE-16) ---
    # A row at this id that was never itself created by the mirror (no "mirrored
    # from native task #<N>" marker in its notes) is a hand-authored task — e.g.
    # the NATIVE-1..15 redesign family — that this UPDATE path must never
    # blind-patch. `.claude/hooks/_task_mirror.py`'s `_foreign_collision()`
    # already refuses this BEFORE calling here, but that is a pre-check on one
    # caller (task-db-mirror.sh); this guard closes the same hole for any path
    # that reaches `_upsert_native_task` directly (a bare `task mirror-native
    # --op update`, or a diverged daemon handler) — no-op + a structured refusal
    # result, never a silent overwrite.
    if not _native_mirror_marker_present(existing["notes"], native_id):
        sys.stderr.write(
            f"[task-mirror] REFUSED NATIVE-16: existing row {db_id} was not "
            f"created by the mirror (no marker for native #{native_id}); "
            f"refusing to patch it via task mirror-native --op {op}\n"
        )
        return {
            "task_id": db_id,
            "native_id": str(native_id),
            "action": "refused_foreign_row",
            "status": existing["status"],
        }

    # Existing row — patch only the columns we were given.
    fields: list[str] = []
    vals: list[object] = []

    def _set(col: str, value: object) -> None:
        fields.append(f"{col}=?")
        vals.append(value)

    if subject is not None and subject.strip():
        _set("title", subject.strip())
    if description is not None:
        _set("description", description)
    if owner is not None:
        _set("assigned_to", owner)
    if db_status is not None:
        _set("status", db_status)
        if db_status == "done":
            _set("completed_at", now)
    _set("updated_at", now)
    vals.append(db_id)
    conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", vals)
    return {"task_id": db_id, "native_id": str(native_id), "action": "updated",
            "status": db_status or existing["status"]}


def cmd_task_mirror_native(args: argparse.Namespace) -> None:
    """Mirror a SINGLE native task op into project.db (used by the hook).

    Called as:
        log.py task mirror-native --op create --native-id 7 \
            --subject "..." [--description ...] [--status in_progress] [--owner forge]
        log.py task mirror-native --op update --native-id 7 --status completed

    Pure mirror: never raises on a benign no-op. If neither a native id is known
    nor anything actionable was passed, it reports a skip rather than erroring,
    so the advisory hook can always exit 0.
    """
    native_id = (args.native_id or "").strip()
    if not native_id:
        print(json.dumps({"action": "skipped", "reason": "no_native_id"}))
        return
    with _conn() as conn:
        summary = _upsert_native_task(
            conn,
            native_id,
            subject=getattr(args, "subject", None),
            description=getattr(args, "description", None),
            status=getattr(args, "status", None),
            owner=getattr(args, "owner", None),
            op=getattr(args, "op", "update") or "update",
        )
    print(json.dumps(summary))


def cmd_task_backfill_native(args: argparse.Namespace) -> None:
    """Bulk-mirror native tasks into project.db from a JSON snapshot.

    Input (``--from FILE``, or stdin when omitted/``-``) is either a JSON array
    or JSONL, each item shaped like the native task list / TaskGet output:
        {"id": "1", "subject": "...", "status": "in_progress",
         "description": "...", "owner": "forge"}
    Field aliases accepted: id|taskId, subject|title, owner|assigned_to|assignedTo.

    Recovery/idempotent: re-running re-upserts the same NATIVE-<id> rows. Use
    this when the hook was added after tasks already existed, or to reconcile the
    full native list in one shot.
    """
    src = getattr(args, "from_file", None)
    try:
        raw = sys.stdin.read() if (not src or src == "-") else Path(src).read_text()
    except OSError as exc:
        print(json.dumps({"error": "read_failed", "reason": str(exc)}), file=sys.stderr)
        sys.exit(1)

    items = _parse_native_snapshot(raw)
    if not items:
        print(json.dumps({"mirrored": 0, "items": [], "note": "no parseable native tasks in input"}))
        return

    results: list[dict] = []
    with _conn() as conn:
        for it in items:
            nid = str(it.get("id", it.get("taskId", "")) or "").strip()
            if not nid:
                continue
            results.append(_upsert_native_task(
                conn,
                nid,
                subject=it.get("subject", it.get("title")),
                description=it.get("description"),
                status=it.get("status"),
                owner=it.get("owner", it.get("assigned_to", it.get("assignedTo"))),
                op="create",
            ))
    print(json.dumps({"mirrored": len(results), "items": results}, indent=2))


def _parse_native_snapshot(raw: str) -> list[dict]:
    """Best-effort parse of a native-task snapshot: JSON array OR JSONL."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            # Accept {"tasks":[...]} or a single task dict.
            inner = obj.get("tasks")
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
            return [obj]
    except json.JSONDecodeError:
        pass
    # JSONL fallback.
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
            if isinstance(o, dict):
                out.append(o)
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# stall increment (Phase F1 — compare-and-swap, concurrency-safe)
# ---------------------------------------------------------------------------

def cmd_stall_increment(args: argparse.Namespace) -> None:
    """Atomically increment stall_count for a task using compare-and-swap.

    Only increments when the current persona matches the previous one AND the
    marker is REVISE or BLOCKED. Two concurrent callers that read the same
    stall_count=N will race: only one UPDATE ... WHERE stall_count=N succeeds;
    the other retries once and either bumps to N+2 (genuine further stall) or
    no-ops (idempotent same-persona retry).

    Outputs JSON:
      {"task_id": "...", "stall_count": <new>, "action": "incremented|reset|noop"}
    """
    task_id = args.task_id
    persona = args.persona
    marker = args.marker.upper()

    if marker not in ("REVISE", "BLOCKED"):
        print(
            f"stall rejected: marker must be REVISE or BLOCKED (got {args.marker!r})",
            file=sys.stderr,
        )
        sys.exit(1)

    with _conn() as conn:
        _migrate_tasks_stall_columns(conn)

        row = conn.execute(
            "SELECT stall_count, last_persona FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            print(f"stall: task {task_id} not found", file=sys.stderr)
            sys.exit(1)

        current_count = row["stall_count"] or 0
        last_persona = row["last_persona"]

        # Reset counter if persona changed — a different agent is now working it.
        if last_persona and last_persona != persona:
            conn.execute(
                "UPDATE tasks SET stall_count=1, last_persona=?, updated_at=? WHERE id=?",
                (persona, _now(), task_id),
            )
            new_count = 1
            action = "reset"
        else:
            # Compare-and-swap: increment only if stall_count is still what we read.
            cur = conn.execute(
                "UPDATE tasks SET stall_count=?, last_persona=?, updated_at=? "
                "WHERE id=? AND stall_count=?",
                (current_count + 1, persona, _now(), task_id, current_count),
            )
            if cur.rowcount == 0:
                # Lost the race — re-read and report current value without modifying.
                row2 = conn.execute(
                    "SELECT stall_count FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
                new_count = row2["stall_count"] if row2 else current_count
                action = "noop"
            else:
                new_count = current_count + 1
                action = "incremented"

    print(json.dumps({
        "task_id": task_id,
        "stall_count": new_count,
        "persona": persona,
        "marker": marker,
        "action": action,
    }))


# ---------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------

def cmd_decision_add(args: argparse.Namespace) -> None:
    """Bi-temporal decision write (OPT-054 / TASK-035).

    Three outcomes, decided by comparing the FULL-payload content_hash (FORK-1)
    against the current row for the logical key:
      ADD       — no current row exists → insert one current row.
      NOOP      — a current row exists with an identical content_hash → do nothing
                  (no new row, no re-embed). An identical re-write is idempotent.
      SUPERSEDE — a current row exists with a DIFFERENT content_hash → close the
                  old row (valid_to, superseded_by, status='superseded', id
                  re-suffixed) and insert a new current row that supersedes it.

    The embed-outbox marker is enqueued in the SAME relational txn as the write
    (OPT-055 A) for ADD and SUPERSEDE; NOOP enqueues nothing.
    """
    now = _now()
    status = (getattr(args, "status", None) or "accepted")
    alternatives = getattr(args, "alternatives", None)
    consequences = getattr(args, "consequences", None)
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        did = args.id or _next_id(conn, "decisions", "DEC")
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None

        payload = {
            "title": args.title,
            "status": status,
            "context": args.context,
            "decision": args.decision,
            "rationale": args.rationale,
            "alternatives": alternatives,
            "consequences": consequences,
        }
        new_hash = _content_hash("decisions", payload)

        current = _current_row(conn, "decisions", did)
        if current is not None and current["content_hash"] == new_hash:
            # NOOP — identical full payload. No new row, no re-embed.
            print(json.dumps({"decision_id": did, "decided_at": current["decided_at"], "noop": True}))
            return

        supersedes_id: str | None = None
        if current is not None:
            # SUPERSEDE — close the old row, free the bare id for the new current row.
            supersedes_id = _close_and_suffix_old_row(conn, "decisions", did, did, now)

        conn.execute(
            """INSERT INTO decisions
               (id, title, status, context, decision, rationale, alternatives, consequences,
                decided_at, session_id, valid_from, valid_to, superseded_by, supersedes,
                content_hash, is_tombstone)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                did,
                args.title,
                status,
                args.context,
                args.decision,
                args.rationale,
                alternatives,
                consequences,
                now,
                session_id,
                now,            # valid_from
                None,           # valid_to (current)
                None,           # superseded_by
                supersedes_id,  # supersedes (NULL on a plain ADD)
                new_hash,
                0,              # is_tombstone
            ),
        )
        # OPT-055 A — enqueue intent-to-embed in the SAME relational txn as the
        # source INSERT so source-row + marker land atomically. The marker is
        # cleared by _vec_insert in the SAME vec txn as the vec write (GUARDRAIL #1).
        decision_blob = (
            f"context: {args.context}\ndecision: {args.decision}\nrationale: {args.rationale}"
        )
        _outbox_enqueue(conn, "decision", did, decision_blob)
    print(json.dumps({"decision_id": did, "decided_at": now}))
    # Embed side-effect — degrades gracefully if LM Studio unavailable
    try:
        text_blob = f"context: {args.context}\ndecision: {args.decision}\nrationale: {args.rationale}"
        with _vec_conn() as vconn:
            _vec_insert(vconn, "decision", did, text_blob, now)
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory: embed side-effect skipped for {did}: {exc}", file=sys.stderr)


def cmd_decision_retire(args: argparse.Namespace) -> None:
    """Tombstone a decision (OPT-054). Close the current row (valid_to set,
    status='superseded') and write a tombstone marker row (is_tombstone=1,
    valid_to IS NULL) so current-only recall hides the logical key while the
    full history chain stays intact and walkable via `decision list --history`.
    """
    now = _now()
    did = args.id
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        current = _current_row(conn, "decisions", did)
        if current is None:
            print(f"No current decision found with id {did}", file=sys.stderr)
            sys.exit(1)
        old = dict(current)
        # Tombstone row reuses the bare id and is current (valid_to IS NULL) but
        # flagged is_tombstone=1; close + re-suffix the prior content row first.
        _close_and_suffix_old_row(conn, "decisions", did, did, now)
        conn.execute(
            """INSERT INTO decisions
               (id, title, status, context, decision, rationale, alternatives, consequences,
                decided_at, session_id, valid_from, valid_to, superseded_by, supersedes,
                content_hash, is_tombstone)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                did,
                old.get("title"),
                "superseded",
                old.get("context"),
                old.get("decision"),
                old.get("rationale"),
                old.get("alternatives"),
                old.get("consequences"),
                now,
                old.get("session_id"),
                now,                       # valid_from
                None,                      # valid_to (current tombstone)
                None,                      # superseded_by
                f"{did}@{now}",            # supersedes (the row just closed)
                old.get("content_hash"),
                1,                         # is_tombstone
            ),
        )
    print(json.dumps({"decision_id": did, "retired_at": now, "tombstoned": True}))


def cmd_decision_list(args: argparse.Namespace) -> None:
    """List decisions. Default is current-only (valid_to IS NULL, not a tombstone);
    --history walks the full bi-temporal chain (every version of every key)."""
    history = bool(getattr(args, "history", False))
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        if history:
            rows = conn.execute(
                "SELECT id, title, status, decision, decided_at, valid_from, valid_to, "
                "superseded_by, supersedes, is_tombstone "
                "FROM decisions ORDER BY valid_from, id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, status, decision, decided_at FROM decisions "
                "WHERE valid_to IS NULL AND is_tombstone=0 ORDER BY id"
            ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# ---------------------------------------------------------------------------
# registry (PLEXUS — project_registry + project_version_history)
# ---------------------------------------------------------------------------

def _registry_history_action(action: str) -> str:
    """Map a `registry add`/`update` action to the history-table action enum.

    project_registry.install_method ∈ {fresh, existing, manual}
    project_version_history.action  ∈ {installed, installed-existing, updated,
                                        removed, rolled-back}
    """
    return {
        "fresh": "installed",
        "existing": "installed-existing",
        "manual": "installed",
        "installed": "installed",
        "installed-existing": "installed-existing",
        "updated": "updated",
        "removed": "removed",
        "rolled-back": "rolled-back",
    }.get(action, action)


def _detect_ledger_state(project_path: str) -> bool:
    """Root-cause fix for NATIVE-2-17 / NATIVE-1-19: whether project_path has an
    initialized, readable ledger (.memory/nexus-stack.json parses as a non-empty
    dict). `registry update` previously never touched has_ledger/last_validated
    at all, so once a project's ledger went stale (e.g. the gemini-gateway
    install's stale nexus-stack.json) the registry row had no mechanism to ever
    reflect reality — has_ledger stayed frozen at its initial value and
    last_validated stayed NULL forever, no matter how many updates ran.
    Re-detecting from disk on every `registry update` call self-heals that:
    the row always reflects the CURRENT on-disk ledger state.
    """
    stack_json = Path(project_path) / ".memory" / "nexus-stack.json"
    try:
        with stack_json.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data)


def _normalize_registry_path(project_path: str) -> str:
    """Dedup identity for project_registry.project_path (R5-T09 half 2).

    /Foo/Bar and /foo/bar/ previously produced TWO rows because `registry add`
    only upserts on an exact string match -- the UNIQUE constraint on
    project_path never catches a case/trailing-slash variant. Collapsing to
    lowercase with no trailing slash gives the identity used both by the
    add-time duplicate guard (cmd_registry_add) and the one-time migration
    pass (cmd_registry_dedup).
    """
    return project_path.rstrip("/").lower()


def cmd_registry_add(args: argparse.Namespace) -> None:
    """Register a new managed project. INSERT OR REPLACE on project_path.

    Ledger-init fix (NATIVE-1-19, R5-T12): `registry add` previously never
    inspected the on-disk ledger at all, so has_ledger stayed frozen at the
    column default (0) even for `installed-existing` projects that already
    carry a valid .memory/nexus-stack.json at the exact moment they're
    registered (the gemini-gateway class: a pre-existing install being
    brought under registry management). The row then sat wrong until
    whatever future `registry update` call happened to come along, which
    could be arbitrarily delayed or never happen. Root cause was that ledger
    detection only ran in `registry update` (see _detect_ledger_state);
    `registry add` is the actual ledger INIT point and needs the same
    detection, not just update. last_validated is only stamped when a ledger
    is actually found at add-time (matching "we just validated one"); a
    brand-new project with no ledger yet still starts at last_validated=NULL,
    same as before this fix -- `registry update`'s unconditional stamp (which
    "never gets stuck", NATIVE-2-17) is the one that guarantees convergence
    over time.
    """
    now = _now()
    # add subcommand --action is one of {installed, installed-existing, manual}.
    # Map to install_method column (fresh|existing|manual).
    install_method = {
        "installed": "fresh",
        "installed-existing": "existing",
        "manual": "manual",
    }.get(args.action, args.action)
    if install_method not in {"fresh", "existing", "manual"}:
        print(f"Invalid action: {args.action}", file=sys.stderr)
        sys.exit(2)
    has_ledger = 1 if _detect_ledger_state(args.project_path) else 0
    last_validated = now if has_ledger else None
    merge = bool(getattr(args, "merge", False))
    with _conn() as conn:
        # Upsert: if path already registered, update version + last_updated_at
        # and reactivate; otherwise insert fresh.
        existing = conn.execute(
            "SELECT id, project_path FROM project_registry WHERE project_path=?",
            (args.project_path,),
        ).fetchone()
        canonical_path = args.project_path
        if existing is None:
            # R5-T09 half 2: a path differing from an existing row ONLY by
            # case or a trailing slash is the same project (the dup-row class
            # that produced double entries) -- reject it unless the caller
            # explicitly passes --merge to fold it into the existing row.
            dup_key = _normalize_registry_path(args.project_path)
            dup_row = next(
                (
                    row for row in conn.execute(
                        "SELECT id, project_path FROM project_registry"
                    ).fetchall()
                    if _normalize_registry_path(row["project_path"]) == dup_key
                ),
                None,
            )
            if dup_row is not None:
                if not merge:
                    print(
                        f"registry add rejected: '{args.project_path}' differs only by "
                        f"case/trailing-slash from existing row id={dup_row['id']} "
                        f"path='{dup_row['project_path']}'. Pass --merge to fold into it.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                existing = dup_row
                canonical_path = dup_row["project_path"]
        if existing:
            conn.execute(
                "UPDATE project_registry SET current_version=?, install_method=?, "
                "last_updated_at=?, status='active', notes=COALESCE(?, notes), "
                "has_ledger=?, last_validated=? "
                "WHERE id=?",
                (args.version, install_method, now, args.notes,
                 has_ledger, last_validated, existing["id"]),
            )
            pid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO project_registry "
                "(project_path, current_version, install_method, status, notes, "
                " installed_at, last_updated_at, has_ledger, last_validated) "
                "VALUES (?,?,?, 'active', ?, ?, ?, ?, ?)",
                (args.project_path, args.version, install_method,
                 args.notes, now, now, has_ledger, last_validated),
            )
            pid = cur.lastrowid
        conn.execute(
            "INSERT INTO project_version_history "
            "(project_path, version, action, acted_at, notes) "
            "VALUES (?,?,?,?,?)",
            (canonical_path, args.version,
             _registry_history_action(args.action), now, args.notes),
        )
    print(json.dumps({
        "id": pid,
        "project_path": canonical_path,
        "current_version": args.version,
        "install_method": install_method,
        "action": args.action,
        "acted_at": now,
        "has_ledger": has_ledger,
        "last_validated": last_validated,
    }))


def cmd_registry_update(args: argparse.Namespace) -> None:
    """Update version + record history for an already-registered project.

    NATIVE-2-17 fix: this previously left has_ledger/last_validated untouched
    forever (they only exist via the migration-002 additive ALTER — see
    _migrate_registry_legacy_columns — and nothing ever wrote them). Every
    `registry update` now re-stamps last_validated=now() and recomputes
    has_ledger from the on-disk ledger unless explicitly overridden via
    --has-ledger, so a project's registry row can never go silently stale
    relative to its actual ledger state (the NATIVE-1-19 gemini-gateway class
    of defect: has_ledger stuck at its initial value while the real ledger
    drifted underneath it).
    """
    now = _now()
    has_ledger_arg = getattr(args, "has_ledger", "auto")
    if has_ledger_arg == "auto":
        has_ledger = 1 if _detect_ledger_state(args.project_path) else 0
    else:
        has_ledger = 1 if has_ledger_arg == "yes" else 0
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, current_version FROM project_registry WHERE project_path=?",
            (args.project_path,),
        ).fetchone()
        if row is None:
            print(
                f"No registry entry for {args.project_path}. "
                "Use `registry add` first.",
                file=sys.stderr,
            )
            sys.exit(1)
        previous = row["current_version"]
        conn.execute(
            "UPDATE project_registry SET current_version=?, last_updated_at=?, "
            "notes=COALESCE(?, notes), has_ledger=?, last_validated=? "
            "WHERE project_path=?",
            (args.version, now, args.notes, has_ledger, now, args.project_path),
        )
        conn.execute(
            "INSERT INTO project_version_history "
            "(project_path, version, action, acted_at, notes) "
            "VALUES (?,?,?,?,?)",
            (args.project_path, args.version,
             _registry_history_action(args.action), now, args.notes),
        )
    print(json.dumps({
        "id": row["id"],
        "project_path": args.project_path,
        "previous_version": previous,
        "current_version": args.version,
        "action": args.action,
        "acted_at": now,
        "has_ledger": has_ledger,
        "last_validated": now,
    }))


def cmd_registry_list(args: argparse.Namespace) -> None:
    """List registry entries (all, or filtered to a single project_path)."""
    with _conn() as conn:
        if getattr(args, "project_path", None):
            rows = conn.execute(
                "SELECT id, project_path, current_version, install_method, "
                "status, installed_at, last_updated_at, legacy_id, "
                "include_prism, has_ledger, last_validated, notes "
                "FROM project_registry WHERE project_path=? ORDER BY id",
                (args.project_path,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project_path, current_version, install_method, "
                "status, installed_at, last_updated_at, legacy_id, "
                "include_prism, has_ledger, last_validated, notes "
                "FROM project_registry ORDER BY id"
            ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2, default=str))


def cmd_registry_remove(args: argparse.Namespace) -> None:
    """Soft-remove: status='removed', and record a history row."""
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, current_version FROM project_registry WHERE project_path=?",
            (args.project_path,),
        ).fetchone()
        if row is None:
            print(f"No registry entry for {args.project_path}.", file=sys.stderr)
            sys.exit(1)
        conn.execute(
            "UPDATE project_registry SET status='removed', last_updated_at=?, "
            "notes=COALESCE(?, notes) WHERE project_path=?",
            (now, args.notes, args.project_path),
        )
        conn.execute(
            "INSERT INTO project_version_history "
            "(project_path, version, action, acted_at, notes) "
            "VALUES (?,?,?,?,?)",
            (args.project_path, row["current_version"], "removed", now, args.notes),
        )
    print(json.dumps({
        "id": row["id"],
        "project_path": args.project_path,
        "status": "removed",
        "acted_at": now,
    }))


def cmd_registry_dedup(args: argparse.Namespace) -> None:
    """One-time migration pass (R5-T09 half 2): fold project_registry rows
    that differ only by case/trailing-slash into a single canonical row --
    the dup-row class the add-time guard in cmd_registry_add now prevents
    going forward, but pre-existing rows need a one-time cleanup.

    Canonical = the group's lowest id (first-registered path string is kept
    on disk/history). The GROUP MEMBER most recently touched (max
    last_updated_at) donates its current_version/notes/has_ledger/
    last_validated to the canonical row -- the freshest write wins, matching
    the self-heal posture NATIVE-2-17 established for `registry update`.
    project_version_history rows for every folded path are re-pointed at the
    canonical project_path so history stays queryable under one identity.

    Dry-run by default (report only, no writes); pass --apply to commit.
    """
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, project_path, current_version, install_method, status, "
            "notes, has_ledger, last_validated, last_updated_at "
            "FROM project_registry ORDER BY id"
        ).fetchall()
        groups: dict = {}
        for row in rows:
            groups.setdefault(_normalize_registry_path(row["project_path"]), []).append(row)
        dup_groups = [members for members in groups.values() if len(members) > 1]

        report_groups = []
        for members in dup_groups:
            canonical = members[0]
            duplicates = members[1:]
            most_recent = max(members, key=lambda r: r["last_updated_at"] or "")
            report_groups.append({
                "canonical": {"id": canonical["id"], "project_path": canonical["project_path"]},
                "merged": [
                    {"id": d["id"], "project_path": d["project_path"]} for d in duplicates
                ],
                "winning_version": most_recent["current_version"],
            })
            if args.apply:
                for d in duplicates:
                    conn.execute(
                        "UPDATE project_version_history SET project_path=? WHERE project_path=?",
                        (canonical["project_path"], d["project_path"]),
                    )
                conn.execute(
                    "UPDATE project_registry SET current_version=?, notes=COALESCE(?, notes), "
                    "has_ledger=?, last_validated=?, last_updated_at=? WHERE id=?",
                    (most_recent["current_version"], most_recent["notes"],
                     most_recent["has_ledger"], most_recent["last_validated"],
                     most_recent["last_updated_at"], canonical["id"]),
                )
                for d in duplicates:
                    conn.execute("DELETE FROM project_registry WHERE id=?", (d["id"],))
        result = {
            "mode": "apply" if args.apply else "dry-run",
            "duplicate_groups": len(dup_groups),
            "merged_rows": sum(len(g["merged"]) for g in report_groups),
            "groups": report_groups,
        }
    print(json.dumps(result, indent=2, default=str))


# ---------------------------------------------------------------------------
# Nexus self-feedback — DEC-019 (self-feedback MVP)
# ---------------------------------------------------------------------------
# `feedback add` writes a per-project friction row into nexus_feedback (every
# install has the table — it ships in schema.sql). `feedback harvest` is the
# Plexus-only aggregator: it walks project_registry, opens each project's
# .memory/project.db, dedups nexus_feedback rows by (category, sha256(message)),
# and aggregates frequencies into the Plexus-only improvement_backlog table —
# which is CREATE-TABLE-IF-NOT-EXISTS *here* (never in schema.sql) so it never
# ships to installs and the R4c schema-identity parity stays green.

_FEEDBACK_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_FEEDBACK_SOURCES = {"tool", "hook"}
_FEEDBACK_CATEGORIES = {
    "gate_deny",
    "gate_needs_decision",
    "gate_revise_stall",
    "unclear_persona",
    "unclear_skill",
    "missing_context",
    "roster_mismatch",
    "workflow_friction",
    "other",
}

# Severity rank for backlog prioritization (higher rank == higher priority).
_FEEDBACK_SEVERITY_RANK = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}


def _feedback_priority(max_severity: str, frequency: int) -> str:
    """Map (worst severity seen, dedup frequency) -> a coarse backlog priority."""
    rank = _FEEDBACK_SEVERITY_RANK.get(max_severity, 1)
    if rank >= 5 or (rank >= 4 and frequency >= 3):
        return "P1"
    if rank >= 4 or frequency >= 3:
        return "P2"
    return "P3"


def cmd_feedback_add(args: argparse.Namespace) -> None:
    """Record one Nexus-friction row into the per-project nexus_feedback table."""
    source = (args.source or "").strip().lower()
    severity = (args.severity or "").strip().lower()
    category = (args.category or "").strip().lower()
    message = (args.message or "").strip()

    if source not in _FEEDBACK_SOURCES:
        print(
            f"feedback rejected: invalid source '{args.source}'. "
            f"Allowed: {', '.join(sorted(_FEEDBACK_SOURCES))}",
            file=sys.stderr,
        )
        sys.exit(1)
    if severity not in _FEEDBACK_SEVERITIES:
        print(
            f"feedback rejected: invalid severity '{args.severity}'. "
            f"Allowed: {', '.join(sorted(_FEEDBACK_SEVERITIES))}",
            file=sys.stderr,
        )
        sys.exit(1)
    if category not in _FEEDBACK_CATEGORIES:
        print(
            f"feedback rejected: invalid category '{args.category}'. "
            f"Allowed: {', '.join(sorted(_FEEDBACK_CATEGORIES))}",
            file=sys.stderr,
        )
        sys.exit(1)
    if not message:
        print("feedback rejected: --message must be a non-empty string", file=sys.stderr)
        sys.exit(1)

    context_json = getattr(args, "context_json", None)
    if context_json:
        try:
            json.loads(context_json)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"feedback rejected: --context-json is not valid JSON: {exc}", file=sys.stderr)
            sys.exit(1)

    now = _now()
    # Caller may pass an explicit --nexus-version (broker reads .nexus-version
    # before shelling out); otherwise derive it from the project's .nexus-version.
    # NEVER required from the caller — falls back to the fail-soft helper.
    nexus_version = (getattr(args, "nexus_version", None) or "").strip() or _installed_nexus_version()
    with _conn() as conn:
        _migrate_feedback_version_column(conn)
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None
        cur = conn.execute(
            """INSERT INTO nexus_feedback
                   (session_id, source, severity, category, message,
                    context_json, source_file, captured_at, nexus_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                source,
                severity,
                category,
                message,
                context_json,
                getattr(args, "source_file", None),
                now,
                nexus_version,
            ),
        )
        inserted_id = cur.lastrowid
    print(json.dumps({"id": inserted_id, "captured_at": now, "nexus_version": nexus_version}))


def _ensure_improvement_backlog(conn: sqlite3.Connection) -> None:
    """Create the Plexus-only improvement_backlog table on demand.

    This table is INTENTIONALLY absent from schema.sql: it is a Plexus-side
    aggregation surface, not a per-project table. Creating it here (and only on
    harvest) keeps it out of every install and preserves R4c schema identity
    between the live and packaged schema.sql.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS improvement_backlog (
               id                  INTEGER PRIMARY KEY AUTOINCREMENT,
               harvest_session_id  TEXT,
               source_project_path TEXT NOT NULL,
               category            TEXT NOT NULL,
               dedup_hash          TEXT NOT NULL,
               frequency           INTEGER NOT NULL DEFAULT 1,
               sample_message      TEXT NOT NULL,
               max_severity        TEXT,
               priority            TEXT,
               reviewed_by         TEXT,
               min_nexus_version   TEXT,
               max_nexus_version   TEXT,
               harvested_at        TEXT NOT NULL,
               UNIQUE(source_project_path, category, dedup_hash)
           )"""
    )
    # Idempotent additive migration for pre-existing Plexus backlog tables that
    # predate version-stamping (CREATE IF NOT EXISTS above won't add columns to an
    # already-present table). Re-runnable: skips columns that already exist.
    existing = {r[1] for r in conn.execute("PRAGMA table_info(improvement_backlog)")}
    if "min_nexus_version" not in existing:
        conn.execute("ALTER TABLE improvement_backlog ADD COLUMN min_nexus_version TEXT")
    if "max_nexus_version" not in existing:
        conn.execute("ALTER TABLE improvement_backlog ADD COLUMN max_nexus_version TEXT")


def cmd_feedback_harvest(args: argparse.Namespace) -> None:
    """Plexus harvest: aggregate per-project nexus_feedback into improvement_backlog.

    Walks project_registry, opens each active project's .memory/project.db,
    reads its nexus_feedback rows, dedups by (category, sha256(message)) within
    a project, and upserts an aggregated row (with frequency + worst severity)
    into the Plexus-only improvement_backlog. Emits a JSON summary (or markdown
    with --md).
    """
    now = _now()
    harvest_session_id: str | None = None
    with _conn() as conn:
        srow = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        harvest_session_id = srow["id"] if srow else None
        rows = conn.execute(
            "SELECT project_path FROM project_registry "
            "WHERE status != 'removed' ORDER BY project_path"
        ).fetchall()
        projects = [r["project_path"] for r in rows]

    self_root = str(Path(__file__).resolve().parent.parent)
    # Test-isolation seam (mirrors the _NEXUS_HOOK_SKIP_DISCOVERY / _NEXUS_*
    # seam convention): _NEXUS_HARVEST_SKIP_SELF lets a hermetic test scope the
    # harvest to ONLY its registered temp projects, so the live Plexus repo's own
    # nexus_feedback rows do not leak into exact-count assertions. PRODUCTION
    # never sets the var, so self_root is harvested normally there.
    if not os.environ.get("_NEXUS_HARVEST_SKIP_SELF") and self_root not in projects:
        # The Plexus repo harvests its OWN feedback too even if it is not a
        # registered project (it is the meta-orchestrator, not an install).
        projects.append(self_root)

    # aggregates keyed by (project_path, category, dedup_hash)
    aggregates: dict[tuple[str, str, str], dict[str, object]] = {}
    scanned_projects = 0
    total_rows = 0
    skipped: list[str] = []

    for ppath in projects:
        pdb = Path(ppath) / ".memory" / "project.db"
        if not pdb.is_file():
            skipped.append(ppath)
            continue
        try:
            pconn = sqlite3.connect(f"file:{pdb}?mode=ro", uri=True)
        except sqlite3.Error:
            skipped.append(ppath)
            continue
        try:
            pconn.row_factory = sqlite3.Row
            has_table = pconn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='nexus_feedback'"
            ).fetchone()
            if not has_table:
                skipped.append(ppath)
                continue
            scanned_projects += 1
            # nexus_version may be absent on a pre-migration source DB; SELECT it
            # defensively so legacy projects still harvest (treated as 'unknown').
            has_version_col = any(
                r[1] == "nexus_version"
                for r in pconn.execute("PRAGMA table_info(nexus_feedback)")
            )
            version_select = "nexus_version" if has_version_col else "'unknown' AS nexus_version"
            for fr in pconn.execute(
                f"SELECT severity, category, message, {version_select} FROM nexus_feedback "
                "WHERE resolved_at IS NULL"
            ):
                total_rows += 1
                message = fr["message"] or ""
                category = fr["category"] or "other"
                severity = (fr["severity"] or "info").lower()
                version = fr["nexus_version"] or "unknown"
                dedup_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
                key = (ppath, category, dedup_hash)
                agg = aggregates.get(key)
                if agg is None:
                    aggregates[key] = {
                        "project_path": ppath,
                        "category": category,
                        "dedup_hash": dedup_hash,
                        "frequency": 1,
                        "sample_message": message,
                        "max_severity": severity,
                        "min_nexus_version": version,
                        "max_nexus_version": version,
                    }
                else:
                    agg["frequency"] = int(agg["frequency"]) + 1  # type: ignore[arg-type]
                    if _FEEDBACK_SEVERITY_RANK.get(severity, 1) > _FEEDBACK_SEVERITY_RANK.get(
                        str(agg["max_severity"]), 1
                    ):
                        agg["max_severity"] = severity
                    if _version_tuple(version) < _version_tuple(str(agg["min_nexus_version"])):
                        agg["min_nexus_version"] = version
                    if _version_tuple(version) > _version_tuple(str(agg["max_nexus_version"])):
                        agg["max_nexus_version"] = version
        finally:
            pconn.close()

    if not getattr(args, "dry_run", False):
        with _conn() as conn:
            _ensure_improvement_backlog(conn)
            for agg in aggregates.values():
                frequency = int(agg["frequency"])  # type: ignore[arg-type]
                max_severity = str(agg["max_severity"])
                priority = _feedback_priority(max_severity, frequency)
                conn.execute(
                    """INSERT INTO improvement_backlog
                           (harvest_session_id, source_project_path, category, dedup_hash,
                            frequency, sample_message, max_severity, priority,
                            min_nexus_version, max_nexus_version, harvested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_project_path, category, dedup_hash) DO UPDATE SET
                           frequency      = excluded.frequency,
                           sample_message = excluded.sample_message,
                           max_severity   = excluded.max_severity,
                           priority       = excluded.priority,
                           min_nexus_version = excluded.min_nexus_version,
                           max_nexus_version = excluded.max_nexus_version,
                           harvested_at   = excluded.harvested_at,
                           harvest_session_id = excluded.harvest_session_id""",
                    (
                        harvest_session_id,
                        agg["project_path"],
                        agg["category"],
                        agg["dedup_hash"],
                        frequency,
                        agg["sample_message"],
                        max_severity,
                        priority,
                        str(agg["min_nexus_version"]),
                        str(agg["max_nexus_version"]),
                        now,
                    ),
                )

    summary = {
        "harvested_at": now,
        "dry_run": bool(getattr(args, "dry_run", False)),
        "projects_scanned": scanned_projects,
        "projects_skipped": len(skipped),
        "feedback_rows": total_rows,
        "backlog_items": len(aggregates),
        "items": sorted(
            (
                {
                    "project_path": str(a["project_path"]),
                    "category": str(a["category"]),
                    "frequency": int(a["frequency"]),  # type: ignore[arg-type]
                    "max_severity": str(a["max_severity"]),
                    "priority": _feedback_priority(
                        str(a["max_severity"]), int(a["frequency"])  # type: ignore[arg-type]
                    ),
                    "min_nexus_version": str(a["min_nexus_version"]),
                    "max_nexus_version": str(a["max_nexus_version"]),
                    "sample_message": str(a["sample_message"]),
                }
                for a in aggregates.values()
            ),
            key=lambda x: (-int(x["frequency"]), str(x["category"])),
        ),
    }

    if getattr(args, "md", False):
        lines = [
            f"# Nexus improvement backlog — harvested {now}",
            "",
            f"- projects scanned: {scanned_projects} (skipped {len(skipped)})",
            f"- feedback rows: {total_rows} -> {len(aggregates)} deduped backlog items",
            "",
            "| priority | freq | severity | category | sample |",
            "|---|---|---|---|---|",
        ]
        for it in summary["items"]:  # type: ignore[union-attr]
            sample = str(it["sample_message"]).replace("|", "\\|")[:80]
            lines.append(
                f"| {it['priority']} | {it['frequency']} | {it['max_severity']} "
                f"| {it['category']} | {sample} |"
            )
        print("\n".join(lines))
    else:
        print(json.dumps(summary, indent=2, default=str))


def cmd_feedback_resolve(args: argparse.Namespace) -> None:
    """Plexus: mark per-project nexus_feedback row(s) resolved so harvest stops re-firing.

    Resolve target is specified EITHER by --backlog-id N (looked up in the
    Plexus-only improvement_backlog to recover source_project_path + category +
    dedup_hash) OR explicitly by --project-path / --category / --hash.

    The dedup_hash is NOT stored on the feedback rows — it is recomputed the SAME
    way harvest does: hashlib.sha256(message.encode('utf-8')).hexdigest(). We open
    the SOURCE project's .memory/project.db READ-WRITE (sqlite3 URI mode=rw, unlike
    harvest's mode=ro), stamp resolved_at + reviewed_by on every OPEN row in the
    target category whose message hashes to the requested hash, and (when resolving
    via a backlog id) stamp improvement_backlog.reviewed_by on the Plexus side too.

    Idempotent: a second resolve touches 0 rows -> exit 0 with already_resolved=True.

    Version-scoped mode (--up-to-version V): only OPEN rows whose nexus_version
    SEMVER-tuple <= V are stamped, so an upgrade can clear "already-fixed-by-upgrade"
    feedback (e.g. all <= 1.11.0) while leaving live pain in newer versions open.
    Comparison is tuple-based (1.9.0 <= 1.12.0, NOT string compare). Rows stamped
    'unknown' (legacy / unreadable version) are LEFT OPEN by default and cleared
    only when --include-unknown is also passed (documented choice: never silently
    sweep ambiguously-versioned rows under a version ceiling).
    """
    reviewed_by = (getattr(args, "reviewed_by", None) or "plexus").strip() or "plexus"
    backlog_id = getattr(args, "backlog_id", None)
    up_to_version = getattr(args, "up_to_version", None)
    include_unknown = bool(getattr(args, "include_unknown", False))

    project_path: str | None = getattr(args, "project_path", None)
    category: str | None = getattr(args, "category", None)
    dedup_hash: str | None = getattr(args, "hash", None)

    if backlog_id is not None:
        with _conn() as conn:
            _ensure_improvement_backlog(conn)
            brow = conn.execute(
                "SELECT source_project_path, category, dedup_hash "
                "FROM improvement_backlog WHERE id=?",
                (backlog_id,),
            ).fetchone()
        if brow is None:
            print(
                f"feedback resolve: no improvement_backlog row with id {backlog_id}",
                file=sys.stderr,
            )
            sys.exit(1)
        project_path = brow["source_project_path"]
        category = brow["category"]
        dedup_hash = brow["dedup_hash"]
    else:
        if not (project_path and category and dedup_hash):
            print(
                "feedback resolve: provide --backlog-id N OR all of "
                "--project-path P --category C --hash H",
                file=sys.stderr,
            )
            sys.exit(1)

    pdb = Path(project_path) / ".memory" / "project.db"
    if not pdb.is_file():
        print(
            f"feedback resolve: no project.db at {pdb}",
            file=sys.stderr,
        )
        sys.exit(1)

    now = _now()
    rows_resolved = 0
    try:
        pconn = sqlite3.connect(f"file:{pdb}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        print(f"feedback resolve: cannot open {pdb} read-write: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        _harden_connection(pconn)
        pconn.row_factory = sqlite3.Row
        has_table = pconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nexus_feedback'"
        ).fetchone()
        if not has_table:
            print(
                f"feedback resolve: {pdb} has no nexus_feedback table",
                file=sys.stderr,
            )
            sys.exit(1)
        # The dedup_hash is not stored — recompute it per OPEN row and match,
        # exactly as harvest hashes the message. When --up-to-version is set we
        # also gate on a SEMVER-tuple ceiling (legacy DBs without the column read
        # back as 'unknown').
        has_version_col = any(
            r[1] == "nexus_version"
            for r in pconn.execute("PRAGMA table_info(nexus_feedback)")
        )
        version_select = "nexus_version" if has_version_col else "'unknown' AS nexus_version"
        ceiling = _version_tuple(up_to_version) if up_to_version else None
        targets: list[int] = []
        for fr in pconn.execute(
            f"SELECT id, message, {version_select} FROM nexus_feedback "
            "WHERE category=? AND resolved_at IS NULL",
            (category,),
        ):
            message = fr["message"] or ""
            row_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
            if row_hash != dedup_hash:
                continue
            if ceiling is not None:
                row_version = fr["nexus_version"] or "unknown"
                if row_version == "unknown":
                    if not include_unknown:
                        continue
                elif _version_tuple(row_version) > ceiling:
                    continue
            targets.append(int(fr["id"]))
        for fid in targets:
            pconn.execute(
                "UPDATE nexus_feedback SET resolved_at=?, reviewed_by=? "
                "WHERE id=? AND resolved_at IS NULL",
                (now, reviewed_by, fid),
            )
        rows_resolved = len(targets)
        pconn.commit()
    finally:
        pconn.close()

    # Stamp the Plexus-side backlog row's reviewer when resolving via a backlog id.
    if backlog_id is not None:
        with _conn() as conn:
            _ensure_improvement_backlog(conn)
            conn.execute(
                "UPDATE improvement_backlog SET reviewed_by=? WHERE id=?",
                (reviewed_by, backlog_id),
            )

    result = {
        "rows_resolved": rows_resolved,
        "source_path": str(project_path),
        "resolved_at": now,
        "reviewed_by": reviewed_by,
    }
    if up_to_version:
        result["up_to_version"] = up_to_version
        result["include_unknown"] = include_unknown
    if rows_resolved == 0:
        result["already_resolved"] = True
    print(json.dumps(result))


def _load_health_module():  # type: ignore[return]
    """Load health.py from nexus-package, registering in sys.modules before exec_module.

    sys.modules registration MUST precede exec_module so the @dataclass decorator
    can resolve cls.__module__ ('health') when building field descriptors.
    """
    import importlib.util as _ilu

    try:
        import health  # type: ignore[import]
        return health
    except ImportError:
        pass
    pkg_health = (
        Path(__file__).resolve().parent.parent / "nexus-package" / ".memory" / "health.py"
    )
    if not pkg_health.is_file():
        raise ImportError(f"health.py not found at {pkg_health}")
    spec = _ilu.spec_from_file_location("health", str(pkg_health))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {pkg_health}")
    mod = _ilu.module_from_spec(spec)
    sys.modules["health"] = mod  # register BEFORE exec_module
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def cmd_registry_health(args: argparse.Namespace) -> None:
    """Fleet health: run health checks on all registered projects."""
    import time as _time

    _health_mod = _load_health_module()
    run_checks = _health_mod.run_checks

    with _conn() as conn:
        rows = conn.execute(
            "SELECT project_path, current_version, status FROM project_registry "
            "WHERE status != 'removed' ORDER BY project_path"
        ).fetchall()

    if not rows:
        print("No registered projects.")
        return

    fleet_t0 = _time.monotonic()
    fleet_results: list[dict] = []

    for row in rows:
        ppath = row["project_path"]
        pversion = row["current_version"] or "?"
        if not Path(ppath).exists():
            fleet_results.append({
                "path": ppath,
                "version": pversion,
                "status": "PATH_MISSING",
                "passes": 0, "warns": 0, "fails": 1,
            })
            continue
        try:
            # leak_check=False: scan is O(files × projects); install-time-only.
            # Pass --leak-check flag to enable explicitly for a full fleet scan.
            report = run_checks(
                ppath,
                runtime=bool(getattr(args, "full", False)),
                drift=bool(getattr(args, "drift", False)),
                embed_check=False,
                leak_check=bool(getattr(args, "leak_check", False)),
            )
            fleet_results.append({
                "path": ppath,
                "version": pversion,
                "status": "OK" if not report.fails else "FAIL",
                "passes": len(report.passes),
                "warns": len(report.warns),
                "fails": len(report.fails),
            })
        except Exception as exc:  # noqa: BLE001
            fleet_results.append({
                "path": ppath,
                "version": pversion,
                "status": "ERROR",
                "passes": 0, "warns": 0, "fails": 1,
                "error": str(exc),
            })

    fleet_elapsed = _time.monotonic() - fleet_t0

    if getattr(args, "json_out", False):
        print(json.dumps({"projects": fleet_results, "elapsed": round(fleet_elapsed, 3)}))
        return

    # ASCII table output
    col_w = max(len(Path(r["path"]).name) for r in fleet_results) + 2
    header_fmt = f"  {{:<{col_w}}} {{:<8}} {{:<7}} {{:<7}} {{}}"
    row_fmt = f"  {{:<{col_w}}} {{:<8}} {{:<7}} {{:<7}} {{}}"
    print("─" * 72)
    print(header_fmt.format("Project", "Version", "Static", "Runtime", "Summary"))
    print("─" * 72)
    for r in fleet_results:
        name = Path(r["path"]).name
        v = r["version"]
        static = "✓" if r["fails"] == 0 and r["warns"] == 0 else ("⚠" if r["warns"] else "✗")
        runtime = "n/a" if not getattr(args, "full", False) else static
        summary = f"{r['passes']} PASS · {r['warns']} WARN · {r['fails']} FAIL"
        if r["status"] == "PATH_MISSING":
            summary = "PATH MISSING"
        elif r["status"] == "ERROR":
            summary = f"ERROR: {r.get('error', '')[:40]}"
        print(row_fmt.format(name, v, static, runtime, summary))
    print("─" * 72)
    total_fail = sum(r["fails"] for r in fleet_results)
    total_warn = sum(r["warns"] for r in fleet_results)
    total_pass = sum(r["passes"] for r in fleet_results)
    print(f"  {len(fleet_results)} project(s) · {total_pass} PASS · {total_warn} WARN · "
          f"{total_fail} FAIL · elapsed {fleet_elapsed:.1f}s")
    print("─" * 72)


# ---------------------------------------------------------------------------
# feature_specs
# ---------------------------------------------------------------------------

def cmd_feature_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO feature_specs
               (id, title, status, spec_path, description, tasks_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,
                 COALESCE((SELECT created_at FROM feature_specs WHERE id=?), ?),
                 ?)""",
            (
                args.id,
                args.title,
                args.status or "planned",
                args.spec_path,
                args.description,
                args.tasks_json,
                args.id,
                now,
                now,
            ),
        )
    print(json.dumps({"feature_id": args.id, "updated_at": now}))


def cmd_feature_update(args: argparse.Namespace) -> None:
    now = _now()
    fields, vals = [], []
    for fname in ("title", "status", "spec_path", "description", "tasks_json"):
        v = getattr(args, fname, None)
        if v is not None:
            fields.append(f"{fname}=?")
            vals.append(v)
    if not fields:
        print("No fields to update", file=sys.stderr)
        sys.exit(1)
    fields.append("updated_at=?")
    vals.append(now)
    vals.append(args.id)
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE feature_specs SET {','.join(fields)} WHERE id=?", vals
        )
        if cur.rowcount == 0:
            print(f"No feature_specs row with id {args.id}", file=sys.stderr)
            sys.exit(1)
    print(json.dumps({"feature_id": args.id, "updated_at": now}))


# ---------------------------------------------------------------------------
# lessons (Phase 3 — Technique 9)
# ---------------------------------------------------------------------------

def cmd_lesson_add(args: argparse.Namespace) -> None:
    """Bi-temporal lesson write (OPT-054 / TASK-035).

    Three outcomes, decided by comparing the FULL-payload content_hash (FORK-1)
    against the current row for the logical key:
      ADD       — no current row exists → insert one current row.
      NOOP      — a current row exists with an identical content_hash → do nothing
                  (no new row, no re-embed). An identical re-write is idempotent.
      SUPERSEDE — a current row exists with a DIFFERENT content_hash → close the
                  old row (valid_to, superseded_by, id re-suffixed) and insert a
                  new current row that supersedes it.

    The embed-outbox marker is enqueued in the SAME relational txn as the write
    (OPT-055 A) for ADD and SUPERSEDE; NOOP enqueues nothing.
    """
    now = _now()
    validated_flag = 1 if args.validated else 0
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        lid = args.id or _next_id(conn, "lessons", "LSN")
        # Get current open session for attribution
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        sid = row["id"] if row else None

        payload = {
            "trigger": args.trigger,
            "title": args.title,
            "body": args.body,
            "applies_to": args.applies_to or "all",
            "validated": validated_flag,
            "source_decision_id": args.source_decision_id,
        }
        new_hash = _content_hash("lessons", payload)

        current = _current_row(conn, "lessons", lid)
        if current is not None and current["content_hash"] == new_hash:
            # NOOP — identical full payload. No new row, no re-embed.
            print(json.dumps({
                "lesson_id": lid,
                "recorded_at": current["recorded_at"],
                "validated": bool(current["validated"]),
                "noop": True,
            }))
            return

        supersedes_id: str | None = None
        if current is not None:
            # SUPERSEDE — close the old row, free the bare id for the new current row.
            supersedes_id = _close_and_suffix_old_row(conn, "lessons", lid, lid, now)

        conn.execute(
            """INSERT INTO lessons
               (id, trigger, title, body, applies_to, source_session_id,
                source_decision_id, validated, recorded_at, validated_at,
                valid_from, valid_to, superseded_by, supersedes,
                content_hash, is_tombstone)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lid,
                args.trigger,
                args.title,
                args.body,
                args.applies_to or "all",
                sid,
                args.source_decision_id,
                validated_flag,
                now,
                now if args.validated else None,
                now,            # valid_from
                None,           # valid_to (current)
                None,           # superseded_by
                supersedes_id,  # supersedes (NULL on a plain ADD)
                new_hash,
                0,              # is_tombstone
            ),
        )
        # OPT-055 A — enqueue intent-to-embed in the SAME relational txn as the
        # source INSERT so source-row + marker land atomically.
        lesson_blob = f"{args.title}\n{args.body}"
        _outbox_enqueue(conn, "lesson", lid, lesson_blob)
    print(json.dumps({"lesson_id": lid, "recorded_at": now, "validated": bool(args.validated)}))
    # Embed side-effect
    try:
        text_blob = f"{args.title}\n{args.body}"
        with _vec_conn() as vconn:
            _vec_insert(vconn, "lesson", lid, text_blob, now)
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory: embed side-effect skipped for {lid}: {exc}", file=sys.stderr)


def cmd_lesson_validate(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE lessons SET validated=1, validated_at=?, source_decision_id=? WHERE id=?",
            (now, args.as_decision, args.id),
        )
        if cur.rowcount == 0:
            print(f"No lesson found with id {args.id}", file=sys.stderr)
            sys.exit(1)
    print(json.dumps({"lesson_id": args.id, "validated_at": now, "decision": args.as_decision}))


def cmd_lesson_list(args: argparse.Namespace) -> None:
    """List lessons. Default is current-only (valid_to IS NULL, not a tombstone);
    --history walks the full bi-temporal chain (every version of every key)."""
    history = bool(getattr(args, "history", False))
    where, vals = [], []
    if not history:
        where.append("valid_to IS NULL")
        where.append("is_tombstone=0")
    if args.validated is not None:
        where.append("validated=?")
        vals.append(1 if args.validated else 0)
    if args.applies_to:
        where.append("(applies_to='all' OR applies_to LIKE ?)")
        vals.append(f"%{args.applies_to}%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        if history:
            rows = conn.execute(
                f"SELECT id, trigger, title, applies_to, validated, recorded_at, "
                f"valid_from, valid_to, superseded_by, supersedes, is_tombstone "
                f"FROM lessons {clause} ORDER BY valid_from, id",
                vals,
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, trigger, title, applies_to, validated, recorded_at "
                f"FROM lessons {clause} ORDER BY id",
                vals,
            ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# ---------------------------------------------------------------------------
# rca add (Phase D Layer 2 — agent_root_cause_log + vec_memory embed)
# ---------------------------------------------------------------------------

def cmd_rca_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None
        conn.execute(
            """INSERT INTO agent_root_cause_log
               (session_id, agent_name, task_summary, symptom, why_chain_json, pattern_fix, logged_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                session_id,
                args.agent,
                getattr(args, "task_summary", None),
                args.symptom,
                args.why_chain_json,
                args.pattern_fix,
                now,
            ),
        )
        rca_rowid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    print(json.dumps({"rca_id": rca_rowid, "logged_at": now}))
    # Embed side-effect
    try:
        why_chain_text = " → ".join(json.loads(args.why_chain_json or "[]"))
        text_blob = f"symptom: {args.symptom}\nwhy-chain: {why_chain_text}\nfix: {args.pattern_fix}"
        with _vec_conn() as vconn:
            _vec_insert(vconn, "rca", str(rca_rowid), text_blob, now)
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory: embed side-effect skipped for rca {rca_rowid}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# reflection add (Phase D Layer 2 — reflection_snapshot + vec_memory embed)
# ---------------------------------------------------------------------------

def cmd_reflection_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None
        conn.execute(
            """INSERT INTO reflection_snapshot
               (session_id, file_path, action_type, one_line_summary, captured_at)
               VALUES (?,?,?,?,?)""",
            (
                session_id,
                args.file_path or "",
                args.action_type,
                args.summary,
                now,
            ),
        )
        refl_rowid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    print(json.dumps({"reflection_id": refl_rowid, "captured_at": now}))
    # Embed side-effect
    try:
        text_blob = args.summary
        if args.file_path:
            try:
                file_content = Path(args.file_path).read_text()[:500]
                text_blob = f"{args.summary}\n{file_content}"
            except OSError:
                pass
        with _vec_conn() as vconn:
            _vec_insert(vconn, "reflection", str(refl_rowid), text_blob, now)
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory: embed side-effect skipped for reflection {refl_rowid}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# recall --semantic (Phase D Layer 2 — semantic search over vec_memory)
# ---------------------------------------------------------------------------

def _recall_unavailable(reason: str) -> None:
    """Emit the structured recall-down sentinel and exit 3 (P1-02).

    This is the load-bearing distinction: a down subsystem must NOT look like
    'no matches'. Callers (and the orchestrator) key off exit 3 + the error
    field to know memory is degraded rather than genuinely empty.
    """
    print(json.dumps({"error": "recall_unavailable", "reason": reason, "results": []}))
    sys.exit(3)


def _recall_keyword_fallback(
    query: str,
    top_k: int,
    kind_filter: str | None,
    reason: str,
) -> None:
    """Keyword/relational fallback for --fallback keyword mode.

    Opens the relational DB and searches decisions, lessons, rca, and reflection
    tables using SQL LIKE across their text columns. Returns a degraded-marker
    envelope and exits 0.
    Only called when embed is unavailable AND --fallback keyword was passed.
    """
    terms = [t.strip() for t in query.split() if t.strip()]
    results: list[dict] = []
    conn = _conn()

    def _escape_like(s: str) -> str:
        """Escape LIKE metacharacters (% and _) so literal matches work correctly.

        Uses backslash as the escape character; append ESCAPE '\\' to each LIKE clause.
        """
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # Derived from _BACKFILL_SOURCES — canonical (kind, table, id_col, ts_col) map.
    # Per-kind search columns match the schema column names exactly.
    # Each source: (kind, table, [text columns to search], [output columns])
    # Table names and column names must exactly match schema.sql:
    #   agent_root_cause_log: symptom, pattern_fix, logged_at
    #   reflection_snapshot:  one_line_summary, captured_at
    #   lessons:              recorded_at (NOT created_at)
    sources: list[tuple[str, str, list[str], list[str]]] = [
        ("decision",   "decisions",            ["context", "decision", "rationale"],  ["id", "context", "decision", "rationale", "decided_at"]),
        ("lesson",     "lessons",              ["title", "body"],                     ["id", "title", "body", "recorded_at"]),
        ("rca",        "agent_root_cause_log", ["symptom", "pattern_fix"],            ["id", "symptom", "pattern_fix", "logged_at"]),
        ("reflection", "reflection_snapshot",  ["one_line_summary"],                  ["id", "one_line_summary", "captured_at"]),
    ]

    for kind, table, search_cols, out_cols in sources:
        if kind_filter and kind_filter != kind:
            continue
        # Check table exists; log (not swallow) when a configured table is absent
        # so a typo or schema drift is never silent.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            print(
                f"[recall_keyword_fallback] WARNING: configured table {table!r} "
                f"for kind {kind!r} not found in DB — skipping",
                file=sys.stderr,
            )
            continue
        if not terms:
            continue
        # Build LIKE clause with ESCAPE to handle metacharacters in query terms.
        # Each term must match at least one search column (OR across cols, AND across terms).
        where_parts: list[str] = []
        params: list[str] = []
        for term in terms:
            escaped = _escape_like(term)
            col_clauses = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in search_cols)
            where_parts.append(f"({col_clauses})")
            params.extend(f"%{escaped}%" for col in search_cols)
        where_sql = " AND ".join(where_parts)
        # Only select columns that exist; guard with a safe column intersection
        try:
            pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
            avail = {row[1] for row in pragma}
            select_cols = [c for c in out_cols if c in avail]
            if not select_cols:
                continue
            sql = f"SELECT {', '.join(select_cols)} FROM {table} WHERE {where_sql} LIMIT ?"
            rows = conn.execute(sql, params + [top_k]).fetchall()
        except sqlite3.DatabaseError:
            continue
        for row in rows:
            entry: dict = {"kind": kind}
            for i, col in enumerate(select_cols):
                entry[col] = row[i]
            results.append(entry)
        if len(results) >= top_k:
            break

    results = results[:top_k]
    print(json.dumps({
        "mode": "keyword_fallback",
        "degraded": True,
        "reason": reason,
        "results": results,
    }))
    sys.exit(0)


def cmd_recall(args: argparse.Namespace) -> None:
    query = args.semantic
    top_k = args.top_k or 5
    kind_filter = getattr(args, "kind", None)
    since_arg = getattr(args, "since", None)

    # Parse --since Nd (e.g. 30d) into cutoff ISO string
    cutoff: str | None = None
    if since_arg:
        from datetime import timedelta
        m = re.match(r"^(\d+)d$", since_arg)
        if m:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))).isoformat()  # noqa: UP017

    # Subsystem-down checks come BEFORE empty-match. The vector store must
    # exist and the embed backend must answer; otherwise recall is unavailable
    # (exit 3), never a silent empty list. EXCEPTION: when --fallback keyword is
    # requested and sqlite-vec itself is unavailable (degraded host / no venv),
    # degrade straight to the relational keyword search — it needs no extension.
    try:
        conn = _vec_conn()
    except VecUnavailable as exc:
        if getattr(args, "fallback", None) == "keyword":
            _recall_keyword_fallback(query, top_k, kind_filter, f"vec_extension_unavailable:{exc}")
            return
        _recall_unavailable(f"vec_extension_unavailable:{exc}")
        return
    except Exception as exc:  # noqa: BLE001
        _recall_unavailable(f"vec_extension_unavailable:{exc}")
        return

    with conn:
        _assert_vec_dim(conn)
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memory'"
        ).fetchone()
        if table is None:
            _recall_unavailable("vec_memory_missing")
            return

        # OPT-055 C2 — model-swap ENFORCE: if any recalled row was embedded by a
        # different model (same dim), emit a loud banner and auto-enqueue the
        # stale rows for re-embed on the next `vec backfill`. Non-fatal — the
        # DIMENSION mismatch hard-stop is _assert_vec_dim above (GUARDRAIL #4).
        _detect_model_swap(conn)

        # Validate --kind against the live set of kinds (exit 2 on unknown).
        if kind_filter:
            valid_kinds = sorted(
                r["kind"] for r in conn.execute(
                    "SELECT DISTINCT kind FROM vec_memory"
                ).fetchall()
                if r["kind"] is not None
            )
            if kind_filter not in valid_kinds:
                print(json.dumps({
                    "error": "unknown_kind",
                    "kind": kind_filter,
                    "valid_kinds": valid_kinds,
                    "results": [],
                }))
                sys.exit(2)

        # Embed the query — backend down ⇒ recall unavailable (exit 3) UNLESS
        # --fallback keyword was passed, in which case degrade to keyword search.
        query_vec = _embed(query)
        if query_vec is None:
            if args.fallback == "keyword":
                _recall_keyword_fallback(query, top_k, kind_filter, "embed_endpoint_unavailable")
                return
            _recall_unavailable("embed_endpoint_unavailable")
            return
        if len(query_vec) != _EMBED_DIM:
            _recall_unavailable(f"query_dim_mismatch:{len(query_vec)}!={_EMBED_DIM}")
            return

        fetch_k = top_k if cutoff is None else top_k * 10

        import sqlite_vec as _sv
        query_blob = _sv.serialize_float32(query_vec)
        try:
            if kind_filter:
                rows = conn.execute(
                    """SELECT kind, ref_id, text_blob, created_at, distance
                         FROM vec_memory
                        WHERE kind = ?
                          AND embedding MATCH ?
                          AND k = ?
                        ORDER BY distance ASC""",
                    (kind_filter, query_blob, fetch_k),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT kind, ref_id, text_blob, created_at, distance
                         FROM vec_memory
                        WHERE embedding MATCH ?
                          AND k = ?
                        ORDER BY distance ASC""",
                    (query_blob, fetch_k),
                ).fetchall()
        except Exception as exc:  # noqa: BLE001
            # A MATCH failure on a present table is a subsystem fault, not empty.
            _recall_unavailable(f"vector_query_failed:{exc}")
            return

    results = [dict(r) for r in rows]
    if cutoff:
        results = [r for r in results if r["created_at"] >= cutoff]
    results = results[:top_k]
    # Genuine empty match on a healthy backend ⇒ exit 0 + [].
    print(json.dumps(results, indent=2))


# ---------------------------------------------------------------------------
# vec backfill (P1-03 — drain dead-letter + re-embed missing source rows)
# ---------------------------------------------------------------------------

def _vec_backfill_text(kind: str, src: sqlite3.Row) -> str | None:
    """Reconstruct the embed text_blob for a source row, mirroring the add path."""
    if kind == "decision":
        return (
            f"context: {src['context']}\n"
            f"decision: {src['decision']}\n"
            f"rationale: {src['rationale']}"
        )
    if kind == "lesson":
        return f"{src['title']}\n{src['body']}"
    if kind == "rca":
        try:
            chain = " → ".join(json.loads(src["why_chain_json"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            chain = ""
        return (
            f"symptom: {src['symptom']}\n"
            f"why-chain: {chain}\n"
            f"fix: {src['pattern_fix']}"
        )
    if kind == "reflection":
        return src["one_line_summary"] or ""
    return None


# (kind, source_table, id_column, created_at_column) for backfill scanning.
_BACKFILL_SOURCES = [
    ("decision",   "decisions",            "id",  "decided_at"),
    ("lesson",     "lessons",              "id",  "recorded_at"),
    ("rca",        "agent_root_cause_log", "id",  "logged_at"),
    ("reflection", "reflection_snapshot",  "id",  "captured_at"),
]


def cmd_vec_backfill(args: argparse.Namespace) -> None:
    """Drain the embed outbox + dead-letter queue, re-embedding into vec_memory.

    OPT-055 B — three steps, cheapest first:
      STEP 0 (primary, O(pending)): drain embed_outbox — the transactional-outbox
        markers written atomically with each source row. For every marker not yet
        in vec_memory, embed + INSERT the vec row + stamp provenance + DELETE the
        marker, all in this same vec txn (GUARDRAIL #1). If the (kind, ref_id) is
        already in vec_memory, just DELETE the marker (self-heal orphans).
      STEP 1: drain the legacy dead-letter queue (pre-OPT-055 / dim-mismatch path).
      STEP 2 (O(N) source sweep): only on --full. The outbox makes the full sweep
        a backstop, not the hot path.

    Model-swap (OPT-055 C2) is detected up front: stale (kind, ref_id) are
    auto-enqueued into embed_outbox and then drained by STEP 0 in this same run.

    Drained rows are deleted only after a successful vector write, so a still-down
    backend leaves the queues intact for the next run. If the embed backend is
    unavailable the command fails LOUD (exit 3) rather than pretending it drained.
    """
    full = bool(getattr(args, "full", False))
    drained_outbox = 0
    drained_deadletter = 0
    embedded_missing = 0
    still_failing = 0

    # _vec_conn gates the import: when sqlite-vec is unavailable it raises the
    # typed VecUnavailable with an actionable message (rc 3) instead of a raw
    # ImportError traceback. backfill is an explicit vec op, so failing here is
    # correct — but it must fail cleanly, not look like a crash.
    try:
        conn = _vec_conn()
    except VecUnavailable as exc:
        print(
            f"vec backfill unavailable: {exc}. Build .memory/.venv with "
            "sqlite-vec, then re-run `log.py vec backfill`.",
            file=sys.stderr,
        )
        sys.exit(3)
    import sqlite_vec as _sv
    with conn:
        _assert_vec_dim(conn)
        _apply_M001(conn)
        _ensure_deadletter_table(conn)
        _ensure_outbox_table(conn)
        _ensure_provenance_table(conn)

        # Probe the backend once up front — a cold backend should not look like
        # "nothing to do".
        if _embed("backfill probe") is None:
            print(json.dumps({
                "error": "recall_unavailable",
                "reason": "embed_endpoint_unavailable",
                "drained_outbox": 0,
                "drained_deadletter": 0,
                "embedded_missing": 0,
            }))
            sys.exit(3)

        existing = {
            (r["kind"], r["ref_id"])
            for r in conn.execute("SELECT kind, ref_id FROM vec_memory").fetchall()
        }

        # C2 — model-swap ENFORCE: enqueue stale rows so STEP 0 re-embeds them now.
        _detect_model_swap(conn)

        # STEP 0 — drain the embed outbox (primary path).
        ob_rows = conn.execute(
            "SELECT id, kind, ref_id, text_blob FROM embed_outbox ORDER BY id"
        ).fetchall()
        for r in ob_rows:
            key = (r["kind"], r["ref_id"])
            if key in existing:
                # Self-heal: vec row already present — just clear the marker.
                _outbox_clear(conn, r["kind"], r["ref_id"])
                drained_outbox += 1
                continue
            vec = _embed(r["text_blob"])
            if vec is None or len(vec) != _EMBED_DIM:
                still_failing += 1
                continue
            conn.execute(
                "INSERT INTO vec_memory(kind, ref_id, text_blob, created_at, embedding) "
                "VALUES (?,?,?,?,?)",
                (r["kind"], r["ref_id"], r["text_blob"], _now(), _sv.serialize_float32(vec)),
            )
            existing.add(key)
            _provenance_upsert(conn, r["kind"], r["ref_id"])
            _outbox_clear(conn, r["kind"], r["ref_id"])
            drained_outbox += 1

        # STEP 1 — drain the legacy dead-letter queue.
        dl_rows = conn.execute(
            "SELECT id, ref_id, kind, text_blob FROM vec_memory_deadletter ORDER BY id"
        ).fetchall()
        for r in dl_rows:
            now = _now()
            key = (r["kind"], r["ref_id"])
            if key in existing:
                conn.execute("DELETE FROM vec_memory_deadletter WHERE id=?", (r["id"],))
                drained_deadletter += 1
                continue
            vec = _embed(r["text_blob"])
            if vec is None or len(vec) != _EMBED_DIM:
                still_failing += 1
                continue
            conn.execute(
                "INSERT INTO vec_memory(kind, ref_id, text_blob, created_at, embedding) "
                "VALUES (?,?,?,?,?)",
                (r["kind"], r["ref_id"], r["text_blob"], now, _sv.serialize_float32(vec)),
            )
            existing.add(key)
            _provenance_upsert(conn, r["kind"], r["ref_id"])
            _outbox_clear(conn, r["kind"], r["ref_id"])
            conn.execute("DELETE FROM vec_memory_deadletter WHERE id=?", (r["id"],))
            drained_deadletter += 1

        # STEP 2 — full O(N) source sweep (backstop; --full only).
        if full:
            for kind, table, id_col, ts_col in _BACKFILL_SOURCES:
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not exists:
                    continue
                for src in conn.execute(f"SELECT * FROM {table}").fetchall():
                    ref_id = str(src[id_col])
                    if (kind, ref_id) in existing:
                        continue
                    text_blob = _vec_backfill_text(kind, src)
                    if not text_blob:
                        continue
                    created_at = src[ts_col] or _now()
                    vec = _embed(text_blob)
                    if vec is None or len(vec) != _EMBED_DIM:
                        _deadletter_insert(conn, kind, ref_id, text_blob, "backfill_embed_failed")
                        still_failing += 1
                        continue
                    conn.execute(
                        "INSERT INTO vec_memory(kind, ref_id, text_blob, created_at, embedding) "
                        "VALUES (?,?,?,?,?)",
                        (kind, ref_id, text_blob, created_at, _sv.serialize_float32(vec)),
                    )
                    existing.add((kind, ref_id))
                    _provenance_upsert(conn, kind, ref_id)
                    _outbox_clear(conn, kind, ref_id)
                    embedded_missing += 1

    print(json.dumps({
        "drained_outbox": drained_outbox,
        "drained_deadletter": drained_deadletter,
        "embedded_missing": embedded_missing,
        "still_failing": still_failing,
    }, indent=2))


# ---------------------------------------------------------------------------
# semantic_facts (Phase 3 — Technique 3, semantic tier)
# ---------------------------------------------------------------------------

def cmd_fact_add(args: argparse.Namespace) -> None:
    """Bi-temporal semantic_fact write (OPT-054 / TASK-036).

    Three outcomes, decided by comparing the FULL-payload content_hash (key +
    value + pinned) against the current row for the logical key:
      ADD       — no current row exists → insert one current row.
      NOOP      — a current row exists with an identical content_hash → do nothing.
                  An identical re-write is idempotent.
      SUPERSEDE — a current row exists with a DIFFERENT content_hash → close the
                  old row (valid_to, superseded_by set) and insert a new current
                  row.  Unlike decisions/lessons, semantic_facts use an INTEGER
                  autoincrement pk — the old row keeps its INTEGER id; only the
                  partial-unique index on ``key`` enforces one-current-row-per-key.
    """
    now = _now()
    pinned_int = 1 if args.pinned else 0
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        sid = row["id"] if row else None

        payload = {
            "key": args.key,
            "value": args.value,
            "pinned": pinned_int,
        }
        new_hash = _content_hash("semantic_facts", payload)

        current = _current_fact_row(conn, args.key)
        if current is not None and current["content_hash"] == new_hash:
            # NOOP — identical full payload.
            print(json.dumps({"key": args.key, "pinned": bool(args.pinned), "noop": True}))
            return

        old_row_id = None
        old_created_at = None
        if current is not None:
            # SUPERSEDE — close the old row; new INSERT will satisfy the partial-unique index.
            old_row_id = current["id"]
            old_created_at = current["created_at"]
            # superseded_by references the logical key (not an integer id) so the chain
            # is walkable via key even after the old row loses currency.
            _close_fact_row(conn, old_row_id, args.key, now)

        # Preserve original created_at so the fact's age is not reset on supersession.
        preserved_created_at = old_created_at if old_created_at is not None else now
        conn.execute(
            """INSERT INTO semantic_facts
               (key, value, source_session_id, source_decision_id, created_at, decayed_at,
                pinned, valid_from, valid_to, superseded_by, supersedes, content_hash, is_tombstone)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                args.key,
                args.value,
                sid,
                args.source_decision_id,
                preserved_created_at,
                None,           # decayed_at (not decayed)
                pinned_int,
                now,            # valid_from
                None,           # valid_to (current)
                None,           # superseded_by
                # supersedes: point to the old row's INTEGER id (as string) if superseding
                str(old_row_id) if old_row_id is not None else None,
                new_hash,
                0,              # is_tombstone
            ),
        )
    print(json.dumps({"key": args.key, "pinned": bool(args.pinned)}))


def cmd_fact_list(args: argparse.Namespace) -> None:
    """List semantic_facts. Default is current-only (valid_to IS NULL, is_tombstone=0,
    decayed_at IS NULL); --history walks the full bi-temporal chain (all versions)."""
    history = bool(getattr(args, "history", False))
    with _conn() as conn:
        _migrate_bitemporal_columns(conn)
        where, vals = [], []
        if not history:
            where.append("valid_to IS NULL")
            where.append("is_tombstone=0")
            where.append("decayed_at IS NULL")
        if args.pinned_only:
            where.append("pinned=1")
        if args.key_like:
            where.append("key LIKE ?")
            vals.append(f"%{args.key_like}%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        if history:
            rows = conn.execute(
                f"SELECT key, value, pinned, source_decision_id, created_at, "
                f"valid_from, valid_to, superseded_by, supersedes, is_tombstone "
                f"FROM semantic_facts {clause} ORDER BY key, valid_from",
                vals,
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT key, value, pinned, source_decision_id, created_at "
                f"FROM semantic_facts {clause} ORDER BY key",
                vals,
            ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


def cmd_fact_decay(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE semantic_facts SET decayed_at=? WHERE key=? AND pinned=0",
            (now, args.key),
        )
        if cur.rowcount == 0:
            print(f"No fact found (or fact is pinned): {args.key}", file=sys.stderr)
            sys.exit(1)
    print(json.dumps({"key": args.key, "decayed_at": now}))


# ---------------------------------------------------------------------------
# procedures (Phase 3 — Technique 3, procedural tier)
# ---------------------------------------------------------------------------

def cmd_procedure_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO procedures
               (id, name, trigger_pattern, steps_json,
                success_count, fail_count, last_used_at, created_at, updated_at)
               VALUES (
                 (SELECT id FROM procedures WHERE name=?),
                 ?, ?, ?,
                 COALESCE((SELECT success_count FROM procedures WHERE name=?), 0),
                 COALESCE((SELECT fail_count FROM procedures WHERE name=?), 0),
                 (SELECT last_used_at FROM procedures WHERE name=?),
                 COALESCE((SELECT created_at FROM procedures WHERE name=?), ?),
                 ?
               )""",
            (
                args.name,
                args.name,
                args.trigger_pattern,
                args.steps_json,
                args.name,
                args.name,
                args.name,
                args.name,
                now,
                now,
            ),
        )
    print(json.dumps({"name": args.name, "updated_at": now}))


def cmd_procedure_record(args: argparse.Namespace) -> None:
    now = _now()
    col = "success_count" if args.outcome == "success" else "fail_count"
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE procedures SET {col}={col}+1, last_used_at=?, updated_at=? WHERE name=?",
            (now, now, args.name),
        )
        if cur.rowcount == 0:
            print(f"No procedure named {args.name}", file=sys.stderr)
            sys.exit(1)
    print(json.dumps({"name": args.name, "outcome": args.outcome, "recorded_at": now}))


def cmd_procedure_list(_args: argparse.Namespace) -> None:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name, trigger_pattern, success_count, fail_count, last_used_at FROM procedures ORDER BY name"
        ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# ---------------------------------------------------------------------------
# memory retain (Phase 3 — Technique 3b retention worker)
# ---------------------------------------------------------------------------

# Quality scoring patterns adapted from orionomega retention-engine.
_HIGH_SIGNAL_PATTERNS = [
    (re.compile(r"\bdecision\b", re.I), 0.30),
    (re.compile(r"\bspec\b|\bcontract\b|\bgate\b", re.I), 0.25),
    (re.compile(r"\bblocker\b|\bblocked\b", re.I), 0.20),
    (re.compile(r"\barchitecture\b|\bschema\b", re.I), 0.20),
    (re.compile(r"\blesson\b", re.I), 0.30),
    (re.compile(r"FEAT-\d+|DEC-\d+|TASK-\d+|LSN-\d+", re.I), 0.15),
]

_LOW_SIGNAL_PATTERNS = [
    (re.compile(r"^(ok|done|sure|continue|next)\.?\s*$", re.I), -0.50),
    (re.compile(r"^starting\.\.\.|^working on", re.I), -0.30),
    (re.compile(r"^auto-snapshot", re.I), -0.20),
]

_QUALITY_THRESHOLD = 0.30
_DEFAULT_TTL_DAYS = 14


def _score_row(text: str) -> float:
    score = 0.0
    for pat, w in _HIGH_SIGNAL_PATTERNS:
        if pat.search(text):
            score += w
    for pat, w in _LOW_SIGNAL_PATTERNS:
        if pat.search(text):
            score += w
    return score


def cmd_memory_retain(args: argparse.Namespace) -> None:
    """Sweep low-signal old context_log rows. Also decay unpinned semantic_facts
    older than --fact-ttl-days. Preserves all decisions/tasks/sessions/lessons/
    procedures by default. Use --apply to commit changes; --dry-run shows what
    would be removed."""
    from datetime import datetime, timedelta, timezone

    ctx_ttl = args.ctx_ttl_days
    fact_ttl = args.fact_ttl_days
    apply_changes = bool(args.apply)

    ctx_threshold = (datetime.now(timezone.utc) - timedelta(days=ctx_ttl)).isoformat()  # noqa: UP017
    fact_threshold = (datetime.now(timezone.utc) - timedelta(days=fact_ttl)).isoformat()  # noqa: UP017

    drop_ctx = []
    decay_facts = []
    with _conn() as conn:
        # Score context_log rows. Drop if score < threshold AND older than TTL
        # AND not linked to a decision (decision_refs IS NULL or empty array).
        for r in conn.execute(
            "SELECT id, logged_at, action_type, summary, decision_refs "
            "FROM context_log WHERE logged_at < ?",
            (ctx_threshold,),
        ).fetchall():
            if r["decision_refs"] and r["decision_refs"] not in ("[]", "null", ""):
                continue
            text = " ".join(filter(None, [r["action_type"] or "", r["summary"] or ""]))
            score = _score_row(text)
            if score < _QUALITY_THRESHOLD:
                drop_ctx.append({"id": r["id"], "score": round(score, 2),
                                 "logged_at": r["logged_at"],
                                 "summary": (r["summary"] or "")[:60]})

        # Decay unpinned semantic_facts not touched in fact_ttl days.
        for r in conn.execute(
            "SELECT id, key FROM semantic_facts "
            "WHERE pinned=0 AND decayed_at IS NULL AND created_at < ?",
            (fact_threshold,),
        ).fetchall():
            decay_facts.append({"id": r["id"], "key": r["key"]})

        if apply_changes:
            now = _now()
            for d in drop_ctx:
                conn.execute("DELETE FROM context_log WHERE id=?", (d["id"],))
            for d in decay_facts:
                conn.execute("UPDATE semantic_facts SET decayed_at=? WHERE id=?", (now, d["id"]))

    print(json.dumps({
        "mode": "applied" if apply_changes else "dry-run",
        "context_log_dropped": len(drop_ctx),
        "facts_decayed": len(decay_facts),
        "ctx_ttl_days": ctx_ttl,
        "fact_ttl_days": fact_ttl,
        "sample_context_drops": drop_ctx[:10],
        "sample_facts_decayed": decay_facts[:10],
    }, indent=2))


def cmd_feature_list(args: argparse.Namespace) -> None:
    where, vals = [], []
    if getattr(args, "status", None):
        where.append("status=?")
        vals.append(args.status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, title, status, spec_path, updated_at FROM feature_specs {clause} ORDER BY id",
            vals,
        ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------

def cmd_context_snapshot(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("No open session.", file=sys.stderr)
            sys.exit(1)
        sid = row["id"]
        conn.execute(
            """INSERT INTO context_log
               (session_id, logged_at, action_type, files_modified, decision_refs, task_updates, summary)
               VALUES (?,?,?,?,?,?,?)""",
            (
                sid,
                now,
                args.action_type,
                args.files_modified,
                args.decision_refs,
                args.task_updates,
                args.summary,
            ),
        )
    print(json.dumps({"session_id": sid, "logged_at": now}))


_CONTEXT_DUMP_TODO_CAP = 8
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_DOMAIN_ORDER = ("nexus", "plexus", "kb", "ops", "other", "unclassified")


def cmd_context_dump(args: argparse.Namespace) -> None:
    tasks_filter = getattr(args, "tasks", "all") or "all"
    dec_limit = getattr(args, "decisions", 5)
    with _conn() as conn:
        session = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        has_domain = _table_has_column(conn, "tasks", "domain")
        # 'archived' (N56 TTL reap), 'pending_review' + 'completed' (DEC-098)
        # are excluded here alongside done/cancelled: terminal or parked-for-
        # review states are not open work — they must NOT reinflate the
        # SessionStart open-task summary. Counted separately below so those
        # rows stay VISIBLE (DEC-005: queryable, never silently dropped)
        # without looking "open".
        open_rows = conn.execute(
            "SELECT id, title, status, priority, assigned_to"
            + (", domain" if has_domain else "")
            + " FROM tasks WHERE status NOT IN "
            "('done','completed','cancelled','archived','pending_review') "
            "ORDER BY id"
        ).fetchall()
        archived_task_count = conn.execute(
            "SELECT count(*) AS c FROM tasks WHERE status='archived'"
        ).fetchone()["c"]
        pending_review_task_count = conn.execute(
            "SELECT count(*) AS c FROM tasks WHERE status='pending_review'"
        ).fetchone()["c"]
        recent_decisions = conn.execute(
            "SELECT id, title, status, decided_at FROM decisions ORDER BY decided_at DESC LIMIT ?",
            (dec_limit,),
        ).fetchall()
    status_counts: dict = {}
    for r in open_rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    if tasks_filter == "in_progress":
        open_tasks = [dict(r) for r in open_rows if r["status"] == "in_progress"]
    else:
        open_tasks = [dict(r) for r in open_rows]
    # DEC-098 grouped view — additive alongside the legacy keys so
    # /project-context and hooks keep parsing open_tasks unchanged. Per domain:
    # in_progress first, then blocked, then todo (todo capped at the top
    # _CONTEXT_DUMP_TODO_CAP by priority, remainder surfaced as todo_more).
    tasks_by_domain: dict = {}
    for dom in _DOMAIN_ORDER:
        rows_d = [
            dict(r) for r in open_rows
            if ((r["domain"] if has_domain else None) or "unclassified") == dom
        ]
        if not rows_d:
            continue
        todo = sorted(
            (r for r in rows_d if r["status"] == "todo"),
            key=lambda r: (_PRIORITY_RANK.get(r.get("priority"), 9), r["id"]),
        )
        tasks_by_domain[dom] = {
            "in_progress": [r for r in rows_d if r["status"] == "in_progress"],
            "blocked": [r for r in rows_d if r["status"] == "blocked"],
            "todo": todo[:_CONTEXT_DUMP_TODO_CAP],
            "todo_more": max(0, len(todo) - _CONTEXT_DUMP_TODO_CAP),
        }
    out = {
        "last_session": dict(session) if session else None,
        "open_tasks": open_tasks,
        "task_status_counts": status_counts,
        "archived_task_count": archived_task_count,
        "pending_review_task_count": pending_review_task_count,
        "tasks_by_domain": tasks_by_domain,
        "recent_decisions": [dict(r) for r in recent_decisions],
    }
    print(json.dumps(out, indent=2))


# ---------------------------------------------------------------------------
# planning-gate
# ---------------------------------------------------------------------------

def _stack_profile_for_gate(project_root: Path) -> dict:
    """Read the project's .memory/nexus-stack.json profile, or {} if absent/unreadable.

    Profile-derived test-stub globs let the planning gate's Item 7 find feature-tagged
    stubs in whatever test locations THIS project actually uses (e.g. insites: web/e2e/
    + api/tests/), instead of the ingestion/app template defaults. Failure to read is
    non-fatal: callers fall back to the hardcoded ingestion/app globs (DEC-010 upstream).
    """
    profile_path = project_root / ".memory" / "nexus-stack.json"
    try:
        with profile_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _top_dir(rel_path: str) -> str:
    """Return the first path segment of a profile-relative dir (e.g. 'web/src' -> 'web')."""
    cleaned = (rel_path or "").strip().strip("/")
    if not cleaned:
        return ""
    return cleaned.split("/", 1)[0]


def _ts_test_globs_for_root(root: str, num_keys: set, spec_slug: str, with_e2e: bool) -> list[str]:
    """Build TS test-stub globs (spec/test .ts/.tsx) under a frontend root dir.

    When ``with_e2e`` is set (a vitest/playwright runner is configured), also emit
    the dedicated ``<root>/e2e/**`` Playwright location used by insites-shaped repos.
    """
    if not root:
        return []
    roots = [root]
    if with_e2e:
        roots.insert(0, f"{root}/e2e")
    globs: list[str] = []
    for r in roots:
        for num in num_keys:
            globs += [
                f"{r}/**/*feat{num}*.spec.ts",
                f"{r}/**/*feat-{num}*.spec.ts",
                f"{r}/**/*feat{num}*.test.ts",
                f"{r}/**/*feat-{num}*.test.ts",
            ]
        if spec_slug:
            globs += [
                f"{r}/**/*{spec_slug}*.spec.ts",
                f"{r}/**/*{spec_slug}*.test.ts",
                f"{r}/**/*{spec_slug}*.test.tsx",
            ]
    return globs


def _py_test_globs_for_root(root: str, num_keys: set, spec_slug: str) -> list[str]:
    """Build Python test-stub globs (pytest) under a backend/data test root dir.

    ``root`` is a source dir from the profile (e.g. 'api'); tests are searched under
    both ``<root>/tests/`` and ``<root>/**`` so api/tests/test_*feat1*.py is found.
    """
    if not root:
        return []
    globs: list[str] = []
    for base in (f"{root}/tests", root):
        for num in num_keys:
            globs += [
                f"{base}/**/test_*feat{num}*.py",
                f"{base}/**/test_*feat_{num}*.py",
                f"{base}/**/*feat-{num}*.py",
                f"{base}/test_*feat{num}*.py",
                f"{base}/test_*feat_{num}*.py",
                f"{base}/*feat-{num}*.py",
            ]
        if spec_slug:
            slug_us = spec_slug.replace("-", "_")
            globs += [
                f"{base}/**/test_{slug_us}*.py",
                f"{base}/**/test_*{spec_slug}*.py",
                f"{base}/test_{slug_us}*.py",
                f"{base}/test_*{spec_slug}*.py",
            ]
    return globs


def _profile_aware_test_globs(
    project_root: Path, num_keys: set, spec_slug: str
) -> list[str]:
    """Derive Item-7 test-stub globs from the project's nexus-stack.json profile.

    Reads frontend.{src_dir,test_dir,test_runner} and backend/data/workers src_dirs and
    builds feature-tagged globs for whatever test locations this project uses. Returns
    [] for a missing/empty profile so the caller keeps the ingestion/app fallback.
    """
    profile = _stack_profile_for_gate(project_root)
    if not profile:
        return []

    globs: list[str] = []
    seen_ts_roots: set = set()
    seen_py_roots: set = set()

    frontend = profile.get("frontend") or {}
    if isinstance(frontend, dict) and frontend.get("present"):
        runner = str(frontend.get("test_runner") or "").lower()
        with_e2e = runner in {"vitest", "playwright"}
        for key in ("src_dir", "test_dir", "ts_check_dir"):
            root = _top_dir(str(frontend.get(key) or ""))
            if root and root not in seen_ts_roots:
                seen_ts_roots.add(root)
                globs += _ts_test_globs_for_root(root, num_keys, spec_slug, with_e2e)

    for bucket_name in ("backend", "data", "workers"):
        bucket = profile.get(bucket_name) or {}
        if not isinstance(bucket, dict):
            continue
        for key in ("src_dir", "py_check_dir", "ingestion_dir"):
            raw = bucket.get(key)
            if not raw:
                continue
            # py_check_dir may be space-separated (e.g. "api worker").
            for token in str(raw).split():
                root = _top_dir(token)
                if root and root not in seen_py_roots:
                    seen_py_roots.add(root)
                    globs += _py_test_globs_for_root(root, num_keys, spec_slug)

    return globs


def cmd_planning_gate_check(args: argparse.Namespace) -> None:
    feat_id = args.feat  # e.g. "FEAT-001"
    docs_root = Path(__file__).resolve().parent.parent / "docs"
    spec_file_glob = sorted(docs_root.glob(f"features/{feat_id}-*.md"))
    spec_dir_glob = sorted(p for p in docs_root.glob(f"features/{feat_id}-*") if p.is_dir())
    spec_glob = spec_file_glob + spec_dir_glob

    results: list[dict] = []

    def check(item: int, title: str, passed: bool, detail: str = "") -> None:
        results.append({"item": item, "title": title, "passed": passed, "detail": detail})

    # Item 1 — spec file exists (flat docs/features/{feat}-*.md, or a directory-style spec
    # docs/features/{feat}-*/ whose *.md files are concatenated below).
    spec_path = spec_glob[0] if spec_glob else None
    check(1, "Spec file exists", spec_path is not None,
          str(spec_path) if spec_path else
          f"docs/features/{feat_id}-*.md (flat) or docs/features/{feat_id}-*/ (directory of *.md files) not found")

    if spec_path is None:
        spec_text = ""
    elif spec_path.is_dir():
        spec_text = "\n".join(p.read_text() for p in sorted(spec_path.rglob("*.md")))
    else:
        spec_text = spec_path.read_text()

    # Item 2 — GWT acceptance criteria present
    gwt_present = bool(re.search(r"\b(Given|When|Then)\b", spec_text))
    check(2, "GWT acceptance criteria written", gwt_present,
          "" if gwt_present else "No Given/When/Then found in spec")

    # Item 3 — no [NEEDS CLARIFICATION] markers
    nc_count = len(re.findall(r"\[NEEDS CLARIFICATION\]", spec_text, re.IGNORECASE))
    check(3, "No [NEEDS CLARIFICATION] markers", nc_count == 0,
          "" if nc_count == 0 else f"{nc_count} marker(s) remain")

    # Item 4 — Constitution check checklist present
    constitution_present = bool(re.search(r"Article\s+[IVX]+", spec_text))
    check(4, "Constitution check checklist present", constitution_present,
          "" if constitution_present else "No Article checklist found — copy from SPEC_TEMPLATE.md")

    # Item 5 — SocratiCode search (manual gate — cannot be auto-verified)
    check(5, "SocratiCode semantic search run (manual)", True,
          "Cannot auto-verify — confirm you ran codebase_search before planning")

    # Item 6 — DB schema locked (DDL present in spec)
    ddl_present = bool(re.search(r"CREATE TABLE", spec_text, re.IGNORECASE))
    check(6, "DB schema locked (DDL in spec)", ddl_present,
          "" if ddl_present else "No CREATE TABLE statement found in spec")

    # Item 7 — test stubs exist for THIS feature (feature-tagged path or name)
    tests_root = Path(__file__).resolve().parent.parent
    feat_num = feat_id.replace("FEAT-", "").lstrip("0") or "0"
    feat_num_padded = feat_id.replace("FEAT-", "")
    # Derive the feature slug from the spec name — flat file (FEAT-006-worksheet-level-search.md
    # → worksheet-level-search) or directory-style spec (FEAT-004-ai-reviewers/ → ai-reviewers).
    spec_slug = ""
    if spec_path is not None:
        name_pattern = rf"{re.escape(feat_id)}-(.+)$" if spec_path.is_dir() else rf"{re.escape(feat_id)}-(.+)\.md$"
        m = re.match(name_pattern, spec_path.name)
        if m:
            spec_slug = m.group(1)
    num_keys = {feat_num, feat_num_padded}
    # Profile-aware globs FIRST: derive test locations from .memory/nexus-stack.json
    # so projects whose tests live elsewhere (insites: web/e2e/ + api/tests/) pass
    # without a local patch (DEC-010 upstream). A missing/partial profile yields [],
    # in which case the hardcoded ingestion/app patterns below act as the fallback.
    feature_globs: list[str] = _profile_aware_test_globs(tests_root, num_keys, spec_slug)
    for num in num_keys:
        feature_globs += [
            f"ingestion/tests/test_*feat{num}*.py",
            f"ingestion/tests/test_*feat_{num}*.py",
            f"ingestion/tests/*feat-{num}*.py",
            f"app/**/*feat{num}*.test.ts",
            f"app/**/*feat-{num}*.test.ts",
            f"app/**/*feat{num}*.spec.ts",
            f"app/**/*feat-{num}*.spec.ts",
        ]
    if spec_slug:
        feature_globs += [
            f"ingestion/tests/test_{spec_slug.replace('-', '_')}*.py",
            f"ingestion/tests/test_*{spec_slug}*.py",
            f"app/**/*{spec_slug}*.test.ts",
            f"app/**/*{spec_slug}*.test.tsx",
            f"app/**/*{spec_slug}*.spec.ts",
        ]
    matches: list[Path] = []
    for g in feature_globs:
        matches.extend(tests_root.glob(g))
    stubs_exist = bool(matches)
    detail = "" if stubs_exist else (
        f"No feature-tagged test stubs found for {feat_id}. Quill must author tests whose path "
        f"or filename contains 'feat{feat_num}', 'feat-{feat_num}', or the spec slug "
        f"'{spec_slug or '<missing>'}' under this project's test dirs (from .memory/nexus-stack.json) "
        f"or the ingestion/tests/ + app/** defaults. "
        f"Generic 'any test file' fallback is not accepted (DEC-035 / reverse-audit E2)."
    )
    check(7, "Feature-tagged test stubs exist", stubs_exist, detail)

    passed_all = all(r["passed"] for r in results)
    gate_result = "PASS" if passed_all else "FAIL"

    print(json.dumps({
        "feat": feat_id,
        "gate": gate_result,
        "items": results,
    }, indent=2))

    if not passed_all:
        sys.exit(1)


_PLANNING_GATE_REQUIRED_FIELDS = (
    "feat",
    "scope_summary",
    "files_touched_estimate",
    "acceptance_criteria",
    "constitution_articles_verified",
    "risks",
    "rollback_plan",
)


def cmd_planning_gate_submit(args: argparse.Namespace) -> None:
    raw = args.json
    if raw == "-":
        raw = sys.stdin.read()
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"gate": "REJECTED", "reason": f"invalid JSON: {e}"}, indent=2))
        sys.exit(2)

    if not isinstance(plan, dict):
        print(json.dumps({"gate": "REJECTED", "reason": "plan must be a JSON object"}, indent=2))
        sys.exit(2)

    feat_id = args.feat or plan.get("feat")
    if not feat_id:
        print(json.dumps({"gate": "REJECTED", "reason": "missing --feat and plan.feat"}, indent=2))
        sys.exit(2)
    plan["feat"] = feat_id

    missing = [f for f in _PLANNING_GATE_REQUIRED_FIELDS if not plan.get(f)]
    type_errors: list[str] = []
    if "acceptance_criteria" in plan and not isinstance(plan["acceptance_criteria"], list):
        type_errors.append("acceptance_criteria must be a list of GWT strings")
    if "constitution_articles_verified" in plan and not isinstance(
        plan["constitution_articles_verified"], list
    ):
        type_errors.append("constitution_articles_verified must be a list of article identifiers")
    if "risks" in plan and not isinstance(plan["risks"], list):
        type_errors.append("risks must be a list of strings")
    if "files_touched_estimate" in plan and not isinstance(
        plan["files_touched_estimate"], (int, float)
    ):
        type_errors.append("files_touched_estimate must be a number")

    if missing or type_errors:
        print(json.dumps({
            "gate": "REJECTED",
            "feat": feat_id,
            "missing_fields": missing,
            "type_errors": type_errors,
        }, indent=2))
        sys.exit(2)

    check_args = argparse.Namespace(feat=feat_id)
    try:
        cmd_planning_gate_check(check_args)
    except SystemExit as e:
        if e.code:
            print(json.dumps({
                "gate": "REJECTED",
                "feat": feat_id,
                "reason": "machine-check failed; resolve issues then resubmit",
            }, indent=2))
            sys.exit(2)

    submitted_at = _now()
    with _conn() as conn:
        sid_row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if sid_row is None:
            print(json.dumps({
                "gate": "REJECTED",
                "feat": feat_id,
                "reason": "no open session — call 'session start' first",
            }, indent=2))
            sys.exit(2)
        conn.execute(
            """
            INSERT INTO context_log (session_id, logged_at, action_type, summary)
            VALUES (?, ?, ?, ?)
            """,
            (sid_row["id"], submitted_at, "planning-gate-submit", json.dumps(plan)),
        )
    print(json.dumps({
        "gate": "ACCEPTED",
        "feat": feat_id,
        "submitted_at": submitted_at,
    }, indent=2))


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

_FEAT_HEADER_RE = re.compile(r"^##\s+(FEAT-\d+)\b")
_OTHER_HEADER_RE = re.compile(r"^##\s+")
_TASK_ROW_RE = re.compile(r"^\|\s*(TASK-\d+)\s*\|")


def _parse_tasks_md(path: Path = TASKS_MD_PATH) -> list[tuple]:
    """Parse `docs/TASKS.md` into seed tuples (id, feature_id, title, priority, assigned_to, status).

    `docs/TASKS.md` is a one-time bootstrap source: it seeds the task table before the
    Stop-hook autosync takes over and `project.db` becomes the source of truth. This
    parser exists so the initial `seed` cannot drift from the hand-authored doc. Tasks
    under a `## FEAT-NNN` heading inherit that feature_id; tasks under any other `## ...`
    heading (e.g. "Infrastructure / Housekeeping") have feature_id=None.
    """
    if not path.exists():
        raise FileNotFoundError(f"TASKS.md not found at {path}")
    feature_id: str | None = None
    rows: list[tuple] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if m := _FEAT_HEADER_RE.match(line):
            feature_id = m.group(1)
            continue
        if _OTHER_HEADER_RE.match(line):
            feature_id = None
            continue
        if not _TASK_ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        tid, title, status, priority, assigned = cells[:5]
        rows.append((tid, feature_id, title, priority, assigned or None, status or "todo"))
    return rows


def cmd_seed(_args: argparse.Namespace) -> None:
    rows = _parse_tasks_md()
    if not rows:
        print(f"No task rows parsed from {TASKS_MD_PATH}.", file=sys.stderr)
        sys.exit(1)
    now = _now()
    with _conn() as conn:
        for tid, fid, title, priority, assigned_to, status in rows:
            exists = conn.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone()
            if exists:
                print(f"  skip {tid} (already exists)")
                continue
            conn.execute(
                """INSERT INTO tasks (id, feature_id, title, status, priority, assigned_to, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (tid, fid, title, status, priority, assigned_to, now, now),
            )
            print(f"  seeded {tid}: {title}")
    print(f"Seed complete ({len(rows)} tasks parsed from docs/TASKS.md).")


# ---------------------------------------------------------------------------
# agent_notepad
# ---------------------------------------------------------------------------

_NOTEPAD_VALID_KINDS = frozenset({"fyi", "nuance", "reminder", "gotcha", "next-agent-action"})

# Heuristic: status-restatement phrases. Rejected when note contains one of
# these tokens in isolation (without other substantive context).
_STATUS_RESTATEMENT_PHRASES = re.compile(
    r"\b(completed|done|in progress|in_progress|finished|accomplished)\b",
    re.IGNORECASE,
)


def _is_status_restatement(note: str) -> bool:
    """Return True if the note looks like a bare task-status update.

    A note is rejected when it contains a restatement phrase AND the note
    itself is short (<=80 chars), which covers "I completed step 3" without
    catching a nuance like "The DuckDB lock is held when done via dramatiq."
    """
    if not _STATUS_RESTATEMENT_PHRASES.search(note):
        return False
    # If the note is very short it's almost certainly a status restatement.
    stripped = note.strip()
    if len(stripped) <= 80:
        return True
    # Longer note: reject only if the status phrase accounts for most of the
    # non-whitespace content (i.e., there's very little else).
    words = stripped.split()
    return len(words) <= 6


# ---------------------------------------------------------------------------
# validation (lens-gate hook)
# ---------------------------------------------------------------------------

# OPT-038 — evidence-derived verdict. The validation row is the single fact
# lens-gate.sh trusts to open the NEXUS:DONE gate (OPT-037: latest in-window
# verdict must be PASS). Historically `validation add` stored the *claimed*
# verdict verbatim — a self-attestation with no binding to the per-criterion
# results, so Lens could record PASS over a report whose criteria_results[] were
# full of FAILs. This moves verdict aggregation OUT of the model: when a
# structured Lens report is supplied, code computes the verdict from the
# evidence (verification cardinal rule #2: "even one FAIL → verdict =
# FAIL") and refuses to store a PASS that the evidence contradicts.

_VALID_VERDICTS = ("PASS", "PARTIAL", "FAIL")


def _extract_report_signals(report: dict) -> tuple[int, int, int, list[str]]:
    """Scan a canonical Lens report for FAIL / PARTIAL evidence.

    Reads the two structured evidence channels of the verification
    output schema:
      * ``criteria_results[]`` — each ``{criterion, result}`` where ``result`` is
        one of PASS|FAIL|PARTIAL.
      * ``deterministic`` — ``{key: {command, exit_code, stdout}}`` where any
        ``exit_code != 0`` is a hard FAIL (a failing build/lint/test).

    Returns ``(criteria_count, fail_count, partial_count, reasons)`` where
    ``criteria_count`` counts every structured signal seen (criteria rows +
    deterministic commands) so the caller can tell "report present but empty"
    from "report carries real evidence". ``reasons`` are short human-readable
    strings naming each FAIL/PARTIAL signal, for the stored evidence_summary.
    """
    criteria_count = 0
    fail_count = 0
    partial_count = 0
    reasons: list[str] = []

    results = report.get("criteria_results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            criteria_count += 1
            status = str(item.get("result", item.get("status", ""))).strip().upper()
            label = str(item.get("criterion", "<unnamed criterion>")).strip()[:60]
            if status == "FAIL":
                fail_count += 1
                reasons.append(f"criterion FAIL: {label}")
            elif status == "PARTIAL":
                partial_count += 1
                reasons.append(f"criterion PARTIAL: {label}")

    deterministic = report.get("deterministic")
    if isinstance(deterministic, dict):
        for key, block in deterministic.items():
            # A deterministic key may be a single {command, exit_code, …} block
            # or a list of them (e.g. the schema's "custom": [ … ]).
            blocks = block if isinstance(block, list) else [block]
            for b in blocks:
                if not isinstance(b, dict) or "exit_code" not in b:
                    continue
                criteria_count += 1
                try:
                    code = int(b.get("exit_code"))
                except (TypeError, ValueError):
                    # Non-numeric exit_code is itself unverifiable — treat as FAIL.
                    fail_count += 1
                    reasons.append(f"deterministic[{key}] non-numeric exit_code")
                    continue
                if code != 0:
                    fail_count += 1
                    cmd = str(b.get("command", key))[:50]
                    reasons.append(f"deterministic[{key}] exit_code={code} ({cmd})")

    return criteria_count, fail_count, partial_count, reasons


def derive_verdict_from_report(
    report: dict | None, claimed: str
) -> tuple[str, bool, str]:
    """Bind a claimed verdict to the evidence in a structured Lens report.

    Returns ``(verdict, backed, note)``:
      * ``verdict`` — the verdict to STORE. With a report carrying evidence the
        verdict is DERIVED, never the bare claim:
          - any FAIL signal  → FAIL (a claimed PASS/PARTIAL is downgraded);
          - else any PARTIAL  → at least PARTIAL (a claimed PASS is downgraded;
            a claimed FAIL is honoured — code never *upgrades* a claim);
          - else (all clean)  → the claim stands (PASS allowed).
      * ``backed`` — True iff the report carried ≥1 structured signal. When
        False the claim is stored as-is but flagged UNBACKED so the gap is
        visible rather than silent.
      * ``note`` — a short annotation appended to evidence_summary recording the
        binding outcome (derived / honoured / unbacked) and any downgrade reason.
    """
    claimed = claimed.upper()
    if report is None:
        return claimed, False, "verdict UNBACKED: no structured report supplied"

    criteria_count, fail_count, partial_count, reasons = _extract_report_signals(report)

    if criteria_count == 0:
        return (
            claimed,
            False,
            "verdict UNBACKED: report carried no criteria_results/deterministic evidence",
        )

    if fail_count > 0:
        derived = "FAIL"
    elif partial_count > 0:
        derived = "PARTIAL"
    else:
        derived = "PASS"

    # Code never upgrades a self-reported verdict — only holds it down to what
    # the evidence supports. A clean report (derived PASS) with a claimed FAIL
    # keeps the FAIL; the implementer's stricter judgement is preserved.
    rank = {"FAIL": 0, "PARTIAL": 1, "PASS": 2}
    final = derived if rank[derived] <= rank[claimed] else claimed

    detail = "; ".join(reasons[:5]) if reasons else "all signals clean"
    if final != claimed:
        note = (
            f"verdict DOWNGRADED {claimed}→{final} by evidence "
            f"({criteria_count} signals, {fail_count} FAIL, {partial_count} PARTIAL): {detail}"
        )
    else:
        note = (
            f"verdict {final} evidence-derived "
            f"({criteria_count} signals, {fail_count} FAIL, {partial_count} PARTIAL): {detail}"
        )
    return final, True, note


def _load_report_for_validation(args: argparse.Namespace) -> dict | None:
    """Resolve the structured Lens report from --report-path / --report-json / stdin.

    Returns the parsed dict, or None when no report was supplied. A supplied-but-
    unparseable report is a hard error (exit 1) — silently treating a malformed
    report as "no report" would re-open the unbacked-PASS hole this guards.
    """
    raw: str | None = None
    src = ""
    report_path = getattr(args, "report_path", None)
    report_json = getattr(args, "report_json", None)
    if report_path:
        src = report_path
        if report_path == "-":
            raw = sys.stdin.read()
        else:
            try:
                raw = Path(report_path).read_text()
            except OSError as exc:
                print(f"validation rejected: cannot read --report-path {report_path!r}: {exc}",
                      file=sys.stderr)
                sys.exit(1)
    elif report_json:
        src = "--report-json"
        raw = report_json

    if raw is None or not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"validation rejected: {src} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(obj, dict):
        print(f"validation rejected: {src} must be a JSON object (the Lens report), "
              f"got {type(obj).__name__}", file=sys.stderr)
        sys.exit(1)
    return obj


def cmd_validation_add(args: argparse.Namespace) -> None:
    """Record a Lens validation row in validation_log.

    Lens calls this as its last action before returning NEXUS:DONE so that
    lens-gate.sh can find the matching row for the implementer's task hash.

    OPT-038: when a structured report is supplied (--report-path / --report-json
    / stdin), the stored verdict is DERIVED from the report's criteria_results[]
    and deterministic[] exit codes — a claimed PASS over any FAIL signal is
    downgraded to FAIL (or PARTIAL) and recorded as such, so Lens can no longer
    grade its own homework. With no report the claim is stored but flagged
    UNBACKED so the absence of evidence is visible to an auditor.

    U1 instrumentation columns (all optional, nullable):
      --files-changed-json  JSON array of the implementer's declared files_changed.
      --dispatch-started-at ISO-8601 UTC stamp of when the Lens dispatch began.
    revise_reason is auto-populated from binding_note when verdict != PASS.
    """
    now = _now()
    task_hash = args.task_hash
    claimed = args.verdict.upper()
    if claimed not in _VALID_VERDICTS:
        print(
            f"validation rejected: verdict must be PASS, PARTIAL, or FAIL (got {args.verdict!r})",
            file=sys.stderr,
        )
        sys.exit(1)

    report = _load_report_for_validation(args)
    verdict, backed, binding_note = derive_verdict_from_report(report, claimed)

    # --strict refuses to record a contradicted PASS rather than silently
    # downgrading it — surfaces the lie to the caller instead of absorbing it.
    if getattr(args, "strict", False) and backed and verdict != claimed:
        print(
            f"validation REJECTED (--strict): claimed {claimed} but evidence derives "
            f"{verdict}. {binding_note}",
            file=sys.stderr,
        )
        sys.exit(1)

    summary = args.summary or ""
    evidence_summary = f"{summary} [{binding_note}]".strip() if binding_note else summary

    # U1 instrumentation: revise_reason is set when the stored verdict != PASS
    # so the failure reason is machine-readable without re-parsing evidence_summary.
    files_changed_json = getattr(args, "files_changed_json", None) or None
    dispatch_started_at = getattr(args, "dispatch_started_at", None) or None
    lens_type = getattr(args, "lens_type", None) or None
    risk_tier = getattr(args, "risk_tier", None) or None
    revise_reason: str | None = None
    if verdict != "PASS" and binding_note:
        revise_reason = binding_note[:500]  # cap at 500 chars for sanity

    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None
        conn.execute(
            """CREATE TABLE IF NOT EXISTS validation_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          TEXT,
                agent_validated     TEXT NOT NULL,
                target_agent        TEXT NOT NULL,
                task_or_brief_hash  TEXT NOT NULL,
                verdict             TEXT NOT NULL,
                evidence_summary    TEXT,
                validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_validation_target
               ON validation_log(target_agent, validated_at DESC)""",
        )
        # Ensure the U1 nullable columns exist (idempotent — no-op on fresh DBs
        # where _migrate_validation_log_columns already ran via cmd_init).
        _migrate_validation_log_columns(conn)
        conn.execute(
            """INSERT INTO validation_log
               (session_id, agent_validated, target_agent, task_or_brief_hash, verdict,
                evidence_summary, validated_at,
                files_changed_json, revise_reason, dispatch_started_at,
                lens_type, risk_tier)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, args.agent, args.target, task_hash, verdict,
             evidence_summary, now,
             files_changed_json, revise_reason, dispatch_started_at,
             lens_type, risk_tier),
        )
    print(json.dumps({
        "recorded": True,
        "agent_validated": args.agent,
        "target_agent": args.target,
        "task_or_brief_hash": task_hash,
        "claimed_verdict": claimed,
        "verdict": verdict,
        "evidence_backed": backed,
        "downgraded": backed and verdict != claimed,
        "binding": binding_note,
        "validated_at": now,
        "files_changed_json": files_changed_json,
        "dispatch_started_at": dispatch_started_at,
        "lens_type": lens_type,
        "risk_tier": risk_tier,
    }))


def cmd_validation_completeness_check(args: argparse.Namespace) -> None:
    """Assert the latest in-window lens PASS row covers the declared files_changed set.

    rc=0  — a PASS row exists within the 1-hour window whose files_changed_json
            is a superset of (or equal to) every path in --files-changed-json AND
            no open-deferral condition is flagged.
    rc=2  — no qualifying row found, or the row's files_changed_json does not
            cover the requested set; stderr names the missing paths.

    Interface pin (PASS_ROW_QUERY, files-changed-keyed variant):
      SELECT verdict, files_changed_json FROM validation_log
      WHERE agent_validated='lens'
        [AND task_or_brief_hash=?]
        AND datetime(validated_at) > datetime('now','-1 hours')
      ORDER BY datetime(validated_at) DESC, id DESC
      LIMIT 1
    Always uses datetime() comparisons (not lexical) to avoid space-vs-T trap.
    1-hour window matches lens-gate VALIDATION_WINDOW=timedelta(hours=1).
    """
    try:
        requested_files: list = json.loads(args.files_changed_json)
    except (json.JSONDecodeError, ValueError) as exc:
        print(
            f"completeness-check: --files-changed-json is not valid JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    if not isinstance(requested_files, list):
        print(
            "completeness-check: --files-changed-json must be a JSON array",
            file=sys.stderr,
        )
        sys.exit(2)
    requested_set = {str(f) for f in requested_files}

    with _conn() as conn:
        # Ensure table exists (no-op on established DBs).
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='validation_log'"
        ).fetchone():
            print(
                "completeness-check: validation_log table does not exist — run `init` first",
                file=sys.stderr,
            )
            sys.exit(2)

        # Ensure new columns (files_changed_json, revise_reason, dispatch_started_at)
        # exist before any SELECT that references them.  On legacy DBs that predate
        # the migration this would otherwise crash with OperationalError.
        _migrate_validation_log_columns(conn)

        task_hash = getattr(args, "task_hash", None) or None
        if task_hash:
            row = conn.execute(
                "SELECT verdict, files_changed_json FROM validation_log "
                "WHERE agent_validated='lens' AND task_or_brief_hash=? "
                "AND datetime(validated_at) > datetime('now','-1 hours') "
                "ORDER BY datetime(validated_at) DESC, id DESC LIMIT 1",
                (task_hash,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT verdict, files_changed_json FROM validation_log "
                "WHERE agent_validated='lens' "
                "AND datetime(validated_at) > datetime('now','-1 hours') "
                "ORDER BY datetime(validated_at) DESC, id DESC LIMIT 1",
            ).fetchone()

    if row is None:
        print(
            "completeness-check: no lens PASS row found in the 1-hour window"
            + (f" for task-hash {task_hash!r}" if task_hash else ""),
            file=sys.stderr,
        )
        sys.exit(2)

    if row["verdict"] != "PASS":
        print(
            f"completeness-check: latest in-window lens row has verdict={row['verdict']!r}, not PASS",
            file=sys.stderr,
        )
        sys.exit(2)

    stored_json = row["files_changed_json"]
    if not stored_json:
        # Row predates U1 or Lens did not supply --files-changed-json — the
        # coverage cannot be verified. Treat as covered (legacy compat) but warn.
        print(
            "completeness-check: PASS row found but files_changed_json is NULL "
            "(pre-U1 row or missing --files-changed-json on `validation add`); "
            "coverage assumed OK.",
            file=sys.stderr,
        )
        sys.exit(0)

    try:
        stored_files: list = json.loads(stored_json)
    except (json.JSONDecodeError, ValueError):
        print(
            "completeness-check: stored files_changed_json is not valid JSON — "
            "treating as uncovered",
            file=sys.stderr,
        )
        sys.exit(2)
    covered_set = {str(f) for f in stored_files}
    missing = sorted(requested_set - covered_set)
    if missing:
        print(
            "completeness-check: FAIL — PASS row does not cover these files:\n"
            + "\n".join(f"  {p}" for p in missing),
            file=sys.stderr,
        )
        sys.exit(2)

    print(json.dumps({
        "ok": True,
        "covered_files": sorted(requested_set),
        "pass_row_files": sorted(covered_set),
    }))


def cmd_validation_check_gate(args: argparse.Namespace) -> None:
    """Read-only lens-gate.sh SubagentStop check (ADR-001 Phase 0): the v1
    floor (>=1 in-window PASS row) plus, for --tier T2, the v2 N-distinct-
    lens-row check (R1-T08) — routed through log.py's single-writer
    connection instead of the hook opening its OWN raw sqlite3.connect and
    racing concurrent hooks on schema-init DDL (the incident #10 mechanism).

    Deliberately NO CREATE TABLE / ALTER TABLE here: this command never runs
    schema-init DDL (that stays in `init` / `validation add`, both already
    single-writer). A DB predating validation_log, or predating the U1
    lens_type/risk_tier columns, degrades EXACTLY like the hook's own old
    column-existence guard (v2 no-ops to True) rather than racing a DDL
    statement — many concurrent `check-gate` invocations are many concurrent
    READERS, which WAL supports natively; they are never writers.

    Prints ONE JSON object to stdout and always exits 0 — this command only
    ever reports facts (validated / v2_ok / v2_detail / db_error); the
    calling hook still owns the ALLOW/DENY decision, unchanged from before
    (a db_error is treated by the hook exactly like the old sqlite3.Error
    branch: fail CLOSED, never a silent pass).

    Query shapes are byte-identical to lens-gate.sh's former
    _has_lens_validation / _has_lens_validation_v2 (same WHERE clauses, same
    datetime() window comparison, same NULL-tier-never-matches rule) — this
    is a routing change, not an enforcement change.
    """
    target = args.target
    task_hash = args.task_hash
    tier = args.tier
    window_hours = 1  # mirrors lens-gate.sh VALIDATION_WINDOW=timedelta(hours=1)

    result = {
        "target": target,
        "task_hash": task_hash,
        "tier": tier,
        "validated": False,
        "v2_ok": True,
        "v2_detail": (
            "tier=T1 — v1 floor only (unchanged)" if tier != "T2" else None
        ),
        "db_error": None,
    }

    try:
        conn = sqlite3.connect(DB_PATH)
        _harden_connection(conn)
        conn.row_factory = sqlite3.Row

        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='validation_log'"
        ).fetchone()
        if not exists:
            result["db_error"] = "validation_log table does not exist — run `init` first"
            conn.close()
            print(json.dumps(result))
            return

        row = conn.execute(
            f"""
            SELECT verdict FROM validation_log
            WHERE agent_validated = 'lens'
              AND target_agent    = ?
              AND task_or_brief_hash = ?
              AND datetime(validated_at) > datetime('now', '-{window_hours} hours')
            ORDER BY datetime(validated_at) DESC, id DESC
            LIMIT 1
            """,
            (target, task_hash),
        ).fetchone()
        result["validated"] = row is not None and row["verdict"] == "PASS"

        if tier == "T2":
            cols = {r[1] for r in conn.execute("PRAGMA table_info(validation_log)")}
            if "lens_type" not in cols or "risk_tier" not in cols:
                result["v2_ok"] = True
                result["v2_detail"] = (
                    "lens_type/risk_tier columns absent (pre-migration DB) — "
                    "v2 check skipped, v1 floor governs"
                )
            else:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT lens_type FROM validation_log
                    WHERE agent_validated = 'lens'
                      AND target_agent    = ?
                      AND task_or_brief_hash = ?
                      AND verdict = 'PASS'
                      AND lens_type IS NOT NULL
                      AND datetime(validated_at) > datetime('now', '-{window_hours} hours')
                    """,
                    (target, task_hash),
                ).fetchall()
                satisfied = {r["lens_type"] for r in rows}
                if "T2" not in satisfied:
                    result["v2_ok"] = False
                    result["v2_detail"] = (
                        f"required tiers=['T2'], satisfied={sorted(satisfied)}, missing=['T2']"
                    )
                else:
                    result["v2_ok"] = True
                    result["v2_detail"] = (
                        f"required tiers=['T2'], satisfied={sorted(satisfied)}"
                    )
        conn.close()
    except sqlite3.Error as exc:
        result["db_error"] = str(exc)

    print(json.dumps(result))


def cmd_session_bump_message_count(_args: argparse.Namespace) -> None:
    """Atomically increment the latest open session's user_message_count.

    ADR-001 Phase 0: replaces context-reset-monitor.py's own raw
    sqlite3.connect + UPDATE — the single highest-frequency independent
    writer in the old inventory (it fired on EVERY UserPromptSubmit, not
    just SubagentStop/PostToolUse boundaries). Routing the read-increment-
    write through the single-writer connection removes it from the
    concurrent-writer set entirely; the hook now only ever reads this
    command's JSON result.

    No session row open -> user_message_count stays None (hook's discontinuity
    check already treats an absent session as "nothing to count"). DB errors
    are reported as JSON, never raised, so the caller (an advisory hook that
    must never block) can warn without crashing on a broken DB.
    """
    result = {
        "session_id": None,
        "previous_count": None,
        "user_message_count": None,
        "db_error": None,
    }
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT id, user_message_count FROM sessions "
                "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                print(json.dumps(result))
                return
            sid = row["id"]
            previous_count = row["user_message_count"] or 0
            new_count = previous_count + 1
            conn.execute(
                "UPDATE sessions SET user_message_count = ? WHERE id = ?",
                (new_count, sid),
            )
        result["session_id"] = sid
        result["previous_count"] = previous_count
        result["user_message_count"] = new_count
    except sqlite3.Error as exc:
        result["db_error"] = str(exc)
    print(json.dumps(result))


def cmd_notepad_add(args: argparse.Namespace) -> None:
    note = args.note.strip()

    if len(note) > 500:
        print(
            f"notepad rejected: note is {len(note)} chars — max is 500. "
            "Notepad entries must be concise. Trim or split across multiple adds.",
            file=sys.stderr,
        )
        sys.exit(1)

    if _is_status_restatement(note):
        print(
            "notepad rejected: this looks like a status update, not context for the next agent. "
            "Status goes in task update / NEXUS:DONE. Notepad is for nuances, gotchas, reminders, "
            "or 'the next agent should know X'.",
            file=sys.stderr,
        )
        sys.exit(1)

    kind = args.kind or "fyi"
    if kind not in _NOTEPAD_VALID_KINDS:
        print(
            f"notepad rejected: invalid kind '{kind}'. "
            f"Allowed: {', '.join(sorted(_NOTEPAD_VALID_KINDS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None

        conn.execute(
            """INSERT INTO agent_notepad (topic, agent_name, session_id, written_at, note, note_kind)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (args.topic, args.agent, session_id, now, note, kind),
        )
        inserted_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        # Rolling window: keep only the 5 most recent entries per topic.
        conn.execute(
            """DELETE FROM agent_notepad
               WHERE topic = ?
               AND id NOT IN (
                   SELECT id FROM agent_notepad WHERE topic = ? ORDER BY written_at DESC LIMIT 5
               )""",
            (args.topic, args.topic),
        )

    print(json.dumps({"notepad_id": inserted_id, "topic": args.topic, "kind": kind, "written_at": now}))


def cmd_notepad_list(args: argparse.Namespace) -> None:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT agent_name, written_at, note_kind, note
               FROM agent_notepad
               WHERE topic = ?
               ORDER BY written_at ASC""",
            (args.topic,),
        ).fetchall()

    if not rows:
        print(f"notepad for {args.topic}: (empty)")
        return

    count = len(rows)
    label = "5 most recent" if count >= 5 else f"{count} entr{'y' if count == 1 else 'ies'}"
    print(f"notepad for {args.topic} ({label}):\n")
    for r in rows:
        agent = r["agent_name"].ljust(8)
        ts_raw = r["written_at"]
        # Normalise to "YYYY-MM-DD HH:MM" (drop seconds + tz suffix)
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            ts = ts_raw[:16]
        kind = r["note_kind"]
        note = r["note"]
        print(f"[{agent} · {ts} · {kind}] {note}")


def cmd_notepad_clear(args: argparse.Namespace) -> None:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM agent_notepad WHERE topic = ?", (args.topic,))
        deleted = cur.rowcount
    print(json.dumps({"topic": args.topic, "deleted": deleted}))


# ---------------------------------------------------------------------------
# subagent-return (Mitigation A — auto-summarize-and-purge)
# ---------------------------------------------------------------------------

_SUBAGENT_RETURNS_DIR = Path(__file__).parent / "subagent-returns"

# Approximate token→char ratio (conservative: 1 token ≈ 4 chars).
_TOKEN_APPROX_CHARS = 4
_MIN_TOKENS_TO_PERSIST = 1_000  # skip tiny responses

# Patterns scanned in priority order to extract a compact insight.
_SUMMARY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nexus_marker",  re.compile(r"##\s*(NEXUS:[A-Z\-]+)", re.MULTILINE)),
    ("root_cause",    re.compile(r"##\s*Root Cause.*?\n(.*?)(?=\n##|\Z)", re.DOTALL)),
    ("files_changed", re.compile(r'"files_changed"\s*:\s*(\[[^\]]*\])')),
    ("acceptance",    re.compile(r'"acceptance_met"\s*:\s*(\[[^\]]*\])')),
    ("verdict",       re.compile(r'"verdict"\s*:\s*"([^"]+)"')),
    ("blockers",      re.compile(r'"blockers"\s*:\s*(\[[^\]]*\])')),
]

_MAX_INSIGHT_CHARS = 490  # leave slack under the 500-char notepad limit


def _extract_insight(text: str) -> str:
    """Scan text for high-signal markers; return a ≤490-char summary line."""
    parts: list[str] = []

    for label, pat in _SUMMARY_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        captured = m.group(1).strip()
        if not captured or captured in ("[]", "null", ""):
            continue
        # Flatten JSON arrays to comma-joined strings.
        if captured.startswith("["):
            try:
                items = json.loads(captured)
                captured = ", ".join(str(i) for i in items if i)
            except (json.JSONDecodeError, TypeError):
                pass
        # Trim each captured segment.
        captured = captured[:120].rstrip()
        parts.append(f"{label}={captured}")
        if sum(len(p) for p in parts) > _MAX_INSIGHT_CHARS - 20:
            break

    if not parts:
        # Fallback: first non-empty line that looks substantive.
        for line in text.splitlines():
            line = line.strip()
            if len(line) > 30 and not line.startswith("{") and not line.startswith("#!"):
                parts.append(line[:_MAX_INSIGHT_CHARS])
                break

    insight = " | ".join(parts)
    return insight[:_MAX_INSIGHT_CHARS] if insight else "(no extractable insight)"


def _derive_topic(agent: str, text: str) -> str:
    """Derive a notepad topic from the agent persona + any task/feat ID found."""
    m = re.search(r"\b(TASK-\d+|FEAT-\d+)\b", text)
    suffix = m.group(1).lower() if m else "return"
    return f"{agent}-{suffix}"


def cmd_subagent_return_record(args: argparse.Namespace) -> None:
    """Read a full subagent response, persist it to disk, and notepad the summary."""
    if args.full_response_file:
        full_text = Path(args.full_response_file).read_text()
    else:
        full_text = sys.stdin.read()

    approx_tokens = len(full_text) // _TOKEN_APPROX_CHARS

    if approx_tokens < _MIN_TOKENS_TO_PERSIST:
        # Tiny response — not worth persisting; skip silently.
        print(json.dumps({"skipped": True, "reason": "response too small", "approx_tokens": approx_tokens}))
        return

    # Persist full text to disk.
    _SUBAGENT_RETURNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    # Determine session prefix from open session.
    session_prefix = "nosession"
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                session_prefix = row["id"]
    except sqlite3.Error:
        pass

    filename = f"{session_prefix}-{args.agent}-{ts}.txt"
    dest = _SUBAGENT_RETURNS_DIR / filename
    dest.write_text(full_text)

    # Extract compact insight.
    insight = _extract_insight(full_text)
    topic = _derive_topic(args.agent, full_text)
    # Prefix with agent identity so the notepad restatement guard doesn't reject
    # entries that contain words like "done" — this is explicitly context, not status.
    note = f"[{args.agent}] {insight} · persisted: .memory/subagent-returns/{filename}"

    # Clamp note to 500 chars (notepad hard limit).
    if len(note) > 500:
        note = note[:497] + "..."

    # Write to notepad.
    notepad_args = argparse.Namespace(
        topic=topic,
        agent=args.agent,
        note=note,
        kind="fyi",
    )
    import contextlib
    with contextlib.suppress(SystemExit):
        cmd_notepad_add(notepad_args)

    print(json.dumps({
        "persisted": str(dest),
        "approx_tokens": approx_tokens,
        "topic": topic,
        "insight": insight,
    }))


# ---------------------------------------------------------------------------
# repair-orphans (maintenance — fix doubled NATIVE- prefix orphans)
# ---------------------------------------------------------------------------

def cmd_task_repair_orphans(_args: argparse.Namespace) -> None:
    """Find and fix tasks whose id has a doubled NATIVE- prefix (e.g. NATIVE-NATIVE-17).

    For each orphan:
      - compute canonical id via native_task_db_id (strips repeated prefixes)
      - if canonical already exists → DELETE the orphan
      - else → UPDATE the orphan's id to canonical

    Idempotent: safe to run multiple times.
    """
    actions: list[dict] = []
    with _conn() as conn:
        orphans = conn.execute(
            "SELECT id, title, status FROM tasks WHERE id LIKE 'NATIVE-NATIVE%'"
        ).fetchall()
        for row in orphans:
            orphan_id = row["id"]
            canonical_id = native_task_db_id(orphan_id)
            existing = conn.execute(
                "SELECT id FROM tasks WHERE id=?", (canonical_id,)
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM tasks WHERE id=?", (orphan_id,))
                actions.append({
                    "orphan": orphan_id,
                    "canonical": canonical_id,
                    "action": "deleted",
                    "reason": "canonical already exists",
                })
                print(f"  DELETE {orphan_id} (canonical {canonical_id} already exists)")
            else:
                conn.execute(
                    "UPDATE tasks SET id=? WHERE id=?", (canonical_id, orphan_id)
                )
                actions.append({
                    "orphan": orphan_id,
                    "canonical": canonical_id,
                    "action": "renamed",
                })
                print(f"  RENAME {orphan_id} -> {canonical_id}")
    print(json.dumps({"repaired": len(actions), "actions": actions}, indent=2))


# ---------------------------------------------------------------------------
# task TTL + archive policy (R5-T01 remainder, N56)
# ---------------------------------------------------------------------------
# DESIGN NOTE (the "design doc" the N56 goal calls for — kept inline because
# this node's write_scope has no dedicated design-doc path): a stale OPEN
# task (todo/in_progress/blocked) is ARCHIVED, never deleted (DEC-005 — every
# row stays queryable; `task list` applies no status filter by default, and
# `context dump` counts archived rows separately from the open-task summary
# below rather than folding them back into "open"). Archiving is a plain
# status transition (status='archived') on the existing `tasks.status`
# column — no schema.sql migration, since the column already has no CHECK
# constraint pinning it to a fixed value set.
#
# TTL DEFAULTS PER ORIGIN — origin is inferred from the id-prefix convention
# already established by the native-task mirror above (native_task_db_id):
#   native        NATIVE-<n>[-<k>]  — mirrors a Claude Code session's own
#                                     TaskCreate/TaskUpdate panel (#24). That
#                                     panel is session-scoped and rarely
#                                     closed out explicitly once the session
#                                     ends, so these rows go stale fastest.
#                                     Default 14 days.
#   hand-authored TASK-<n>          — the human-queued roadmap/backlog
#                                     surface (`task add`). Someone queued it
#                                     on purpose; default stays long-lived.
#                                     Default 90 days.
#   other         anything else     — a custom --id or a future id
#                                     convention this policy doesn't yet
#                                     know about. Conservative middle
#                                     default. 30 days.
# PER-TASK OVERRIDE — a task's free-text `notes` field may carry an inline
# `[ttl_days=N]` marker; `tasks reap` always prefers it over the origin
# default. This purposely avoids a schema column for the same
# no-schema-migration reason above.
TASK_TTL_DEFAULT_DAYS = {
    "native": 14,
    "hand-authored": 90,
    "other": 30,
}

# Statuses `tasks reap` treats as "open" (i.e. eligible for staleness).
# done/cancelled/archived are terminal and never re-evaluated.
TASK_REAP_OPEN_STATUSES = ("todo", "in_progress", "blocked")

_TASK_TTL_OVERRIDE_RE = re.compile(r"\[ttl_days=(\d+)\]")


def _task_origin(task_id: str) -> str:
    """Classify a task id's origin for TTL-default purposes (id-prefix
    heuristic — there is no schema column for origin, see design note above)."""
    tid = (task_id or "").strip().upper()
    if re.match(r"^NATIVE-", tid):
        return "native"
    if re.match(r"^TASK-", tid):
        return "hand-authored"
    return "other"


def _task_ttl_override_days(notes: str | None) -> int | None:
    """Parse a per-task `[ttl_days=N]` override marker out of `notes`, if present."""
    if not notes:
        return None
    m = _TASK_TTL_OVERRIDE_RE.search(notes)
    return int(m.group(1)) if m else None


def _task_ttl_days(task_id: str, notes: str | None) -> int:
    """Effective TTL for a task: per-task override wins, else the per-origin default."""
    override = _task_ttl_override_days(notes)
    if override is not None:
        return override
    return TASK_TTL_DEFAULT_DAYS[_task_origin(task_id)]


def cmd_tasks_reap(args: argparse.Namespace) -> None:
    """TTL-based stale-task archival (R5-T01 remainder, N56).

    Archives (status -> 'archived') every OPEN task (TASK_REAP_OPEN_STATUSES)
    whose `updated_at` is older than its effective TTL (see _task_ttl_days).
    NEVER deletes a row (DEC-005): the prior status + a dated reason are
    appended to `notes`, and the row remains fully queryable via `task list`
    or `context dump`. `--dry-run` previews the candidate set without
    writing anything.
    """
    now_dt = datetime.now(timezone.utc)  # noqa: UP017
    now = _now()
    dry_run = bool(getattr(args, "dry_run", False))
    with _conn() as conn:
        placeholders = ",".join("?" for _ in TASK_REAP_OPEN_STATUSES)
        rows = conn.execute(
            f"SELECT id, status, updated_at, notes FROM tasks WHERE status IN ({placeholders})",
            TASK_REAP_OPEN_STATUSES,
        ).fetchall()
        candidates: list[dict] = []
        for r in rows:
            updated_at = r["updated_at"]
            try:
                updated_dt = datetime.fromisoformat(updated_at)
            except (TypeError, ValueError):
                continue  # malformed timestamp -- never reap on ambiguous data
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)  # noqa: UP017
            ttl_days = _task_ttl_days(r["id"], r["notes"])
            age_days = (now_dt - updated_dt).total_seconds() / 86400.0
            if age_days < ttl_days:
                continue
            candidates.append({
                "task_id": r["id"],
                "prior_status": r["status"],
                "ttl_days": ttl_days,
                "age_days": round(age_days, 1),
            })
            if dry_run:
                continue
            reason = (
                f"[archived {now}: stale {round(age_days, 1)}d > ttl {ttl_days}d, "
                f"prior_status={r['status']}]"
            )
            new_notes = f"{r['notes']}\n{reason}" if r["notes"] else reason
            conn.execute(
                "UPDATE tasks SET status='archived', notes=?, updated_at=? WHERE id=?",
                (new_notes, now, r["id"]),
            )
    if dry_run:
        print(json.dumps(
            {"dry_run": True, "would_archive_count": len(candidates), "would_archive": candidates},
            indent=2,
        ))
    else:
        print(json.dumps(
            {"dry_run": False, "archived_count": len(candidates), "archived": candidates},
            indent=2,
        ))


# ---------------------------------------------------------------------------
# improvements — nexus-improvement backlog (evaluated-vs-unread tracker)
# ---------------------------------------------------------------------------
# DB-as-truth store: distilled research notes with relevance_to_nexus >= a
# THRESHOLD are auto-inserted as review_state='unread'; the user promotes a
# subset to 'flagged'. `populate` is idempotent and NEVER downgrades a row that
# a human has already moved off 'unread' (evaluated/flagged/dismissed).

# Distilled source notes live here. process-inbox.py files notes into this dir.
IMPROVEMENTS_SOURCES_DIR = (
    Path(__file__).resolve().parent.parent
    / "research" / "10-knowledge" / "ai-techniques" / "research" / "collection" / "sources"
)
# Dashboard note Obsidian renders.
IMPROVEMENTS_DASHBOARD_PATH = (
    Path(__file__).resolve().parent.parent / "research" / "00-meta" / "NEXUS-IMPROVEMENTS.md"
)
# review_state values a human owns — populate must never overwrite these.
_IMPROVEMENTS_HUMAN_STATES = ("evaluated", "flagged", "dismissed")
_IMPROVEMENTS_VALID_STATES = ("unread", "evaluated", "flagged", "dismissed")

_IMPROVEMENTS_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def improvements_threshold() -> int:
    """Relevance gate for auto-population. Default 4; override via env.

    The user tunes this after seeing the first unread-list size — keeping it
    env-overridable avoids a code edit per experiment (>=4 vs >=3).
    """
    raw = os.environ.get("NEXUS_IMPROVEMENTS_THRESHOLD")
    if raw is None:
        return 4
    try:
        return int(raw)
    except ValueError:
        return 4


def _improvements_parse_frontmatter(text: str) -> dict[str, object]:
    """Parse the YAML frontmatter block of a note. Returns {} when absent/bad."""
    match = _IMPROVEMENTS_FM_RE.match(text)
    if not match:
        return {}
    try:
        import yaml  # noqa: PLC0415

        loaded = yaml.safe_load(match.group(1))
    except Exception:  # noqa: BLE001 — a malformed note must not crash the scan
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _improvements_coerce_score(value: object) -> int | None:
    """Coerce a frontmatter relevance value to an int score, or None if unrated."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def improvements_note_meta(note_path: Path) -> dict[str, object] | None:
    """Read a distilled note's frontmatter into a backlog-row metadata dict.

    Returns None when the file is unreadable. The score is read from the
    DURABLE persisted frontmatter (`relevance_to_nexus`), not from any in-memory
    distillation dict — so populate and the auto-populate hook agree.
    """
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm = _improvements_parse_frontmatter(text)
    raw_tags = fm.get("tags", [])
    if isinstance(raw_tags, list):
        tags = ",".join(str(t) for t in raw_tags)
    elif isinstance(raw_tags, str):
        tags = raw_tags
    else:
        tags = ""
    # A distilled note carries a "## Claims" section with evidence sub-bullets.
    evidence_present = 1 if "## Claims" in text else 0
    return {
        "source_url": str(fm.get("source_url") or ""),
        "title": str(fm.get("title") or note_path.stem),
        "relevance_score": _improvements_coerce_score(fm.get("relevance_to_nexus")),
        "tags": tags,
        "evidence_present": evidence_present,
    }


def _improvements_rel_path(note_path: Path) -> str:
    """Repo-relative path string for the UNIQUE note_path key (stable across runs)."""
    repo_root = Path(__file__).resolve().parent.parent
    try:
        return str(note_path.resolve().relative_to(repo_root))
    except ValueError:
        return str(note_path)


def _improvements_upsert_row(
    conn: sqlite3.Connection,
    rel_path: str,
    score: int,
    meta: dict[str, object],
) -> str:
    """Core upsert: insert 'unread', refresh metadata, NEVER downgrade a human state.

    Caller has already applied the relevance gate. Returns
    'inserted' | 'refreshed' | 'preserved'.
    """
    now = _now()
    existing = conn.execute(
        "SELECT review_state FROM nexus_improvements WHERE note_path = ?",
        (rel_path,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """INSERT INTO nexus_improvements
                 (note_path, source_url, title, relevance_score, review_state,
                  evidence_present, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'unread', ?, ?, ?, ?)""",
            (
                rel_path,
                meta.get("source_url", ""),
                meta.get("title", ""),
                score,
                meta.get("evidence_present", 0),
                meta.get("tags", ""),
                now,
                now,
            ),
        )
        return "inserted"

    # Refresh metadata only; review_state is left exactly as it is, so a row a
    # human moved to evaluated/flagged/dismissed is never reset to 'unread'.
    conn.execute(
        """UPDATE nexus_improvements
             SET source_url = ?, title = ?, relevance_score = ?,
                 evidence_present = ?, tags = ?, updated_at = ?
           WHERE note_path = ?""",
        (
            meta.get("source_url", ""),
            meta.get("title", ""),
            score,
            meta.get("evidence_present", 0),
            meta.get("tags", ""),
            now,
            rel_path,
        ),
    )
    return "preserved" if existing["review_state"] in _IMPROVEMENTS_HUMAN_STATES else "refreshed"


def improvements_upsert_note(
    conn: sqlite3.Connection,
    note_path: Path,
    *,
    threshold: int | None = None,
) -> str:
    """Upsert one distilled note into nexus_improvements by reading its frontmatter.

    Gate: only inserts when the note's persisted relevance_to_nexus >= threshold.
    No-downgrade: an existing human-owned review_state is preserved. Idempotent.

    Returns: 'inserted' | 'refreshed' | 'preserved' | 'below_threshold' | 'unrated'.
    """
    thr = improvements_threshold() if threshold is None else threshold
    meta = improvements_note_meta(note_path)
    if meta is None:
        return "below_threshold"
    score = meta["relevance_score"]
    if score is None:
        return "unrated"
    if not isinstance(score, int) or score < thr:
        return "below_threshold"
    return _improvements_upsert_row(conn, _improvements_rel_path(note_path), score, meta)


def improvements_upsert_meta(
    conn: sqlite3.Connection,
    note_path: Path,
    *,
    relevance_score: object,
    source_url: str = "",
    title: str = "",
    tags: object = "",
    evidence_present: int = 0,
    threshold: int | None = None,
) -> str:
    """Upsert from EXPLICIT metadata — the process-inbox auto-populate hook path.

    The freshly distilled note's integer relevance lives in the pipeline's
    in-memory frontmatter (not always re-serialised to the note's YAML), so the
    hook passes it directly here rather than re-reading the file. Same gate +
    no-downgrade semantics as improvements_upsert_note.

    Returns: 'inserted' | 'refreshed' | 'preserved' | 'below_threshold' | 'unrated'.
    """
    thr = improvements_threshold() if threshold is None else threshold
    score = _improvements_coerce_score(relevance_score)
    if score is None:
        return "unrated"
    if score < thr:
        return "below_threshold"
    if isinstance(tags, (list, tuple)):
        tags_str = ",".join(str(t) for t in tags)
    else:
        tags_str = str(tags or "")
    meta: dict[str, object] = {
        "source_url": source_url or "",
        "title": title or note_path.stem,
        "tags": tags_str,
        "evidence_present": evidence_present,
    }
    return _improvements_upsert_row(conn, _improvements_rel_path(note_path), score, meta)


def _improvements_resolve_note_arg(note: str) -> Path:
    """Resolve a `flag <note>` argument to a path: accept a basename or rel path."""
    candidate = Path(note)
    if candidate.exists():
        return candidate
    by_name = IMPROVEMENTS_SOURCES_DIR / note
    if by_name.exists():
        return by_name
    if not note.endswith(".md"):
        by_name_md = IMPROVEMENTS_SOURCES_DIR / f"{note}.md"
        if by_name_md.exists():
            return by_name_md
    return candidate


def cmd_improvements_populate(args: argparse.Namespace) -> None:
    threshold = getattr(args, "threshold", None) or improvements_threshold()
    sources_dir = IMPROVEMENTS_SOURCES_DIR
    if not sources_dir.is_dir():
        print(json.dumps({"scanned": 0, "inserted": 0, "note": f"no sources dir at {sources_dir}"}))
        return
    counts = {"inserted": 0, "refreshed": 0, "preserved": 0, "below_threshold": 0, "unrated": 0}
    scanned = 0
    with _conn() as conn:
        for note_path in sorted(sources_dir.glob("*.md")):
            if note_path.name.startswith((".", "_")):
                continue
            scanned += 1
            action = improvements_upsert_note(conn, note_path, threshold=threshold)
            counts[action] = counts.get(action, 0) + 1
    print(json.dumps({"scanned": scanned, "threshold": threshold, **counts}, indent=2))


def _improvements_fetch_rows(state: str) -> list[sqlite3.Row]:
    with _conn() as conn:
        if state == "all":
            return conn.execute(
                """SELECT * FROM nexus_improvements
                   ORDER BY review_state, relevance_score DESC, updated_at DESC"""
            ).fetchall()
        return conn.execute(
            """SELECT * FROM nexus_improvements WHERE review_state = ?
               ORDER BY relevance_score DESC, updated_at DESC""",
            (state,),
        ).fetchall()


def cmd_improvements_list(args: argparse.Namespace) -> None:
    state = getattr(args, "state", None) or "unread"
    rows = _improvements_fetch_rows(state)
    if not rows:
        print(f"nexus-improvements [{state}]: (empty)")
        return
    print(f"nexus-improvements [{state}] — {len(rows)} item(s):\n")
    for r in rows:
        rel = r["relevance_score"] if r["relevance_score"] is not None else "?"
        line = f"[{r['review_state']:<9} · rel={rel}] {r['title']}"
        print(line)
        if r["source_url"]:
            print(f"    {r['source_url']}")
        if r["flag_note"]:
            print(f"    flag: {r['flag_note']}")
        print(f"    note: {r['note_path']}")


def _improvements_set_state(note: str, state: str, flag_note: str | None) -> None:
    if state not in _IMPROVEMENTS_VALID_STATES:
        print(f"invalid review_state '{state}'.", file=sys.stderr)
        sys.exit(1)
    note_path = _improvements_resolve_note_arg(note)
    rel_path = _improvements_rel_path(note_path)
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM nexus_improvements WHERE note_path = ?", (rel_path,)
        ).fetchone()
        if row is None:
            # Allow flagging a note that is not yet on the backlog (manual add).
            meta = improvements_note_meta(note_path)
            if meta is None:
                print(
                    f"note not found on backlog and unreadable on disk: {note}",
                    file=sys.stderr,
                )
                sys.exit(1)
            conn.execute(
                """INSERT INTO nexus_improvements
                     (note_path, source_url, title, relevance_score, review_state,
                      flag_note, evidence_present, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rel_path,
                    meta["source_url"],
                    meta["title"],
                    meta["relevance_score"],
                    state,
                    flag_note,
                    meta["evidence_present"],
                    meta["tags"],
                    now,
                    now,
                ),
            )
        elif flag_note is not None:
            conn.execute(
                "UPDATE nexus_improvements SET review_state = ?, flag_note = ?, updated_at = ? WHERE note_path = ?",
                (state, flag_note, now, rel_path),
            )
        else:
            conn.execute(
                "UPDATE nexus_improvements SET review_state = ?, updated_at = ? WHERE note_path = ?",
                (state, now, rel_path),
            )
    print(json.dumps({"note_path": rel_path, "review_state": state, "flag_note": flag_note}))


def cmd_improvements_flag(args: argparse.Namespace) -> None:
    _improvements_set_state(args.note, "flagged", getattr(args, "note_text", None))


def cmd_improvements_evaluate(args: argparse.Namespace) -> None:
    _improvements_set_state(args.note, "evaluated", getattr(args, "note_text", None))


def cmd_improvements_dismiss(args: argparse.Namespace) -> None:
    _improvements_set_state(args.note, "dismissed", getattr(args, "note_text", None))


def _improvements_render_dashboard() -> str:
    unread = _improvements_fetch_rows("unread")
    flagged = _improvements_fetch_rows("flagged")

    def _table(rows: list[sqlite3.Row]) -> str:
        if not rows:
            return "_(none)_\n"
        out = ["| Title | Relevance | Source | Flag note |", "| --- | --- | --- | --- |"]
        for r in rows:
            title = (r["title"] or "").replace("|", "\\|")
            rel = r["relevance_score"] if r["relevance_score"] is not None else "?"
            src = r["source_url"] or ""
            src_cell = f"[link]({src})" if src else ""
            flag = (r["flag_note"] or "").replace("|", "\\|")
            out.append(f"| {title} | {rel} | {src_cell} | {flag} |")
        return "\n".join(out) + "\n"

    generated_at = _now()
    return (
        "<!-- GENERATED — do not hand-edit; regenerate via "
        "`python3 .memory/log.py improvements dashboard` -->\n"
        "# Nexus Improvements Backlog\n\n"
        f"_Derived from `nexus_improvements` in `.memory/project.db` at {generated_at}._\n\n"
        f"## Flagged ({len(flagged)})\n\n"
        "Items you elevated for nexus-improvement research.\n\n"
        f"{_table(flagged)}\n"
        f"## Unread ({len(unread)})\n\n"
        "Auto-populated at or above the relevance threshold; not yet reviewed.\n\n"
        f"{_table(unread)}"
    )


def cmd_improvements_dashboard(_args: argparse.Namespace) -> None:
    content = _improvements_render_dashboard()
    IMPROVEMENTS_DASHBOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    IMPROVEMENTS_DASHBOARD_PATH.write_text(content, encoding="utf-8")
    print(json.dumps({"dashboard": str(IMPROVEMENTS_DASHBOARD_PATH)}))


# ---------------------------------------------------------------------------
# activity — per-agent activity capture (R1-T05 cockpit feed)
# ---------------------------------------------------------------------------
# Cockpit query (exact column names must match):
#   SELECT agent, task, started, elapsed, status, current_action
#   FROM agent_activity ORDER BY started DESC LIMIT 50
#
# Three actions:
#   activity start  --agent A --task T [--action "..."] [--session S]
#   activity update --id N [--action "..."] [--status "..."]
#   activity end    --id N [--status done]

def _activity_elapsed(started_iso):  # type: ignore[no-untyped-def]
    """Compute a human-readable elapsed string from started ISO-8601 to now.

    Returns a string like '3m42s' or '45s'. Falls back to '?' on any parse
    error so a bad timestamp never crashes the CLI. Uses timezone.utc to stay
    3.9-safe (no datetime.UTC / from datetime import UTC).
    """
    try:
        # Accept both +00:00 and Z suffixes (Python 3.9 fromisoformat doesn't
        # accept the Z suffix, so normalise it first).
        ts = started_iso.replace("Z", "+00:00")
        started_dt = datetime.fromisoformat(ts)
        now_dt = datetime.now(timezone.utc)  # noqa: UP017
        delta_secs = int((now_dt - started_dt).total_seconds())
        if delta_secs < 0:
            delta_secs = 0
        minutes, secs = divmod(delta_secs, 60)
        if minutes:
            return "{}m{}s".format(minutes, secs)
        return "{}s".format(secs)
    except Exception:  # noqa: BLE001
        return "?"


def cmd_activity_start(args):  # type: ignore[no-untyped-def]
    """INSERT an agent_activity row with status='active'."""
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = getattr(args, "session", None) or (row["id"] if row else None)

        cur = conn.execute(
            """INSERT INTO agent_activity
               (agent, task, started, status, current_action, session_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                args.agent,
                getattr(args, "task", None),
                now,
                "active",
                getattr(args, "action", None),
                session_id,
                now,
            ),
        )
        activity_id = cur.lastrowid
    print(json.dumps({"activity_id": activity_id}))


def cmd_activity_update(args):  # type: ignore[no-untyped-def]
    """UPDATE current_action and/or status on an existing agent_activity row."""
    now = _now()
    fields = []
    vals = []
    action_val = getattr(args, "action", None)
    status_val = getattr(args, "status", None)
    if action_val is not None:
        fields.append("current_action=?")
        vals.append(action_val)
    if status_val is not None:
        fields.append("status=?")
        vals.append(status_val)
    fields.append("updated_at=?")
    vals.append(now)
    vals.append(args.id)
    if len(fields) == 1:
        # Only updated_at — still a valid no-op update; proceed.
        pass
    with _conn() as conn:
        conn.execute(
            "UPDATE agent_activity SET {} WHERE id=?".format(", ".join(fields)),
            vals,
        )
    print(json.dumps({"activity_id": args.id, "updated_at": now}))


def cmd_activity_end(args):  # type: ignore[no-untyped-def]
    """Set final status + compute elapsed on an agent_activity row."""
    now = _now()
    final_status = getattr(args, "status", None) or "done"
    with _conn() as conn:
        row = conn.execute(
            "SELECT started FROM agent_activity WHERE id=?",
            (args.id,),
        ).fetchone()
        if row is None:
            print(
                "activity end: no row with id {}".format(args.id),
                file=sys.stderr,
            )
            sys.exit(1)
        elapsed = _activity_elapsed(row["started"])
        conn.execute(
            "UPDATE agent_activity SET status=?, elapsed=?, updated_at=? WHERE id=?",
            (final_status, elapsed, now, args.id),
        )
    print(json.dumps({"activity_id": args.id, "status": final_status, "elapsed": elapsed}))


# ---------------------------------------------------------------------------
# dispatch — per-dispatch token+time telemetry (NATIVE-42 / R1-T01)
# ---------------------------------------------------------------------------
# The orchestrator reads exact subagent_tokens + duration_ms from each
# async/Workflow dispatch completion notification and records one row per
# dispatch. token_source='approx' marks the char/4 heuristic fallback used
# only for synchronous dispatches with no usage block in the notification.
#
#   dispatch record --persona P [--model M] [--task-id T] [--marker MARK] \
#       --tokens N [--token-source exact|approx] [--tool-uses U] \
#       --duration-ms MS [--dispatch-id D] [--session-id S] \
#       [--run-context local|ci] [--independent-subtask-count N] \
#       [--decomposition-considered 0|1]


def record_dispatch(
    persona,  # type: ignore[no-untyped-def]
    tokens,
    duration_ms,
    model=None,
    task_id=None,
    marker=None,
    token_source="exact",
    tool_uses=None,
    dispatch_id=None,
    session_id=None,
    run_context="local",
    independent_subtask_count=None,
    decomposition_considered=None,
    conn=None,
):
    """INSERT one dispatch_telemetry row. Importable module function mirroring
    tools/record_test_run.py's record_run() so tests and future callers can
    invoke it directly without shelling out to the CLI.

    session_id, when omitted, resolves to the current open session (same
    "SELECT id FROM sessions WHERE ended_at IS NULL ..." helper used by
    cmd_activity_start / cmd_validation_add).

    independent_subtask_count / decomposition_considered (R2-T15 / spec §7,
    FIX-2) are both optional and NULL by default — Art. XIII.d decompose-cue
    instrumentation. Callers that haven't adopted the flags yet get NULL rows,
    read as "not recorded" by any consumer, never as a false match.

    Returns the new dispatch_telemetry row id.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = _conn()
    try:
        resolved_session_id = session_id
        if resolved_session_id is None:
            row = conn.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            resolved_session_id = row["id"] if row else None
        cur = conn.execute(
            """INSERT INTO dispatch_telemetry
               (session_id, dispatch_id, persona, model, task_id, marker,
                tokens, token_source, tool_uses, duration_ms, run_context,
                independent_subtask_count, decomposition_considered)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                resolved_session_id,
                dispatch_id,
                persona,
                model,
                task_id,
                marker,
                tokens,
                token_source,
                tool_uses,
                duration_ms,
                run_context,
                independent_subtask_count,
                decomposition_considered,
            ),
        )
        dispatch_telemetry_id = cur.lastrowid
        if owns_conn:
            conn.commit()
        return dispatch_telemetry_id
    finally:
        if owns_conn:
            conn.close()


def cmd_dispatch_record(args):  # type: ignore[no-untyped-def]
    """CLI entry point for `dispatch record`. Prints a JSON confirmation."""
    token_source = getattr(args, "token_source", None) or "exact"
    run_context = getattr(args, "run_context", None) or "local"
    with _conn() as conn:
        dispatch_telemetry_id = record_dispatch(
            args.persona,
            args.tokens,
            args.duration_ms,
            model=getattr(args, "model", None),
            task_id=getattr(args, "task_id", None),
            marker=getattr(args, "marker", None),
            token_source=token_source,
            tool_uses=getattr(args, "tool_uses", None),
            dispatch_id=getattr(args, "dispatch_id", None),
            session_id=getattr(args, "session_id", None),
            run_context=run_context,
            independent_subtask_count=getattr(args, "independent_subtask_count", None),
            decomposition_considered=getattr(args, "decomposition_considered", None),
            conn=conn,
        )
    print(json.dumps({
        "dispatch_telemetry_id": dispatch_telemetry_id,
        "persona": args.persona,
        "tokens": args.tokens,
        "token_source": token_source,
        "duration_ms": args.duration_ms,
    }))


# ---------------------------------------------------------------------------
# Workflow-leg journal ingestion — drift-analysis Finding #6 item 3
# ---------------------------------------------------------------------------
# Workflow-internal `agent()` legs never fire a main-session SubagentStop
# event (BUG #1, documented in .claude/hooks/completion-capture.py's module
# docstring: Workflow-internal teammate spawns are confined to the Workflow's
# interior message stream). No hook can ever reach them. The Workflow script
# primitive `log(...)` (.claude/skills/dispatch/references/
# operating-the-primitive.md: "append to journal.jsonl; survives compaction")
# is therefore the only durable on-disk trace of a leg's usage IF the script
# author chooses to log it — this function is the READ/ingest half of that
# path: it feeds any journal.jsonl rows matching the `agent_complete`
# convention below through record_dispatch(), the EXACT SAME write path
# completion-capture.py uses for direct harness dispatches ("the same path"
# per the Finding #6 brief).
#
# NOTE — the PRODUCE side (a Workflow script actually calling
# `log({"event": "agent_complete", ...})` after each agent() leg) is not
# wired by this change: that is a convention adopted in *dynamically
# authored* Workflow scripts, not a fixed file this ingestion function can
# reach into, and updating the `dispatch` skill's documented `log()`
# convention is outside this dispatch's write surface (.claude/skills/**).
# This function builds the durable, tested, idempotent ingestion substrate;
# adoption by future workflow-authoring is a tracked follow-up.


def ingest_workflow_journal(journal_path, *, conn=None):  # type: ignore[no-untyped-def]
    """Parse a Workflow's journal.jsonl for `agent_complete` rows and feed
    each through record_dispatch().

    Expected per-line shape (append-only JSONL, one JSON object per line;
    lines that are not valid JSON, not a dict, or lack
    ``"event": "agent_complete"`` are silently skipped — never raise on a
    shape mismatch, same fail-soft discipline as every hook in this repo):

        {"event": "agent_complete", "persona": "...", "dispatch_id": "...",
         "tokens": N, "token_source": "exact"|"approx", "tool_uses": N,
         "duration_ms": N, "marker": "...", "model": "...",
         "task_id": "...", "session_id": "..."}

    ``persona`` and an integer ``tokens`` are the only required fields;
    every other field is optional and passed through as-is (None when
    absent).

    IDEMPOTENT: a line carrying a non-empty ``dispatch_id`` that ALREADY has
    a matching dispatch_telemetry row is skipped — re-running ingestion on
    the same journal (e.g. after a Workflow resume re-reads its own
    journal.jsonl from the top) never double-counts. Lines with no
    dispatch_id are always ingested (no identity key to dedupe on) — callers
    adopting this convention SHOULD always emit dispatch_id.

    Returns the number of NEW dispatch_telemetry rows written.
    """
    path = Path(journal_path)
    if not path.is_file():
        return 0
    owns_conn = conn is None
    if owns_conn:
        conn = _conn()
    written = 0
    try:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict) or rec.get("event") != "agent_complete":
                continue
            persona = rec.get("persona")
            if not isinstance(persona, str) or not persona.strip():
                continue
            tokens = rec.get("tokens")
            if not isinstance(tokens, int):
                continue
            dispatch_id = rec.get("dispatch_id")
            if dispatch_id:
                existing = conn.execute(
                    "SELECT 1 FROM dispatch_telemetry WHERE dispatch_id=? LIMIT 1",
                    (dispatch_id,),
                ).fetchone()
                if existing:
                    continue
            duration_ms = rec.get("duration_ms")
            tool_uses = rec.get("tool_uses")
            record_dispatch(
                persona.strip(),
                tokens,
                duration_ms if isinstance(duration_ms, int) else None,
                model=rec.get("model"),
                task_id=rec.get("task_id"),
                marker=rec.get("marker"),
                token_source=rec.get("token_source") or "approx",
                tool_uses=tool_uses if isinstance(tool_uses, int) else None,
                dispatch_id=dispatch_id,
                session_id=rec.get("session_id"),
                conn=conn,
            )
            written += 1
        if owns_conn:
            conn.commit()
        return written
    finally:
        if owns_conn:
            conn.close()


def cmd_dispatch_ingest_journal(args):  # type: ignore[no-untyped-def]
    """CLI entry point for `dispatch ingest-journal`. Prints a JSON summary."""
    with _conn() as conn:
        written = ingest_workflow_journal(args.path, conn=conn)
    print(json.dumps({"journal_path": args.path, "rows_written": written}))


# ---------------------------------------------------------------------------
# skill load events — R2-T15 / spec §7 (FIX-2 corrected design)
# Event-sourced evidence that a persona actually invoked the Skill tool during
# a dispatch, as observed by the harness (a Skill-tool-observing hook) — NOT
# the persona's self-reported skills_loaded claim. SHADOW/ADVISORY only in R2;
# skills-required-guard.sh promotion to hard-deny is R3-T07/T08 scope.
#
#   skill record-load --dispatch-id D --skill-id S [--ts ISO8601] \
#       [--byte-len N]


def record_skill_load(
    dispatch_id,  # type: ignore[no-untyped-def]
    skill_id,
    ts=None,
    byte_len=None,
    conn=None,
):
    """INSERT one skill_load_events row. Importable module function mirroring
    record_dispatch()'s shape so tests and future callers can invoke it
    directly without shelling out to the CLI.

    ts, when omitted, defaults to the current UTC time (ISO-8601) — the event
    is being recorded as it is observed, not backdated.

    Returns the new skill_load_events row id.
    """
    owns_conn = conn is None
    if owns_conn:
        conn = _conn()
    try:
        resolved_ts = ts or _now()
        cur = conn.execute(
            """INSERT INTO skill_load_events
               (dispatch_id, skill_id, ts, byte_len)
               VALUES (?, ?, ?, ?)""",
            (dispatch_id, skill_id, resolved_ts, byte_len),
        )
        skill_load_event_id = cur.lastrowid
        if owns_conn:
            conn.commit()
        return skill_load_event_id
    finally:
        if owns_conn:
            conn.close()


def cmd_skill_record_load(args):  # type: ignore[no-untyped-def]
    """CLI entry point for `skill record-load`. Prints a JSON confirmation."""
    with _conn() as conn:
        skill_load_event_id = record_skill_load(
            args.dispatch_id,
            args.skill_id,
            ts=getattr(args, "ts", None),
            byte_len=getattr(args, "byte_len", None),
            conn=conn,
        )
    print(json.dumps({
        "skill_load_event_id": skill_load_event_id,
        "dispatch_id": args.dispatch_id,
        "skill_id": args.skill_id,
    }))


# ---------------------------------------------------------------------------
# gate stats — R1-T02 workstream D: JOIN/aggregate the two raw telemetry
# sinks (hook_heartbeat.jsonl fires, gate_blocks.jsonl denies) by hook name.
# Read-only, best-effort: a missing/unreadable JSONL file yields an empty
# dataset, never an error. Does not touch project.db.
# ---------------------------------------------------------------------------

def _gate_stats_default_blocks_path() -> Path:
    override = os.environ.get("NEXUS_GATE_BLOCKS_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "files" / "gate_blocks.jsonl"


def _gate_stats_default_heartbeat_path() -> Path:
    # NEXUS_HEARTBEAT_PATH is a log.py-only test seam (mirrors the existing
    # NEXUS_GATE_BLOCKS_PATH override); heartbeat-emitter.sh itself has no
    # env override and always derives its path from repo-root walk-up.
    override = os.environ.get("NEXUS_HEARTBEAT_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "files" / "hook_heartbeat.jsonl"


def _gate_stats_read_jsonl(path: Path) -> list:
    """Best-effort JSONL reader: missing file / bad line -> skipped, never raises."""
    rows: list = []
    try:
        if not path.is_file():
            return rows
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return rows
    return rows


def _gate_stats_filter(rows: list, since: str | None, hook: str | None) -> list:
    out = rows
    if since:
        out = [r for r in out if isinstance(r.get("ts"), str) and r["ts"] >= since]
    if hook:
        out = [r for r in out if r.get("hook") == hook]
    return out


def compute_gate_stats(
    blocks_path: Path,
    heartbeat_path: Path,
    since: str | None = None,
    hook: str | None = None,
) -> dict:
    """Group hook_heartbeat.jsonl + gate_blocks.jsonl rows by hook name.

    Returns {"hooks": {hook_name: {fire_count, block_count, block_rate,
    avg_latency_ms}}, "since": since, "hook_filter": hook}. Pure function —
    no I/O side effects beyond reading the two input files.
    """
    heartbeats = _gate_stats_filter(_gate_stats_read_jsonl(heartbeat_path), since, hook)
    blocks = _gate_stats_filter(_gate_stats_read_jsonl(blocks_path), since, hook)

    fire_counts: dict = {}
    latency_sums: dict = {}
    latency_counts: dict = {}
    for r in heartbeats:
        h = r.get("hook")
        if not h:
            continue
        fire_counts[h] = fire_counts.get(h, 0) + 1
        lat = r.get("latency_ms")
        if isinstance(lat, (int, float)):
            latency_sums[h] = latency_sums.get(h, 0) + lat
            latency_counts[h] = latency_counts.get(h, 0) + 1

    block_counts: dict = {}
    for r in blocks:
        h = r.get("hook")
        if not h:
            continue
        block_counts[h] = block_counts.get(h, 0) + 1

    all_hooks = set(fire_counts) | set(block_counts)
    result: dict = {}
    for h in all_hooks:
        fires = fire_counts.get(h, 0)
        blocked = block_counts.get(h, 0)
        block_rate = (blocked / fires) if fires else 0.0
        lat_count = latency_counts.get(h, 0)
        avg_latency = (latency_sums.get(h, 0) / lat_count) if lat_count else 0.0
        result[h] = {
            "fire_count": fires,
            "block_count": blocked,
            "block_rate": block_rate,
            "avg_latency_ms": avg_latency,
        }
    return {"hooks": result, "since": since, "hook_filter": hook}


def cmd_gate_stats(args: argparse.Namespace) -> None:
    since = getattr(args, "since", None)
    hook = getattr(args, "hook", None)
    stats = compute_gate_stats(
        _gate_stats_default_blocks_path(),
        _gate_stats_default_heartbeat_path(),
        since=since,
        hook=hook,
    )

    if getattr(args, "json_out", False):
        print(json.dumps(stats))
        return

    rows = sorted(
        stats["hooks"].items(), key=lambda kv: kv[1]["fire_count"], reverse=True
    )
    if not rows:
        print("No gate telemetry recorded.")
        return

    col_w = max(len(h) for h, _ in rows) + 2
    header_fmt = f"  {{:<{col_w}}} {{:<8}} {{:<8}} {{:<11}} {{}}"
    row_fmt = f"  {{:<{col_w}}} {{:<8}} {{:<8}} {{:<11}} {{}}"
    print("─" * 66)
    print(header_fmt.format("hook", "fires", "blocks", "block_rate", "avg_latency_ms"))
    print("─" * 66)
    for h, s in rows:
        print(row_fmt.format(
            h,
            s["fire_count"],
            s["block_count"],
            f"{s['block_rate']:.2%}",
            f"{s['avg_latency_ms']:.1f}",
        ))
    print("─" * 66)


# ---------------------------------------------------------------------------
# health — single-project self-test (SessionStart banner + manual report)
# ---------------------------------------------------------------------------

def _load_colocated_health_module():  # type: ignore[return]
    """Load the co-located .memory/health.py (this file's own directory).

    `.memory` is NOT a package, so we load by file path via importlib rather
    than relying on `import health` resolving on sys.path. We anchor on
    __file__'s directory (the LIVE .memory/), falling back to the
    nexus-package snapshot only when no co-located source exists. sys.modules
    registration MUST precede exec_module so health.py's @dataclass decorators
    can resolve cls.__module__ ('health') while building field descriptors.
    """
    import importlib.util as _ilu

    if "health" in sys.modules:
        return sys.modules["health"]

    here = Path(__file__).resolve().parent / "health.py"
    pkg = (
        Path(__file__).resolve().parent.parent
        / "nexus-package" / ".memory" / "health.py"
    )
    src = here if here.is_file() else pkg
    if not src.is_file():
        raise ImportError(f"health.py not found (looked at {here} and {pkg})")
    spec = _ilu.spec_from_file_location("health", str(src))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {src}")
    mod = _ilu.module_from_spec(spec)
    sys.modules["health"] = mod  # register BEFORE exec_module
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def cmd_health(args: argparse.Namespace) -> None:
    """Single-project health self-test.

    Runs run_checks() against this project root and renders the report. The
    SessionStart banner calls `health --no-runtime --json`; --no-runtime maps
    to runtime=False, --drift to drift=True. Default output is --json (the
    machine-readable shape the banner parses); --md and a human table are also
    available. Exit code is 0 even when checks FAIL — a nonzero rc here would
    abort the SessionStart hook chain; FAILs are surfaced via severity in the
    payload, not via process exit.
    """
    project_path = str(Path(__file__).resolve().parent.parent)
    health_mod = _load_colocated_health_module()
    report = health_mod.run_checks(
        project_path,
        runtime=not getattr(args, "no_runtime", False),
        drift=bool(getattr(args, "drift", False)),
    )

    fmt = "json"
    if getattr(args, "md", False):
        fmt = "md"
    elif getattr(args, "table", False) or not getattr(args, "json_out", False):
        # --json is the default the banner relies on; only render the human
        # table when the caller explicitly asks (or asks for neither json/md).
        fmt = "table" if getattr(args, "table", False) else "json"

    if fmt == "md":
        print(report.to_markdown())
    elif fmt == "table":
        print(report.to_table(color=not getattr(args, "no_color", False)))
    else:
        print(json.dumps(report.to_json()))


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(prog="log.py", description="Project memory CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize project.db from schema.sql")

    # session
    sp = sub.add_parser("session")
    ssp = sp.add_subparsers(dest="subcommand", required=True)
    ss = ssp.add_parser("start")
    ss.add_argument("--branch", default="main")
    se = ssp.add_parser("end")
    se.add_argument("--summary", required=True)
    se.add_argument("--next_step", required=True)
    se.add_argument(
        "--experiment", default=None,
        help="R5-T04/NATIVE-35 handoff block: the open experiment under test this session",
    )
    se.add_argument(
        "--hypothesis", default=None,
        help="R5-T04/NATIVE-35 handoff block: the hypothesis behind the open experiment",
    )
    se.add_argument(
        "--next-probe", dest="next_probe", default=None,
        help="R5-T04/NATIVE-35 handoff block: the concrete next probe continuing the experiment",
    )
    sr = ssp.add_parser("reap")
    sr.add_argument("--max-age-hours", dest="max_age_hours", type=int, default=2,
                    help="Close sessions with ended_at IS NULL older than this. Default 2 hours.")
    ssp.add_parser("status")
    sreset = ssp.add_parser("reset", help="End current session + start new one (context handoff)")
    sreset.add_argument("--summary", required=True, help="One-line summary of completed work")
    sreset.add_argument(
        "--handoff-notepad-topic",
        dest="handoff_notepad_topic",
        help="Notepad topic to write handoff entry into (optional)",
    )
    srecall = ssp.add_parser("recall", help="Read-side memory recall (R5-T04): last N sessions + handoff blocks")
    srecall.add_argument("--limit", type=int, default=5, help="Number of most-recent sessions to return (default 5)")
    ssp.add_parser(
        "bump-message-count",
        help="ADR-001 Phase 0: atomically increment the latest open session's "
             "user_message_count (replaces context-reset-monitor.py's own raw write)",
    )

    # task
    tp = sub.add_parser("task")
    tsp = tp.add_subparsers(dest="subcommand", required=True)
    ta = tsp.add_parser("add")
    ta.add_argument("--id", help="Explicit id override (skips per-domain minting)")
    ta.add_argument("--domain", required=True, choices=list(TASK_DOMAINS),
                    help="DEC-098 task domain; mints NEX-/PLX-/KB-/OPS-/OTH- ids")
    ta.add_argument("--feature-id", dest="feature_id")
    ta.add_argument("--title", required=True)
    ta.add_argument("--description")
    ta.add_argument("--status")
    ta.add_argument("--priority")
    ta.add_argument("--assigned-to", dest="assigned_to")
    ta.add_argument("--acceptance-criteria", dest="acceptance_criteria")
    ta.add_argument("--notes")
    tu = tsp.add_parser("update")
    tu.add_argument("--id", required=True)
    tu.add_argument("--title")
    tu.add_argument("--status")
    tu.add_argument("--priority")
    tu.add_argument("--assigned-to", dest="assigned_to")
    tu.add_argument("--notes")
    tu.add_argument("--worktree")
    tl = tsp.add_parser("list")
    tl.add_argument("--status")
    tl.add_argument("--feature-id", dest="feature_id")
    tmg = tsp.add_parser(
        "migrate-domains",
        help="DEC-098: apply the owner triage-verdict dispositions (dry-run by default)",
    )
    tmg.add_argument("--verdicts", required=True,
                     help="Path to the triage verdict JSON file")
    tmg.add_argument("--apply", action="store_true",
                     help="Back up the DB, then execute in one transaction (default: dry-run)")
    ti = tsp.add_parser("stall", help="Atomically increment stall_count (compare-and-swap)")
    ti.add_argument("--task-id", dest="task_id", required=True, help="TASK-NNN or memory task id")
    ti.add_argument("--persona", required=True, help="Persona name that produced the stall marker")
    ti.add_argument("--marker", required=True, choices=["REVISE", "BLOCKED", "revise", "blocked"],
                    help="The marker type: REVISE or BLOCKED")
    tmn = tsp.add_parser(
        "mirror-native",
        help="Mirror ONE native TaskCreate/TaskUpdate op into project.db (used by the task-db-mirror hook)",
    )
    tmn.add_argument("--op", choices=["create", "update"], default="update",
                     help="create=TaskCreate, update=TaskUpdate")
    tmn.add_argument("--native-id", dest="native_id", required=True,
                     help="Native integer task id (e.g. 7)")
    tmn.add_argument("--subject")
    tmn.add_argument("--description")
    tmn.add_argument("--status",
                     help="Native status: pending|in_progress|completed|deleted")
    tmn.add_argument("--owner", help="Native task owner (agent name) -> assigned_to")
    tbn = tsp.add_parser(
        "backfill-native",
        help="Bulk-mirror a native task snapshot (JSON array or JSONL on --from/stdin) into project.db",
    )
    tbn.add_argument("--from", dest="from_file", default="-",
                     help="Path to a JSON/JSONL snapshot of native tasks, or '-' for stdin")
    tsp.add_parser(
        "repair-orphans",
        help="Find tasks with doubled NATIVE- prefix (e.g. NATIVE-NATIVE-17), delete or rename to canonical",
    )

    # tasks (plural) — TTL/archive maintenance (R5-T01 remainder, N56). Deliberately
    # a SEPARATE top-level command from singular `task` (which owns single-row CRUD)
    # since this is a bulk maintenance sweep, not a per-row op.
    tsk = sub.add_parser("tasks")
    tsksp = tsk.add_subparsers(dest="subcommand", required=True)
    tskreap = tsksp.add_parser(
        "reap",
        help="Archive (never delete) open tasks past their TTL; see TASK_TTL_DEFAULT_DAYS",
    )
    tskreap.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Preview the candidate set without writing",
    )

    # decision
    dp = sub.add_parser("decision")
    dsp = dp.add_subparsers(dest="subcommand", required=True)
    da = dsp.add_parser("add")
    da.add_argument("--id")
    da.add_argument("--title", required=True)
    da.add_argument("--status", default="accepted")
    da.add_argument("--context", required=True)
    da.add_argument("--decision", required=True)
    da.add_argument("--rationale", required=True,
                    help="Why this option over alternatives — required (no empty rows)")
    da.add_argument("--alternatives",
                    help="Other options considered + why rejected")
    da.add_argument("--consequences",
                    help="What this decision implies / who is impacted")
    dl = dsp.add_parser("list")
    dl.add_argument("--history", action="store_true",
                    help="Walk the full bi-temporal chain (every version), not just current rows.")
    dr = dsp.add_parser("retire", help="Tombstone a decision (hides from current recall; history kept).")
    dr.add_argument("id", help="Decision id to retire (e.g. DEC-007).")

    # lesson (Phase 3 — Technique 9)
    lp = sub.add_parser("lesson")
    lsp = lp.add_subparsers(dest="subcommand", required=True)
    la = lsp.add_parser("add")
    la.add_argument("--id")
    la.add_argument("--trigger", required=True,
                    choices=["lens_fail", "redelegation", "session_drift", "manual", "reflection"])
    la.add_argument("--title", required=True)
    la.add_argument("--body", required=True, help="1-paragraph, ≤80 words")
    la.add_argument("--applies-to", dest="applies_to",
                    help="'all' or comma-separated persona names")
    la.add_argument("--source-decision-id", dest="source_decision_id")
    la.add_argument("--validated", action="store_true",
                    help="Skip unvalidated state; use only for manual high-confidence lessons.")
    lv = lsp.add_parser("validate")
    lv.add_argument("--id", required=True)
    lv.add_argument("--as-decision", dest="as_decision",
                    help="Decision ID that promotes this lesson to validated.")
    ll = lsp.add_parser("list")
    ll.add_argument("--validated", action="store_const", const=True, default=None)
    ll.add_argument("--unvalidated", dest="validated", action="store_const", const=False)
    ll.add_argument("--applies-to", dest="applies_to")
    ll.add_argument("--history", action="store_true",
                    help="Show full bi-temporal chain (all versions, including superseded).")

    # fact (Phase 3 — Technique 3 semantic tier)
    fp2 = sub.add_parser("fact")
    fsp2 = fp2.add_subparsers(dest="subcommand", required=True)
    fa2 = fsp2.add_parser("add")
    fa2.add_argument("--key", required=True)
    fa2.add_argument("--value", required=True)
    fa2.add_argument("--source-decision-id", dest="source_decision_id")
    fa2.add_argument("--pinned", action="store_true",
                     help="Pinned facts never decay.")
    fl2 = fsp2.add_parser("list")
    fl2.add_argument("--pinned-only", dest="pinned_only", action="store_true")
    fl2.add_argument("--key-like", dest="key_like")
    fl2.add_argument("--history", action="store_true",
                     help="Show full bi-temporal chain (all versions, including superseded).")
    fd = fsp2.add_parser("decay")
    fd.add_argument("--key", required=True)

    # procedure (Phase 3 — Technique 3 procedural tier)
    pp = sub.add_parser("procedure")
    psp = pp.add_subparsers(dest="subcommand", required=True)
    pa = psp.add_parser("add")
    pa.add_argument("--name", required=True)
    pa.add_argument("--trigger-pattern", dest="trigger_pattern")
    pa.add_argument("--steps-json", dest="steps_json", required=True,
                    help='JSON array, e.g. \'["step 1", "step 2"]\'')
    pr = psp.add_parser("record-outcome")
    pr.add_argument("--name", required=True)
    pr.add_argument("--outcome", required=True, choices=["success", "fail"])
    psp.add_parser("list")

    # feature
    fp = sub.add_parser("feature")
    fsp = fp.add_subparsers(dest="subcommand", required=True)
    fa = fsp.add_parser("add")
    fa.add_argument("--id", required=True, help="FEAT-XXX")
    fa.add_argument("--title", required=True)
    fa.add_argument("--status", default="planned",
                    help="planned | in_progress | done | cancelled")
    fa.add_argument("--spec-path", dest="spec_path",
                    help="Path to docs/features/FEAT-XXX-*.md")
    fa.add_argument("--description")
    fa.add_argument("--tasks-json", dest="tasks_json",
                    help='JSON array of TASK IDs, e.g. \'["TASK-001","TASK-002"]\'')
    fu = fsp.add_parser("update")
    fu.add_argument("--id", required=True)
    fu.add_argument("--title")
    fu.add_argument("--status")
    fu.add_argument("--spec-path", dest="spec_path")
    fu.add_argument("--description")
    fu.add_argument("--tasks-json", dest="tasks_json")
    fl = fsp.add_parser("list")
    fl.add_argument("--status")

    # context
    cp = sub.add_parser("context")
    csp = cp.add_subparsers(dest="subcommand", required=True)
    cs = csp.add_parser("snapshot")
    cs.add_argument("--action-type", dest="action_type")
    cs.add_argument("--files-modified", dest="files_modified")
    cs.add_argument("--decision-refs", dest="decision_refs")
    cs.add_argument("--task-updates", dest="task_updates")
    cs.add_argument("--summary")
    cd_p = csp.add_parser("dump")
    cd_p.add_argument("--tasks", choices=["in_progress", "all"], default="all",
                      help="Which open tasks to include in open_tasks (default: all). "
                           "'in_progress' trims to mid-flight rows; task_status_counts always covers the full open set.")
    cd_p.add_argument("--decisions", type=int, default=5,
                      help="Cap recent_decisions to N rows (default: 5; was hardcoded 10).")

    sub.add_parser("seed", help="One-time bootstrap: seed tasks from docs/TASKS.md before autosync (DB is source of truth)")

    # memory (Phase 3 — Technique 3b retention worker)
    mp = sub.add_parser("memory")
    msp = mp.add_subparsers(dest="subcommand", required=True)
    mr = msp.add_parser("retain")
    mr.add_argument("--ctx-ttl-days", dest="ctx_ttl_days", type=int, default=14,
                    help="context_log rows older than this AND quality<threshold are dropped")
    mr.add_argument("--fact-ttl-days", dest="fact_ttl_days", type=int, default=180,
                    help="unpinned semantic_facts older than this are decayed (soft-deleted)")
    mr.add_argument("--apply", action="store_true",
                    help="actually commit deletions; default is dry-run reporting")

    # planning-gate
    pgp = sub.add_parser("planning-gate")
    pgsp = pgp.add_subparsers(dest="subcommand", required=True)
    pgc = pgsp.add_parser("check")
    pgc.add_argument("--feat", required=True, help="Feature ID, e.g. FEAT-001")
    pgs = pgsp.add_parser("submit",
                          help="Submit a structured plan JSON; rejects on missing fields or failed check")
    pgs.add_argument("--feat", help="Feature ID (overrides plan.feat); required if absent from plan")
    pgs.add_argument("--json", required=True,
                     help="Plan JSON string, or '-' to read from stdin")

    # validation (lens-gate)
    vp = sub.add_parser("validation", help="Lens validation log for lens-gate hook")
    vsp = vp.add_subparsers(dest="subcommand", required=True)
    va = vsp.add_parser("add", help="Record a Lens validation row")
    va.add_argument("--agent", required=True, help="Agent that validated (typically 'lens')")
    va.add_argument("--target", required=True, help="Agent whose work was validated (e.g. 'forge')")
    va.add_argument("--task-hash", dest="task_hash", required=True,
                    help="16-char SHA-256 prefix of the task ID or brief text (from lens-gate stderr)")
    va.add_argument("--verdict", required=True, help="PASS | PARTIAL | FAIL (claimed; "
                    "evidence-derived down when a report is supplied — OPT-038)")
    va.add_argument("--summary", default="", help="One-line evidence summary (optional)")
    va.add_argument("--report-path", dest="report_path",
                    help="Path to the structured Lens report JSON ('-' for stdin). When "
                         "present, the stored verdict is DERIVED from its criteria_results[] "
                         "and deterministic[] exit codes (any FAIL ⇒ verdict cannot be PASS).")
    va.add_argument("--report-json", dest="report_json",
                    help="Inline structured Lens report JSON (alternative to --report-path).")
    va.add_argument("--strict", action="store_true",
                    help="Reject (exit 1) instead of silently downgrading when the claimed "
                         "verdict contradicts the report evidence.")
    va.add_argument("--files-changed-json", dest="files_changed_json", default=None,
                    help="JSON array of the implementer's declared files_changed. Stored "
                         "for completeness-check coverage assertions.")
    va.add_argument("--dispatch-started-at", dest="dispatch_started_at", default=None,
                    help="ISO-8601 UTC timestamp of when this Lens dispatch began "
                         "(distinct from validated_at = row-write time); enables wall-clock "
                         "duration instrumentation.")
    va.add_argument("--lens-type", dest="lens_type", default=None, choices=["T0", "T1", "T2"],
                    help="R1-T08: depth tier Lens actually ran at for this row. Read by "
                         "lens-gate.sh's _has_lens_validation_v2 / lens-tier-backstop.sh to "
                         "confirm the DELIVERED depth (not merely the required depth).")
    va.add_argument("--risk-tier", dest="risk_tier", default=None, choices=["T0", "T1", "T2"],
                    help="R1-T08: depth tier the orchestrator's classifier required ahead of "
                         "dispatch. Stored alongside lens_type (never conflated with it) so a "
                         "gate can assert delivered-depth >= required-depth.")

    vc = vsp.add_parser("completeness-check",
                        help="Assert that the latest in-window lens PASS row covers "
                             "a declared set of files_changed (rc=0 on PASS, rc=2 otherwise)")
    vc.add_argument("--files-changed-json", dest="files_changed_json", required=True,
                    help="JSON array of file paths that must be covered by the PASS row.")
    vc.add_argument("--task-hash", dest="task_hash", default=None,
                    help="Optional: further filter to a specific task/brief hash.")

    vcg = vsp.add_parser(
        "check-gate",
        help="ADR-001 Phase 0: read-only lens-gate.sh SubagentStop check (v1 floor + "
             "v2 tier-distinct), routed through the single-writer connection instead "
             "of the hook's own raw sqlite3.connect. Always prints JSON, exit 0.",
    )
    vcg.add_argument("--target", required=True, help="Agent whose work is being validated (agent_name)")
    vcg.add_argument("--task-hash", dest="task_hash", required=True)
    vcg.add_argument("--tier", required=True, choices=["T1", "T2"],
                     help="Required lens tier for this dispatch (from the hook's own "
                          "local _classify_lens_tier — no DB round-trip needed for this)")

    # subagent-return (Mitigation A)
    srp = sub.add_parser("subagent-return", help="Record and summarize a subagent response")
    srsp = srp.add_subparsers(dest="subcommand", required=True)
    srr = srsp.add_parser("record", help="Persist full response to disk; write summary to notepad")
    srr.add_argument("--agent", required=True, help="Persona name of the returning subagent")
    srr.add_argument(
        "--full-response-file", dest="full_response_file",
        help="Path to file containing the full response (omit to read from stdin)",
    )

    # notepad — rolling 5-entry shared context for phased tasks
    np = sub.add_parser("notepad", help="Agent notepad: shared rolling context for phased tasks")
    nsp = np.add_subparsers(dest="subcommand", required=True)
    na = nsp.add_parser("add", help="Add a notepad entry (rolling window of 5 per topic)")
    na.add_argument("--topic", required=True,
                    help="Scope key: TASK-NNN, FEAT-NNN, branch name, or freeform kebab")
    na.add_argument("--agent", required=True,
                    help="Persona name (scout|forge|pipeline|hermes|atlas|lens|quill|palette|nexus)")
    na.add_argument("--note", required=True, help="Insight for the next agent on this topic (≤500 chars)")
    na.add_argument("--kind", default="fyi",
                    help="fyi | nuance | reminder | gotcha | next-agent-action (default: fyi)")
    nl = nsp.add_parser("list", help="List last 5 notepad entries for a topic (chronological)")
    nl.add_argument("--topic", required=True)
    nc = nsp.add_parser("clear", help="Delete all notepad entries for a topic")
    nc.add_argument("--topic", required=True)

    # registry (PLEXUS — project registry)
    reg = sub.add_parser("registry", help="Project registry")
    reg_sub = reg.add_subparsers(dest="subcommand", required=True)

    reg_a = reg_sub.add_parser("add")
    reg_a.add_argument("--project-path", dest="project_path", required=True)
    reg_a.add_argument("--version", required=True)
    reg_a.add_argument("--action", required=True, choices=["installed", "installed-existing", "manual"])
    reg_a.add_argument("--notes", default=None)
    reg_a.add_argument(
        "--merge", action="store_true",
        help="R5-T09: fold a path that differs only by case/trailing-slash "
             "into the existing matching row instead of rejecting it.",
    )

    reg_u = reg_sub.add_parser("update")
    reg_u.add_argument("--project-path", dest="project_path", required=True)
    reg_u.add_argument("--version", required=True)
    reg_u.add_argument("--action", default="updated", choices=["updated", "rolled-back"])
    reg_u.add_argument("--notes", default=None)
    reg_u.add_argument(
        "--has-ledger", dest="has_ledger", choices=["auto", "yes", "no"], default="auto",
        help="Ledger presence stamp (NATIVE-2-17 fix): 'auto' (default) detects "
             "<project-path>/.memory/nexus-stack.json on disk; 'yes'/'no' overrides "
             "detection explicitly. Always re-stamps last_validated=now().",
    )

    reg_l = reg_sub.add_parser("list")
    reg_l.add_argument("--project-path", dest="project_path", default=None)

    reg_r = reg_sub.add_parser("remove")
    reg_r.add_argument("--project-path", dest="project_path", required=True)
    reg_r.add_argument("--notes", default=None)

    reg_d = reg_sub.add_parser(
        "dedup", help="R5-T09: one-time migration folding case/trailing-slash duplicate rows",
    )
    reg_d.add_argument(
        "--apply", action="store_true",
        help="Actually write the merge (default: dry-run report only, no writes).",
    )

    reg_h = reg_sub.add_parser("health", help="Fleet health: run static checks on all registered projects")
    reg_h.add_argument("--full", action="store_true", help="Include runtime checks per project (slower)")
    reg_h.add_argument("--drift", action="store_true", help="Include drift checks vs canonical package")
    reg_h.add_argument("--json", action="store_true", dest="json_out", help="Emit machine-readable JSON")
    reg_h.add_argument(
        "--leak-check", action="store_true", dest="leak_check",
        help="Enable per-project leak scan (slow — O(files × projects); omit for fast fleet polling)",
    )

    # feedback (DEC-019 — Nexus self-feedback MVP)
    fbp = sub.add_parser(
        "feedback",
        help="Nexus self-feedback: per-project friction log + Plexus harvest",
    )
    fb_sub = fbp.add_subparsers(dest="subcommand", required=True)
    fb_a = fb_sub.add_parser("add", help="Record one Nexus-friction row in nexus_feedback")
    fb_a.add_argument("--source", required=True, choices=sorted(_FEEDBACK_SOURCES),
                      help="Who reported it: tool (MCP) | hook (passive marker capture)")
    fb_a.add_argument("--severity", required=True, choices=sorted(_FEEDBACK_SEVERITIES),
                      help="critical | high | medium | low | info")
    fb_a.add_argument("--category", required=True, choices=sorted(_FEEDBACK_CATEGORIES),
                      help="Friction class (gate_deny, gate_needs_decision, roster_mismatch, …)")
    fb_a.add_argument("--message", required=True, help="What blocked/confused/stalled the agent")
    fb_a.add_argument("--context-json", dest="context_json", default=None,
                      help="Optional JSON blob (turn_id, persona, marker, …)")
    fb_a.add_argument("--source-file", dest="source_file", default=None,
                      help="Optional path the friction relates to")
    fb_a.add_argument("--nexus-version", dest="nexus_version", default=None,
                      help="Optional explicit version stamp (default: read from "
                           ".memory/.nexus-version; falls back to 'unknown')")
    fb_h = fb_sub.add_parser(
        "harvest",
        help="Plexus-only: aggregate per-project nexus_feedback into improvement_backlog",
    )
    fb_h.add_argument("--md", action="store_true",
                      help="Render a markdown summary instead of JSON")
    fb_h.add_argument("--dry-run", dest="dry_run", action="store_true",
                      help="Count unresolved feedback across the fleet WITHOUT writing improvement_backlog (read-only; used by the SessionStart harvest-banner)")
    fb_r = fb_sub.add_parser(
        "resolve",
        help="Plexus-only: stamp resolved_at on per-project nexus_feedback so harvest stops re-firing",
    )
    fb_r.add_argument("--backlog-id", dest="backlog_id", type=int, default=None,
                      help="improvement_backlog row id to resolve (recovers source path + category + hash)")
    fb_r.add_argument("--project-path", dest="project_path", default=None,
                      help="Source project path (with --category + --hash, instead of --backlog-id)")
    fb_r.add_argument("--category", default=None,
                      help="Feedback category to resolve (used with --project-path + --hash)")
    fb_r.add_argument("--hash", default=None,
                      help="dedup_hash = sha256(message) of the rows to resolve")
    fb_r.add_argument("--reviewed-by", dest="reviewed_by", default="plexus",
                      help="Who triaged/resolved it (default: plexus)")
    fb_r.add_argument("--up-to-version", dest="up_to_version", default=None,
                      help="Version-scoped: resolve ONLY rows whose nexus_version "
                           "semver-tuple <= VERSION (e.g. 1.11.0). Leaves newer "
                           "(live-pain) rows open. Rows stamped 'unknown' are left "
                           "open unless --include-unknown is also passed.")
    fb_r.add_argument("--include-unknown", dest="include_unknown", action="store_true",
                      help="With --up-to-version: also resolve rows whose nexus_version "
                           "is 'unknown' (legacy / unreadable version stamp).")

    # rca (Phase D Layer 2 — root cause analysis + embed)
    rp = sub.add_parser("rca", help="Root cause analysis log (embeds to vec_memory)")
    rsp = rp.add_subparsers(dest="subcommand", required=True)
    ra = rsp.add_parser("add", help="Record a root cause analysis")
    ra.add_argument("--agent", required=True, help="Agent persona that performed the RCA")
    ra.add_argument("--symptom", required=True, help="Observable failure or bug description")
    ra.add_argument("--why-chain-json", dest="why_chain_json", required=True,
                    help='JSON array of why strings, e.g. \'["Why 1","Why 2","Why 3","Why 4","Why 5"]\'')
    ra.add_argument("--pattern-fix", dest="pattern_fix", required=True,
                    help="Root fix / pattern change applied")
    ra.add_argument("--task-summary", dest="task_summary",
                    help="Optional task ID or brief summary for attribution")

    # reflection (Phase D Layer 2 — reflection_snapshot + embed)
    refp = sub.add_parser("reflection", help="Reflection snapshot (embeds to vec_memory)")
    refsp = refp.add_subparsers(dest="subcommand", required=True)
    refa = refsp.add_parser("add", help="Record a reflection snapshot")
    refa.add_argument("--file-path", dest="file_path", help="Path to the amended file (optional)")
    refa.add_argument("--action-type", dest="action_type", required=True,
                      help="spec_update | decision_amend | constitution_amend | other")
    refa.add_argument("--summary", required=True, help="One-line summary (≤200 chars)")

    # recall (Phase D Layer 2 — semantic search)
    rcp = sub.add_parser("recall", help="Semantic recall over vec_memory")
    rcp.add_argument("--semantic", required=True, help="Natural-language query to embed and search")
    rcp.add_argument("--kind", help="Filter by kind: decision | lesson | rca | reflection")
    rcp.add_argument("--top-k", dest="top_k", type=int, default=5, help="Max results (default: 5)")
    rcp.add_argument("--since", help="Only results within Nd (e.g. 30d)")
    rcp.add_argument(
        "--fallback",
        choices=["keyword"],
        default=None,
        help=(
            "Opt-in degraded fallback when embed is unavailable. "
            "'keyword': fall back to relational keyword search; "
            "response includes mode='keyword_fallback' and degraded=true. "
            "Without this flag, embed failure exits 3 (strict default)."
        ),
    )

    # vec (P1-03 / OPT-055 — outbox + dead-letter recovery / backfill)
    vecp = sub.add_parser("vec", help="vec_memory maintenance")
    vecsp = vecp.add_subparsers(dest="subcommand", required=True)
    vec_bf = vecsp.add_parser(
        "backfill",
        help="Drain embed_outbox + dead-letter into vec_memory (re-embed missing rows)",
    )
    vec_bf.add_argument(
        "--full",
        action="store_true",
        help="Also run the O(N) source sweep (backstop). Default drains outbox + dead-letter only.",
    )

    # embed-backfill — OPT-055 thin alias for `vec backfill`.
    eb = sub.add_parser(
        "embed-backfill",
        help="Alias for `vec backfill`: drain embed_outbox + dead-letter into vec_memory",
    )
    eb.add_argument(
        "--full",
        action="store_true",
        help="Also run the O(N) source sweep (backstop). Default drains outbox + dead-letter only.",
    )

    # improvements — nexus-improvement backlog (evaluated-vs-unread tracker)
    imp = sub.add_parser(
        "improvements",
        help="Nexus-improvement backlog: auto-populate unread + manual flag/evaluate/dismiss",
    )
    imp_sub = imp.add_subparsers(dest="subcommand", required=True)
    imp_pop = imp_sub.add_parser(
        "populate",
        help="Scan distilled sources; upsert 'unread' rows at relevance>=THRESHOLD (idempotent, no-downgrade)",
    )
    imp_pop.add_argument(
        "--threshold", type=int, default=None,
        help="Relevance gate (default 4 or $NEXUS_IMPROVEMENTS_THRESHOLD)",
    )
    imp_list = imp_sub.add_parser("list", help="List backlog rows by review_state")
    imp_list.add_argument(
        "--state", default="unread",
        choices=["unread", "evaluated", "flagged", "dismissed", "all"],
        help="Filter by review_state (default: unread)",
    )
    imp_flag = imp_sub.add_parser("flag", help="Promote a note to review_state='flagged'")
    imp_flag.add_argument("note", help="Note basename or repo-relative path")
    imp_flag.add_argument("--note", dest="note_text", help="Why this matters to Nexus")
    imp_eval = imp_sub.add_parser("evaluate", help="Mark a note review_state='evaluated'")
    imp_eval.add_argument("note", help="Note basename or repo-relative path")
    imp_eval.add_argument("--note", dest="note_text", help="Optional evaluation note")
    imp_dis = imp_sub.add_parser("dismiss", help="Mark a note review_state='dismissed'")
    imp_dis.add_argument("note", help="Note basename or repo-relative path")
    imp_dis.add_argument("--note", dest="note_text", help="Optional reason")
    imp_sub.add_parser(
        "dashboard",
        help="Regenerate research/00-meta/NEXUS-IMPROVEMENTS.md (derive-only)",
    )

    # activity — per-agent activity capture (R1-T05 cockpit feed)
    act = sub.add_parser("activity", help="Per-agent activity capture (cockpit feed)")
    act_sub = act.add_subparsers(dest="subcommand", required=True)

    act_s = act_sub.add_parser("start", help="Start an activity row (status=active)")
    act_s.add_argument("--agent", required=True, help="Agent persona name")
    act_s.add_argument("--task", default=None, help="Task id or description")
    act_s.add_argument("--action", default=None, dest="action",
                       help="Initial current_action string")
    act_s.add_argument("--session", default=None,
                       help="Session id override (default: open session)")

    act_u = act_sub.add_parser("update", help="Update action/status on an activity row")
    act_u.add_argument("--id", required=True, type=int, help="agent_activity row id")
    act_u.add_argument("--action", default=None, dest="action",
                       help="New current_action string")
    act_u.add_argument("--status", default=None,
                       help="New status (active|done|failed)")

    act_e = act_sub.add_parser("end", help="End an activity row (compute elapsed)")
    act_e.add_argument("--id", required=True, type=int, help="agent_activity row id")
    act_e.add_argument("--status", default="done",
                       help="Final status (default: done)")

    # dispatch — per-dispatch token+time telemetry (NATIVE-42 / R1-T01)
    disp = sub.add_parser("dispatch", help="Per-dispatch token+time telemetry")
    disp_sub = disp.add_subparsers(dest="subcommand", required=True)

    disp_r = disp_sub.add_parser("record", help="Record one dispatch_telemetry row")
    disp_r.add_argument("--persona", required=True, help="Dispatched persona")
    disp_r.add_argument("--model", default=None,
                        help="opus|sonnet|haiku|fable lineage (optional)")
    disp_r.add_argument("--task-id", dest="task_id", default=None,
                        help="Task or feature id (optional)")
    disp_r.add_argument("--marker", default=None,
                        help="Terminal NEXUS marker: DONE|REVISE|BLOCKED|... (optional)")
    disp_r.add_argument("--tokens", required=True, type=int,
                        help="Exact subagent_tokens, or char/4 approx")
    disp_r.add_argument("--token-source", dest="token_source", default="exact",
                        choices=["exact", "approx"],
                        help="'exact' (from usage block) or 'approx' (char/4 heuristic "
                             "fallback for synchronous dispatches); default 'exact'")
    disp_r.add_argument("--tool-uses", dest="tool_uses", default=None, type=int,
                        help="Tool-use count from the completion notification (optional)")
    disp_r.add_argument("--duration-ms", dest="duration_ms", required=False, default=None, type=int,
                        help="Exact wall-clock duration in milliseconds (optional — "
                             "nullable when the caller has no start-time reference, e.g. "
                             "a dispatch whose PreToolUse activity-open row is missing)")
    disp_r.add_argument("--dispatch-id", dest="dispatch_id", default=None,
                        help="Harness agent/dispatch id (optional)")
    disp_r.add_argument("--session-id", dest="session_id", default=None,
                        help="Session id override (default: current open session)")
    disp_r.add_argument("--run-context", dest="run_context", default="local",
                        choices=["local", "ci"],
                        help="'local' (default) or 'ci'")
    disp_r.add_argument("--independent-subtask-count", dest="independent_subtask_count",
                        default=None, type=int,
                        help="Count of independent subtasks identified before this "
                             "dispatch (Art. XIII.d decompose-cue; optional, R2-T15)")
    disp_r.add_argument("--decomposition-considered", dest="decomposition_considered",
                        default=None, type=int, choices=[0, 1],
                        help="0/1 — whether a Workflow/fan-out decomposition was "
                             "explicitly evaluated before this dispatch (DEC-029; "
                             "optional, R2-T15)")

    disp_ij = disp_sub.add_parser(
        "ingest-journal",
        help="Ingest agent_complete rows from a Workflow journal.jsonl into "
             "dispatch_telemetry (Finding #6 item 3 — Workflow-leg capture)",
    )
    disp_ij.add_argument("--path", required=True, help="Path to the journal.jsonl file")

    # skill — R2-T15 / spec §7: harness-observed Skill-tool invocation events
    skillp = sub.add_parser("skill", help="Skill-load event recording (harness-observed)")
    skill_sub = skillp.add_subparsers(dest="subcommand", required=True)

    skill_r = skill_sub.add_parser(
        "record-load", help="Record one skill_load_events row"
    )
    skill_r.add_argument("--dispatch-id", dest="dispatch_id", required=True,
                         help="Harness agent/dispatch id (FK, advisory)")
    skill_r.add_argument("--skill-id", dest="skill_id", required=True,
                         help="Skill name as passed to the Skill tool")
    skill_r.add_argument("--ts", default=None,
                         help="ISO-8601 UTC timestamp of the observed event "
                              "(default: now)")
    skill_r.add_argument("--byte-len", dest="byte_len", default=None, type=int,
                         help="Size in bytes of the injected skill content (optional)")

    # gate — R1-T02 workstream D: aggregate hook_heartbeat.jsonl + gate_blocks.jsonl
    gatep = sub.add_parser("gate", help="Gate telemetry aggregation (fire/block-rate by hook)")
    gate_sub = gatep.add_subparsers(dest="subcommand", required=True)
    gate_stats = gate_sub.add_parser(
        "stats",
        help="Join hook_heartbeat.jsonl + gate_blocks.jsonl, group by hook: "
             "fires, blocks, block_rate, avg_latency_ms",
    )
    gate_stats.add_argument("--since", default=None,
                            help="ISO8601 timestamp; only rows with ts >= this value")
    gate_stats.add_argument("--hook", default=None,
                            help="Filter to a single hook name")
    gate_stats.add_argument("--json", action="store_true", dest="json_out",
                            help="Emit machine-readable JSON instead of a table")

    # health — single-project self-test (bare command, no subcommand).
    # NOTE: distinct from `registry health` (fleet). The SessionStart banner
    # calls `health --no-runtime --json`.
    hp = sub.add_parser(
        "health",
        help="Single-project health self-test (run_checks against this project)",
    )
    hp.add_argument(
        "--no-runtime", action="store_true", dest="no_runtime",
        help="Skip RUNTIME-tier checks (broker/hooks/DB/embeddings); run STATIC only",
    )
    hp.add_argument(
        "--drift", action="store_true",
        help="Include DRIFT-tier checks comparing this install to the canonical package",
    )
    hp.add_argument(
        "--json", action="store_true", dest="json_out",
        help="Emit the machine-readable HealthReport JSON (the SessionStart banner default)",
    )
    hp.add_argument(
        "--md", action="store_true",
        help="Render a markdown table (suitable for PR comments)",
    )
    hp.add_argument(
        "--table", action="store_true",
        help="Render a human-readable rich/ASCII table",
    )
    hp.add_argument(
        "--no-color", action="store_true", dest="no_color",
        help="Disable color in --table output",
    )

    args = p.parse_args()

    dispatch = {
        "init": cmd_init,
        "seed": cmd_seed,
        "recall": cmd_recall,
        "health": cmd_health,  # single-project self-test (SessionStart banner)
        "embed-backfill": cmd_vec_backfill,  # OPT-055 alias for `vec backfill`
    }
    if args.command in dispatch:
        dispatch[args.command](args)
        return

    sub_dispatch = {
        ("session", "start"):    cmd_session_start,
        ("session", "end"):      cmd_session_end,
        ("session", "reap"):     cmd_session_reap,
        ("session", "status"):   cmd_session_status,
        ("session", "reset"):    cmd_session_reset,
        ("session", "recall"):   cmd_session_recall,
        ("session", "bump-message-count"): cmd_session_bump_message_count,
        ("task",    "add"):      cmd_task_add,
        ("task",    "update"):   cmd_task_update,
        ("task",    "list"):     cmd_task_list,
        ("task",    "migrate-domains"): cmd_task_migrate_domains,
        ("task",    "stall"):    cmd_stall_increment,
        ("task",    "mirror-native"):   cmd_task_mirror_native,
        ("task",    "backfill-native"): cmd_task_backfill_native,
        ("task",    "repair-orphans"):  cmd_task_repair_orphans,
        ("tasks",   "reap"):            cmd_tasks_reap,
        ("decision","add"):      cmd_decision_add,
        ("decision","list"):     cmd_decision_list,
        ("decision","retire"):   cmd_decision_retire,
        ("feature", "add"):      cmd_feature_add,
        ("feature", "update"):   cmd_feature_update,
        ("feature", "list"):     cmd_feature_list,
        ("lesson",  "add"):      cmd_lesson_add,
        ("lesson",  "validate"): cmd_lesson_validate,
        ("lesson",  "list"):     cmd_lesson_list,
        ("fact",    "add"):      cmd_fact_add,
        ("fact",    "list"):     cmd_fact_list,
        ("fact",    "decay"):    cmd_fact_decay,
        ("procedure", "add"):           cmd_procedure_add,
        ("procedure", "record-outcome"): cmd_procedure_record,
        ("procedure", "list"):          cmd_procedure_list,
        ("memory",  "retain"):   cmd_memory_retain,
        ("context",        "snapshot"): cmd_context_snapshot,
        ("context",        "dump"):     cmd_context_dump,
        ("planning-gate",  "check"):    cmd_planning_gate_check,
        ("planning-gate",  "submit"):   cmd_planning_gate_submit,
        ("notepad",        "add"):      cmd_notepad_add,
        ("notepad",        "list"):     cmd_notepad_list,
        ("notepad",        "clear"):    cmd_notepad_clear,
        ("validation",     "add"):      cmd_validation_add,
        ("validation",     "completeness-check"): cmd_validation_completeness_check,
        ("validation",     "check-gate"): cmd_validation_check_gate,
        ("subagent-return", "record"):  cmd_subagent_return_record,
        ("registry", "add"):           cmd_registry_add,
        ("registry", "update"):        cmd_registry_update,
        ("registry", "list"):          cmd_registry_list,
        ("registry", "remove"):        cmd_registry_remove,
        ("registry", "dedup"):         cmd_registry_dedup,
        ("registry", "health"):        cmd_registry_health,
        ("feedback", "add"):            cmd_feedback_add,
        ("feedback", "harvest"):        cmd_feedback_harvest,
        ("feedback", "resolve"):        cmd_feedback_resolve,
        ("rca",        "add"):          cmd_rca_add,
        ("reflection", "add"):          cmd_reflection_add,
        ("vec",        "backfill"):     cmd_vec_backfill,
        ("improvements", "populate"):   cmd_improvements_populate,
        ("improvements", "list"):       cmd_improvements_list,
        ("improvements", "flag"):       cmd_improvements_flag,
        ("improvements", "evaluate"):   cmd_improvements_evaluate,
        ("improvements", "dismiss"):    cmd_improvements_dismiss,
        ("improvements", "dashboard"):  cmd_improvements_dashboard,
        ("activity", "start"):          cmd_activity_start,
        ("activity", "update"):         cmd_activity_update,
        ("activity", "end"):            cmd_activity_end,
        ("dispatch", "record"):         cmd_dispatch_record,
        ("dispatch", "ingest-journal"): cmd_dispatch_ingest_journal,
        ("skill",    "record-load"):    cmd_skill_record_load,
        ("gate",     "stats"):          cmd_gate_stats,
    }
    key = (args.command, args.subcommand)
    if key in sub_dispatch:
        sub_dispatch[key](args)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

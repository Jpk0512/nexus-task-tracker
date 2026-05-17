#!/usr/bin/env python3
"""Project memory CLI — log sessions, tasks, decisions, and context snapshots."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "project.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
TASKS_MD_PATH = Path(__file__).resolve().parent.parent / "docs" / "TASKS.md"
MEMORY_FILES_DIR = Path(__file__).parent / "files"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(conn: sqlite3.Connection, table: str, prefix: str) -> str:
    cur = conn.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        return f"{prefix}-001"
    last_num = int(row["id"].split("-")[-1])
    return f"{prefix}-{last_num + 1:03d}"


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


# ---------------------------------------------------------------------------
# sqlite-vec helpers (Phase D Layer 2 — semantic memory)
# ---------------------------------------------------------------------------

_LM_STUDIO_EMBED_URL = "http://127.0.0.1:1234/v1/embeddings"
_EMBED_MODEL = "nomic-embed-text-v1.5"
_EMBED_DIM = 768


def _vec_conn() -> sqlite3.Connection:
    """Return a connection with sqlite-vec extension loaded. Raises on failure."""
    import sqlite_vec as _sv
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    _sv.load(conn)
    conn.enable_load_extension(False)
    return conn


def _l2_normalize(vec: list[float]) -> list[float]:
    import math as _math
    norm = _math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _embed(text: str) -> list[float] | None:
    """Call LM Studio embed endpoint. Returns None if unavailable."""
    import urllib.request as _req
    import urllib.error as _uerr
    payload = json.dumps({"model": _EMBED_MODEL, "input": text}).encode()
    request = _req.Request(
        _LM_STUDIO_EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _req.urlopen(request, timeout=5) as resp:
            body = json.loads(resp.read())
            vec = body["data"][0]["embedding"]
            return _l2_normalize(vec)
    except (_uerr.URLError, KeyError, IndexError, json.JSONDecodeError):
        return None


def _vec_insert(
    conn: sqlite3.Connection,
    kind: str,
    ref_id: str,
    text_blob: str,
    created_at: str,
) -> None:
    """Embed text_blob and insert into vec_memory. Degrades gracefully if embed unavailable."""
    import sqlite_vec as _sv
    vec = _embed(text_blob)
    if vec is None:
        print(
            f"vec_memory: embed unavailable for {kind} {ref_id} — skipping vector write",
            file=sys.stderr,
        )
        return
    blob = _sv.serialize_float32(vec)
    conn.execute(
        "INSERT INTO vec_memory(kind, ref_id, text_blob, created_at, embedding) VALUES (?,?,?,?,?)",
        (kind, ref_id, text_blob, created_at, blob),
    )


def _apply_M001(conn: sqlite3.Connection) -> None:
    """Idempotent: create vec_memory virtual table if not present (requires extension loaded)."""
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_memory'"
    ).fetchone()
    if existing:
        return
    conn.executescript("""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    kind TEXT PARTITION KEY,
    ref_id TEXT,
    text_blob TEXT,
    created_at TEXT,
    embedding float[768]
);
""")


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
        _migrate_tasks_stall_columns(conn)
    # Apply M-001 vec_memory via extension-loaded conn (fails open if extension absent)
    try:
        with _vec_conn() as vconn:
            _apply_M001(vconn)
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory migration skipped (sqlite-vec unavailable): {exc}", file=sys.stderr)
    print(f"Initialized {DB_PATH}")


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------

def cmd_session_start(args: argparse.Namespace) -> None:
    now = _now()
    # Use date-based ID: S20260510-143000
    sid = "S" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, started_at, branch) VALUES (?, ?, ?)",
            (sid, now, getattr(args, "branch", "main") or "main"),
        )
    print(json.dumps({"session_id": sid, "started_at": now}))


def cmd_session_end(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("No open session found.", file=sys.stderr)
            sys.exit(1)
        sid = row["id"]
        conn.execute(
            "UPDATE sessions SET ended_at=?, summary=?, next_step=? WHERE id=?",
            (now, args.summary, args.next_step, sid),
        )
    print(json.dumps({"session_id": sid, "ended_at": now, "summary": args.summary}))
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
    threshold = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
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

        new_sid = "S" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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
    threshold_2h = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
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


# ---------------------------------------------------------------------------
# task
# ---------------------------------------------------------------------------

def cmd_task_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        tid = args.id or _next_id(conn, "tasks", "TASK")
        conn.execute(
            """INSERT OR REPLACE INTO tasks
               (id, feature_id, title, description, status, priority, assigned_to,
                acceptance_criteria, created_at, updated_at, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
    print(json.dumps({"task_id": tid, "status": args.status or "todo"}))


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
    now = _now()
    with _conn() as conn:
        did = args.id or _next_id(conn, "decisions", "DEC")
        # Get current open session id
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        session_id = row["id"] if row else None
        conn.execute(
            """INSERT OR REPLACE INTO decisions
               (id, title, status, context, decision, rationale, alternatives, consequences, decided_at, session_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                did,
                args.title,
                args.status or "accepted",
                args.context,
                args.decision,
                args.rationale,
                args.alternatives,
                args.consequences,
                now,
                session_id,
            ),
        )
    print(json.dumps({"decision_id": did, "decided_at": now}))
    # Embed side-effect — degrades gracefully if LM Studio unavailable
    try:
        text_blob = f"context: {args.context}\ndecision: {args.decision}\nrationale: {args.rationale}"
        with _vec_conn() as vconn:
            _vec_insert(vconn, "decision", did, text_blob, now)
    except Exception as exc:  # noqa: BLE001
        print(f"vec_memory: embed side-effect skipped for {did}: {exc}", file=sys.stderr)


def cmd_decision_list(_args: argparse.Namespace) -> None:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, status, decided_at FROM decisions ORDER BY id"
        ).fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2))


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
    now = _now()
    with _conn() as conn:
        lid = args.id or _next_id(conn, "lessons", "LSN")
        # Get current open session for attribution
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        sid = row["id"] if row else None
        conn.execute(
            """INSERT OR REPLACE INTO lessons
               (id, trigger, title, body, applies_to, source_session_id,
                source_decision_id, validated, recorded_at, validated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                lid,
                args.trigger,
                args.title,
                args.body,
                args.applies_to or "all",
                sid,
                args.source_decision_id,
                1 if args.validated else 0,
                now,
                now if args.validated else None,
            ),
        )
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
    where, vals = [], []
    if args.validated is not None:
        where.append("validated=?")
        vals.append(1 if args.validated else 0)
    if args.applies_to:
        where.append("(applies_to='all' OR applies_to LIKE ?)")
        vals.append(f"%{args.applies_to}%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, trigger, title, applies_to, validated, recorded_at FROM lessons {clause} ORDER BY id",
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
            cutoff = (datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))).isoformat()

    # Embed query
    query_vec = _embed(query)
    if query_vec is None:
        print(
            "recall: embed endpoint unavailable — cannot perform semantic search",
            file=sys.stderr,
        )
        print(json.dumps([]))
        return

    import sqlite_vec as _sv
    query_blob = _sv.serialize_float32(query_vec)
    fetch_k = top_k if cutoff is None else top_k * 10

    try:
        with _vec_conn() as conn:
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
        print(f"recall: query failed: {exc}", file=sys.stderr)
        print(json.dumps([]))
        return

    results = [dict(r) for r in rows]
    if cutoff:
        results = [r for r in results if r["created_at"] >= cutoff]
    results = results[:top_k]
    print(json.dumps(results, indent=2))


# ---------------------------------------------------------------------------
# semantic_facts (Phase 3 — Technique 3, semantic tier)
# ---------------------------------------------------------------------------

def cmd_fact_add(args: argparse.Namespace) -> None:
    now = _now()
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        sid = row["id"] if row else None
        conn.execute(
            """INSERT OR REPLACE INTO semantic_facts
               (id, key, value, source_session_id, source_decision_id, created_at, decayed_at, pinned)
               VALUES (
                 (SELECT id FROM semantic_facts WHERE key=?),
                 ?, ?, ?, ?,
                 COALESCE((SELECT created_at FROM semantic_facts WHERE key=?), ?),
                 NULL,
                 ?
               )""",
            (
                args.key,
                args.key,
                args.value,
                sid,
                args.source_decision_id,
                args.key,
                now,
                1 if args.pinned else 0,
            ),
        )
    print(json.dumps({"key": args.key, "pinned": bool(args.pinned)}))


def cmd_fact_list(args: argparse.Namespace) -> None:
    where, vals = ["decayed_at IS NULL"], []
    if args.pinned_only:
        where.append("pinned=1")
    if args.key_like:
        where.append("key LIKE ?")
        vals.append(f"%{args.key_like}%")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT key, value, pinned, source_decision_id, created_at FROM semantic_facts {clause} ORDER BY key",
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

    ctx_threshold = (datetime.now(timezone.utc) - timedelta(days=ctx_ttl)).isoformat()
    fact_threshold = (datetime.now(timezone.utc) - timedelta(days=fact_ttl)).isoformat()

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


def cmd_context_dump(_args: argparse.Namespace) -> None:
    with _conn() as conn:
        session = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        open_tasks = conn.execute(
            "SELECT id, title, status, priority, assigned_to FROM tasks WHERE status NOT IN ('done','cancelled') ORDER BY id"
        ).fetchall()
        recent_decisions = conn.execute(
            "SELECT id, title, status, decided_at FROM decisions ORDER BY decided_at DESC LIMIT 10"
        ).fetchall()
    out = {
        "last_session": dict(session) if session else None,
        "open_tasks": [dict(r) for r in open_tasks],
        "recent_decisions": [dict(r) for r in recent_decisions],
    }
    print(json.dumps(out, indent=2))


# ---------------------------------------------------------------------------
# planning-gate
# ---------------------------------------------------------------------------

def cmd_planning_gate_check(args: argparse.Namespace) -> None:
    feat_id = args.feat  # e.g. "FEAT-001"
    docs_root = Path(__file__).resolve().parent.parent / "docs"
    spec_glob = list(docs_root.glob(f"features/{feat_id}-*.md"))

    results: list[dict] = []

    def check(item: int, title: str, passed: bool, detail: str = "") -> None:
        results.append({"item": item, "title": title, "passed": passed, "detail": detail})

    # Item 1 — spec file exists
    spec_path = spec_glob[0] if spec_glob else None
    check(1, "Spec file exists", spec_path is not None,
          str(spec_path) if spec_path else f"docs/features/{feat_id}-*.md not found")

    spec_text = spec_path.read_text() if spec_path else ""

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
    # Derive the feature slug from the spec filename (e.g. FEAT-006-worksheet-level-search.md → worksheet-level-search)
    spec_slug = ""
    if spec_path is not None:
        m = re.match(rf"{re.escape(feat_id)}-(.+)\.md$", spec_path.name)
        if m:
            spec_slug = m.group(1)
    feature_globs: list[str] = []
    for num in {feat_num, feat_num_padded}:
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
        f"'{spec_slug or '<missing>'}' under ingestion/tests/ or app/**. "
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

    `docs/TASKS.md` is the human-edited source of truth for tasks. This parser exists so
    `seed` cannot drift from the doc — both the live DB and the bootstrap path read from
    the same file. Tasks under a `## FEAT-NNN` heading inherit that feature_id; tasks
    under any other `## ...` heading (e.g. "Infrastructure / Housekeeping") have
    feature_id=None.
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
    if len(words) <= 6:
        return True
    return False


# ---------------------------------------------------------------------------
# validation (lens-gate hook)
# ---------------------------------------------------------------------------

def cmd_validation_add(args: argparse.Namespace) -> None:
    """Record a Lens validation row in validation_log.

    Lens calls this as its last action before returning NEXUS:DONE so that
    lens-gate.sh can find the matching row for the implementer's task hash.
    """
    import hashlib as _hashlib
    now = _now()
    task_hash = args.task_hash
    verdict = args.verdict.upper()
    if verdict not in ("PASS", "PARTIAL", "FAIL"):
        print(
            f"validation rejected: verdict must be PASS, PARTIAL, or FAIL (got {args.verdict!r})",
            file=sys.stderr,
        )
        sys.exit(1)
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
        conn.execute(
            """INSERT INTO validation_log
               (session_id, agent_validated, target_agent, task_or_brief_hash, verdict, evidence_summary, validated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, args.agent, args.target, task_hash, verdict, args.summary, now),
        )
    print(json.dumps({
        "recorded": True,
        "agent_validated": args.agent,
        "target_agent": args.target,
        "task_or_brief_hash": task_hash,
        "verdict": verdict,
        "validated_at": now,
    }))


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
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
    try:
        cmd_notepad_add(notepad_args)
    except SystemExit:
        # notepad rejected (e.g. status-restatement check); still report success.
        pass

    print(json.dumps({
        "persisted": str(dest),
        "approx_tokens": approx_tokens,
        "topic": topic,
        "insight": insight,
    }))


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

    # task
    tp = sub.add_parser("task")
    tsp = tp.add_subparsers(dest="subcommand", required=True)
    ta = tsp.add_parser("add")
    ta.add_argument("--id")
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
    ti = tsp.add_parser("stall", help="Atomically increment stall_count (compare-and-swap)")
    ti.add_argument("--task-id", dest="task_id", required=True, help="TASK-NNN or memory task id")
    ti.add_argument("--persona", required=True, help="Persona name that produced the stall marker")
    ti.add_argument("--marker", required=True, choices=["REVISE", "BLOCKED", "revise", "blocked"],
                    help="The marker type: REVISE or BLOCKED")

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
    da.add_argument("--alternatives", required=True,
                    help="Other options considered + why rejected — required")
    da.add_argument("--consequences", required=True,
                    help="What this decision implies / who is impacted — required")
    dsp.add_parser("list")

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
    csp.add_parser("dump")

    sub.add_parser("seed", help="Seed tasks from docs/TASKS.md")

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
    va.add_argument("--verdict", required=True, help="PASS | PARTIAL | FAIL")
    va.add_argument("--summary", default="", help="One-line evidence summary (optional)")

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

    args = p.parse_args()

    dispatch = {
        "init": cmd_init,
        "seed": cmd_seed,
        "recall": cmd_recall,
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
        ("task",    "add"):      cmd_task_add,
        ("task",    "update"):   cmd_task_update,
        ("task",    "list"):     cmd_task_list,
        ("task",    "stall"):    cmd_stall_increment,
        ("decision","add"):      cmd_decision_add,
        ("decision","list"):     cmd_decision_list,
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
        ("subagent-return", "record"):  cmd_subagent_return_record,
        ("rca",        "add"):          cmd_rca_add,
        ("reflection", "add"):          cmd_reflection_add,
    }
    key = (args.command, args.subcommand)
    if key in sub_dispatch:
        sub_dispatch[key](args)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

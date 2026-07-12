"""Warm cache of recent `sessions`/`context_log` rows — the 2.9 session-digest
substrate (`plans/08-daemon-capability-catalog.md` §2.9), scoped to answer
ONE "what happened last session" question instead of a cold SQLite scan on
every `SessionStart` hook fire. R5-T04's "multi-session SessionStart digest"
consumes this substrate (`TASKS.md` R5-T04 row).

`project.db` stays authoritative (`plans/07` §1 constraint 1): this module is
cache-only, never a write path and never a second source of truth. Wiring a
`session_digest` RPC method into `server.py`'s `handle_request` dispatch and
the live `SessionStart` hook consumer are later work (`server.py` is out of
this node's write scope) — this node ships the substrate: a direct (cold)
query function, a warm cache mirroring `_SchemaCache`'s TTL+mtime
invalidation shape (`server.py`), and a fail-closed `get_session_digest()`
wrapper matching `fallback.py`'s shape (daemon RPC first, falls back to the
direct query on `DaemonUnavailable`) — the SessionStart-consumer entry point
this node's acceptance criteria name.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from broker.daemon.client import DaemonUnavailable, call

# Recent context_log rows held per session digest — a bound, not a promise of
# completeness; "recent" per plans/08 §2.9's own wording, not "all history".
CONTEXT_LOG_LIMIT = 50


def query_session_digest_direct(db_path: Path) -> dict[str, Any]:
    """Cold, uncached scan: the most-recently-started `sessions` row plus its
    `context_log` entries, oldest-first. Missing/unreadable db_path -> an
    empty digest (same posture as `schema_scan.scan_schema`: an absent DB is
    an empty answer, not an error the cache should raise on). This is both
    the cache-fill query AND the direct-fallback query — one implementation,
    never two that could drift (mirrors `registry_scan.py`'s framing).
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return {"session": None, "context_log": []}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id, started_at, ended_at, summary, last_step, next_step, "
            "branch, user_message_count FROM sessions ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"session": None, "context_log": []}
        session = {
            "id": row[0],
            "started_at": row[1],
            "ended_at": row[2],
            "summary": row[3],
            "last_step": row[4],
            "next_step": row[5],
            "branch": row[6],
            "user_message_count": row[7],
        }
        # Most-recent CONTEXT_LOG_LIMIT rows first (DESC), then re-ordered
        # oldest-first for chronological readability — "recent rows held
        # warm" means the newest window, never the oldest N rows silently
        # truncating a long session's tail off the end.
        log_rows = list(
            reversed(
                conn.execute(
                    "SELECT id, logged_at, action_type, files_modified, decision_refs, "
                    "task_updates, summary FROM context_log WHERE session_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (session["id"], CONTEXT_LOG_LIMIT),
                ).fetchall()
            )
        )
        context_log = [
            {
                "id": r[0],
                "logged_at": r[1],
                "action_type": r[2],
                "files_modified": r[3],
                "decision_refs": r[4],
                "task_updates": r[5],
                "summary": r[6],
            }
            for r in log_rows
        ]
        return {"session": session, "context_log": context_log}
    finally:
        conn.close()


class SessionDigestCache:
    """1.1-shaped warm cache — TTL/mtime-invalidated, mirrors `server.py`'s
    `_SchemaCache`. One instance per daemon `DaemonState` (not wired into
    `DaemonState` by this node — `server.py` is out of write scope; a later
    node adds the `session_digest` RPC method + the `DaemonState` field that
    holds an instance of this class).
    """

    def __init__(self, db_path: Path, ttl_s: float = 30.0) -> None:
        self.db_path = Path(db_path)
        self.ttl_s = ttl_s
        self._digest: dict[str, Any] | None = None
        self._loaded_at = 0.0
        self._mtime: float | None = None

    def get(self) -> dict[str, Any]:
        now = time.monotonic()
        mtime = self.db_path.stat().st_mtime if self.db_path.is_file() else None
        stale = (now - self._loaded_at) > self.ttl_s
        if self._digest is None or mtime != self._mtime or stale:
            self._digest = query_session_digest_direct(self.db_path)
            self._mtime = mtime
            self._loaded_at = now
        return self._digest


def get_session_digest(
    project_path: Path,
    *,
    allow_spawn: bool = True,
) -> dict[str, Any]:
    """Fail-closed session-digest read, matching `fallback.py`'s shape: try
    the daemon's `session_digest` RPC, fall back to a direct (uncached)
    `project.db` scan on `DaemonUnavailable` — the SessionStart-consumer
    entry point this node's acceptance criteria name. A daemon that is
    reachable but does not yet register a `session_digest` method (the
    `server.py` RPC wiring is later work) answers with a dispatch error,
    which the client wraps as `DaemonUnavailable` too — this wrapper treats
    that identically to an unreachable daemon and falls back to the direct
    read below, which is the correct fail-closed behavior either way: the
    caller never gets a wrong or partial answer, only "warm" or "direct".
    """
    try:
        result = call(project_path, "session_digest", {}, spawn_if_missing=allow_spawn)
        return {
            "session": result["session"],
            "context_log": result["context_log"],
            "source": "daemon",
        }
    except DaemonUnavailable:
        db_path = Path(project_path) / ".memory" / "project.db"
        digest = query_session_digest_direct(db_path)
        return {
            "session": digest["session"],
            "context_log": digest["context_log"],
            "source": "direct-fallback",
        }

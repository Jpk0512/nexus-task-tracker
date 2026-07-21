"""R4 Phase-B (post-reversibility-gate) — plans/08 §2.9 session digest.

Covers the substrate this node ships: a direct (cold) `sessions`/
`context_log` query, a warm `SessionDigestCache` mirroring `server.py`'s
`_SchemaCache` TTL+mtime invalidation shape, and the fail-closed
`get_session_digest()` SessionStart-consumer entry point. `server.py`'s
`session_digest` RPC method + `DaemonState` wiring are later work (out of
this node's write scope) — the daemon-down fail-closed drill below exercises
exactly the same "no daemon reachable -> direct read" path `fallback.py`'s
own tests use (`allow_spawn=False`, no listener at the isolated socket).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.daemon import session_digest

SCHEMA_SQL = """
CREATE TABLE sessions (
    id                  TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    summary             TEXT,
    last_step           TEXT,
    next_step           TEXT,
    branch              TEXT DEFAULT 'main',
    context_json        TEXT,
    user_message_count  INTEGER DEFAULT 0,
    last_reset_at       TIMESTAMP
);
CREATE TABLE context_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    logged_at   TEXT NOT NULL,
    action_type TEXT,
    files_modified TEXT,
    decision_refs  TEXT,
    task_updates   TEXT,
    summary     TEXT
);
"""


def _make_project(root: Path) -> Path:
    project = root / "proj"
    (project / ".memory").mkdir(parents=True)
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return project


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    started_at: str,
    *,
    ended_at: str | None = None,
    summary: str | None = None,
    last_step: str | None = None,
    next_step: str | None = None,
    branch: str = "main",
    user_message_count: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO sessions (id, started_at, ended_at, summary, last_step, "
        "next_step, branch, user_message_count) VALUES (?,?,?,?,?,?,?,?)",
        (session_id, started_at, ended_at, summary, last_step, next_step, branch, user_message_count),
    )


def _insert_context_log(
    conn: sqlite3.Connection,
    session_id: str,
    logged_at: str,
    *,
    action_type: str = "code_change",
    files_modified: str | None = None,
    decision_refs: str | None = None,
    task_updates: str | None = None,
    summary: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO context_log (session_id, logged_at, action_type, files_modified, "
        "decision_refs, task_updates, summary) VALUES (?,?,?,?,?,?,?)",
        (session_id, logged_at, action_type, files_modified, decision_refs, task_updates, summary),
    )


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path)


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # Same short-dir-under-/tmp rationale as test_daemon_pilot.py: AF_UNIX
    # paths are capped at ~104 bytes on macOS/BSD, and pytest's tmp_path is
    # too long for bind()/connect() to succeed.
    sock_dir = Path(tempfile.mkdtemp(prefix="nxsd", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


def test_query_session_digest_direct_missing_db_returns_empty(tmp_path, snapshot: SnapshotAssertion):
    result = session_digest.query_session_digest_direct(tmp_path / "no-such.db")
    # envelope fixture: the empty-digest RPC response shape, reviewed via snapshot (F3-04).
    assert result == snapshot(name="empty_digest_envelope")


def test_query_session_digest_direct_no_sessions_returns_empty(project, snapshot: SnapshotAssertion):
    db_path = project / ".memory" / "project.db"
    result = session_digest.query_session_digest_direct(db_path)
    assert result == snapshot(name="empty_digest_envelope")


def test_query_session_digest_direct_returns_latest_session_with_context_log(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(db_path)
    try:
        _insert_session(conn, "S1", "2026-07-08T08:00:00", summary="older session")
        _insert_session(
            conn,
            "S2",
            "2026-07-08T10:00:00",
            ended_at="2026-07-08T11:00:00",
            summary="shipped the digest substrate",
            last_step="wrote session_digest.py",
            next_step="wire the RPC method",
            user_message_count=7,
        )
        _insert_context_log(conn, "S2", "2026-07-08T10:10:00", summary="first")
        _insert_context_log(conn, "S2", "2026-07-08T10:20:00", summary="second")
        # A row on the OLDER session must never leak into S2's digest.
        _insert_context_log(conn, "S1", "2026-07-08T08:05:00", summary="belongs to S1")
        conn.commit()
    finally:
        conn.close()

    result = session_digest.query_session_digest_direct(db_path)

    assert result["session"]["id"] == "S2"
    assert result["session"]["summary"] == "shipped the digest substrate"
    assert result["session"]["last_step"] == "wrote session_digest.py"
    assert result["session"]["next_step"] == "wire the RPC method"
    assert result["session"]["user_message_count"] == 7
    assert [row["summary"] for row in result["context_log"]] == ["first", "second"]


def test_query_session_digest_direct_caps_to_most_recent_window(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(db_path)
    try:
        _insert_session(conn, "S1", "2026-07-08T08:00:00")
        total = session_digest.CONTEXT_LOG_LIMIT + 10
        for i in range(total):
            _insert_context_log(conn, "S1", f"2026-07-08T09:{i:04d}", summary=f"entry-{i}")
        conn.commit()
    finally:
        conn.close()

    result = session_digest.query_session_digest_direct(db_path)

    assert len(result["context_log"]) == session_digest.CONTEXT_LOG_LIMIT
    # The window is the newest LIMIT rows, oldest-first within that window —
    # never the oldest N rows silently truncating the recent tail off.
    expected = [f"entry-{i}" for i in range(10, total)]
    assert [row["summary"] for row in result["context_log"]] == expected


def test_session_digest_cache_matches_direct_query_byte_for_byte(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(db_path)
    try:
        _insert_session(
            conn,
            "S1",
            "2026-07-08T09:00:00",
            ended_at="2026-07-08T10:00:00",
            summary="shipped X",
            last_step="ran tests",
            next_step="ship Y",
            user_message_count=12,
        )
        _insert_context_log(
            conn,
            "S1",
            "2026-07-08T09:10:00",
            files_modified=json.dumps(["a.py"]),
            decision_refs=json.dumps(["DEC-001"]),
            task_updates=json.dumps([{"id": "TASK-1", "status": "done"}]),
            summary="did A",
        )
        _insert_context_log(conn, "S1", "2026-07-08T09:20:00", summary="did B")
        conn.commit()
    finally:
        conn.close()

    cache = session_digest.SessionDigestCache(db_path)
    warm = cache.get()
    direct = session_digest.query_session_digest_direct(db_path)

    assert json.dumps(warm, sort_keys=True) == json.dumps(direct, sort_keys=True)


def test_session_digest_cache_serves_warm_then_invalidates_on_mtime_change(project, monkeypatch):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(db_path)
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00")
        conn.commit()
    finally:
        conn.close()

    calls = {"n": 0}
    orig = session_digest.query_session_digest_direct

    def counting(path):
        calls["n"] += 1
        return orig(path)

    monkeypatch.setattr(session_digest, "query_session_digest_direct", counting)

    cache = session_digest.SessionDigestCache(db_path, ttl_s=3600.0)
    first = cache.get()
    second = cache.get()

    assert calls["n"] == 1  # warm cache answered the second call, no re-query
    assert first == second
    assert first["session"]["id"] == "S1"

    conn = sqlite3.connect(db_path)
    try:
        _insert_session(conn, "S2", "2026-07-08T11:00:00")
        conn.commit()
    finally:
        conn.close()
    future = time.time() + 5
    os.utime(db_path, (future, future))  # force an observable mtime change

    third = cache.get()

    assert calls["n"] == 2  # mtime change invalidated the warm entry
    assert third["session"]["id"] == "S2"


def test_get_session_digest_daemon_down_falls_back_to_direct_read(project, isolated_sockets):
    """Fail-closed drill: no daemon listening at the isolated socket, spawn
    disabled -> get_session_digest() must fall back to the exact same direct
    read query_session_digest_direct() would produce, never a wrong or
    partial answer.
    """
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(db_path)
    try:
        _insert_session(
            conn,
            "S1",
            "2026-07-08T10:00:00",
            summary="did the thing",
            last_step="wrote code",
            next_step="verify",
        )
        _insert_context_log(conn, "S1", "2026-07-08T10:05:00", summary="first step")
        conn.commit()
    finally:
        conn.close()

    result = session_digest.get_session_digest(project, allow_spawn=False)

    assert result["source"] == "direct-fallback"
    direct = session_digest.query_session_digest_direct(db_path)
    assert result["session"] == direct["session"]
    assert result["context_log"] == direct["context_log"]


def test_get_session_digest_daemon_down_empty_project_still_answers(
    project, isolated_sockets, snapshot: SnapshotAssertion
):
    """No daemon AND no session rows yet — must return an empty digest, not
    raise, so a cold SessionStart on a brand-new project never breaks.
    """
    result = session_digest.get_session_digest(project, allow_spawn=False)

    assert result == snapshot(name="empty_digest_fallback_envelope")

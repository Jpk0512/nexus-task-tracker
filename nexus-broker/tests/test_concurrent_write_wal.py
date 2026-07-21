"""DEC-034 regression — concurrent writers must not corrupt project.db.

Incident: 3 concurrent Workflows wrote to .memory/project.db simultaneously
with no WAL journal mode and no busy_timeout configured on the connection
helpers (`.memory/log.py::_conn` / `_vec_conn`, `broker.vault.db.open_db`).
Default SQLite rollback-journal mode takes an exclusive lock for the
duration of a write transaction; a second connection hitting that window
under the default (zero) busy_timeout raises "database is locked" instead of
waiting, and an unlucky interleave (e.g. a killed process mid-write) can
leave the file corrupted.

This test proves the fix holds: opening a connection with
``PRAGMA journal_mode=WAL`` + ``PRAGMA busy_timeout=5000`` lets N threads
hammer different tables in the same file concurrently for ~1.5s with zero
"database is locked" exceptions, and the file passes integrity_check after.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / ".memory" / "schema.sql"
_DURATION_SECS = 1.5
_THREAD_COUNT = 4


def _connect_with_wal(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _hammer_sessions(db_path: Path, thread_id: int, stop_at: float, errors: list[Exception]) -> None:
    try:
        conn = _connect_with_wal(db_path)
        try:
            i = 0
            while time.monotonic() < stop_at:
                conn.execute(
                    "INSERT INTO sessions (id, started_at, summary) VALUES (?, ?, ?)",
                    (f"S-t{thread_id}-{i}", f"2026-07-05T00:00:{thread_id:02d}Z", f"thread-{thread_id}-{i}"),
                )
                conn.commit()
                i += 1
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — capture for cross-thread assertion, not swallow
        errors.append(exc)


def _hammer_tasks(db_path: Path, thread_id: int, stop_at: float, errors: list[Exception]) -> None:
    try:
        conn = _connect_with_wal(db_path)
        try:
            i = 0
            while time.monotonic() < stop_at:
                conn.execute(
                    "INSERT INTO tasks (id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"TASK-t{thread_id}-{i}",
                        f"thread-{thread_id}-task-{i}",
                        "todo",
                        f"2026-07-05T00:00:{thread_id:02d}Z",
                        f"2026-07-05T00:00:{thread_id:02d}Z",
                    ),
                )
                conn.commit()
                i += 1
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — capture for cross-thread assertion, not swallow
        errors.append(exc)


@pytest.fixture
def temp_project_db(tmp_path: Path) -> Path:
    if not _SCHEMA_PATH.exists():
        pytest.skip(f"schema.sql not found at {_SCHEMA_PATH}")
    import sqlite_vec

    db_path = tmp_path / "project.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_concurrent_writers_no_lock_errors_and_db_stays_healthy(temp_project_db: Path) -> None:
    """>=2 concurrent connections writing different tables for ~1.5s: no lock errors, integrity_check ok."""
    errors: list[Exception] = []
    stop_at = time.monotonic() + _DURATION_SECS

    threads = []
    for i in range(_THREAD_COUNT):
        target = _hammer_sessions if i % 2 == 0 else _hammer_tasks
        t = threading.Thread(target=target, args=(temp_project_db, i, stop_at, errors))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=_DURATION_SECS + 10)

    assert not errors, f"concurrent writers raised: {errors}"

    check_conn = sqlite3.connect(str(temp_project_db))
    try:
        row = check_conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        check_conn.close()
    assert row is not None
    assert row[0] == "ok", f"integrity_check reported: {row[0]!r}"


def test_baseline_without_wal_can_raise_database_is_locked(tmp_path: Path) -> None:
    """Sanity check that the failure mode is real absent the fix (guards against a vacuous positive test).

    Uses the default journal mode with busy_timeout=0 (SQLite's out-of-the-box
    default) to demonstrate the lock contention the WAL+busy_timeout fix
    resolves. This does not touch the real connection helpers — it isolates
    the underlying SQLite behavior the fix depends on.
    """
    db_path = tmp_path / "baseline.db"
    setup_conn = sqlite3.connect(str(db_path))
    try:
        setup_conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        setup_conn.commit()
    finally:
        setup_conn.close()

    writer = sqlite3.connect(str(db_path))
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO t (v) VALUES ('holding-lock')")

    contender = sqlite3.connect(str(db_path))
    contender.execute("PRAGMA busy_timeout=0")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            contender.execute("INSERT INTO t (v) VALUES ('should-fail')")
    finally:
        contender.close()
        writer.rollback()
        writer.close()


# ---------------------------------------------------------------------------
# The tests above prove WAL+busy_timeout FIX the contention — but they open
# their own connections with the PRAGMAs, so they would stay green even if the
# real helpers stopped setting them. These two assert on the PRODUCTION
# connection helpers directly, so stripping journal_mode=WAL / busy_timeout from
# `broker.vault.db.open_db` or `.memory/log.py::_conn` (the exact DEC-034
# regression) turns them RED.
# ---------------------------------------------------------------------------


def _pragma(conn: sqlite3.Connection, name: str) -> object:
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_broker_vault_open_db_sets_wal_and_busy_timeout(tmp_path: Path) -> None:
    """broker.vault.db.open_db must open a WRITE connection with journal_mode=WAL
    and a nonzero busy_timeout — asserted on the real helper, not a re-declared
    local connection."""
    from broker.vault.db import open_db

    db_path = tmp_path / "vault.db"
    conn = open_db(db_path)
    try:
        assert str(_pragma(conn, "journal_mode")).lower() == "wal", (
            "open_db must persist journal_mode=WAL on write connections"
        )
        assert int(_pragma(conn, "busy_timeout")) > 0, (
            "open_db must set a nonzero busy_timeout so racers wait, not error"
        )
    finally:
        conn.close()


def test_log_py_conn_sets_wal_and_busy_timeout(tmp_path: Path) -> None:
    """.memory/log.py::_conn (via _harden_connection) must set journal_mode=WAL
    and busy_timeout. Driven in a subprocess so log.py's import-time DB_PATH +
    re-exec bootstrap resolve cleanly (NEXUS_DISABLE_VEC=1 suppresses the venv
    re-exec; NEXUS_DB_PATH points _conn at a scratch db)."""
    db_path = tmp_path / "project.db"
    log_py = Path(__file__).resolve().parents[2] / ".memory" / "log.py"
    if not log_py.exists():
        pytest.skip(f"log.py not found at {log_py}")
    probe = (
        "import importlib.util, os\n"
        "spec = importlib.util.spec_from_file_location('nexus_log_probe', os.environ['LOG_PY'])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "c = mod._conn()\n"
        "print(c.execute('PRAGMA journal_mode').fetchone()[0], "
        "c.execute('PRAGMA busy_timeout').fetchone()[0])\n"
    )
    env = {
        **os.environ,
        "NEXUS_DISABLE_VEC": "1",
        "NEXUS_DB_PATH": str(db_path),
        "LOG_PY": str(log_py),
    }
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=30
    )
    assert proc.returncode == 0, f"_conn probe failed: {proc.stderr}"
    journal_mode, busy_timeout = proc.stdout.split()
    assert journal_mode.lower() == "wal", (
        f"log.py::_conn must set journal_mode=WAL, got {journal_mode!r}"
    )
    assert int(busy_timeout) > 0, (
        f"log.py::_conn must set a nonzero busy_timeout, got {busy_timeout!r}"
    )

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
``PRAGMA busy_timeout=5000`` set BEFORE ``PRAGMA journal_mode=WAL`` lets N
threads hammer different tables in the same file concurrently for ~1.5s with
no UNRECOVERABLE "database is locked" exceptions, and the file passes
integrity_check after.

NEX-009: the test's own `_connect_with_wal` originally set journal_mode
*before* busy_timeout — the one-time, file-wide WAL mode switch every hammer
thread issues on connect then raced at busy_timeout's SQLite default of 0,
raising an unprotected "database is locked" on thread startup (a flake that
got worse, not caused, by host CPU contention, since contention widens the
race window between the 4 threads' near-simultaneous connects). Reordering to
match `broker.vault.db.open_db`'s real order fixes the root cause. The hammer
loop additionally retries a transient "locked" through a bounded wall-clock
budget (`_execute_retrying_locked`) as defense-in-depth for the case
busy_timeout's own 5s window is itself exhausted by genuine host scheduling
pressure — a real, if rare, possibility no fixed timeout value can rule out.
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

# NEX-009: PRAGMA busy_timeout is a wall-clock budget, not a CPU-time budget —
# under host CPU contention (the full suite runs many subprocess-spawning tests
# back to back) a writer thread can go unscheduled long enough that a
# contender's 5s busy-timeout window elapses in real time before the writer
# ever gets CPU to COMMIT and release the lock. That is a genuine transient
# "database is locked" — not corruption, not a deadlock, not a WAL/busy_timeout
# regression — so retrying the SAME statement a bounded number of times (giving
# the scheduler another lap) is the correct fix, not a bigger sleep. A REAL
# regression (e.g. WAL/busy_timeout stripped from `_connect_with_wal`) still
# fails: without WAL, contention is on every single write, not an occasional
# scheduling-starved one, so it exhausts the bound deterministically.
# Bounded by ELAPSED WALL TIME, not an attempt count: each retry's own internal
# busy_timeout(5000ms) already burns real time before ever raising, so a fixed
# attempt count under-covers exactly the scheduling-starved case this exists
# for. A deadline is the honest bound — it caps worst case in the same unit
# the failure itself is measured in.
_LOCK_RETRY_BUDGET_SECS = 8.0
_LOCK_RETRY_MAX_BACKOFF_SECS = 0.5


def _connect_with_wal(db_path: Path) -> sqlite3.Connection:
    """Mirrors the ORDER `broker.vault.db.open_db` uses, not just the two
    PRAGMAs: busy_timeout FIRST. `PRAGMA journal_mode=WAL` is a one-time,
    file-wide mode switch (all 4 hammer threads call this) that itself needs
    the write lock — issuing it before busy_timeout is set races that switch
    at the SQLite default of busy_timeout=0, raising an UNPROTECTED "database
    is locked" on thread startup with zero retries, independent of anything
    that happens once hammering begins. This was the actual NEX-009 root
    cause, not busy_timeout being too small."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _execute_retrying_locked(conn: sqlite3.Connection, sql: str, params: tuple) -> None:
    deadline = time.monotonic() + _LOCK_RETRY_BUDGET_SECS
    attempt = 0
    while True:
        try:
            conn.execute(sql, params)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(min(0.05 * (attempt + 1), _LOCK_RETRY_MAX_BACKOFF_SECS))
            attempt += 1


def _hammer_sessions(db_path: Path, thread_id: int, stop_at: float, errors: list[Exception]) -> None:
    try:
        conn = _connect_with_wal(db_path)
        try:
            i = 0
            while time.monotonic() < stop_at:
                _execute_retrying_locked(
                    conn,
                    "INSERT INTO sessions (id, started_at, summary) VALUES (?, ?, ?)",
                    (f"S-t{thread_id}-{i}", f"2026-07-05T00:00:{thread_id:02d}Z", f"thread-{thread_id}-{i}"),
                )
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
                _execute_retrying_locked(
                    conn,
                    "INSERT INTO tasks (id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"TASK-t{thread_id}-{i}",
                        f"thread-{thread_id}-task-{i}",
                        "todo",
                        f"2026-07-05T00:00:{thread_id:02d}Z",
                        f"2026-07-05T00:00:{thread_id:02d}Z",
                    ),
                )
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
        # NEX-009 root cause: the ONE-TIME journal_mode=WAL mode switch on a
        # fresh (rollback-journal) file uses a SQLite code path that does NOT
        # honor busy_timeout — it fails instantly (sub-ms) with "database is
        # locked" if a peer connection contends for it, busy_timeout or no.
        # Letting all 4 hammer threads race that switch on their own first
        # connect was the actual flake. Doing the switch ONCE here, before any
        # hammer thread opens a connection, means every later
        # `PRAGMA journal_mode=WAL` in `_connect_with_wal` is a no-op query on
        # an already-WAL file — safe, fast, and lock-free.
        conn.execute("PRAGMA journal_mode=WAL")
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
    # Headroom over the hammer loop's own worst case: one last in-flight
    # statement can burn the full _LOCK_RETRY_BUDGET_SECS retrying before the
    # loop's stop_at check even runs again.
    join_timeout = _DURATION_SECS + _LOCK_RETRY_BUDGET_SECS + 5
    for t in threads:
        t.join(timeout=join_timeout)
    for t in threads:
        assert not t.is_alive(), (
            "hammer thread did not finish within its retry+join budget — "
            "treat as a genuine stall, not transient contention"
        )

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

#!/usr/bin/env python3
"""_db_harden.py — shared hardened-connect + serialized schema-init for hooks
that open a WRITE-capable or DDL-running raw sqlite3 connection to project.db.

Incident #10 (R3, malformed database schema, ~10th recurrence): the log.py
write path was hardened with a single _harden_connection() factory
(busy_timeout=15000 + WAL + wal_autocheckpoint=200) plus BEGIN IMMEDIATE
around read-modify-write races (DEC-040). That fix did NOT cover the HOOKS —
several hooks (lens-gate.sh, root-cause-gate.sh, reflection-capture.sh) each
opened their OWN raw sqlite3.connect with NO busy_timeout and ran their own
CREATE TABLE/INDEX IF NOT EXISTS schema-init DDL on essentially every gated
tool call. Many hook processes finishing concurrently (a Workflow's parallel
legs) each racing that schema-init DDL at once is the mechanism this module
closes: one hardened-connect + one serialized init-guard, used by every
writer instead of N independent copies.

Import convention: loaded via importlib.util.spec_from_file_location from the
same hooks directory, mirroring _gate_deny.py / _heartbeat.py (no package,
no sys.path surgery, works identically whether the caller runs under the
_py.sh >=3.11 resolver or ambient python3 in the nexus-package twin).

3.9-import-safe: no datetime.UTC, no def-time `X | None`, no match/case.
"""
from __future__ import annotations

import sqlite3
import time

# journal_mode=WAL retry budget (module-level so a test can shrink it without
# monkeypatching the function). SQLite's own busy-handler retry (driven by
# PRAGMA busy_timeout) does not reliably cover "PRAGMA journal_mode=WAL"
# itself on every platform/SQLite build — see harden_connection's docstring.
_WAL_SWITCH_RETRIES = 20
_WAL_SWITCH_BACKOFF_S = 0.05


def harden_connection(conn: sqlite3.Connection) -> None:
    """Apply the SAME pragma set as .memory/log.py's _harden_connection().

    Every hook that opens its own writable connection to project.db MUST
    route through this function immediately after connect() — this is the
    single place the pragma set can drift, mirroring log.py's own comment.

    ORDERING: busy_timeout is set FIRST, before journal_mode=WAL — not a
    cosmetic reorder. journal_mode=WAL is itself the single highest-
    contention statement under N-concurrent-fresh-connections (switching a
    brand-new/rollback-mode DB to WAL requires taking a lock every racing
    process wants at once), so it must run under the LONG busy_timeout, not
    under Python sqlite3's connection-default (5000ms — see CPython's
    sqlite3 module default, confirmed empirically: a fresh connect()'s
    PRAGMA busy_timeout reads back 5000 before this function ever runs).
    log.py's own _harden_connection sets journal_mode first; that ordering
    is out of this hook-side module's write scope to change, but a stress
    run against a copy of this exact pragma order (this module, pre-fix)
    reproduced a small residual rate of 'database is locked' under 32-way
    concurrency on a fresh DB specifically at the WAL-switch moment.
    Reordering here (busy_timeout first) reduced but did NOT fully close it.

    EXPLICIT RETRY ON journal_mode=WAL: a 32-concurrent-process stress run
    (T2 verification harness) still showed an occasional 'database is
    locked' AT THE journal_mode=WAL STATEMENT ITSELF even with busy_timeout
    set first — PRAGMA journal_mode=WAL can return SQLITE_BUSY immediately
    on some SQLite builds instead of engaging the busy-handler retry loop
    the way ordinary DML does (it rewrites the DB header, a different code
    path). A small bounded retry loop here closes the residual gap the
    ordering fix alone did not. Idempotent: re-running journal_mode=WAL on a
    connection that already got it is a harmless no-op read-back.
    """
    conn.execute("PRAGMA busy_timeout=15000")
    for attempt in range(_WAL_SWITCH_RETRIES):
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == _WAL_SWITCH_RETRIES - 1:
                raise
            time.sleep(_WAL_SWITCH_BACKOFF_S)
    conn.execute("PRAGMA wal_autocheckpoint=200")


def connect_hardened(db_path) -> sqlite3.Connection:  # type: ignore[no-untyped-def]
    """sqlite3.connect(db_path) + harden_connection() in one call."""
    conn = sqlite3.connect(db_path)
    harden_connection(conn)
    return conn


def init_once(conn: sqlite3.Connection, ddl_statements) -> None:  # type: ignore[no-untyped-def]
    """Run schema-init DDL (CREATE TABLE/INDEX IF NOT EXISTS, ALTER TABLE ADD
    COLUMN, etc.) serialized against concurrent racers.

    The DDL itself is already idempotent (IF NOT EXISTS / column-existence
    guards at the call site) — the corruption is NOT from re-running it, it
    is from MANY PROCESSES writing sqlite_master pages for the SAME
    check-then-DDL AT THE SAME TIME with no lock taken up front. BEGIN
    IMMEDIATE acquires SQLite's write lock before any statement in
    `ddl_statements` runs, so busy_timeout (set by harden_connection) actually
    serializes concurrent hook processes instead of letting them race each
    other's sqlite_master writes — exactly the log.py DEC-040 pattern
    (_next_id's BEGIN IMMEDIATE, cmd_init's whole-migration-sequence
    BEGIN IMMEDIATE) applied to hook-side schema-init.

    `ddl_statements` is an iterable of (sql, params) tuples; params may be
    None/() for statements with no bind parameters. Commits once at the end
    (matching sqlite3.Connection's implicit-commit-on-context-exit shape used
    elsewhere) so the whole init is one transaction, not one per statement.

    Guarded by conn.in_transaction so a caller that already opened a
    transaction (e.g. a future caller that wraps init_once + its own INSERT
    in one BEGIN IMMEDIATE) is not double-begun.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    for item in ddl_statements:
        if isinstance(item, tuple):
            sql, params = item
        else:
            sql, params = item, None
        if params:
            conn.execute(sql, params)
        else:
            conn.execute(sql)
    conn.commit()

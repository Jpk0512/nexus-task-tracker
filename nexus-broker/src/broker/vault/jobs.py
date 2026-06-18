"""vault_jobs table helpers — single chokepoint for job enqueue + dequeue.

Per plan §7.1 (B3 single-writer): the reader processes (stdio/http) call
`enqueue()` only — the daemon (broker.vault.writer) is the only caller of
`dequeue_next()` and the only writer to anything other than vault_jobs.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from broker.vault.db import open_db


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds")


def enqueue(
    db_path: Path,
    *,
    kind: str,
    payload: dict[str, Any],
    job_id: str | None = None,
) -> str:
    """INSERT one row into vault_jobs. Returns the job_id.

    This is the ONLY write a reader process makes (per §7.1 B3).
    """
    jid = job_id or str(uuid.uuid4())
    conn = open_db(db_path)
    try:
        conn.execute(
            "INSERT INTO vault_jobs(job_id, enqueued_at, kind, payload, status) "
            "VALUES (?, ?, ?, ?, 'queued')",
            (jid, now_iso(), kind, json.dumps(payload, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()
    return jid


def get_job(db_path: Path, job_id: str) -> dict[str, Any] | None:
    conn = open_db(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT job_id, enqueued_at, kind, payload, status, started_at, "
            "finished_at, result, error FROM vault_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    for key in ("payload", "result"):
        if d.get(key):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                d[key] = json.loads(d[key])
    return d


def claim_next_queued(db_path: Path) -> dict[str, Any] | None:
    """Writer-side dequeue. Atomically transitions queued → in_flight.

    Returns the claimed row, or None if no jobs are queued. Uses an explicit
    BEGIN IMMEDIATE so multiple writer attempts collapse to one winner.
    """
    conn = open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT job_id, enqueued_at, kind, payload "
            "FROM vault_jobs WHERE status = 'queued' "
            "ORDER BY enqueued_at, job_id LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return None
        started = now_iso()
        cur = conn.execute(
            "UPDATE vault_jobs SET status = 'in_flight', started_at = ? "
            "WHERE job_id = ? AND status = 'queued'",
            (started, row["job_id"]),
        )
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return None
        conn.commit()
        return {
            "job_id": row["job_id"],
            "enqueued_at": row["enqueued_at"],
            "kind": row["kind"],
            "payload": json.loads(row["payload"]) if row["payload"] else {},
            "started_at": started,
        }
    except sqlite3.Error:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def mark_done(db_path: Path, job_id: str, result: dict[str, Any] | None = None) -> None:
    conn = open_db(db_path)
    try:
        conn.execute(
            "UPDATE vault_jobs SET status = 'done', finished_at = ?, result = ? "
            "WHERE job_id = ?",
            (now_iso(), json.dumps(result or {}, sort_keys=True), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_failed(db_path: Path, job_id: str, error: str) -> None:
    conn = open_db(db_path)
    try:
        conn.execute(
            "UPDATE vault_jobs SET status = 'failed', finished_at = ?, error = ? "
            "WHERE job_id = ?",
            (now_iso(), error, job_id),
        )
        conn.commit()
    finally:
        conn.close()


# VAULT-7: lease/visibility-timeout constant for in_flight requeue.
LEASE_TIMEOUT_SECONDS: int = 300


def requeue_stale_in_flight(
    db_path: Path,
    lease_seconds: int = LEASE_TIMEOUT_SECONDS,
) -> int:
    """Return in_flight jobs older than lease_seconds back to 'queued' status.

    A writer crash leaves jobs permanently stuck in_flight (mark_done/mark_failed
    is never called). This function resets them so the next dequeue can reclaim
    them, preventing indefinite strandedness.

    Returns the number of rows requeued. Safe to call concurrently — uses
    BEGIN IMMEDIATE to serialise the read-then-update against other writers.
    """
    conn = open_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # ISO timestamp cutoff: anything started_at before this is stale.
        from datetime import timedelta
        cutoff = (
            datetime.now(tz=UTC) - timedelta(seconds=lease_seconds)
        ).isoformat(timespec="microseconds")
        cur = conn.execute(
            "UPDATE vault_jobs SET status = 'queued', started_at = NULL "
            "WHERE status = 'in_flight' AND started_at IS NOT NULL AND started_at < ?",
            (cutoff,),
        )
        requeued = cur.rowcount
        conn.commit()
        return requeued
    except sqlite3.Error:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

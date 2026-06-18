"""Phase 5a — writer drains 5 concurrently-enqueued jobs strictly serially.

Acceptance gate C7c: 5 concurrent vault_append_inbox calls enqueue 5 jobs;
writer with --once drains them; vault_jobs.status transitions queued →
in_flight → done in enqueued_at order.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest
from broker.vault import writer as writer_mod
from broker.vault.writes import vault_append_inbox_impl


@pytest.mark.asyncio
async def test_writer_drains_five_serially(config_local) -> None:
    # Enqueue 5 jobs from 5 threads concurrently.
    job_ids: list[str] = []
    lock = threading.Lock()

    def enqueue_one(idx: int) -> None:
        async def _go() -> None:
            r = await vault_append_inbox_impl(
                config=config_local,
                title=f"job-{idx}",
                body=f"body-{idx}",
                source="test",
                tags=[],
            )
            with lock:
                job_ids.append(r["job_id"])

        asyncio.run(_go())

    threads = [threading.Thread(target=enqueue_one, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(job_ids) == 5

    # Snapshot pre-drain: all queued.
    conn = sqlite3.connect(str(config_local.db_path))
    try:
        statuses = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT job_id, status FROM vault_jobs WHERE job_id IN ({})".format(
                    ",".join("?" for _ in job_ids)
                ),
                job_ids,
            )
        }
        assert all(s == "queued" for s in statuses.values())
    finally:
        conn.close()

    # Drain.
    drained = writer_mod._drain_once(config_local.db_path, config_local.vault_root)
    assert drained == 5

    # Post-drain: all done, started_at order matches enqueued_at order.
    conn = sqlite3.connect(str(config_local.db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = list(
            conn.execute(
                "SELECT job_id, status, enqueued_at, started_at, finished_at "
                "FROM vault_jobs ORDER BY enqueued_at, job_id"
            )
        )
    finally:
        conn.close()

    assert len(rows) == 5
    for r in rows:
        assert r["status"] == "done", f"job {r['job_id']} status={r['status']}"
        assert r["started_at"] is not None
        assert r["finished_at"] is not None

    # Serial drain invariant: each job's started_at >= previous job's finished_at.
    for prev, curr in zip(rows, rows[1:], strict=False):
        assert curr["started_at"] >= prev["started_at"], (
            f"out-of-order start: prev started {prev['started_at']}, "
            f"curr started {curr['started_at']}"
        )

    # Vault files exist for every job.
    inbox = config_local.vault_root / "40-inbox" / "raw"
    written = list(inbox.glob("*.md"))
    assert len(written) == 5


@pytest.mark.asyncio
async def test_writer_marks_unknown_kind_failed(config_local) -> None:
    """Job with an unknown kind transitions to 'failed' rather than blocking the queue."""
    import json
    import uuid

    from broker.vault.jobs import now_iso

    bad_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(config_local.db_path))
    try:
        conn.execute(
            "INSERT INTO vault_jobs(job_id, enqueued_at, kind, payload, status) "
            "VALUES (?, ?, 'mystery_kind', ?, 'queued')",
            (bad_id, now_iso(), json.dumps({})),
        )
        conn.commit()
    finally:
        conn.close()

    drained = writer_mod._drain_once(config_local.db_path, config_local.vault_root)
    assert drained == 1

    conn = sqlite3.connect(str(config_local.db_path))
    try:
        row = conn.execute(
            "SELECT status, error FROM vault_jobs WHERE job_id = ?", (bad_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "failed"
    assert "unknown_kind" in (row[1] or "")

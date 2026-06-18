"""App C #18 — reader processes are read-only EXCEPT for vault_jobs INSERT.

Verifies the architectural invariant from plan §7.1 (B3): the only write the
stdio reader makes is INSERT-ing into vault_jobs. Other tables remain
untouched after a full sweep of read + write tool calls.
"""
from __future__ import annotations

import sqlite3

import pytest
from broker.vault.moc import vault_moc_impl
from broker.vault.notes import vault_get_note_impl
from broker.vault.search import vault_query_impl
from broker.vault.writes import (
    ingest_repo_impl,
    ingest_url_impl,
    vault_append_inbox_impl,
    vault_capture_idea_impl,
)


def _seed_extra_tables(db_path) -> None:
    """Pretend the production schema is here. Whatever rows we insert here
    must remain UNCHANGED after we exercise the reader."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vec_memory_stub (
              ref_id TEXT PRIMARY KEY, text_blob TEXT
            );
            CREATE TABLE IF NOT EXISTS vec_memory_meta_stub (
              ref_id TEXT PRIMARY KEY, domain TEXT
            );
            INSERT OR REPLACE INTO vec_memory_stub VALUES ('row-1', 'hello');
            INSERT OR REPLACE INTO vec_memory_meta_stub VALUES ('row-1', 'general-knowledge');
            """
        )
        conn.commit()
    finally:
        conn.close()


def _table_snapshot(db_path, table: str):
    conn = sqlite3.connect(str(db_path))
    try:
        return list(conn.execute(f"SELECT * FROM {table} ORDER BY ref_id"))
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_reader_only_writes_to_vault_jobs(config_local) -> None:
    _seed_extra_tables(config_local.db_path)
    snap_before_vec = _table_snapshot(config_local.db_path, "vec_memory_stub")
    snap_before_meta = _table_snapshot(config_local.db_path, "vec_memory_meta_stub")

    # READS
    await vault_query_impl(
        config=config_local, filters={}, query=None, order_by=None, mode="fast", limit=5
    )
    await vault_get_note_impl(
        config=config_local,
        path="10-knowledge/general-knowledge/golden-note.md",
        include_body=True,
    )
    await vault_moc_impl(config=config_local, zone="00-meta")

    # WRITES (each is a vault_jobs INSERT — nothing else)
    r1 = await vault_append_inbox_impl(
        config=config_local, title="t", body="b", source="t", tags=[]
    )
    r2 = await vault_capture_idea_impl(
        config=config_local, title="t", body="b", kind="brainstorm", source_note_paths=[]
    )
    r3 = await ingest_url_impl(
        config=config_local, url="http://x", domain="general-knowledge", notes=""
    )
    r4 = await ingest_repo_impl(config=config_local, repo_url_or_path="https://x", target="")

    # Stub tables unchanged.
    assert _table_snapshot(config_local.db_path, "vec_memory_stub") == snap_before_vec
    assert _table_snapshot(config_local.db_path, "vec_memory_meta_stub") == snap_before_meta

    # vault_jobs gained EXACTLY 4 rows from the writes.
    conn = sqlite3.connect(str(config_local.db_path))
    try:
        ids = [row[0] for row in conn.execute("SELECT job_id FROM vault_jobs ORDER BY enqueued_at")]
    finally:
        conn.close()

    new_ids = {r1["job_id"], r2["job_id"], r3["job_id"], r4["job_id"]}
    assert new_ids.issubset(set(ids))
    # And no surprise rows in vault_jobs.
    assert set(ids) == new_ids

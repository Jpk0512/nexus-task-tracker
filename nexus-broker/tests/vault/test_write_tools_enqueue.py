"""Phase 5a — write tools enqueue vault_jobs rows and return {job_id, status: queued}."""
from __future__ import annotations

import sqlite3

import pytest
from broker.vault.writes import (
    ingest_repo_impl,
    ingest_url_impl,
    vault_append_inbox_impl,
    vault_capture_idea_impl,
)


def _job_row(db_path, job_id):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM vault_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_append_inbox_enqueues(config_local) -> None:
    result = await vault_append_inbox_impl(
        config=config_local, title="t1", body="b1", source="manual", tags=["a"]
    )
    assert result["status"] == "queued"
    assert result["poll_uri"] == f"job://{result['job_id']}"
    row = _job_row(config_local.db_path, result["job_id"])
    assert row is not None
    assert row["kind"] == "append_inbox"
    assert row["status"] == "queued"


@pytest.mark.asyncio
async def test_capture_idea_enqueues(config_local) -> None:
    result = await vault_capture_idea_impl(
        config=config_local,
        title="idea",
        body="body",
        kind="brainstorm",
        source_note_paths=["foo.md"],
    )
    assert result["status"] == "queued"
    row = _job_row(config_local.db_path, result["job_id"])
    assert row["kind"] == "capture_idea"


@pytest.mark.asyncio
async def test_ingest_url_enqueues(config_local) -> None:
    result = await ingest_url_impl(
        config=config_local,
        url="https://example.com",
        domain="general-knowledge",
        notes="x",
    )
    assert result["status"] == "queued"
    row = _job_row(config_local.db_path, result["job_id"])
    assert row["kind"] == "ingest_url"


@pytest.mark.asyncio
async def test_ingest_repo_enqueues(config_local) -> None:
    result = await ingest_repo_impl(
        config=config_local, repo_url_or_path="https://github.com/x/y", target=""
    )
    assert result["status"] == "queued"
    row = _job_row(config_local.db_path, result["job_id"])
    assert row["kind"] == "ingest_repo"

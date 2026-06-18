"""Write-tool impls (4) — every one ENQUEUES a vault_jobs row and returns
{job_id, status: 'queued', poll_uri: 'job://<id>'} per plan §7.1 + §7.2.

Reader processes (stdio/http) NEVER touch vault files or vec_memory directly.
The writer daemon (broker.vault.writer) drains the queue serially.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from broker.vault.jobs import enqueue

if TYPE_CHECKING:
    from broker.vault._server import AppConfig


def _result(job_id: str) -> dict[str, Any]:
    return {"job_id": job_id, "status": "queued", "poll_uri": f"job://{job_id}"}


async def vault_append_inbox_impl(
    *,
    config: AppConfig,
    title: str,
    body: str,
    source: str,
    tags: list[str],
) -> dict[str, Any]:
    payload = {
        "title": title,
        "body": body,
        "source": source,
        "tags": list(tags),
    }
    jid = enqueue(config.db_path, kind="append_inbox", payload=payload)
    return _result(jid)


async def vault_capture_idea_impl(
    *,
    config: AppConfig,
    title: str,
    body: str,
    kind: str,
    source_note_paths: list[str],
) -> dict[str, Any]:
    payload = {
        "title": title,
        "body": body,
        "kind": kind,
        "source_note_paths": list(source_note_paths),
    }
    jid = enqueue(config.db_path, kind="capture_idea", payload=payload)
    return _result(jid)


async def ingest_url_impl(
    *,
    config: AppConfig,
    url: str,
    domain: str,
    notes: str,
) -> dict[str, Any]:
    payload = {"url": url, "domain": domain, "notes": notes}
    jid = enqueue(config.db_path, kind="ingest_url", payload=payload)
    return _result(jid)


async def ingest_repo_impl(
    *,
    config: AppConfig,
    repo_url_or_path: str,
    target: str,
) -> dict[str, Any]:
    payload = {"repo_url_or_path": repo_url_or_path, "target": target}
    jid = enqueue(config.db_path, kind="ingest_repo", payload=payload)
    return _result(jid)

"""Phase 5b — HTTP MCP daemon: bearer auth + privacy fence + read-only surface.

Boots the daemon in a subprocess on a random free port (NOT 8848 — avoids
clobbering a running prod daemon). Each assertion:

  1. /health           → 200 + bearer_loaded=True (bypasses bearer).
  2. POST /mcp no auth → 401.
  3. POST /mcp wrong bearer → 401.
  4. tools/call vault_query (general-knowledge) → hits returned.
  5. tools/call vault_query (personal)          → privacy fence: empty hits.
  6. tools/list                                  → no write tools registered
                                                   (vault_append_inbox absent).

We use the FastMCP Client over Streamable HTTP for 4/5/6, and raw httpx for
1/2/3 (the auth-failure paths bypass the MCP handshake entirely).
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parents[2]  # nexus-broker/


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    sleep_interval = 0.05
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(sleep_interval, remaining))
        sleep_interval = min(sleep_interval * 2, 1.0)
    raise RuntimeError(f"daemon did not become healthy at {url}: {last_err}")


@pytest.fixture()
def http_daemon(vault_env, tmp_path):
    """Spawn broker.vault.http on a random port, with a temp bearer file."""
    port = _free_port()
    bearer = "test-bearer-" + os.urandom(8).hex()
    bearer_path = tmp_path / "default.token"
    bearer_path.write_text(bearer)
    os.chmod(bearer_path, 0o600)

    env = {
        **os.environ,
        "NEXUS_VAULT_HTTP_HOST": "127.0.0.1",
        "NEXUS_VAULT_HTTP_PORT": str(port),
        "NEXUS_VAULT_BEARER_DEFAULT": str(bearer_path),
        "NEXUS_VAULT_ROOT": str(vault_env["vault_root"]),
        "NEXUS_VAULT_DB": str(vault_env["db_path"]),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "broker.vault.http"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_for_health(f"{base}/health", timeout=15.0)
        yield {
            "base": base,
            "port": port,
            "bearer": bearer,
            "bearer_path": bearer_path,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_health_endpoint_ok_without_bearer(http_daemon) -> None:
    """GET /health is unauthenticated by design (ops/launchd healthcheck)."""
    r = httpx.get(f"{http_daemon['base']}/health", timeout=3.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["bearer_loaded"] is True
    assert "build" in body


def test_mcp_rejects_missing_bearer(http_daemon) -> None:
    """POST /mcp without an Authorization header → 401."""
    # Initialize-style probe; we never expect to get to the MCP layer.
    r = httpx.post(
        f"{http_daemon['base']}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=3.0,
    )
    assert r.status_code == 401


def test_mcp_rejects_wrong_bearer(http_daemon) -> None:
    """Wrong bearer → 401 (constant-time compare on the daemon side)."""
    r = httpx.post(
        f"{http_daemon['base']}/mcp",
        headers={"Authorization": "Bearer not-the-right-token"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=3.0,
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_mcp_allows_query_with_right_bearer(http_daemon) -> None:
    """Right bearer + vault_query on a non-fenced domain → hits."""
    url = f"{http_daemon['base']}/mcp"
    async with Client(
        url, auth=http_daemon["bearer"]
    ) as client:
        result = await client.call_tool(
            "vault_query", {"filters": {"domain": "general-knowledge"}}
        )
    payload = result.data if hasattr(result, "data") else result
    assert payload["fenced"] is False
    paths = [h["path"] for h in payload["hits"]]
    assert any("golden-note.md" in p for p in paths), payload


@pytest.mark.asyncio
async def test_mcp_privacy_fence_blocks_personal_over_http(http_daemon) -> None:
    """Plan App C #8 — vault_query(domain=personal) via web_default → empty hits."""
    url = f"{http_daemon['base']}/mcp"
    async with Client(
        url, auth=http_daemon["bearer"]
    ) as client:
        result = await client.call_tool(
            "vault_query", {"filters": {"domain": "personal"}}
        )
    payload = result.data if hasattr(result, "data") else result
    assert payload["fenced"] is True
    assert payload["hits"] == []


@pytest.mark.asyncio
async def test_mcp_write_tools_not_registered(http_daemon) -> None:
    """Phase-5b: HTTP surface excludes write tools (vault_append_inbox etc.)."""
    url = f"{http_daemon['base']}/mcp"
    async with Client(
        url, auth=http_daemon["bearer"]
    ) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}

    # Read + graph tools must be present.
    assert {"vault_query", "vault_get_note", "vault_related", "vault_moc"} <= names
    assert {"vault_graph_query", "vault_health"} <= names
    # Write tools must NOT be on the HTTP surface (Plexus Phase-5b decision).
    write_tools = {
        "vault_append_inbox",
        "vault_capture_idea",
        "ingest_url",
        "ingest_repo",
    }
    assert not (write_tools & names), f"write tools leaked to HTTP: {write_tools & names}"

"""R5-T04 (N57) — daemon-side wiring for `session_digest` + `registry_query_full`,
closing the RPC gap N47 left open (feedback id=135): both capabilities already
existed as importable modules (`broker.daemon.session_digest`,
`broker.daemon.registry_query`) but `broker.daemon.server.handle_request` never
dispatched either method name, so every caller
(`broker.jit.context_expansion.session_start_digest` / `registry_query_full`)
silently degraded through the `DaemonUnavailable` path and always answered
"direct-fallback" even against a live, reachable daemon (see
`test_jit_context_expansion.py`'s own "real daemon round trip falls back" tests,
written under that assumption -- their docstrings say the RPC wiring is "out of
THIS node's write_scope", i.e. a later node's job).

This file proves the other side of that gap: a real spawned daemon now correctly
ANSWERS both RPC methods, and the JIT layer's own `source` field flips from
"direct-fallback" to "daemon" against that same live daemon -- the concrete
evidence that `daemon-session-digest` / `daemon-registry-query-full`
(`nexus-redesign/activation/liveness-registry.yaml`) can be flipped from
`staged` to `live`. Flipping that registry file's `state:` fields is OUTSIDE
this node's write_scope (not listed in plans/15-r5-dag.yaml's N57 row) -- noted
for the orchestrator to action with this file as supporting evidence.

Run:  cd nexus-broker && uv run pytest tests/test_session_recall_daemon.py -q
"""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
import sqlite3
import tempfile
from pathlib import Path

import pytest

from broker.daemon import client as daemon_client
from broker.daemon.registry_query import query_registry
from broker.daemon.server import DaemonState, handle_request
from broker.daemon.session_digest import get_session_digest, query_session_digest_direct
from broker.jit import context_expansion as jit

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

AGENT_MD = """---
name: demo-recall-agent
description: "A demo persona for session-recall daemon-wiring tests."
model: sonnet
skills:
  - agent-protocol
---

# Demo Recall Agent
"""

SKILL_MD = """---
name: demo-recall-skill
description: "A demo skill for session-recall daemon-wiring tests."
metadata: {tier: sonnet}
---

# Demo Recall Skill
"""


def _make_project(root: Path) -> Path:
    project = root / "proj"
    (project / ".memory" / "files").mkdir(parents=True)
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "skills" / "demo-recall-skill").mkdir(parents=True)
    (project / ".claude" / "agents" / "demo-recall-agent.md").write_text(AGENT_MD)
    (project / ".claude" / "skills" / "demo-recall-skill" / "SKILL.md").write_text(SKILL_MD)
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return project


def _insert_session(conn: sqlite3.Connection, session_id: str, started_at: str, **kw) -> None:
    conn.execute(
        "INSERT INTO sessions (id, started_at, ended_at, summary, last_step, next_step, "
        "user_message_count) VALUES (?,?,?,?,?,?,?)",
        (
            session_id,
            started_at,
            kw.get("ended_at"),
            kw.get("summary"),
            kw.get("last_step"),
            kw.get("next_step"),
            kw.get("user_message_count", 0),
        ),
    )


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path)


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD; pytest's tmp_path is
    # too long for bind()/connect() to succeed (same rationale as
    # test_daemon_pilot.py / test_jit_context_expansion.py).
    sock_dir = Path(tempfile.mkdtemp(prefix="nxsrd", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture()
def spawned_daemons():
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


# ── in-process handle_request coverage (no socket) ──────────────────────────


def test_handle_request_session_digest_matches_direct_query(project) -> None:
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-09T10:00:00", summary="did X", next_step="do Y")
        conn.commit()
    finally:
        conn.close()

    state = DaemonState(project)
    result = handle_request(state, "session_digest", {})
    direct = query_session_digest_direct(project / ".memory" / "project.db")

    assert result == direct
    assert state.session_digest_queries_served == 1


def test_handle_request_session_digest_is_warm_cached_not_requeried(project) -> None:
    """Same TTL/mtime-invalidated shape as `_SchemaCache` (server.py) --
    two calls with no DB mtime change must serve the SAME cached dict, not
    re-scan project.db each time."""
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-09T10:00:00", summary="warm")
        conn.commit()
    finally:
        conn.close()

    state = DaemonState(project)
    first = handle_request(state, "session_digest", {})
    second = handle_request(state, "session_digest", {})

    assert first == second
    assert state.session_digest_cache._digest is first  # noqa: SLF001 -- same cached object


def test_handle_request_registry_query_full_matches_query_registry(project) -> None:
    state = DaemonState(project)
    result = handle_request(state, "registry_query_full", {"query_context": "demo-recall-agent"})

    assert result == {"entries": query_registry(project, "demo-recall-agent")}
    assert [e["name"] for e in result["entries"]] == ["demo-recall-agent"]
    assert state.registry_query_full_queries_served == 1


def test_handle_request_registry_query_full_none_context_returns_everything(project) -> None:
    state = DaemonState(project)
    result = handle_request(state, "registry_query_full", {})  # query_context key absent entirely

    names = {e["name"] for e in result["entries"]}
    assert names == {"demo-recall-agent", "demo-recall-skill"}


def test_budget_summary_reports_the_two_new_counters(project) -> None:
    state = DaemonState(project)
    handle_request(state, "session_digest", {})
    handle_request(state, "registry_query_full", {"query_context": None})
    summary = handle_request(state, "budget_summary", {})

    assert summary["session_digest_queries_served"] == 1
    assert summary["registry_query_full_queries_served"] == 1


# ── real (non-fixture) daemon round trips -- the "flip to live" evidence ────


@pytest.mark.slow
def test_real_daemon_answers_session_digest_rpc_directly(project, isolated_sockets, spawned_daemons):
    """Spawn an ACTUAL daemon subprocess and RPC it directly (bypassing the
    JIT fallback wrapper): `session_digest` must now be a KNOWN method,
    answering the real digest content instead of "unknown method" (feedback
    id=135's gap)."""
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-09T10:00:00", summary="daemon answered for real")
        conn.commit()
    finally:
        conn.close()

    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    result = daemon_client.call(project, "session_digest", {}, spawn_if_missing=False)

    assert result["session"]["id"] == "S1"
    assert result["session"]["summary"] == "daemon answered for real"


@pytest.mark.slow
def test_real_daemon_answers_registry_query_full_rpc_directly(project, isolated_sockets, spawned_daemons):
    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    result = daemon_client.call(
        project, "registry_query_full", {"query_context": "demo-recall-skill"}, spawn_if_missing=False
    )

    assert [e["name"] for e in result["entries"]] == ["demo-recall-skill"]


@pytest.mark.slow
def test_jit_session_start_digest_source_flips_to_daemon_against_real_daemon(
    project, isolated_sockets, spawned_daemons
):
    """The concrete "flip to live" proof: with the daemon-side RPC wired,
    `broker.jit.context_expansion.session_start_digest` (the SAME JIT surface
    N47 shipped) now reports `source == "daemon"` against a real running
    daemon instead of degrading to `direct-fallback` -- closing feedback
    id=135's gap end-to-end, not just at the raw RPC layer."""
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-09T10:00:00", summary="flip to live")
        conn.commit()
    finally:
        conn.close()

    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    result = jit.session_start_digest(project, mode="summary", allow_spawn=False)

    assert result["source"] == "daemon"
    assert result["data"]["summary"] == "flip to live"


@pytest.mark.slow
def test_jit_registry_query_full_source_flips_to_daemon_against_real_daemon(
    project, isolated_sockets, spawned_daemons
):
    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    result = jit.registry_query_full(project, query_context="demo-recall-skill", allow_spawn=False)

    assert result["source"] == "daemon"
    assert [e["name"] for e in result["data"]["entries"]] == ["demo-recall-skill"]


# ── daemon-down fail-open path still holds (no regression) ─────────────────


def test_get_session_digest_still_falls_back_when_daemon_down(project, isolated_sockets) -> None:
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-09T10:00:00", summary="no daemon here")
        conn.commit()
    finally:
        conn.close()

    result = get_session_digest(project, allow_spawn=False)

    assert result["source"] == "direct-fallback"
    assert result["session"]["summary"] == "no daemon here"

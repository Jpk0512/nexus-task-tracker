"""R5-T02 N47 — JIT context expansion A-D (plans/15-r5-dag.yaml).

Covers `broker.jit.context_expansion`'s four capability surfaces
(`memory.session_start_digest`, `tasks.reconcile`, `lessons.pending`,
`registry.query_full`), the `dispatch()` entry point, the token-cap/
bounded-output contract, the no-daemon direct-read fallback (socket
ABSENT), and the `nexus_run(capability_id=..., params=...)` wiring in
`broker.server` — including real (non-fixture) daemon round trips
(`@pytest.mark.slow`): a genuinely spawned daemon subprocess answering
"unknown method" for `session_digest`/`registry_query_full` (neither is
wired into `broker.daemon.server.handle_request` — that RPC dispatch is
out of THIS node's write_scope; `nexus-broker/src/broker/daemon/**` is
not in it), which the client wraps as `DaemonUnavailable` — proving the
fail-open contract against a REAL running daemon, not just a mock.
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

import broker.server as srv
from broker.daemon import client as daemon_client
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
CREATE TABLE decisions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'accepted',
    context     TEXT NOT NULL,
    decision    TEXT NOT NULL,
    rationale   TEXT,
    alternatives TEXT,
    consequences TEXT,
    decided_at  TEXT NOT NULL,
    session_id  TEXT REFERENCES sessions(id)
);
CREATE TABLE lessons (
    id                  TEXT PRIMARY KEY,
    trigger             TEXT NOT NULL,
    title               TEXT NOT NULL,
    body                TEXT NOT NULL,
    applies_to          TEXT NOT NULL DEFAULT 'all',
    source_session_id   TEXT REFERENCES sessions(id),
    source_decision_id  TEXT REFERENCES decisions(id),
    validated           INTEGER NOT NULL DEFAULT 0,
    recorded_at         TEXT NOT NULL,
    validated_at        TEXT
);
"""

AGENT_MD = """---
name: demo-agent
description: "A demo persona for JIT context-expansion tests."
model: sonnet
skills:
  - agent-protocol
---

# Demo Agent
"""

SKILL_MD = """---
name: demo-skill
description: "A demo skill for JIT context-expansion tests."
metadata: {tier: sonnet, token_budget: 500}
---

# Demo Skill
"""


def _make_project(root: Path) -> Path:
    project = root / "proj"
    (project / ".memory" / "files").mkdir(parents=True)
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "skills" / "demo-skill").mkdir(parents=True)
    (project / ".claude" / "agents" / "demo-agent.md").write_text(AGENT_MD)
    (project / ".claude" / "skills" / "demo-skill" / "SKILL.md").write_text(SKILL_MD)
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


def _insert_decision(conn: sqlite3.Connection, dec_id: str, session_id: str, **kw) -> None:
    conn.execute(
        "INSERT INTO decisions (id, title, context, decision, rationale, decided_at, session_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            dec_id,
            kw.get("title", dec_id),
            kw.get("context", ""),
            kw.get("decision", "did the thing"),
            kw.get("rationale", ""),
            kw.get("decided_at", "2026-07-08T09:00:00"),
            session_id,
        ),
    )


def _insert_lesson(conn: sqlite3.Connection, lesson_id: str, source_decision_id: str) -> None:
    conn.execute(
        "INSERT INTO lessons (id, trigger, title, body, source_decision_id, recorded_at) "
        "VALUES (?,?,?,?,?,?)",
        (lesson_id, "redelegation", "a lesson", "body text", source_decision_id, "2026-07-08T09:05:00"),
    )


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path)


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD — pytest's tmp_path
    # is too long for bind()/connect(); force a short-named dir under /tmp
    # (same rationale as test_daemon_pilot.py / test_daemon_session_digest.py).
    sock_dir = Path(tempfile.mkdtemp(prefix="nxjit", dir="/tmp"))
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


# ── A. memory.session_start_digest ──────────────────────────────────────────


def test_session_start_digest_summary_mode_no_daemon(project, isolated_sockets):
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00", summary="did X", next_step="do Y")
        conn.commit()
    finally:
        conn.close()

    result = jit.session_start_digest(project, mode="summary", allow_spawn=False)

    assert result["capability_id"] == "memory.session_start_digest"
    assert result["mode"] == "summary"
    assert result["source"] == "direct-fallback"
    assert result["data"]["session_id"] == "S1"
    assert result["data"]["summary"] == "did X"
    assert result["truncated"] is False
    assert result["estimated_tokens"] <= result["token_cap"]


def test_session_start_digest_full_mode_no_daemon_matches_direct_query(project, isolated_sockets):
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00", summary="did X", next_step="do Y")
        conn.execute(
            "INSERT INTO context_log (session_id, logged_at, summary) VALUES (?,?,?)",
            ("S1", "2026-07-08T10:05:00", "first step"),
        )
        conn.commit()
    finally:
        conn.close()

    result = jit.session_start_digest(project, mode="full", allow_spawn=False)

    assert result["source"] == "direct-fallback"
    assert result["data"]["session"]["id"] == "S1"
    assert [row["summary"] for row in result["data"]["context_log"]] == ["first step"]
    assert result["truncated"] is False


def test_session_start_digest_invalid_mode_raises():
    with pytest.raises(jit.JitCapabilityError, match="unknown mode"):
        jit.session_start_digest(Path("/nonexistent"), mode="bogus", allow_spawn=False)


def test_session_start_digest_full_mode_bounded_when_oversized(project, isolated_sockets):
    huge_summary = "x" * 200_000  # ~55.5k estimated tokens, way over the full-mode cap
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00", summary=huge_summary, next_step="do Y")
        conn.commit()
    finally:
        conn.close()

    result = jit.session_start_digest(project, mode="full", allow_spawn=False)

    assert result["truncated"] is True
    # Bounded, not literally unbounded — some slack for JSON-escaping overhead
    # over the chars/3.6 heuristic (documented as an estimate, not an exact count).
    assert result["estimated_tokens"] <= result["token_cap"] * 1.15


@pytest.mark.slow
def test_session_start_digest_real_daemon_round_trip_falls_back(project, isolated_sockets, spawned_daemons):
    """Real (non-fixture) round trip: spawn an ACTUAL daemon subprocess, then
    call the JIT surface against it. N57 wired `session_digest` into
    `broker.daemon.server.handle_request`, so the real daemon now answers the
    RPC directly (`source == "daemon"`) instead of falling through to
    "unknown method"/DaemonUnavailable. Proves the daemon path serves the
    correct content against a live socket, not just a mocked one.
    """
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00", summary="did the real thing")
        conn.commit()
    finally:
        conn.close()

    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    result = jit.session_start_digest(project, mode="summary", allow_spawn=False)

    assert result["source"] == "daemon"
    assert result["data"]["summary"] == "did the real thing"


# ── B. tasks.reconcile ───────────────────────────────────────────────────────


def test_tasks_reconcile_no_report_file(project):
    result = jit.tasks_reconcile(project, mode="full")

    assert result["capability_id"] == "tasks.reconcile"
    assert result["source"] == "direct-read"
    assert result["data"]["report_available"] is False
    assert result["data"]["content"] is None
    assert result["truncated"] is False


def test_tasks_reconcile_reads_report_written_by_the_hook(project):
    report_path = project / jit.RECONCILE_REPORT_RELPATH
    report_path.write_text("# Session Task Reconcile — full report\n\nTASK-1 open\n")

    result = jit.tasks_reconcile(project, mode="full")

    assert result["data"]["report_available"] is True
    assert "TASK-1 open" in result["data"]["content"]
    assert result["truncated"] is False


def test_tasks_reconcile_bounded_when_oversized(project):
    report_path = project / jit.RECONCILE_REPORT_RELPATH
    report_path.write_text("TASK line\n" * 50_000)

    result = jit.tasks_reconcile(project, mode="full")

    assert result["truncated"] is True
    assert result["estimated_tokens"] <= result["token_cap"] * 1.15
    assert "TRUNCATED" in result["data"]["content"]


def test_tasks_reconcile_invalid_mode_raises(project):
    with pytest.raises(jit.JitCapabilityError, match="unknown mode"):
        jit.tasks_reconcile(project, mode="summary")


# ── C. lessons.pending ───────────────────────────────────────────────────────


def test_lessons_pending_returns_only_decisions_missing_a_lesson(project):
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00")
        _insert_decision(conn, "DEC-1", "S1", rationale="had to redelegation this to a new agent")
        _insert_decision(conn, "DEC-2", "S1", rationale="a routine, unremarkable decision")
        _insert_decision(conn, "DEC-3", "S1", rationale="this was blocked for a while")
        _insert_lesson(conn, "LSN-1", "DEC-3")  # DEC-3 already has a lesson -> excluded
        conn.commit()
    finally:
        conn.close()

    result = jit.lessons_pending(project, mode="full")

    assert result["capability_id"] == "lessons.pending"
    assert result["source"] == "direct-read"
    ids = {row["decision_id"] for row in result["data"]["pending"]}
    assert ids == {"DEC-1"}
    assert result["data"]["pending_count"] == 1
    assert result["truncated"] is False


def test_lessons_pending_empty_project_returns_empty(project):
    result = jit.lessons_pending(project, mode="full")

    assert result["data"]["pending"] == []
    assert result["data"]["pending_count"] == 0


def test_lessons_pending_bounded_when_oversized(project):
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00")
        for i in range(2000):
            _insert_decision(
                conn, f"DEC-{i}", "S1", rationale="failure " + ("x" * 500)
            )
        conn.commit()
    finally:
        conn.close()

    result = jit.lessons_pending(project, mode="full")

    assert result["truncated"] is True
    assert result["estimated_tokens"] <= result["token_cap"] * 1.15
    assert result["data"]["pending_count"] == 2000  # true count preserved even though the list is capped
    assert len(result["data"]["pending"]) < 2000


def test_lessons_pending_invalid_mode_raises(project):
    with pytest.raises(jit.JitCapabilityError, match="unknown mode"):
        jit.lessons_pending(project, mode="summary")


# ── D. registry.query_full ──────────────────────────────────────────────────


def test_registry_query_full_no_daemon_matches_direct_query(project, isolated_sockets):
    result = jit.registry_query_full(project, query_context="demo-agent", allow_spawn=False)

    assert result["capability_id"] == "registry.query_full"
    assert result["source"] == "direct-fallback"
    assert [e["name"] for e in result["data"]["entries"]] == ["demo-agent"]
    assert result["data"]["entry_count"] == 1


def test_registry_query_full_none_query_returns_everything(project, isolated_sockets):
    result = jit.registry_query_full(project, query_context=None, allow_spawn=False)

    names = {e["name"] for e in result["data"]["entries"]}
    assert names == {"demo-agent", "demo-skill"}


def test_registry_query_full_bounded_when_oversized(project, isolated_sockets):
    agents_dir = project / ".claude" / "agents"
    for i in range(500):
        (agents_dir / f"agent-{i}.md").write_text(
            f"---\nname: agent-{i}\ndescription: \"{'y' * 300}\"\nmodel: sonnet\n---\n\n# Agent {i}\n"
        )

    result = jit.registry_query_full(project, query_context=None, allow_spawn=False)

    assert result["truncated"] is True
    assert result["estimated_tokens"] <= result["token_cap"] * 1.15
    assert result["data"]["entry_count"] > len(result["data"]["entries"])


@pytest.mark.slow
def test_registry_query_full_real_daemon_round_trip_falls_back(project, isolated_sockets, spawned_daemons):
    """Real (non-fixture) round trip against a live daemon: N57 wired
    `registry_query_full` into `broker.daemon.server.handle_request`, so the
    real running daemon now answers the RPC directly (`source == "daemon"`)
    and must still serve the correct FULL-scope query result.
    """
    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    result = jit.registry_query_full(project, query_context="demo-skill", allow_spawn=False)

    assert result["source"] == "daemon"
    assert [e["name"] for e in result["data"]["entries"]] == ["demo-skill"]


# ── dispatch() — the nexus_run(capability_id=...) entry point ──────────────


def test_dispatch_routes_each_capability_id(project, isolated_sockets):
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00", summary="dispatched")
        conn.commit()
    finally:
        conn.close()

    a = jit.dispatch(
        "memory.session_start_digest", {"mode": "summary"}, project_path=project, allow_spawn=False
    )
    b = jit.dispatch("tasks.reconcile", {}, project_path=project, allow_spawn=False)
    c = jit.dispatch("lessons.pending", {}, project_path=project, allow_spawn=False)
    d = jit.dispatch(
        "registry.query_full", {"query_context": "demo-agent"}, project_path=project, allow_spawn=False
    )

    assert a["capability_id"] == "memory.session_start_digest"
    assert a["data"]["summary"] == "dispatched"
    assert b["capability_id"] == "tasks.reconcile"
    assert c["capability_id"] == "lessons.pending"
    assert d["capability_id"] == "registry.query_full"
    assert [e["name"] for e in d["data"]["entries"]] == ["demo-agent"]


def test_dispatch_unknown_capability_id_raises(project):
    with pytest.raises(jit.JitCapabilityError, match="unknown JIT capability id"):
        jit.dispatch("not.a.real.capability", {}, project_path=project, allow_spawn=False)


def test_dispatch_defaults_modes_sensibly(project, isolated_sockets):
    # tasks.reconcile / lessons.pending default to mode="full" with no params at all.
    b = jit.dispatch("tasks.reconcile", None, project_path=project, allow_spawn=False)
    c = jit.dispatch("lessons.pending", None, project_path=project, allow_spawn=False)
    assert b["mode"] == "full"
    assert c["mode"] == "full"
    # memory.session_start_digest defaults to mode="summary".
    a = jit.dispatch("memory.session_start_digest", None, project_path=project, allow_spawn=False)
    assert a["mode"] == "summary"


# ── nexus_run(capability_id=..., params=...) wiring in broker.server ───────


@pytest.fixture()
def wired_project(project, monkeypatch):
    monkeypatch.setattr(srv, "REPO_ROOT", project)
    return project


async def test_nexus_run_turn_id_path_unchanged(tmp_path, monkeypatch):
    """Regression: the original turn-based nexus_run behavior (no capability_id)
    must be byte-for-byte unchanged by this node's extension.
    """
    import broker.state as state_mod

    target = tmp_path / "broker_state.json"
    monkeypatch.setattr(state_mod, "STATE_PATH", target)

    result = await srv.nexus_run(turn_id="turn-without-prepare")

    assert result["ok"] is False
    assert any("no matching prepared dispatch" in e for e in result["errors"])


async def test_nexus_run_capability_tasks_reconcile(wired_project):
    result = await srv.nexus_run(capability_id="tasks.reconcile", params={"mode": "full"})

    assert result["capability_id"] == "tasks.reconcile"
    assert result["data"]["report_available"] is False


async def test_nexus_run_capability_lessons_pending(wired_project):
    result = await srv.nexus_run(capability_id="lessons.pending")

    assert result["capability_id"] == "lessons.pending"
    assert result["data"]["pending"] == []


async def test_nexus_run_unknown_capability_returns_ok_false(wired_project):
    result = await srv.nexus_run(capability_id="not.a.real.capability")

    assert result["ok"] is False
    assert "unknown JIT capability id" in result["error"]


@pytest.mark.slow
async def test_nexus_run_capability_session_digest_real_daemon(wired_project, isolated_sockets, spawned_daemons):
    """Exercises the FULL path through the MCP tool wrapper with a REAL daemon
    reachable (spawn-on-demand, since `nexus_run` doesn't pass allow_spawn=False):
    the daemon answers, doesn't know `session_digest`, and the packet still
    carries the correct direct-fallback content.
    """
    conn = sqlite3.connect(wired_project / ".memory" / "project.db")
    try:
        _insert_session(conn, "S1", "2026-07-08T10:00:00", summary="via nexus_run")
        conn.commit()
    finally:
        conn.close()

    result = await srv.nexus_run(
        capability_id="memory.session_start_digest", params={"mode": "summary"}
    )

    assert result["data"]["summary"] == "via nexus_run"
    assert result["source"] in ("direct-fallback", "daemon")

    # Track whatever daemon may have been spawned by the call above for cleanup.
    with contextlib.suppress(daemon_client.DaemonUnavailable):
        health = daemon_client.call(wired_project, "health", spawn_if_missing=False)
        spawned_daemons.append(health["pid"])

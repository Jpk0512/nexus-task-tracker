"""R4-T06 daemon pilot (Option C) — plans/13-r4-conductor-lane-plan.md N11.

Covers exactly the Phase-A IN-scope acceptance criteria: spawn-on-demand +
idle-shutdown + stale-socket self-heal (1.7/1.8), the daemon-killed-mid-
session fail-closed drill (zero data loss, cache-only warmth lost), the
skills/agents-only registry query (no MCP schemas — SS1 boundary), the
write-through telemetry batch surviving `kill -9` with project.db remaining
authoritative, and the budget-summary counters (2.8).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.daemon import client as daemon_client
from broker.daemon import fallback, paths
from broker.daemon.client import DaemonUnavailable
from broker.daemon.registry_scan import filter_registry, scan_agents, scan_registry, scan_skills
from broker.daemon.schema_scan import scan_schema
from broker.daemon.server import DaemonState, handle_request
from broker.daemon.telemetry_store import TelemetryStore

BROKER_ROOT = Path(__file__).resolve().parent.parent  # nexus-broker/

AGENT_MD = """---
name: demo-agent
description: "A demo persona for daemon pilot tests."
model: sonnet
skills:
  - agent-protocol
---

# Demo Agent
"""

SKILL_MD = """---
name: demo-skill
description: "A demo skill for daemon pilot tests."
metadata: {tier: sonnet, token_budget: 500}
---

# Demo Skill
"""

SCHEMA_SQL = """
CREATE TABLE dispatch_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT, dispatch_id TEXT, persona TEXT NOT NULL, model TEXT,
    task_id TEXT, marker TEXT, tokens INTEGER, token_source TEXT NOT NULL DEFAULT 'exact',
    tool_uses INTEGER, duration_ms INTEGER, run_context TEXT DEFAULT 'local',
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE skill_load_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_id TEXT NOT NULL, skill_id TEXT NOT NULL, ts TEXT NOT NULL, byte_len INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL, task TEXT, started TEXT NOT NULL, elapsed TEXT,
    status TEXT, current_action TEXT, session_id TEXT, updated_at TEXT
);
"""


def _make_project(root: Path) -> Path:
    project = root / "proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "skills" / "demo-skill").mkdir(parents=True)
    (project / ".memory").mkdir(parents=True)
    (project / ".claude" / "agents" / "demo-agent.md").write_text(AGENT_MD)
    (project / ".claude" / "skills" / "demo-skill" / "SKILL.md").write_text(SKILL_MD)
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return project


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path)


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD — pytest's tmp_path
    # (deeply nested under pytest-of-<user>/pytest-NNN/test-name/, itself
    # under macOS's long default TMPDIR) is too long for bind() to succeed, so
    # this forces a short-named dir directly under /tmp instead of tmp_path
    # or tempfile's TMPDIR-derived default.
    sock_dir = Path(tempfile.mkdtemp(prefix="nxd", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture()
def spawned_daemons():
    """Tracks PIDs spawned via the client's spawn-on-demand path so the test
    suite never leaks a resident daemon process across runs.
    """
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _spawn_daemon_process(project_path: Path, env_overrides: dict[str, str] | None = None):
    env = {**os.environ, **(env_overrides or {})}
    return subprocess.Popen(
        [sys.executable, "-m", "broker.daemon.server", "--project-path", str(project_path)],
        cwd=str(BROKER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_health(project_path: Path, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return daemon_client.call(
                project_path, "health", spawn_if_missing=False, connect_timeout=0.2
            )
        except DaemonUnavailable as exc:
            last_exc = exc
            time.sleep(0.05)
    raise AssertionError(f"daemon never became healthy: {last_exc}")


# ── 1.1 / 2.1-half — warm skills/agents registry cache, no MCP schemas ─────


def test_scan_agents_and_skills_from_disk(project) -> None:
    agents = scan_agents(project)
    skills = scan_skills(project)
    assert agents == [
        {
            "kind": "agent",
            "name": "demo-agent",
            "description": "A demo persona for daemon pilot tests.",
            "model": "sonnet",
            "skills": ["agent-protocol"],
        }
    ]
    assert skills == [
        {
            "kind": "skill",
            "name": "demo-skill",
            "description": "A demo skill for daemon pilot tests.",
            "tier": "sonnet",
        }
    ]


def test_filter_registry_narrows_by_query_context(project) -> None:
    entries = scan_registry(project)
    assert len(entries) == 2
    narrowed = filter_registry(entries, "demo-skill")
    assert [e["name"] for e in narrowed] == ["demo-skill"]
    assert filter_registry(entries, None) == entries
    assert filter_registry(entries, "nonexistent-xyz") == []


def test_query_registry_rpc_serves_no_mcp_tool_schemas(project) -> None:
    """SS1 boundary: the registry surface is skills/agents ONLY — never an MCP schema."""
    state = DaemonState(project)
    result = handle_request(state, "query_registry", {})
    assert set(result.keys()) == {"entries"}
    for entry in result["entries"]:
        assert entry["kind"] in ("agent", "skill")
        # No MCP-tool-schema-shaped keys anywhere in a served entry.
        assert "inputSchema" not in entry
        assert "tools" not in entry
        assert "mcp" not in entry


# ── 1.3 — schema-snapshot cache is schema-agnostic ──────────────────────────


def test_schema_scan_reflects_whatever_shape_is_present(tmp_path) -> None:
    db_a = tmp_path / "a.db"
    conn = sqlite3.connect(db_a)
    conn.executescript("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT);")
    conn.commit()
    conn.close()

    db_b = tmp_path / "b.db"
    conn = sqlite3.connect(db_b)
    conn.executescript(
        "CREATE TABLE gadgets (id INTEGER PRIMARY KEY, kind TEXT, weight REAL);"
    )
    conn.commit()
    conn.close()

    shape_a = scan_schema(db_a)
    shape_b = scan_schema(db_b)
    assert shape_a == {"widgets": ["id", "name"]}
    assert shape_b == {"gadgets": ["id", "kind", "weight"]}
    # Two projects, two different shapes — no hardcoded assumption leaked across them.
    assert shape_a != shape_b


def test_schema_scan_missing_db_is_empty_not_an_error(tmp_path) -> None:
    assert scan_schema(tmp_path / "does-not-exist.db") == {}


# ── in-process handle_request coverage for telemetry + budget summary ──────


def test_handle_request_telemetry_and_budget_summary(project) -> None:
    state = DaemonState(project)
    handle_request(
        state,
        "record_telemetry",
        {"table": "agent_activity", "row": {"agent": "pipeline-async", "started": "now", "status": "active"}},
    )
    assert state.telemetry.pending_count() == 1
    flushed = handle_request(state, "flush_telemetry", {})
    assert flushed == {"flushed": 1}

    handle_request(state, "query_registry", {})
    summary = handle_request(state, "budget_summary", {})
    assert summary["registry_queries_served"] == 1
    assert summary["telemetry_rows_flushed"] == 1
    assert summary["telemetry_flush_count"] == 1
    assert summary["telemetry_pending"] == 0

    conn = sqlite3.connect(state.db_path)
    try:
        rows = conn.execute("SELECT agent, status FROM agent_activity").fetchall()
    finally:
        conn.close()
    assert rows == [("pipeline-async", "active")]


def test_telemetry_store_rejects_unknown_table() -> None:
    store = TelemetryStore()
    with pytest.raises(ValueError, match="unknown telemetry table"):
        store.record("not_a_real_table", {"x": 1})


# ── Tranche 2 (daemon-hook-plan-2026-07-12.md §C) — record_event + emit_heartbeat ──


def test_record_event_appends_to_allow_listed_jsonl_sink(
    project, snapshot: SnapshotAssertion
) -> None:
    """completion-capture.py / dispatch-capture.py's shim: sink -> JSONL append."""
    import json

    state = DaemonState(project)
    result = handle_request(
        state,
        "record_event",
        {"sink": "completion_events", "row": {"session_id": "s1", "marker": "DONE"}},
    )
    # envelope fixture: the record_event RPC response shape, reviewed via snapshot (F3-04).
    assert result == snapshot(name="record_event_envelope")
    sink_path = project / ".memory" / "files" / "completion_events.jsonl"
    rows = [json.loads(line) for line in sink_path.read_text().splitlines()]
    assert rows == [{"session_id": "s1", "marker": "DONE"}]

    handle_request(
        state,
        "record_event",
        {"sink": "router_dispatches", "row": {"session_id": "s1", "dispatched_persona": "hermes"}},
    )
    sink_path2 = project / ".memory" / "files" / "router_dispatches.jsonl"
    rows2 = [json.loads(line) for line in sink_path2.read_text().splitlines()]
    assert rows2 == [{"session_id": "s1", "dispatched_persona": "hermes"}]


def test_record_event_rejects_unknown_sink(project) -> None:
    state = DaemonState(project)
    with pytest.raises(ValueError, match="unknown record_event sink"):
        handle_request(state, "record_event", {"sink": "not_a_real_sink", "row": {}})


def test_record_event_requires_sink_and_row(project) -> None:
    state = DaemonState(project)
    with pytest.raises(ValueError, match="record_event requires"):
        handle_request(state, "record_event", {"row": {}})
    with pytest.raises(ValueError, match="record_event requires"):
        handle_request(state, "record_event", {"sink": "completion_events"})


def test_record_event_nexus_feedback_sink_queues_without_raising(
    project, snapshot: SnapshotAssertion
) -> None:
    """feedback-capture.py's shim: fire-and-forget log.py spawn, never awaited
    inline — this only proves the RPC itself accepts and returns immediately,
    not that the spawned subprocess completes (that's the up/down hook drill).
    """
    state = DaemonState(project)
    result = handle_request(
        state,
        "record_event",
        {
            "sink": "nexus_feedback",
            "row": {"source": "hook", "severity": "low", "category": "other", "message": "m"},
        },
    )
    assert result == snapshot(name="record_event_feedback_envelope")


def test_emit_heartbeat_rpc_appends_fixed_columns_only(
    project, snapshot: SnapshotAssertion
) -> None:
    """_heartbeat.py's shim: unknown/extra payload keys never leak into the row."""
    import json

    state = DaemonState(project)
    result = handle_request(
        state,
        "emit_heartbeat",
        {
            "ts": "2026-07-12T00:00:00Z",
            "hook": "worktree-guard",
            "event": "PreToolUse",
            "decision": "allow",
            "latency_ms": 5,
            "unexpected_extra_key": "must not leak into the row",
        },
    )
    assert result == snapshot(name="emit_heartbeat_envelope")
    sink_path = project / ".memory" / "files" / "hook_heartbeat.jsonl"
    rows = [json.loads(line) for line in sink_path.read_text().splitlines()]
    assert rows == [
        {
            "ts": "2026-07-12T00:00:00Z",
            "hook": "worktree-guard",
            "event": "PreToolUse",
            "decision": "allow",
            "latency_ms": 5,
        }
    ]


# ── Tranche 3 (daemon-hook-plan-2026-07-12.md §C) — reflection_snapshot + task_mirror ──

META_REPO_ROOT = BROKER_ROOT.parent
REAL_LOG_PY = META_REPO_ROOT / ".memory" / "log.py"
REAL_SCHEMA_SQL = META_REPO_ROOT / ".memory" / "schema.sql"


def _venv_python() -> str:
    for cand in (
        META_REPO_ROOT / ".memory" / ".venv" / "bin" / "python",
        META_REPO_ROOT / ".memory" / ".venv" / "bin" / "python3",
    ):
        if cand.exists():
            return str(cand)
    return sys.executable


def _make_project_with_real_log_py(tmp_path: Path) -> Path:
    """A project skeleton with the REAL `.memory/log.py` + schema (initialized),
    mirroring `.claude/hooks/tests/test_task_db_mirror.py`'s `_make_repo()` —
    needed to exercise `_run_task_mirror`'s subprocess end-to-end against a
    real `tasks` table (the minimal SCHEMA_SQL fixture above has none)."""
    project = tmp_path / "real-proj"
    (project / ".memory").mkdir(parents=True)
    shutil.copy(REAL_LOG_PY, project / ".memory" / "log.py")
    shutil.copy(REAL_SCHEMA_SQL, project / ".memory" / "schema.sql")
    subprocess.run(
        [_venv_python(), str(project / ".memory" / "log.py"), "init"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return project


def test_record_event_reflection_snapshot_sink_appends(project) -> None:
    """reflection-capture.sh's shim: same allow-listed JSONL-append shape as
    the Tranche 2 sinks."""
    import json

    state = DaemonState(project)
    row = {
        "session_id": "s1",
        "file_path": "docs/CONSTITUTION.md",
        "action_type": "constitution_amend",
        "one_line_summary": "added: 'x'",
        "captured_at": "2026-07-12T00:00:00Z",
    }
    result = handle_request(state, "record_event", {"sink": "reflection_snapshot", "row": row})
    assert result == {"accepted": True, "sink": "reflection_snapshot"}
    sink_path = project / ".memory" / "files" / "reflection_snapshot.jsonl"
    rows = [json.loads(line) for line in sink_path.read_text().splitlines()]
    assert rows == [row]


def test_record_event_task_mirror_sink_persists_before_returning(tmp_path) -> None:
    """task-db-mirror.sh's shim: UNLIKE nexus_feedback, this AWAITS the real
    `log.py task mirror-native` subprocess — the row must already be durable
    in project.db by the time this call returns (cross-session continuity)."""
    real_project = _make_project_with_real_log_py(tmp_path)
    state = DaemonState(real_project)
    result = handle_request(
        state,
        "record_event",
        {
            "sink": "task_mirror",
            "row": {"op": "create", "native_id": "7", "subject": "Wire the broker", "status": "in_progress"},
        },
    )
    assert result == {"accepted": True, "sink": "task_mirror"}

    conn = sqlite3.connect(real_project / ".memory" / "project.db")
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, title, status FROM tasks").fetchall()
    finally:
        conn.close()
    assert [dict(r) for r in rows] == [{"id": "NATIVE-7", "title": "Wire the broker", "status": "in_progress"}]


def test_record_event_task_mirror_missing_log_py_not_accepted(project) -> None:
    """No `.memory/log.py` under this project (the `project` fixture doesn't
    ship one) — reported not-accepted so the hook's inline fallback runs,
    never raises."""
    state = DaemonState(project)
    result = handle_request(
        state, "record_event", {"sink": "task_mirror", "row": {"op": "update", "native_id": "1"}}
    )
    assert result == {"accepted": False, "sink": "task_mirror", "reason": "log.py missing"}


def test_record_event_task_mirror_requires_op_and_native_id(tmp_path) -> None:
    # log.py present (else the missing-log.py early-return would fire first).
    real_project = _make_project_with_real_log_py(tmp_path)
    state = DaemonState(real_project)
    with pytest.raises(ValueError, match="task_mirror row requires"):
        handle_request(state, "record_event", {"sink": "task_mirror", "row": {"op": "create"}})
    with pytest.raises(ValueError, match="task_mirror row requires"):
        handle_request(state, "record_event", {"sink": "task_mirror", "row": {"native_id": "7"}})


# ── 1.6 / 1.7 — live daemon: health, spawn-on-demand ────────────────────────


@pytest.mark.slow
def test_spawn_on_demand_serves_health(project, isolated_sockets, spawned_daemons) -> None:
    sock_path = paths.socket_path_for(project)
    assert not sock_path.exists()

    result = daemon_client.call(project, "health", spawn_wait_s=10.0)
    assert result["status"] == "ok"
    assert result["project_path"] == str(project)
    spawned_daemons.append(result["pid"])

    # A fast repeat call proves the process is warm, not re-spawned per call.
    result2 = daemon_client.call(project, "health", spawn_if_missing=False)
    assert result2["pid"] == result["pid"]


@pytest.mark.slow
def test_daemon_query_registry_over_real_socket(project, isolated_sockets, spawned_daemons) -> None:
    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])
    result = daemon_client.call(project, "query_registry", {"query_context": "demo-agent"})
    assert [e["name"] for e in result["entries"]] == ["demo-agent"]


# ── 1.7 — idle-shutdown ─────────────────────────────────────────────────────


async def test_resident_mode_skips_idle_watchdog_when_timeout_not_positive(project, monkeypatch) -> None:
    """NEXUS_DAEMON_IDLE_TIMEOUT_S<=0 (resident mode) must not start the
    idle_task at all — fast unit assertion, no 5-minute real wait."""
    from broker.daemon.server import start_idle_watchdog

    state = DaemonState(project)
    shutdown_event = asyncio.Event()

    monkeypatch.setattr(paths, "IDLE_TIMEOUT_S", 0.0)
    task = start_idle_watchdog(state, shutdown_event)
    assert task is None, "resident mode (timeout<=0) must not schedule the idle watchdog"

    monkeypatch.setattr(paths, "IDLE_TIMEOUT_S", -1.0)
    task = start_idle_watchdog(state, shutdown_event)
    assert task is None, "a negative timeout is also resident, not a busy-loop"


async def test_default_positive_timeout_still_schedules_idle_watchdog(project, monkeypatch) -> None:
    """Control case: any positive timeout (including the 300.0 default) DOES
    schedule the shutdown watchdog — the additive change is backward-compatible."""
    from broker.daemon.server import start_idle_watchdog

    state = DaemonState(project)
    shutdown_event = asyncio.Event()

    monkeypatch.setattr(paths, "IDLE_TIMEOUT_S", 300.0)
    task = start_idle_watchdog(state, shutdown_event)
    try:
        assert task is not None, "a positive timeout must still schedule the idle watchdog"
        assert not task.done()
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.slow
def test_idle_shutdown_exits_after_timeout(project, isolated_sockets) -> None:
    proc = _spawn_daemon_process(
        project,
        {
            "NEXUS_DAEMON_IDLE_TIMEOUT_S": "1",
            "NEXUS_DAEMON_IDLE_CHECK_INTERVAL_S": "0.2",
        },
    )
    try:
        _wait_for_health(project, timeout=10.0)
        sock_path = paths.socket_path_for(project)
        assert sock_path.exists()

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.1)
        assert proc.poll() is not None, "daemon did not idle-shutdown in time"
        assert not sock_path.exists(), "idle-shutdown must remove its own socket file"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# ── 1.8 — stale-socket self-heal ────────────────────────────────────────────


@pytest.mark.slow
def test_stale_socket_self_heals(project, isolated_sockets, spawned_daemons) -> None:
    sock_path = paths.socket_path_for(project)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    # A bound-but-never-listened socket: any connect() to it raises
    # ConnectionRefusedError, exactly the "leftover socket file, no live
    # listener" shape plans/07 §2 Option C names as risk (b).
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()
    assert sock_path.exists()

    result = daemon_client.call(project, "health", spawn_wait_s=10.0)
    assert result["status"] == "ok"
    spawned_daemons.append(result["pid"])


# ── the required drill: daemon killed mid-session -> fail closed, zero data loss ──


@pytest.mark.slow
def test_daemon_killed_mid_session_fails_closed_with_zero_data_loss(
    project, isolated_sockets
) -> None:
    baseline = scan_registry(project)  # ground truth, computed with no daemon involved

    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    pid = health["pid"]
    via_daemon = fallback.get_registry(project, allow_spawn=False)
    assert via_daemon["source"] == "daemon"
    assert via_daemon["entries"] == baseline

    os.kill(pid, signal.SIGKILL)
    # SIGKILL delivery/termination is kernel-guaranteed and near-instant; the
    # variable here is REAPING latency (the OS clearing the zombie once the
    # daemon's adoptive parent — init/launchd, after the double-fork detach —
    # calls wait()), which is a scheduler fact, not something this daemon's
    # code controls. 15s gives that headroom under real concurrent load
    # (reproduced empirically: 4-way-parallel runs of this suite made a live,
    # healthy daemon's response — and, transiently, this reap — take longer
    # than a tight budget allows, even though nothing was actually wrong).
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("daemon did not die under SIGKILL")

    # allow_spawn=False isolates the pure fail-closed branch: no daemon,
    # no auto-respawn masking the failure — must fall back to a direct read.
    after_kill = fallback.get_registry(project, allow_spawn=False)
    assert after_kill["source"] == "direct-fallback"
    assert after_kill["entries"] == baseline  # zero data loss — only cache warmth lost


# ── write-through telemetry batch survives kill -9; project.db stays authoritative ──


@pytest.mark.slow
def test_telemetry_write_through_survives_kill_minus_9(project, isolated_sockets) -> None:
    health = daemon_client.call(project, "health", spawn_wait_s=10.0)
    pid = health["pid"]

    daemon_client.call(
        project,
        "record_telemetry",
        {"table": "dispatch_telemetry", "row": {"persona": "pipeline-async", "tokens": 123}},
        spawn_if_missing=False,
    )
    daemon_client.call(
        project,
        "record_telemetry",
        {"table": "skill_load_events", "row": {"dispatch_id": "d1", "skill_id": "agent-protocol", "ts": "now"}},
        spawn_if_missing=False,
    )
    daemon_client.call(
        project,
        "record_telemetry",
        {"table": "agent_activity", "row": {"agent": "pipeline-async", "started": "now", "status": "active"}},
        spawn_if_missing=False,
    )
    flushed = daemon_client.call(project, "flush_telemetry", {}, spawn_if_missing=False)
    assert flushed["flushed"] == 3

    os.kill(pid, signal.SIGKILL)
    # See the matching comment in the sibling drill test: 15s covers reap
    # latency under real concurrent load, not the (kernel-guaranteed,
    # near-instant) SIGKILL termination itself.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)

    # project.db remains authoritative + uncorrupted under a hard daemon kill.
    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT persona, tokens FROM dispatch_telemetry"
        ).fetchall() == [("pipeline-async", 123)]
        assert conn.execute(
            "SELECT dispatch_id, skill_id FROM skill_load_events"
        ).fetchall() == [("d1", "agent-protocol")]
        assert conn.execute(
            "SELECT agent, status FROM agent_activity"
        ).fetchall() == [("pipeline-async", "active")]
    finally:
        conn.close()


# ── fallback.record_telemetry direct-write path when no daemon is running ──


def test_fallback_record_telemetry_direct_write_when_daemon_down(project, isolated_sockets) -> None:
    result = fallback.record_telemetry(
        project,
        "agent_activity",
        {"agent": "hermes", "started": "now", "status": "active"},
        allow_spawn=False,
    )
    assert result == {"accepted": True, "source": "direct-fallback"}

    conn = sqlite3.connect(project / ".memory" / "project.db")
    try:
        rows = conn.execute("SELECT agent, status FROM agent_activity").fetchall()
    finally:
        conn.close()
    assert rows == [("hermes", "active")]


def test_fallback_get_registry_direct_when_daemon_down(project, isolated_sockets) -> None:
    result = fallback.get_registry(project, allow_spawn=False)
    assert result["source"] == "direct-fallback"
    assert [e["name"] for e in result["entries"]] == ["demo-agent", "demo-skill"]


def test_fallback_get_schema_snapshot_direct_when_daemon_down(project, isolated_sockets) -> None:
    result = fallback.get_schema_snapshot(project, allow_spawn=False)
    assert result["source"] == "direct-fallback"
    assert "dispatch_telemetry" in result["tables"]


def test_call_raises_daemon_unavailable_when_spawn_disabled(project, isolated_sockets) -> None:
    with pytest.raises(DaemonUnavailable):
        daemon_client.call(project, "health", spawn_if_missing=False, connect_timeout=0.1)

"""TASK-094 LEG B — hook-level `gate`-kind span emission end to end.

LEG A (spans.py) shipped `validate_gate_attributes` (rpc_miss, rpc_latency_ms,
revise_reasons) and `validate_tool_call_attributes` with "schema + writer
support; live hook-side emission is LEG B's" (see f7bfd54's commit message).
This file proves LEG B's half: `.claude/hooks/_gate_deny.py`'s
`emit_gate_span` (called from `deny()`/`advise()` via the optional
`span_attrs` kwarg, or directly) durably materializes a real `gate` span via
the daemon's `span.emit` RPC — spawning a REAL daemon process (mirrors
test_daemon_pilot.py's own `_spawn_daemon_process` pattern) rather than
mocking the RPC boundary (tdd-core "no mocking the analytics DB" convention).

Also covers `.claude/hooks/_daemon_rpc.py`'s universal RPC-miss recording
(TASK-094 LEG B item "daemon RPC-miss event emission from hook shims") —
no daemon needed for that half, since a miss is precisely the no-daemon case.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from broker.daemon import paths
from broker.daemon.spans import SpanStore, spans_db_path_for

BROKER_ROOT = Path(__file__).resolve().parent.parent  # nexus-broker/
HOOKS_DIR = BROKER_ROOT.parent / ".claude" / "hooks"


def _load_hook_module(name: str):
    """Same-directory dynamic import — mirrors every hook's own
    `importlib.util.spec_from_file_location` convention (hooks run under
    ambient python3, never `import broker.*`)."""
    spec = importlib.util.spec_from_file_location(name, HOOKS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


daemon_rpc = _load_hook_module("_daemon_rpc")
gate_deny = _load_hook_module("_gate_deny")


# ── daemon RPC-miss recording (no daemon needed — a miss IS the no-daemon case)

def test_call_records_a_miss_when_no_daemon_is_running(tmp_path: Path) -> None:
    result = daemon_rpc.call(tmp_path, "health", {}, 0.05)
    assert result is None

    miss_path = tmp_path / ".memory" / "files" / "daemon_rpc_misses.jsonl"
    assert miss_path.is_file(), "a daemon RPC miss must be durably recorded locally"
    rows = [json.loads(ln) for ln in miss_path.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["method"] == "health"
    assert rows[0]["reason"] == "no-socket"
    assert "ts" in rows[0]


def test_call_records_one_miss_row_per_call(tmp_path: Path) -> None:
    daemon_rpc.call(tmp_path, "health", {}, 0.05)
    daemon_rpc.call(tmp_path, "span.emit", {"span": {}}, 0.05)
    miss_path = tmp_path / ".memory" / "files" / "daemon_rpc_misses.jsonl"
    rows = [json.loads(ln) for ln in miss_path.read_text().splitlines() if ln.strip()]
    assert [r["method"] for r in rows] == ["health", "span.emit"]


def test_call_success_path_records_no_miss(tmp_path: Path) -> None:
    """A confirmed daemon accept must NEVER also append a miss row —
    otherwise every real RPC would look like a false-positive daemon-health
    incident. A listening unix socket answering a well-formed reply on a
    BACKGROUND THREAD (its own event loop, so the synchronous client call
    below can proceed concurrently without a same-thread deadlock) is
    sufficient to exercise `call()`'s full success branch — no real daemon
    process needed for this specific miss-vs-no-miss contract."""
    import asyncio
    import threading

    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD — pytest's tmp_path
    # is too deeply nested; force a short dir directly under /tmp.
    sock_dir = Path(tempfile.mkdtemp(prefix="nxm", dir="/tmp"))
    os.environ["NEXUS_DAEMON_SOCKET_DIR"] = str(sock_dir)
    ready = threading.Event()
    stop_loop: list = []

    def _serve_in_thread(sock_path: Path) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _handle(reader, writer):
            line = await reader.readline()
            req = json.loads(line)
            writer.write((json.dumps({"id": req.get("id"), "result": {"ok": True}}) + "\n").encode())
            await writer.drain()
            writer.close()

        async def _main():
            server = await asyncio.start_unix_server(_handle, path=str(sock_path))
            stop_loop.append(server)
            ready.set()
            async with server:
                await server.serve_forever()

        with contextlib.suppress(asyncio.CancelledError):
            loop.run_until_complete(_main())

    try:
        sock_path = daemon_rpc.socket_path(tmp_path)
        thread = threading.Thread(target=_serve_in_thread, args=(sock_path,), daemon=True)
        thread.start()
        assert ready.wait(timeout=5), "background unix-socket server never started"

        result = daemon_rpc.call(tmp_path, "health", {}, 2.0)
    finally:
        del os.environ["NEXUS_DAEMON_SOCKET_DIR"]
        shutil.rmtree(sock_dir, ignore_errors=True)

    assert result == {"ok": True}
    miss_path = tmp_path / ".memory" / "files" / "daemon_rpc_misses.jsonl"
    assert not miss_path.exists(), "a successful RPC must never also record a miss"


# ── emit_gate_span — no-op contract (no daemon needed) ──────────────────────

def test_emit_gate_span_is_a_pure_noop_without_span_attrs(monkeypatch) -> None:
    """The overwhelming majority of `_gate_deny_mod.deny()/.advise()` call
    sites in this repo never pass `span_attrs` — this must cost them
    NOTHING, not even a daemon-rpc import attempt."""
    calls: list = []
    monkeypatch.setattr(gate_deny, "_load_daemon_rpc", lambda: calls.append("loaded") or daemon_rpc)
    gate_deny.emit_gate_span("PreToolUse", "TEST/CODE", "deny", "reason", None)
    gate_deny.emit_gate_span("PreToolUse", "TEST/CODE", "deny", "reason", {})
    gate_deny.emit_gate_span("PreToolUse", "TEST/CODE", "deny", "reason", {"lens_verdict": "FAIL"})
    assert calls == [], "no RPC module load attempted without a resolvable trace_id"


def test_deny_and_advise_return_values_unchanged_when_span_attrs_passed(tmp_path: Path) -> None:
    """span_attrs must never change deny()/advise()'s own return value,
    stdout shape, or exit-code contract — only ADD a best-effort side
    channel. Points at an isolated repo root with no daemon so the RPC
    attempt is a guaranteed no-op miss."""
    monkeypatch_root = tmp_path
    os.environ["_HOOK_REPO_ROOT"] = str(monkeypatch_root)
    try:
        rc = gate_deny.deny(
            "PreToolUse", "TEST/CODE", "a reason", exit_code=2, stderr=False,
            span_attrs={"trace_id": "sess-1", "lens_verdict": "FAIL"},
        )
        assert rc == 2
        rc0 = gate_deny.advise(
            "PreToolUse", "TEST/CODE", "a msg", stderr=False,
            span_attrs={"trace_id": "sess-1", "lens_verdict": "PASS"},
        )
        assert rc0 == 0
    finally:
        del os.environ["_HOOK_REPO_ROOT"]


# ── end-to-end: a real daemon durably records the gate span ─────────────────

def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    return project


@pytest.fixture()
def project(tmp_path) -> Path:
    return _make_project(tmp_path)


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD — force a short dir
    # directly under /tmp (mirrors test_daemon_pilot.py's own fixture).
    sock_dir = Path(tempfile.mkdtemp(prefix="nxg", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


def _spawn_daemon(project_path: Path) -> subprocess.Popen:
    env = {**os.environ}
    return subprocess.Popen(
        [sys.executable, "-m", "broker.daemon.server", "--project-path", str(project_path)],
        cwd=str(BROKER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_health(project_path: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = daemon_rpc.call(project_path, "health", {}, 0.3)
        if result is not None:
            return
        time.sleep(0.05)
    raise AssertionError("daemon never became healthy")


def _stop_daemon(proc: subprocess.Popen, project_path: Path) -> None:
    sock_path = paths.socket_path_for(project_path)
    with contextlib.suppress(ProcessLookupError):
        proc.send_signal(signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    deadline = time.monotonic() + 5
    while sock_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_deny_with_span_attrs_durably_records_a_gate_span(project, isolated_sockets) -> None:
    """A real, deny-capable gate-span emission (lens_verdict/lens_tier/
    revise_reasons/gate_name/verdict/reason) lands in spans.duckdb, queryable
    via `SpanStore.query_trace` after the daemon releases the file — the
    exact single-writer discipline `spans.py`'s own module docstring and
    `span_smoke.py` already prove for the daemon side."""
    proc = _spawn_daemon(project)
    os.environ["_HOOK_REPO_ROOT"] = str(project)
    os.environ["NEXUS_GATE_SPAN_TIMEOUT_S"] = "2.0"
    try:
        _wait_for_health(project)
        rc = gate_deny.deny(
            "SubagentStop",
            "LENS/NO-VALIDATION",
            "no lens row found",
            span_attrs={
                "trace_id": "trace-gate-1",
                "lens_verdict": "FAIL",
                "lens_tier": "T2",
                "revise_reasons": ["missing verification_result"],
                "rpc_miss": False,
                "rpc_latency_ms": 12.5,
                "task_id": "TASK-094",
            },
        )
        assert rc == 2
        # Give the daemon's event loop a moment to process the RPC before
        # tearing it down (the deny() call itself is fire-and-forget-fast,
        # not awaited by this test process).
        time.sleep(0.3)
    finally:
        del os.environ["_HOOK_REPO_ROOT"]
        del os.environ["NEXUS_GATE_SPAN_TIMEOUT_S"]
        _stop_daemon(proc, project)

    store = SpanStore(spans_db_path_for(project))
    try:
        spans_for_trace = store.query_trace("trace-gate-1")
    finally:
        store.close()

    assert len(spans_for_trace) == 1, spans_for_trace
    span = spans_for_trace[0]
    assert span["kind"] == "gate"
    assert span["status"] == "ERROR"
    assert span["task_id"] == "TASK-094"
    attrs = json.loads(span["attributes"])
    assert attrs["gate_name"] == "LENS"
    assert attrs["verdict"] == "deny"
    assert attrs["lens_verdict"] == "FAIL"
    assert attrs["lens_tier"] == "T2"
    assert attrs["revise_reasons"] == ["missing verification_result"]
    assert attrs["rpc_miss"] is False
    assert attrs["rpc_latency_ms"] == 12.5
    assert "no lens row found" in attrs["reason"]


def test_advise_with_span_attrs_records_ok_status_gate_span(project, isolated_sockets) -> None:
    proc = _spawn_daemon(project)
    os.environ["_HOOK_REPO_ROOT"] = str(project)
    os.environ["NEXUS_GATE_SPAN_TIMEOUT_S"] = "2.0"
    try:
        _wait_for_health(project)
        rc = gate_deny.advise(
            "SubagentStop",
            "LENS/TIER",
            "Lens tier for this change: T1",
            span_attrs={"trace_id": "trace-gate-2", "lens_verdict": "PASS", "lens_tier": "T1"},
        )
        assert rc == 0
        time.sleep(0.3)
    finally:
        del os.environ["_HOOK_REPO_ROOT"]
        del os.environ["NEXUS_GATE_SPAN_TIMEOUT_S"]
        _stop_daemon(proc, project)

    store = SpanStore(spans_db_path_for(project))
    try:
        spans_for_trace = store.query_trace("trace-gate-2")
    finally:
        store.close()

    assert len(spans_for_trace) == 1
    span = spans_for_trace[0]
    assert span["kind"] == "gate"
    assert span["status"] == "OK"
    attrs = json.loads(span["attributes"])
    assert attrs["lens_verdict"] == "PASS"
    assert attrs["lens_tier"] == "T1"

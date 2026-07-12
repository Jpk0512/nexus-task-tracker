"""N31 broker-side daemon activation (plans/14 SS6) — `python -m broker.daemon
ensure` entrypoint + drift_watch lifecycle wiring.

Covers exactly this node's acceptance criteria:
  1. `ensure()` is idempotent: a second invocation against an already-healthy
     daemon spawns nothing.
  2. The 1.7 idle-shutdown and 1.8 stale-socket self-heal drills pass through
     the `ensure()` path itself (not just the raw `client.call` spawn-on-
     demand path `test_daemon_pilot.py` already covers).
  3. `drift_watch` runs under the daemon lifecycle (`server.start_drift_watch`)
     and flags a seeded divergence.
  4. `build_snapshot --check` staying green after sync is verified separately
     at the shell level (deployable-engineering's release-gate step) — not a
     pytest-level assertion here, matching `test_daemon_drift_watch.py`'s own
     note.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from broker.daemon import client as daemon_client
from broker.daemon import ensure as ensure_mod
from broker.daemon import paths
from broker.daemon.client import DaemonUnavailable
from broker.daemon.server import DaemonState, start_drift_watch

BROKER_ROOT = Path(__file__).resolve().parent.parent  # nexus-broker/


@pytest.fixture()
def project(tmp_path) -> Path:
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    return project


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # See test_daemon_pilot.py's matching fixture: AF_UNIX paths are capped at
    # ~104 bytes on macOS/BSD, so this forces a short-named dir directly under
    # /tmp instead of tmp_path's deep pytest-of-<user>/... nesting.
    sock_dir = Path(tempfile.mkdtemp(prefix="nxde", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture()
def spawned_daemons():
    """Tracks PIDs spawned during a test so the suite never leaks a resident
    daemon process across runs."""
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


def _wait_for_death(proc: subprocess.Popen, sock_path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.1)
    assert proc.poll() is not None, "daemon did not idle-shutdown in time"
    assert not sock_path.exists(), "idle-shutdown must remove its own socket file"


# ── AC1 — ensure() idempotency ──────────────────────────────────────────────


@pytest.mark.slow
def test_ensure_spawns_on_first_call_and_nothing_on_second(
    project, isolated_sockets, spawned_daemons, monkeypatch
) -> None:
    sock_path = paths.socket_path_for(project)
    assert not sock_path.exists()

    spawn_calls: list[Path] = []
    real_spawn = daemon_client._spawn_daemon

    def _tracking_spawn(p: Path) -> None:
        spawn_calls.append(p)
        real_spawn(p)

    monkeypatch.setattr(daemon_client, "_spawn_daemon", _tracking_spawn)

    result1 = ensure_mod.ensure(project, spawn_wait_s=10.0)
    assert result1["status"] == "ok"
    spawned_daemons.append(result1["pid"])
    assert len(spawn_calls) == 1

    # Second invocation against the now-healthy daemon: must answer via the
    # first probe inside client.call() and spawn NOTHING.
    result2 = ensure_mod.ensure(project)
    assert result2["pid"] == result1["pid"]
    assert len(spawn_calls) == 1


def test_ensure_raises_daemon_unavailable_when_unreachable_and_spawn_fails(
    project, isolated_sockets, monkeypatch
) -> None:
    """A daemon that never becomes reachable, even after ensure()'s own spawn
    attempt, must surface as DaemonUnavailable — never a silently swallowed
    failure the CLI could mistake for success."""
    monkeypatch.setattr(daemon_client, "_spawn_daemon", lambda project_path: None)
    with pytest.raises(DaemonUnavailable):
        ensure_mod.ensure(project, connect_timeout=0.1, spawn_wait_s=0.2)


# ── CLI (`python -m broker.daemon ensure`) exit-code translation ───────────


def test_main_ensure_exits_0_on_success(project, monkeypatch) -> None:
    def fake_ensure(project_path, **kwargs):
        return {"status": "ok", "pid": 12345, "project_path": str(project_path), "uptime_s": 0.01}

    monkeypatch.setattr(ensure_mod, "ensure", fake_ensure)
    rc = ensure_mod.main(["ensure", "--project-path", str(project)])
    assert rc == 0


def test_main_ensure_exits_nonzero_when_daemon_unavailable(project, monkeypatch) -> None:
    def fake_ensure(project_path, **kwargs):
        raise DaemonUnavailable("nope")

    monkeypatch.setattr(ensure_mod, "ensure", fake_ensure)
    rc = ensure_mod.main(["ensure", "--project-path", str(project)])
    assert rc != 0


def test_main_ensure_prints_json_health_payload(project, monkeypatch, capsys) -> None:
    def fake_ensure(project_path, **kwargs):
        return {"status": "ok", "pid": 999, "project_path": str(project_path), "uptime_s": 1.5}

    monkeypatch.setattr(ensure_mod, "ensure", fake_ensure)
    rc = ensure_mod.main(["ensure", "--project-path", str(project)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["pid"] == 999


# ── AC2 — 1.7 idle-shutdown drill through the ensure() path ────────────────


@pytest.mark.slow
def test_idle_shutdown_drill_through_ensure_path(project, isolated_sockets) -> None:
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

        # ensure() against the already-healthy daemon: spawns nothing, just
        # confirms health — exercising the SAME idempotent path AC1 covers.
        confirmed = ensure_mod.ensure(project)
        assert confirmed["status"] == "ok"

        _wait_for_death(proc, sock_path)

        # ensure() against the now-dead daemon must self-heal + respawn.
        respawned = ensure_mod.ensure(project, spawn_wait_s=10.0)
        assert respawned["status"] == "ok"
        assert respawned["pid"] != confirmed["pid"]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        with contextlib.suppress(Exception):  # noqa: BLE001 — best-effort reap of ensure()'s respawn
            leftover = daemon_client.call(
                project, "health", spawn_if_missing=False, connect_timeout=0.2
            )
            os.kill(leftover["pid"], signal.SIGKILL)


# ── AC2 — 1.8 stale-socket self-heal drill through the ensure() path ───────


@pytest.mark.slow
def test_stale_socket_self_heals_through_ensure_path(
    project, isolated_sockets, spawned_daemons
) -> None:
    sock_path = paths.socket_path_for(project)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    # A bound-but-never-listened socket: any connect() to it raises
    # ConnectionRefusedError — the "leftover socket file, no live listener"
    # shape 1.8 exists to self-heal.
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()
    assert sock_path.exists()

    result = ensure_mod.ensure(project, spawn_wait_s=10.0)
    assert result["status"] == "ok"
    spawned_daemons.append(result["pid"])


# ── AC3 — drift_watch under the daemon lifecycle ────────────────────────────


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _bump_mtime(path: Path) -> None:
    new_time = time.time() + 5.0
    os.utime(path, (new_time, new_time))


def _build_fake_meta_repo(root: Path) -> None:
    """A synthetic meta-repo-shaped tree under tmp_path — mirrors
    test_daemon_drift_watch.py's own helper (never the real repo's
    nexus-package/, per the deployable-engineering install-surface-test
    gotcha)."""
    _write(root / "tools" / "build_snapshot.sh", "PLEXUS_SELF_TESTS=()\n")
    (root / "nexus-package").mkdir(parents=True, exist_ok=True)
    _write(root / "nexus-broker" / "src" / "broker" / "mod.py", "VALUE = 1\n")
    _write(root / "nexus-package" / "nexus-broker" / "src" / "broker" / "mod.py", "VALUE = 1\n")
    _write(root / ".memory" / "log.py", "VERSION = 1\n")
    _write(root / ".memory" / "schema.sql", "CREATE TABLE t (id INTEGER);\n")
    _write(root / ".memory" / "health.py", "def check(): return True\n")
    _write(root / "nexus-package" / ".memory" / "log.py", "VERSION = 1\n")
    _write(root / "nexus-package" / ".memory" / "schema.sql", "CREATE TABLE t (id INTEGER);\n")
    _write(root / "nexus-package" / ".memory" / "health.py", "def check(): return True\n")


def test_start_drift_watch_returns_none_for_non_meta_repo_tenant(project) -> None:
    state = DaemonState(project)
    task = start_drift_watch(state, interval_s=0.05)
    assert task is None
    assert state.drift_watcher is None


async def test_drift_watch_runs_under_daemon_lifecycle_and_flags_seeded_divergence(
    tmp_path,
) -> None:
    _build_fake_meta_repo(tmp_path)
    state = DaemonState(tmp_path)

    task = start_drift_watch(state, interval_s=0.05)
    try:
        assert task is not None
        assert state.drift_watcher is not None  # set synchronously, before the first tick

        await asyncio.sleep(0.15)
        assert state.drift_report is not None
        assert state.drift_report.has_drift is False  # clean fake meta-repo, first check

        # Seed a real live-vs-package divergence.
        live_mod = tmp_path / "nexus-broker" / "src" / "broker" / "mod.py"
        live_mod.write_text("VALUE = 2\n", encoding="utf-8")
        _bump_mtime(live_mod)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not state.drift_report.has_drift:
            await asyncio.sleep(0.05)

        assert state.drift_report.has_drift is True
        assert any(
            f.rel_path == "broker/mod.py" and f.pair_label == "nexus-broker/src"
            for f in state.drift_report.findings
        )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_drift_watch_loop_failure_never_kills_the_loop(monkeypatch, tmp_path) -> None:
    """A single check() raising must not stop future ticks — drift-watch is
    advisory-only (see drift_watch.py's module docstring)."""
    _build_fake_meta_repo(tmp_path)
    state = DaemonState(tmp_path)

    task = start_drift_watch(state, interval_s=0.02)
    try:
        assert task is not None
        assert state.drift_watcher is not None

        calls = {"n": 0}
        real_check = state.drift_watcher.check

        def _flaky_check(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("seeded transient failure")
            return real_check(*args, **kwargs)

        monkeypatch.setattr(state.drift_watcher, "check", _flaky_check)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and calls["n"] < 3:
            await asyncio.sleep(0.02)

        assert calls["n"] >= 3, "loop must keep ticking past a raised check()"
        assert not task.done(), "a check() failure must never crash the background task"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

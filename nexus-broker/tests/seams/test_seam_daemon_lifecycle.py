"""Seam test: broker.daemon start/ready/stop end-to-end (TASK-118).

Real integration boundary — never mocked:
  * a REAL `broker.daemon.server` subprocess, driven only through its actual
    Unix-domain-socket wire protocol and the real `pidfile.py` OS-level flock
    (the exact three primitives — pidfile, socket, owner-checked unlink — the
    2026-07-17 zombie-daemon incident hinged on, see pidfile.py's docstring);
  * the sibling `ready_set.serve_ready_set` thin server, driven over its own
    real Unix socket with a background-thread event loop (mirrors
    test_daemon_ready_set.py's own `_BackgroundReadySetServer` pattern) so a
    DAG run's readiness lifecycle is exercised the same way — real bytes over
    a real socket, not a direct in-process call into the registry.

Every temp dir/socket/process is torn down at test end; nothing touches
~/.nexus or the real repo.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import socket as _socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from broker.daemon import paths, pidfile, ready_set

_BROKER_ROOT = Path(__file__).resolve().parents[2]


def _send_recv(sock_path: Path, request: dict, *, timeout: float = 5.0) -> dict:
    s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8"))
    finally:
        with contextlib.suppress(OSError):
            s.close()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".memory").mkdir(parents=True)
    return p


@pytest.fixture()
def sock_dir(monkeypatch: pytest.MonkeyPatch) -> Path:
    # AF_UNIX paths cap ~104 bytes on macOS — pytest's tmp_path is too deep/long
    # for a socket file to bind under; a short /tmp dir mirrors the same
    # workaround conftest.py's spawn_daemon_for_project fixture already uses.
    d = Path(tempfile.mkdtemp(prefix="nxsm-", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(d))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_daemon_start_ready_stop_end_to_end(project: Path, sock_dir: Path) -> None:
    """GIVEN a fresh tmp project, WHEN the real daemon server subprocess is
    spawned, THEN it (a) acquires the pidfile lock and writes the owner
    record ("start"), (b) opens its Unix socket and answers a real health RPC
    ("ready"), and (c) on SIGTERM releases the pidfile lock and unlinks its
    OWN socket via the owner-checked unlink ("stop") — never leaving a
    zombie holder behind for the next spawn to collide with.
    """
    sock_path = paths.socket_path_for(project)
    pf_path = pidfile.pidfile_path_for(project)

    env = dict(os.environ)
    env["NEXUS_DAEMON_SOCKET_DIR"] = str(sock_dir)
    env["NEXUS_DISABLE_VEC"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "broker.daemon.server", "--project-path", str(project)],
        cwd=str(_BROKER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # --- start: pidfile lock acquired + owner record written ---
        deadline = time.time() + 10.0
        while True:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                err = proc.stderr.read() if proc.stderr else ""
                pytest.fail(
                    f"daemon exited early rc={proc.returncode}\nstdout={out}\nstderr={err}"
                )
            if sock_path.exists() and pf_path.exists():
                break
            if time.time() >= deadline:
                pytest.fail("daemon never created its socket + pidfile within 10s")
            time.sleep(0.05)

        holder = pidfile.holder_pid(pf_path)
        assert holder == proc.pid, f"pidfile must record the LIVE daemon pid, got {holder}"

        pf_data = pidfile.read_pidfile(pf_path)
        assert pf_data is not None
        assert pf_data["pid"] == proc.pid
        assert pf_data["socket"] == str(sock_path)

        # --- ready: a real health RPC over the real socket ---
        resp = None
        deadline = time.time() + 10.0
        while time.time() < deadline:
            with contextlib.suppress(OSError, ValueError):
                resp = _send_recv(sock_path, {"id": 1, "method": "health", "params": {}})
            if resp is not None and resp.get("result", {}).get("status") == "ok":
                break
            time.sleep(0.05)
        assert resp is not None, "daemon never answered a health RPC"
        assert resp["result"]["status"] == "ok"
        assert resp["result"]["pid"] == proc.pid

        # --- stop: SIGTERM must release the lock and unlink the socket ---
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            pytest.fail("daemon did not exit within 10s of SIGTERM")

        assert not sock_path.exists(), (
            "clean shutdown must unlink its own socket (owner-checked unlink, TASK-105)"
        )
        assert pidfile.holder_pid(pf_path) is None, (
            "pidfile lock must be released on clean shutdown"
        )

        relock = pidfile.PidfileLock(project)
        assert relock.acquire() is True, (
            "a released pidfile lock must be re-acquirable by a fresh process — "
            "the exact single-instance guarantee the zombie incident broke"
        )
        relock.release()
    finally:
        if proc.poll() is None:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


class _BackgroundReadySetServer:
    """Runs `serve_ready_set` on its own thread + event loop so this test's
    synchronous blocking-socket client never contends with the server's
    asyncio loop for the same OS thread — same two-process-like relationship
    `test_daemon_start_ready_stop_end_to_end` gets for free from a real
    subprocess, without subprocess-spawn overhead for this sub-server."""

    def __init__(self, sock_path: Path) -> None:
        self.sock_path = sock_path
        self._loop = asyncio.new_event_loop()
        self._server: asyncio.Server | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "ready-set test server never started"

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)

        async def _start() -> None:
            self._server = await ready_set.serve_ready_set(self.sock_path)
            self._ready.set()

        self._loop.create_task(_start())
        self._loop.run_forever()

    def stop(self) -> None:
        async def _shutdown() -> None:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()

        asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=5.0)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        self.sock_path.unlink(missing_ok=True)


def test_daemon_ready_set_serves_real_dag_over_unix_socket() -> None:
    """GIVEN the daemon-resident ready-set thin server, WHEN a 2-node DAG is
    registered and driven through register/pull/complete/snapshot/invalidate
    over a REAL Unix-domain socket, THEN a dependency-gated node only becomes
    pullable after its dependency completes, and invalidate tears the run
    down so a later snapshot errors rather than silently succeeding.
    """
    # AF_UNIX paths cap ~104 bytes on macOS — a short /tmp dir, not pytest's
    # (deep, long) tmp_path, mirrors the same workaround `sock_dir` above uses.
    sock_tmp = Path(tempfile.mkdtemp(prefix="nxrs-", dir="/tmp"))
    sock_path = sock_tmp / "readyset.sock"
    server = _BackgroundReadySetServer(sock_path)
    try:
        assert sock_path.exists()
        nodes = [
            {"node_id": "a", "depends_on": []},
            {"node_id": "b", "depends_on": ["a"]},
        ]
        reg = _send_recv(
            sock_path,
            {"id": 1, "method": "ready_set_register", "params": {"run_id": "run-1", "nodes": nodes}},
        )
        assert reg["result"] == {"registered": True, "node_count": 2}

        pulled = _send_recv(
            sock_path, {"id": 2, "method": "ready_set_pull", "params": {"run_id": "run-1"}}
        )
        assert pulled["result"]["node_id"] == "a", "only the zero-dependency node is pullable first"

        blocked = _send_recv(
            sock_path, {"id": 3, "method": "ready_set_pull", "params": {"run_id": "run-1"}}
        )
        assert blocked["result"]["node_id"] is None, "'b' must stay blocked on its dependency"

        completed = _send_recv(
            sock_path,
            {"id": 4, "method": "ready_set_complete", "params": {"run_id": "run-1", "node_id": "a"}},
        )
        assert completed["result"]["newly_ready"] == ["b"]

        pulled_b = _send_recv(
            sock_path, {"id": 5, "method": "ready_set_pull", "params": {"run_id": "run-1"}}
        )
        assert pulled_b["result"]["node_id"] == "b"

        snap = _send_recv(
            sock_path, {"id": 6, "method": "ready_set_snapshot", "params": {"run_id": "run-1"}}
        )
        assert snap["result"]["completed"] == ["a"]
        assert snap["result"]["remaining"] == 1

        inv = _send_recv(
            sock_path, {"id": 7, "method": "ready_set_invalidate", "params": {"run_id": "run-1"}}
        )
        assert inv["result"] == {"invalidated": True}

        gone = _send_recv(
            sock_path, {"id": 8, "method": "ready_set_snapshot", "params": {"run_id": "run-1"}}
        )
        assert "error" in gone, "a snapshot after invalidate must error (UnknownRun), not succeed"
    finally:
        server.stop()
        shutil.rmtree(sock_tmp, ignore_errors=True)

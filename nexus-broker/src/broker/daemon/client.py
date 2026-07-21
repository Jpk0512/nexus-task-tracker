"""Thin RPC client — connect / spawn-on-demand / stale-socket self-heal.

This is the "thin RPC shim" R4-T06's charter requires: it NEVER silently
assumes an answer. On any failure to reach the daemon (even after a spawn
attempt) it raises `DaemonUnavailable` — the caller (see fallback.py; a
future hook integration is out of this pilot's scope, do_not_touch:
`.claude/hooks/**`) is the one that must fail closed to a direct file/db
read. There is no daemon-required invariant anywhere in this module.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

from broker.daemon import paths, pidfile

# nexus-broker/ repo root — three parents up from daemon/client.py
# (daemon -> broker -> src -> nexus-broker). The spawned subprocess needs
# this as its cwd so `python -m broker.daemon.server` resolves.
_NEXUS_BROKER_ROOT = Path(__file__).resolve().parents[3]


class DaemonUnavailable(Exception):
    """Raised when the daemon cannot be reached, even after a spawn attempt."""


def _rpc(sock_path: Path, method: str, params: dict[str, Any] | None, timeout: float) -> Any:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(sock_path))
        request = {"id": 1, "method": method, "params": params or {}}
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        if not buf:
            raise DaemonUnavailable(f"daemon closed connection with no response ({method})")
        response = json.loads(buf.decode("utf-8"))
        if "error" in response:
            raise DaemonUnavailable(f"daemon RPC error: {response['error']}")
        return response["result"]
    finally:
        sock.close()


def _spawn_daemon(project_path: Path) -> None:
    """Double-fork daemonize: the grandchild execs the real daemon and is
    reparented to init, so it never needs reaping by whoever called `call()`.

    A plain `subprocess.Popen(..., start_new_session=True)` detaches the
    session but NOT the parent/child relationship — a long-lived caller
    (an orchestrator session, or this test suite) that never `wait()`s on it
    accumulates a zombie per spawn once the daemon later exits (idle-shutdown
    or a test's `kill -9`). The immediate child here exits right after its
    own fork, so the parent's `waitpid` returns almost instantly and the
    actual daemon (the grandchild) is owned by init from the start.
    """
    pid = os.fork()
    if pid == 0:
        os.setsid()
        pid2 = os.fork()
        if pid2 == 0:
            os.chdir(str(_NEXUS_BROKER_ROOT))
            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, 0)
            os.dup2(devnull, 1)
            os.dup2(devnull, 2)
            os.execv(
                sys.executable,
                [
                    sys.executable,
                    "-m",
                    "broker.daemon.server",
                    "--project-path",
                    str(project_path),
                ],
            )
            os._exit(127)  # exec failed
        os._exit(0)
    os.waitpid(pid, 0)  # reap the immediate child right away; it exits almost instantly


def call(
    project_path: Path,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    connect_timeout: float | None = None,
    spawn_if_missing: bool = True,
    spawn_wait_s: float | None = None,
) -> Any:
    """Call the daemon for `project_path`; spawn-on-demand + self-heal a stale socket.

    Raises DaemonUnavailable if the daemon cannot be reached — the caller
    must fall back to a direct file/db read (see fallback.py), never assume
    a daemon answer.
    """
    sock_path = paths.socket_path_for(project_path)
    timeout = connect_timeout if connect_timeout is not None else paths.CONNECT_TIMEOUT_S

    try:
        return _rpc(sock_path, method, params, timeout)
    except FileNotFoundError:
        pass  # no socket yet at all — fall through to spawn below
    except OSError:
        # 1.8 stale-socket self-heal: a socket FILE with no live listener
        # (ConnectionRefusedError, a subclass of OSError) must be unlinked,
        # never left to wedge every subsequent connect attempt. Owner-checked
        # since TASK-105: a socket whose pidfile owner is a LIVE process is
        # never unlinked from here — the ensure path reaps the owner first.
        pidfile.owner_checked_unlink(sock_path, pidfile.pidfile_path_for(project_path))

    if not spawn_if_missing:
        raise DaemonUnavailable(f"daemon unreachable for {project_path} (spawn disabled)")

    _spawn_daemon(project_path)
    wait_s = spawn_wait_s if spawn_wait_s is not None else paths.SPAWN_WAIT_S
    deadline = time.monotonic() + wait_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _rpc(sock_path, method, params, timeout)
        except OSError as exc:
            last_exc = exc
            time.sleep(0.05)
    raise DaemonUnavailable(f"daemon did not become reachable for {project_path}: {last_exc}")

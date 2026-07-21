"""conftest.py — nexus-package/.claude/hooks/tests/ fire-once state isolation.

TASK-085: lens-gate.sh's fire-once-per-(task_hash, code) dedup persists a
flag file (one per denial) to answer "did this exact block already fire this
turn?" across separate hook subprocess invocations. Left at its default
location (tempfile.gettempdir()), that state leaks ACROSS test functions in
this directory — several fixtures across this test suite omit both
`task_id`/`task_description` AND `session_id`, so _derive_task_hash's
fallback (`dispatch:{agent_name}:unknown-session`) collides identically
between UNRELATED tests using the same persona. One test's first-occurrence
BLOCK would leave a flag file that makes a LATER test's own expected
first-occurrence BLOCK incorrectly look like a repeat (WARN+allow instead of
exit 2).

Function-scoped + autouse: every test gets its own tmp_path-derived state
dir by default, so cross-test collisions are impossible. A single test that
invokes lens-gate.sh multiple times still shares this SAME path across those
calls within itself — only isolation ACROSS test functions changes.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

import pytest

# ── TASK-092 leg A: resident-daemon fixture (F2-03 R4e-live hook tests) ──────
#
# Hand-reconciled twin of `.claude/hooks/tests/conftest.py`'s fixture. The
# tranche-A advisory hooks (socraticode-flag.sh, analysis-paralysis-guard.sh,
# ... — F2-03) shrank to `exec python3 _ping_shim.py <event> <consumer>`; the
# real side-effect logic lives daemon-resident in
# `nexus-broker/src/broker/daemon/advisory_handlers.py`. With NO daemon the ping
# shim fails OPEN (silent), so any test asserting a banner/flag/count side-effect
# fails. This spawns a REAL, hermetic daemon (isolated /tmp socket dir + TMPDIR,
# one temp project each — NEVER ~/.nexus/daemon/ or the real .memory/) so those
# shipped hooks run against real handlers. No package-twin test consumes it yet
# (the twin carries no test_advisory_hooks.py); kept in parity with the meta-repo
# copy so a future package-hook-test migration inherits it.
#
# 3.9 IMPORT-SAFE (this twin runs un-shimmed under ambient python3): no
# `datetime.UTC`, no def-time `X | None`, no `match`/`case`; the interpreter is
# resolved portably (venv when present, else sys.executable — never a hard-coded
# meta-repo path).

_TESTS_DIR = Path(__file__).resolve().parent
_HOOKS_DIR = _TESTS_DIR.parent
_REPO_ROOT = _HOOKS_DIR.parent.parent
_NEXUS_BROKER = _REPO_ROOT / "nexus-broker"

_HEALTH_GATE_S = 5.0
_HEALTH_POLL_START_S = 0.05
_HEALTH_POLL_MAX_S = 0.5
_TERM_WAIT_S = 2.0


def _resolve_daemon_python() -> str:
    """Interpreter that can import `broker`: the nexus-broker venv when present,
    else the running interpreter (portable fallback — never a hard-coded path)."""
    for cand in (
        _NEXUS_BROKER / ".venv" / "bin" / "python3",
        _NEXUS_BROKER / ".venv" / "bin" / "python",
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return sys.executable


def _daemon_socket_path(socket_dir: Path, project_path: Path) -> Path:
    """Byte-identical to broker.daemon.paths.socket_path_for /
    _daemon_rpc.socket_path — sha256 of the resolved project path, first 16 hex."""
    digest = hashlib.sha256(str(Path(project_path).resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(socket_dir) / (digest + ".sock")


def _init_project(project_path: Path) -> None:
    mem = project_path / ".memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "broker_state.json").write_text(
        json.dumps(
            {
                "project_path": str(project_path),
                "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            }
        )
    )
    schema = _REPO_ROOT / ".memory" / "schema.sql"
    db = mem / "project.db"
    if schema.is_file() and not db.exists():
        try:
            import sqlite3

            conn = sqlite3.connect(str(db))
            try:
                import sqlite_vec

                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except Exception:
                pass  # vec unavailable (ambient 3.9 / NEXUS_DISABLE_VEC) — schema still loads what it can
            conn.executescript(schema.read_text())
            conn.commit()
            conn.close()
        except Exception:
            pass  # best-effort init — the env-seam handlers this fixture serves never read project.db


class _DaemonHandle:
    """One live daemon serving exactly one project. `env` carries the seams the
    ping shim needs to reach THIS daemon: `NEXUS_DAEMON_SOCKET_DIR` +
    `_HOOK_REPO_ROOT` (which daemon/project), plus a widened
    `NEXUS_PING_SHIM_TIMEOUT_S` for the project_path-BOUND consumers whose
    daemon-resident handler shells out to `log.py` (task-db-mirror, task-mirror,
    session-task-reconcile, reflection-capture, stall-counter,
    memory-errors-banner). The shim's 50ms default (sized for pure in-memory
    advisory compute) fires before that subprocess replies — the client drops
    the connection mid-write and the shim reports a miss (fail-open); the widen
    is test-scoped and does NOT change production shim defaults. Consumers with a
    baked per-consumer default (router-health-check) ignore this global. A test
    merges this into the hook subprocess env. (Hand-reconciled twin of the
    meta-repo copy; kept in parity so a future package-hook-test migration
    inherits the seam.)"""

    _SLOW_HANDLER_TIMEOUT_S = "15"

    def __init__(self, project_path: Path, socket_dir: Path, tmpdir: Path, process: subprocess.Popen) -> None:
        self.project_path = Path(project_path)
        self.socket_dir = Path(socket_dir)
        self.tmpdir = Path(tmpdir)
        self.process = process
        self.socket_path = _daemon_socket_path(socket_dir, project_path)

    @property
    def env(self) -> dict:
        return {
            "NEXUS_DAEMON_SOCKET_DIR": str(self.socket_dir),
            "_HOOK_REPO_ROOT": str(self.project_path),
            "NEXUS_PING_SHIM_TIMEOUT_S": self._SLOW_HANDLER_TIMEOUT_S,
        }


class _ResidentDaemons:
    """Session-scoped daemon factory. Attribute access (`project_path`,
    `socket_path`, `tmpdir`, `env`, `process`) delegates to the DEFAULT daemon;
    `for_project(path)` spawns/caches a daemon for an arbitrary (isolated-repo)
    project. Teardown stops every spawned daemon and removes all temp state."""

    def __init__(self, base_tmp: Path) -> None:
        self._base_tmp = Path(base_tmp)
        self._socket_dir = Path(tempfile.mkdtemp(prefix="rd-s-", dir="/tmp"))
        self._python = _resolve_daemon_python()
        self._handles: dict = {}
        self._tmpdirs: list = [self._socket_dir]
        default_project = self._base_tmp / "default-project"
        self._default = self.for_project(default_project)

    def for_project(self, project_path, extra_env=None) -> _DaemonHandle:
        project_path = Path(project_path)
        key = str(project_path)
        if key in self._handles:
            return self._handles[key]
        _init_project(project_path)
        daemon_tmp = Path(tempfile.mkdtemp(prefix="rd-t-", dir="/tmp"))
        self._tmpdirs.append(daemon_tmp)
        env = dict(os.environ)
        env["NEXUS_DAEMON_SOCKET_DIR"] = str(self._socket_dir)
        env["NEXUS_DISABLE_VEC"] = "1"
        env["TMPDIR"] = str(daemon_tmp)
        if extra_env:
            env.update(extra_env)
        process = subprocess.Popen(
            [self._python, "-m", "broker.daemon.server", "--project-path", str(project_path)],
            cwd=str(_NEXUS_BROKER),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        handle = _DaemonHandle(project_path, self._socket_dir, daemon_tmp, process)
        self._await_health(handle)
        self._handles[key] = handle
        return handle

    def _await_health(self, handle: _DaemonHandle) -> None:
        deadline = time.time() + _HEALTH_GATE_S
        delay = _HEALTH_POLL_START_S
        while time.time() < deadline:
            if handle.process.poll() is not None:
                _err = handle.process.stderr.read() if handle.process.stderr else ""
                raise pytest.fail.Exception(
                    "resident daemon exited during health gate (rc="
                    + str(handle.process.returncode)
                    + "): "
                    + _err
                )
            if handle.socket_path.exists() and self._ping(handle.socket_path):
                return
            time.sleep(delay)
            delay = min(delay * 2, _HEALTH_POLL_MAX_S)
        self._stop(handle)
        raise pytest.fail.Exception(
            "resident daemon did not answer health within "
            + str(_HEALTH_GATE_S)
            + "s (socket=" + str(handle.socket_path) + ")"
        )

    @staticmethod
    def _ping(socket_path: Path) -> bool:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            s.connect(str(socket_path))
            s.sendall((json.dumps({"id": 1, "method": "health", "params": {}}) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return bool(buf)
        except OSError:
            return False
        finally:
            with contextlib.suppress(OSError):
                s.close()

    @staticmethod
    def _stop(handle: _DaemonHandle) -> None:
        proc = handle.process
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=_TERM_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=_TERM_WAIT_S)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    def shutdown(self) -> None:
        for handle in self._handles.values():
            self._stop(handle)
        for d in self._tmpdirs:
            shutil.rmtree(d, ignore_errors=True)

    # DEFAULT-daemon delegation (design: "exposes project_path, socket_path, env, process")
    @property
    def project_path(self) -> Path:
        return self._default.project_path

    @property
    def socket_path(self) -> Path:
        return self._default.socket_path

    @property
    def tmpdir(self) -> Path:
        return self._default.tmpdir

    @property
    def env(self) -> dict:
        return self._default.env

    @property
    def process(self) -> subprocess.Popen:
        return self._default.process


@pytest.fixture(scope="session")
def resident_daemon(tmp_path_factory):
    daemons = _ResidentDaemons(tmp_path_factory.mktemp("resident-daemon"))
    try:
        yield daemons
    finally:
        daemons.shutdown()


@pytest.fixture(autouse=True)
def _lens_gate_fire_once_state_isolation(tmp_path):
    previous = os.environ.get("_HOOK_LENS_GATE_STATE_DIR")
    state_dir = tmp_path / "lens-gate-state"
    os.environ["_HOOK_LENS_GATE_STATE_DIR"] = str(state_dir)
    try:
        yield str(state_dir)
    finally:
        if previous is None:
            os.environ.pop("_HOOK_LENS_GATE_STATE_DIR", None)
        else:
            os.environ["_HOOK_LENS_GATE_STATE_DIR"] = previous

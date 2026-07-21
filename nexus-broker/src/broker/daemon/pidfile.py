"""Single-instance pidfile lock, zombie reaping, and owner-checked socket
unlink for the per-project daemon (TASK-105).

Production incident this exists to prevent (2026-07-17): a daemon survived an
app quit SOCKETLESS while still holding the events.duckdb write lock. Liveness
was socket-only, so `ensure` could not see the zombie and spawned a duplicate —
two daemons, the DB lock captured by the zombie. When the zombie was SIGTERMed,
its exit cleanup unlinked the socket the LIVE daemon was serving, because
socket unlink had no owner check.

Three primitives close all three holes:
  1. `PidfileLock` — an fcntl.flock-backed OS-level lock under
     ~/.nexus/daemon/<digest>.pid (same digest as the socket). Held for the
     daemon's whole life; the kernel releases it on ANY process death, so a
     socketless zombie is still visible via `holder_pid`.
  2. `reap` — TERM, wait, escalate to KILL: the ensure path's tool for
     removing a lock-holding zombie before a clean respawn.
  3. `owner_checked_unlink` — a socket file is unlinked ONLY if the unlinking
     process owns it per the pidfile, or the recorded owner is dead, or no
     owner was ever recorded (pre-pidfile daemon).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from broker.daemon import paths

_DAEMON_SRC = Path(__file__).resolve().parent


def pidfile_path_for(project_path: Path) -> Path:
    """~/.nexus/daemon/<digest>.pid — same digest as `paths.socket_path_for`."""
    return paths.socket_path_for(project_path).with_suffix(".pid")


def source_version(src_dir: Path | None = None) -> str:
    """Digest of the daemon package's own source files, as they exist on disk.

    The running daemon computes this ONCE at startup and reports it as
    `resident_version` in its health payload; `ensure` recomputes it from disk
    and restarts any daemon whose resident code no longer matches — the
    auto-refresh that replaces "restart the app to pick up new daemon code".
    """
    base = src_dir if src_dir is not None else _DAEMON_SRC
    h = hashlib.sha256()
    for p in sorted(base.glob("*.py")):
        h.update(p.name.encode("utf-8"))
        with contextlib.suppress(OSError):
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


class PidfileLock:
    """fcntl.flock single-instance lock. The fd stays open for the holder's
    lifetime; the kernel drops the lock on process death, so liveness of the
    holder is exactly "the lock is still held" — no socket required."""

    def __init__(self, project_path: Path) -> None:
        self.path = pidfile_path_for(project_path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def write_owner(self, *, pid: int, socket_path: Path, version: str) -> None:
        if self._fd is None:
            raise RuntimeError("write_owner requires an acquired lock")
        payload = json.dumps(
            {
                "pid": pid,
                "socket": str(socket_path),
                "version": version,
                "started_at": time.time(),
            },
            separators=(",", ":"),
        )
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.ftruncate(self._fd, 0)
        os.write(self._fd, payload.encode("utf-8"))
        os.fsync(self._fd)

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(self._fd)
            self._fd = None


def read_pidfile(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def holder_pid(path: Path) -> int | None:
    """PID of the LIVE process currently holding the pidfile lock, else None.

    This is the zombie detector: a daemon that lost its socket but never died
    still holds the flock, so a failed socket ping plus a non-None holder here
    means "reap it, then respawn" — never "spawn a duplicate"."""
    if not path.exists():
        return None
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            data = read_pidfile(path)
            pid = data.get("pid") if data is not None else None
            if isinstance(pid, int) and pid_alive(pid):
                return pid
            return None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def _wait_dead(pid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        # Reap a defunct child so pid_alive stops seeing its zombie entry;
        # ChildProcessError just means it was never our child (the usual
        # init-parented daemon case).
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)
        if not pid_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def reap(pid: int, *, term_wait_s: float = 5.0, kill_wait_s: float = 2.0) -> str:
    """TERM, wait, escalate to KILL. Returns one of: "already-dead",
    "terminated", "killed", "survived"."""
    if not pid_alive(pid):
        return "already-dead"
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    if _wait_dead(pid, term_wait_s):
        return "terminated"
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
    if _wait_dead(pid, kill_wait_s):
        return "killed"
    return "survived"


def owner_checked_unlink(sock_path: Path, pidfile_path: Path) -> bool:
    """Unlink `sock_path` ONLY if this process owns it per the pidfile, or the
    recorded owner is dead, or no owner was ever recorded. Returns True when
    the socket file is gone on return.

    This is the fix for incident hole 3: a TERMed zombie's exit cleanup must
    never be able to unlink a socket a different live daemon is serving."""
    data = read_pidfile(pidfile_path)
    if data is not None:
        pid = data.get("pid")
        if isinstance(pid, int) and pid != os.getpid() and pid_alive(pid):
            return not sock_path.exists()
    with contextlib.suppress(OSError):
        sock_path.unlink(missing_ok=True)
    return not sock_path.exists()

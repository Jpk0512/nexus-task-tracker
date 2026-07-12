"""Socket-path derivation + env-tunable timing constants for the daemon pilot.

Per-project namespacing (plans/07 §1 constraint 3): one Unix-domain socket
per project, path derived deterministically from the project's absolute
path so a different project is structurally a different socket file, never
a shared logical namespace.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def socket_dir() -> Path:
    """Root directory for daemon sockets. Overridable so tests never touch ~/.nexus."""
    override = os.environ.get("NEXUS_DAEMON_SOCKET_DIR")
    if override:
        return Path(override)
    return Path.home() / ".nexus" / "daemon"


def socket_path_for(project_path: Path) -> Path:
    """~/.nexus/daemon/<sha256(project_path)[:16]>.sock — plans/13 N11 goal text, verbatim."""
    resolved = str(Path(project_path).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return socket_dir() / f"{digest}.sock"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Whole-round-trip budget (connect + send + recv — `socket.settimeout()`
# applies to every blocking op on the socket, not just connect()) before a
# client decides the daemon is unreachable and either spawns one or fails
# closed. 2s, not the ~100-250ms single-hook-cold-start cost this daemon
# exists to beat (plans/13 SS3): under real concurrent load (many daemon
# subprocesses forking/execving/scanning at once — reproduced empirically
# via 4-way-parallel test runs while validating this pilot) a live, healthy
# daemon's asyncio loop can take longer than a couple hundred ms to get
# scheduled and answer, even though it never crashed. A tighter budget here
# doesn't make the daemon safer — record_telemetry/get_registry already fail
# closed to a correct direct read/write on any timeout — it just trades a
# correct, present daemon for a slower fallback path more often than the
# real unreachable-daemon case requires.
CONNECT_TIMEOUT_S = _env_float("NEXUS_DAEMON_CONNECT_TIMEOUT_S", 2.0)

# After spawning an on-demand daemon, how long the client retries connecting
# before giving up and failing closed.
SPAWN_WAIT_S = _env_float("NEXUS_DAEMON_SPAWN_WAIT_S", 3.0)

# 1.7 idle-shutdown: no resident process the user must remember exists.
IDLE_TIMEOUT_S = _env_float("NEXUS_DAEMON_IDLE_TIMEOUT_S", 300.0)
IDLE_CHECK_INTERVAL_S = _env_float("NEXUS_DAEMON_IDLE_CHECK_INTERVAL_S", 1.0)

# 1.5 thin write-through batch: how often pending telemetry rows are flushed.
FLUSH_INTERVAL_S = _env_float("NEXUS_DAEMON_FLUSH_INTERVAL_S", 0.5)

# 2.6 install-drift background loop (N31, plans/14 SS6): how often the
# meta-repo-tenant-only DriftWatcher re-checks under the daemon's own event
# loop, and the TTL passed to the DriftWatcher itself (mirrors the watcher's
# own cache-invalidation posture — see drift_watch.py).
DRIFT_WATCH_INTERVAL_S = _env_float("NEXUS_DAEMON_DRIFT_WATCH_INTERVAL_S", 30.0)
DRIFT_WATCH_TTL_S = _env_float("NEXUS_DAEMON_DRIFT_WATCH_TTL_S", 30.0)

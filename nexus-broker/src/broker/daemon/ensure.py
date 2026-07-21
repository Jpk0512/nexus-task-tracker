"""`python -m broker.daemon ensure` — daemon lifecycle GUARANTEE (TASK-105,
supersedes the N31 thin-wrapper ensure).

The flow is now a four-way decision, not a bare spawn-on-demand probe:

  ping ok + resident_version matches on-disk source  -> done (idempotent)
  ping ok + resident_version stale/missing           -> graceful restart
                                                        (the auto-refresh that
                                                        replaces "restart the
                                                        app to pick up new
                                                        daemon code")
  ping fails + pidfile lock held by a live pid       -> reap the socketless
                                                        zombie (TERM, wait,
                                                        KILL), then respawn
  ping fails + no live lock holder                   -> clean spawn

The daemon stays cache-only and non-authoritative: `ensure` failing must never
be treated by any caller as a hard requirement — see `broker.daemon.fallback`
for the fail-closed contract every real consumer sits behind.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from broker.daemon import paths, pidfile
from broker.daemon.client import DaemonUnavailable, call

LOG = logging.getLogger("nexus-daemon.ensure")

ACTION_OK = "ok"
ACTION_RESTART_STALE = "restart-stale"
ACTION_REAP_THEN_SPAWN = "reap-then-spawn"
ACTION_SPAWN = "spawn"


def decide_action(
    ping_ok: bool,
    resident_version: str | None,
    current_version: str,
    holder: int | None,
) -> str:
    """Pure decision core — `holder` is the LIVE pidfile-lock holder (already
    liveness-checked by `pidfile.holder_pid`), or None. A responding daemon
    with no `resident_version` at all predates TASK-105 and is by definition
    stale."""
    if ping_ok:
        if resident_version is not None and resident_version == current_version:
            return ACTION_OK
        return ACTION_RESTART_STALE
    if holder is not None:
        return ACTION_REAP_THEN_SPAWN
    return ACTION_SPAWN


def ensure(
    project_path: Path,
    *,
    connect_timeout: float | None = None,
    spawn_wait_s: float | None = None,
) -> dict[str, Any]:
    """Guarantee a live, current-source daemon for `project_path`.

    Returns the `health` RPC payload plus an `ensure_action` key recording
    which branch ran. Raises `DaemonUnavailable` if the daemon never becomes
    reachable even after a spawn attempt."""
    project_path = Path(project_path)
    sock_path = paths.socket_path_for(project_path)
    pf_path = pidfile.pidfile_path_for(project_path)

    health: dict[str, Any] | None = None
    try:
        health = call(
            project_path,
            "health",
            spawn_if_missing=False,
            connect_timeout=connect_timeout,
        )
        ping_ok = True
    except DaemonUnavailable:
        ping_ok = False

    current_version = pidfile.source_version()
    holder = pidfile.holder_pid(pf_path)
    resident = health.get("resident_version") if health is not None else None
    action = decide_action(ping_ok, resident, current_version, holder)

    if action == ACTION_OK:
        assert health is not None
        health["ensure_action"] = ACTION_OK
        return health

    if action in (ACTION_RESTART_STALE, ACTION_REAP_THEN_SPAWN):
        target = holder
        if target is None and health is not None and isinstance(health.get("pid"), int):
            target = health["pid"]
        if target is not None:
            outcome = pidfile.reap(target)
            LOG.info("reaped daemon pid %s (%s) for %s [%s]", target, outcome, project_path, action)
        pidfile.owner_checked_unlink(sock_path, pf_path)

    fresh = call(
        project_path,
        "health",
        spawn_if_missing=True,
        connect_timeout=connect_timeout,
        spawn_wait_s=spawn_wait_s,
    )
    fresh["ensure_action"] = action
    return fresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m broker.daemon",
        description="Nexus daemon lifecycle CLI (TASK-105 lifecycle guarantee)",
    )
    parser.add_argument(
        "verb",
        choices=["ensure"],
        help="ensure: ping -> stale-restart / zombie-reap / spawn; exit 0 healthy, nonzero otherwise",
    )
    parser.add_argument("--project-path", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("NEXUS_DAEMON_LOG_LEVEL", "WARNING"))

    project_path = Path(args.project_path).resolve()
    try:
        health = ensure(project_path)
    except DaemonUnavailable as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}))
        return 1
    print(json.dumps(health))
    return 0

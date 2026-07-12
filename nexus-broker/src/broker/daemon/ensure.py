"""`python -m broker.daemon ensure` — N31, plans/14 SS6 broker-side daemon
activation entry.

Idempotent: `ensure()` is a thin CLI-facing wrapper around `client.call`'s
existing spawn-on-demand (1.7) + stale-socket self-heal (1.8) paths — no new
daemon capability is added here. A healthy daemon answers on the first probe
inside `call()` and nothing is spawned; an unreachable or stale one is
spawned exactly once and then health-polled until reachable or the spawn-wait
budget expires. The daemon stays cache-only and non-authoritative: `ensure`
failing must never be treated by any caller as a hard requirement — see
`broker.daemon.fallback` for the fail-closed contract every real consumer
sits behind.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from broker.daemon.client import DaemonUnavailable, call

LOG = logging.getLogger("nexus-daemon.ensure")


def ensure(
    project_path: Path,
    *,
    connect_timeout: float | None = None,
    spawn_wait_s: float | None = None,
) -> dict[str, Any]:
    """Health-probe `project_path`'s daemon; spawn-on-demand if unreachable.

    Returns the `health` RPC payload (`status`, `pid`, `project_path`,
    `uptime_s`) on success. Raises `DaemonUnavailable` if the daemon never
    becomes reachable even after a spawn attempt — the caller decides what
    that means (the CLI below turns it into a nonzero exit; a future
    programmatic caller must not silently swallow it either).
    """
    return call(
        project_path,
        "health",
        spawn_if_missing=True,
        connect_timeout=connect_timeout,
        spawn_wait_s=spawn_wait_s,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m broker.daemon",
        description="Nexus daemon lifecycle CLI (N31, plans/14 SS6)",
    )
    parser.add_argument(
        "verb",
        choices=["ensure"],
        help="ensure: idempotent health-probe + spawn-on-demand; exit 0 healthy, nonzero otherwise",
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

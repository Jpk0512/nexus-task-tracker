#!/usr/bin/env python3
"""_heartbeat.py — Python-side invocation heartbeat (mirrors heartbeat-emitter.sh).

Importable by Python gate hooks. Writes to the EXACT SAME sink and schema as
the bash heartbeat-emitter.sh: one JSONL line per invocation (fire, block, or
allow) to .memory/files/hook_heartbeat.jsonl:

    {"ts":..,"hook":..,"event":..,"decision":..,"latency_ms":..}

BEST-EFFORT ONLY: emit_heartbeat() never raises. Any failure (missing dir,
disk full, permission error, etc.) is swallowed so telemetry can never change
a gate's exit code or stdout JSON.

DAEMON SHIM (Tranche 2, docs/archive/nexus-redesign/audits/daemon-hook-plan-2026-07-12.md
§C) — emit_heartbeat() first tries an `emit_heartbeat` RPC against the
resident daemon's Unix socket with a SHORT, env-tunable timeout
(`NEXUS_HEARTBEAT_DAEMON_TIMEOUT_S`, default 0.2s — tighter than the other
Tranche-2 shims because this fires on ~20 hooks' hot path) via the shared
`_daemon_rpc` module (same-directory dynamic import). ANY daemon miss/
timeout/error falls back INLINE to the exact JSONL append this module has
always done. A `NEXUS_HEARTBEAT_PATH` override (test isolation) SKIPS the
daemon hop entirely: the resident daemon only knows its own project_path,
never a per-call sink override, so honoring the override correctly means
never asking the daemon to write it. This module doubles as the CLI
heartbeat-emitter.sh (the bash sink used by non-Python hooks) now shells
out to, so this is the single home of the daemon-RPC-with-fallback logic —
bash never re-implements the socket protocol.
"""
# NOTE: live runtime is >=3.11 via the _py.sh resolver shim, but 3.9
# IMPORT-safety is retained because the package twin runs this file un-shimmed
# under ambient python3 (3.9) and test_hooks_py39_import.py enforces it — do
# NOT introduce 3.11-only idioms (datetime.UTC, def-time X | None, match/case).
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _repo_root() -> Path:
    """Resolve repo root by walking up from this file's location for .memory.

    Mirrors broker-gate.py:_repo_root — never depends on CWD, so a hook fired
    from an unexpected working directory still finds the real sink.
    """
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    return here.parent.parent.parent


def _default_heartbeat_path() -> Path:
    return _repo_root() / ".memory" / "files" / "hook_heartbeat.jsonl"


def _daemon_rpc_module():
    """Same-directory dynamic import — mirrors broker-gate.py's own load of
    this module. Kept lazy (called only when a daemon hop is attempted) so
    the common case (no override, daemon reachable) pays one extra file
    stat + exec, never an eager cross-module import at load time.
    """
    spec = importlib.util.spec_from_file_location(
        "_daemon_rpc", Path(__file__).resolve().parent / "_daemon_rpc.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_DAEMON_TIMEOUT_S = float(os.environ.get("NEXUS_HEARTBEAT_DAEMON_TIMEOUT_S", "0.2"))


def emit_heartbeat(hook: str, event: str, decision: str, latency_ms: int) -> None:
    """Record one heartbeat row. NEVER raises.

    Tries the daemon `emit_heartbeat` RPC first (module docstring) UNLESS
    `NEXUS_HEARTBEAT_PATH` overrides the sink (test isolation — the resident
    daemon cannot honor a per-call override for a project it did not start
    against). ANY daemon miss falls back INLINE to the exact JSONL append
    this module has always done. Any failure anywhere (missing directory,
    disk full, permission error, bad env override, etc.) is swallowed —
    telemetry must never change a gate's exit code or block/allow behavior.
    """
    try:
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # noqa: UP017
            "hook": hook,
            "event": event,
            "decision": decision,
            "latency_ms": int(latency_ms),
        }
    except Exception:
        return

    sink_path = os.environ.get("NEXUS_HEARTBEAT_PATH")
    if not sink_path:
        try:
            if _daemon_rpc_module().call(_repo_root(), "emit_heartbeat", row, _DAEMON_TIMEOUT_S) is not None:
                return
        except Exception:
            pass

    try:
        sink = Path(sink_path) if sink_path else _default_heartbeat_path()
        sink.parent.mkdir(parents=True, exist_ok=True)
        with open(sink, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass


def main(argv: list) -> int:
    """CLI entry — lets heartbeat-emitter.sh (the bash sink) delegate to this
    same daemon-RPC-with-fallback logic instead of re-implementing the
    socket protocol in bash. argv: hook event decision latency_ms.
    """
    if len(argv) < 4:
        return 0
    hook, event, decision = argv[0], argv[1], argv[2]
    try:
        latency_ms = int(argv[3])
    except Exception:
        latency_ms = 0
    emit_heartbeat(hook, event, decision, latency_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

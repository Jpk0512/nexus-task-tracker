#!/usr/bin/env python3
"""_heartbeat.py — Python-side invocation heartbeat (mirrors heartbeat-emitter.sh).

Importable by Python gate hooks. Writes to the EXACT SAME sink and schema as
the bash heartbeat-emitter.sh: one JSONL line per invocation (fire, block, or
allow) to .memory/files/hook_heartbeat.jsonl:

    {"ts":..,"hook":..,"event":..,"decision":..,"latency_ms":..}

BEST-EFFORT ONLY: emit_heartbeat() never raises. Any failure (missing dir,
disk full, permission error, etc.) is swallowed so telemetry can never change
a gate's exit code or stdout JSON.
"""
# NOTE: live runtime is >=3.11 via the _py.sh resolver shim, but 3.9
# IMPORT-safety is retained because the package twin runs this file un-shimmed
# under ambient python3 (3.9) and test_hooks_py39_import.py enforces it — do
# NOT introduce 3.11-only idioms (datetime.UTC, def-time X | None, match/case).
from __future__ import annotations

import json
import os
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


def emit_heartbeat(hook: str, event: str, decision: str, latency_ms: int) -> None:
    """Append one JSONL row to the heartbeat sink. NEVER raises.

    Any failure (missing directory, disk full, permission error, bad env
    override, etc.) is swallowed — telemetry must never change a gate's exit
    code or block/allow behavior.
    """
    try:
        sink_path = os.environ.get("NEXUS_HEARTBEAT_PATH")
        sink = Path(sink_path) if sink_path else _default_heartbeat_path()
        sink.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # noqa: UP017
            "hook": hook,
            "event": event,
            "decision": decision,
            "latency_ms": int(latency_ms),
        }
        with open(sink, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass

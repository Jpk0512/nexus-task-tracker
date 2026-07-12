#!/usr/bin/env python3
"""_gate_deny.py — OPT-030 canonical structured-denial emitters (shared).

Importable by Python gate hooks; also callable as a CLI so bash gate hooks
can invoke it without sys.path surgery.

CLI:  python3 _gate_deny.py <kind> <event> <code> <reason> [flags]
  kind = deny | advise
  deny flags:   --exit N   (default 2)   --no-stderr
  advise flags: --stderr
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _emit(obj: dict) -> None:
    # separators=(',',':') → compact, matches `jq -cn` output byte-for-byte.
    print(json.dumps(obj, separators=(",", ":")))


def _record_block(event: str, code: str, reason: str) -> None:
    """Append one JSONL row to the gate-block sink. BEST-EFFORT: any failure is swallowed."""
    try:
        sink_path = os.environ.get("NEXUS_GATE_BLOCKS_PATH")
        if sink_path is None:
            # Default: <repo-root>/.memory/files/gate_blocks.jsonl
            # repo-root = 3 parents up from this file's location
            repo_root = Path(__file__).resolve().parents[2]
            sink_path = str(repo_root / ".memory" / "files" / "gate_blocks.jsonl")
        sink = Path(sink_path)
        sink.parent.mkdir(parents=True, exist_ok=True)
        # Split HOOK/CODE from the full code token
        if "/" in code:
            hook, code_part = code.split("/", 1)
        else:
            hook, code_part = code, ""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "hook": hook,
            "code": code_part,
            "reason": reason[:200],
        }
        with open(sink, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass


def deny(
    event: str,
    code: str,
    reason: str,
    exit_code: int = 2,
    stderr: bool = True,
) -> int:
    """Emit canonical hard-deny JSON to stdout (and optionally stderr).

    Returns exit_code; caller does sys.exit(deny(...)).
    Default: exit 2 + stderr (the majority hard-deny case).
    socraticode variant: deny(..., exit_code=0, stderr=False).
    """
    full = f"[GATE:{code}] {reason}"
    _emit(
        {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": full,
            }
        }
    )
    if stderr:
        sys.stderr.write(full + "\n")
    _record_block(event, code, reason)
    return exit_code


def advise(
    event: str,
    code: str,
    msg: str,
    stderr: bool = False,
) -> int:
    """Emit canonical advisory JSON to stdout.

    Returns 0 always; caller keeps its own trailing exit 0 in place —
    do NOT let gate_advise exit or it skips post-advisory gate logic.
    Default: no stderr.
    broker-gate / worktree escape-hatch variant: advise(..., stderr=True).
    """
    full = f"[GATE:{code}] {msg}"
    _emit(
        {
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": full,
            }
        }
    )
    if stderr:
        sys.stderr.write(full + "\n")
    return 0


# ---------------------------------------------------------------------------
# CLI entrypoint — used by bash sites that cannot import directly.
# argv: <kind> <event> <code> <reason> [--exit N] [--no-stderr|--stderr]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 4:
        sys.stderr.write(
            "Usage: _gate_deny.py <deny|advise> <event> <code> <reason>"
            " [--exit N] [--no-stderr|--stderr]\n"
        )
        sys.exit(1)

    kind, event_arg, code_arg, reason_arg = args[0], args[1], args[2], args[3]
    rest = args[4:]

    if kind == "deny":
        _exit_code = 2
        _stderr = True
        i = 0
        while i < len(rest):
            if rest[i] == "--exit" and i + 1 < len(rest):
                _exit_code = int(rest[i + 1])
                i += 2
            elif rest[i] == "--no-stderr":
                _stderr = False
                i += 1
            else:
                i += 1
        sys.exit(deny(event_arg, code_arg, reason_arg, exit_code=_exit_code, stderr=_stderr))

    elif kind == "advise":
        _stderr = False
        i = 0
        while i < len(rest):
            if rest[i] == "--stderr":
                _stderr = True
                i += 1
            else:
                i += 1
        sys.exit(advise(event_arg, code_arg, reason_arg, stderr=_stderr))

    else:
        sys.stderr.write(f"kind must be 'deny' or 'advise', got: {kind!r}\n")
        sys.exit(1)

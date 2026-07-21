#!/usr/bin/env python3
"""_gate_deny.py — OPT-030 canonical structured-denial emitters (shared).

Importable by Python gate hooks; also callable as a CLI so bash gate hooks
can invoke it without sys.path surgery.

CLI:  python3 _gate_deny.py <kind> <event> <code> <reason> [flags]
  kind = deny | advise
  deny flags:   --exit N   (default 2)   --no-stderr
  advise flags: --stderr

TASK-094 LEG B — gate-span emission (observability-taxonomy.json gate_fire
level; spans.py's `validate_gate_attributes`, authored by LEG A, "schema +
writer support; live hook-side emission is LEG B's"). `deny()`/`advise()`
gain an OPTIONAL keyword-only `span_attrs` dict; when a caller supplies one
(and it resolves a `trace_id`), a best-effort `gate`-kind span is emitted via
the daemon's `span.emit` RPC alongside the existing stdout/stderr/exit-code
behavior. `span_attrs` is None by default on every pre-existing call site in
this repo, so `emit_gate_span` below is a complete no-op unless a caller
opts in explicitly — zero behavior change, zero new latency, for any call
site that does not pass it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    """Mirrors `_record_block`'s own root derivation (3 parents up from this
    file's location: .claude/hooks/_gate_deny.py -> repo root), honoring the
    same `_HOOK_REPO_ROOT` test-isolation override every other hook in this
    repo uses."""
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def _load_daemon_rpc():
    spec = importlib.util.spec_from_file_location("_daemon_rpc", Path(__file__).parent / "_daemon_rpc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# TASK-094 LEG B — extra gate_fire-level attributes a caller may pass through
# `span_attrs` (spans.py's `_GATE_*_ATTRS` schema, minus the free-form
# gate_name/event/verdict/reason this module already derives itself).
_GATE_SPAN_ATTR_KEYS: tuple[str, ...] = (
    "lens_verdict", "lens_tier", "revise_reasons", "rpc_miss", "rpc_latency_ms",
)
# First-class span keys (spans.py's `_FIRST_CLASS_KEY_COLUMNS`) — apply to
# every span kind, gate included.
_GATE_SPAN_TOP_LEVEL_KEYS: tuple[str, ...] = ("task_id", "workflow_id", "phase_id")


def emit_gate_span(event: str, code: str, verdict: str, reason: str, span_attrs: dict[str, Any] | None = None) -> None:
    """Best-effort `gate`-kind span emission — the hook-level half of LEG A's
    gate_fire attribute schema. NO-OP (no RPC attempt at all, ever) when
    `span_attrs` is falsy or carries no resolvable `trace_id`/`session_id` —
    a gate span's `trace_id` is REQUIRED (`spans.validate_span`) and there is
    no meaningful value to fabricate one from, so an uninstrumented call site
    costs nothing.

    Callable directly (bypassing deny()/advise() entirely) by a hook that
    wants pure telemetry with NO stdout/stderr side effect — e.g. lens-gate.sh
    capturing REVISE reasons on an otherwise-silent pass.

    `verdict` drives the span's `status`: "deny" -> ERROR, everything else
    (e.g. "advise", "PASS") -> OK — mirrors spans.py's VALID_STATUSES set.

    ANY failure (daemon down, malformed reply, import error) is swallowed —
    this must never affect a gate's own allow/deny decision, its exit code,
    or its stdout/stderr.
    """
    if not span_attrs:
        return
    trace_id = span_attrs.get("trace_id") or span_attrs.get("session_id")
    if not trace_id:
        return
    try:
        hook = code.split("/", 1)[0] if "/" in code else code
        attributes: dict[str, Any] = {
            "gate_name": hook,
            "event": event,
            "verdict": verdict,
            "reason": str(reason)[:500],
        }
        for key in _GATE_SPAN_ATTR_KEYS:
            value = span_attrs.get(key)
            if value is not None:
                attributes[key] = value
        span: dict[str, Any] = {
            "trace_id": str(trace_id),
            "span_id": f"gate-{uuid.uuid4().hex}",
            "name": f"gate:{hook}",
            "kind": "gate",
            "status": "ERROR" if verdict == "deny" else "OK",
            "attributes": attributes,
        }
        for key in _GATE_SPAN_TOP_LEVEL_KEYS:
            value = span_attrs.get(key)
            if value:
                span[key] = str(value)
        timeout = float(os.environ.get("NEXUS_GATE_SPAN_TIMEOUT_S", "0.2"))
        _load_daemon_rpc().call(_repo_root(), "span.emit", {"span": span}, timeout)
    except Exception:
        pass


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
            "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
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
    *,
    span_attrs: dict[str, Any] | None = None,
) -> int:
    """Emit canonical hard-deny JSON to stdout (and optionally stderr).

    Returns exit_code; caller does sys.exit(deny(...)).
    Default: exit 2 + stderr (the majority hard-deny case).
    socraticode variant: deny(..., exit_code=0, stderr=False).

    TASK-094 LEG B: `span_attrs` (keyword-only, default None) opts a caller
    into a best-effort `gate`-kind span emission carrying the deny reason —
    see `emit_gate_span`'s docstring. A caller that omits it (every
    pre-existing call site) is unaffected.
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
    emit_gate_span(event, code, "deny", reason, span_attrs)
    return exit_code


def advise(
    event: str,
    code: str,
    msg: str,
    stderr: bool = False,
    *,
    span_attrs: dict[str, Any] | None = None,
) -> int:
    """Emit canonical advisory JSON to stdout.

    Returns 0 always; caller keeps its own trailing exit 0 in place —
    do NOT let gate_advise exit or it skips post-advisory gate logic.
    Default: no stderr.
    broker-gate / worktree escape-hatch variant: advise(..., stderr=True).

    TASK-094 LEG B: `span_attrs` (keyword-only, default None) — see
    `deny()`'s matching note above.
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
    emit_gate_span(event, code, "advise", msg, span_attrs)
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

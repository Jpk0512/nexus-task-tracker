#!/usr/bin/env python3
"""Shared unix-socket JSON-RPC client for hook -> daemon shims (Tranche 2,
docs/archive/nexus-redesign/audits/daemon-hook-plan-2026-07-12.md §C).

Not itself a registered hook. Import via the same same-directory dynamic-
import convention `_heartbeat.py`'s own callers already use (see
broker-gate.py: `importlib.util.spec_from_file_location`) — hooks run under
ambient python3 without the nexus-broker venv, so this module MUST NOT
import the `broker` package. The socket-path derivation is hand-inlined
(mirrors `broker.daemon.paths.socket_path_for`, copied first in
skill-load-capture.py's Tranche-1 shim) — keep the two derivations in sync
by hand if `paths.py` ever changes its formula.

UNIFORM SHIM CONTRACT every caller relies on: `call()` returns the RPC
`result` dict on a confirmed daemon accept, or `None` on ANY miss (no socket
file, connection refused, timeout, malformed reply, an "error" key in the
response). Callers MUST treat `None` as "fall back to today's inline path" —
never as an error to raise or surface. Timeouts are SHORT by design
(caller-supplied, no built-in default) because every current caller fires
on an interactive PostToolUse/SubagentStop/PreToolUse path, never a
background job — an absent or slow daemon must never be felt as latency.

F2-02 TRANCHE-AWARE WRAPPERS (event-bus-design.md §2a/§3, notepad gotcha
#327) — `call_advisory()` / `call_deny_capable()` below wrap `call()` with
the event-bus's fail policy, for the future shared ping shims F2-03/F2-04
migrate hook bodies into. `call()` itself is UNCHANGED (still `None` on any
miss) so every existing caller's "fall back to today's inline path" contract
keeps working untouched. The two wrappers are STRUCTURALLY DISTINCT on a
miss — `call_advisory` always returns an `ok`-keyed dict (fail OPEN: a dead
daemon never bricks a session for an advisory event), `call_deny_capable`
always returns a `decision`-keyed dict defaulting to `"deny"` (fail CLOSED:
a dead daemon must never silently allow a deny-capable gate) — so a caller
can never mistake one policy's miss-shape for the other's.

3.9 IMPORT-SAFETY — live runtime is >=3.11 via `_py.sh`, but the package
twin runs this file un-shimmed under ambient python3 (3.9). No 3.11-only
idioms: no `datetime.UTC`, no def-time `X | None`, no `match`/`case`
(`from __future__ import annotations` keeps PEP-604 annotations
def-time-safe).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path
from typing import Any


def socket_path(root: Path) -> Path:
    override = os.environ.get("NEXUS_DAEMON_SOCKET_DIR")
    socket_dir = Path(override) if override else Path.home() / ".nexus" / "daemon"
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return socket_dir / f"{digest}.sock"


# TASK-094 LEG B — "daemon RPC-miss event emission from hook shims"
# (observability-taxonomy.json gate_fire.rpc_miss: "the single most important
# full-fidelity gap for daemon-health observability"). `call()` is the ONE
# funnel every hook shim in this repo already uses to reach the daemon
# (broker-gate.py, lens-gate.sh, _verify_shadow.py, completion-capture.py,
# dispatch-capture.py, ...) — recording every miss HERE, as a plain local
# file append, gives universal daemon-health observability with zero
# per-caller wiring, and (unlike a span.emit RPC) works even when the miss
# itself IS the daemon being unreachable: this is a local write, never a
# second network round-trip that could itself miss.
_MISS_SINK_RELATIVE = Path(".memory") / "files" / "daemon_rpc_misses.jsonl"

# TASK-105 rotation: one dark evening produced 13k+ no-socket rows; the sink
# must stay bounded without losing the recent window health.py's
# daemon.rpc_misses check reads (last 10 minutes).
_MISS_ROTATE_MAX_LINES = 5000
_MISS_ROTATE_KEEP_LINES = 1000


def _miss_sink_path(root: Path) -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override) / "daemon_rpc_misses.jsonl"
    return root / _MISS_SINK_RELATIVE


def _rotate_miss_sink(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= _MISS_ROTATE_MAX_LINES:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines[-_MISS_ROTATE_KEEP_LINES:]) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def record_rpc_miss(root: Path, method: str, reason: str) -> None:
    """Best-effort local append — one JSONL row per daemon RPC miss.

    Never raises; a failure to even record a miss must never surface past
    this function (mirrors every other JSONL-sink writer in this repo, e.g.
    _gate_deny.py's `_record_block`). `root`-relative so a caller under test
    isolation (an isolated tmp `root`) never touches the real repo's sink.
    """
    try:
        path = _miss_sink_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "method": method,
            "reason": reason,
        }
        with path.open("a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        _rotate_miss_sink(path)
    except Exception:
        pass


def call(root: Path, method: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any] | None:
    """One best-effort RPC round-trip. Returns None on ANY miss; never raises.

    Cheapest possible miss check first (socket file existence) before
    touching the network stack at all — the common "daemon not running"
    case never even opens a socket.

    Every miss (no socket file, connect/send/recv exception, empty response,
    malformed/error response) is durably recorded via `record_rpc_miss`
    before returning None — see that function's docstring.
    """
    sock_path = socket_path(root)
    if not sock_path.exists():
        record_rpc_miss(root, method, "no-socket")
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect(str(sock_path))
        request = {"id": 1, "method": method, "params": params}
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        if not buf:
            record_rpc_miss(root, method, "empty-response")
            return None
        response = json.loads(buf.decode("utf-8"))
        if not isinstance(response, dict) or "error" in response:
            record_rpc_miss(root, method, "error-response")
            return None
        result = response.get("result")
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        record_rpc_miss(root, method, f"exception:{type(exc).__name__}")
        return None
    finally:
        with contextlib.suppress(Exception):
            sock.close()


def call_advisory(root: Path, method: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Tranche-A wrapper (event.emit and friends): fail OPEN on any RPC miss.

    Worst case on a dead/unreachable daemon is a lost banner — this ALWAYS
    returns a dict (never `None`), so a caller never needs its own
    miss-handling branch to get the fail-open behaviour right.
    """
    result = call(root, method, params, timeout_s)
    if result is None:
        return {"ok": True, "fail_open": True, "reason": "daemon-miss"}
    return result


def call_deny_capable(root: Path, method: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """Tranche-B wrapper (event.verify and friends): fail CLOSED on any RPC miss.

    A deny-capable gate must NEVER silently allow because the daemon is
    unreachable (C-06; notepad gotcha #327) — this ALWAYS returns a
    `decision` key, `"deny"` on any miss, structurally distinct from
    `call_advisory`'s `ok`-keyed miss shape so the two policies can never be
    confused for one another.
    """
    result = call(root, method, params, timeout_s)
    if result is None:
        return {"decision": "deny", "fail_closed": True, "reason": "daemon-miss"}
    return result

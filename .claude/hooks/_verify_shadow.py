#!/usr/bin/env python3
"""Tranche-B (deny-capable) SHADOW comparison shim — F2-04
(nexus-foundation/plans/artifacts/event-bus-design.md §1a/§2a/§3,
wave-2.md §(d), notepad F2-04 #327/#330).

SHADOW ONLY (C-06): every one of the 14 tranche-B hook bodies keeps its own
existing logic as the SOLE authoritative decision-maker. This module never
changes a hook's exit code, stdout, or stderr — it only OBSERVES the verdict
the hook body already computed, fires a best-effort `event.verify` RPC
against the daemon-resident port of that same consumer
(`broker.daemon.deny_handlers`), and appends a
`{ts, event, consumer, hook_verdict, daemon_verdict, divergent}` comparison
row to the shadow log (`.memory/files/gate_verdict_shadow.jsonl` by default)
for `hook_parity.sh --tranche B --assert-zero-divergence` to later assert
over (nexus-foundation/tools/hook_parity_check.py).

SHADOW-ONLY (notepad #327 — "the deny-capable shim's miss-policy must be
distinct from the advisory shim's"):

  shadow_verify() / install_shadow_wiring() / capture_stdin() /
  capture_verdicts() — Best-effort; NEVER raises past its own boundary,
  NEVER blocks past its bounded per-consumer timeout, NEVER exits the
  caller process, NEVER influences the caller's own decision. This is the
  ONLY family of entry points any of the 14 tranche-B hook bodies call.

  (DEC-104: the NEX-004 pilot's deny-capable `enforce_fail_closed()` entry
  point — the daemon-resident enforcement OFFLOAD broker-gate.py's now-
  retired `NEXUS_BROKER_GATE_DAEMON_MODE=1` thin client armed — was removed
  as a measured latency regression. The cold in-process gate path is once
  again the sole, unconditional enforcement authority; this module is
  SHADOW/telemetry-only again.)

DETACHED / BACKGROUNDED BY DESIGN: every caller (both the bash
`nexus_shadow_verify_ping` helper in gate-lib.sh and this module's own
`install_shadow_wiring` atexit hook for the python-shebang hook bodies)
spawns the actual RPC-and-log work as a DETACHED background process (this
file's own `ping` CLI subcommand) rather than doing it in the caller's own
process — a dead/slow daemon must NEVER add latency to a gate's own exit
path (F2-04 brief constraint). The RPC itself still carries a bounded
per-consumer timeout (F2-03 dca7d85 override pattern) as a second,
independent safety net for the detached child.

3.9 IMPORT-SAFETY — this file is invoked directly by `.claude/hooks/*.sh`
python-shebang bodies (which run under ambient python3, no venv) AND is the
target of a background `python3 _verify_shadow.py ping ...` spawn from the
bash tranche-B bodies. No 3.11-only idioms: no `datetime.UTC`, no def-time
`X | None`, no `match`/`case` (`from __future__ import annotations` keeps
PEP-604 annotations def-time-safe).
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent

# event.verify's own design budget (event-bus-design.md §2a: "Timeout ~200ms
# (must allow verdict compute yet stay sub-latency-budget)") — used here as
# the DETACHED child's own RPC bound, never felt by the calling gate since
# the ping is backgrounded before this budget is ever spent.
_DEFAULT_TIMEOUT_S = float(os.environ.get("NEXUS_VERIFY_SHADOW_TIMEOUT_S", "0.2"))

# Per-consumer overrides (F2-03 dca7d85 pattern) — none needed yet: every
# tranche-B daemon handler is in-memory/on-disk-read compute except
# plan-validation-gate's scorer subprocess (up to 30s inside the daemon
# handler itself, deny_handlers.py) — that handler is simply allowed to be
# SLOW from this shim's point of view: the client-side socket timeout below
# still bounds OUR wait to _DEFAULT_TIMEOUT_S regardless, so a row is just
# not produced on the common case, never a hang (best-effort, by design).
_CONSUMER_TIMEOUT_DEFAULT_S: dict = {}

_FORWARD_ENV_PREFIXES = ("_HOOK_", "NEXUS_", "LM_STUDIO_")
_FORWARD_ENV_EXACT = ("REPO_ROOT",)


def _timeout_for(consumer: str) -> float:
    baked_default = _CONSUMER_TIMEOUT_DEFAULT_S.get(consumer)
    if baked_default is None:
        return _DEFAULT_TIMEOUT_S
    env_key = "NEXUS_VERIFY_SHADOW_TIMEOUT_S__" + consumer.upper().replace("-", "_")
    return float(os.environ.get(env_key, str(baked_default)))


def _repo_root() -> Path:
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _load_daemon_rpc():
    spec = importlib.util.spec_from_file_location("_daemon_rpc", HOOKS_DIR / "_daemon_rpc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _forwarded_env() -> dict:
    out = {}
    for key, val in os.environ.items():
        if key in _FORWARD_ENV_EXACT or key.startswith(_FORWARD_ENV_PREFIXES):
            out[key] = val
    return out


def _shadow_log_path() -> Path:
    override = os.environ.get("NEXUS_GATE_VERDICT_SHADOW_PATH")
    if override:
        return Path(override)
    return _repo_root() / ".memory" / "files" / "gate_verdict_shadow.jsonl"


def _append_row(row: dict) -> None:
    try:
        path = _shadow_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass  # best-effort telemetry — must never surface to any caller


def shadow_verify(
    event_name: str,
    consumer: str,
    payload: dict,
    decision: str,
    code: str = "",
    reason: str = "",
    timeout_s: float = None,
) -> None:
    """IN-PROCESS best-effort tranche-B shadow comparison ping. Fires
    `event.verify`, logs a comparison row, and returns — never raises,
    never blocks past its bounded timeout, never influences `decision`
    (already computed by the caller; the hook body's own verdict stays
    authoritative, C-06). Callers that care about zero added gate latency
    should prefer the detached `ping` CLI subcommand below instead of
    calling this directly in-process.
    """
    try:
        rpc = _load_daemon_rpc()
        t = timeout_s if timeout_s is not None else _timeout_for(consumer)
        daemon_result = rpc.call_deny_capable(
            _repo_root(),
            "event.verify",
            {"name": event_name, "consumer": consumer, "payload": payload, "env": _forwarded_env()},
            t,
        )
        if not isinstance(daemon_result, dict):
            daemon_result = {}
        daemon_decision = daemon_result.get("decision", "deny")
        fail_closed = bool(daemon_result.get("fail_closed"))
        daemon_verdict = {
            "decision": daemon_decision,
            "code": daemon_result.get("code", ""),
            "reason": daemon_result.get("reason", ""),
            "fail_closed": fail_closed,
        }
        hook_verdict = {"decision": decision, "code": code, "reason": reason}
        # A daemon-miss (fail_closed marker) is a known infrastructure gap,
        # not a genuine LOGIC divergence between the ported handler and the
        # hook body — C-06's "zero UNEXPLAINED divergence" excludes it.
        divergent = (not fail_closed) and (daemon_decision != decision)
        _append_row({
            "ts": time.time(),
            "event": event_name,
            "consumer": consumer,
            "hook_verdict": hook_verdict,
            "daemon_verdict": daemon_verdict,
            "divergent": divergent,
        })
    except Exception:
        pass  # best-effort — a shadow-ping bug must never surface to the gate


# ---------------------------------------------------------------------------
# Python-shebang hook-body wiring — captures the raw stdin payload and the
# hook's own final verdict (via a deny()/advise() wrapper), then fires a
# DETACHED background ping at process exit. Safe to call `capture_stdin`
# and `capture_verdicts` in either order, and more than once (idempotent).
# ---------------------------------------------------------------------------

_state = {
    "event": "",
    "consumer": "",
    "decision": "allow",
    "code": "",
    "reason": "",
    "raw_stdin": "",
    "_stdin_patched": False,
    "_atexit_registered": False,
}


def _spawn_detached_ping() -> None:
    try:
        event_name = _state.get("event", "")
        consumer = _state.get("consumer", "")
        if not event_name or not consumer:
            return
        proc = subprocess.Popen(
            [
                sys.executable, str(HOOKS_DIR / "_verify_shadow.py"),
                "ping", event_name, consumer, _state.get("decision", "allow"), _state.get("code", ""),
            ],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            proc.stdin.write((_state.get("raw_stdin") or "").encode("utf-8"))
            proc.stdin.close()
        except Exception:
            pass
    except Exception:
        pass  # best-effort — must never surface to the caller at interpreter shutdown


def _ensure_atexit_registered() -> None:
    if not _state["_atexit_registered"]:
        _state["_atexit_registered"] = True
        atexit.register(_spawn_detached_ping)


def capture_stdin(event_name: str = "", consumer: str = "") -> None:
    """Call EARLY (before the hook body's own first stdin read) to snapshot
    the raw payload text for the eventual detached ping. Idempotent — a
    second call is a no-op for the patch itself, but still records
    event/consumer if not already set (supports the split call order
    plan-validation-gate.py needs: stdin is read before `_gate_deny_mod`
    is loaded there)."""
    if event_name:
        _state["event"] = event_name
    if consumer:
        _state["consumer"] = consumer
    _ensure_atexit_registered()
    if _state["_stdin_patched"]:
        return
    _state["_stdin_patched"] = True
    orig_read = sys.stdin.read

    def _capture_read(*a, **kw):
        text = orig_read(*a, **kw)
        _state["raw_stdin"] = text
        return text

    sys.stdin.read = _capture_read


def capture_verdicts(gate_deny_mod, event_name: str = "", consumer: str = "") -> None:
    """Wrap `gate_deny_mod.deny` / `.advise` so this module observes the
    hook body's own final verdict without changing what either function
    returns or does. Every one of the 14 tranche-B hook bodies calls both
    functions only via `<module>.deny(...)` / `<module>.advise(...)`
    attribute access (never a bound local name), so patching the
    attributes on the already-loaded module object affects every call
    site."""
    if event_name:
        _state["event"] = event_name
    if consumer:
        _state["consumer"] = consumer
    _ensure_atexit_registered()
    if gate_deny_mod is None:
        return

    orig_deny = gate_deny_mod.deny

    def _capture_deny(event, code, reason, *a, **kw):
        _state["decision"], _state["code"], _state["reason"] = "deny", code, reason
        return orig_deny(event, code, reason, *a, **kw)

    gate_deny_mod.deny = _capture_deny

    orig_advise = gate_deny_mod.advise

    def _capture_advise(event, code, msg, *a, **kw):
        # advise() is always an ALLOW-with-context path for these gates
        # (never a deny) — matches deny_handlers.py's ported semantics.
        if _state["decision"] != "deny":
            _state["decision"], _state["code"], _state["reason"] = "allow", code, msg
        return orig_advise(event, code, msg, *a, **kw)

    gate_deny_mod.advise = _capture_advise


def install_shadow_wiring(gate_deny_mod, event_name: str, consumer: str) -> None:
    """Convenience one-call installer for the 5 of 6 python-shebang hook
    bodies whose `_gate_deny_mod` load happens BEFORE their own stdin
    read — combines `capture_stdin` + `capture_verdicts`. `plan-validation-
    gate.py` (whose stdin read precedes its `_gate_deny_mod` load) calls
    the two halves separately instead — see that file."""
    capture_stdin(event_name, consumer)
    capture_verdicts(gate_deny_mod, event_name, consumer)


# ---------------------------------------------------------------------------
# CLI — the detached-ping entry point every caller (bash and python) spawns.
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "ping":
        return
    if len(sys.argv) < 5:
        return
    event_name, consumer, decision = sys.argv[2], sys.argv[3], sys.argv[4]
    code = sys.argv[5] if len(sys.argv) > 5 else ""
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    shadow_verify(event_name, consumer, payload, decision, code)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass

"""
lm-studio-triage-gate.py — R3-T11 (node N18): LM Studio local-classifier triage gate.

Wires the LM Studio local classifier (see router_core.py's call_router_model) as a
TRIAGE step ahead of any API-model reviewer dispatch:

  - "trivial"           -> route to the deterministic-only path (no reviewer, mirrors
                            model_tier.NO_REVIEWER / risk_tier T0 — nothing to re-derive).
  - "standard"/"risky"  -> route to N17's tier path (LENS_FAST for T1-weight changes,
                            LENS_FULL_OPUS/LENS_FULL_SONNET for T2/irreversible — the
                            existing model_tier.REVIEWER_TABLE machinery decides which).

FAIL-OPEN + advisory (C2 speed mandate): if LM Studio is not reachable, this gate must
NEVER block or slow the review path. On any connect/timeout failure it NO-OPS straight
to N17's tier routing — a hard dependency on a desktop app would be new fragility, the
exact opposite of the speed goal this gate exists to serve. The connect timeout is
capped short (default 0.1s = the acceptance-criteria 100ms cap) specifically so a
down/slow LM Studio costs at most that much wall-clock before falling through.

Every classification (or fail-open no-op) is appended to
.memory/files/triage_decisions.jsonl for R5-T06 calibration — best-effort, never
raises (a telemetry write failure must not affect routing).

.claude/hooks/*.py execute under the SYSTEM python3 (3.9.6 here), NOT uv/3.12. Keep
this module 3.9-import-safe: no `datetime.UTC`, no def-time `X | None`, no
`match`/`case` (PEP-563 future import keeps annotations lazy).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone  # noqa: UP017 -- py3.9-safe; no datetime.UTC
from pathlib import Path
from typing import Any

HOOKS_DIR = Path(__file__).resolve().parent

# Route names — mirror model_tier.py's reviewer-tier vocabulary (N17/N16 owned; this
# module only ever READS those names, never redefines the tiering policy itself).
ROUTE_DETERMINISTIC_ONLY = "deterministic-only"  # T0 — no reviewer, nothing to re-derive
ROUTE_N17_TIER = "n17-tier-routing"  # standard/risky -> model_tier.REVIEWER_TABLE decides

# Classifier verdict -> route decision.
_TRIVIAL_VERDICTS = frozenset({"trivial"})
_ESCALATE_VERDICTS = frozenset({"standard", "risky", "simple", "complex"})

# Connect/read timeout for the triage HTTP call. Acceptance criterion #2 caps the
# fail-open cost at <=100ms beyond a down/slow endpoint; 0.1s is that cap exactly.
TRIAGE_TIMEOUT_S = float(os.environ.get("_HOOK_TRIAGE_TIMEOUT", "0.1"))

TRIAGE_URL = os.environ.get("_HOOK_ROUTER_URL") or os.environ.get(
    "_HOOK_QWEN_URL", "http://127.0.0.1:1234/v1/chat/completions"
)
TRIAGE_MODEL = os.environ.get("_HOOK_ROUTER_MODEL", "granite-4.1-3b")

# Benign "LM Studio down/slow" failures — fail-open silently, exactly router_core.py's
# _is_benign_call_error posture (this gate deliberately mirrors that classification,
# not a new one, so the two triage points behave identically under the same outage).
_BENIGN_ERRORS = (
    ConnectionError,
    TimeoutError,
    socket.timeout,
    urllib.error.URLError,
)


def _is_benign(exc: BaseException) -> bool:
    if isinstance(exc, _BENIGN_ERRORS):
        if isinstance(exc, urllib.error.URLError) and not isinstance(
            exc, _BENIGN_ERRORS[:-1]
        ):
            reason = getattr(exc, "reason", None)
            return isinstance(reason, (ConnectionError, TimeoutError, socket.timeout, OSError))
        return True
    return False


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def classify_change(summary: str) -> dict[str, Any] | None:
    """Call the LM Studio local classifier for a triage verdict.

    Returns {"verdict": "trivial"|"standard"|"risky"|..., "raw": <parsed body>} on
    success, or None on ANY failure (benign outage or otherwise) — the caller always
    falls through to N17 tier routing on None, never raises.

    Test hook: _MOCK_TRIAGE_RESPONSE env var short-circuits the HTTP call with a JSON
    string, e.g. '{"verdict": "trivial"}' or '{"verdict": "risky"}'.
    _MOCK_TRIAGE_CONNECT_ERROR (if set) simulates a down endpoint.
    """
    if os.environ.get("_MOCK_TRIAGE_CONNECT_ERROR"):
        return None

    mock = os.environ.get("_MOCK_TRIAGE_RESPONSE")
    if mock is not None:
        try:
            return json.loads(mock)
        except json.JSONDecodeError:
            return None

    payload = json.dumps(
        {
            "model": TRIAGE_MODEL,
            "temperature": 0.0,
            "max_tokens": 32,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify the following code change as exactly one of: "
                        "trivial, standard, risky. Reply with JSON: "
                        '{"verdict": "<one of those>"}'
                    ),
                },
                {"role": "user", "content": summary},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage_classification",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "required": ["verdict"],
                        "properties": {
                            "verdict": {
                                "type": "string",
                                "enum": ["trivial", "standard", "risky"],
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
        }
    ).encode()

    req = urllib.request.Request(
        TRIAGE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TRIAGE_TIMEOUT_S) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:
        if not _is_benign(exc):
            print(f"[triage] degraded: {type(exc).__name__}", file=sys.stderr)
        return None


def triage_route(summary: str) -> dict[str, Any]:
    """Return the triage decision for a change summary.

    {"route": ROUTE_DETERMINISTIC_ONLY | ROUTE_N17_TIER,
     "verdict": <classifier verdict string> | None,
     "fail_open": bool,
     "elapsed_s": float}

    fail_open=True means the endpoint was unreachable/malformed and the route fell
    through to N17 tier routing WITHOUT ever getting a verdict — this is the
    advisory no-op path, never a block.
    """
    start = time.monotonic()
    result = classify_change(summary)
    elapsed = time.monotonic() - start

    if result is None:
        decision = {
            "route": ROUTE_N17_TIER,
            "verdict": None,
            "fail_open": True,
            "elapsed_s": elapsed,
        }
    else:
        verdict = result.get("verdict")
        route = ROUTE_DETERMINISTIC_ONLY if verdict in _TRIVIAL_VERDICTS else ROUTE_N17_TIER
        decision = {
            "route": route,
            "verdict": verdict,
            "fail_open": False,
            "elapsed_s": elapsed,
        }

    _log_telemetry(summary, decision)
    return decision


def _log_telemetry(summary: str, decision: dict[str, Any]) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 -- py3.9-safe import
        "summary": summary[:200],
        "verdict": decision["verdict"],
        "route": decision["route"],
        "fail_open": decision["fail_open"],
        "elapsed_s": decision["elapsed_s"],
        "model": TRIAGE_MODEL,
    }
    _append_jsonl(_files_dir() / "triage_decisions.jsonl", record)


def main() -> int:
    """Advisory PreToolUse-style entrypoint: read a change summary from stdin JSON
    (`{"summary": "..."}`), print the triage decision as additionalContext, exit 0
    always (this gate never blocks — fail-open extends to its own crash path)."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        summary = data.get("summary", "")
    except Exception:
        summary = ""

    try:
        decision = triage_route(summary)
    except Exception:
        decision = {"route": ROUTE_N17_TIER, "verdict": None, "fail_open": True, "elapsed_s": 0.0}

    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": f"[triage] route={decision['route']} verdict={decision['verdict']}",
        }
    }
    with contextlib_suppress():
        sys.stdout.write(json.dumps(payload))
    return 0


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(Exception)


if __name__ == "__main__":
    sys.exit(main())

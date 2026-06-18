#!/usr/bin/env python3
"""UserPromptSubmit hook — single-stage router-model classifier (Phase E1).

LIVE mode (2026-05-14): all 15 personas in `live` array; pre-fills emitted as
HINTS for Nexus. Three safety nets catch misroutes (HINT semantics, persona-
alias-resolver, lens-gate).

Data capture: every router invocation appends a v2 record (FULL prompt +
session_id + the exact model input + the model's guess + provenance) to
.memory/files/router_decisions.jsonl. The labeler/harvester is `python -m
broker.router_train` (PLANNED — not yet built); it will extract fine-tune-ready
(prompt, correct_persona) pairs by joining the dispatch sidecar (PRIMARY
ground-truth) with a transcript-mining fallback. Rows captured during the
blank-persona-bug era are stamped router_version="buggy" and excluded from
training; rows from the OPT-001 fix onward are stamped "fixed" and retained.
"""

# .claude/hooks/*.py execute under the SYSTEM python3 (3.9.6 here), NOT uv/3.12.
# PEP-563 lazy annotations keep PEP-604 'X | None' unions from being evaluated at
# def-time (3.10+). datetime.UTC / 'from datetime import UTC' is 3.11+ only — use
# timezone.utc. Both keep this hook importing clean under 3.9.
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Threshold 0.85 → 0.70 on 2026-05-14: the router model clusters confidence in the
# 70-85 band; 0.85 left all useful signals on the floor. Override: _HOOK_THRESHOLD_LLM=0.75
THRESHOLD_LLM = float(os.environ.get("_HOOK_THRESHOLD_LLM", "0.70"))
HOOKS_DIR = Path(__file__).parent

# Capture schema/provenance (T0). schema_version=2 separates these provenance-
# stamped rows from the legacy v1 (the 604 unlabeled rows). router_version is the
# OPT-001 quarantine flag: "buggy" until the blank-persona prompt bug was fixed,
# then "fixed" from that commit onward. router_core._read_persona_descriptions now
# renders every dispatchable persona with a meaningful ownership-boundary blurb
# (the bug rendered the 6 implementers as bare names), so captures from here are
# training-grade. Any harvester drops router_version=="buggy" from training;
# "fixed" rows are retained.
SCHEMA_VERSION = 2
ROUTER_VERSION = "fixed"


def _router_code_sha() -> str:
    """Git sha of router code at write time, best-effort ('' if unavailable)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=HOOKS_DIR,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


def _shadow_personas() -> set[str]:
    # Env var takes precedence over JSON file (enables testing without touching disk)
    env_val = os.environ.get("_HOOK_SHADOW_PERSONAS")
    if env_val is not None:
        return {p.strip() for p in env_val.split(",") if p.strip()}

    shadow_path = _files_dir() / "router_shadow_personas.json"
    try:
        data = json.loads(shadow_path.read_text())
        return set(data.get("shadow", []))
    except Exception:
        # No shadow file or unreadable → all personas live (fail-open)
        return set()


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _validate_record(rec: dict) -> tuple[bool, str]:
    """Write-time integrity guard for a v2 capture record.

    Returns (ok, reason). On failure the caller STILL appends the row (fail-soft —
    never block the user) and additionally appends a capture_invalid heartbeat so a
    broken capture announces itself at write time. This is the guard that would
    have surfaced the 5 prior phantom-pipeline failures.

    A decision:"error" row legitimately carries no model input (router_core never
    returned), so messages/system_prompt_sha256 are not asserted for it.
    """
    if rec.get("schema_version") != SCHEMA_VERSION:
        return False, f"schema_version != {SCHEMA_VERSION}"
    if not rec.get("router_version"):
        return False, "missing router_version"
    if not rec.get("model_id"):
        return False, "missing model_id"
    if not rec.get("session_id") or rec.get("session_id") == "unknown":
        return False, "session_id missing or 'unknown'"
    if rec.get("decision") != "error":
        messages = rec.get("messages")
        if not messages:
            return False, "empty messages"
        if not any(m.get("role") == "system" and m.get("content") for m in messages):
            return False, "no non-empty system message"
        if not rec.get("system_prompt_sha256"):
            return False, "missing system_prompt_sha256"
    return True, ""


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        user_msg = payload.get("prompt", "")
        # session_id enables joining router decisions against Arize spans +
        # validation_log rows for fine-tune training-data harvesting.
        session_id = payload.get("session_id") or payload.get("sessionId") or "unknown"
    except Exception:
        sys.exit(0)

    start_ns = time.monotonic_ns()

    # router_core import / call failures (module missing, syntax error, or an
    # unexpected error escaping call_router_model) must be VISIBLE, not silently
    # swallowed: emit a degraded stderr line and record a decision:"error" row
    # below so a broken router path announces itself. (Benign LM-Studio-down
    # failures are handled inside call_router_model and return None without raising.)
    call_error: Exception | None = None
    raw_result: dict | None = None
    try:
        sys.path.insert(0, str(HOOKS_DIR))
        from router_core import ROUTER_MODEL, call_router_model  # type: ignore[import]

        agents_dir = str(HOOKS_DIR.parent / "agents")
        raw_result = call_router_model(user_msg, agents_dir=agents_dir)
    except Exception as exc:
        call_error = exc
        raw_result = None
        ROUTER_MODEL = os.environ.get("_HOOK_ROUTER_MODEL", "granite-4.1-3b")
        print(f"[router] degraded: {type(exc).__name__}", file=sys.stderr)

    latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000

    # call_router_model now returns the parsed classification wrapped with the exact
    # model input. The threshold/persona logic below reads `classification`; the record
    # writer stamps the model input (messages/system_prompt_sha256) as provenance.
    classification: dict | None = None
    messages: list = []
    system_prompt_sha256 = ""
    if raw_result is not None:
        classification = raw_result.get("classification")
        messages = raw_result.get("messages", [])
        system_prompt_sha256 = raw_result.get("system_prompt_sha256", "")

    files_dir = _files_dir()
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    prompt_hash = hashlib.sha256(user_msg.encode("utf-8")).hexdigest()
    router_code_sha = _router_code_sha()

    # v2 capture record (T0). Carries the exact model input (messages +
    # system_prompt_sha256), provenance (model_id, router_code_sha, prompt_hash),
    # and the OPT-001 quarantine flag (router_version="buggy") so the moment
    # OPT-001 lands, "clean from today" rows are unambiguously identifiable. The
    # labeler is `python -m broker.router_train` (PLANNED). Logs stay LOCAL in
    # .memory/files/ — never uploaded.
    def _decision_record(decision: str, model_out: dict | None) -> dict:
        rec = {
            "timestamp": now_iso,
            "session_id": session_id,
            "prompt": user_msg,  # FULL prompt — not truncated. Needed for fine-tune.
            "decision": decision,
            "latency_ms": latency_ms,
            "schema_version": SCHEMA_VERSION,
            "router_version": ROUTER_VERSION,
            "model_id": ROUTER_MODEL,
            "messages": messages,
            "system_prompt_sha256": system_prompt_sha256,
            "router_code_sha": router_code_sha,
            "prompt_hash": prompt_hash,
        }
        if model_out is not None:
            rec.update(
                {
                    "pred_persona": model_out.get("persona", "unknown"),
                    "pred_confidence": model_out.get("confidence", 0.0),
                    "pred_difficulty": model_out.get("difficulty", "unknown"),
                    "pred_required_skills": model_out.get("required_skills", []),
                    "pred_tdd_required": model_out.get("tdd_required", False),
                }
            )
        return rec

    def _write_decision(decision: str, model_out: dict | None) -> dict:
        """Build, validate (fail-soft), and append a decision record.

        The write-time guard (_validate_record) is the check that would have
        surfaced the 5 prior phantom-pipeline failures: on a malformed capture it
        STILL appends the row (never block the user) AND appends a
        capture_invalid heartbeat so a broken capture announces itself.
        """
        rec = _decision_record(decision, model_out)
        ok, reason = _validate_record(rec)
        if not ok:
            _append_jsonl(
                files_dir / "router_decisions.jsonl",
                {
                    "timestamp": now_iso,
                    "session_id": session_id,
                    "decision": "capture_invalid",
                    "reason": reason,
                },
            )
        _append_jsonl(files_dir / "router_decisions.jsonl", rec)
        return rec

    # router_core blew up (import/HTTP/unexpected) — record a durable
    # decision:"error" row carrying the exception repr so the failure is
    # observable in router_decisions.jsonl, then fail open (exit 0).
    if call_error is not None:
        err_rec = _decision_record("error", None)
        err_rec["error"] = repr(call_error)
        _append_jsonl(
            files_dir / "hook_heartbeat.jsonl",
            {"hook": "router", "ts": now_iso, "decision": "error"},
        )
        ok, reason = _validate_record(err_rec)
        if not ok:
            _append_jsonl(
                files_dir / "router_decisions.jsonl",
                {
                    "timestamp": now_iso,
                    "session_id": session_id,
                    "decision": "capture_invalid",
                    "reason": reason,
                },
            )
        _append_jsonl(files_dir / "router_decisions.jsonl", err_rec)
        sys.exit(0)

    if classification is None or classification.get("confidence", 0.0) < THRESHOLD_LLM:
        decision = "fallthrough"

        _append_jsonl(
            files_dir / "hook_heartbeat.jsonl",
            {"hook": "router", "ts": now_iso, "decision": decision},
        )
        # Capture EVERY decision — even fallthroughs — for training data.
        # Fallthroughs tell us where the model is under-confident and need
        # disambiguation examples.
        _write_decision(decision, classification)
        sys.exit(0)

    persona = classification["persona"]
    confidence = classification["confidence"]
    difficulty = classification.get("difficulty", "standard")
    required_skills = classification.get("required_skills", [])
    tdd_required = classification.get("tdd_required", False)

    # Read-only / design personas never write production code — force tdd=false
    # regardless of what the model emitted to prevent spurious tdd="true" chips.
    _READ_ONLY_PERSONAS = frozenset({"scout", "lens", "lens-fast", "palette"})
    if persona in _READ_ONLY_PERSONAS:
        tdd_required = False
        classification = {**classification, "tdd_required": False}

    # meta = Nexus handles the request directly; suppress routing chip
    if persona == "meta":
        _write_decision("meta", classification)
        sys.exit(0)

    shadow_set = _shadow_personas()
    is_shadow = persona in shadow_set

    if is_shadow:
        decision = "shadow"
        tag_name = "routing-shadow"
    else:
        decision = "prefill"
        tag_name = "routing-pre-fill"

    skills_str = ", ".join(required_skills) if required_skills else "none"
    tag_attrs = (
        f'persona="{persona}" difficulty="{difficulty}" '
        f'confidence="{confidence:.2f}" tdd="{str(tdd_required).lower()}" '
        f'required_skills="{skills_str}"'
    )
    additional_context = (
        f"<{tag_name} {tag_attrs}>"
        f"Route to {persona} (difficulty={difficulty}, tdd={tdd_required}, "
        f"skills={skills_str})"
        f"</{tag_name}>"
    )

    _append_jsonl(
        files_dir / "hook_heartbeat.jsonl",
        {"hook": "router", "ts": now_iso, "decision": decision},
    )
    _write_decision(decision, classification)

    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": additional_context}}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)

#!/usr/bin/env python3
# SubagentStop hook: enforces Constitution Article XI — No Deferral of
# discovered errors. When an agent that SHOULD have fixed a discovered
# error/anomaly instead defers the FIX to a follow-up, that is a contract
# violation unless the user explicitly authorized the defer.
#
# Scope: target Nexus installs (Art. XI is Core+Target).
#
# DECISION MODEL
#   block (exit 2)  — a defer-of-a-FIX pattern is present, emitted WITHOUT a
#                     sanctioned `## NEXUS:NEEDS-DECISION` marker, by an agent
#                     whose job was to fix it, with no legitimate inline
#                     resolution or tracked-task reference (DEC-005). Requires
#                     an inline fix, a tracked task, or a user-authorized defer
#                     (escalated via NEEDS-DECISION). ONLY reachable when the
#                     calibration flag below is set — see SHADOW MODE.
#   warn  (exit 0 + additionalContext) — a defer pattern is present but the
#                     signal is AMBIGUOUS (report-only framing co-occurs, or a
#                     read-only persona), OR the shadow-mode flag is active and
#                     this return WOULD have been denied (would-deny, logged
#                     but not enforced). Surfaced, not blocked. Conservative:
#                     when in doubt, WARN — never block a legitimate return.
#   allow (exit 0)  — no defer-of-fix pattern, OR a NEEDS-DECISION marker is
#                     present, OR an unambiguous read-only/verifier "reported
#                     only" return, OR a matching typed override was honored.
#
# CRITICAL PRECISION (false-positive avoidance): read-only / verifier returns
# that merely say "out of scope, reported only", "verify and report only", or
# note an item is already tracked in a task are LEGITIMATE and MUST NOT block.
# Only an agent that should have FIXED something — and deferred the fix — is
# blocked.
#
# SHADOW MODE (R3-T08 / N13, per plans/11-gate-enforcement-audit.md §4):
# this gate ships upgraded to deny-CAPABLE but SHADOW-FIRST — a would-deny
# fixture logs a `"decision":"would-deny"` row to gate_blocks.jsonl and WARNS
# (never blocks) unless the calibration flag is explicitly set. This is the
# speed guard from C2: an uncalibrated deny gate is new ritual latency.
#   NEXUS_NO_DEFERRAL_SHADOW  — default "1" (ON). "0"/"false" disables shadow
#                               logging (has no effect on enforcement).
#   NEXUS_NO_DEFERRAL_ENFORCE — default unset (OFF). Only "1"/"true" flips a
#                               would-deny into a REAL deny (exit 2). This is
#                               the one flag N12's promotion criteria (§4:
#                               would-deny rate<=5% AND false-positive
#                               rate<=10% over N=100/14d) must be met before
#                               flipping — this leaf does NOT flip it.
#
# TYPED OVERRIDE (N12 §3): a single machine-readable `override` object on the
# payload (top-level or under tool_input), scoped to the EXACT (gate, code)
# pair this hook would have denied with:
#   {"override": {"gate": "DEFER", "code": "FIX-DEFERRED",
#                  "reason": "<non-empty>", "authorized_by": "user"}}
# A mismatched/missing pair or empty reason is not an override — falls
# through to the normal decision. Honored overrides are audit-logged as a
# `"decision":"override"` row (never silently allowed).
#
# BLOCK / WARN SHAPE (mirrors root-cause-gate.sh / lens-gate.sh in this package):
# block is plain stderr + `exit 2` (the SubagentStop block contract — JSON is
# ignored on exit 2; permissionDecision is PreToolUse-only). Warn is a nested
# `hookSpecificOutput.additionalContext` object on stdout + exit 0. This package
# carries no shared _gate_deny helper, so the emitters are inlined here.
#
# 3.9 CONSTRAINT — the harness runs hooks under the system python3 (3.9.6 on
# macOS). No `X | None` runtime unions, no `datetime.UTC`, no match/case here;
# `list[...]` / `tuple[...]` / `re.Pattern[str]` subscripts are 3.9-safe
# (PEP 585). test_hooks_py39_import.py imports this file under /usr/bin/python3.
#
# Returns exit 2 (block) or exit 0 (pass / warn / skip).

import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

EVENT = "SubagentStop"
GATE_ID = "DEFER"
DENY_CODE = "FIX-DEFERRED"

# Load _heartbeat from the same hooks directory. Best-effort only — see
# _heartbeat.py; this MUST NEVER change exit code/behavior of this gate.
try:
    _hb_path = Path(__file__).parent / "_heartbeat.py"
    _hb_spec = importlib.util.spec_from_file_location("_heartbeat", _hb_path)
    _heartbeat_mod = importlib.util.module_from_spec(_hb_spec)
    _hb_spec.loader.exec_module(_heartbeat_mod)
except Exception:
    _heartbeat_mod = None


def _emit_heartbeat(event, decision, latency_ms):
    if _heartbeat_mod is None:
        return
    _heartbeat_mod.emit_heartbeat("no-deferral-gate", event, decision, latency_ms)


_START_TIME = time.time()


def _elapsed_ms():
    try:
        return int((time.time() - _START_TIME) * 1000)
    except Exception:
        return 0


def _gate_blocks_sink():
    sink_path = os.environ.get("NEXUS_GATE_BLOCKS_PATH")
    if sink_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        sink_path = str(repo_root / ".memory" / "files" / "gate_blocks.jsonl")
    return Path(sink_path)


def _record_block(event, code, reason):
    """Append one JSONL row to the gate-block sink. BEST-EFFORT: swallows all errors.

    This package build carries no shared _gate_deny helper (see header note),
    so its own inline stderr+exit-2 deny path never wrote to gate_blocks.jsonl.
    Mirrors _gate_deny.py's _record_block schema exactly ({ts, event, hook,
    code, reason}) so no-deferral-gate deny events land in the same stream.
    """
    try:
        sink = _gate_blocks_sink()
        sink.parent.mkdir(parents=True, exist_ok=True)
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


def _record_would_deny(reason):
    """Best-effort shadow-mode audit row: gate_blocks.jsonl, decision=would-deny."""
    try:
        sink = _gate_blocks_sink()
        sink.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "event": EVENT,
            "hook": GATE_ID,
            "code": DENY_CODE,
            "reason": reason[:200],
            "decision": "would-deny",
        }
        with open(sink, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _record_override(reason, authorized_by):
    """Best-effort audit row for an honored typed override (N12 §3)."""
    try:
        sink = _gate_blocks_sink()
        sink.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "event": EVENT,
            "hook": GATE_ID,
            "code": DENY_CODE,
            "reason": DENY_CODE,
            "decision": "override",
            "override_reason": reason[:200],
            "authorized_by": authorized_by,
        }
        with open(sink, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass


def _env_flag(name, default):
    val = os.environ.get(name, default).strip().lower()
    return val in ("1", "true", "yes", "on")


def _shadow_mode_enabled():
    return _env_flag("NEXUS_NO_DEFERRAL_SHADOW", "1")


def _enforce_enabled():
    return _env_flag("NEXUS_NO_DEFERRAL_ENFORCE", "0")


def _extract_override(payload):
    """Return the `override` object from top-level or tool_input, else {}."""
    ov = payload.get("override")
    if not isinstance(ov, dict):
        tool_input = payload.get("tool_input", {})
        if isinstance(tool_input, dict):
            ov = tool_input.get("override")
    return ov if isinstance(ov, dict) else {}


def _override_matches(override):
    """N12 §3: override is scoped to the EXACT (gate, code) pair, requires a
    non-empty reason, and is only honored when authorized_by == 'user'
    (human-in-the-loop only — no persona/orchestrator self-override)."""
    if not override:
        return False
    if str(override.get("gate", "")).strip() != GATE_ID:
        return False
    if str(override.get("code", "")).strip() != DENY_CODE:
        return False
    if not str(override.get("reason", "")).strip():
        return False
    if str(override.get("authorized_by", "")).strip().lower() != "user":
        return False
    return True


# Agents whose mandate is to FIX. A deferred fix from one of these, without
# authorization, is the Art. XI violation this gate exists to catch.
# Mirrors lens-gate's GATED_AGENTS (code-writing personas) + the orchestrator.
FIXING_AGENTS = frozenset({
    "forge-ui",
    "forge-wire",
    "pipeline-data",
    "pipeline-async",
    "atlas",
    "hermes",
    "quill-ts",
    "quill-py",
    "nexus",
    "plexus",
    "nexus-orchestrator",
    "plexus-orchestrator",
})

# Read-only / report-only personas. Their job is to investigate or validate and
# REPORT — deferring a fix is their correct behavior, never a violation. For
# these, the gate degrades to WARN at most (never block).
# palette moved here: palette is a design/advisory persona, not a code-writing
# fixer — deferred items from palette are correct report-only behavior.
READONLY_AGENTS = frozenset({
    "scout",
    "lens",
    "palette",
})

# Defer-of-a-FIX patterns. These signal a discovered issue whose FIX is being
# pushed to later work. Word-boundary / phrase anchored to avoid matching
# unrelated prose (e.g. "follow-up question").
DEFER_PATTERNS = [
    re.compile(r"defer(?:red|ring)?\s+(?:to|the\s+fix|this)\b.*\bfollow[\s-]?up", re.IGNORECASE),
    re.compile(r"\bdeferred\s+to\s+a\s+follow[\s-]?up\b", re.IGNORECASE),
    re.compile(r"\bwill\s+address\s+(?:it|this|that|these)?\s*separately\b", re.IGNORECASE),
    re.compile(r"\bwill\s+fix\s+(?:it|this|that|these)?\s*separately\b", re.IGNORECASE),
    re.compile(r"\baddress(?:ed|ing)?\s+(?:it|this|that)?\s*in\s+a\s+(?:separate|follow[\s-]?up)\b", re.IGNORECASE),
    re.compile(r"\bfil(?:ed|ing)\s+(?:it\s+)?as\s+a\s+follow[\s-]?up\b", re.IGNORECASE),
    re.compile(r"\bfil(?:ed|ing)\s+a\s+follow[\s-]?up\s+task\b", re.IGNORECASE),
    re.compile(r"\bcreat(?:ed|ing)\s+a\s+follow[\s-]?up\s+task\s+to\s+fix\b", re.IGNORECASE),
    re.compile(r"\bleav(?:e|ing)\s+(?:it|this|that)\s+for\s+a\s+follow[\s-]?up\b", re.IGNORECASE),
    re.compile(r"TODO\(", re.IGNORECASE),
]

# Sanctioned escalation marker. Its presence means the agent did NOT silently
# defer — it surfaced the decision to the orchestrator/user, which is the
# Art. XI escape hatch ("unless the user explicitly authorizes the defer").
NEEDS_DECISION_RE = re.compile(r"##\s+NEXUS:NEEDS-DECISION", re.IGNORECASE)

# Explicit user-authorization phrasing (belt-and-suspenders alongside the
# NEEDS-DECISION marker). If the agent records that the user sanctioned the
# defer, it is authorized. Handles both subject phrasings ("the user
# authorized", "you authorized") and either order of the authorize-verb and the
# "defer" token within a clause.
_AUTH_SUBJECT = r"(?:user|operator|you)"
_AUTH_VERB = r"(?:authori[sz]ed|approved|sanctioned|confirmed|agreed\s+to)"
USER_AUTHORIZED_RE = re.compile(
    r"\b" + _AUTH_SUBJECT + r"\b[^.\n]{0,20}\b" + _AUTH_VERB + r"\b[^.\n]{0,40}\bdefer"
    r"|\b" + _AUTH_SUBJECT + r"[\s-]authori[sz]ed\b[^.\n]{0,40}\bdefer"
    r"|\bdefer[^.\n]{0,40}\b" + _AUTH_SUBJECT + r"\b[^.\n]{0,20}\b" + _AUTH_VERB + r"\b",
    re.IGNORECASE,
)

# Legitimate read-only / verifier framing. When a return is explicitly a
# report-only / out-of-scope / already-tracked statement, the "defer" is not a
# deferred FIX — it is the correct behavior of a reporting agent. Presence of
# any of these near a defer pattern downgrades block → warn (and for read-only
# personas, allow outright).
REPORTONLY_PATTERNS = [
    re.compile(r"\bout[\s-]of[\s-]scope\b[^.\n]{0,40}\breport(?:ed|ing)?\s+only\b", re.IGNORECASE),
    re.compile(r"\breport(?:ed|ing)?\s+only\b", re.IGNORECASE),
    re.compile(r"\bverify\s+and\s+report\s+only\b", re.IGNORECASE),
    re.compile(r"\bread[\s-]only\b[^.\n]{0,40}\b(?:report|recon|investigat|review|audit)", re.IGNORECASE),
    re.compile(r"\balready\s+tracked\b", re.IGNORECASE),
    re.compile(r"\btracked\s+(?:in|as|under)\s+(?:task|ticket|issue|TASK-|#)\b", re.IGNORECASE),
    re.compile(r"\b(?:noting|reporting|flagging|surfacing)\b[^.\n]{0,40}\bout[\s-]of[\s-]scope\b", re.IGNORECASE),
    re.compile(r"\bout[\s-]of[\s-]scope\s+for\s+this\s+(?:task|delivery|brief|change)\b", re.IGNORECASE),
]

# DEC-005 tracked-task reference (distinct from generic report-only prose):
# a concrete TaskCreate / TASK-<id> / ticket reference proves the surfaced
# item has an actual tracked follow-up, not just a verbal "noted" gesture.
TRACKED_TASK_RE = re.compile(
    r"\bTASK-\d+\b"
    r"|\bTaskCreate\b"
    r"|\btask\s+(?:id|#)\s*[:=]?\s*\S+"
    r"|\b(?:opened|created|filed)\s+(?:a\s+)?tracked\s+task\b",
    re.IGNORECASE,
)


def _extract(payload):
    """Return (assistant_text, agent_name) from the hook payload.

    NATIVE-4: agent_type / tool_input.agent_type added to the persona fallback
    chain (mirrors return-validator.py's _extract()). The harness dispatches
    via the Agent tool, which carries the persona under subagent_type for
    Task-shaped dispatches but under agent_type for Agent/Team-shaped
    dispatches — a SubagentStop payload for an Agent-tool dispatch was falling
    through straight to "unknown", which this (deny-capable) gate then routed
    to the bare "unknown" WARN branch instead of resolving the real persona's
    FIXING_AGENTS / READONLY_AGENTS membership.
    """
    assistant_text = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    agent_name = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("agent_type")
        or tool_input.get("subagent_type")
        or tool_input.get("agent_type")
        or "unknown"
    )
    return assistant_text, str(agent_name).strip().lower()


def _first_defer_match(text):
    for pat in DEFER_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    return ""


def _any(patterns, text):
    return any(p.search(text) for p in patterns)


def _emit_warn(reason):
    """Emit a non-blocking SubagentStop additionalContext warning (exit 0)."""
    msg = (
        "[no-deferral-gate] WARN — a deferral-of-a-fix phrase was detected but "
        "the signal is ambiguous, so this return is NOT blocked. Confirm the "
        "discovered issue was either fixed inline (Constitution Article XI: the "
        "default is FIX, not FILE) or that the defer is user-authorized and "
        "surfaced via a `## NEXUS:NEEDS-DECISION` marker.\n"
        "  Trigger phrase: " + reason
    )
    out = {
        "hookSpecificOutput": {
            "hookEventName": EVENT,
            "additionalContext": msg,
        }
    }
    print(json.dumps(out))


def _emit_would_deny_warn(agent_name, defer_phrase):
    """Shadow-mode: log a would-deny row, then WARN (never block) so the
    calibration signal accrues without adding ritual latency (C2)."""
    msg = (
        "[no-deferral-gate] WOULD-DENY (shadow mode) — this return would have "
        "been BLOCKED once the calibration flag (NEXUS_NO_DEFERRAL_ENFORCE) is "
        "set: a discovered issue's FIX was deferred without authorization, an "
        "inline resolution, or a tracked task. See Constitution Article XI.\n"
        "  Agent: " + agent_name + "\n"
        "  Deferral phrase: " + defer_phrase + "\n"
        "  Required: fix the issue inline in THIS delivery, OR open a tracked "
        "task (TaskCreate / TASK-<id>), OR escalate via a `## NEXUS:NEEDS-DECISION` "
        "marker and obtain explicit user authorization (AskUserQuestion).\n"
    )
    _record_would_deny(msg)
    out = {
        "hookSpecificOutput": {
            "hookEventName": EVENT,
            "additionalContext": msg,
        }
    }
    print(json.dumps(out))


def _warn_extract_miss(payload):
    """EXTRACT_OK canary (S1-22): valid SubagentStop JSON yielded NO assistant text.

    Harness schema drift (renamed payload keys) would silently disarm this gate —
    every return would look empty and exit 0 forever. Warn LOUDLY instead of
    staying silent (still exit 0: warn, not block). Once per session via a flag
    file keyed on session_id so repeat returns do not spam the orchestrator.
    """
    if not isinstance(payload, dict) or not payload:
        return
    import contextlib
    import tempfile
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "unknown"))[:64]
    flag = os.path.join(tempfile.gettempdir(), ".nexus-extract-miss-no-deferral-gate-" + sid)
    if os.path.exists(flag):
        return
    with contextlib.suppress(OSError):
        open(flag, "w").close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": EVENT,
            "additionalContext": (
                "[no-deferral-gate] EXTRACT-MISS: SubagentStop payload had no "
                "extractable assistant text — possible harness schema drift"
            ),
        }
    }))


def _emit_block(agent_name, defer_phrase):
    """Emit a hard-block via plain stderr + exit 2 (SubagentStop block contract).

    JSON is ignored on exit 2 and permissionDecision is PreToolUse-only, so the
    durable block is stderr + exit 2 — the same shape root-cause-gate.sh and
    lens-gate.sh use in this package.
    """
    msg = (
        "[no-deferral-gate] BLOCK — a discovered issue's FIX was deferred "
        "without authorization. See Constitution Article XI (No Deferral): "
        "the default is FIX, not FILE. Filing a follow-up task without "
        "actually tracking it is FORBIDDEN unless the user explicitly "
        "authorizes the defer.\n"
        "  Agent: " + agent_name + "\n"
        "  Deferral phrase: " + defer_phrase + "\n"
        "  Required: fix the issue inline in THIS delivery, open a tracked "
        "task (TaskCreate / TASK-<id>), OR — if the defer is genuinely "
        "warranted — escalate via a `## NEXUS:NEEDS-DECISION` marker and "
        "obtain explicit user authorization (AskUserQuestion). To override "
        "this specific denial, resubmit with "
        "'\"override\": {\"gate\": \"DEFER\", \"code\": \"FIX-DEFERRED\", "
        "\"reason\": \"<why>\", \"authorized_by\": \"user\"}'.\n"
    )
    print(msg, file=sys.stderr)
    _record_block(EVENT, GATE_ID + "/" + DENY_CODE, msg)
    return 2


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Non-JSON input — fail-safe, do not block.
        return 0

    assistant_text, agent_name = _extract(payload)
    if not assistant_text:
        _warn_extract_miss(payload)
        return 0

    defer_phrase = _first_defer_match(assistant_text)
    if not defer_phrase:
        # No deferral-of-fix language at all — nothing to enforce.
        return 0

    # Sanctioned escalation present → the agent surfaced the decision instead
    # of silently deferring. Allow (Art. XI escape hatch).
    if NEEDS_DECISION_RE.search(assistant_text) or USER_AUTHORIZED_RE.search(assistant_text):
        return 0

    report_only = _any(REPORTONLY_PATTERNS, assistant_text)
    tracked_task = bool(TRACKED_TASK_RE.search(assistant_text))

    # Read-only / verifier personas: deferring a fix is their correct behavior.
    # If they also frame it as report-only (or cite a tracked task) it is
    # unambiguously legitimate → allow. Otherwise warn (never block a
    # reporting agent).
    if agent_name in READONLY_AGENTS:
        if report_only or tracked_task:
            return 0
        _emit_warn(defer_phrase)
        return 0

    # DEC-005: an inline resolution OR a tracked-task reference both satisfy
    # "resolve or track" — a fixing agent that names a real tracked task is
    # not silently deferring, even without the softer report-only framing.
    if report_only or tracked_task:
        _emit_warn(defer_phrase)
        return 0

    # A fixing agent (or the orchestrator) deferred a FIX with no sanctioned
    # NEEDS-DECISION marker, no tracked task, and no report-only framing.
    # This is the DEC-005 violation — deny-capable, but SHADOW-MODE FIRST
    # (N12 §4): log would-deny and warn unless the calibration flag is set.
    if agent_name in FIXING_AGENTS:
        override = _extract_override(payload)
        if _override_matches(override):
            _record_override(
                str(override.get("reason", "")),
                str(override.get("authorized_by", "")),
            )
            return 0

        if _enforce_enabled():
            return _emit_block(agent_name, defer_phrase)

        if _shadow_mode_enabled():
            _emit_would_deny_warn(agent_name, defer_phrase)
            return 0

        # Shadow mode explicitly disabled AND enforce not set: fall back to
        # the pre-N13 advisory WARN behavior (never silently allow with zero
        # signal at all).
        _emit_warn(defer_phrase)
        return 0

    # Unknown / unclassified persona with a bare defer phrase: do not block an
    # agent we cannot confirm was responsible for the fix — WARN instead.
    _emit_warn(defer_phrase)
    return 0


if __name__ == "__main__":
    # main() returns an int (0/2), never raising SystemExit itself — capture
    # it here so heartbeat covers every one of main()'s early-return exit
    # paths (deny/warn/allow) without touching its internal control flow.
    _rc = main()
    _emit_heartbeat(EVENT, "block" if _rc == 2 else "allow", _elapsed_ms())
    sys.exit(_rc)

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
#                     whose job was to fix it, with no legitimate report-only
#                     framing. Requires an inline fix or a user-authorized
#                     defer (escalated via NEEDS-DECISION).
#   warn  (exit 0 + additionalContext) — a defer pattern is present but the
#                     signal is AMBIGUOUS (report-only framing co-occurs, or a
#                     read-only persona). Surfaced, not blocked. Conservative:
#                     when in doubt, WARN — never block a legitimate return.
#   allow (exit 0)  — no defer-of-fix pattern, OR a NEEDS-DECISION marker is
#                     present, OR an unambiguous read-only/verifier "reported
#                     only" return.
#
# CRITICAL PRECISION (false-positive avoidance): read-only / verifier returns
# that merely say "out of scope, reported only", "verify and report only", or
# note an item is already tracked in a task are LEGITIMATE and MUST NOT block.
# Only an agent that should have FIXED something — and deferred the fix — is
# blocked.
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

import json
import re
import sys

EVENT = "SubagentStop"

# Agents whose mandate is to FIX. A deferred fix from one of these, without
# authorization, is the Art. XI violation this gate exists to catch.
# Mirrors lens-gate's GATED_AGENTS (code-writing personas) + the orchestrator.
FIXING_AGENTS = frozenset({
    "forge",
    "forge-ui",
    "forge-wire",
    "forge-ui-pro",
    "forge-wire-pro",
    "pipeline",
    "pipeline-data",
    "pipeline-async",
    "pipeline-data-pro",
    "pipeline-async-pro",
    "atlas",
    "hermes",
    "quill",
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


def _extract(payload: dict) -> tuple:
    """Return (assistant_text, agent_name) from the hook payload."""
    assistant_text = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    agent_name = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("tool_input", {}).get("subagent_type")
        or "unknown"
    )
    return assistant_text, str(agent_name).strip().lower()


def _first_defer_match(text: str) -> str:
    for pat in DEFER_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    return ""


def _any(patterns: list, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _emit_warn(reason: str) -> None:
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


def _warn_extract_miss(payload: dict) -> None:
    """EXTRACT_OK canary (S1-22): valid SubagentStop JSON yielded NO assistant text.

    Harness schema drift (renamed payload keys) would silently disarm this gate —
    every return would look empty and exit 0 forever. Warn LOUDLY instead of
    staying silent (still exit 0: warn, not block). Once per session via a flag
    file keyed on session_id so repeat returns do not spam the orchestrator.
    """
    if not isinstance(payload, dict) or not payload:
        return
    import contextlib
    import os
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


def _emit_block(agent_name: str, defer_phrase: str) -> int:
    """Emit a hard-block via plain stderr + exit 2 (SubagentStop block contract).

    JSON is ignored on exit 2 and permissionDecision is PreToolUse-only, so the
    durable block is stderr + exit 2 — the same shape root-cause-gate.sh and
    lens-gate.sh use in this package.
    """
    msg = (
        "[no-deferral-gate] BLOCK — a discovered issue's FIX was deferred "
        "without authorization. See Constitution Article XI (No Deferral): "
        "the default is FIX, not FILE. Filing a follow-up task is FORBIDDEN "
        "unless the user explicitly authorizes the defer.\n"
        "  Agent: " + agent_name + "\n"
        "  Deferral phrase: " + defer_phrase + "\n"
        "  Required: fix the issue inline in THIS delivery, OR — if the "
        "defer is genuinely warranted — escalate via a `## NEXUS:NEEDS-DECISION` "
        "marker and obtain explicit user authorization (AskUserQuestion).\n"
    )
    print(msg, file=sys.stderr)
    return 2


def main() -> int:
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

    # Read-only / verifier personas: deferring a fix is their correct behavior.
    # If they also frame it as report-only it is unambiguously legitimate →
    # allow. Otherwise warn (never block a reporting agent).
    if agent_name in READONLY_AGENTS:
        if report_only:
            return 0
        _emit_warn(defer_phrase)
        return 0

    # Ambiguous: a defer phrase co-occurs with explicit report-only framing,
    # even from a fixing agent (e.g. the agent both fixed its scope AND reported
    # an out-of-scope item). Be conservative — WARN, do not block.
    if report_only:
        _emit_warn(defer_phrase)
        return 0

    # A fixing agent (or the orchestrator) deferred a FIX with no sanctioned
    # NEEDS-DECISION marker and no report-only framing → BLOCK.
    if agent_name in FIXING_AGENTS:
        return _emit_block(agent_name, defer_phrase)

    # Unknown / unclassified persona with a bare defer phrase: do not block an
    # agent we cannot confirm was responsible for the fix — WARN instead.
    _emit_warn(defer_phrase)
    return 0


if __name__ == "__main__":
    sys.exit(main())

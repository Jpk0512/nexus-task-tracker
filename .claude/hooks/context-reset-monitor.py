#!/usr/bin/env python3
"""UserPromptSubmit hook: increment user_message_count + re-inject INVARIANTS.

Two responsibilities, both advisory (exit 0 always):

1. Per-session message counting. Emits a stderr HIGH-CONTEXT warning every
   CONTEXT_RESET_AT messages (default 10) to remind the orchestrator to
   consider a fresh-context reset. (Unchanged from the original hook.)

2. Post-compaction / resume RE-INJECTION (SOTA report 3.7 "periodic
   re-injection after compaction"; 3.6 Self-Reminder / position-anchoring).
   On the first turn after a context reset (compact / resume / clear) the
   verbatim NEXUS INVARIANTS digest is emitted as additionalContext so the
   model re-sees its HARD RULES at the recency edge of the freshly compacted
   context. The digest is read VERBATIM from the single canonical file
   .claude/INVARIANTS.md and never paraphrased (NoLiMa 2502.05167: associative
   recall collapses when load-bearing tokens — DEC IDs, file paths — are
   reworded), so this UserPromptSubmit copy is byte-identical to the
   SessionStart copy emitted by inject-invariants.sh.

Hook protocol: exit 0 always (advisory, non-blocking). The stderr warning
surfaces as systemMessage text in the UI; the additionalContext injection is
emitted on stdout as the documented hookSpecificOutput object shape.
"""

# PEP 563 lazy annotations so `str | None` never evaluates at runtime — the
# harness launches this hook with the system `python3` (3.9 on macOS), which
# lacks PEP 604 runtime union support.
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = REPO_ROOT / ".memory" / "project.db"
# Override path for tests
_db_path_override = os.environ.get("_HOOK_DB_PATH")
if _db_path_override:
    DB_PATH = Path(_db_path_override)

# Single canonical source of the digest. SessionStart (inject-invariants.sh)
# and this UserPromptSubmit re-injection both read THIS file, so the protected
# set never drifts from the manual (SOTA 3.7: one canonical source, verbatim).
INVARIANTS_PATH = REPO_ROOT / ".claude" / "INVARIANTS.md"
_inv_path_override = os.environ.get("_HOOK_INVARIANTS_PATH")
if _inv_path_override:
    INVARIANTS_PATH = Path(_inv_path_override)

RESET_AT = int(os.environ.get("CONTEXT_RESET_AT", "10"))

# Payload source values that signal a context boundary. Claude Code stamps a
# UserPromptSubmit / SessionStart payload with a "source" (and some harness
# builds use "hook_event"/"trigger") on compaction, resume, and clear.
_RESET_SOURCES = frozenset({"compact", "resume", "clear", "compaction", "auto-compact"})

# NoLiMa 2502.05167 / OPT-023: this nudge MUST NOT recite the rules. The rule
# text lives in exactly ONE place — .claude/INVARIANTS.md, re-emitted verbatim by
# _emit_injection(). A second hand-maintained paraphrase here (the old
# Article-numbered list) silently drifts from the canonical digest, which is the
# exact lost-in-the-middle failure mode the digest exists to prevent. Keep this to
# a counter nudge that POINTS at the canonical source; do not restate any rule.
_WARNING_TEXT = (
    "[context-reset] HIGH-CONTEXT WARNING — {count} user messages this session. "
    "Consider triggering a fresh context: session end with handoff, then session start fresh. "
    "Re-read the verbatim HARD RULES in .claude/INVARIANTS.md (re-injected on any "
    "compaction/resume); do not work from a remembered paraphrase."
)


def _payload_signals_reset(payload: dict) -> bool:
    """Boundary-detection branch (i): an explicit reset signal in the payload.

    A compaction/resume/clear is detected when any of the conventional source
    fields carries one of the reset markers. Robust to the field-name variance
    across harness builds (source / hook_event / trigger / reason).
    """
    if not isinstance(payload, dict):
        return False
    for key in ("source", "hook_event", "hookEvent", "trigger", "reason"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip().lower() in _RESET_SOURCES:
            return True
    return False


def _count_position_discontinuity(payload: dict, persisted_count: int) -> bool:
    """Boundary-detection branch (ii): count-vs-position discontinuity.

    A fresh/compacted context is inferred when project.db still holds a
    non-zero user_message_count for the live session (so we are mid-session by
    the DB's reckoning) but the live transcript indicates a fresh start — the
    harness reports few/zero prior messages in this physical context window.
    The transcript length, when the harness provides it, is the cheap live
    signal; a large persisted count against a tiny live transcript is the
    discontinuity that a silent auto-compaction produces.
    """
    if persisted_count <= 0:
        return False
    live_len = None
    for key in ("transcript_length", "message_count", "context_message_count"):
        val = payload.get(key)
        if isinstance(val, int):
            live_len = val
            break
    if live_len is None:
        # No live-length signal available — cannot assert a discontinuity
        # without risking a false re-inject on every turn. Defer to branch (i).
        return False
    # Persisted count well ahead of the live transcript ⇒ context was reset.
    # <=1: a fresh/compacted window has at most the current prompt (1 msg); strict
    # <persisted: ensures we only fire when the DB already recorded prior progress
    # (persisted_count >= 2), so the very first message of a brand-new session never
    # triggers a spurious re-inject (live_len=1, persisted_count=0 → False).
    return live_len <= 1 < persisted_count


def _emit_injection() -> None:
    """Emit the verbatim INVARIANTS digest as additionalContext (stdout).

    JSON shape contract (matches every live hook): hookSpecificOutput is an
    OBJECT, hookEventName matches the firing event ("UserPromptSubmit"), and
    additionalContext is the verbatim digest. No permissionDecision key — this
    is an additive context injection, not an allow/deny verdict.
    """
    try:
        digest = INVARIANTS_PATH.read_text(encoding="utf-8")
    except OSError:
        # Canonical file missing/unreadable: stay silent rather than inject a
        # paraphrase. A missing digest is a wiring bug, not a place to improvise.
        return
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": digest,
        }
    }
    print(json.dumps(out))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(payload, dict):
        payload = {}

    if not DB_PATH.exists():
        # No DB ⇒ no counting. Still honor an explicit reset signal so a fresh
        # resume re-grounds even before the session row exists.
        if _payload_signals_reset(payload):
            _emit_injection()
        sys.exit(0)

    persisted_count = 0
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, user_message_count FROM sessions "
                "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                # No live session row; honor explicit reset signal then exit.
                if _payload_signals_reset(payload):
                    _emit_injection()
                sys.exit(0)

            sid = row["id"]
            persisted_count = row["user_message_count"] or 0
            new_count = persisted_count + 1

            conn.execute(
                "UPDATE sessions SET user_message_count = ? WHERE id = ?",
                (new_count, sid),
            )
            conn.commit()

            if new_count % RESET_AT == 0:
                print(_WARNING_TEXT.format(count=new_count), file=sys.stderr)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # Advisory hook (exit 0 always): surface the failure so a broken DB
        # (bad path, locked file, missing sqlite extension) is visible instead
        # of silently dropping the message-count update and reset warning.
        print(
            f"[context-reset] DB error, message count NOT updated: {exc} "
            f"(db={DB_PATH})",
            file=sys.stderr,
        )

    # Re-injection branch (SOTA 3.7). Either an explicit reset signal OR a
    # count-vs-position discontinuity re-grounds the model with the verbatim
    # digest at the recency edge.
    if _payload_signals_reset(payload) or _count_position_discontinuity(
        payload, persisted_count
    ):
        _emit_injection()

    sys.exit(0)


if __name__ == "__main__":
    main()

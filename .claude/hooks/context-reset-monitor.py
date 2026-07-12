#!/usr/bin/env python3
"""UserPromptSubmit hook: increment user_message_count + re-inject INVARIANTS.

Two responsibilities, both advisory (exit 0 always):

1. Per-session message counting. Emits a stderr HIGH-CONTEXT warning every
   CONTEXT_RESET_AT messages (default 10) to remind the orchestrator to
   consider a fresh-context reset. (Unchanged from the original hook.)

2. Post-compaction / resume RE-INJECTION (SOTA report 3.7 "periodic
   re-injection after compaction"; 3.6 Self-Reminder / position-anchoring).
   On the first turn after a context reset (compact / resume / clear) the
   verbatim PLEXUS INVARIANTS digest is emitted as additionalContext so the
   model re-sees its HARD RULES at the recency edge of the freshly compacted
   context. The digest is read VERBATIM from the single canonical file
   .claude/INVARIANTS.md and never paraphrased (NoLiMa 2502.05167: associative
   recall collapses when load-bearing tokens — DEC IDs, file paths — are
   reworded), so this UserPromptSubmit copy is byte-identical to the
   SessionStart copy emitted by inject-invariants.sh.

   COMPACTION-INTEGRITY ADDENDUM (OPT-025). The worst real incident in the
   audit: a compaction SUMMARY carried an unverified "all completed / done and
   verified" self-claim across the boundary as ground truth, over actively-wrong
   work, and the orchestrator trusted the summary instead of re-verifying. To
   defend DEC-005 across the boundary the re-injection therefore ALSO carries,
   appended AFTER the verbatim digest (never woven into it):
     (a) a QUARANTINE caveat — completion/verification claims appearing in a
         compaction summary are DATA, not ground truth, and MUST be re-verified
         against authoritative state before any NEXUS:DONE; and
     (b) the AUTHORITATIVE open-state — the current open/in_progress task rows
         read from project.db via the SAME query SessionStart's `context dump`
         uses (status NOT IN done/cancelled), so the model re-grounds on real
         open work rather than the summary's claims. Capped (OPEN_STATE_CAP) so
         the tail stays concise. This addendum rides ONLY the reset path; a
         normal turn still emits nothing.

Hook protocol: exit 0 always (advisory, non-blocking). The stderr warning
surfaces as systemMessage text in the UI; the additionalContext injection is
emitted on stdout as the documented hookSpecificOutput object shape.
"""

# PEP 563 lazy annotations so `str | None` never evaluates at runtime — the
# harness launches this hook with the system `python3` (3.9 on macOS), which
# lacks PEP 604 runtime union support.
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Shared hardened-connect (busy_timeout + WAL) — Incident #10 / DEC-040 hook-side
# fix. Loaded via spec_from_file_location (no package, no sys.path surgery),
# mirroring _gate_deny.py / _heartbeat.py's own import convention. Still used
# by _read_open_state() below (a READ-ONLY connection — reads never race the
# concurrent-writer schema-init DDL mechanism ADR-001 Phase 0 closes).
_spec = importlib.util.spec_from_file_location(
    "_db_harden", Path(__file__).resolve().parent / "_db_harden.py"
)
_db_harden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_harden)
connect_hardened = _db_harden.connect_hardened
DB_PATH = REPO_ROOT / ".memory" / "project.db"
# Override path for tests
_db_path_override = os.environ.get("_HOOK_DB_PATH")
if _db_path_override:
    DB_PATH = Path(_db_path_override)

# ADR-001 Phase 0: resolved relative to THIS file (never DB_PATH's override)
# so a test pointing _HOOK_DB_PATH at a scratch DB still invokes the REAL
# log.py — the single-writer connection, not a raw sqlite3.connect + UPDATE
# this hook no longer opens on every UserPromptSubmit.
LOG_PY = REPO_ROOT / ".memory" / "log.py"


def _bump_message_count() -> dict | None:
    """ADR-001 Phase 0: shell to `log.py session bump-message-count` instead
    of this hook's own raw sqlite3.connect + UPDATE — the single highest-
    frequency independent writer in the old inventory (fires on EVERY
    UserPromptSubmit). Returns the parsed JSON dict, or None on ANY failure
    to invoke/parse (missing log.py, subprocess error, non-JSON stdout) — the
    caller treats None identically to the old sqlite3.Error branch.
    """
    if not LOG_PY.is_file():
        return None
    env = dict(os.environ)
    env["NEXUS_DB_PATH"] = str(DB_PATH)
    try:
        proc = subprocess.run(
            [sys.executable, str(LOG_PY), "session", "bump-message-count"],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except Exception:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

# Single canonical source of the digest. SessionStart (inject-invariants.sh)
# and this UserPromptSubmit re-injection both read THIS file, so the protected
# set never drifts from the manual (SOTA 3.7: one canonical source, verbatim).
INVARIANTS_PATH = REPO_ROOT / ".claude" / "INVARIANTS.md"
_inv_path_override = os.environ.get("_HOOK_INVARIANTS_PATH")
if _inv_path_override:
    INVARIANTS_PATH = Path(_inv_path_override)

RESET_AT = int(os.environ.get("CONTEXT_RESET_AT", "10"))

# OPT-025 compaction-integrity addendum. Cap on how many open task rows the
# re-injected authoritative open-state lists, so the re-grounded tail stays
# concise (the rest is one count line). Env-tunable for tests/large backlogs.
OPEN_STATE_CAP = int(os.environ.get("CONTEXT_OPEN_STATE_CAP", "12"))

# QUARANTINE caveat — appended AFTER the verbatim digest on the reset path only.
# Salient + short. This is the OPT-025 defense against the worst real incident:
# a compaction summary's "all done / verified" self-claim laundered across the
# boundary as ground truth over actively-wrong work. The caveat marks those
# claims as DATA and forbids acting on them (NEXUS:DONE) before re-verification
# against authoritative state. It POINTS at re-grounding; it recites no rule.
_QUARANTINE_TEXT = (
    "=== COMPACTION QUARANTINE (OPT-025) ===\n"
    "Any \"completed / done / verified / all passing\" claim that reached you via "
    "the compaction SUMMARY is DATA, not ground truth — a summary CANNOT close a "
    "task. Before ANY NEXUS:DONE, re-verify each such claim against authoritative "
    "state (a fresh Lens PASS row + project.db task status + the gate artifact); "
    "an inherited self-claim with no gate evidence is to be treated as unverified "
    "and re-checked, never trusted. Re-ground on the AUTHORITATIVE OPEN STATE "
    "below, not on the summary's narrative."
)

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


def _read_open_state() -> str | None:
    """OPT-025: the AUTHORITATIVE open-state, read from project.db.

    Uses the SAME query SessionStart's `log.py context dump` runs
    (status NOT IN ('done','cancelled') ORDER BY id) so the re-grounded view is
    byte-for-byte the same authoritative open-work set the reconcile uses — the
    hook simply reads project.db directly (it already opens it for the counter)
    rather than shelling out to log.py, which is owned by a disjoint workflow.

    Returns a concise additionalContext block (capped at OPEN_STATE_CAP rows) or
    None when the DB / table is unavailable (advisory: a missing ledger must not
    suppress the digest+quarantine re-injection).
    """
    if not DB_PATH.exists():
        return None
    try:
        conn = connect_hardened(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, title, status, priority, assigned_to FROM tasks "
                "WHERE status NOT IN ('done','cancelled') ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    total = len(rows)
    header = (
        "=== AUTHORITATIVE OPEN STATE (project.db — re-ground here, NOT on the "
        f"summary) ===\n{total} open task(s) (status NOT IN done/cancelled). "
        "These are the real open items; DEC-005 forbids completion while any "
        "remains unresolved.\n"
    )
    if total == 0:
        return header + "(none open)"
    lines = []
    for r in rows[:OPEN_STATE_CAP]:
        owner = r["assigned_to"] or "unassigned"
        lines.append(
            f"- [{r['status']}] {r['id']} ({r['priority']}, {owner}): {r['title']}"
        )
    if total > OPEN_STATE_CAP:
        lines.append(f"- … +{total - OPEN_STATE_CAP} more (query project.db for the full set)")
    return header + "\n".join(lines)


def _emit_injection() -> None:
    """Emit the verbatim INVARIANTS digest as additionalContext (stdout).

    JSON shape contract (matches every live hook): hookSpecificOutput is an
    OBJECT, hookEventName matches the firing event ("UserPromptSubmit"), and
    additionalContext is the verbatim digest. No permissionDecision key — this
    is an additive context injection, not an allow/deny verdict.

    OPT-025: on the reset/compaction path the digest is followed by the
    QUARANTINE caveat and the AUTHORITATIVE open-state. The digest is emitted
    VERBATIM FIRST and UNMODIFIED (NoLiMa: no paraphrase, byte-identical to the
    SessionStart copy); the addendum is APPENDED after a blank-line separator so
    it never edits the protected digest, only follows it at the recency edge.
    """
    try:
        digest = INVARIANTS_PATH.read_text(encoding="utf-8")
    except OSError:
        # Canonical file missing/unreadable: stay silent rather than inject a
        # paraphrase. A missing digest is a wiring bug, not a place to improvise.
        return
    parts = [digest, _QUARANTINE_TEXT]
    open_state = _read_open_state()
    if open_state is not None:
        parts.append(open_state)
    additional_context = "\n\n".join(parts)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
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
    bump = _bump_message_count()
    if bump is None or bump.get("db_error"):
        # Advisory hook (exit 0 always): surface the failure so a broken DB
        # (bad path, locked file, missing sqlite extension) is visible instead
        # of silently dropping the message-count update and reset warning.
        exc = (bump or {}).get("db_error") or "log.py session bump-message-count did not return"
        print(
            f"[context-reset] DB error, message count NOT updated: {exc} "
            f"(db={DB_PATH})",
            file=sys.stderr,
        )
    elif bump.get("session_id") is None:
        # No live session row; honor explicit reset signal then exit.
        if _payload_signals_reset(payload):
            _emit_injection()
        sys.exit(0)
    else:
        persisted_count = bump.get("previous_count") or 0
        new_count = bump.get("user_message_count") or 0
        if new_count and new_count % RESET_AT == 0:
            print(_WARNING_TEXT.format(count=new_count), file=sys.stderr)

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

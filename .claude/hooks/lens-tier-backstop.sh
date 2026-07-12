#!/usr/bin/env python3
"""lens-tier-backstop.sh — Stop hook (ADVISORY ONLY, exit 0 always).

R1-T08 (lens-gate v2) third enforcement point. lens-gate.sh enforces the
N-distinct-lens-row requirement at SubagentStop time (per-dispatch, can
block). This hook is the SESSION-END backstop: it independently re-derives
the same invariant against validation_log at Stop time, as a safety net in
case a SubagentStop dispatch was somehow never observed by lens-gate.sh
(e.g. a harness delivery gap, a hook that errored out before running, or a
DONE marker embedded in a transcript the SubagentStop hook never fired on).

WHY ADVISORY, NOT BLOCKING: Stop fires when the session is already ending —
there is no implementer left to re-dispatch and no further tool call to deny.
Blocking here would only make the harness's own exit fail with nothing left
to fix it; the CONTRACT-compliant response to a gap discovered this late is
to surface it loudly so the NEXT session (or the user) can act, never to
silently pass OR to hard-fail with no recourse. Mirrors the advisory
contract of do-not-touch-guard.sh / session-end-reminder.sh.

WHAT IT CHECKS: for every validation_log row with agent_validated='lens',
verdict='PASS', risk_tier='T2' (the orchestrator required a full audit) in
the recent window, confirm a matching lens_type='T2' PASS row exists for the
SAME (target_agent, task_or_brief_hash) — i.e. the required depth was
ACTUALLY delivered, not merely claimed via risk_tier. Mirrors lens-gate.sh's
_has_lens_validation_v2 logic (same query shape) but runs session-wide
instead of per-dispatch.

Reads the SAME validation_log table lens-gate.sh writes to — no new state,
no schema change.

Output contract:
  {"hookSpecificOutput":{"hookEventName":"Stop",
                         "additionalContext":<warning text>}}
on STDOUT only when at least one gap is found. Otherwise STDOUT is silent.
Always exits 0.

Env overrides (test isolation, mirroring lens-gate.sh / do-not-touch-guard.sh):
  _HOOK_DB_PATH  — path to project.db (falls back to /Users/john.keeney/nexus-task-tracker token)

NOTE: this file ships un-shimmed and runs under the project's ambient python3
(3.9 on stock macOS), so it MUST stay 3.9-import-safe — do NOT introduce
3.11-only idioms (datetime.UTC, def-time X | None, match/case).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import timedelta

EVENT = "Stop"

DB_PATH = os.environ.get(
    "_HOOK_DB_PATH",
    "/Users/john.keeney/nexus-task-tracker/.memory/project.db",
)

VALIDATION_WINDOW = timedelta(hours=1)


def _advise(msg: str) -> None:
    full = "[lens-tier-backstop] " + msg
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": EVENT,
                    "additionalContext": full,
                }
            },
            separators=(",", ":"),
        )
    )


def _find_tier_gaps(conn: sqlite3.Connection):
    """Return human-readable gap descriptions, or [] if the invariant holds.

    Degrades to "no gaps found" (never crashes) on a DB that predates the
    R1-T08 migration (lens_type/risk_tier columns absent).
    """
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='validation_log'"
    ).fetchone()
    if not table_exists:
        return []

    cols = {r[1] for r in conn.execute("PRAGMA table_info(validation_log)")}
    if "lens_type" not in cols or "risk_tier" not in cols:
        return []

    window_hours = int(VALIDATION_WINDOW.total_seconds() // 3600)
    required_t2_rows = conn.execute(
        "SELECT DISTINCT target_agent, task_or_brief_hash "
        "FROM validation_log "
        "WHERE agent_validated = 'lens' "
        "AND verdict = 'PASS' "
        "AND risk_tier = 'T2' "
        "AND datetime(validated_at) > datetime('now', '-" + str(window_hours) + " hours')"
    ).fetchall()

    gaps = []
    for target_agent, task_hash in required_t2_rows:
        satisfied = conn.execute(
            "SELECT 1 FROM validation_log "
            "WHERE agent_validated = 'lens' "
            "AND target_agent = ? "
            "AND task_or_brief_hash = ? "
            "AND verdict = 'PASS' "
            "AND lens_type = 'T2' "
            "AND datetime(validated_at) > datetime('now', '-" + str(window_hours) + " hours') "
            "LIMIT 1",
            (target_agent, task_hash),
        ).fetchone()
        if not satisfied:
            gaps.append(
                "target_agent=" + str(target_agent) + " task_hash=" + str(task_hash)
                + " risk_tier=T2 required but no lens_type=T2 PASS row found"
            )
    return gaps


def main() -> int:
    try:
        sys.stdin.read()
    except Exception:
        pass

    try:
        conn = sqlite3.connect(DB_PATH)
        gaps = _find_tier_gaps(conn)
        conn.close()
    except sqlite3.Error as exc:
        _advise(
            "WARN — could not audit validation_log for N-distinct-lens-row "
            "coverage: DB error: " + str(exc) + " (db=" + DB_PATH + ")."
        )
        return 0

    if gaps:
        _advise(
            "WARN — session-end audit found T2 (full-audit) validation rows "
            "whose required depth was never actually delivered (risk_tier=T2 "
            "claimed but no matching lens_type=T2 PASS row exists for the same "
            "target_agent+task_hash). This is a backstop signal, not a block — "
            "review before trusting the affected DONE marker(s):\n"
            + "\n".join("  - " + g for g in gaps)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# SubagentStop hook: enforces Root Cause Analysis discipline.
#
# Rules:
#   - NEXUS:REVISE or NEXUS:BLOCKED always require a ## Root Cause Analysis
#     block with >=5 "Why N:" lines.
#   - NEXUS:DONE requires the same block when the task description contains
#     fix/bug/error/regression/broken/hangs/crashes/500 keywords.
#   - Passes write a row to agent_root_cause_log in project.db.
#
# Returns exit 2 (block) or exit 0 (pass/skip).

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone


def _resolve_db_path() -> str:
    """Resolve the project.db path at RUNTIME.

    Precedence:
      1. _HOOK_DB_PATH env override (used by tests and custom installs).
      2. git rev-parse --show-toplevel from this script's directory.
    Falls back to <cwd>/.memory/project.db if git is unavailable.
    """
    override = os.environ.get("_HOOK_DB_PATH")
    if override:
        return override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        repo = subprocess.run(
            ["git", "-C", script_dir, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        repo = os.getcwd()
    return os.path.join(repo, ".memory", "project.db")


DB_PATH = _resolve_db_path()

FIX_KEYWORDS = re.compile(
    r"\b(fix|bug|error|regression|broken|hangs|crashes|500)\b", re.IGNORECASE
)
WHY_LINE = re.compile(r"^\s*Why\s+\d+\s*:", re.IGNORECASE | re.MULTILINE)
RCA_HEADER = re.compile(r"##\s+Root Cause Analysis", re.IGNORECASE)
MARKER_RE = re.compile(
    r"##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)", re.IGNORECASE
)


def init_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_root_cause_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT,
            agent_name      TEXT,
            task_summary    TEXT,
            symptom         TEXT,
            why_chain_json  TEXT,
            pattern_fix     TEXT,
            logged_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def extract_rca(text: str) -> tuple[str, list[str], str]:
    """Return (symptom, why_chain, pattern_fix) from the RCA block, or empty."""
    m = RCA_HEADER.search(text)
    if not m:
        return "", [], ""
    block = text[m.start():]
    # Grab the next H2 boundary as end of block
    next_h2 = re.search(r"\n##\s+", block[3:])
    block = block[: next_h2.start() + 3] if next_h2 else block

    symptom = ""
    sm = re.search(r"Symptom\s*:\s*(.+)", block, re.IGNORECASE)
    if sm:
        symptom = sm.group(1).strip()

    why_lines = WHY_LINE.findall(block)
    # Collect the full line text for each Why N:
    why_chain: list[str] = []
    for wm in re.finditer(r"(Why\s+\d+\s*:.+)", block, re.IGNORECASE):
        why_chain.append(wm.group(1).strip())

    pattern_fix = ""
    pfm = re.search(r"Pattern\s+fix\s*:\s*(.+)", block, re.IGNORECASE)
    if pfm:
        pattern_fix = pfm.group(1).strip()

    return symptom, why_chain, pattern_fix


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
    import tempfile
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "unknown"))[:64]
    flag = os.path.join(tempfile.gettempdir(), ".nexus-extract-miss-root-cause-gate-" + sid)
    if os.path.exists(flag):
        return
    with contextlib.suppress(OSError):
        open(flag, "w").close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": (
                "[root-cause-gate] EXTRACT-MISS: SubagentStop payload had no "
                "extractable assistant text — possible harness schema drift"
            ),
        }
    }))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Non-JSON input — fail-safe, do not block.
        return 0

    # Extract fields from hook payload (multiple possible paths).
    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    session_id: str = payload.get("session_id", "unknown")
    agent_name: str = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("tool_input", {}).get("subagent_type")
        or "unknown"
    )
    task_description: str = (
        payload.get("task_description")
        or payload.get("tool_input", {}).get("description")
        or os.environ.get("CLAUDE_TASK_DESCRIPTION", "")
        or ""
    )

    if not assistant_text:
        _warn_extract_miss(payload)
        return 0

    # Determine which marker is present.
    marker_match = MARKER_RE.search(assistant_text)
    if not marker_match:
        return 0

    marker = marker_match.group(1).upper()

    needs_rca = marker in ("REVISE", "BLOCKED") or (
        marker == "DONE" and FIX_KEYWORDS.search(task_description)
    )

    if not needs_rca:
        return 0

    symptom, why_chain, pattern_fix = extract_rca(assistant_text)
    rca_present = RCA_HEADER.search(assistant_text) is not None
    why_count = len(why_chain)

    if not rca_present or why_count < 5:
        msg = (
            "[root-cause-gate] BLOCK — fix-tasks require ## Root Cause Analysis "
            "with 5+ Why levels. See Constitution Article X.\n"
            f"  Marker: NEXUS:{marker}\n"
            f"  RCA block found: {rca_present}\n"
            f"  Why lines found: {why_count} (need >=5)\n"
        )
        print(msg, file=sys.stderr)
        return 2

    # Pass — log to DB.
    try:
        conn = sqlite3.connect(DB_PATH)
        init_table(conn)
        conn.execute(
            """
            INSERT INTO agent_root_cause_log
                (session_id, agent_name, task_summary, symptom, why_chain_json, pattern_fix, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                agent_name,
                task_description[:200],
                symptom,
                json.dumps(why_chain),
                pattern_fix,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        # Fail-safe: DB write failure does not block the agent.
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

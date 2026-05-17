#!/usr/bin/env python3
# PostToolUse hook: captures a snapshot row whenever a doc-critical file is
# edited (docs/features/*, docs/CONSTITUTION.md, docs/DECISIONS.md).
# Non-blocking — records only.

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))
REPO = os.environ.get("REPO_ROOT", os.getcwd())

WATCHED_PATTERNS = (
    re.compile(r"docs/features/"),
    re.compile(r"docs/CONSTITUTION\.md$"),
    re.compile(r"docs/DECISIONS\.md$"),
)

MIN_LINE_DIFF = 5


def init_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reflection_snapshot (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT,
            file_path       TEXT NOT NULL,
            action_type     TEXT,
            one_line_summary TEXT,
            captured_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def classify_action(file_path: str) -> str:
    if "CONSTITUTION" in file_path:
        return "constitution_amend"
    if "DECISIONS" in file_path:
        return "decision_amend"
    if "features/" in file_path:
        return "spec_update"
    return "other"


def summarize_diff(old_content: str, new_content: str) -> tuple[str, int]:
    """Return (one_line_summary, changed_line_count)."""
    old_lines = old_content.splitlines() if old_content else []
    new_lines = new_content.splitlines() if new_content else []

    added = [l for l in new_lines if l not in set(old_lines)]
    removed = [l for l in old_lines if l not in set(new_lines)]
    changed_count = len(added) + len(removed)

    if changed_count == 0:
        return "no significant changes", 0

    # Try to find a meaningful first added/changed line for summary.
    first_added = next((l.strip() for l in added if l.strip()), "")
    first_removed = next((l.strip() for l in removed if l.strip()), "")

    if first_added and first_removed:
        summary = f"changed: '{first_removed[:80]}' -> '{first_added[:80]}'"
    elif first_added:
        summary = f"added: '{first_added[:120]}'"
    elif first_removed:
        summary = f"removed: '{first_removed[:120]}'"
    else:
        summary = f"{changed_count} line(s) modified"

    return summary[:200], changed_count


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    tool_name: str = payload.get("tool_name") or payload.get("tool", "") or ""
    tool_input: dict = payload.get("tool_input") or {}
    tool_result: dict = payload.get("tool_result") or {}
    session_id: str = payload.get("session_id", "unknown")

    # Determine file path from the tool input.
    file_path: str = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or ""
    )

    if not file_path:
        return 0

    # Normalize to relative path for pattern matching.
    rel_path = file_path.replace(REPO + "/", "").replace(REPO, "")
    if not any(p.search(rel_path) for p in WATCHED_PATTERNS):
        return 0

    # Compute diff summary.
    old_content: str = tool_input.get("old_string") or ""
    new_content: str = tool_input.get("new_string") or tool_input.get("content") or ""

    summary, changed_count = summarize_diff(old_content, new_content)

    if changed_count < MIN_LINE_DIFF:
        return 0

    action_type = classify_action(rel_path)

    try:
        conn = sqlite3.connect(DB_PATH)
        init_table(conn)
        conn.execute(
            """
            INSERT INTO reflection_snapshot
                (session_id, file_path, action_type, one_line_summary, captured_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                rel_path,
                action_type,
                summary,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

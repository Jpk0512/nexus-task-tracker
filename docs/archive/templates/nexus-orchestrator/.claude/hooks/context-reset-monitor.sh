#!/usr/bin/env python3
"""UserPromptSubmit hook: increment user_message_count per session.

Emits a stderr warning every CONTEXT_RESET_AT messages (default 10) to
remind the orchestrator to consider a fresh-context reset.

Hook protocol: exit 0 always (advisory, non-blocking). Warnings go to
stderr so they surface as systemMessage text in the UI.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / ".memory" / "project.db"
# Override path for tests
_db_path_override = os.environ.get("_HOOK_DB_PATH")
if _db_path_override:
    DB_PATH = Path(_db_path_override)

RESET_AT = int(os.environ.get("CONTEXT_RESET_AT", "10"))

_WARNING_TEXT = (
    "[context-reset] HIGH-CONTEXT WARNING — {count} user messages this session. "
    "Consider triggering a fresh context: session end with handoff, then session start fresh. "
    "Top-of-mind rules reload: Root Cause (Art X), No Deferral (Art XI), "
    "Visual+E2E (Art XII), Parallel-First (Art XIII), Lens-before-done (Rule 17), "
    "Notepad read-first/write-last (Rule 16), Deploy-step block (Rule 14)."
)


def main() -> None:
    # Read stdin but we only need it for hook protocol compliance.
    try:
        _payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if not DB_PATH.exists():
        sys.exit(0)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, user_message_count FROM sessions "
                "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if row is None:
                sys.exit(0)

            sid = row["id"]
            new_count = (row["user_message_count"] or 0) + 1

            conn.execute(
                "UPDATE sessions SET user_message_count = ? WHERE id = ?",
                (new_count, sid),
            )
            conn.commit()

            if new_count % RESET_AT == 0:
                print(_WARNING_TEXT.format(count=new_count), file=sys.stderr)
        finally:
            conn.close()
    except sqlite3.Error:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()

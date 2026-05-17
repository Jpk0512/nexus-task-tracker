#!/usr/bin/env python3
# SessionStart hook: surfaces decisions from the prior session that should
# have generated a lesson but didn't. Non-blocking — prints to stderr only.
#
# Trigger keywords: redelegation, revise, blocked, failure, root cause

import json
import os
import sqlite3
import sys
import textwrap

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))
TRIGGER_KEYWORDS = ("redelegation", "revise", "blocked", "failure", "root cause")


def find_decisions_without_lessons(
    conn: sqlite3.Connection, session_id: str
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, title, rationale, context
        FROM decisions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchall()

    results = []
    for dec_id, title, rationale, context in rows:
        combined = " ".join(filter(None, [rationale or "", context or ""])).lower()
        if not any(kw in combined for kw in TRIGGER_KEYWORDS):
            continue
        # Check if a lesson already references this decision.
        lesson_exists = conn.execute(
            "SELECT 1 FROM lessons WHERE source_decision_id = ? LIMIT 1",
            (dec_id,),
        ).fetchone()
        if lesson_exists:
            continue
        results.append(
            {
                "id": dec_id,
                "title": title,
                "rationale": rationale or "",
                "context": context or "",
            }
        )
    return results


def truncate_words(text: str, max_words: int = 80) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + " ..."


def main() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return 0

    try:
        # Find the most recently ended session.
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return 0
        prior_session_id = row[0]

        decisions = find_decisions_without_lessons(conn, prior_session_id)
        if not decisions:
            return 0

        lines = [
            f"\n[lesson-harvester] {len(decisions)} decision(s) from the prior session "
            f"({prior_session_id}) match failure/revise/blocked keywords but have no lesson yet.",
            "  Consider adding lessons with:",
        ]
        for d in decisions:
            body_source = truncate_words(d["rationale"] or d["context"], 80)
            lines.append(
                f"\n  python3 .memory/log.py lesson add \\\n"
                f"    --trigger redelegation \\\n"
                f"    --title \"Lesson from {d['id']}: {d['title'][:60]}\" \\\n"
                f"    --body \"{body_source}\" \\\n"
                f"    --source-decision-id {d['id']}"
            )

        print("\n".join(lines), file=sys.stderr)
    except sqlite3.Error:
        pass
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

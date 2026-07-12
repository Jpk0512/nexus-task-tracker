#!/usr/bin/env python3
"""PreCompact hook: re-inject the durable Nexus invariants before compaction.

Auto-compaction summarises the system prompt and early turns away, which silently
drops the orchestrator's role, the Constitution headings, the broker dispatch
ritual, and which tasks are mid-flight (SOTA 3.6/3.7 — position-aware anchoring +
post-compaction re-injection). This hook re-emits that durable set as
PreCompact `additionalContext` so it survives every compaction pass.

The verbatim NEXUS INVARIANTS digest (.claude/INVARIANTS.md) is re-emitted FIRST
and UNMODIFIED — the SAME single canonical file SessionStart (inject-invariants.sh)
and the UserPromptSubmit re-injection (context-reset-monitor.py) read, so the
protected HARD-RULE set never drifts across the three injection paths (SOTA 3.7:
one canonical source, verbatim; NoLiMa 2502.05167: never paraphrase load-bearing
tokens). The dynamic role/Constitution/tasks block is APPENDED after it.

The Constitution article HEADINGS are read DYNAMICALLY from docs/CONSTITUTION.md
(grep of the `^## Article` lines at runtime) so a newly added article is picked
up automatically — the list is never hardcoded. Live open tasks are read from
.memory/project.db (the same sqlite pattern context-reset-monitor.py uses).

Hook protocol: exit 0 always (advisory). Output is the nested
{"hookSpecificOutput": {"hookEventName": "PreCompact", "additionalContext": ...}}
shape via json.dumps — never a flat string.
"""

import contextlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = _REPO_ROOT / ".memory" / "project.db"
CONSTITUTION_PATH = _REPO_ROOT / "docs" / "CONSTITUTION.md"
# Single canonical digest source — shared verbatim with inject-invariants.sh
# (SessionStart) and context-reset-monitor.py (UserPromptSubmit re-injection).
INVARIANTS_PATH = _REPO_ROOT / ".claude" / "INVARIANTS.md"

# Override paths for tests.
_db_path_override = os.environ.get("_HOOK_DB_PATH")
if _db_path_override:
    DB_PATH = Path(_db_path_override)
_constitution_override = os.environ.get("_HOOK_CONSTITUTION_PATH")
if _constitution_override:
    CONSTITUTION_PATH = Path(_constitution_override)
_invariants_override = os.environ.get("_HOOK_INVARIANTS_PATH")
if _invariants_override:
    INVARIANTS_PATH = Path(_invariants_override)

ROLE_LINE = (
    "You are **Nexus**, the orchestrating agent. You PLAN, DELEGATE, VERIFY. "
    "You do NOT write code (disallowedTools: Write, Edit, NotebookEdit)."
)

BROKER_REMINDER = (
    "Broker dispatch ritual (survives compaction): before ANY Task, "
    "validate -> ping -> dispatch. Call nexus_validate_brief_tool, then "
    "nexus_notepad_ping (after notepad list), then Task — within a 120s "
    "freshness window or re-validate. Gate is FAIL-CLOSED: missing/malformed/"
    "unreadable broker_state.json → DENY (exit 2). Set "
    "NEXUS_BROKER_ALLOW_DEGRADED=1 to bypass (LOUD warning every turn)."
)


def _read_invariants_digest() -> str:
    """Return the verbatim NEXUS INVARIANTS digest, or '' if unreadable.

    Read UNMODIFIED from the single canonical .claude/INVARIANTS.md so this
    PreCompact copy is byte-identical to the SessionStart and UserPromptSubmit
    copies (NoLiMa: load-bearing tokens — DEC ids, file paths, markers — must
    never drift). A missing/unreadable digest is a wiring bug, not a place to
    improvise a paraphrase: return '' and fall back to the dynamic block alone.
    """
    try:
        return INVARIANTS_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_article_headings() -> list[str]:
    """Return the Constitution `## Article ...` heading lines, read dynamically."""
    try:
        text = CONSTITUTION_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^## Article\b", line)
    ]


def _read_open_tasks() -> list[str]:
    """Return 'id — title' for in_progress tasks; empty on any DB/schema problem."""
    if not DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, title FROM tasks WHERE status = 'in_progress' "
                "ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [f"{row['id']} — {row['title']}" for row in rows]


def _build_context() -> str:
    parts: list[str] = []

    # The verbatim INVARIANTS digest leads, UNMODIFIED — the durable HARD-RULE
    # set at the recency edge of the freshly compacted context. The dynamic
    # role/Constitution/tasks block follows it (re-grounding on live state).
    digest = _read_invariants_digest()
    if digest:
        parts.append(digest)
        parts.append("")

    parts.append("=== NEXUS INVARIANTS (re-injected on compaction — obey over compacted history) ===")
    parts.append("")
    parts.append(f"ROLE: {ROLE_LINE}")
    parts.append("")

    headings = _read_article_headings()
    if headings:
        parts.append("CONSTITUTION (headings — full text in docs/CONSTITUTION.md):")
        parts.extend(f"  {h}" for h in headings)
    else:
        parts.append(
            "CONSTITUTION: docs/CONSTITUTION.md unreadable — re-read it manually."
        )
    parts.append("")

    tasks = _read_open_tasks()
    if tasks:
        parts.append("OPEN IN-PROGRESS TASKS (re-ground against these):")
        parts.extend(f"  - {t}" for t in tasks)
    else:
        parts.append("OPEN IN-PROGRESS TASKS: none recorded (verify via log.py / TaskList).")
    parts.append("")

    parts.append(f"DISPATCH: {BROKER_REMINDER}")
    parts.append("")
    parts.append(
        "POST-COMPACTION: this digest re-injects role + Constitution headings + the "
        "dispatch ritual + live tasks — NOT full file bodies. Manually re-read any "
        "file you are about to delegate against, and re-read .memory/files/"
        "session_state.md + progress.md before acting on stale context."
    )
    return "\n".join(parts)


def main() -> None:
    # Read stdin for hook-protocol compliance; a parse failure must not block.
    with contextlib.suppress(json.JSONDecodeError, ValueError):
        json.load(sys.stdin)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": _build_context(),
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()

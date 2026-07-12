#!/usr/bin/env python3
# PostToolUse hook: captures a snapshot row whenever a doc-critical file is
# edited (docs/features/*, docs/CONSTITUTION.md, docs/DECISIONS.md).
# Non-blocking — records only.
#
# ADR-001 Phase 0: appends to .memory/files/reflection_snapshot.jsonl instead
# of INSERTing into project.db — fire-and-forget telemetry (no gate reads
# this back synchronously), so a durable JSONL journal replaces the old raw
# sqlite3 INSERT + schema-init DDL entirely.

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Install-time substitution renders /Users/john.keeney/nexus-task-tracker. Tests (and a runtime
# sanity check) can override via the _HOOK_INSTALL_ROOT env var. KEEP the literal
# /Users/john.keeney/nexus-task-tracker as the default so render_template still substitutes it.
REPO = os.environ.get("_HOOK_INSTALL_ROOT", "/Users/john.keeney/nexus-task-tracker")
FILES_DIR = Path(REPO) / ".memory" / "files"
JOURNAL_PATH = FILES_DIR / "reflection_snapshot.jsonl"

WATCHED_PATTERNS = (
    re.compile(r"docs/features/"),
    re.compile(r"docs/CONSTITUTION\.md$"),
    re.compile(r"docs/DECISIONS\.md$"),
)

MIN_LINE_DIFF = 5


def _append_journal(row):
    """Fire-and-forget JSONL append (ADR-001 Phase 0). Returns an error
    string on failure (never raises) — mirrors the old sqlite3.Error swallow
    so this hook still never blocks.
    """
    try:
        FILES_DIR.mkdir(parents=True, exist_ok=True)
        with open(JOURNAL_PATH, "a") as fh:
            fh.write(json.dumps(row) + "\n")
        return None
    except OSError as exc:
        return str(exc)


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


def _emit_unrendered_warning() -> None:
    """The install-time /Users/john.keeney/nexus-task-tracker token was never rendered. This hook
    would otherwise silently no-op (FILES_DIR points at a literal-token path
    that does not exist), so doc-critical edits would never be snapshotted.
    Fail SAFE (do not block the edit) but LOUD: emit a nested additionalContext
    warning naming the unrendered token so the orchestrator notices the hook
    is inert."""
    ctx = (
        "[reflection-capture] WARNING — the install-time /Users/john.keeney/nexus-task-tracker token was "
        "never rendered, so this PostToolUse hook cannot locate .memory/files/ and "
        "is silently NOT recording reflection snapshots of doc-critical edits. Re-run "
        "the Nexus install/render step (or set _HOOK_INSTALL_ROOT) to restore capture."
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": ctx,
            }
        },
        sys.stdout,
    )
    print(ctx, file=sys.stderr)


def main() -> int:
    # Unrendered install token: fail SAFE + LOUD instead of silent no-op.
    if REPO.startswith("__") and REPO.endswith("__"):
        _emit_unrendered_warning()
        return 0

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

    _append_journal({
        "session_id": session_id,
        "file_path": rel_path,
        "action_type": action_type,
        "one_line_summary": summary,
        "captured_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    })

    return 0


if __name__ == "__main__":
    sys.exit(main())

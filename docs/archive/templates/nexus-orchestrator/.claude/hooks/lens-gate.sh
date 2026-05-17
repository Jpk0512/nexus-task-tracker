#!/usr/bin/env python3
# SubagentStop hook: enforces Lens-before-done for implementing agents.
#
# Contract Rule 17: Forge / Pipeline / Hermes / Atlas returning NEXUS:DONE
# with files_changed touching source paths must have a Lens validation row
# in validation_log written within the last hour for the same task hash.
#
# Returns exit 2 (block) or exit 0 (pass/skip).
#
# Env vars (with defaults):
#   DB_PATH              — path to project.db (default: <cwd>/.memory/project.db)
#   GATED_SOURCE_PATHS   — comma-separated source path prefixes to gate
#                          (default: app/,src/,lib/)

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get(
    "_HOOK_DB_PATH",
    os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db")),
)

# Agents that must pass through Lens before NEXUS:DONE is accepted.
GATED_AGENTS = frozenset({"forge", "pipeline", "hermes", "atlas"})

# Source paths that trigger the gate when listed in files_changed.
# Read from env var, defaulting to generic source directories.
_gated_paths_raw = os.environ.get("GATED_SOURCE_PATHS", "app/,src/,lib/")
GATED_PATH_PREFIXES = tuple(
    p.strip() for p in _gated_paths_raw.split(",") if p.strip()
)

MARKER_RE = re.compile(
    r"##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)", re.IGNORECASE
)

VALIDATION_WINDOW = timedelta(hours=1)


def _init_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS validation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            agent_validated     TEXT NOT NULL,
            target_agent        TEXT NOT NULL,
            task_or_brief_hash  TEXT NOT NULL,
            verdict             TEXT NOT NULL,
            evidence_summary    TEXT,
            validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_target
            ON validation_log(target_agent, validated_at DESC)
    """)
    conn.commit()


def _parse_files_changed(text: str) -> list[str]:
    """Extract files_changed list from the first JSON block in the agent response."""
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        fc = obj.get("files_changed")
        if isinstance(fc, list) and all(isinstance(x, str) for x in fc):
            return fc
    return []


def _touches_source(files: list[str]) -> bool:
    """Return True if any path in files falls under a gated source directory."""
    for f in files:
        # Normalise: strip leading ./ or /
        norm = f.lstrip("./")
        for prefix in GATED_PATH_PREFIXES:
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return True
    return False


def _derive_task_hash(payload: dict, assistant_text: str) -> str:
    """Produce a stable hash that Lens can reproduce when it calls `validation add`.

    Priority: explicit task_id > task_description > brief hash from assistant text.
    Nexus embeds task_id in the delegation payload when it exists.
    """
    task_id: str = (
        payload.get("task_id")
        or payload.get("tool_input", {}).get("task_id")
        or ""
    )
    task_desc: str = (
        payload.get("task_description")
        or payload.get("tool_input", {}).get("description")
        or os.environ.get("CLAUDE_TASK_DESCRIPTION", "")
        or ""
    )
    raw = task_id or task_desc or assistant_text[:500]
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _has_lens_validation(
    conn: sqlite3.Connection,
    target_agent: str,
    task_hash: str,
) -> bool:
    """Return True if Lens logged a validation row within the past hour."""
    cutoff = (datetime.now(timezone.utc) - VALIDATION_WINDOW).isoformat()
    row = conn.execute(
        """
        SELECT id FROM validation_log
        WHERE agent_validated = 'lens'
          AND target_agent    = ?
          AND task_or_brief_hash = ?
          AND validated_at    > ?
        LIMIT 1
        """,
        (target_agent, task_hash, cutoff),
    ).fetchone()
    return row is not None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    if not assistant_text:
        return 0

    marker_match = MARKER_RE.search(assistant_text)
    if not marker_match:
        return 0

    marker = marker_match.group(1).upper()
    if marker != "DONE":
        # Only NEXUS:DONE triggers the gate; REVISE/BLOCKED/CHECKPOINT pass freely.
        return 0

    agent_name: str = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("tool_input", {}).get("subagent_type")
        or "unknown"
    ).lower()

    if agent_name not in GATED_AGENTS:
        return 0

    files_changed = _parse_files_changed(assistant_text)
    if not _touches_source(files_changed):
        # Pure-docs change or no source files listed — gate does not apply.
        return 0

    task_hash = _derive_task_hash(payload, assistant_text)

    try:
        conn = sqlite3.connect(DB_PATH)
        _init_table(conn)
        validated = _has_lens_validation(conn, agent_name, task_hash)
        conn.close()
    except sqlite3.Error:
        # DB unavailable — fail-safe, do not block.
        return 0

    if not validated:
        print(
            f"[lens-gate] BLOCK — {agent_name.capitalize()} NEXUS:DONE requires Lens "
            "validation first (CONTRACT.md Rule 17). Dispatch Lens before re-claiming done.\n"
            f"  Agent: {agent_name}\n"
            f"  Task hash: {task_hash}\n"
            f"  Files changed (source): {[f for f in files_changed if _touches_source([f])][:5]}\n"
            "  Lens must run: python3 .memory/log.py validation add "
            f"--agent lens --target {agent_name} --task-hash {task_hash} "
            "--verdict PASS|PARTIAL|FAIL --summary \"...\"",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())

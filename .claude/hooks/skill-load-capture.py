#!/usr/bin/env python3
"""PostToolUse hook (matcher: Skill) — event-sourced skills_loaded capture (R2-T15).

Per nexus-redesign/plans/03-r2e2-design-APPROVED.md §7 (FIX-2 corrected design):
a persona *claiming* `skills_loaded: [...]` in its return is DATA — unverifiable
and gameable. The `Skill` tool invocation is an event the harness observes; that
observed event is the only trustworthy signal. This hook is the observation
point: on every `Skill` tool call, it appends one row to the NEW `skill_load_events`
table (dispatch_id, skill_id, ts, byte_len) — a separate table from
dispatch_telemetry, per spec (N-per-dispatch vs one-row-per-dispatch; see
.memory/schema.sql comment above the CREATE TABLE for the full rationale).

CORRELATION KEY CAVEAT (read before assuming this is a real dispatch_id):
the harness does not always hand a PostToolUse:Skill hook a first-class
per-subagent dispatch identifier — dispatch_telemetry.dispatch_id is populated
later, from the orchestrator's own completion-notification parsing (see
dispatch-capture.py docstring). This hook prefers an explicit dispatch_id when
the payload carries one (tool_input.dispatch_id / top-level dispatch_id —
present on Task/Agent-shaped payloads, and what the guard's SubagentStop
shadow comparison keys its lookup on), and falls back to session_id only when
no dispatch_id is present. The session_id fallback is a known coarser
granularity than the schema's ideal (a session can span multiple dispatches)
— flagged here for whoever owns the R3 promotion so it is not silently
assumed to already be exact in that fallback case.

CLI CONTRACT (landed by pipeline-data this wave; verified against the live
`.memory/log.py` argparse tree — top-level command is `skill`, subcommand is
`record-load`, NOT a top-level `skill-load` command):
    python3 .memory/log.py skill record-load \
        --dispatch-id <dispatch_id or session_id fallback> \
        --skill-id <skill> --ts <iso8601> --byte-len <n>

If the subcommand is ever removed/renamed again, this hook still fails soft
(exit 0, no row written, no error surfaced) — it NEVER blocks the Skill call
and never duplicates the INSERT itself.

ADVISORY / SHADOW ONLY — this hook never denies. It has no PreToolUse gate
role; it only observes and records.

3.9 IMPORT-SAFETY — live runtime is >=3.11 via _py.sh, but the package twin
runs this file un-shimmed under ambient python3 (3.9). No 3.11-only idioms:
keep timezone.utc + # noqa: UP017, no datetime.UTC, no match/case, no
def-time X|None (from __future__ import annotations keeps PEP-604 safe).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent


def _repo_root() -> Path:
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _log_py(root: Path) -> Path:
    return root / ".memory" / "log.py"


def _extract_payload(data: dict) -> tuple[str, str, int]:
    """Return (skill_id, dispatch_id, byte_len) from the hook payload.

    tool_input carries {"skill": "<name>", "args": "..."} for the Skill tool.
    byte_len is measured from tool_response text when present; 0 if absent —
    the column is nullable in the schema for exactly this "not measured" case,
    but 0 is a safer subprocess argv value than an empty/None string.

    Correlation key: prefer an explicit dispatch_id if the payload carries one
    (tool_input.dispatch_id or top-level dispatch_id) — that is what the
    SubagentStop shadow-mode comparison in skills-required-guard.sh actually
    keys its lookup on. Fall back to session_id only when no dispatch_id is
    present, per the documented coarser-granularity caveat above.
    """
    tool_input = data.get("tool_input") or data.get("input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    skill_id = str(tool_input.get("skill") or tool_input.get("name") or "").strip()

    dispatch_id = str(
        tool_input.get("dispatch_id")
        or data.get("dispatch_id")
        or data.get("session_id")
        or data.get("sessionId")
        or "unknown"
    )

    tool_response = data.get("tool_response")
    byte_len = 0
    if isinstance(tool_response, dict):
        text = tool_response.get("text") or tool_response.get("content") or ""
        byte_len = len(str(text).encode("utf-8", errors="ignore"))
    elif isinstance(tool_response, str):
        byte_len = len(tool_response.encode("utf-8", errors="ignore"))

    return skill_id, dispatch_id, byte_len


def _record_skill_load(root: Path, dispatch_id: str, skill_id: str, byte_len: int) -> None:
    """Best-effort `log.py skill record-load`. Never raises; failure is swallowed.

    Still tolerant of the subcommand ever going missing/renamed again — argparse
    would exit nonzero with 'invalid choice', which subprocess.run swallows here
    same as any other failure.
    """
    log_py = _log_py(root)
    if not log_py.is_file():
        return
    cmd = [
        sys.executable,
        str(log_py),
        "skill",
        "record-load",
        "--dispatch-id",
        dispatch_id,
        "--skill-id",
        skill_id,
        "--ts",
        datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "--byte-len",
        str(byte_len),
    ]
    try:
        subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    skill_id, dispatch_id, byte_len = _extract_payload(data)
    if not skill_id:
        sys.exit(0)  # nothing observable — fail open, never block

    _record_skill_load(_repo_root(), dispatch_id, skill_id, byte_len)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)

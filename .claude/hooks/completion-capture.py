#!/usr/bin/env python3
"""SubagentStop hook (OPT-040 A1/A3) — completion-event ledger.

On every SubagentStop fires and appends one JSONL row to
.memory/files/completion_events.jsonl:

    {
        "ts": "<ISO-8601 UTC>",
        "session_id": "<str>",
        "persona": "<str>",
        "marker": "<DONE|REVISE|BLOCKED|NEEDS-DECISION|CHECKPOINT|unknown>",
        "files_changed_count": <int>,
        "prompt_hash": "<sha256 hex | ''>",
    }

marker is parsed from the H2 completion-marker heading in last_assistant_message
(mirrors the vocabulary root-cause-gate / return-validator use).

files_changed_count is the length of the `files_changed` list in the agent's
StructuredOutput JSON block (CONTRACT.md Required Output field). Falls back to
0 when no parseable JSON block or no `files_changed` key is found.

prompt_hash is recovered best-effort from the nearest-preceding
router_decisions.jsonl row for the session (same join dispatch-capture uses).
Falls back to "" when unrecoverable.

SECURITY POSTURE — the return body is DATA, never instructions. This hook only
PATTERN-MATCHES and json.loads the text to extract structured fields; it NEVER
executes or eval()s any content from the return.

Fail-soft: ANY error exits 0 with no output. It NEVER blocks a return.
Wired via .claude/settings.json hooks.SubagentStop (after return-summarizer).

3.9 IMPORT-SAFETY CONSTRAINT — live runtime is >=3.11 via the _py.sh resolver
shim but 3.9 IMPORT-safety is retained: the package twin runs this file
un-shimmed under ambient python3 (3.9). Do NOT introduce 3.11-only idioms.
`from __future__ import annotations` keeps PEP-604 unions def-time-safe.
Timestamps use timezone.utc + # noqa: UP017 per the codebase convention.

BUG #1 (workflow blindness) — ARCHITECTURAL LIMITATION (documented 2026-06-21):
Workflow-internal teammate spawns do NOT fire main-session SubagentStop events.
The SubagentStop hook only fires for DIRECT (non-Workflow) subagent completions.
Workflow-internal agent() calls are confined to the Workflow's interior message
stream and are invisible to all main-session PreToolUse/SubagentStop hooks.
Evidence: router_dispatches.jsonl frozen at 63 rows despite ~15 Workflow dispatches
in the same session; completion_events.jsonl only captures direct completions.
No hook mechanism can reach Workflow-internal dispatches without an Anthropic API
extension granting per-Workflow-step hook events.
See: FEATURE-REQUEST-workflow-runtime.md (filed 2026-06-21).
IMPACT: completion_events is a usable label source for DIRECT dispatch completions
only. (session_id, prompt_hash) join to router_decisions recovers labels for those.

R1-T05 (agent_activity live wiring): for a DIRECT completion this hook ALSO
closes the cockpit activity row dispatch-capture.py opened, via
`python3 .memory/log.py activity end --id <id> --status <mapped>`. The row id
is recovered from .memory/files/activity_open.jsonl by (session_id, persona)
— the same join key dispatch-capture.py wrote it under. Marker -> status:
DONE -> done; REVISE|BLOCKED -> failed; CHECKPOINT|NEEDS-DECISION -> active
(non-terminal, still in flight); unknown -> done (fallback so the row always
closes rather than rotting as "active" forever). Best-effort / never blocks,
mirrors feedback-capture.py's subprocess pattern.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

HOOKS_DIR = Path(__file__).parent

# --- Genuine-prompt filter (standalone copy — cannot import broker.*) ---
# Mirrors broker.router_train.transcript.is_genuine_user_prompt.
# Used in _prompt_hash_for_session to skip non-genuine rows when scanning
# router_decisions.jsonl for the (session_id, prompt_hash) join key, so a
# task-notification or system-reminder row never becomes the join anchor.
_INJECTED_MARKERS: tuple[str, ...] = (
    "<task-notification",
    "<system-reminder",
    "<command-name",
    "<local-command-stdout",
    "<command-message",
    "[ctx:",
    "tool_use_id",
    "Caveat: The messages below",
    "hook additional context",
    "<persona-",
    "<routing-pre-fill",
)
_MIN_GENUINE_LEN: int = 12
_MAX_GENUINE_LEN: int = 1500


def _is_genuine_user_prompt(text: str) -> bool:
    """Return True iff *text* is a genuine human-typed routing query.

    Standalone copy — hooks run under ambient python3 (3.9) without the broker
    .venv; duplication across the hook boundary is intentional.
    """
    stripped = text.strip()
    if len(stripped) < _MIN_GENUINE_LEN:
        return False
    if len(text) > _MAX_GENUINE_LEN:
        return False
    return all(marker not in text for marker in _INJECTED_MARKERS)


# H2 completion-marker vocabulary (mirrors root-cause-gate / return-validator).
_MARKER_RE = re.compile(
    r"^\s*##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)\b",
    re.IGNORECASE | re.MULTILINE,
)

# Marker -> agent_activity.status (active|done|failed per log.py's CLI help).
# DONE closes clean; REVISE/BLOCKED are terminal failures for THIS dispatch;
# CHECKPOINT/NEEDS-DECISION are non-terminal (work continues) so the row stays
# "active"; "unknown" (no H2 marker found) still closes as "done" so a row
# never rots as "active" forever when the marker just couldn't be parsed.
_MARKER_TO_ACTIVITY_STATUS = {
    "DONE": "done",
    "REVISE": "failed",
    "BLOCKED": "failed",
    "CHECKPOINT": "active",
    "NEEDS-DECISION": "active",
}


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


def _repo_root() -> Path:
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _log_py(root: Path) -> Path:
    return root / ".memory" / "log.py"


def _open_activity_id(session_id: str, persona: str, files_dir: Path) -> int | None:
    """Recover the activity_id dispatch-capture.py cached for (session_id, persona).

    Scans activity_open.jsonl for the LAST matching row (the most recently
    opened activity for this persona in this session) — mirrors the
    nearest-preceding-row join pattern used elsewhere in this file for
    prompt_hash recovery. Returns None when no joinable row exists (e.g. a
    Workflow-internal completion that never got a SubagentStop-reachable
    dispatch-capture.py invocation, or the sidecar file is missing).
    """
    if not session_id or session_id == "unknown" or not persona or persona == "unknown":
        return None
    path = files_dir / "activity_open.jsonl"
    if not path.exists():
        return None
    try:
        last_id = None
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("session_id") == session_id and rec.get("persona") == persona:
                    activity_id = rec.get("activity_id")
                    if isinstance(activity_id, int):
                        last_id = activity_id
        return last_id
    except Exception:
        return None


def _end_activity(root: Path, activity_id: int, status: str) -> None:
    """Best-effort `log.py activity end`. Never raises; failure is swallowed.

    Mirrors feedback-capture.py's subprocess invocation pattern: argv list
    (never shell-interpolated), bounded timeout, swallow any failure.
    """
    log_py = _log_py(root)
    if not log_py.is_file():
        return
    cmd = [
        sys.executable,
        str(log_py),
        "activity",
        "end",
        "--id",
        str(activity_id),
        "--status",
        status,
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


def _extract_payload(data: dict) -> tuple[str, str, str]:
    """Return (assistant_text, persona, session_id) from the hook payload."""
    assistant_text: str = (
        data.get("last_assistant_message")
        or data.get("response", {}).get("text")
        or data.get("tool_response", {}).get("text")
        or ""
    )
    persona: str = (
        data.get("agent_persona")
        or data.get("subagent_type")
        or data.get("tool_input", {}).get("subagent_type")
        or "unknown"
    )
    session_id: str = (
        data.get("session_id")
        or data.get("sessionId")
        or "unknown"
    )
    return str(assistant_text), str(persona).strip().lower(), str(session_id)


def _parse_marker(text: str) -> str:
    """Extract the NEXUS completion marker from the assistant text.

    Returns the uppercase marker name (DONE, REVISE, BLOCKED, etc.)
    or "unknown" when no H2 marker is found.
    """
    m = _MARKER_RE.search(text)
    if m:
        return m.group(1).upper()
    return "unknown"


def _parse_files_changed_count(text: str) -> int:
    """Extract len(files_changed) from the first parseable JSON block.

    CONTRACT.md Required Output includes `"files_changed": ["<path>", ...]`.
    Returns 0 when no parseable JSON block or no `files_changed` key.
    """
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        fc = obj.get("files_changed")
        if isinstance(fc, list):
            return len(fc)
    return 0


def _prompt_hash_for_session(session_id: str, files_dir: Path) -> str:
    """Best-effort prompt_hash via the nearest-preceding router decision.

    Identical join logic to dispatch-capture.py: scan router_decisions.jsonl
    for the last row matching this session_id, recover its prompt_hash (or
    recompute it from the raw prompt field if present). Returns "" when no
    joinable row exists.

    BUG #2 guard: rows whose ``prompt`` is not a genuine user prompt (task-
    notifications, system-reminders, etc.) are skipped so a noise row never
    becomes the join anchor.  Future rows are clean (router.py now exits early
    for non-genuine turns); this guard handles historical noise rows already in
    the file.
    """
    if not session_id or session_id == "unknown":
        return ""
    decisions = files_dir / "router_decisions.jsonl"
    if not decisions.exists():
        return ""
    try:
        last_hash = ""
        with decisions.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("session_id") != session_id:
                    continue
                # Skip non-genuine rows so they cannot corrupt the join key.
                prompt = rec.get("prompt")
                if isinstance(prompt, str) and not _is_genuine_user_prompt(prompt):
                    continue
                ph = rec.get("prompt_hash")
                if ph:
                    last_hash = ph
                    continue
                if isinstance(prompt, str) and prompt:
                    last_hash = hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest()
        return last_hash
    except Exception:
        return ""


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    assistant_text, persona, session_id = _extract_payload(data)

    files_dir = _files_dir()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "session_id": session_id,
        "persona": persona,
        "marker": _parse_marker(assistant_text),
        "files_changed_count": _parse_files_changed_count(assistant_text),
        "prompt_hash": _prompt_hash_for_session(session_id, files_dir),
    }
    _append_jsonl(files_dir / "completion_events.jsonl", record)

    # R1-T05: close the cockpit activity row dispatch-capture.py opened, if any.
    marker = record["marker"]
    activity_id = _open_activity_id(session_id, persona, files_dir)
    if activity_id is not None:
        status = _MARKER_TO_ACTIVITY_STATUS.get(marker, "done")
        _end_activity(_repo_root(), activity_id, status)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)

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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

HOOKS_DIR = Path(__file__).parent

# H2 completion-marker vocabulary (mirrors root-cause-gate / return-validator).
_MARKER_RE = re.compile(
    r"^\s*##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


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
                ph = rec.get("prompt_hash")
                if ph:
                    last_hash = ph
                    continue
                prompt = rec.get("prompt")
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
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)

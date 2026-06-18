"""broker_state.json read/write helpers."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

NOTEPAD_STALE_SECONDS = 300
TURN_STALE_SECONDS = 120


def _find_repo_root() -> Path:
    """Walk up from this file to find the repo root (.memory/ dir is the marker)."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    # Fallback: CWD (original behavior, works when run from repo root)
    return Path.cwd()


REPO_ROOT: Path = _find_repo_root()
STATE_PATH: Path = REPO_ROOT / ".memory" / "files" / "broker_state.json"


class BrokerState(TypedDict, total=False):
    turn_id: str
    approved: bool
    persona: str
    called_at: str  # ISO timestamp
    notepad_logged_at: str | None  # ISO timestamp, may be absent
    team_name: str  # populated by nexus_validate_brief when a team_name is supplied;
    # TASK-083: the validated brief's gate-relevant fields, persisted on approval
    # so the dispatch gates (broker-gate.py, skills-required-guard.sh) can read
    # them from state instead of re-parsing a JSON block out of the Agent prompt.
    # Single-source: nexus_validate_brief already saw the full brief, so it writes
    # the gate fields here once rather than forcing the orchestrator to re-embed a
    # full brief in every Agent prompt. broker-gate reads these FIRST and falls
    # back to prompt-JSON only when absent (back-compat).
    approved_brief: dict  # {task_tier, work_type, intent, skills_required}


def read_state() -> BrokerState:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(state: BrokerState) -> None:
    """Atomically write broker_state.json.

    A parallel-Workflow reader (broker-gate.py / read_state) can race the writer.
    A plain write_text truncates-then-writes, so a racing read can observe a torn,
    half-written file and fall back to {} — erasing notepad_logged_at / approved.
    Write to a temp file in the SAME directory (so os.replace stays a rename, not a
    cross-device copy) then os.replace() onto the target: an atomic rename on POSIX
    and Windows, so every reader sees the complete old file or the complete new one,
    never a partial.
    """
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(state, indent=2))
        os.replace(tmp_path, STATE_PATH)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def is_notepad_fresh(state: BrokerState) -> bool:
    """Returns True if notepad_logged_at is within NOTEPAD_STALE_SECONDS."""
    ts = state.get("notepad_logged_at")
    if not ts:
        return False
    try:
        logged = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return False
    # Normalize: if logged is naive (no tzinfo), attach UTC so arithmetic is safe.
    if logged.tzinfo is None:
        logged = logged.replace(tzinfo=UTC)
    now = datetime.now(tz=UTC)
    return (now - logged).total_seconds() < NOTEPAD_STALE_SECONDS

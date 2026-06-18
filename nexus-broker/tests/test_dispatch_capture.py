"""Tests for the PreToolUse dispatch sidecar (.claude/hooks/dispatch-capture.py).

T1 (PRIMARY ground-truth): every Agent-tool dispatch must append one row to
router_dispatches.jsonl recording the persona the orchestrator ACTUALLY
dispatched. These tests drive the live hook end-to-end as a subprocess (the way
it runs) and assert the sidecar row shape, the prompt_hash join convention, the
agent_type fallback, and fail-soft on a non-dispatch / persona-less payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_HOOK = REPO_ROOT / ".claude" / "hooks" / "dispatch-capture.py"


def _run_hook(payload: dict, files_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "_HOOK_MEMORY_FILES_DIR": str(files_dir)}
    proc = subprocess.run(
        [sys.executable, str(DISPATCH_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"hook exited {proc.returncode}; stderr={proc.stderr}"
    return proc


def _rows(files_dir: Path) -> list[dict]:
    path = files_dir / "router_dispatches.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_agent_dispatch_appends_label_row(tmp_path: Path) -> None:
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "scout", "description": "recon the build"},
        "session_id": "sess-abc",
    }
    _run_hook(payload, tmp_path)

    rows = _rows(tmp_path)
    assert rows, "no row written to router_dispatches.jsonl"
    rec = rows[-1]
    assert rec["session_id"] == "sess-abc"
    assert rec["dispatched_persona"] == "scout"
    assert rec["ts"], "ts must be populated"
    assert "prompt_hash" in rec


def test_prompt_hash_recovered_from_preceding_router_decision(tmp_path: Path) -> None:
    """prompt_hash joins on the nearest-preceding router decision for the session."""
    prompt = "investigate the failing build"
    expected_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    (tmp_path / "router_decisions.jsonl").write_text(
        json.dumps(
            {
                "session_id": "sess-join",
                "prompt": prompt,
                "prompt_hash": expected_hash,
                "decision": "prefill",
            }
        )
        + "\n"
    )

    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "lens"},
        "session_id": "sess-join",
    }
    _run_hook(payload, tmp_path)

    rec = _rows(tmp_path)[-1]
    assert rec["dispatched_persona"] == "lens"
    assert rec["prompt_hash"] == expected_hash


def test_agent_type_fallback_is_recorded(tmp_path: Path) -> None:
    """Agent/Team-shaped payloads carry the persona under agent_type."""
    payload = {
        "tool_name": "Agent",
        "input": {"agent_type": "pipeline-data"},
        "session_id": "sess-team",
    }
    _run_hook(payload, tmp_path)

    rec = _rows(tmp_path)[-1]
    assert rec["dispatched_persona"] == "pipeline-data"
    assert rec["session_id"] == "sess-team"


def test_no_persona_writes_nothing(tmp_path: Path) -> None:
    """A dispatch payload with no subagent_type/agent_type appends no row (fail-soft)."""
    payload = {
        "tool_name": "Agent",
        "tool_input": {"description": "no persona here"},
        "session_id": "sess-empty",
    }
    _run_hook(payload, tmp_path)
    assert _rows(tmp_path) == []


def test_non_dispatch_tool_writes_nothing(tmp_path: Path) -> None:
    """A non-dispatch tool name (e.g. Bash) is ignored."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"subagent_type": "scout"},
        "session_id": "sess-bash",
    }
    _run_hook(payload, tmp_path)
    assert _rows(tmp_path) == []

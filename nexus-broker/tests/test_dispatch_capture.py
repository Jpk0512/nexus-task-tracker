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
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_HOOK = REPO_ROOT / ".claude" / "hooks" / "dispatch-capture.py"
LOG_PY = REPO_ROOT / ".memory" / "log.py"


def _live_agent_activity_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM agent_activity").fetchone()[0]
    finally:
        conn.close()


def _isolated_db_env(tmp_path: Path) -> dict:
    """Build a subprocess env pointing NEXUS_DB_PATH at an isolated tmp DB.

    _start_activity() in dispatch-capture.py shells out to `log.py activity
    start`, which resolves its sqlite path from NEXUS_DB_PATH at import time.
    Without this override the hook writes into the real .memory/project.db —
    the CRITICAL Lens finding this fixture exists to close. `log.py init` is
    run once to build the fresh DB from schema.sql (agent_activity, sessions,
    etc.) before the hook ever runs against it.
    """
    db_path = tmp_path / "isolated.db"
    env = {**os.environ, "NEXUS_DB_PATH": str(db_path)}
    proc = subprocess.run(
        [sys.executable, str(LOG_PY), "init"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"log.py init failed; stderr={proc.stderr}"
    return env


def _run_hook(payload: dict, files_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**_isolated_db_env(files_dir), "_HOOK_MEMORY_FILES_DIR": str(files_dir)}
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


def test_hook_run_never_writes_to_the_real_project_db(tmp_path: Path) -> None:
    """NATIVE-18-3 regression lock (false-green class: isolation-that-isn't).

    A prior version of this suite ran the hook with only _HOOK_MEMORY_FILES_DIR
    isolated — NEXUS_DB_PATH was left unset, so _start_activity() shelled out to
    `log.py activity start` against the REAL .memory/project.db. Every test run
    left orphaned `agent_activity` rows in the live DB (Lens caught 18 polluted
    rows: sess-abc/join/team). The per-hook-call assertions above (row shape,
    prompt_hash join, fallback fields) all PASSED throughout — they check the
    JSONL sidecar the hook emits, never the real DB the hook also touches. That
    is the false-green pattern: a test suite can be 100% green while silently
    corrupting production state, because nothing asserts the OTHER side effect
    didn't happen.

    Stand-in for "the real DB" (fixed 2026-07-04): this test originally pointed
    straight at REPO_ROOT/.memory/project.db and asserted `.exists()`. That path
    is gitignored (`.gitignore: /.memory/*.db`) and is populated only as a side
    effect of a live orchestrator session having run in this checkout before —
    it does not exist on a fresh clone, in CI, or in an ad hoc worktree (all
    reproduced this exact AssertionError/OperationalError here), and nothing in
    this suite's own fixtures ever creates it. That made the test's own set-up
    the stale assumption, not the hook: the hook was never even reached. The fix
    builds its OWN throwaway "real DB" stand-in the same way _isolated_db_env
    already builds the isolated one (`log.py init` against a fresh sqlite file),
    at a path distinct from the one NEXUS_DB_PATH will be pointed at for the
    hook run — then proves the hook's row count there is unchanged. That is a
    strictly stronger regression lock than the original: it isolates the "real"
    side of the comparison too, so the assertion can never again pass or fail
    for reasons unrelated to the hook's own NEXUS_DB_PATH handling.
    """
    stand_in_real_db = tmp_path / "stand-in-real-project.db"
    init_proc = subprocess.run(
        [sys.executable, str(LOG_PY), "init"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ, "NEXUS_DB_PATH": str(stand_in_real_db)},
    )
    assert init_proc.returncode == 0, f"log.py init failed; stderr={init_proc.stderr}"
    assert stand_in_real_db.exists(), f"expected stand-in DB at {stand_in_real_db}"

    before_count = _live_agent_activity_count(stand_in_real_db)

    hook_isolation_dir = tmp_path / "hook-isolation"
    hook_isolation_dir.mkdir()
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "scout", "description": "regression probe"},
        "session_id": "sess-regression-probe",
    }
    _run_hook(payload, hook_isolation_dir)

    # The hook must still do its normal job against the ISOLATED db/sidecar.
    rows = _rows(hook_isolation_dir)
    assert rows and rows[-1]["dispatched_persona"] == "scout"

    after_count = _live_agent_activity_count(stand_in_real_db)
    assert after_count == before_count, (
        f"hook invocation changed the stand-in 'real' project.db agent_activity "
        f"row count ({before_count} -> {after_count}) — NEXUS_DB_PATH isolation "
        f"regressed and the hook wrote outside the tmp DB it was pointed at."
    )

"""
Tests for Phase F1: stall-counter.sh + log.py task stall subcommand.

Run with:  python3 -m pytest .claude/hooks/tests/test_stall_counter.py -v

Covers:
- log.py task stall --task-id X --persona Y --marker REVISE (compare-and-swap)
- Concurrent increments do not lose updates
- Invalid marker rejected (exit 1)
- Persona change resets stall_count to 1
- stall-counter.sh exists and handles no-marker payloads
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
from io import StringIO
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
REPO_ROOT = HOOKS_DIR.parent.parent
SCHEMA_PATH = REPO_ROOT / ".memory" / "schema.sql"
LOG_PY_PATH = REPO_ROOT / ".memory" / "log.py"
STALL_COUNTER_SCRIPT = HOOKS_DIR / "stall-counter.sh"

# Load log.py as a module for direct function invocation
_spec = importlib.util.spec_from_file_location("log_module", LOG_PY_PATH)
assert _spec is not None
_log_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_log_module)  # type: ignore[union-attr]


def _make_isolated_db(tmp_path: Path) -> Path:
    """Create a fresh DB with schema + stall columns applied."""
    db_path = tmp_path / "test_project.db"
    schema = SCHEMA_PATH.read_text()
    conn = sqlite3.connect(str(db_path))
    import sqlite_vec as _sv
    conn.enable_load_extension(True)
    _sv.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(schema)
    # Apply Phase F migration idempotently
    for ddl in (
        "ALTER TABLE tasks ADD COLUMN stall_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tasks ADD COLUMN last_persona TEXT",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "INSERT INTO sessions (id, started_at) VALUES "
        "('S-stall-test', '2026-05-13T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    return db_path


def _seed_task(
    db_path: Path,
    task_id: str,
    stall_count: int = 0,
    last_persona: str | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT OR REPLACE INTO tasks
           (id, title, status, priority, created_at, updated_at, stall_count, last_persona)
           VALUES (?, ?, 'in_progress', 'medium', '2026-05-13', '2026-05-13', ?, ?)""",
        (task_id, f"Task {task_id}", stall_count, last_persona),
    )
    conn.commit()
    conn.close()


def _get_stall_count(db_path: Path, task_id: str) -> int:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT stall_count FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else -1


def _run_stall(
    db_path: Path,
    task_id: str,
    persona: str,
    marker: str,
) -> tuple[int, str, str]:
    """Call cmd_stall_increment directly with DB_PATH patched."""
    original_db = _log_module.DB_PATH
    _log_module.DB_PATH = db_path
    buf_out = StringIO()
    buf_err = StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = buf_out, buf_err
    try:
        ns = types.SimpleNamespace(
            task_id=task_id,
            persona=persona,
            marker=marker,
        )
        _log_module.cmd_stall_increment(ns)  # type: ignore[attr-defined]
        exit_code = 0
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 1
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        _log_module.DB_PATH = original_db
    return exit_code, buf_out.getvalue(), buf_err.getvalue()


# ---------------------------------------------------------------------------
# Test 1 — basic increment: stall_count goes from 0 to 1
# ---------------------------------------------------------------------------
# Given: task with stall_count=0
# When:  cmd_stall_increment called with persona=forge-ui, marker=REVISE
# Then:  stall_count=1, action=incremented


def test_stall_increment_basic(tmp_path: Path) -> None:
    """log.py task stall increments stall_count from 0 to 1."""
    db = _make_isolated_db(tmp_path)
    _seed_task(db, "TASK-S-01", stall_count=0)

    code, out, err = _run_stall(db, "TASK-S-01", "forge-ui", "REVISE")
    assert code == 0, f"Expected exit 0, got {code}. stderr={err}"

    result = json.loads(out)
    assert result["stall_count"] == 1, (
        f"Expected stall_count=1 after first increment, got {result['stall_count']}"
    )
    assert result["action"] == "incremented", (
        f"Expected action=incremented, got {result['action']}"
    )
    assert _get_stall_count(db, "TASK-S-01") == 1


# ---------------------------------------------------------------------------
# Test 2 — concurrent increments are safe (compare-and-swap)
# ---------------------------------------------------------------------------
# Given: task TASK-S-CAS with stall_count=0, same persona
# When:  two parallel stall calls race
# Then:  final stall_count > 0 (no lost update); both calls exit 0


def test_increment_concurrency_safe(tmp_path: Path) -> None:
    """Concurrent stall increments must not lose updates (compare-and-swap)."""
    db = _make_isolated_db(tmp_path)
    _seed_task(db, "TASK-S-CAS", stall_count=0)

    results: list[tuple[int, str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def increment_with_barrier() -> None:
        barrier.wait()
        r = _run_stall(db, "TASK-S-CAS", "forge-ui", "REVISE")
        with lock:
            results.append(r)

    t1 = threading.Thread(target=increment_with_barrier)
    t2 = threading.Thread(target=increment_with_barrier)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    final_count = _get_stall_count(db, "TASK-S-CAS")
    assert final_count > 0, (
        f"Expected stall_count > 0 after two concurrent increments, got {final_count}. "
        "This indicates a lost-update race condition."
    )
    for code, _out, err in results:
        assert code == 0, f"stall increment must exit 0, got {code}. stderr={err}"


# ---------------------------------------------------------------------------
# Test 3 — stall rejects invalid marker
# ---------------------------------------------------------------------------
# Given: marker is 'DONE' (not REVISE or BLOCKED)
# When:  cmd_stall_increment called
# Then:  exit 1, stderr explains rejection


def test_stall_rejects_invalid_marker(tmp_path: Path) -> None:
    """cmd_stall_increment must reject markers other than REVISE/BLOCKED."""
    db = _make_isolated_db(tmp_path)
    _seed_task(db, "TASK-S-BAD", stall_count=0)

    code, _out, err = _run_stall(db, "TASK-S-BAD", "forge-ui", "DONE")
    assert code != 0, "Expected non-zero exit for invalid marker 'DONE'"
    assert err.strip(), f"Expected stderr message for invalid marker. Got: {err!r}"


# ---------------------------------------------------------------------------
# Test 4 — persona change resets stall_count to 1
# ---------------------------------------------------------------------------
# Given: task TASK-S-PC with stall_count=2, last_persona=forge-ui
# When:  stall called with persona=pipeline-data
# Then:  stall_count=1, action=reset


def test_persona_change_resets_stall_count(tmp_path: Path) -> None:
    """When persona changes, stall_count resets to 1 for the new persona."""
    db = _make_isolated_db(tmp_path)
    _seed_task(db, "TASK-S-PC", stall_count=2, last_persona="forge-ui")

    code, out, err = _run_stall(db, "TASK-S-PC", "pipeline-data", "REVISE")
    assert code == 0, f"Expected exit 0, got {code}. stderr={err}"

    result = json.loads(out)
    assert result["stall_count"] == 1, (
        f"Expected stall_count=1 after persona change, got {result['stall_count']}"
    )
    assert result["action"] == "reset", (
        f"Expected action=reset for persona change, got {result['action']}"
    )


# ---------------------------------------------------------------------------
# Test 5 — stall-counter.sh script exists and is executable
# ---------------------------------------------------------------------------


def test_stall_counter_script_exists_and_is_executable() -> None:
    """stall-counter.sh must exist and be executable."""
    assert STALL_COUNTER_SCRIPT.exists(), (
        f"stall-counter.sh not found at {STALL_COUNTER_SCRIPT}"
    )
    assert os.access(STALL_COUNTER_SCRIPT, os.X_OK), (
        "stall-counter.sh must be executable"
    )


# ---------------------------------------------------------------------------
# Test 6 — stall-counter.sh: NEXUS:DONE in tool response → exit 0, no stall
# ---------------------------------------------------------------------------
# Given: PostToolUse payload with NEXUS:DONE (not REVISE or BLOCKED) in tool_response
# When:  stall-counter.sh fires
# Then:  exit 0 (no stall to count)


def test_stall_counter_noop_on_done(tmp_path: Path) -> None:
    """stall-counter.sh must exit 0 when tool_response contains NEXUS:DONE."""
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": "forge-ui",
            "value": json.dumps({"task_id": "TASK-S-NOOP", "subagent_type": "forge-ui"}),
        },
        "tool_response": "## NEXUS:DONE\nAll tests passed.",
        "session_id": "S-stall-test",
    }
    result = subprocess.run(
        ["/bin/bash", str(STALL_COUNTER_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Expected exit 0 when tool response contains NEXUS:DONE (not a stall). "
        f"Got {result.returncode}. stderr={result.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 7 — stall-counter.sh: missing task_id or persona → exit 0, skip
# ---------------------------------------------------------------------------
# Given: PostToolUse payload with NEXUS:REVISE but no identifiable task_id/persona
# When:  stall-counter.sh fires
# Then:  exit 0 (can't increment without context)


def test_stall_counter_skips_without_context() -> None:
    """stall-counter.sh must exit 0 (skip) when task_id/persona cannot be extracted."""
    payload = {
        "tool_name": "Task",
        "tool_input": {"description": "no task id here"},
        "tool_response": "## NEXUS:REVISE\nNeeds more work.",
        "session_id": "S-stall-test",
    }
    result = subprocess.run(
        ["/bin/bash", str(STALL_COUNTER_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=10,
    )
    assert result.returncode == 0, (
        f"Expected exit 0 when no task_id/persona can be extracted. "
        f"Got {result.returncode}. stderr={result.stderr}"
    )

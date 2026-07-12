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

import contextlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
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

# log.py self-re-execs into the dedicated memory venv (python 3.12 + sqlite-vec)
# whenever it is loaded under an interpreter that cannot load the sqlite-vec C
# extension — see log.py:_bootstrap_reexec. Importing it IN-PROCESS under system
# python 3.9 therefore os.execv's the *pytest* process away mid-collection, which
# silently nukes the whole run. These direct-call unit tests call
# cmd_stall_increment against a sqlite-vec DB and so are 3.12-only BY DESIGN; the
# end-to-end tests below likewise force the venv interpreter (see _drive_revise).
# Mirror log.py's own re-exec predicate and skip the module (rather than letting
# the re-exec fire) on any interpreter where loading log.py would re-exec. Under
# uv/3.12 and under the memory venv this guard is a no-op and every assertion runs.
_MEMORY_VENV_PY = str(REPO_ROOT / ".memory" / ".venv" / "bin" / "python")


def _sqlite_vec_loadable() -> bool:
    """True iff this interpreter can load sqlite extensions AND import sqlite_vec.

    Identical capability gate to log.py:_sqlite_vec_capable — when it is False and
    we are not already the memory venv, importing log.py would os.execv re-exec.
    """
    try:
        _c = sqlite3.connect(":memory:")
        try:
            _c.enable_load_extension(True)
        finally:
            _c.close()
        import sqlite_vec  # noqa: F401
        return True
    except Exception:
        return False


if (
    os.path.realpath(sys.executable) != os.path.realpath(_MEMORY_VENV_PY)
    and not _sqlite_vec_loadable()
):
    pytest.skip(
        "log.py re-execs into the 3.12 memory venv (sqlite-vec) when imported under "
        "a system interpreter that cannot load the sqlite-vec extension; importing "
        "it in-process here would os.execv the pytest process. These direct-call "
        "tests are 3.12+sqlite-vec only by design — run under "
        "`cd nexus-broker && uv run pytest` or the memory venv.",
        allow_module_level=True,
    )

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
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute(ddl)
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


# ===========================================================================
# P3-01 — END-TO-END escalation tests
# ---------------------------------------------------------------------------
# These drive the REAL stall-counter.sh (not cmd_stall_increment directly)
# against an ISOLATED temp repo so the script's own LOG_PY resolution,
# heartbeat sourcing, fail-loud branches, and the 2/3-strike escalation are all
# exercised end-to-end. A temp .memory/log.py shim repoints DB_PATH at a seeded
# test DB; the script walks parents for .memory/log.py and finds the shim.
# ===========================================================================

# log.py shim: import the real module by path, repoint DB_PATH at the sibling
# test DB, then hand off to its main() (sys.argv already carries the subcommand).
_LOG_SHIM_SRC = '''#!/usr/bin/env python3
import importlib.util
import sys
from pathlib import Path

_REAL_LOG_PY = Path({real_log_py!r})
_TEST_DB = Path(__file__).parent / "project.db"

_spec = importlib.util.spec_from_file_location("_real_log", _REAL_LOG_PY)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_mod.DB_PATH = _TEST_DB
_mod.main()
'''


def _build_isolated_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Construct a temp mini-repo: .claude/hooks/ (real scripts) + .memory/ (DB + log.py shim).

    Returns (repo_root, seeded_db_path). The real stall-counter.sh and
    heartbeat-emitter.sh are COPIED in so the script-under-test is byte-identical
    to production; only log.py is shimmed to isolate the database.
    """
    repo = tmp_path / "repo"
    hooks = repo / ".claude" / "hooks"
    mem = repo / ".memory" / "files"
    hooks.mkdir(parents=True)
    mem.mkdir(parents=True)

    # Copy the real hook scripts verbatim.
    for name in ("stall-counter.sh", "heartbeat-emitter.sh"):
        dst = hooks / name
        dst.write_text((HOOKS_DIR / name).read_text())
        dst.chmod(0o755)

    # Seed an isolated DB next to the shim.
    db = _make_isolated_db(repo / ".memory")
    # _make_isolated_db writes test_project.db; the shim expects project.db.
    target_db = repo / ".memory" / "project.db"
    db.rename(target_db)

    # Drop the log.py shim.
    shim = repo / ".memory" / "log.py"
    shim.write_text(_LOG_SHIM_SRC.format(real_log_py=str(LOG_PY_PATH)))
    shim.chmod(0o755)

    return repo, target_db


def _revise_payload(task_id: str, persona: str) -> str:
    """A PostToolUse:Task payload that carries a NEXUS:REVISE return for task/persona."""
    return json.dumps(
        {
            "tool_name": "Task",
            "tool_input": {
                "subagent_type": persona,
                "value": json.dumps({"task_id": task_id, "subagent_type": persona}),
            },
            "tool_response": "## NEXUS:REVISE\nStill failing — needs another pass.",
            "session_id": "S-stall-test",
        }
    )


def _drive_revise(repo: Path, task_id: str, persona: str) -> subprocess.CompletedProcess:
    """Fire the real stall-counter.sh once with a REVISE payload, from / (CWD-agnostic)."""
    script = repo / ".claude" / "hooks" / "stall-counter.sh"
    env = os.environ.copy()
    # Force the venv interpreter so the shim's import of log.py is sqlite-vec
    # capable and never tries to re-exec into a missing interpreter.
    venv_bin = str(REPO_ROOT / ".memory" / ".venv" / "bin")
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["/bin/bash", str(script)],
        input=_revise_payload(task_id, persona),
        capture_output=True,
        text=True,
        env=env,
        cwd="/",  # prove no leaked relative-path dependency
        timeout=20,
    )


@pytest.fixture(scope="module")
def _venv_has_sqlite_vec() -> bool:
    """The integration tests need sqlite_vec to build the seeded schema."""
    try:
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Test 8 — three consecutive REVISE returns escalate: warn@2, block+ask@3
# ---------------------------------------------------------------------------
# Given: a freshly seeded task (stall_count=0) in an isolated repo
# When:  the REAL stall-counter.sh fires 3 times with NEXUS:REVISE for the same
#        persona, run from CWD=/
# Then:  call #1 → exit 0, no escalation JSON
#        call #2 → exit 0 + the warn hookSpecificOutput (stall_count=2)
#        call #3 → exit 2 + decision:block + askUserQuestion JSON


def test_three_revise_escalates_to_block_and_askuser(
    tmp_path: Path, _venv_has_sqlite_vec: bool
) -> None:
    """3 consecutive REVISE returns → warn at 2, block+askUserQuestion at 3 (exit 2)."""
    if not _venv_has_sqlite_vec:
        pytest.skip("sqlite_vec not importable in this interpreter")

    repo, _db = _build_isolated_repo(tmp_path)
    # NOTE: stall-counter.sh extracts the task id with the regex TASK-\d+ (the
    # canonical TASK-NNN form), so the seeded id MUST be numeric or the script
    # skips with skip-no-context. Using a non-numeric id silently no-ops.
    task_id, persona = "TASK-801", "forge-ui"
    _seed_task(repo / ".memory" / "project.db", task_id, stall_count=0)

    # --- call #1: first stall, count -> 1, no escalation ---
    r1 = _drive_revise(repo, task_id, persona)
    assert r1.returncode == 0, f"call#1 expected exit 0, got {r1.returncode}. stderr={r1.stderr}"
    assert "decision" not in r1.stdout, f"call#1 must not escalate. stdout={r1.stdout}"
    assert "askUserQuestion" not in r1.stdout

    # --- call #2: count -> 2, warn hookSpecificOutput, still exit 0 ---
    r2 = _drive_revise(repo, task_id, persona)
    assert r2.returncode == 0, f"call#2 expected exit 0, got {r2.returncode}. stderr={r2.stderr}"
    assert r2.stdout.strip(), f"call#2 must emit warn JSON. stderr={r2.stderr}"
    warn = json.loads(r2.stdout)
    assert "hookSpecificOutput" in warn, f"call#2 expected hookSpecificOutput. Got {warn}"
    hso2 = warn["hookSpecificOutput"]
    assert isinstance(hso2, dict), (
        f"SHAPE-1w: hookSpecificOutput must be a nested dict (not a string). Got: {hso2!r}"
    )
    assert "stall_count=2" in hso2.get("additionalContext", ""), (
        f"call#2 warn must cite stall_count=2 in additionalContext. Got: {hso2}"
    )
    assert "decision" not in warn, "call#2 must NOT block yet (count==2 is warn-only)"

    # --- call #3: count -> 3, block (exit 2) + escalation in hookSpecificOutput.additionalContext ---
    # Note: 'askUserQuestion' is NOT a real PostToolUse hook field — the harness silently
    # drops it. The escalation is surfaced via hookSpecificOutput.additionalContext (SHAPE-3 fix).
    r3 = _drive_revise(repo, task_id, persona)
    assert r3.returncode == 2, (
        f"call#3 expected exit 2 (block), got {r3.returncode}. "
        f"stdout={r3.stdout} stderr={r3.stderr}"
    )
    assert r3.stdout.strip(), f"call#3 must emit block JSON. stderr={r3.stderr}"
    block = json.loads(r3.stdout)
    assert block.get("decision") == "block", f"call#3 expected decision=block. Got {block}"
    assert "askUserQuestion" not in block, (
        "SHAPE-3: 'askUserQuestion' is not a real hook field — harness silently drops it. "
        f"Use hookSpecificOutput.additionalContext instead. Got keys: {list(block.keys())}"
    )
    hso = block.get("hookSpecificOutput", {})
    assert isinstance(hso, dict), f"call#3 hookSpecificOutput must be a nested dict. Got: {hso!r}"
    ctx = hso.get("additionalContext", "")
    assert task_id in ctx or "stall" in ctx.lower(), (
        f"call#3 additionalContext must describe the escalation. Got: {ctx!r}"
    )
    assert "3" in str(block.get("reason", "")) or "stall_count=3" in block.get("reason", ""), (
        f"call#3 reason should cite the count. Got: {block.get('reason')}"
    )

    # The DB must actually show count==3 (escalation is grounded in real state).
    assert _get_stall_count(repo / ".memory" / "project.db", task_id) == 3


# ---------------------------------------------------------------------------
# Test 9 — exactly the count==2 warn shape (regression guard on the warn branch)
# ---------------------------------------------------------------------------
# Given: a task pre-seeded at stall_count=1
# When:  one REVISE fires (count -> 2)
# Then:  exit 0 + warn JSON naming quill-<lang> RCA + the -pro variant


def test_warn_branch_names_rca_and_pro_variant(
    tmp_path: Path, _venv_has_sqlite_vec: bool
) -> None:
    """The count==2 warn must name the quill RCA persona and the -pro escalation variant."""
    if not _venv_has_sqlite_vec:
        pytest.skip("sqlite_vec not importable in this interpreter")

    repo, _db = _build_isolated_repo(tmp_path)
    # quill-py is used so the warn's quill-<lang> derivation has a non-empty
    # language (the 'py'/'ts' grep matches 'py'); a base persona like forge-ui
    # exposes a separate cosmetic gap in the warn string, out of scope here.
    task_id, persona = "TASK-802", "quill-py"
    # Pre-seed at 1 with the SAME persona so the next REVISE increments to 2.
    _seed_task(repo / ".memory" / "project.db", task_id, stall_count=1, last_persona=persona)

    r = _drive_revise(repo, task_id, persona)
    assert r.returncode == 0, f"warn branch expected exit 0, got {r.returncode}. stderr={r.stderr}"
    warn = json.loads(r.stdout)
    hso = warn["hookSpecificOutput"]
    assert isinstance(hso, dict), (
        f"SHAPE-1w: hookSpecificOutput must be a nested dict (not a string). Got: {hso!r}"
    )
    ctx = hso["additionalContext"]
    assert "stall_count=2" in ctx, f"expected stall_count=2 in warn. Got {ctx}"
    assert "quill-py" in ctx, f"warn must name the quill-<lang> RCA persona. Got {ctx}"
    assert "-pro" in ctx, f"warn must name the -pro escalation variant. Got {ctx}"


# ---------------------------------------------------------------------------
# Test 10 — fail-LOUD on an unknown task (no silent stall_count=0 default)
# ---------------------------------------------------------------------------
# Given: a REVISE return for a task that does NOT exist in the DB
# When:  stall-counter.sh fires (log.py task stall exits 1 + stderr)
# Then:  exit 0 (non-blocking) BUT a WARNING hookSpecificOutput is emitted and
#        stderr carries the failure — the failure is NOT swallowed, and it is NOT
#        treated as a clean stall_count=0.


def test_unknown_task_fails_loud_not_silent(
    tmp_path: Path, _venv_has_sqlite_vec: bool
) -> None:
    """A failed stall call (unknown task) surfaces loudly and does NOT escalate or noop-silently."""
    if not _venv_has_sqlite_vec:
        pytest.skip("sqlite_vec not importable in this interpreter")

    repo, _db = _build_isolated_repo(tmp_path)
    # Numeric id (so the script's TASK-\d+ extraction matches and it actually
    # CALLS log.py) but deliberately NOT seeded — log.py exits 1 "task not found".
    r = _drive_revise(repo, "TASK-999", "forge-ui")

    assert r.returncode == 0, (
        f"unknown-task should not block the return, got {r.returncode}. stderr={r.stderr}"
    )
    # LOUD on stderr (un-swallowed): the underlying failure is visible.
    assert "not found" in r.stderr or "FAILED" in r.stderr or "rc=" in r.stderr, (
        f"the stall-call failure must surface on stderr. Got: {r.stderr!r}"
    )
    # And surfaced as additionalContext, explicitly flagged as a FAILED increment.
    warn = json.loads(r.stdout)
    hso = warn["hookSpecificOutput"]
    assert isinstance(hso, dict), (
        f"SHAPE-1: hookSpecificOutput must be a nested dict (not a string). Got: {hso!r}"
    )
    ctx = hso["additionalContext"]
    assert "FAILED" in ctx or "did NOT advance" in ctx, (
        f"the stall failure must be flagged in hookSpecificOutput.additionalContext, not silently dropped. Got: {ctx}"
    )


# ---------------------------------------------------------------------------
# Test 11 — CWD independence: heartbeat + log.py resolve from script location
# ---------------------------------------------------------------------------
# Given: the script run from CWD=/ with REPO_ROOT unset
# When:  a full 1->2->3 escalation drives through
# Then:  no ".memory/log.py not found" and no relative-path leakage in stderr;
#        the heartbeat file lands under the TEMP repo (not under / or CWD).


def test_no_leaked_paths_when_run_from_root(
    tmp_path: Path, _venv_has_sqlite_vec: bool
) -> None:
    """Run from / with REPO_ROOT unset: paths resolve from BASH_SOURCE, nothing leaks to CWD."""
    if not _venv_has_sqlite_vec:
        pytest.skip("sqlite_vec not importable in this interpreter")

    repo, _db = _build_isolated_repo(tmp_path)
    task_id, persona = "TASK-803", "pipeline-data"
    _seed_task(repo / ".memory" / "project.db", task_id, stall_count=0)

    # Explicitly strip REPO_ROOT so the old `${REPO_ROOT:-.}` fallback would have
    # resolved to ./.memory (i.e. /.memory under cwd=/) — proving the new
    # BASH_SOURCE-based resolution.
    saved = os.environ.pop("REPO_ROOT", None)
    try:
        r1 = _drive_revise(repo, task_id, persona)
        r2 = _drive_revise(repo, task_id, persona)
        r3 = _drive_revise(repo, task_id, persona)
    finally:
        if saved is not None:
            os.environ["REPO_ROOT"] = saved

    for label, r in (("1", r1), ("2", r2), ("3", r3)):
        assert "log.py not found" not in r.stdout, f"call#{label} could not resolve log.py: {r.stdout}"
        assert "log.py not found" not in r.stderr, f"call#{label} could not resolve log.py: {r.stderr}"
        assert "No such file or directory" not in r.stderr, (
            f"call#{label} leaked a missing relative path: {r.stderr}"
        )

    assert r3.returncode == 2, f"escalation must still fire from /, got {r3.returncode}. {r3.stderr}"

    # The heartbeat file must land inside the TEMP repo, resolved from BASH_SOURCE.
    hb = repo / ".memory" / "files" / "hook_heartbeat.jsonl"
    assert hb.exists(), "heartbeat must be written under the temp repo (BASH_SOURCE-resolved)"
    assert not Path("/.memory").exists(), "heartbeat must NOT leak to /.memory under cwd=/"

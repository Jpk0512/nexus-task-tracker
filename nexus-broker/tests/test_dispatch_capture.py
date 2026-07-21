"""Tests for the PreToolUse dispatch sidecar (.claude/hooks/dispatch-capture.py).

T1 (PRIMARY ground-truth): every Agent-tool dispatch must append one row to
router_dispatches.jsonl recording the persona the orchestrator ACTUALLY
dispatched. These tests drive the live hook end-to-end as a subprocess (the way
it runs) and assert the sidecar row shape, the prompt_hash join convention, the
agent_type fallback, and fail-soft on a non-dispatch / persona-less payload.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_HOOK = REPO_ROOT / ".claude" / "hooks" / "dispatch-capture.py"
COMPLETION_HOOK = REPO_ROOT / ".claude" / "hooks" / "completion-capture.py"
LOG_PY = REPO_ROOT / ".memory" / "log.py"
SCHEMA_SQL = REPO_ROOT / ".memory" / "schema.sql"
BROKER_ROOT = REPO_ROOT / "nexus-broker"


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


def _activity_open_rows(files_dir: Path) -> list[dict]:
    path = files_dir / "activity_open.jsonl"
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


# ── TASK-093 stage 2: task_id recovery + activity_open.jsonl relay ─────────
#
# `_extract_task_id` recovers `"task_id": "..."` from the dispatched brief
# JSON embedded in tool_input's prompt/description, and `_cache_open_activity`
# now caches it onto the SAME activity_open.jsonl row completion-capture.py
# joins on — closing the "dispatch_telemetry.task_id always NULL" gap.


def test_task_id_recovered_from_prompt_and_cached_to_activity_open(tmp_path: Path) -> None:
    """A dispatch whose `prompt` embeds a CONTRACT.md-shaped brief JSON with
    a `task_id` key gets that id cached onto its activity_open.jsonl row."""
    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "hermes",
            "prompt": (
                'BRIEF: {"agent_persona":"hermes","task_id":"TASK-093-capture",'
                '"work_type":"implementation"}'
            ),
        },
        "session_id": "sess-task-id",
    }
    _run_hook(payload, tmp_path)

    rows = _activity_open_rows(tmp_path)
    assert rows, "expected an activity_open.jsonl row"
    assert rows[-1]["task_id"] == "TASK-093-capture"


def test_task_id_recovered_from_description_when_prompt_has_none(tmp_path: Path) -> None:
    """`description` is checked when `prompt` carries no task_id — mirrors
    `_dispatch_task_label`'s own field preference order."""
    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "scout",
            "description": '{"task_id": "TASK-007"}',
            "prompt": "no brief json here",
        },
        "session_id": "sess-task-id-desc",
    }
    _run_hook(payload, tmp_path)

    rows = _activity_open_rows(tmp_path)
    assert rows[-1]["task_id"] == "TASK-007"


def test_task_id_absent_caches_none_never_fabricated(tmp_path: Path) -> None:
    """No task_id anywhere in the dispatch -> the cached row's task_id is
    None (JSON null), never a fabricated/guessed value."""
    payload = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "lens", "description": "review the diff"},
        "session_id": "sess-no-task-id",
    }
    _run_hook(payload, tmp_path)

    rows = _activity_open_rows(tmp_path)
    assert rows[-1]["task_id"] is None


# ── TASK-093 stage 3: completion-capture.py's daemon-first dispatch-telemetry
# bridge — `_record_dispatch_telemetry` now tries the daemon's
# `record_telemetry` RPC (per-consumer timeout pattern, `_daemon_rpc.py`)
# before falling back to the pre-existing `log.py dispatch record`
# subprocess. This file (not `.claude/hooks/tests/`) is the right home: only
# HERE does `sys.executable` resolve to the nexus-broker venv, so a real
# daemon (`python -m broker.daemon.server`) can actually be spawned.
#
# The daemon-first attempt is gated by completion-capture.py's own
# `_daemon_call_isolated()` (fires whenever `_HOOK_REPO_ROOT`/
# `_HOOK_MEMORY_FILES_DIR` is set — the SAME guard the pre-existing
# `record_event` RPC hops use) so every isolated subprocess-driven test in
# `.claude/hooks/tests/test_completion_capture.py` keeps exercising ONLY the
# subprocess fallback, byte-identical to before this change. Exercising the
# daemon-UP branch therefore requires calling `_record_dispatch_telemetry`
# directly (same-directory dynamic import, mirroring `_ping_shim.py`'s own
# test harness) with those two env vars unset, so `_daemon_call_isolated()`
# is False in-process while an explicit `root` param + an isolated
# `NEXUS_DAEMON_SOCKET_DIR` keep the RPC fully sandboxed.


def _load_completion_capture_module():
    spec = importlib.util.spec_from_file_location("_completion_capture_under_test", COMPLETION_HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _isolated_project(tmp_path: Path) -> Path:
    """A throwaway project with its OWN `.memory/log.py` + a fresh sqlite DB
    — both the daemon's `--project-path` and the fallback subprocess's
    `root` must be the SAME directory, so (unlike `_isolated_db_env`, which
    points `NEXUS_DB_PATH` at a DB elsewhere while `log.py` stays the real
    one) this copies `log.py`/`schema.sql` in so the directory is fully
    self-contained."""
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    shutil.copy(LOG_PY, project / ".memory" / "log.py")
    shutil.copy(SCHEMA_SQL, project / ".memory" / "schema.sql")
    init = subprocess.run(
        [sys.executable, str(project / ".memory" / "log.py"), "init"],
        cwd=str(project),
        capture_output=True,
        text=True,
        env={**os.environ, "NEXUS_DISABLE_VEC": "1"},
    )
    assert init.returncode == 0, f"isolated project init failed: {init.stderr}"
    return project


def _spawn_daemon(project_path: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "broker.daemon.server", "--project-path", str(project_path)],
        cwd=str(BROKER_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_daemon_health(daemon_rpc_module, root: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if daemon_rpc_module.call(root, "health", {}, 0.5) is not None:
            return
        time.sleep(0.05)
    raise AssertionError(f"daemon at {root} never became healthy")


def _query_dispatch_telemetry(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM dispatch_telemetry ORDER BY id").fetchall()]
    conn.close()
    return rows


def test_record_dispatch_telemetry_daemon_up_writes_telemetry_and_span_rows(
    tmp_path: Path, monkeypatch
) -> None:
    """Daemon reachable: the RPC accepts (the subprocess fallback never
    fires), a graceful SIGTERM shutdown flushes the telemetry row into the
    daemon's own project.db, and the daemon's dispatch->span bridge
    (server._emit_dispatch_span_from_telemetry) durably records a matching
    span in the same shutdown's span-store close."""
    sock_dir = Path(tempfile.mkdtemp(prefix="ccd-", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    monkeypatch.delenv("_HOOK_REPO_ROOT", raising=False)
    monkeypatch.delenv("_HOOK_MEMORY_FILES_DIR", raising=False)
    project = _isolated_project(tmp_path)
    module = _load_completion_capture_module()
    daemon_rpc = module._daemon_rpc_module()

    daemon = _spawn_daemon(project, {**os.environ, "NEXUS_DAEMON_SOCKET_DIR": str(sock_dir)})
    try:
        _wait_for_daemon_health(daemon_rpc, project)

        module._record_dispatch_telemetry(
            project,
            persona="hermes",
            model="sonnet",
            marker="DONE",
            tokens=1234,
            token_source="exact",
            tool_uses=3,
            duration_ms=5500,
            session_id="sess-daemon-up",
            task_id="TASK-093",
        )

        # Graceful shutdown: server.serve()'s finally-block flushes pending
        # telemetry AND closes the span-store's write connection — both
        # durable sinks are safe to read fresh right after the process exits.
        daemon.send_signal(signal.SIGTERM)
        daemon.wait(timeout=10)
    finally:
        with contextlib.suppress(ProcessLookupError):
            daemon.kill()
        daemon.wait(timeout=10)
        shutil.rmtree(sock_dir, ignore_errors=True)

    rows = _query_dispatch_telemetry(project / ".memory" / "project.db")
    assert len(rows) == 1, f"expected exactly one dispatch_telemetry row via the daemon path, got {rows}"
    row = rows[0]
    assert row["persona"] == "hermes"
    assert row["marker"] == "DONE"
    assert row["tokens"] == 1234
    assert row["token_source"] == "exact"
    assert row["session_id"] == "sess-daemon-up"
    assert row["task_id"] == "TASK-093"

    import duckdb

    spans_path = project / ".memory" / "spans.duckdb"
    assert spans_path.is_file(), "the daemon-up path must produce a durable spans.duckdb file"
    conn = duckdb.connect(str(spans_path), read_only=True)
    try:
        span_rows = conn.execute(
            "SELECT trace_id, name, kind, status FROM spans WHERE trace_id = ?", ["sess-daemon-up"]
        ).fetchall()
    finally:
        conn.close()
    assert span_rows == [("sess-daemon-up", "dispatch:hermes", "dispatch", "OK")]


def test_record_dispatch_telemetry_daemon_down_falls_back_never_bricks(
    tmp_path: Path, monkeypatch
) -> None:
    """No daemon reachable at all (empty, isolated socket dir — no socket
    file exists): the RPC attempt misses cleanly and the EXISTING `log.py
    dispatch record` subprocess fallback still writes the row —
    byte-identical to pre-TASK-093-stage-3 behavior. No span is ever
    written, because no daemon ever ran to bridge one."""
    sock_dir = Path(tempfile.mkdtemp(prefix="ccd-", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    monkeypatch.delenv("_HOOK_REPO_ROOT", raising=False)
    monkeypatch.delenv("_HOOK_MEMORY_FILES_DIR", raising=False)
    project = _isolated_project(tmp_path)
    module = _load_completion_capture_module()

    try:
        module._record_dispatch_telemetry(
            project,
            persona="scout",
            model="haiku",
            marker="REVISE",
            tokens=42,
            token_source="approx",
            tool_uses=None,
            duration_ms=None,
            session_id="sess-daemon-down",
            task_id=None,
        )  # never raises — the acceptance bar this proves ("exit 0" at the
        # hook-process level maps to "never raises" at this function level).
    finally:
        shutil.rmtree(sock_dir, ignore_errors=True)

    rows = _query_dispatch_telemetry(project / ".memory" / "project.db")
    assert len(rows) == 1, f"expected the fallback subprocess to write exactly one row, got {rows}"
    row = rows[0]
    assert row["persona"] == "scout"
    assert row["marker"] == "REVISE"
    assert row["tokens"] == 42
    assert row["token_source"] == "approx"
    assert row["session_id"] == "sess-daemon-down"

    assert not (project / ".memory" / "spans.duckdb").exists(), (
        "no daemon ever ran in this test -- a spans.duckdb file appearing here would mean "
        "the fallback path somehow still produced a span, contradicting the daemon-down contract"
    )

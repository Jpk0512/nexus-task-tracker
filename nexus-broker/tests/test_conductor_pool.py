"""Tests for R4-T01: broker.conductor.pool (claude -p worker pool) + the
empirical ramp protocol (broker.conductor.ramp).

No live Anthropic API call anywhere in this suite: the `claude` binary is
fully stubbed by a fake executable script whose behavior is switched via the
FAKE_CLAUDE_MODE env var. run_pool()'s internals are exercised directly in
the pool tests; the ramp() orchestration/stop-logic tests monkeypatch
run_pool for deterministic timings, except for one test that exercises the
real `dispatch record` subprocess path against a temp project.db, proving
the ramp protocol reuses the EXISTING R1-T01 dispatch_telemetry path rather
than inventing a new one.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from broker.conductor.pool import WorkerTask, run_pool, run_worker

pool_mod = importlib.import_module("broker.conductor.pool")
# broker.conductor's __init__ re-exports the `ramp` FUNCTION under the name
# `ramp`, shadowing the submodule attribute (same pattern as
# broker.router_train.aggregate) — import_module bypasses that shadowing to
# get the actual module object monkeypatch needs to patch.
ramp_mod = importlib.import_module("broker.conductor.ramp")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_PY = _REPO_ROOT / ".memory" / "log.py"

_FAKE_CLAUDE_SRC = '''#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
if mode == "fail":
    sys.stderr.write("boom: internal error\\n")
    sys.exit(1)
if mode == "ratelimit":
    sys.stderr.write("error: rate_limit_error - please retry later\\n")
    sys.exit(1)
if mode == "badjson":
    print("not json at all")
    sys.exit(0)
inner = {"score": 4, "reason": "ok"}
if mode == "fenced":
    inner_text = "```json\\n" + json.dumps(inner) + "\\n```"
else:
    inner_text = json.dumps(inner)
print(json.dumps({"result": inner_text, "model": "sonnet", "total_cost_usd": 0.001}))
'''


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text(_FAKE_CLAUDE_SRC)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _run_worker(fake_claude: Path, mode: str, task_id: str = "t1") -> pool_mod.WorkerResult:
    os.environ["FAKE_CLAUDE_MODE"] = mode
    try:
        task = WorkerTask(task_id=task_id, prompt="do the thing", cwd=str(_REPO_ROOT))
        return run_worker(task, claude_bin=str(fake_claude))
    finally:
        os.environ.pop("FAKE_CLAUDE_MODE", None)


# ---------------------------------------------------------------------------
# run_worker — the double-JSON-decode envelope contract (Skill sdk-workflow)
# ---------------------------------------------------------------------------


def test_run_worker_double_decodes_envelope(fake_claude: Path) -> None:
    result = _run_worker(fake_claude, "ok")
    assert result.ok is True
    assert result.payload == {"score": 4, "reason": "ok"}
    assert result.envelope["model"] == "sonnet"
    assert result.total_cost_usd == pytest.approx(0.001)


def test_run_worker_strips_markdown_fence_on_inner_payload(fake_claude: Path) -> None:
    result = _run_worker(fake_claude, "fenced")
    assert result.ok is True
    assert result.payload == {"score": 4, "reason": "ok"}


def test_run_worker_nonzero_rc_fails_loud(fake_claude: Path) -> None:
    result = _run_worker(fake_claude, "fail")
    assert result.ok is False
    assert "rc=1" in result.error
    assert "boom" in result.error


def test_run_worker_non_json_stdout_fails_loud(fake_claude: Path) -> None:
    result = _run_worker(fake_claude, "badjson")
    assert result.ok is False
    assert "non-JSON" in result.error


def test_run_worker_detects_rate_limit_signal(fake_claude: Path) -> None:
    result = _run_worker(fake_claude, "ratelimit")
    assert result.ok is False
    assert result.rate_limited is True


def test_build_argv_denies_all_tools_when_allowed_tools_empty() -> None:
    """R4-T06: an empty `allowed_tools` grant must produce an EXPLICIT
    `--tools=` deny-all, not an omitted flag — a real smoke probe with no
    flag at all let the CLI fall back to its permissive default and the
    worker went off-prompt doing a live web search instead of answering.
    Single-token `=`-form, not a separate `["--tools", ""]` pair: `--tools`
    is variadic and a separate empty-string token gets consumed alongside
    the NEXT argv token (the prompt itself), which a live probe confirmed
    fails with "Input must be provided ... as a prompt argument"."""
    task = WorkerTask(task_id="t3", prompt="x", cwd=".")
    argv = pool_mod._build_argv(task)
    assert "--tools=" in argv
    assert not any(a.startswith("--allowedTools") for a in argv)
    assert argv[-1] == "x"  # the prompt is the trailing positional, not swallowed


def test_run_worker_passes_per_worker_cwd_and_allowed_tools(
    fake_claude: Path, tmp_path: Path
) -> None:
    os.environ["FAKE_CLAUDE_MODE"] = "ok"
    try:
        task = WorkerTask(
            task_id="t2", prompt="x", cwd=str(tmp_path), allowed_tools=["Read", "Bash"]
        )
        argv = pool_mod._build_argv(task)
        assert "--allowedTools=Read,Bash" in argv
        assert argv[-1] == "x"  # the prompt is the trailing positional, not swallowed
        result = run_worker(task, claude_bin=str(fake_claude))
        assert result.ok is True
    finally:
        os.environ.pop("FAKE_CLAUDE_MODE", None)


# ---------------------------------------------------------------------------
# run_pool — N concurrent workers
# ---------------------------------------------------------------------------


def test_run_pool_spawns_n_workers(fake_claude: Path) -> None:
    os.environ["FAKE_CLAUDE_MODE"] = "ok"
    try:
        tasks = [WorkerTask(task_id=f"w{i}", prompt="x", cwd=str(_REPO_ROOT)) for i in range(4)]
        results = run_pool(tasks, max_workers=4, claude_bin=str(fake_claude))
        assert len(results) == 4
        assert all(r.ok for r in results)
        assert {r.task_id for r in results} == {f"w{i}" for i in range(4)}
    finally:
        os.environ.pop("FAKE_CLAUDE_MODE", None)


def test_run_pool_empty_tasks_returns_empty() -> None:
    assert run_pool([], max_workers=4) == []


# ---------------------------------------------------------------------------
# ramp() — the empirical ramp protocol (plan-13 SS9) + dispatch_telemetry reuse
# ---------------------------------------------------------------------------


def test_ramp_stops_on_failure_rate_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_run_pool(tasks, *, max_workers, claude_bin="claude"):
        calls.append(max_workers)
        return [
            pool_mod.WorkerResult(t.task_id, ok=False, duration_ms=100, error="boom")
            for t in tasks
        ]

    monkeypatch.setattr(ramp_mod, "run_pool", fake_run_pool)
    monkeypatch.setattr(ramp_mod, "_record_telemetry", lambda **kw: None)

    result = ramp_mod.ramp("prompt", levels=[1, 2, 4, 8])

    assert calls == [1]  # stopped after level 1 (100% failure rate > 5%)
    assert result["ceiling_n"] == 1
    assert result["levels"][0]["failure_rate"] == 1.0


def test_ramp_stops_when_wallclock_stops_improving(monkeypatch: pytest.MonkeyPatch) -> None:
    durations_by_level = {1: 100, 2: 150}  # got WORSE at N=2, not better

    def fake_run_pool(tasks, *, max_workers, claude_bin="claude"):
        d = durations_by_level[max_workers]
        return [pool_mod.WorkerResult(t.task_id, ok=True, duration_ms=d) for t in tasks]

    monkeypatch.setattr(ramp_mod, "run_pool", fake_run_pool)
    monkeypatch.setattr(ramp_mod, "_record_telemetry", lambda **kw: None)

    result = ramp_mod.ramp("prompt", levels=[1, 2, 4, 8])

    assert [row["n"] for row in result["levels"]] == [1, 2]
    assert result["ceiling_n"] == 1


def test_ramp_keeps_going_while_wallclock_improves(monkeypatch: pytest.MonkeyPatch) -> None:
    durations_by_level = {1: 400, 2: 200, 4: 100, 8: 100}  # flattens at N=8

    def fake_run_pool(tasks, *, max_workers, claude_bin="claude"):
        d = durations_by_level[max_workers]
        return [pool_mod.WorkerResult(t.task_id, ok=True, duration_ms=d) for t in tasks]

    monkeypatch.setattr(ramp_mod, "run_pool", fake_run_pool)
    monkeypatch.setattr(ramp_mod, "_record_telemetry", lambda **kw: None)

    result = ramp_mod.ramp("prompt", levels=[1, 2, 4, 8, 16])

    assert [row["n"] for row in result["levels"]] == [1, 2, 4, 8]
    assert result["ceiling_n"] == 4


def test_ramp_records_dispatch_telemetry_via_existing_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Proves the ramp protocol writes through the EXISTING R1-T01
    `dispatch record` CLI path, not a new schema/table."""
    db = tmp_path / "project.db"
    init = subprocess.run(
        [sys.executable, str(_LOG_PY), "init"],
        capture_output=True, text=True,
        env={**os.environ, "NEXUS_DB_PATH": str(db), "NEXUS_DISABLE_VEC": "1"},
    )
    assert init.returncode == 0, init.stderr

    def fake_run_pool(tasks, *, max_workers, claude_bin="claude"):
        return [pool_mod.WorkerResult(t.task_id, ok=True, duration_ms=50) for t in tasks]

    monkeypatch.setattr(ramp_mod, "run_pool", fake_run_pool)
    monkeypatch.setenv("NEXUS_DB_PATH", str(db))
    monkeypatch.setenv("NEXUS_DISABLE_VEC", "1")

    ramp_mod.ramp("prompt", levels=[1, 2])

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE task_id LIKE 'R4-T01-ramp-%' ORDER BY id"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert {r["task_id"] for r in rows} == {"R4-T01-ramp-n1", "R4-T01-ramp-n2"}
    assert all(r["persona"] == "pipeline-async" for r in rows)
    assert all(r["model"] == "ramp-probe" for r in rows)
    assert all("fail_rate=" in (r["marker"] or "") for r in rows)


def test_ramp_and_pool_modules_importable() -> None:
    import broker.conductor.pool  # noqa: F401
    import broker.conductor.ramp  # noqa: F401

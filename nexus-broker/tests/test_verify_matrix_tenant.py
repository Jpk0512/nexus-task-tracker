"""Tests for R4-T02: broker.conductor.verify_matrix — the W1 3-gate
verify-matrix conductor tenant + its harness-lane wall-clock baseline.

No live Anthropic API call anywhere in this suite: the `claude` binary is
fully stubbed by a fake executable script (same convention as
test_conductor_pool.py). The pool/run_worker plumbing is exercised for
real against the stub; `run_pool`/`run_worker` are monkeypatched only where
a test needs deterministic timings (median computation, run-count
enforcement) rather than real subprocess wall-clock. One test exercises the
real `dispatch record` subprocess path against a temp project.db, proving
both the tenant and the baseline write through the EXISTING R1-T01
dispatch_telemetry path rather than inventing a new one.
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

from broker.conductor.verify_matrix import (
    _GATES,
    _build_gate_tasks,
    capture_harness_baseline,
    run_verify_matrix_tenant,
)

vm_mod = importlib.import_module("broker.conductor.verify_matrix")
pool_mod = importlib.import_module("broker.conductor.pool")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_PY = _REPO_ROOT / ".memory" / "log.py"

_FAKE_CLAUDE_SRC = '''#!/usr/bin/env python3
import json, os, sys, time
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
if mode == "fail":
    sys.stderr.write("boom: internal error\\n")
    sys.exit(1)
print(json.dumps({"result": json.dumps({"score": 1}), "model": "sonnet", "total_cost_usd": 0.0}))
'''


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text(_FAKE_CLAUDE_SRC)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture(autouse=True)
def _clean_fake_claude_mode():
    yield
    os.environ.pop("FAKE_CLAUDE_MODE", None)


@pytest.fixture(autouse=True)
def _isolate_dispatch_telemetry_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4-T06 provenance fix: `_dispatch_record()` shells out to the REAL
    `.memory/log.py dispatch record` with no env override, so any test that
    calls `run_verify_matrix_tenant`/`capture_harness_baseline` without its
    own `NEXUS_DB_PATH` pollutes the LIVE project.db with stub-fast
    telemetry rows indistinguishable at a glance from real conductor
    timing — this is exactly what produced the 24 sub-second
    'R4-T02-verify-matrix-conductor' rows found in the live DB (all 6
    batches of 4 line up one-for-one with this file's 4 non-isolated
    conductor-dispatching test invocations). Default every test in this
    file to an isolated tmp DB; the one test that inspects written rows
    still does its own explicit `log.py init` + `NEXUS_DB_PATH`, which
    layers cleanly on top of (overrides) this default."""
    monkeypatch.setenv("NEXUS_DB_PATH", str(tmp_path / "_default_isolated_project.db"))
    monkeypatch.setenv("NEXUS_DISABLE_VEC", "1")


# ---------------------------------------------------------------------------
# the 3-gate matrix definition — disjoint per-leg scopes
# ---------------------------------------------------------------------------


def test_gates_have_disjoint_scopes() -> None:
    scopes = [g["scope"] for g in _GATES]
    assert len(scopes) == 3
    assert len(set(scopes)) == 3
    prefixes = [s.rstrip("*") for s in scopes]
    for i, a in enumerate(prefixes):
        for j, b in enumerate(prefixes):
            if i == j:
                continue
            assert not a.startswith(b), f"{scopes[i]!r} overlaps {scopes[j]!r}"


def test_build_gate_tasks_returns_one_task_per_gate() -> None:
    tasks = _build_gate_tasks(str(_REPO_ROOT))
    assert len(tasks) == 3
    assert {t.task_id for t in tasks} == {f"verify-matrix-{g['gate_id']}" for g in _GATES}
    assert len({t.task_id for t in tasks}) == 3


def test_build_gate_tasks_are_bounded_for_real_dispatch() -> None:
    """R4-T06: real `claude --print` smoke probes against the OLD prompt
    shape took 251-264s and ~$1-1.50 each, blew past the 120s timeout, and
    ignored the prompt entirely (one web-searched, one answered an
    unrelated topic). The fixed gate tasks must be timeout-bounded well
    under the old 120s default, must NOT grant any tool (pool._build_argv
    turns an empty `allowed_tools` into an explicit `--tools=` deny-all,
    single-token `=`-form so the variadic flag doesn't swallow the
    trailing prompt positional), must dispatch from a neutral cwd (not the
    repo root, whose CLAUDE.md/hooks hijacked two independent live smoke
    probes into off-prompt answers), and the prompt itself must ask for a
    short fixed-format token rather than open-ended reasoning."""
    tasks = _build_gate_tasks(str(_REPO_ROOT))
    for task in tasks:
        assert task.timeout_s == vm_mod._GATE_TIMEOUT_S
        assert task.timeout_s < 120.0
        assert task.allowed_tools == []
        assert task.cwd != str(_REPO_ROOT)
        assert not (Path(task.cwd) / ".claude").exists()
        assert not (Path(task.cwd) / "CLAUDE.md").exists()
        assert "PASS" in task.prompt
        assert "tool" in task.prompt.lower()
        argv = pool_mod._build_argv(task)
        assert "--tools=" in argv
        assert not any(a.startswith("--allowedTools") for a in argv)
        assert argv[-1] == task.prompt  # prompt is the trailing positional, not swallowed


# ---------------------------------------------------------------------------
# run_verify_matrix_tenant — the conductor lane (through the N02 pool)
# ---------------------------------------------------------------------------


def test_tenant_runs_3_gate_matrix_through_pool(fake_claude: Path) -> None:
    os.environ["FAKE_CLAUDE_MODE"] = "ok"
    result = run_verify_matrix_tenant(cwd=str(_REPO_ROOT), claude_bin=str(fake_claude))

    assert result["lane"] == "conductor"
    assert result["passed"] is True
    assert len(result["gates"]) == 3
    assert {g["gate_id"] for g in result["gates"]} == {g["gate_id"] for g in _GATES}
    assert all(g["ok"] for g in result["gates"])
    assert {g["scope"] for g in result["gates"]} == {g["scope"] for g in _GATES}


def test_tenant_reports_failure_when_a_gate_fails(fake_claude: Path) -> None:
    os.environ["FAKE_CLAUDE_MODE"] = "fail"
    result = run_verify_matrix_tenant(cwd=str(_REPO_ROOT), claude_bin=str(fake_claude))

    assert result["passed"] is False
    assert all(not g["ok"] for g in result["gates"])


def test_tenant_is_idempotent_and_re_runnable(fake_claude: Path) -> None:
    """N13 must be able to invoke the tenant repeatedly during the gate
    week: two back-to-back calls must both succeed with no shared state
    carried between them (each call recomputes its own gate batch)."""
    os.environ["FAKE_CLAUDE_MODE"] = "ok"
    first = run_verify_matrix_tenant(cwd=str(_REPO_ROOT), claude_bin=str(fake_claude))
    second = run_verify_matrix_tenant(cwd=str(_REPO_ROOT), claude_bin=str(fake_claude))

    assert first["passed"] is True
    assert second["passed"] is True
    assert first["gates"] != []
    assert second["gates"] != []


# ---------------------------------------------------------------------------
# capture_harness_baseline — the harness lane (sequential, >=20 runs)
# ---------------------------------------------------------------------------


def test_baseline_rejects_fewer_than_20_runs() -> None:
    with pytest.raises(ValueError, match="20"):
        capture_harness_baseline(runs=5)


def test_baseline_runs_at_least_20_and_computes_median(monkeypatch: pytest.MonkeyPatch) -> None:
    durations = iter([100, 200, 150] * 20)

    def fake_run_worker(task, *, claude_bin="claude"):
        return pool_mod.WorkerResult(task.task_id, ok=True, duration_ms=next(durations))

    monkeypatch.setattr(vm_mod, "run_worker", fake_run_worker)
    monkeypatch.setattr(vm_mod, "_dispatch_record", lambda **kw: None)

    import time as time_mod

    ticks = iter(float(i) for i in range(0, 100000, 1))
    monkeypatch.setattr(time_mod, "monotonic", lambda: next(ticks))

    result = capture_harness_baseline(runs=20)

    assert result["lane"] == "harness"
    assert result["run_count"] == 20
    assert len(result["durations_ms"]) == 20
    assert result["median_ms"] >= 0


def test_baseline_default_run_count_is_at_least_20(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_run_worker(task, *, claude_bin="claude"):
        calls["n"] += 1
        return pool_mod.WorkerResult(task.task_id, ok=True, duration_ms=10)

    monkeypatch.setattr(vm_mod, "run_worker", fake_run_worker)
    monkeypatch.setattr(vm_mod, "_dispatch_record", lambda **kw: None)

    result = capture_harness_baseline()

    assert result["run_count"] >= 20
    assert calls["n"] == result["run_count"] * 3  # 3 gates per run


# ---------------------------------------------------------------------------
# dispatch_telemetry persistence — reuses the EXISTING R1-T01 path
# ---------------------------------------------------------------------------


def test_tenant_and_baseline_record_via_existing_dispatch_telemetry_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_claude: Path
) -> None:
    db = tmp_path / "project.db"
    init = subprocess.run(
        [sys.executable, str(_LOG_PY), "init"],
        capture_output=True, text=True,
        env={**os.environ, "NEXUS_DB_PATH": str(db), "NEXUS_DISABLE_VEC": "1"},
    )
    assert init.returncode == 0, init.stderr

    monkeypatch.setenv("NEXUS_DB_PATH", str(db))
    monkeypatch.setenv("NEXUS_DISABLE_VEC", "1")
    os.environ["FAKE_CLAUDE_MODE"] = "ok"

    def fake_run_worker(task, *, claude_bin="claude"):
        return pool_mod.WorkerResult(task.task_id, ok=True, duration_ms=42)

    monkeypatch.setattr(vm_mod, "run_worker", fake_run_worker)

    run_verify_matrix_tenant(cwd=str(_REPO_ROOT), claude_bin=str(fake_claude))
    capture_harness_baseline(cwd=str(_REPO_ROOT), claude_bin=str(fake_claude), runs=20)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conductor_rows = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE task_id = 'R4-T02-verify-matrix-conductor'"
    ).fetchall()
    harness_run_rows = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE task_id = 'R4-T02-verify-matrix-harness-baseline'"
    ).fetchall()
    harness_summary_rows = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE task_id = 'R4-T02-verify-matrix-harness-baseline-summary'"
    ).fetchall()
    conn.close()

    assert len(conductor_rows) == 1
    assert conductor_rows[0]["persona"] == "pipeline-async"
    assert "passed=True" in (conductor_rows[0]["marker"] or "")

    assert len(harness_run_rows) == 20
    assert all(r["persona"] == "pipeline-async" for r in harness_run_rows)

    assert len(harness_summary_rows) == 1
    assert "BASELINE median_ms=" in (harness_summary_rows[0]["marker"] or "")


def test_verify_matrix_module_importable() -> None:
    import broker.conductor.verify_matrix  # noqa: F401

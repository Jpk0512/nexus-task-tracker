"""Tests for R4-T03/N34: broker.conductor.entry — the production conductor
entrypoint (`python -m broker.conductor run|tenant`, plans/14-cutover-
activation-plan.md SS4).

No live `claude` binary anywhere in this suite: claude legs are routed
through injected `dispatch_claude_fn` stubs (same convention as
test_conductor_dag.py) or a monkeypatched `broker.conductor.verify_matrix`
tenant function. Every test that exercises the journal-write path points
`journal_path`/`entry.append_run_record` at a tmp_path fixture — the REAL
`.memory/files/conductor_runs.jsonl` is never touched by this suite (the one
real, non-fixture run required as delivery evidence is executed separately,
outside pytest, per the N34 brief)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import broker.conductor.__main__ as main_mod
from broker.conductor import dag as dag_mod
from broker.conductor import entry


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _node(node_id: str = "solo") -> dict:
    return {
        "node_id": node_id,
        "depends_on": [],
        "downstream_consumers": [],
        "agent_persona": "pipeline-async",
        "goal": f"do the work for {node_id}",
        "context_files": [],
        "acceptance_criteria": [f"{node_id} completes"],
        "verification_method": {"type": "command", "command": f"echo {node_id}"},
        "risk_tier": "T1",
        "skills_required": ["agent-protocol"],
        "do_not_touch": [],
        "budget": "S",
        "irreversible": False,
    }


def _valid_dag_doc(dag_id: str = "test-dag") -> dict:
    return {"schema_version": 2, "dag_id": dag_id, "nodes": [_node()]}


def _invalid_dag_doc() -> dict:
    """Missing every required field but node_id -> a batch of 'missing-field' errors."""
    return {"schema_version": 2, "nodes": [{"node_id": "broken"}]}


def _write_dag(tmp_path: Path, doc: dict, name: str = "dag.yaml") -> Path:
    path = tmp_path / name
    # JSON is valid YAML - avoids hand-rolling YAML syntax for the fixture.
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("dispatch must not run when the DAG fails node-contract validation")


def _fake_dispatch_claude_factory(*, ok: bool = True):
    def fake(node, *, template, worker_id, claude_bin="claude"):
        telemetry = dag_mod.DispatchTelemetry(node["node_id"], "claude", ok, 5, worker_id)
        return dag_mod.NodeResult(node["node_id"], "claude", ok, worker_id, telemetry, payload={"ok": ok})

    return fake


def _read_journal_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


_EXPECTED_KEYS = {"run_id", "tenant", "status", "started_at", "wall_ms"}


@pytest.fixture(autouse=True)
def _conductor_enabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Availability gate (DEC-056) default-ENABLED for this whole module: every
    test above/below this fixture exercises DAG-validation/dispatch/journal
    logic, not the gate itself — the dedicated gate tests further down flip it
    off (or override it explicitly via `conductor_flag_path`) to prove the
    off-state. Zero changes to any pre-existing test body."""
    flag = tmp_path / "conductor.enabled"
    flag.touch()
    monkeypatch.setattr(entry, "_default_conductor_flag_path", lambda: flag)
    return flag


# ---------------------------------------------------------------------------
# run_dag_entry — pre-dispatch validation gate
# ---------------------------------------------------------------------------


def test_run_dag_entry_rejects_invalid_dag_before_dispatch(tmp_path: Path) -> None:
    dag_path = _write_dag(tmp_path, _invalid_dag_doc())
    journal_path = tmp_path / "conductor_runs.jsonl"

    with pytest.raises(dag_mod.DagValidationError) as excinfo:
        entry.run_dag_entry(
            dag_path, journal_path=journal_path,
            dispatch_claude_fn=_fail_if_called, dispatch_codex_fn=_fail_if_called,
        )
    assert any(e.code == "missing-field" for e in excinfo.value.errors)
    assert _read_journal_lines(journal_path) == []


# ---------------------------------------------------------------------------
# run_dag_entry — valid DAG dispatches + journals
# ---------------------------------------------------------------------------


def test_run_dag_entry_runs_valid_dag_and_appends_journal_line(tmp_path: Path) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc(dag_id="my-dag"))
    journal_path = tmp_path / "conductor_runs.jsonl"
    fake = _fake_dispatch_claude_factory(ok=True)

    outcome = entry.run_dag_entry(
        dag_path, journal_path=journal_path,
        dispatch_claude_fn=fake, dispatch_codex_fn=_fail_if_called,
    )
    assert outcome["status"] == "ok"

    lines = _read_journal_lines(journal_path)
    assert len(lines) == 1
    record = lines[0]
    assert set(record) == _EXPECTED_KEYS
    assert record["tenant"] == "my-dag"
    assert record["status"] == "ok"
    assert record["run_id"] == outcome["run_id"]
    assert isinstance(record["wall_ms"], int)
    assert isinstance(record["started_at"], str) and record["started_at"]


def test_run_dag_entry_falls_back_to_file_stem_when_dag_id_absent(tmp_path: Path) -> None:
    doc = _valid_dag_doc()
    del doc["dag_id"]
    dag_path = _write_dag(tmp_path, doc, name="unnamed-dag.yaml")
    journal_path = tmp_path / "conductor_runs.jsonl"

    outcome = entry.run_dag_entry(
        dag_path, journal_path=journal_path,
        dispatch_claude_fn=_fake_dispatch_claude_factory(ok=True), dispatch_codex_fn=_fail_if_called,
    )
    assert outcome["tenant"] == "unnamed-dag"


def test_run_dag_entry_journals_failed_status_on_node_failure(tmp_path: Path) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc())
    journal_path = tmp_path / "conductor_runs.jsonl"

    outcome = entry.run_dag_entry(
        dag_path, journal_path=journal_path,
        dispatch_claude_fn=_fake_dispatch_claude_factory(ok=False), dispatch_codex_fn=_fail_if_called,
    )
    assert outcome["status"] == "failed"
    lines = _read_journal_lines(journal_path)
    assert lines[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# append_run_record — append-only JSONL shape
# ---------------------------------------------------------------------------


def test_append_run_record_is_append_only(tmp_path: Path) -> None:
    journal_path = tmp_path / "conductor_runs.jsonl"
    entry.append_run_record(
        run_id="r1", tenant="t1", status="ok", started_at="2026-01-01T00:00:00+00:00",
        wall_ms=10, journal_path=journal_path,
    )
    entry.append_run_record(
        run_id="r2", tenant="t2", status="failed", started_at="2026-01-01T00:00:01+00:00",
        wall_ms=20, journal_path=journal_path,
    )
    lines = _read_journal_lines(journal_path)
    assert len(lines) == 2
    assert [r["run_id"] for r in lines] == ["r1", "r2"]
    assert set(lines[0]) == _EXPECTED_KEYS
    assert set(lines[1]) == _EXPECTED_KEYS


def test_append_run_record_creates_missing_parent_dirs(tmp_path: Path) -> None:
    journal_path = tmp_path / "nested" / "dir" / "conductor_runs.jsonl"
    entry.append_run_record(
        run_id="r1", tenant="t1", status="ok", started_at="2026-01-01T00:00:00+00:00",
        wall_ms=5, journal_path=journal_path,
    )
    assert journal_path.exists()


# ---------------------------------------------------------------------------
# run_verify_matrix_entry — the standing tenant job through the same entry
# ---------------------------------------------------------------------------


def test_run_verify_matrix_entry_wraps_tenant_and_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tenant(*, cwd, claude_bin, run_label):
        return {
            "lane": "conductor", "passed": True, "duration_ms": 321,
            "gates": [{"gate_id": "lint", "scope": "x", "ok": True, "duration_ms": 100}],
        }

    monkeypatch.setattr(entry.verify_matrix, "run_verify_matrix_tenant", fake_tenant)
    journal_path = tmp_path / "conductor_runs.jsonl"

    outcome = entry.run_verify_matrix_entry(journal_path=journal_path)
    assert outcome["status"] == "ok"
    assert outcome["wall_ms"] == 321

    lines = _read_journal_lines(journal_path)
    assert len(lines) == 1
    assert lines[0]["tenant"] == "verify-matrix"
    assert lines[0]["status"] == "ok"
    assert lines[0]["wall_ms"] == 321
    assert set(lines[0]) == _EXPECTED_KEYS


def test_run_verify_matrix_entry_journals_failed_on_gate_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tenant(*, cwd, claude_bin, run_label):
        return {"lane": "conductor", "passed": False, "duration_ms": 50, "gates": []}

    monkeypatch.setattr(entry.verify_matrix, "run_verify_matrix_tenant", fake_tenant)
    journal_path = tmp_path / "conductor_runs.jsonl"

    outcome = entry.run_verify_matrix_entry(journal_path=journal_path)
    assert outcome["status"] == "failed"
    assert _read_journal_lines(journal_path)[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# CLI: python -m broker.conductor run|tenant
# ---------------------------------------------------------------------------


def test_cli_run_rejects_invalid_dag_before_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    dag_path = _write_dag(tmp_path, _invalid_dag_doc())
    monkeypatch.setattr(entry, "append_run_record", _fail_if_called)
    monkeypatch.setattr(dag_mod.pool, "run_worker", _fail_if_called)

    rc = main_mod._cli(["run", str(dag_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "missing-field" in err


def test_cli_run_dispatches_valid_dag_and_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc(dag_id="cli-dag"))
    recorded: list[dict] = []

    def fake_append(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(entry, "append_run_record", fake_append)
    monkeypatch.setattr(
        dag_mod.pool, "run_worker",
        lambda task, *, claude_bin="claude": dag_mod.pool.WorkerResult(
            task.task_id, ok=True, duration_ms=5, payload={"ok": True},
        ),
    )

    rc = main_mod._cli(["run", str(dag_path)])
    assert rc == 0
    assert len(recorded) == 1
    assert recorded[0]["tenant"] == "cli-dag"
    assert recorded[0]["status"] == "ok"
    out = json.loads(capsys.readouterr().out)
    assert out["tenant"] == "cli-dag"
    assert out["status"] == "ok"


def test_cli_tenant_verify_matrix_journals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    recorded: list[dict] = []

    def fake_append(**kwargs):
        recorded.append(kwargs)

    def fake_tenant(*, cwd, claude_bin, run_label):
        return {"lane": "conductor", "passed": True, "duration_ms": 7, "gates": []}

    monkeypatch.setattr(entry, "append_run_record", fake_append)
    monkeypatch.setattr(entry.verify_matrix, "run_verify_matrix_tenant", fake_tenant)

    rc = main_mod._cli(["tenant", "verify-matrix"])
    assert rc == 0
    assert len(recorded) == 1
    assert recorded[0]["tenant"] == "verify-matrix"
    assert recorded[0]["status"] == "ok"
    out = json.loads(capsys.readouterr().out)
    assert out["tenant"] == "verify-matrix"


# ---------------------------------------------------------------------------
# module import surface — importing __main__ must never invoke the CLI
# ---------------------------------------------------------------------------


def test_main_module_importable_without_invoking_cli() -> None:
    import importlib

    reloaded = importlib.import_module("broker.conductor.__main__")
    assert callable(reloaded._cli)


def test_conductor_entry_module_importable() -> None:
    import broker.conductor.entry  # noqa: F401
    from broker.conductor import CONDUCTOR_RUNS_JOURNAL, run_dag_entry, run_verify_matrix_entry  # noqa: F401


# ---------------------------------------------------------------------------
# conductor.enabled availability gate (DEC-056) — SELECTIVE opt-in, never the
# default execution path. Both states proven at the entry-function level
# (direct `conductor_flag_path` override, no monkeypatch needed) and at the
# CLI chokepoint (`python -m broker.conductor run|tenant`), same
# no-real-subprocess dispatch-stub convention as every other test above.
# ---------------------------------------------------------------------------


def test_run_dag_entry_refuses_when_conductor_flag_absent(tmp_path: Path) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc())
    journal_path = tmp_path / "conductor_runs.jsonl"
    missing_flag = tmp_path / "missing" / "conductor.enabled"

    with pytest.raises(entry.ConductorDisabledError) as excinfo:
        entry.run_dag_entry(
            dag_path, journal_path=journal_path, conductor_flag_path=missing_flag,
            dispatch_claude_fn=_fail_if_called, dispatch_codex_fn=_fail_if_called,
        )
    assert excinfo.value.flag_path == missing_flag
    assert _read_journal_lines(journal_path) == []


def test_run_dag_entry_dispatches_when_conductor_flag_present(tmp_path: Path) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc(dag_id="present-dag"))
    journal_path = tmp_path / "conductor_runs.jsonl"
    present_flag = tmp_path / "conductor.enabled"
    present_flag.touch()

    outcome = entry.run_dag_entry(
        dag_path, journal_path=journal_path, conductor_flag_path=present_flag,
        dispatch_claude_fn=_fake_dispatch_claude_factory(ok=True), dispatch_codex_fn=_fail_if_called,
    )
    assert outcome["status"] == "ok"
    assert len(_read_journal_lines(journal_path)) == 1


def test_cli_run_refuses_when_conductor_flag_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc())
    # deliberately NOT tmp_path/"conductor.enabled" — the autouse fixture already
    # touches that exact path to default-enable every other test in this module.
    missing_flag = tmp_path / "off" / "conductor.enabled"
    monkeypatch.setattr(entry, "_default_conductor_flag_path", lambda: missing_flag)
    monkeypatch.setattr(entry, "append_run_record", _fail_if_called)
    monkeypatch.setattr(dag_mod.pool, "run_worker", _fail_if_called)

    rc = main_mod._cli(["run", str(dag_path)])

    assert rc == 2
    assert not missing_flag.exists()
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "conductor is disabled" in err
    assert "conductor.enabled" in err


def test_cli_run_dispatches_when_conductor_flag_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    dag_path = _write_dag(tmp_path, _valid_dag_doc(dag_id="gate-on-dag"))
    present_flag = tmp_path / "conductor.enabled"
    present_flag.touch()
    monkeypatch.setattr(entry, "_default_conductor_flag_path", lambda: present_flag)
    recorded: list[dict] = []

    def fake_append(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(entry, "append_run_record", fake_append)
    monkeypatch.setattr(
        dag_mod.pool, "run_worker",
        lambda task, *, claude_bin="claude": dag_mod.pool.WorkerResult(
            task.task_id, ok=True, duration_ms=5, payload={"ok": True},
        ),
    )

    rc = main_mod._cli(["run", str(dag_path)])

    assert rc == 0
    assert len(recorded) == 1
    assert recorded[0]["tenant"] == "gate-on-dag"


def test_cli_tenant_verify_matrix_refuses_when_conductor_flag_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    # deliberately NOT tmp_path/"conductor.enabled" — see comment in
    # test_cli_run_refuses_when_conductor_flag_absent above.
    missing_flag = tmp_path / "off" / "conductor.enabled"
    monkeypatch.setattr(entry, "_default_conductor_flag_path", lambda: missing_flag)
    monkeypatch.setattr(entry, "append_run_record", _fail_if_called)
    monkeypatch.setattr(entry.verify_matrix, "run_verify_matrix_tenant", _fail_if_called)

    rc = main_mod._cli(["tenant", "verify-matrix"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err

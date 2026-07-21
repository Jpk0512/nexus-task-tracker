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
import threading
from pathlib import Path

import pytest

import broker.conductor.__main__ as main_mod
from broker.conductor import checkpoint as checkpoint_mod
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
# run_dag_entry — partial-success status (P1 fix): status is no longer
# binary all-or-nothing. A multi-node DAG where SOME nodes fail (not all)
# must record "partial", not "failed" or "ok".
# ---------------------------------------------------------------------------


def test_run_dag_entry_partial_status_when_some_nodes_fail(tmp_path: Path) -> None:
    # node_contract's orphan-leaf rule requires exactly one terminal node —
    # chain n-ok -> n-bad rather than two independent (double-terminal) nodes.
    n_ok = _node("n-ok")
    n_ok["downstream_consumers"] = ["n-bad"]
    n_bad = _node("n-bad")
    n_bad["depends_on"] = ["n-ok"]
    doc = {
        "schema_version": 2, "dag_id": "partial-dag",
        "nodes": [n_ok, n_bad],
    }
    dag_path = _write_dag(tmp_path, doc)
    journal_path = tmp_path / "conductor_runs.jsonl"

    def fake(node, *, template, worker_id, claude_bin="claude"):
        ok = node["node_id"] == "n-ok"
        telemetry = dag_mod.DispatchTelemetry(node["node_id"], "claude", ok, 5, worker_id)
        err = None if ok else "boom"
        return dag_mod.NodeResult(node["node_id"], "claude", ok, worker_id, telemetry, error=err)

    outcome = entry.run_dag_entry(
        dag_path, journal_path=journal_path,
        checkpoint_journal_path=tmp_path / "checkpoints.jsonl",
        dispatch_claude_fn=fake, dispatch_codex_fn=_fail_if_called,
    )
    assert outcome["status"] == "partial"

    lines = _read_journal_lines(journal_path)
    assert lines[0]["status"] == "partial"
    detail = lines[0]["detail"]
    assert detail["total_nodes"] == 2
    assert detail["completed_nodes"] == 2
    assert detail["node_results"]["n-ok"]["ok"] is True
    assert detail["node_results"]["n-bad"]["ok"] is False
    assert detail["node_results"]["n-bad"]["error"] == "boom"


# ---------------------------------------------------------------------------
# CENTERPIECE: durable per-node checkpoint/resume (crash-resilience fix).
#
# RCA (`.memory/scout-reports/1783912955/conductor-rca.md`, run
# `25182409948f4da1b473025fb8eb2f44`): a transient rc!=0/timeout on ONE
# gate lost the whole run's evidence — binary all-or-nothing status, no
# retry, NO PER-NODE DURABILITY. This test IS the spec for the fix: a real
# "crash" is simulated by raising a BaseException (NOT Exception — the
# scheduler's `except Exception` in dag.py deliberately does not catch it)
# from inside `dispatch_claude_fn` after node K has already been
# checkpointed to disk, killing the sole worker thread mid-run. A SECOND
# call, resuming from the SAME run_id, must (a) never re-dispatch the
# already-checkpointed nodes, (b) complete the run, (c) lose no node
# result.
# ---------------------------------------------------------------------------


class _SimulatedCrash(BaseException):
    """Deliberately NOT an Exception subclass — escapes dag.py's
    `except Exception` handler exactly like a real process crash would,
    killing the worker thread mid-dispatch."""


def _four_node_dag(dag_id: str = "crash-resume-dag") -> dict:
    """A LINEAR CHAIN n0 -> n1 -> n2 -> n3 — node_contract's orphan-leaf rule
    requires exactly one terminal node, so 4 independent (all-terminal)
    nodes would fail validation. A chain also gives `max_workers=1`
    deterministic FIFO dispatch order for free (each node only becomes
    ready once its single predecessor completes)."""
    ids = [f"n{i}" for i in range(4)]
    nodes = []
    for i, nid in enumerate(ids):
        node = _node(nid)
        node["depends_on"] = [ids[i - 1]] if i > 0 else []
        node["downstream_consumers"] = [ids[i + 1]] if i < len(ids) - 1 else []
        nodes.append(node)
    return {"schema_version": 2, "dag_id": dag_id, "nodes": nodes}


def test_crash_mid_run_then_resume_loses_no_completed_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(threading, "excepthook", lambda args: None)  # silence the simulated crash's traceback

    dag_path = _write_dag(tmp_path, _four_node_dag())
    journal_path = tmp_path / "conductor_runs.jsonl"
    checkpoint_path = tmp_path / "checkpoints.jsonl"

    # --- run 1: crash after n0 and n1 checkpoint, before n2/n3 dispatch ---
    completed_before_crash: list[str] = []

    def crashing_dispatch(node, *, template, worker_id, claude_bin="claude"):
        node_id = node["node_id"]
        if len(completed_before_crash) >= 2:
            raise _SimulatedCrash(f"simulated crash dispatching {node_id}")
        completed_before_crash.append(node_id)
        telemetry = dag_mod.DispatchTelemetry(node_id, "claude", True, 5, worker_id)
        return dag_mod.NodeResult(node_id, "claude", True, worker_id, telemetry, payload={"ok": True})

    outcome1 = entry.run_dag_entry(
        dag_path, journal_path=journal_path, checkpoint_journal_path=checkpoint_path,
        max_workers=1,  # deterministic FIFO dispatch order over the shared ready-queue
        dispatch_claude_fn=crashing_dispatch, dispatch_codex_fn=_fail_if_called,
    )
    run_id = outcome1["run_id"]

    # the "crash" did not stop the process (only the one worker thread died),
    # so run_dag_entry returned — but with an INCOMPLETE result: only the 2
    # nodes that finished before the crash are present.
    assert set(outcome1["result"].results) == {"n0", "n1"}
    assert outcome1["status"] == "partial"

    # the durable checkpoint journal has exactly the 2 completed nodes ON
    # DISK — this is the crash-resilience guarantee: nothing already
    # completed is lost, even though the run object itself never finished.
    checkpointed = checkpoint_mod.load_checkpoint(checkpoint_path, run_id)
    assert set(checkpointed) == {"n0", "n1"}
    assert all(rec["ok"] for rec in checkpointed.values())

    # --- run 2: resume from the SAME run_id ---
    resumed_calls: list[str] = []

    def resume_dispatch(node, *, template, worker_id, claude_bin="claude"):
        node_id = node["node_id"]
        assert node_id not in ("n0", "n1"), (
            f"{node_id} was already checkpointed in run {run_id} — must not be re-dispatched on resume"
        )
        resumed_calls.append(node_id)
        telemetry = dag_mod.DispatchTelemetry(node_id, "claude", True, 5, worker_id)
        return dag_mod.NodeResult(node_id, "claude", True, worker_id, telemetry, payload={"ok": True})

    outcome2 = entry.run_dag_entry(
        dag_path, journal_path=journal_path, checkpoint_journal_path=checkpoint_path,
        max_workers=1, resume_run_id=run_id,
        dispatch_claude_fn=resume_dispatch, dispatch_codex_fn=_fail_if_called,
    )

    # (a) already-completed nodes were never re-executed
    assert set(resumed_calls) == {"n2", "n3"}
    # (b) the run completes
    assert outcome2["status"] == "ok"
    assert outcome2["run_id"] == run_id
    # (c) no node result is lost — all 4 nodes present, all ok
    result2 = outcome2["result"]
    assert set(result2.results) == {"n0", "n1", "n2", "n3"}
    assert all(r.ok for r in result2.results.values())

    # the checkpoint journal ends up with exactly ONE record per node_id for
    # this run_id — the pre-crash nodes were not re-checkpointed either.
    final_checkpoint = checkpoint_mod.load_checkpoint(checkpoint_path, run_id)
    assert set(final_checkpoint) == {"n0", "n1", "n2", "n3"}
    all_lines = [
        json.loads(line) for line in checkpoint_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    node_ids_for_run = [rec["node_id"] for rec in all_lines if rec["run_id"] == run_id]
    assert len(node_ids_for_run) == 4  # no duplicate checkpoint lines
    assert sorted(node_ids_for_run) == ["n0", "n1", "n2", "n3"]


def test_checkpoint_journal_written_incrementally_as_nodes_complete(tmp_path: Path) -> None:
    """The journal must carry a line per node as it completes, not only a
    final summary — checked here without any crash, on a normal full run."""
    dag_path = _write_dag(tmp_path, _four_node_dag(dag_id="incremental-dag"))
    checkpoint_path = tmp_path / "checkpoints.jsonl"

    outcome = entry.run_dag_entry(
        dag_path, journal_path=tmp_path / "conductor_runs.jsonl",
        checkpoint_journal_path=checkpoint_path, max_workers=2,
        dispatch_claude_fn=_fake_dispatch_claude_factory(ok=True), dispatch_codex_fn=_fail_if_called,
    )
    checkpointed = checkpoint_mod.load_checkpoint(checkpoint_path, outcome["run_id"])
    assert set(checkpointed) == {"n0", "n1", "n2", "n3"}
    assert all(rec["ok"] for rec in checkpointed.values())
    for rec in checkpointed.values():
        assert rec["timestamp"]


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


def test_run_verify_matrix_entry_partial_status_when_some_gates_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1 fix: previously `passed` (hence journaled `status`) was strictly
    `all(g["ok"] for g in gates)` — a lint+test pass with only contract
    failing recorded identically to all 3 gates failing. Gate-level detail
    (RCA "detective gap") must also land in the journal's `detail.gates`."""

    def fake_tenant(*, cwd, claude_bin, run_label):
        return {
            "lane": "conductor", "passed": False, "duration_ms": 90,
            "gates": [
                {"gate_id": "lint", "scope": "x", "ok": True, "duration_ms": 10, "error": None},
                {"gate_id": "test", "scope": "y", "ok": True, "duration_ms": 20, "error": None},
                {"gate_id": "contract", "scope": "z", "ok": False, "duration_ms": 30, "error": "rc=1: boom"},
            ],
        }

    monkeypatch.setattr(entry.verify_matrix, "run_verify_matrix_tenant", fake_tenant)
    journal_path = tmp_path / "conductor_runs.jsonl"

    outcome = entry.run_verify_matrix_entry(journal_path=journal_path)
    assert outcome["status"] == "partial"

    line = _read_journal_lines(journal_path)[0]
    assert line["status"] == "partial"
    gates = line["detail"]["gates"]
    assert len(gates) == 3
    failing = next(g for g in gates if g["gate_id"] == "contract")
    assert failing["ok"] is False
    assert failing["error"] == "rc=1: boom"


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

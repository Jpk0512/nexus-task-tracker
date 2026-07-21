"""Tests for R4-T03: broker.conductor.dag — the schema_version-2 node-contract
DAG conductor (plan-13 N06).

No live `claude`/`codex` binary anywhere in this suite: claude legs are routed
through injected `dispatch_claude_fn` stubs or a monkeypatched `pool.run_worker`
(same convention as test_conductor_pool.py); codex legs are routed through an
injected `run` callable standing in for `subprocess.run` (never a real
`codex exec` — subprocess stubbed, per the R4-T03 brief).
"""
from __future__ import annotations

import subprocess
import sqlite3
import sys
import threading
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.conductor import dag as dag_mod
from broker.conductor.pool import WorkerResult

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_PY = _REPO_ROOT / ".memory" / "log.py"


# ---------------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    downstream_consumers: list[str] | None = None,
    executor: str | None = None,
    executor_model: str | None = None,
    write_scope: list[str] | None = None,
) -> dict:
    node = {
        "node_id": node_id,
        "depends_on": depends_on or [],
        "downstream_consumers": downstream_consumers or [],
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
    if executor is not None:
        node["executor"] = executor
    if executor_model is not None:
        node["executor_model"] = executor_model
    if write_scope is not None:
        node["write_scope"] = write_scope
    return node


def _diamond_dag() -> dict:
    """root -> {A1, B1} (disjoint branches) -> {A2, B2} -> merge."""
    return {
        "schema_version": 2,
        "nodes": [
            _node("root", downstream_consumers=["A1", "B1"]),
            _node("A1", depends_on=["root"], downstream_consumers=["A2"]),
            _node("B1", depends_on=["root"], downstream_consumers=["B2"]),
            _node("A2", depends_on=["A1"], downstream_consumers=["merge"]),
            _node("B2", depends_on=["B1"], downstream_consumers=["merge"]),
            _node("merge", depends_on=["A2", "B2"]),
        ],
    }


def _enabled_codex_flag(tmp_path: Path) -> Path:
    flag = tmp_path / "codex-lane.enabled"
    flag.write_text("")
    return flag


def _fake_dispatch_claude_factory():
    calls: list[tuple[str, str]] = []

    def fake(node, *, template, worker_id, claude_bin="claude"):
        calls.append((node["node_id"], worker_id))
        telemetry = dag_mod.DispatchTelemetry(node["node_id"], "claude", True, 5, worker_id)
        return dag_mod.NodeResult(
            node["node_id"], "claude", True, worker_id, telemetry, payload={"ok": True},
        )

    fake.calls = calls
    return fake


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("dispatch must not run when the DAG fails node-contract validation")


# ---------------------------------------------------------------------------
# pre-dispatch validation gate
# ---------------------------------------------------------------------------


def test_run_dag_rejects_invalid_dag_before_dispatch() -> None:
    """A DAG with a cycle must be rejected by broker.node_contract BEFORE any
    node is dispatched — zero dispatch side effects."""
    doc = {
        "schema_version": 2,
        "nodes": [
            _node("a", depends_on=["b"], downstream_consumers=[]),
            _node("b", depends_on=["a"], downstream_consumers=[]),
        ],
    }
    # edges are asymmetric on purpose too (not the point of this test) — fix them
    doc["nodes"][0]["downstream_consumers"] = ["b"]
    doc["nodes"][1]["downstream_consumers"] = ["a"]

    with pytest.raises(dag_mod.DagValidationError) as excinfo:
        dag_mod.run_dag(
            doc, max_workers=2,
            dispatch_claude_fn=_fail_if_called, dispatch_codex_fn=_fail_if_called,
        )
    assert any(e.code == "cycle" for e in excinfo.value.errors)


def test_run_dag_rejects_dag_missing_required_field_before_dispatch() -> None:
    doc = {
        "schema_version": 2,
        "nodes": [
            {"node_id": "solo", "depends_on": [], "downstream_consumers": []},
        ],
    }
    with pytest.raises(dag_mod.DagValidationError) as excinfo:
        dag_mod.run_dag(
            doc, max_workers=2,
            dispatch_claude_fn=_fail_if_called, dispatch_codex_fn=_fail_if_called,
        )
    assert any(e.code == "missing-field" for e in excinfo.value.errors)


# ---------------------------------------------------------------------------
# topological scheduling over a fixture DAG
# ---------------------------------------------------------------------------


def test_run_dag_topologically_schedules_fixture_dag() -> None:
    fake = _fake_dispatch_claude_factory()
    doc = _diamond_dag()

    result = dag_mod.run_dag(doc, max_workers=2, dispatch_claude_fn=fake, dispatch_codex_fn=_fail_if_called)

    assert all(r.ok for r in result.results.values())
    assert set(result.results) == {"root", "A1", "B1", "A2", "B2", "merge"}
    order = result.order
    # dependency-respecting order: root first, merge last, each dep before its dependent
    assert order[0] == "root"
    assert order[-1] == "merge"
    assert order.index("A1") < order.index("A2")
    assert order.index("B1") < order.index("B2")
    assert order.index("A2") < order.index("merge")
    assert order.index("B2") < order.index("merge")


def test_run_dag_single_node_dag() -> None:
    fake = _fake_dispatch_claude_factory()
    doc = {"schema_version": 2, "nodes": [_node("solo")]}
    result = dag_mod.run_dag(doc, max_workers=3, dispatch_claude_fn=fake, dispatch_codex_fn=_fail_if_called)
    assert result.order == ["solo"]
    assert result.results["solo"].ok is True


# ---------------------------------------------------------------------------
# work-stealing: >=2 workers genuinely run disjoint branches concurrently
# ---------------------------------------------------------------------------


def test_work_stealing_runs_disjoint_branches_concurrently() -> None:
    """A threading.Barrier(2) gates the two disjoint-branch nodes (A1, B1) —
    this only clears if BOTH are dispatched at the same time by two distinct
    workers pulling off the shared ready-queue. A scheduler that serializes
    dispatch (single effective worker) times out the barrier and both nodes
    come back failed, which the assertions below would catch."""
    barrier = threading.Barrier(2, timeout=5)
    lock = threading.Lock()
    branch_workers: dict[str, str] = {}

    def fake_dispatch_claude(node, *, template, worker_id, claude_bin="claude"):
        nid = node["node_id"]
        if nid in ("A1", "B1"):
            with lock:
                branch_workers[nid] = worker_id
            barrier.wait()  # blocks up to 5s; raises BrokenBarrierError if not concurrent
        telemetry = dag_mod.DispatchTelemetry(nid, "claude", True, 5, worker_id)
        return dag_mod.NodeResult(nid, "claude", True, worker_id, telemetry, payload={"ok": True})

    doc = _diamond_dag()
    result = dag_mod.run_dag(
        doc, max_workers=2, dispatch_claude_fn=fake_dispatch_claude, dispatch_codex_fn=_fail_if_called,
    )

    assert result.results["A1"].ok is True, result.results["A1"].error
    assert result.results["B1"].ok is True, result.results["B1"].error
    assert branch_workers["A1"] != branch_workers["B1"], "disjoint branches ran on the SAME worker — not concurrent"


# ---------------------------------------------------------------------------
# executor-dispatch switch
# ---------------------------------------------------------------------------


def test_dispatch_node_routes_claude_through_pool(
    monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotAssertion
) -> None:
    def fake_run_worker(task, *, claude_bin="claude"):
        return WorkerResult(task.task_id, ok=True, duration_ms=42, payload={"a": 1}, total_cost_usd=0.002)

    monkeypatch.setattr(dag_mod.pool, "run_worker", fake_run_worker)

    node = _node("n1")
    templates = dag_mod.build_worker_templates({"n1": node}, cwd_root=str(_REPO_ROOT))
    result = dag_mod.dispatch_node(node, worker_id="w0", templates=templates, worktree_root=str(_REPO_ROOT))

    assert result.executor == "claude"
    assert result.ok is True
    # envelope fixture: the dispatch_node result payload, reviewed via snapshot (F3-04).
    assert result.payload == snapshot(name="claude_payload")
    assert result.telemetry.total_cost_usd == pytest.approx(0.002)
    assert result.telemetry.worker_id == "w0"


def test_dispatch_node_routes_codex_through_exec_subprocess(snapshot: SnapshotAssertion) -> None:
    jsonl = "\n".join([
        '{"type":"thread.started","thread_id":"th-1"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"{\\"greeting\\":\\"hi\\"}"}}',
        '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}',
    ])

    def fake_run(argv, *, input, capture_output, text, cwd, timeout):  # noqa: A002 - matches subprocess.run kwarg
        return subprocess.CompletedProcess(argv, 0, stdout=jsonl, stderr="")

    node = _node("codex1", executor="codex", executor_model="gpt-5.4", write_scope=["nexus-broker/src/broker/conductor/**"])
    templates = dag_mod.build_worker_templates({"codex1": node}, cwd_root=str(_REPO_ROOT))

    import functools
    codex_fn = functools.partial(dag_mod.dispatch_codex, run=fake_run)

    result = dag_mod.dispatch_node(
        node, worker_id="w1", templates=templates, worktree_root=str(_REPO_ROOT), dispatch_codex_fn=codex_fn,
    )

    assert result.executor == "codex"
    assert result.ok is True
    assert result.payload == snapshot(name="codex_payload")
    assert result.telemetry.input_tokens == 100
    assert result.telemetry.output_tokens == 20
    assert result.telemetry.worker_id == "w1"
    # documented argv composition (plans/11 SS9.4)
    assert "--output-schema" in result.argv
    assert "-s" in result.argv and "workspace-write" in result.argv
    assert "-C" in result.argv and str(_REPO_ROOT) in result.argv
    assert "--json" in result.argv
    assert result.argv[-1] == "-"  # brief-on-stdin marker
    assert "-m" in result.argv and "gpt-5.4" in result.argv


def test_dispatch_codex_fails_loud_on_nonzero_rc() -> None:
    def fake_run(argv, *, input, capture_output, text, cwd, timeout):
        return subprocess.CompletedProcess(
            argv, 1, stdout='{"type":"turn.failed","error":{"message":"boom"}}', stderr="crash",
        )

    node = _node("codexfail", executor="codex", write_scope=[])
    result = dag_mod.dispatch_codex(node, worktree=str(_REPO_ROOT), worker_id="w0", run=fake_run)

    assert result.ok is False
    assert result.telemetry.ok is False
    assert "boom" in result.error


def test_build_codex_argv_read_only_for_empty_write_scope() -> None:
    node = _node("readonly", executor="codex", write_scope=[])
    argv = dag_mod.build_codex_argv(node, worktree="/tmp/wt")
    assert argv[argv.index("-s") + 1] == "read-only"


def test_build_codex_argv_raises_on_unmappable_write_scope() -> None:
    node = _node("unbounded", executor="codex", write_scope=["**/*"])
    with pytest.raises(ValueError, match="no expressible codex sandbox"):
        dag_mod.build_codex_argv(node, worktree="/tmp/wt")


# ---------------------------------------------------------------------------
# uniform DispatchTelemetry shape across executors
# ---------------------------------------------------------------------------


def test_dispatch_telemetry_uniform_shape_across_executors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_worker(task, *, claude_bin="claude"):
        return WorkerResult(task.task_id, ok=True, duration_ms=10, payload={}, total_cost_usd=0.001)

    monkeypatch.setattr(dag_mod.pool, "run_worker", fake_run_worker)

    jsonl = '{"type":"turn.completed","usage":{"input_tokens":50,"output_tokens":5}}'

    def fake_run(argv, *, input, capture_output, text, cwd, timeout):
        return subprocess.CompletedProcess(argv, 0, stdout=jsonl, stderr="")

    claude_node = _node("c1")
    codex_node = _node("k1", executor="codex", write_scope=[])
    templates = dag_mod.build_worker_templates({"c1": claude_node}, cwd_root=str(_REPO_ROOT))

    import functools
    codex_fn = functools.partial(dag_mod.dispatch_codex, run=fake_run)

    claude_result = dag_mod.dispatch_node(
        claude_node, worker_id="w0", templates=templates, worktree_root=str(_REPO_ROOT),
    )
    codex_result = dag_mod.dispatch_node(
        codex_node, worker_id="w1", templates={}, worktree_root=str(_REPO_ROOT), dispatch_codex_fn=codex_fn,
    )

    assert type(claude_result.telemetry) is type(codex_result.telemetry) is dag_mod.DispatchTelemetry
    claude_fields = {f.name for f in __import__("dataclasses").fields(claude_result.telemetry)}
    codex_fields = {f.name for f in __import__("dataclasses").fields(codex_result.telemetry)}
    assert claude_fields == codex_fields
    assert claude_result.telemetry.total_cost_usd == pytest.approx(0.001)
    assert codex_result.telemetry.input_tokens == 50
    assert codex_result.telemetry.output_tokens == 5


# ---------------------------------------------------------------------------
# record_dispatch_telemetry — the SAME existing dispatch_telemetry CLI path
# ---------------------------------------------------------------------------


def test_record_dispatch_telemetry_feeds_same_table_for_both_executors(tmp_path: Path) -> None:
    import os

    db = tmp_path / "project.db"
    init = subprocess.run(
        [sys.executable, str(_LOG_PY), "init"],
        capture_output=True, text=True,
        env={**os.environ, "NEXUS_DB_PATH": str(db), "NEXUS_DISABLE_VEC": "1"},
    )
    assert init.returncode == 0, init.stderr

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("NEXUS_DB_PATH", str(db))
    monkeypatch.setenv("NEXUS_DISABLE_VEC", "1")
    try:
        claude_node = _node("c-tel")
        claude_telemetry = dag_mod.DispatchTelemetry(
            "c-tel", "claude", True, 123, "w0", total_cost_usd=0.004,
        )
        codex_node = _node("k-tel", executor="codex", executor_model="gpt-5.4")
        codex_telemetry = dag_mod.DispatchTelemetry(
            "k-tel", "codex", True, 456, "w1", input_tokens=80, output_tokens=10,
        )
        dag_mod.record_dispatch_telemetry(claude_node, claude_telemetry, cwd_root=_REPO_ROOT)
        dag_mod.record_dispatch_telemetry(codex_node, codex_telemetry, cwd_root=_REPO_ROOT)
    finally:
        monkeypatch.undo()

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE task_id IN ('c-tel', 'k-tel') ORDER BY task_id"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    by_task = {r["task_id"]: r for r in rows}
    assert by_task["c-tel"]["duration_ms"] == 123
    assert by_task["c-tel"]["token_source"] == "approx"
    assert by_task["k-tel"]["duration_ms"] == 456
    assert by_task["k-tel"]["tokens"] == 90
    assert by_task["k-tel"]["token_source"] == "exact"
    assert by_task["k-tel"]["model"] == "gpt-5.4"


# ---------------------------------------------------------------------------
# module import surface
# ---------------------------------------------------------------------------


def test_conductor_dag_module_importable() -> None:
    import broker.conductor.dag  # noqa: F401
    from broker.conductor import DagValidationError, DispatchTelemetry, run_dag  # noqa: F401

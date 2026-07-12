"""Tests for R4-T09 (node N24, plans/08 §3.8): the daemon-side trace journal —
one trace ID assigned and propagated through every dispatch/gate/verdict
event of a multi-phase Workflow chain, reconstructed end-to-end from ONE
query.

Covers exactly this node's acceptance criteria:
  1. a fixture 3-leg Workflow chain (dispatch -> gate -> verdict) reconstructs
     end-to-end from one `TraceJournal.reconstruct(trace_id)` call;
  2. events arriving without a trace ID are journaled as untraced, never
     dropped and never blocking;
  3. no `.memory/schema.sql` modification — verified here via `git diff`.

All three propagation surfaces named in this node's brief are proven for
real, not simulated in Python alone:
  - the bash-hook subprocess boundary is proven against a REAL `bash`
    subprocess (env-var round trip through actual process inheritance);
  - the conductor is proven against the REAL `broker.conductor.dag.run_dag`
    scheduler (dispatch functions stubbed, exactly `test_conductor_dag.py`'s
    own convention — no real `claude`/`codex` binary anywhere in this suite);
  - the Python broker / N23 bus wiring is proven against a REAL
    `broker.daemon.bus.EventBus` publish/subscribe/receive round trip.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from broker import node_contract
from broker.conductor import dag as dag_mod
from broker.daemon import bus as bus_mod
from broker.daemon import tracing

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _node(node_id: str, **overrides) -> dict:
    node = {
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
    node.update(overrides)
    return node


def _fake_dispatch_claude_factory():
    def fake(node, *, template, worker_id, claude_bin="claude"):
        telemetry = dag_mod.DispatchTelemetry(node["node_id"], "claude", True, 7, worker_id)
        return dag_mod.NodeResult(
            node["node_id"], "claude", True, worker_id, telemetry, payload={"ok": True},
        )

    return fake


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("this fixture must not dispatch a real codex leg")


# ── trace-ID assignment ──────────────────────────────────────────────────


def test_new_trace_id_unique_and_opaque() -> None:
    a, b = tracing.new_trace_id(), tracing.new_trace_id()
    assert a != b
    assert isinstance(a, str) and a


def test_ensure_trace_id_keeps_inbound() -> None:
    assert tracing.ensure_trace_id("trace-abc") == "trace-abc"


def test_ensure_trace_id_mints_when_absent() -> None:
    minted = tracing.ensure_trace_id(None)
    assert minted and minted != tracing.ensure_trace_id(None)


# ── surface 1: bash-hook subprocess-boundary propagation ───────────────────


def test_propagate_env_requires_nonempty_trace_id() -> None:
    with pytest.raises(ValueError, match="non-empty trace_id"):
        tracing.propagate_env("")


def test_trace_id_from_env_round_trip_in_memory() -> None:
    env = tracing.propagate_env("trace-xyz", env={})
    assert tracing.trace_id_from_env(env) == "trace-xyz"


def test_trace_id_from_env_absent_returns_none() -> None:
    assert tracing.trace_id_from_env({}) is None


def test_propagate_env_survives_a_real_bash_subprocess() -> None:
    """The subprocess-boundary contract, proven against a REAL bash process
    (not a Python stand-in): a parent sets TRACE_ID_ENV_VAR via
    `propagate_env`, a real child shell reads it back, exactly the hop a
    bash gate hook would make before shelling out to whatever runs next."""
    trace_id = tracing.new_trace_id()
    env = tracing.propagate_env(trace_id)
    result = subprocess.run(
        ["bash", "-c", f'echo "${tracing.TRACE_ID_ENV_VAR}"'],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == trace_id


# ── surface 3: conductor node-dict propagation ──────────────────────────────


def test_attach_trace_id_is_additive_and_still_passes_node_contract_validation() -> None:
    node = _node("solo")
    tagged = tracing.attach_trace_id(node, "trace-123")
    assert tagged is not node  # non-mutating
    assert tagged["trace_id"] == "trace-123"
    doc = {"schema_version": 2, "nodes": [tagged]}
    errors = node_contract.validate_dag(doc)
    assert errors == []


def test_trace_id_from_node_reads_attach_trace_id_output() -> None:
    tagged = tracing.attach_trace_id(_node("solo"), "trace-123")
    assert tracing.trace_id_from_node(tagged) == "trace-123"


def test_trace_id_from_node_absent_returns_none() -> None:
    assert tracing.trace_id_from_node(_node("solo")) is None


# ── the journal: record / reconstruct / untraced ────────────────────────────


def test_record_and_reconstruct_orders_by_seq_not_by_caller_supplied_ts() -> None:
    journal = tracing.TraceJournal()
    trace_id = "trace-order"
    journal.record(trace_id, "dispatch_started", {"n": 1}, source="conductor")
    journal.record(trace_id, "gate_denied", {"n": 2}, source="hook")
    journal.record(trace_id, "lens_verdict_recorded", {"n": 3}, source="broker")

    chain = journal.reconstruct(trace_id)
    assert [e["kind"] for e in chain] == ["dispatch_started", "gate_denied", "lens_verdict_recorded"]
    assert [e["payload"]["n"] for e in chain] == [1, 2, 3]
    assert [e["seq"] for e in chain] == sorted(e["seq"] for e in chain)


def test_reconstruct_unknown_trace_id_returns_empty_list_not_an_error() -> None:
    journal = tracing.TraceJournal()
    assert journal.reconstruct("trace-never-seen") == []


def test_reconstruct_only_returns_events_for_the_requested_trace() -> None:
    journal = tracing.TraceJournal()
    journal.record("trace-a", "dispatch_started", {}, source="conductor")
    journal.record("trace-b", "dispatch_started", {}, source="conductor")
    assert len(journal.reconstruct("trace-a")) == 1
    assert len(journal.reconstruct("trace-b")) == 1


def test_untraced_events_are_journaled_never_dropped() -> None:
    journal = tracing.TraceJournal()
    for i in range(5):
        journal.record(None, "dispatch_started", {"i": i}, source="conductor")
    untraced = journal.untraced()
    assert len(untraced) == 5
    assert [e["payload"]["i"] for e in untraced] == [0, 1, 2, 3, 4]
    assert all(e["trace_id"] is None for e in untraced)
    # untraced events never pollute a real trace's reconstruction
    assert journal.reconstruct("trace-anything") == []


def test_untraced_recording_never_raises_and_never_blocks_concurrently() -> None:
    """20 threads racing `record(None, ...)` must all complete without
    raising, deadlocking, or losing an event — the "never blocking" half of
    the untraced acceptance criterion under real concurrency, mirroring
    `bus.py`'s own concurrency posture."""
    journal = tracing.TraceJournal()
    errors: list[BaseException] = []

    def _worker(i: int) -> None:
        try:
            journal.record(None, "skill_load_observed", {"worker": i}, source="broker")
        except BaseException as exc:  # noqa: BLE001 — a thread must report, not swallow
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, errors
    assert len(journal.untraced()) == 20


def test_record_accepts_any_kind_string_never_a_second_gate() -> None:
    """Unlike bus.EventBus.publish, the journal must not reject an event on
    an unrecognized kind — a hook/gate script's vocabulary is not this
    module's to police, and rejecting would violate "never dropped"."""
    journal = tracing.TraceJournal()
    event = journal.record("trace-1", "some_future_kind_not_in_bus_yet", {}, source="hook")
    assert event.kind == "some_future_kind_not_in_bus_yet"
    assert journal.reconstruct("trace-1")[0]["kind"] == "some_future_kind_not_in_bus_yet"


def test_journal_stats_reports_trace_event_and_untraced_counts() -> None:
    journal = tracing.TraceJournal()
    journal.record("trace-1", "dispatch_started", {}, source="conductor")
    journal.record("trace-1", "gate_denied", {}, source="hook")
    journal.record("trace-2", "dispatch_started", {}, source="conductor")
    journal.record(None, "dispatch_started", {}, source="conductor")

    stats = journal.stats()
    assert stats["trace_count"] == 2
    assert stats["event_count"] == 3
    assert stats["untraced_count"] == 1
    assert journal.trace_ids() == ["trace-1", "trace-2"]


# ── surface-3 wiring: the REAL run_dag scheduler ────────────────────────────


def test_dag_telemetry_sink_wires_into_real_run_dag_and_reconstructs() -> None:
    journal = tracing.TraceJournal()
    trace_id = tracing.new_trace_id()
    node = tracing.attach_trace_id(_node("leg1_dispatch"), trace_id)
    doc = {"schema_version": 2, "nodes": [node]}

    result = dag_mod.run_dag(
        doc,
        max_workers=1,
        dispatch_claude_fn=_fake_dispatch_claude_factory(),
        dispatch_codex_fn=_fail_if_called,
        telemetry_sink=tracing.dag_telemetry_sink(journal),
    )

    assert result.results["leg1_dispatch"].ok is True
    chain = journal.reconstruct(trace_id)
    assert len(chain) == 1
    event = chain[0]
    assert event["kind"] == "dispatch_completed"
    assert event["source"] == "conductor"
    assert event["node_id"] == "leg1_dispatch"
    assert event["payload"]["ok"] is True
    assert event["payload"]["executor"] == "claude"


def test_dag_telemetry_sink_multi_node_dag_separates_traces_correctly() -> None:
    """Two disjoint branches dispatched in the SAME DagRun must not cross-
    contaminate each other's reconstruction — each node carries its own
    trace ID via `attach_trace_id` (a shared `merge` sink is required for a
    valid multi-node DAG per `broker.node_contract`'s single-terminal MECE
    rule; it is left untraced on purpose to prove that doesn't leak into
    either branch's chain)."""
    journal = tracing.TraceJournal()
    trace_a, trace_b = tracing.new_trace_id(), tracing.new_trace_id()
    doc = {
        "schema_version": 2,
        "nodes": [
            tracing.attach_trace_id(_node("a1", downstream_consumers=["merge"]), trace_a),
            tracing.attach_trace_id(_node("b1", downstream_consumers=["merge"]), trace_b),
            _node("merge", depends_on=["a1", "b1"]),
        ],
    }
    dag_mod.run_dag(
        doc, max_workers=2,
        dispatch_claude_fn=_fake_dispatch_claude_factory(),
        dispatch_codex_fn=_fail_if_called,
        telemetry_sink=tracing.dag_telemetry_sink(journal),
    )

    chain_a = journal.reconstruct(trace_a)
    chain_b = journal.reconstruct(trace_b)
    assert len(chain_a) == 1 and chain_a[0]["node_id"] == "a1"
    assert len(chain_b) == 1 and chain_b[0]["node_id"] == "b1"
    assert len(journal.untraced()) == 1 and journal.untraced()[0]["node_id"] == "merge"


# ── bus wiring: the REAL N23 EventBus ───────────────────────────────────────


async def test_bus_trace_recorder_consumes_a_real_bus_event() -> None:
    bus = bus_mod.EventBus()
    journal = tracing.TraceJournal()
    consume = tracing.bus_trace_recorder(journal)
    sub = bus.subscribe(kinds=[bus_mod.EVENT_KIND_LENS_VERDICT_RECORDED])

    trace_id = tracing.new_trace_id()
    bus.publish(
        bus_mod.EVENT_KIND_LENS_VERDICT_RECORDED,
        {"trace_id": trace_id, "node_id": "leg3_verdict", "verdict": "PASS"},
    )
    event = await bus.receive(sub.id, timeout=1.0)
    consume(event)

    chain = journal.reconstruct(trace_id)
    assert len(chain) == 1
    assert chain[0]["kind"] == bus_mod.EVENT_KIND_LENS_VERDICT_RECORDED
    assert chain[0]["source"] == "broker"
    assert chain[0]["node_id"] == "leg3_verdict"
    assert chain[0]["payload"]["verdict"] == "PASS"


async def test_bus_trace_recorder_journals_untraced_bus_events_too() -> None:
    """A bus event published without a `trace_id` key in its payload must
    still be journaled (as untraced), never dropped."""
    bus = bus_mod.EventBus()
    journal = tracing.TraceJournal()
    consume = tracing.bus_trace_recorder(journal)
    sub = bus.subscribe(kinds=[bus_mod.EVENT_KIND_GATE_DENIED])

    bus.publish(bus_mod.EVENT_KIND_GATE_DENIED, {"reason": "no upstream trace"})
    event = await bus.receive(sub.id, timeout=1.0)
    consume(event)

    assert len(journal.untraced()) == 1
    assert journal.untraced()[0]["kind"] == bus_mod.EVENT_KIND_GATE_DENIED


# ── acceptance: fixture 3-leg Workflow chain reconstructs from ONE query ────


async def test_three_leg_workflow_chain_reconstructs_end_to_end_from_one_query() -> None:
    """dispatch (conductor, via the real run_dag scheduler) -> gate (bash
    hook, via a real bash subprocess round trip) -> verdict (Python broker,
    via a real EventBus publish/subscribe/receive round trip), all three
    legs correlated under ONE trace ID, reconstructed with ONE
    `journal.reconstruct(trace_id)` call — this node's core acceptance
    criterion."""
    journal = tracing.TraceJournal()
    trace_id = tracing.new_trace_id()

    # Leg 1 — dispatch, via the REAL conductor scheduler.
    node = tracing.attach_trace_id(_node("leg1_dispatch"), trace_id)
    doc = {"schema_version": 2, "nodes": [node]}
    dag_result = dag_mod.run_dag(
        doc, max_workers=1,
        dispatch_claude_fn=_fake_dispatch_claude_factory(),
        dispatch_codex_fn=_fail_if_called,
        telemetry_sink=tracing.dag_telemetry_sink(journal),
    )
    assert dag_result.results["leg1_dispatch"].ok is True

    # Leg 2 — gate, via a REAL bash subprocess carrying the trace ID across
    # the process boundary (propagate_env / trace_id_from_env round trip),
    # exactly the hop a lens-gate-style hook makes before recording its verdict.
    env = tracing.propagate_env(trace_id)
    gate_proc = subprocess.run(
        ["bash", "-c", f'echo "${tracing.TRACE_ID_ENV_VAR}"'],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert gate_proc.returncode == 0, gate_proc.stderr
    gate_trace_id = tracing.trace_id_from_env({tracing.TRACE_ID_ENV_VAR: gate_proc.stdout.strip()})
    assert gate_trace_id == trace_id
    journal.record(
        gate_trace_id, "gate_denied", {"gate": "lens-gate", "reason": "pending-revise"},
        source="hook", node_id="leg2_gate",
    )

    # Leg 3 — verdict, via a REAL EventBus publish/subscribe/receive round trip.
    bus = bus_mod.EventBus()
    consume = tracing.bus_trace_recorder(journal)
    sub = bus.subscribe(kinds=[bus_mod.EVENT_KIND_LENS_VERDICT_RECORDED])
    bus.publish(
        bus_mod.EVENT_KIND_LENS_VERDICT_RECORDED,
        {"trace_id": trace_id, "node_id": "leg3_verdict", "verdict": "PASS"},
    )
    bus_event = await bus.receive(sub.id, timeout=1.0)
    consume(bus_event)

    # ONE reconstruction query returns the whole correlated chain.
    chain = journal.reconstruct(trace_id)
    assert [e["kind"] for e in chain] == ["dispatch_completed", "gate_denied", "lens_verdict_recorded"]
    assert [e["source"] for e in chain] == ["conductor", "hook", "broker"]
    assert [e["node_id"] for e in chain] == ["leg1_dispatch", "leg2_gate", "leg3_verdict"]
    assert all(e["trace_id"] == trace_id for e in chain)
    assert chain[-1]["payload"]["verdict"] == "PASS"


# ── acceptance: no .memory/schema.sql modification ──────────────────────────


def test_no_schema_sql_modification() -> None:
    """R4-T09 acceptance: trace IDs live entirely in this daemon-side journal
    — `.memory/schema.sql` must carry zero diff against HEAD. Verified via a
    real `git diff`, not an assertion-by-absence."""
    schema_path = _REPO_ROOT / ".memory" / "schema.sql"
    assert schema_path.is_file(), f"expected {schema_path} to exist"

    result = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", ".memory/schema.sql"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "`.memory/schema.sql` has a diff against HEAD — this node must not "
        f"modify it. stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_tracing_module_never_imports_sqlite3_or_touches_a_db_path() -> None:
    """Structural guarantee, not just documentation (same posture bus.py
    asserts of itself): this module takes no db_path/project_path parameter
    anywhere in its public callables' signatures and never imports sqlite3."""
    import inspect

    source = (_REPO_ROOT / "nexus-broker" / "src" / "broker" / "daemon" / "tracing.py").read_text()
    assert "import sqlite3" not in source
    assert "sqlite3" not in sys.modules or "sqlite3" not in dir(tracing)

    public_callables = [
        obj for name, obj in vars(tracing).items()
        if not name.startswith("_") and (inspect.isfunction(obj) or inspect.isclass(obj))
    ]
    assert public_callables, "expected at least one public callable to inspect"
    for obj in public_callables:
        target = obj.__init__ if inspect.isclass(obj) else obj
        params = set(inspect.signature(target).parameters)
        assert "db_path" not in params, f"{obj!r} unexpectedly accepts db_path"
        assert "project_path" not in params, f"{obj!r} unexpectedly accepts project_path"

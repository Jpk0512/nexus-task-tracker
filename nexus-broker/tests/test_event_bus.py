"""F2-02 daemon event-bus core — `nexus-foundation/plans/artifacts/
event-bus-design.md` + `event-taxonomy.json` (wave-2.md §(d)).

Covers exactly this leaf's acceptance surface:
  - the resident `EventTaxonomy` hydrates the real 16-event taxonomy file
    (never a hand-invented shape — tdd-core's real-data-shapes rule) and
    degrades to an EMPTY taxonomy (never a construction crash) on a tenant
    without `nexus-foundation/` (test_daemon_pilot.py's own project fixture
    shape);
  - `event.emit`/`event.verify` enforce tranche discipline as a hard
    ValueError, never a silent cross-tranche fallthrough (constraint 2);
  - `health.ping` / `governance.reload` / `span.emit` round-trip through
    `server.handle_request`'s real dispatch table, in-process AND over the
    real unix-socket transport a spawned daemon subprocess serves;
  - the fail-policy CLIENT contract in `_daemon_rpc.py` — `call_advisory`
    (fail OPEN) vs `call_deny_capable` (fail CLOSED) — is structurally
    DISTINCT on a daemon miss (notepad gotcha #327), and the pre-existing
    `call()` None-on-miss contract stays byte-for-byte unchanged.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.daemon import event_bus
from broker.daemon.server import DaemonState, handle_request

REPO_ROOT = Path(__file__).resolve().parents[2]
BROKER_ROOT = REPO_ROOT / "nexus-broker"
REAL_TAXONOMY_PATH = REPO_ROOT / "nexus-foundation" / "plans" / "artifacts" / "event-taxonomy.json"
if not REAL_TAXONOMY_PATH.is_file():
    # TASK-088/DEC-085 Option A: this file is snapshotted verbatim into
    # nexus-package/nexus-broker/tests/ (not a PLEXUS_SELF_TESTS exclusion —
    # its socket-transport/tranche-dispatch coverage IS part of the broker
    # deployable contract), and parents[2] path-doubles there: REPO_ROOT
    # resolves to nexus-package/ instead of the meta-repo root, so the
    # meta-repo-only nexus-foundation/plans/artifacts/ canonical source never
    # exists in that tree. DEC-085 Option A already established the fix
    # direction for exactly this shape (build_snapshot.sh's sync_broker
    # bundles event-taxonomy.json into broker/daemon/data/, never a package
    # plans/ tree) — the broker-bundled default IS the canonical source once
    # there is no meta-repo tree to defer to, so fall back to it here too.
    REAL_TAXONOMY_PATH = event_bus._BUNDLED_TAXONOMY_PATH
DAEMON_RPC_PATH = REPO_ROOT / ".claude" / "hooks" / "_daemon_rpc.py"

TRANCHE_A_EVENT = "session.start"  # advisory-fail-open, per event-taxonomy.json
TRANCHE_B_EVENT = "write.pre.verify"  # deny-capable-fail-closed, per event-taxonomy.json


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path) -> Path:
    """A bare project with no `nexus-foundation/` — the not-a-meta-repo-
    tenant shape `DaemonState` must never crash on."""
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    return project


@pytest.fixture()
def project_with_real_taxonomy(tmp_path) -> Path:
    """A project whose taxonomy file is the REAL, production
    `event-taxonomy.json` bytes — never a hand-invented mock shape."""
    project = tmp_path / "proj"
    dest = project / "nexus-foundation" / "plans" / "artifacts" / "event-taxonomy.json"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(REAL_TAXONOMY_PATH.read_bytes())
    (project / ".memory").mkdir(parents=True)
    return project


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD — force a short dir
    # directly under /tmp (mirrors test_daemon_pilot.py / test_daemon_ensure.py).
    sock_dir = Path(tempfile.mkdtemp(prefix="nxeb", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture()
def spawned_daemons():
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _daemon_rpc_module():
    """Same-directory dynamic import of the hook-side transport — mirrors
    `completion-capture.py`'s own `_daemon_rpc_module()` convention. This
    test file runs under nexus-broker's >=3.12 venv, but the module itself
    stays 3.9-import-safe (checked separately via `python3 -m py_compile`).
    """
    spec = importlib.util.spec_from_file_location("_daemon_rpc", DAEMON_RPC_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _spawn_daemon_process(project_path: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "broker.daemon.server", "--project-path", str(project_path)],
        cwd=str(BROKER_ROOT),
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_health_ping(daemon_rpc, project_path: Path, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        result = daemon_rpc.call(project_path, "health.ping", {}, 0.5)
        if result is not None:
            return result
        last = result
        time.sleep(0.05)
    raise AssertionError(f"daemon never answered health.ping in time (last={last!r})")


# ── EventTaxonomy: real 16-event shape + tranche map ────────────────────────


def test_real_taxonomy_loads_all_16_events_with_known_tranches() -> None:
    taxonomy = event_bus.EventTaxonomy(REAL_TAXONOMY_PATH)
    assert taxonomy.event_count == 16
    assert taxonomy.content_digest != "no-taxonomy"

    dispatch_verify = taxonomy.get("dispatch.pre.verify")
    assert dispatch_verify.tranche == "B"
    assert dispatch_verify.fail_policy == event_bus.FAIL_POLICY_DENY_CAPABLE
    assert "routing-target-validator.py" in dispatch_verify.consumers

    session_start = taxonomy.get("session.start")
    assert session_start.tranche == "A"
    assert session_start.fail_policy == event_bus.FAIL_POLICY_ADVISORY

    # subagent.stop.verify/observe independence (Finding #6) — both present,
    # opposite tranches, neither silently folded into the other.
    verify_evt = taxonomy.get("subagent.stop.verify")
    observe_evt = taxonomy.get("subagent.stop.observe")
    assert verify_evt.tranche == "B"
    assert observe_evt.tranche == "A"


def test_taxonomy_get_unknown_event_raises_value_error() -> None:
    taxonomy = event_bus.EventTaxonomy(REAL_TAXONOMY_PATH)
    with pytest.raises(ValueError, match="unknown event"):
        taxonomy.get("not.a.real.event")


def test_taxonomy_empty_when_file_absent(tmp_path) -> None:
    taxonomy = event_bus.EventTaxonomy(tmp_path / "nope" / "event-taxonomy.json")
    assert taxonomy.event_count == 0
    assert taxonomy.content_digest == "no-taxonomy"
    assert taxonomy.loaded_at  # still stamped, just empty


# ── DaemonState / EventBusState construction never crashes ────────────────


def test_daemon_state_construction_hydrates_bundled_taxonomy_without_project_file(project) -> None:
    """DEC-085: a project with no `nexus-foundation/` of its own (the
    `project` fixture — same shape a package-shaped/target install has) now
    hydrates the broker-BUNDLED default (`event_bus._BUNDLED_TAXONOMY_PATH`,
    sibling to `event_bus.py`) rather than an empty taxonomy — this is the
    exact C-07 gap that made `event.emit` raise `ValueError` -> a
    permanently-lost advisory banner on every non-meta-repo tenant. Still
    never a construction-time crash either way (the original assertion this
    test replaces)."""
    state = DaemonState(project)
    assert state.event_bus.taxonomy.event_count == 16
    assert state.event_bus.taxonomy.content_digest != "no-taxonomy"


def test_bundled_taxonomy_default_matches_canonical_source() -> None:
    """DEC-085 acceptance: the package-shaped install path
    (`event_bus._BUNDLED_TAXONOMY_PATH`) is present and byte-identical to the
    canonical `nexus-foundation/plans/artifacts/event-taxonomy.json` this
    meta-repo edits — `tools/build_snapshot.sh`'s `sync_broker` step is what
    keeps them in sync; this test proves the CURRENT snapshot is not stale."""
    assert event_bus._BUNDLED_TAXONOMY_PATH.is_file(), (
        f"bundled taxonomy default missing at {event_bus._BUNDLED_TAXONOMY_PATH} — "
        "run tools/build_snapshot.sh --sync"
    )
    assert event_bus._BUNDLED_TAXONOMY_PATH.read_bytes() == REAL_TAXONOMY_PATH.read_bytes()

    taxonomy = event_bus.EventTaxonomy(event_bus._BUNDLED_TAXONOMY_PATH)
    assert taxonomy.event_count == 16
    assert taxonomy.content_digest != "no-taxonomy"


def test_daemon_state_construction_loads_real_taxonomy_when_present(project_with_real_taxonomy) -> None:
    state = DaemonState(project_with_real_taxonomy)
    assert state.event_bus.taxonomy.event_count == 16


# ── event.emit / event.verify tranche discipline (constraint 2) ────────────


def test_handle_event_emit_accepts_tranche_a_event(
    project_with_real_taxonomy, snapshot: SnapshotAssertion
) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    result = event_bus.handle_event_emit(state, {"name": TRANCHE_A_EVENT})
    # envelope fixture: the event.emit RPC response shape, reviewed via
    # snapshot (F3-04) — advisory_context is `{}` without a `consumer` param,
    # see test_advisory_handlers.py.
    assert result == snapshot(name="emit_envelope")
    assert state.emit_count == 1


def test_handle_event_emit_rejects_tranche_b_event(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    with pytest.raises(ValueError, match="use event.verify"):
        event_bus.handle_event_emit(state, {"name": TRANCHE_B_EVENT})
    assert state.emit_count == 0  # rejected call never counted as a real emit


def test_handle_event_verify_accepts_tranche_b_event(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    result = event_bus.handle_event_verify(state, {"name": TRANCHE_B_EVENT})
    assert result["decision"] == "allow"
    assert result["tranche"] == "B"
    assert result["fail_policy"] == event_bus.FAIL_POLICY_DENY_CAPABLE
    assert state.verify_count == 1


def test_handle_event_verify_rejects_tranche_a_event(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    with pytest.raises(ValueError, match="use event.emit"):
        event_bus.handle_event_verify(state, {"name": TRANCHE_A_EVENT})
    assert state.verify_count == 0


def test_handle_event_emit_unknown_event_raises(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    with pytest.raises(ValueError, match="unknown event"):
        event_bus.handle_event_emit(state, {"name": "not.a.real.event"})


def test_handle_event_emit_requires_name(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    with pytest.raises(ValueError, match="requires name"):
        event_bus.handle_event_emit(state, {})


# ── health.ping / governance.reload / span.emit ─────────────────────────────


def test_handle_health_ping_reports_resident_state(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    result = event_bus.handle_health_ping(state, {})
    assert result["status"] == "ok"
    assert result["event_count"] == 16
    assert result["resident_version"] != "no-taxonomy"
    assert result["loaded_at"]


def test_handle_governance_reload_rehydrates_after_taxonomy_change(tmp_path) -> None:
    taxonomy_path = tmp_path / "proj" / "nexus-foundation" / "plans" / "artifacts" / "event-taxonomy.json"
    taxonomy_path.parent.mkdir(parents=True)
    taxonomy_path.write_text(json.dumps({
        "fail_policy_classes": {},
        "events": [
            {"name": "a.one", "tranche": "A", "fail_policy": "advisory-fail-open"},
        ],
    }))
    state = event_bus.EventBusState(taxonomy_path.parents[3])
    assert state.taxonomy.event_count == 1

    unchanged = event_bus.handle_governance_reload(state, {})
    assert unchanged["reloaded"] is True
    assert unchanged["changed"] is False
    assert unchanged["event_count"] == 1

    taxonomy_path.write_text(json.dumps({
        "fail_policy_classes": {},
        "events": [
            {"name": "a.one", "tranche": "A", "fail_policy": "advisory-fail-open"},
            {"name": "b.two", "tranche": "B", "fail_policy": "deny-capable-fail-closed"},
        ],
    }))
    changed = event_bus.handle_governance_reload(state, {})
    assert changed["changed"] is True
    assert changed["event_count"] == 2
    assert state.taxonomy.get("b.two").tranche == "B"


def test_handle_span_emit_accepts_and_counts(project_with_real_taxonomy) -> None:
    """Payload shape updated for F2-05's stricter write-boundary validation
    (notepad #331) — `span_id`/`kind` are now required; see test_spans.py
    for the full shape-validation + DuckDB-persistence coverage."""
    state = event_bus.EventBusState(project_with_real_taxonomy)
    r1 = event_bus.handle_span_emit(
        state, {"span": {"trace_id": "t1", "span_id": "s1", "name": "dispatch", "kind": "dispatch"}}
    )
    assert r1["accepted"] is True
    r2 = event_bus.handle_span_emit(
        state,
        {"span": {"trace_id": "t1", "span_id": "s2", "parent_span_id": "s1", "name": "gate", "kind": "gate"}},
    )
    assert r2["accepted"] is True
    assert state.span_count == 2
    state.close_span_store()


def test_handle_span_emit_requires_span_dict(project_with_real_taxonomy) -> None:
    state = event_bus.EventBusState(project_with_real_taxonomy)
    with pytest.raises(ValueError, match="span:dict"):
        event_bus.handle_span_emit(state, {})


# ── server.handle_request end-to-end dispatch (in-process, no socket) ──────


def test_handle_request_dispatches_all_five_bus_methods(project_with_real_taxonomy) -> None:
    state = DaemonState(project_with_real_taxonomy)

    emit = handle_request(state, "event.emit", {"name": TRANCHE_A_EVENT})
    assert emit["ok"] is True

    verify = handle_request(state, "event.verify", {"name": TRANCHE_B_EVENT})
    assert verify["decision"] == "allow"

    ping = handle_request(state, "health.ping", {})
    assert ping["status"] == "ok"

    reload_result = handle_request(state, "governance.reload", {})
    assert reload_result["reloaded"] is True

    span = handle_request(
        state, "span.emit", {"span": {"trace_id": "t1", "span_id": "s1", "name": "dispatch", "kind": "dispatch"}}
    )
    assert span["accepted"] is True
    state.event_bus.close_span_store()


def test_handle_request_unknown_method_still_raises(project) -> None:
    state = DaemonState(project)
    with pytest.raises(ValueError, match="unknown method"):
        handle_request(state, "not.a.real.method", {})


# ── _daemon_rpc.py fail-policy CLIENT contract (constraint 2, gotcha #327) ──


def test_call_unchanged_still_returns_none_on_miss(project, isolated_sockets) -> None:
    """Regression guard: existing callers' `None -> fall back inline`
    contract must stay byte-for-byte unchanged by the new wrappers."""
    daemon_rpc = _daemon_rpc_module()
    assert daemon_rpc.call(project, "health.ping", {}, 0.2) is None


def test_call_advisory_fails_open_on_daemon_miss(project, isolated_sockets) -> None:
    daemon_rpc = _daemon_rpc_module()
    result = daemon_rpc.call_advisory(project, "event.emit", {"name": TRANCHE_A_EVENT}, 0.2)
    assert result == {"ok": True, "fail_open": True, "reason": "daemon-miss"}


def test_call_deny_capable_fails_closed_on_daemon_miss(project, isolated_sockets) -> None:
    daemon_rpc = _daemon_rpc_module()
    result = daemon_rpc.call_deny_capable(project, "event.verify", {"name": TRANCHE_B_EVENT}, 0.2)
    assert result == {"decision": "deny", "fail_closed": True, "reason": "daemon-miss"}


def test_advisory_and_deny_capable_miss_shapes_are_structurally_distinct(project, isolated_sockets) -> None:
    """The bus's core safety property (notepad gotcha #327): a caller cannot
    mistake one policy's miss-shape for the other's. Fail-open never denies;
    fail-closed never silently allows."""
    daemon_rpc = _daemon_rpc_module()
    advisory = daemon_rpc.call_advisory(project, "event.emit", {"name": TRANCHE_A_EVENT}, 0.2)
    deny_capable = daemon_rpc.call_deny_capable(project, "event.verify", {"name": TRANCHE_B_EVENT}, 0.2)

    assert set(advisory) == {"ok", "fail_open", "reason"}
    assert set(deny_capable) == {"decision", "fail_closed", "reason"}
    assert "decision" not in advisory
    assert "ok" not in deny_capable
    assert deny_capable["decision"] == "deny"  # never silently "allow" on daemon death
    assert advisory["ok"] is True  # never blocks/denies on daemon death


# ── real unix-socket transport (spawned daemon subprocess) ─────────────────


@pytest.mark.slow
def test_health_ping_over_real_socket_transport(
    project_with_real_taxonomy, isolated_sockets, spawned_daemons
) -> None:
    daemon_rpc = _daemon_rpc_module()
    proc = _spawn_daemon_process(project_with_real_taxonomy)
    try:
        health = _wait_for_health_ping(daemon_rpc, project_with_real_taxonomy)
        spawned_daemons.append(proc.pid)
        assert health["status"] == "ok"
        assert health["event_count"] == 16
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.slow
def test_event_emit_and_verify_over_real_socket_transport(
    project_with_real_taxonomy, isolated_sockets, spawned_daemons, snapshot: SnapshotAssertion
) -> None:
    daemon_rpc = _daemon_rpc_module()
    proc = _spawn_daemon_process(project_with_real_taxonomy)
    try:
        _wait_for_health_ping(daemon_rpc, project_with_real_taxonomy)
        spawned_daemons.append(proc.pid)

        emit = daemon_rpc.call(project_with_real_taxonomy, "event.emit", {"name": TRANCHE_A_EVENT}, 1.0)
        # envelope fixture: the same event.emit shape as the in-process test
        # above, this time over the real unix-socket transport — reviewed
        # via snapshot (F3-04).
        assert emit == snapshot(name="emit_envelope")

        verify = daemon_rpc.call(project_with_real_taxonomy, "event.verify", {"name": TRANCHE_B_EVENT}, 1.0)
        assert verify["decision"] == "allow"
        assert verify["tranche"] == "B"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.slow
def test_call_advisory_and_deny_capable_pass_through_real_daemon_result_unchanged(
    project_with_real_taxonomy, isolated_sockets, spawned_daemons
) -> None:
    """With a REACHABLE daemon, the wrappers must return the daemon's real
    answer verbatim — the fail-open/fail-closed miss-shapes are a
    daemon-UNREACHABLE-only behaviour, never substituted over a real hit."""
    daemon_rpc = _daemon_rpc_module()
    proc = _spawn_daemon_process(project_with_real_taxonomy)
    try:
        _wait_for_health_ping(daemon_rpc, project_with_real_taxonomy)
        spawned_daemons.append(proc.pid)

        advisory = daemon_rpc.call_advisory(
            project_with_real_taxonomy, "event.emit", {"name": TRANCHE_A_EVENT}, 1.0
        )
        assert "fail_open" not in advisory
        assert advisory["ok"] is True

        deny_capable = daemon_rpc.call_deny_capable(
            project_with_real_taxonomy, "event.verify", {"name": TRANCHE_B_EVENT}, 1.0
        )
        assert "fail_closed" not in deny_capable
        assert deny_capable["decision"] == "allow"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

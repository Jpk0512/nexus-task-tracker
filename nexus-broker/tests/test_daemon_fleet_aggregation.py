"""N16 (plans/13-r4-conductor-lane-plan.md, Phase B) — plans/08 items 2.3
(cross-project registry/task/decision queries) + 2.4 (fleet gate-block
rollup) as ONE thin client-side aggregation surface: `broker.daemon.fleet`.

Per plan 13's own N16 cross-release note, the aggregation code path is
"buildable and testable NOW at N=1" — this repo is the only registered
project with a real daemon. The N>1 fleet path is therefore exercised with
one REAL fixture project (a genuine spawned daemon, a real socket, real
`query_registry`/`health` RPCs) plus one SYNTHETIC second project (its
"socket" is a monkeypatched `broker.daemon.client.call`, standing in for a
second project's not-yet-existing daemon) — exactly the "fixture
second-project socket" plan 13 §2.B names. The gate-block half (2.4) needs
no daemon at all — it reads each project's own `.memory/files/gate_blocks.jsonl`
directly, so its fleet (2-project) tests use two plain fixture directories.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import tempfile
from pathlib import Path
from typing import Any

import pytest

from broker.daemon import client as daemon_client
from broker.daemon import fleet
from broker.daemon.client import DaemonUnavailable
from broker.daemon.fleet import (
    FleetQueryResult,
    GateBlockRollup,
    ProjectRef,
    fleet_call,
    fleet_gate_block_rollup,
    query_fleet_registry,
)

BROKER_ROOT = Path(__file__).resolve().parent.parent  # nexus-broker/

REAL_AGENT_MD = """---
name: real-agent
description: "The real fixture project's own persona."
model: sonnet
skills:
  - agent-protocol
---

# Real Agent
"""

REAL_SKILL_MD = """---
name: real-skill
description: "The real fixture project's own skill."
metadata: {tier: sonnet}
---

# Real Skill
"""


def _make_real_project(root: Path) -> Path:
    project = root / "real-proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    (project / ".claude" / "skills" / "real-skill").mkdir(parents=True)
    (project / ".memory").mkdir(parents=True)
    (project / ".claude" / "agents" / "real-agent.md").write_text(REAL_AGENT_MD)
    (project / ".claude" / "skills" / "real-skill" / "SKILL.md").write_text(REAL_SKILL_MD)
    return project


# ── shared fixtures (self-contained — this file owns its own daemon lifecycle) ──


@pytest.fixture()
def isolated_sockets(monkeypatch):
    # AF_UNIX paths are capped at ~104 bytes on macOS/BSD; pytest's tmp_path
    # is too deeply nested for bind() to succeed reliably, so force a short
    # dir directly under /tmp (same workaround test_daemon_pilot.py uses).
    sock_dir = Path(tempfile.mkdtemp(prefix="nxdfleet", dir="/tmp"))
    monkeypatch.setenv("NEXUS_DAEMON_SOCKET_DIR", str(sock_dir))
    yield sock_dir
    shutil.rmtree(sock_dir, ignore_errors=True)


@pytest.fixture()
def spawned_daemons():
    """Tracks PIDs spawned via the client's spawn-on-demand path so this
    suite never leaks a resident daemon process across runs."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


@pytest.fixture()
def real_project(tmp_path) -> Path:
    return _make_real_project(tmp_path)


@pytest.fixture()
def synthetic_project(tmp_path) -> Path:
    """A second project directory that never gets a real daemon — its
    filesystem shape exists (so path-based operations like the gate-block
    reader are genuine), but its socket RPCs are monkeypatched per-test."""
    proj = tmp_path / "synthetic-proj"
    proj.mkdir(parents=True)
    return proj


def _patch_synthetic_daemon_call(
    monkeypatch, synthetic_path: Path, canned_by_method: dict[str, Any]
) -> None:
    """Monkeypatch `broker.daemon.client.call` so calls targeting
    `synthetic_path` return canned data (simulating a second project's
    daemon/socket) while calls to any OTHER project path fall through to the
    real implementation untouched — the real fixture project still talks to
    its genuine spawned daemon over its genuine socket.
    """
    real_call = daemon_client.call

    def _fake_call(project_path, method, params=None, **kwargs):
        if Path(project_path) == synthetic_path:
            if method not in canned_by_method:
                raise DaemonUnavailable(f"synthetic daemon has no canned answer for {method!r}")
            return canned_by_method[method]
        return real_call(project_path, method, params, **kwargs)

    monkeypatch.setattr(fleet.daemon_client, "call", _fake_call)


# ── 2.3 — cross-project registry aggregation over >=2 sockets ──────────────


@pytest.mark.slow
def test_query_fleet_registry_aggregates_two_projects_with_namespacing(
    real_project, synthetic_project, isolated_sockets, spawned_daemons, monkeypatch
) -> None:
    health = daemon_client.call(real_project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    synthetic_entries = {
        "entries": [
            {
                "kind": "agent",
                "name": "synthetic-agent",
                "description": "Lives only on the synthetic second project.",
                "model": "opus",
                "skills": [],
            }
        ]
    }
    _patch_synthetic_daemon_call(
        monkeypatch, synthetic_project, {"query_registry": synthetic_entries}
    )

    real_ref = ProjectRef(real_project, label="real")
    synth_ref = ProjectRef(synthetic_project, label="synthetic")

    result = query_fleet_registry([real_ref, synth_ref], query_context=None)

    assert isinstance(result, FleetQueryResult)
    assert result.errors == {}
    assert set(result.ok_labels) == {"real", "synthetic"}

    # Per-project namespacing preserved: each project's answer only ever
    # contains ITS OWN entries — no cross-project bleed.
    real_names = {e["name"] for e in result.by_project["real"]["entries"]}
    synth_names = {e["name"] for e in result.by_project["synthetic"]["entries"]}
    assert real_names == {"real-agent", "real-skill"}
    assert synth_names == {"synthetic-agent"}
    assert real_names.isdisjoint(synth_names)


@pytest.mark.slow
def test_query_fleet_registry_query_context_narrows_each_project_independently(
    real_project, synthetic_project, isolated_sockets, spawned_daemons, monkeypatch
) -> None:
    health = daemon_client.call(real_project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    # The synthetic daemon "narrows" too — a real per-project daemon would
    # apply query_registry's own filtering; the fake mirrors that contract.
    _patch_synthetic_daemon_call(
        monkeypatch,
        synthetic_project,
        {"query_registry": {"entries": []}},  # nothing on this project matches
    )

    result = query_fleet_registry(
        [ProjectRef(real_project, label="real"), ProjectRef(synthetic_project, label="synthetic")],
        query_context="real-agent",
    )
    assert [e["name"] for e in result.by_project["real"]["entries"]] == ["real-agent"]
    assert result.by_project["synthetic"]["entries"] == []


@pytest.mark.slow
def test_query_fleet_registry_partial_failure_does_not_abort_the_fleet_call(
    real_project, synthetic_project, isolated_sockets, spawned_daemons
) -> None:
    health = daemon_client.call(real_project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    # synthetic_project has no daemon and spawning is disabled -> unreachable.
    result = query_fleet_registry(
        [ProjectRef(real_project, label="real"), ProjectRef(synthetic_project, label="synthetic")],
        spawn_if_missing=False,
        connect_timeout=0.3,
    )

    assert "real" in result.by_project
    assert "synthetic" not in result.by_project
    assert "synthetic" in result.errors
    assert result.ok_labels == ["real"]


def test_fleet_call_is_generic_over_rpc_method(
    real_project, synthetic_project, isolated_sockets, spawned_daemons, monkeypatch
) -> None:
    """fleet_call is not registry-specific — the same primitive serves ANY
    method a per-project daemon answers (2.3's "registry/task/decision"
    framing: this is the query-agnostic layer those all ride on)."""
    health = daemon_client.call(real_project, "health", spawn_wait_s=10.0)
    spawned_daemons.append(health["pid"])

    synthetic_health = {"status": "ok", "pid": 999999, "project_path": str(synthetic_project)}
    _patch_synthetic_daemon_call(monkeypatch, synthetic_project, {"health": synthetic_health})

    result = fleet_call(
        [ProjectRef(real_project, label="real"), ProjectRef(synthetic_project, label="synthetic")],
        "health",
    )
    assert result.by_project["real"]["status"] == "ok"
    assert result.by_project["real"]["pid"] == health["pid"]
    assert result.by_project["synthetic"] == synthetic_health


def test_project_ref_defaults_label_to_directory_name(real_project) -> None:
    ref = ProjectRef(real_project)
    assert ref.label == real_project.name
    assert ref.project_path == real_project


# ── 2.4 — fleet gate-block rollup, no daemon required ───────────────────────


def _write_gate_blocks(project: Path, rows: list[dict[str, Any]]) -> None:
    sink = project / fleet.GATE_BLOCKS_RELATIVE_PATH
    sink.parent.mkdir(parents=True, exist_ok=True)
    with open(sink, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _block_row(hook: str, code: str = "SOME-CODE") -> dict[str, Any]:
    return {
        "ts": "2026-07-09T00:00:00+00:00",
        "event": "PreToolUse",
        "hook": hook,
        "code": code,
        "reason": f"blocked by {hook}",
    }


def test_gate_block_rollup_single_project_counts_by_hook(real_project) -> None:
    _write_gate_blocks(
        real_project,
        [
            _block_row("LENS"),
            _block_row("LENS"),
            _block_row("BROKER"),
        ],
    )
    rollup = fleet_gate_block_rollup([ProjectRef(real_project, label="real")])

    assert isinstance(rollup, GateBlockRollup)
    assert rollup.by_hook == {"LENS": 2, "BROKER": 1}
    assert rollup.by_project == {"real": {"LENS": 2, "BROKER": 1}}
    assert rollup.total_blocks == 3
    assert rollup.top_hook == ("LENS", 2)


def test_gate_block_rollup_aggregates_across_fleet_and_keeps_per_project_breakdown(
    real_project, synthetic_project
) -> None:
    _write_gate_blocks(real_project, [_block_row("LENS"), _block_row("BROKER")])
    _write_gate_blocks(
        synthetic_project,
        [_block_row("BROKER"), _block_row("BROKER"), _block_row("WORKTREE")],
    )

    rollup = fleet_gate_block_rollup(
        [ProjectRef(real_project, label="real"), ProjectRef(synthetic_project, label="synthetic")]
    )

    # Fleet-wide: BROKER (1 + 2 = 3) beats LENS (1) even though neither
    # single project alone shows BROKER as its own top hook by a wide
    # margin — proving this is a genuine fleet-wide rollup, not just one
    # project's local counts relabeled.
    assert rollup.by_hook == {"LENS": 1, "BROKER": 3, "WORKTREE": 1}
    assert rollup.total_blocks == 5
    assert rollup.top_hook == ("BROKER", 3)

    # Per-project breakdown preserved (namespacing, same as 2.3).
    assert rollup.by_project["real"] == {"LENS": 1, "BROKER": 1}
    assert rollup.by_project["synthetic"] == {"BROKER": 2, "WORKTREE": 1}


def test_gate_block_rollup_missing_sink_is_zero_not_an_error(real_project) -> None:
    # No gate_blocks.jsonl ever written for this project.
    rollup = fleet_gate_block_rollup([ProjectRef(real_project, label="real")])
    assert rollup.by_hook == {}
    assert rollup.by_project == {"real": {}}
    assert rollup.total_blocks == 0
    assert rollup.top_hook is None


def test_gate_block_rollup_skips_malformed_lines_without_raising(real_project) -> None:
    sink = real_project / fleet.GATE_BLOCKS_RELATIVE_PATH
    sink.parent.mkdir(parents=True, exist_ok=True)
    with open(sink, "w", encoding="utf-8") as f:
        f.write(json.dumps(_block_row("LENS")) + "\n")
        f.write("{not valid json at all\n")
        f.write("\n")  # blank line
        f.write(json.dumps(_block_row("LENS")) + "\n")

    rollup = fleet_gate_block_rollup([ProjectRef(real_project, label="real")])
    assert rollup.by_hook == {"LENS": 2}
    assert rollup.total_blocks == 2


def test_gate_block_rollup_never_opens_a_sqlite_connection(
    real_project, synthetic_project, monkeypatch
) -> None:
    """Structural proof of the 'no hand-written cross-project SQL join'
    acceptance criterion: the rollup must not even import/touch sqlite3."""
    import sqlite3

    def _forbidden_connect(*_args, **_kwargs):
        raise AssertionError("fleet_gate_block_rollup must never open a sqlite3 connection")

    monkeypatch.setattr(sqlite3, "connect", _forbidden_connect)

    _write_gate_blocks(real_project, [_block_row("LENS")])
    _write_gate_blocks(synthetic_project, [_block_row("BROKER")])
    # No project.db exists for either fixture project at all — proving the
    # rollup is computable with zero database access of any kind.
    assert not (real_project / ".memory" / "project.db").exists()
    assert not (synthetic_project / ".memory" / "project.db").exists()

    rollup = fleet_gate_block_rollup(
        [ProjectRef(real_project, label="real"), ProjectRef(synthetic_project, label="synthetic")]
    )
    assert rollup.total_blocks == 2
    assert rollup.top_hook in {("LENS", 1), ("BROKER", 1)}


# ── no daemon-of-daemons — structural check ─────────────────────────────────


def test_fleet_module_spawns_no_new_daemon_process_class(real_project, synthetic_project) -> None:
    """fleet.py must not define or import any server/listener machinery of
    its own — it is a client over EXISTING per-project daemons/files only."""
    import inspect

    source = inspect.getsource(fleet)
    assert "start_unix_server" not in source
    assert "SOCK_STREAM" not in source
    # The only daemon touchpoint is the already-existing per-project client.
    assert "from broker.daemon import client as daemon_client" in source

"""Tests for R4-T04: broker.conductor.governance — in-process governance for
the conductor lane (plan-13 N07).

No live `claude`/`codex` binary anywhere in this suite (same convention as
test_conductor_dag.py). The lens-gate v2 tests use a real scratch project.db
built via `.memory/log.py init` / `validation add` (NEXUS_DB_PATH-pointed,
same pattern as test_conductor_dag.py's
test_record_dispatch_telemetry_feeds_same_table_for_both_executors) — never
the live repo DB.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from broker.conductor import dag as dag_mod
from broker.conductor import governance

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_PY = _REPO_ROOT / ".memory" / "log.py"


# ---------------------------------------------------------------------------
# fixture helpers (mirrors test_conductor_dag.py's _node)
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    downstream_consumers: list[str] | None = None,
    write_scope: list[str] | None = None,
    required_lens_types: list[str] | None = None,
    agent_persona: str = "pipeline-async",
) -> dict:
    node = {
        "node_id": node_id,
        "depends_on": depends_on or [],
        "downstream_consumers": downstream_consumers or [],
        "agent_persona": agent_persona,
        "goal": f"do the work for {node_id}",
        "context_files": [],
        "acceptance_criteria": [f"{node_id} completes"],
        "verification_method": {"type": "command", "command": f"echo {node_id}"},
        "risk_tier": "T2",
        "skills_required": ["agent-protocol"],
        "do_not_touch": [],
        "budget": "S",
        "irreversible": False,
    }
    if write_scope is not None:
        node["write_scope"] = write_scope
    if required_lens_types is not None:
        node["required_lens_types"] = required_lens_types
    return node


def _init_scratch_db(tmp_path: Path) -> Path:
    db = tmp_path / "project.db"
    init = subprocess.run(
        [sys.executable, str(_LOG_PY), "init"],
        capture_output=True, text=True,
        env={**os.environ, "NEXUS_DB_PATH": str(db), "NEXUS_DISABLE_VEC": "1"},
    )
    assert init.returncode == 0, init.stderr
    return db


def _record_pass_row(
    db: Path, *, target: str, task_hash: str, lens_type: str, risk_tier: str | None = None,
) -> None:
    result = subprocess.run(
        [
            sys.executable, str(_LOG_PY), "validation", "add",
            "--agent", "lens", "--target", target, "--task-hash", task_hash,
            "--verdict", "PASS", "--lens-type", lens_type,
            "--risk-tier", risk_tier or lens_type, "--summary", "test PASS row",
        ],
        capture_output=True, text=True,
        env={**os.environ, "NEXUS_DB_PATH": str(db), "NEXUS_DISABLE_VEC": "1"},
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# 1a. allowedTools grant (claude legs)
# ---------------------------------------------------------------------------


def test_allowed_tools_for_node_read_only_grant_has_no_write_tools() -> None:
    node = _node("readonly")  # no write_scope at all
    grants = governance.allowed_tools_for_node(node)
    assert grants == ["Read", "Grep", "Glob"]
    assert not any(g.startswith("Edit") or g.startswith("Write") for g in grants)


def test_allowed_tools_for_node_empty_write_scope_is_also_read_only() -> None:
    node = _node("readonly2", write_scope=[])
    grants = governance.allowed_tools_for_node(node)
    assert grants == ["Read", "Grep", "Glob"]


def test_allowed_tools_for_node_grants_scoped_edit_write_per_glob() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    grants = governance.allowed_tools_for_node(node)
    assert "Edit(nexus-broker/src/broker/conductor/**)" in grants
    assert "Write(nexus-broker/src/broker/conductor/**)" in grants
    assert {"Read", "Grep", "Glob"} <= set(grants)


def test_build_worker_templates_wires_allowed_tools_from_governance() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    templates = dag_mod.build_worker_templates({"n1": node}, cwd_root=str(_REPO_ROOT))
    assert templates["n1"].allowed_tools == governance.allowed_tools_for_node(node)
    assert "Edit(nexus-broker/src/broker/conductor/**)" in templates["n1"].allowed_tools


# ---------------------------------------------------------------------------
# 2. PreToolUse-equivalent scope callback — in-process deny + record
# ---------------------------------------------------------------------------


def test_check_write_scope_in_scope_path_returns_none() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    denial = governance.check_write_scope(
        node, tool="Write", path="nexus-broker/src/broker/conductor/governance.py",
    )
    assert denial is None


def test_check_write_scope_out_of_scope_path_returns_denial() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    denial = governance.check_write_scope(node, tool="Write", path="app/secrets.py")
    assert denial is not None
    assert denial.node_id == "n1"
    assert denial.tool == "Write"
    assert denial.attempted_path == "app/secrets.py"
    assert "app/secrets.py" in denial.reason


def test_out_of_scope_write_attempt_by_stub_worker_is_denied_in_process_and_recorded() -> None:
    """The core R4-T04 acceptance criterion: a stub worker attempting a write
    outside its node's write_scope is denied BEFORE the write happens (no
    file is ever created), and the denial is recorded."""
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    audit_log: list[governance.ScopeDenial] = []
    disk: dict[str, str] = {}

    def stub_worker_write(path: str, content: str) -> None:
        governance.enforce_write_scope(node, tool="Write", path=path, audit_log=audit_log)
        disk[path] = content  # only reached if the scope check allowed it

    with pytest.raises(governance.ScopeViolation) as excinfo:
        stub_worker_write(".claude/hooks/lens-gate.sh", "malicious payload")

    assert "n1" in str(excinfo.value)
    assert ".claude/hooks/lens-gate.sh" not in disk, "the out-of-scope write must never reach disk"
    assert len(audit_log) == 1
    assert audit_log[0].attempted_path == ".claude/hooks/lens-gate.sh"
    assert audit_log[0].tool == "Write"

    # an in-scope write from the SAME stub worker is unaffected
    stub_worker_write("nexus-broker/src/broker/conductor/new_file.py", "ok")
    assert disk["nexus-broker/src/broker/conductor/new_file.py"] == "ok"
    assert len(audit_log) == 1, "the in-scope write must not add a spurious denial"


def test_scope_callback_denies_and_returns_hook_shaped_decision() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    decision = governance.scope_callback(
        node, tool_name="Edit", tool_input={"file_path": "app/secrets.py"},
    )
    assert decision["decision"] == "deny"
    assert decision["attempted_path"] == "app/secrets.py"


def test_scope_callback_allows_in_scope_edit() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    decision = governance.scope_callback(
        node, tool_name="Edit", tool_input={"file_path": "nexus-broker/src/broker/conductor/dag.py"},
    )
    assert decision == {"decision": "allow"}


def test_scope_callback_passes_through_non_write_tools_untouched() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    for tool_name in ("Read", "Grep", "Glob", "Bash"):
        decision = governance.scope_callback(
            node, tool_name=tool_name, tool_input={"file_path": "app/anything.py"},
        )
        assert decision == {"decision": "allow"}, tool_name


def test_scope_callback_denies_every_write_when_write_scope_is_empty() -> None:
    node = _node("readonly")  # no write_scope: a read-only leg
    decision = governance.scope_callback(
        node, tool_name="Write", tool_input={"file_path": "nexus-broker/anything.py"},
    )
    assert decision["decision"] == "deny"
    assert "read-only" in decision["reason"]


# ---------------------------------------------------------------------------
# 1b. codex sandbox mapping table (SS9.5)
# ---------------------------------------------------------------------------


def test_codex_sandbox_flags_read_only_for_empty_write_scope() -> None:
    flags = governance.codex_sandbox_flags_for_write_scope([], worktree="/tmp/wt")
    assert flags.sandbox == "read-only"
    assert flags.cd == "/tmp/wt"
    assert flags.add_dirs == []
    assert flags.to_argv() == ["-s", "read-only", "-C", "/tmp/wt"]


def test_codex_sandbox_flags_workspace_write_for_bounded_scope() -> None:
    flags = governance.codex_sandbox_flags_for_write_scope(
        ["nexus-broker/src/broker/conductor/**"], worktree="/tmp/wt",
    )
    assert flags.sandbox == "workspace-write"
    assert flags.cd == "/tmp/wt"
    assert flags.add_dirs == []
    assert flags.to_argv() == ["-s", "workspace-write", "-C", "/tmp/wt"]


def test_codex_sandbox_flags_raises_on_unmappable_write_scope() -> None:
    with pytest.raises(ValueError, match="no expressible codex sandbox"):
        governance.codex_sandbox_flags_for_write_scope(["**/*"], worktree="/tmp/wt")


def test_scope_grant_for_node_bundles_claude_and_codex_with_coarser_note() -> None:
    node = _node("n1", write_scope=["nexus-broker/src/broker/conductor/**"])
    grant = governance.scope_grant_for_node(node, worktree="/tmp/wt")

    assert grant.node_id == "n1"
    assert grant.persona == "pipeline-async"
    assert grant.allowed_tools == governance.allowed_tools_for_node(node)
    assert grant.codex_sandbox.sandbox == "workspace-write"
    assert grant.codex_sandbox.cd == "/tmp/wt"
    # the SAME write_scope narrows claude's grant per-glob but NOT codex's —
    # the documented coarser-enforcement bound this node exists to record.
    assert "Edit(nexus-broker/src/broker/conductor/**)" in grant.allowed_tools
    assert grant.codex_sandbox.add_dirs == []
    assert "coarser" in grant.coarser_enforcement_note.lower()
    assert "codex" in grant.coarser_enforcement_note.lower()


def test_scope_grant_for_node_read_only_case() -> None:
    node = _node("readonly")
    grant = governance.scope_grant_for_node(node, worktree="/tmp/wt")
    assert grant.allowed_tools == ["Read", "Grep", "Glob"]
    assert grant.codex_sandbox.sandbox == "read-only"


# ---------------------------------------------------------------------------
# 3. lens-gate v2 assertion — native, in-process
# ---------------------------------------------------------------------------


def test_assert_lens_gate_v2_no_requirement_is_trivially_satisfied(tmp_path: Path) -> None:
    node = _node("n1")  # no required_lens_types
    ok, detail = governance.assert_lens_gate_v2(node, db_path=tmp_path / "does-not-exist.db")
    assert ok is True
    assert "no tier requirement" in detail


def test_assert_lens_gate_v2_missing_pass_row_denies(tmp_path: Path) -> None:
    db = _init_scratch_db(tmp_path)
    node = _node("n1", required_lens_types=["T2"], agent_persona="pipeline-async")

    ok, detail = governance.assert_lens_gate_v2(node, db_path=db)

    assert ok is False
    assert "T2" in detail
    assert "missing" in detail


def test_assert_lens_gate_v2_stale_tier_does_not_satisfy_required_tier(tmp_path: Path) -> None:
    """A PASS row exists but at the WRONG tier (T1) — must not satisfy a T2
    requirement (the exact gap R1-T08/lens-gate.sh v2 closes)."""
    db = _init_scratch_db(tmp_path)
    node = _node("n1", required_lens_types=["T2"], agent_persona="pipeline-async")
    _record_pass_row(db, target="pipeline-async", task_hash="n1", lens_type="T1")

    ok, detail = governance.assert_lens_gate_v2(node, db_path=db)

    assert ok is False
    assert "T1" in detail  # satisfied set is visible in the deny detail
    assert "T2" in detail


def test_assert_lens_gate_v2_satisfied_with_matching_pass_row(tmp_path: Path) -> None:
    db = _init_scratch_db(tmp_path)
    node = _node("n1", required_lens_types=["T2"], agent_persona="pipeline-async")
    _record_pass_row(db, target="pipeline-async", task_hash="n1", lens_type="T2")

    ok, detail = governance.assert_lens_gate_v2(node, db_path=db)

    assert ok is True
    assert "T2" in detail


def test_assert_lens_gate_v2_wrong_target_agent_does_not_satisfy(tmp_path: Path) -> None:
    """A PASS row recorded against a DIFFERENT persona must not satisfy this
    node's requirement — target_agent is part of the match key."""
    db = _init_scratch_db(tmp_path)
    node = _node("n1", required_lens_types=["T2"], agent_persona="pipeline-async")
    _record_pass_row(db, target="forge-ui", task_hash="n1", lens_type="T2")

    ok, _detail = governance.assert_lens_gate_v2(node, db_path=db)

    assert ok is False


def test_assert_lens_gate_v2_db_missing_fails_closed(tmp_path: Path) -> None:
    node = _node("n1", required_lens_types=["T2"])
    ok, detail = governance.assert_lens_gate_v2(node, db_path=tmp_path / "no-such.db")
    assert ok is False
    assert "unavailable" in detail or "unable" in detail.lower() or "no such" in detail.lower()


# ---------------------------------------------------------------------------
# wiring: lens-gate v2 gates the conductor's merge step in-lane
# ---------------------------------------------------------------------------


def _fake_dispatch_claude_ok(node, *, template, worker_id, claude_bin="claude"):
    telemetry = dag_mod.DispatchTelemetry(node["node_id"], "claude", True, 5, worker_id)
    return dag_mod.NodeResult(
        node["node_id"], "claude", True, worker_id, telemetry, payload={"wrote": "something"},
    )


def test_run_dag_blocks_merge_when_required_lens_types_not_satisfied(tmp_path: Path) -> None:
    """R4-T04 acceptance: a leg lacking its required distinct-lens PASS
    row(s) cannot merge, even though the underlying dispatch itself
    succeeded — verified IN-LANE via run_dag, not just the bare function."""
    db = _init_scratch_db(tmp_path)
    doc = {
        "schema_version": 2,
        "nodes": [
            _node("solo", required_lens_types=["T2"], agent_persona="pipeline-async"),
        ],
    }

    result = dag_mod.run_dag(
        doc, max_workers=1, dispatch_claude_fn=_fake_dispatch_claude_ok,
        dispatch_codex_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no codex node")),
        validation_db_path=db,
    )

    solo = result.results["solo"]
    assert solo.ok is False, "leg output must NOT merge without its required lens PASS row"
    assert solo.payload is None
    assert "lens-gate-v2" in solo.error


def test_run_dag_allows_merge_when_required_lens_types_satisfied(tmp_path: Path) -> None:
    db = _init_scratch_db(tmp_path)
    _record_pass_row(db, target="pipeline-async", task_hash="solo", lens_type="T2")
    doc = {
        "schema_version": 2,
        "nodes": [
            _node("solo", required_lens_types=["T2"], agent_persona="pipeline-async"),
        ],
    }

    result = dag_mod.run_dag(
        doc, max_workers=1, dispatch_claude_fn=_fake_dispatch_claude_ok,
        dispatch_codex_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no codex node")),
        validation_db_path=db,
    )

    solo = result.results["solo"]
    assert solo.ok is True
    assert solo.payload == {"wrote": "something"}


def test_run_dag_skips_lens_gate_check_when_no_requirement_declared() -> None:
    """A node with no required_lens_types is byte-identical to pre-N07
    behavior — no DB touched, no gate applied (existing R4-T03 fixtures never
    set this field and must be unaffected)."""
    doc = {"schema_version": 2, "nodes": [_node("solo")]}
    result = dag_mod.run_dag(
        doc, max_workers=1, dispatch_claude_fn=_fake_dispatch_claude_ok,
        dispatch_codex_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no codex node")),
        validation_db_path="/no/such/db/needed",
    )
    assert result.results["solo"].ok is True
    assert result.results["solo"].payload == {"wrote": "something"}


# ---------------------------------------------------------------------------
# module import surface
# ---------------------------------------------------------------------------


def test_conductor_governance_module_importable() -> None:
    import broker.conductor.governance  # noqa: F401

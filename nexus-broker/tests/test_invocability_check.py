"""Tests for broker.plan_validation.checks.invocability (R3-T05, N11).

Every plan leaf's dispatch primitive must be orchestrator-invocable (Workflow / Agent /
Monitor / Cron / RemoteTrigger / TeamCreate / inline) — never a user-only slash command
(/goal, /loop, /effort — DEC-020/DEC-024-PENDING). A node with no `dispatch_primitive`
declared makes no claim and passes trivially (the field is optional accept-tier metadata,
not yet in `node_contract.REQUIRED_NODE_FIELDS`).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from broker.plan_validation.checks.invocability import check_invocability
from broker.plan_validation.score import score_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
# The R3 plan DAG moved to the redesign archive at NATIVE-11-5 (docs/archive/
# nexus-redesign/). It is a real production-shaped plan the scorer must not
# choke on — repointed here (TASK-111b) from the pre-archive nexus-redesign/
# path so the live-only smokes below actually RUN in the meta-repo again
# (previously they skipped on EVERY tree, live path included). The archive dir
# is meta-repo-only, so the package snapshot still skips — genuinely live-only.
REAL_PLAN = REPO_ROOT / "docs" / "archive" / "nexus-redesign" / "plans" / "09-r3-plan-dag.md"


def _node(node_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "node_id": node_id,
        "depends_on": [],
        "downstream_consumers": [],
        "agent_persona": "scout",
        "goal": f"do the {node_id} thing",
        "context_files": [],
        "acceptance_criteria": ["done"],
        "verification_method": {"type": "command", "command": "true"},
        "risk_tier": "T0",
        "skills_required": [],
        "do_not_touch": [],
    }
    base.update(overrides)
    return base


def _doc(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": 2, "nodes": nodes}


# --- positive fixture: every invocable primitive named in the brief passes ----------------


def test_workflow_primitive_passes() -> None:
    doc = _doc([_node("W1", dispatch_primitive="Workflow")])
    verdict = check_invocability(doc)
    assert verdict.passed is True
    assert verdict.offending_node_ids == []


def test_agent_primitive_passes() -> None:
    doc = _doc([_node("A1", dispatch_primitive="Agent")])
    assert check_invocability(doc).passed is True


def test_monitor_primitive_passes() -> None:
    doc = _doc([_node("M1", dispatch_primitive="Monitor")])
    assert check_invocability(doc).passed is True


def test_cron_primitive_passes() -> None:
    doc = _doc([_node("C1", dispatch_primitive="CronCreate")])
    assert check_invocability(doc).passed is True


def test_loop_until_done_workflow_phrasing_passes() -> None:
    """A phrased primitive ('loop-until-done Workflow') still resolves to Workflow —
    the orchestrator's own emulation of /loop (DEC-023/024), not the /loop command itself."""
    doc = _doc([_node("L1", dispatch_primitive="loop-until-done Workflow")])
    assert check_invocability(doc).passed is True


def test_positive_fixture_all_invocable_leaves_pass() -> None:
    """Acceptance criterion 1: a positive fixture with Workflow/Agent/Monitor/Cron leaves
    passes as a whole plan."""
    doc = _doc(
        [
            _node("N1", dispatch_primitive="Workflow", downstream_consumers=["N2"]),
            _node("N2", dispatch_primitive="Agent", depends_on=["N1"], downstream_consumers=["N3"]),
            _node("N3", dispatch_primitive="Monitor", depends_on=["N2"], downstream_consumers=["N4"]),
            _node("N4", dispatch_primitive="Cron", depends_on=["N3"]),
        ]
    )
    verdict = check_invocability(doc)
    assert verdict.passed is True
    assert verdict.offending_node_ids == []
    assert verdict.details == []


# --- negative fixture: user-only slash commands fail with the offending node id -----------


def test_goal_slash_command_fails_with_offending_node_id() -> None:
    """Acceptance criterion 1: a negative fixture (a leaf requiring '/goal') fails with
    the offending node id."""
    doc = _doc([_node("BAD1", dispatch_primitive="/goal")])
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["BAD1"]
    assert verdict.details


def test_loop_slash_command_fails() -> None:
    doc = _doc([_node("BAD2", dispatch_primitive="/loop")])
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["BAD2"]


def test_effort_slash_command_fails() -> None:
    doc = _doc([_node("BAD3", dispatch_primitive="/effort")])
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["BAD3"]


def test_bare_unslashed_user_only_command_word_also_fails() -> None:
    doc = _doc([_node("BAD4", dispatch_primitive="goal")])
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["BAD4"]


def test_unrecognized_primitive_fails() -> None:
    doc = _doc([_node("BAD5", dispatch_primitive="carrier pigeon")])
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["BAD5"]


def test_negative_fixture_only_offending_node_flagged_not_the_whole_plan() -> None:
    """Only the leaf naming the non-invocable primitive is reported — a plan with one
    good leaf and one bad leaf identifies exactly the bad one."""
    doc = _doc(
        [
            _node("GOOD", dispatch_primitive="Workflow", downstream_consumers=["BAD"]),
            _node("BAD", dispatch_primitive="/goal", depends_on=["GOOD"]),
        ]
    )
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["BAD"]


# --- absent field makes no claim (same convention as check_write_disjoint) ----------------


def test_missing_dispatch_primitive_field_passes_trivially() -> None:
    doc = _doc([_node("N1")])
    verdict = check_invocability(doc)
    assert verdict.passed is True
    assert verdict.offending_node_ids == []


def test_non_string_dispatch_primitive_is_flagged() -> None:
    doc = _doc([_node("N1", dispatch_primitive=42)])
    verdict = check_invocability(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["N1"]


# --- acceptance criterion 2: wired into the default (non-opt-in) gate path ----------------


def test_invocability_wired_into_default_score_plan_output() -> None:
    """The check runs in the default score_plan() gate path — it is deterministic, so it
    is NOT opt-in behind a separate probe call."""
    doc = _doc([_node("N1", dispatch_primitive="Workflow")])
    result = score_plan(doc)
    assert "invocability" in result
    assert result["invocability"]["pass"] is True


def test_invocability_failure_propagates_to_overall_pass() -> None:
    doc = _doc([_node("N1", dispatch_primitive="/goal")])
    result = score_plan(doc)
    assert result["invocability"]["pass"] is False
    assert result["invocability"]["offending_node_ids"] == ["N1"]
    assert result["overall_pass"] is False


def test_default_score_plan_unaffected_when_no_node_declares_a_primitive() -> None:
    """Plans that don't yet use the (optional) dispatch_primitive field — e.g. the real
    R3 plan DAG today — are not penalized by this new check."""
    doc = _doc([_node("N1")])
    result = score_plan(doc)
    assert result["invocability"]["pass"] is True


@pytest.mark.skipif(
    not REAL_PLAN.exists(),
    reason="live-only: docs/archive/nexus-redesign/ is deliberately not shipped in the package snapshot",
)
def test_real_r3_plan_invocability_check_runs_without_error() -> None:
    """End-to-end: the real plan DAG scores without a crash through the wired-in check."""
    from broker.plan_validation.score import score_file

    result = score_file(REAL_PLAN)
    assert "invocability" in result
    assert isinstance(result["invocability"]["pass"], bool)


# --- static guard: deterministic-only, zero model calls ------------------------------------


def test_no_network_or_model_client_imported() -> None:
    src = (
        REPO_ROOT
        / "nexus-broker"
        / "src"
        / "broker"
        / "plan_validation"
        / "checks"
        / "invocability.py"
    ).read_text(encoding="utf-8")
    banned_tokens = (
        "import requests",
        "import httpx",
        "import urllib",
        "import socket",
        "anthropic",
        "openai",
        "import http.client",
    )
    for banned in banned_tokens:
        assert banned not in src, f"unexpected network/model dependency: {banned}"


@pytest.mark.skipif(
    not REAL_PLAN.exists(),
    reason="live-only: docs/archive/nexus-redesign/ is deliberately not shipped in the package snapshot",
)
def test_cli_score_json_includes_invocability_key() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "broker.plan_validation", "score", str(REAL_PLAN), "--json"],
        cwd=REPO_ROOT / "nexus-broker" / "src",
        capture_output=True,
        text=True,
        check=False,
    )
    import json

    payload = json.loads(proc.stdout)
    assert "invocability" in payload


def _run_all() -> int:
    """__main__ runner (NATIVE-6 integrity guard): executes every test_* function in
    this module and exits non-zero on any failure, for plain-script invocation."""
    import traceback

    failures = 0
    total = 0
    module = sys.modules[__name__]
    for name in dir(module):
        if not name.startswith("test_"):
            continue
        fn = getattr(module, name)
        if not callable(fn):
            continue
        total += 1
        try:
            fn()
        except Exception:  # noqa: BLE001 - intentional: report every failure, don't stop at the first
            failures += 1
            print(f"FAIL: {name}", file=sys.stderr)
            traceback.print_exc()
        else:
            print(f"PASS: {name}")
    print(f"\n{total - failures}/{total} passed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_run_all())

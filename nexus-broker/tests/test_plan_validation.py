"""Tests for broker.plan_validation — plan-validation gate deterministic core (R3-T04, N08).

Covers: verdict JSON shape, each of the five checks failing/passing on
targeted fixtures, end-to-end scoring of the real R3 plan DAG
(nexus-redesign/plans/09-r3-plan-dag.md) without a crash, the CLI, and the
deterministic-only static import guard.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from broker.plan_validation.plan_doc import load_plan_as_dag_doc
from broker.plan_validation.probes.stub_mutation import mutate_to_stub
from broker.plan_validation.score import score_file, score_plan
from broker.plan_validation.skill_map import parse_skill_map, required_skills_for

FIXTURES = Path(__file__).parent / "fixtures" / "plan_validation"
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PLAN = REPO_ROOT / "nexus-redesign" / "plans" / "09-r3-plan-dag.md"
FIXTURE_SKILL_MAP = FIXTURES / "skill_map_fixture.md"

VERDICT_KEYS = {"acyclic", "verification_concrete", "mece", "skills_derived", "write_disjoint"}


def test_good_plan_passes_all_checks() -> None:
    # Pinned to FIXTURE_SKILL_MAP, not DEFAULT_SKILL_MAP_PATH: the default resolves
    # relative to wherever this package physically lives, so this same call would read
    # a structurally different table (product-install vs. meta-repo skill names) when
    # this test is copied verbatim into the nexus-package/ snapshot — see
    # skill_map_fixture.md's own docstring.
    result = score_file(FIXTURES / "good_plan.md", skill_map_path=FIXTURE_SKILL_MAP)
    assert VERDICT_KEYS <= set(result)
    for key in VERDICT_KEYS:
        assert result[key]["pass"] is True, f"{key} unexpectedly failed: {result[key]}"
    assert result["overall_pass"] is True


def test_verdict_json_has_required_keys_each_pass_fail() -> None:
    result = score_file(FIXTURES / "good_plan.md")
    for key in VERDICT_KEYS:
        assert "pass" in result[key]
        assert "offending_node_ids" in result[key]


def test_write_collision_detected_with_offending_node_ids() -> None:
    result = score_file(FIXTURES / "bad_write_collision.md")
    assert result["write_disjoint"]["pass"] is False
    assert set(result["write_disjoint"]["offending_node_ids"]) == {"B1", "B2"}
    assert result["overall_pass"] is False


def test_write_collision_not_flagged_when_ordered() -> None:
    """B1/B3 both appear in the collision fixture's dependency chain (B3 depends on B1) —
    only the genuinely unordered pair (B1, B2) may be reported."""
    result = score_file(FIXTURES / "bad_write_collision.md")
    assert "B3" not in result["write_disjoint"]["offending_node_ids"]


def test_missing_required_skill_detected() -> None:
    result = score_file(FIXTURES / "bad_missing_skill.md")
    assert result["skills_derived"]["pass"] is False
    assert "C1" in result["skills_derived"]["offending_node_ids"]
    assert result["overall_pass"] is False


def test_acyclic_check_reuses_node_contract_cycle_detection() -> None:
    doc = load_plan_as_dag_doc(FIXTURES / "good_plan.md")
    # Introduce a cycle: A1 now depends on A2 (which already depends on A1).
    for node in doc["nodes"]:
        if node["node_id"] == "A1":
            node["depends_on"] = ["A2"]
    result = score_plan(doc)
    assert result["acyclic"]["pass"] is False
    assert result["overall_pass"] is False


def test_verification_concreteness_reuses_node_contract_prose_rejection() -> None:
    doc = load_plan_as_dag_doc(FIXTURES / "good_plan.md")
    for node in doc["nodes"]:
        if node["node_id"] == "A1":
            node["verification_method"] = {"type": "manual", "description": "eyeball it"}
    result = score_plan(doc)
    assert result["verification_concrete"]["pass"] is False
    assert "A1" in result["verification_concrete"]["offending_node_ids"]


@pytest.mark.skipif(
    not REAL_PLAN.exists(),
    reason="live-only: nexus-redesign/ is deliberately not shipped in the package snapshot",
)
def test_real_r3_plan_scores_end_to_end_without_error() -> None:
    """Acceptance criterion 2: scores nexus-redesign/plans/09-r3-plan-dag.md end-to-end
    without error. 'Without error' means the tool completes and returns well-formed
    verdict JSON — a real substantive FAIL on a specific check is a legitimate score,
    not a tool error (a gate that cannot fail is not a gate)."""
    result = score_file(REAL_PLAN)
    assert VERDICT_KEYS <= set(result)
    for key in VERDICT_KEYS:
        assert isinstance(result[key]["pass"], bool)
        assert isinstance(result[key]["offending_node_ids"], list)


@pytest.mark.skipif(
    not REAL_PLAN.exists(),
    reason="live-only: nexus-redesign/ is deliberately not shipped in the package snapshot",
)
def test_real_r3_plan_is_acyclic_and_verification_concrete() -> None:
    """The 20-node plan is a valid topological order (§4) with 20/20 concrete
    verification_methods (§7 checklist item 2) — these two checks must pass."""
    result = score_file(REAL_PLAN)
    assert result["acyclic"]["pass"] is True, result["acyclic"]["details"]
    assert result["verification_concrete"]["pass"] is True, result["verification_concrete"]["details"]


def test_skill_map_parser_resolves_pipeline_async_meta_fallback() -> None:
    skill_map = parse_skill_map(
        "| persona | work_type | skills |\n"
        "|---|---|---|\n"
        "| pipeline-async | * | agent-protocol, deployable-engineering |\n"
        "| pipeline-async | implement_ingestion | agent-protocol, deployable-engineering |\n"
    )
    resolved = required_skills_for(skill_map, "pipeline-async", "meta")
    assert "agent-protocol" in resolved
    assert "deployable-engineering" in resolved


@pytest.mark.skipif(
    not REAL_PLAN.exists(),
    reason="live-only: nexus-redesign/ is deliberately not shipped in the package snapshot",
)
def test_cli_score_json_matches_acceptance_pipeline() -> None:
    """Runs the EXACT verification_method pipeline from the N08 brief."""
    proc1 = subprocess.run(
        [sys.executable, "-m", "broker.plan_validation", "score", str(REAL_PLAN), "--json"],
        cwd=REPO_ROOT / "nexus-broker" / "src",
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc1.stdout)
    assert VERDICT_KEYS <= set(payload)


def test_no_network_or_model_client_imported_anywhere_in_package() -> None:
    """Static guard (acceptance criterion 3): the plan_validation package must import
    no network/model-call library anywhere, at any node in this leaf. Model-judged
    probes are a separate later leaf (N09, broker/plan_validation/probes/) and must
    never be imported by this package's default path."""
    pkg_dir = REPO_ROOT / "nexus-broker" / "src" / "broker" / "plan_validation"
    banned_tokens = (
        "import requests",
        "import httpx",
        "import urllib",
        "import socket",
        "anthropic",
        "openai",
        "import http.client",
    )
    py_files = sorted(pkg_dir.rglob("*.py"))
    assert py_files, "expected plan_validation package to contain .py files"
    for py_file in py_files:
        src = py_file.read_text(encoding="utf-8")
        for banned in banned_tokens:
            assert banned not in src, f"{py_file}: unexpected network/model dependency: {banned}"


# ---------------------------------------------------------------------------
# N09 probes wired into the live gate (N20 finding #1, R3-T15): the SAME CLI
# the live hook and every leaf's verification_method uses
# (`python -m broker.plan_validation score <file> --json`) must genuinely run
# N09's opt-in probes when gate_requires_probes(doc) is true, and run zero
# probes when it is not — reproducing N20's exact stub-mutation drill.
# ---------------------------------------------------------------------------


def _write_doc_as_plan_md(path: Path, doc: dict) -> None:
    """Serialize a node-contract DAG doc back into a fenced-yaml plan markdown
    file the CLI can score — mirrors plan_doc.py's own fence format."""
    import yaml

    content = "# generated fixture\n\n"
    for node in doc["nodes"]:
        content += f"```yaml\n{yaml.safe_dump(node, sort_keys=False)}```\n\n"
    path.write_text(content, encoding="utf-8")


def test_t0_plan_via_live_cli_runs_zero_probes() -> None:
    """Opt-in contract preserved through the live CLI: a T0 plan (good_plan.md)
    must not trip the probes at all — no 'probes' key in the scorer's JSON."""
    result = score_file(FIXTURES / "good_plan.md")
    assert "probes" not in result


def test_probes_wired_into_live_cli_baseline_passes_t2_plan() -> None:
    """Baseline leg of the drill: a clean T2 plan passes via the live CLI, and
    the 'probes' key is present — proving N09's probes actually ran (not
    silently skipped) because gate_requires_probes(doc) is true.

    --skill-map pins FIXTURE_SKILL_MAP rather than letting the CLI fall through
    to DEFAULT_SKILL_MAP_PATH: that default resolves relative to wherever this
    package physically lives, and t2_probe_ok.md's hermes/meta node is declared
    against the meta-repo table's vocabulary (agent-protocol,
    deployable-engineering) — the package snapshot's own product-install
    SKILL_MAP.md would otherwise flag it for missing Tableau/Azure domain
    skills that have nothing to do with this probes-wiring drill.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "broker.plan_validation",
            "score",
            str(FIXTURES / "t2_probe_ok.md"),
            "--json",
            "--skill-map",
            str(FIXTURE_SKILL_MAP),
        ],
        cwd=REPO_ROOT / "nexus-broker" / "src",
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert payload["overall_pass"] is True
    assert "probes" in payload, "T2 plan must trip gate_requires_probes and run N09's probes"
    assert payload["probes"]["overall_pass"] is True


def test_probes_wired_into_live_cli_stub_mutation_fails_same_command() -> None:
    """Mutation leg: mutate the plan's one leaf's verification_method to a
    trivial stub, re-score via the exact same CLI command -> now FAILS, with
    the offending node id surfaced under probes.stub_mutation. This is the
    CONFIRMED-BUG repro from N20's terminal Lens pass: before this leaf's fix,
    the CLI's own N08 core has no opinion on a stub command (it only checks
    non-empty/type=='command'), so this mutated plan used to PASS overall."""
    import tempfile

    doc = load_plan_as_dag_doc(FIXTURES / "t2_probe_ok.md")
    mutated = mutate_to_stub(doc, "P1")

    with tempfile.TemporaryDirectory() as td:
        mutated_path = Path(td) / "t2_probe_stub.md"
        _write_doc_as_plan_md(mutated_path, mutated)
        proc = subprocess.run(
            [sys.executable, "-m", "broker.plan_validation", "score", str(mutated_path), "--json"],
            cwd=REPO_ROOT / "nexus-broker" / "src",
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(proc.stdout)

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert payload["overall_pass"] is False
    assert payload["probes"]["overall_pass"] is False
    assert payload["probes"]["stub_mutation"]["pass"] is False
    assert payload["probes"]["stub_mutation"]["offending_node_ids"] == ["P1"]


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

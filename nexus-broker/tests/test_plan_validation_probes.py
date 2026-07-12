"""Tests for broker.plan_validation.probes — opt-in gate probes (R3-T04, N09).

Covers: the K=2 diversity probe (structural divergence + N01 same-model
guard), the stub-mutation oracle's falsifiability (a seeded stub MUST fail),
citation-coverage, the opt-in gate (`gate_requires_probes` / `run_probes`),
and the acceptance-criteria static guards (zero probe import on the default
path; no network/model-client import anywhere in this package).

All fixtures are built in-code (no new files under tests/fixtures/) so this
leaf's file footprint stays disjoint from N08's `tests/fixtures/plan_validation/`
directory and from N11's `checks/` module.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from broker.plan_validation.probes.citation import check_citation_coverage
from broker.plan_validation.probes.diversity import check_k2_diversity, structural_divergence
from broker.plan_validation.probes.gate import gate_requires_probes, run_probes
from broker.plan_validation.probes.stub_mutation import check_stub_mutation, is_stub_command, mutate_to_stub

REPO_ROOT = Path(__file__).resolve().parents[2]


def _node(node_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "node_id": node_id,
        "depends_on": [],
        "downstream_consumers": [],
        "agent_persona": "hermes",
        "work_type": "meta",
        "goal": "do a real, non-trivial thing",
        "context_files": ["docs/agents/CONTRACT.md"],
        "acceptance_criteria": ["the real thing was done"],
        "verification_method": {"type": "command", "command": "pytest tests/test_x.py -q"},
        "risk_tier": "T0",
        "skills_required": ["agent-protocol"],
        "budget": "S",
        "irreversible": False,
        "do_not_touch": [],
        "notepad_topic": "TEST-N09",
    }
    base.update(overrides)
    return base


def _doc(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 2, "nodes": list(nodes)}


# ---------------------------------------------------------------------------
# citation-coverage
# ---------------------------------------------------------------------------


def test_citation_coverage_passes_when_all_files_exist() -> None:
    doc = _doc(_node("C1", context_files=["docs/agents/CONTRACT.md", "docs/agents/SKILL_MAP.md"]))
    verdict = check_citation_coverage(doc, repo_root=REPO_ROOT)
    assert verdict.passed is True


def test_citation_coverage_fails_on_missing_file() -> None:
    doc = _doc(_node("C1", context_files=["docs/does/not/exist.md"]))
    verdict = check_citation_coverage(doc, repo_root=REPO_ROOT)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["C1"]


def test_citation_coverage_fails_on_zero_citations() -> None:
    doc = _doc(_node("C1", context_files=[]))
    verdict = check_citation_coverage(doc, repo_root=REPO_ROOT)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["C1"]


def test_citation_coverage_accepts_directory_citations() -> None:
    doc = _doc(_node("C1", context_files=["nexus-broker/src/broker/plan_validation/"]))
    verdict = check_citation_coverage(doc, repo_root=REPO_ROOT)
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# stub-mutation oracle — falsifiability (acceptance criterion 1)
# ---------------------------------------------------------------------------


def test_is_stub_command_matches_known_trivial_patterns() -> None:
    for cmd in ("true", ":", "pass", "exit 0", "echo", "echo ok", "EXIT 0"):
        assert is_stub_command(cmd), cmd


def test_is_stub_command_rejects_real_commands() -> None:
    for cmd in ("pytest tests/test_x.py -q", "uv run ruff check src/", "python -m broker.plan_validation score f"):
        assert not is_stub_command(cmd), cmd


def test_clean_plan_passes_stub_mutation_check() -> None:
    doc = _doc(_node("A1"))
    assert check_stub_mutation(doc).passed is True


def test_stub_mutation_seeded_stub_fails_the_gate() -> None:
    """Acceptance criterion 1: seed a stub into a known-clean leaf and prove
    the probe's verdict flips to fail — falsifiability proven."""
    doc = _doc(_node("A1"))
    assert check_stub_mutation(doc).passed is True  # sanity: clean before mutation

    mutated = mutate_to_stub(doc, "A1")
    verdict = check_stub_mutation(mutated)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["A1"]


def test_mutate_to_stub_does_not_mutate_the_input_doc() -> None:
    doc = _doc(_node("A1"))
    original_vm = dict(doc["nodes"][0]["verification_method"])
    mutate_to_stub(doc, "A1")
    assert doc["nodes"][0]["verification_method"] == original_vm


def test_stub_mutation_detects_text_markers() -> None:
    doc = _doc(_node("A1", goal="TODO: figure this out later"))
    verdict = check_stub_mutation(doc)
    assert verdict.passed is False
    assert verdict.offending_node_ids == ["A1"]


# ---------------------------------------------------------------------------
# K=2 diversity probe
# ---------------------------------------------------------------------------


def test_structural_divergence_zero_for_identical_docs() -> None:
    doc = _doc(_node("D1"), _node("D2", depends_on=["D1"]))
    assert structural_divergence(doc, doc) == 0.0


def test_structural_divergence_nonzero_for_structurally_different_docs() -> None:
    doc_a = _doc(_node("D1"))
    doc_b = _doc(_node("D1"), _node("D2", depends_on=["D1"]))
    assert structural_divergence(doc_a, doc_b) > 0.0


def test_k2_diversity_fails_on_near_identical_samples() -> None:
    doc = _doc(_node("D1"), _node("D2", depends_on=["D1"]))
    verdict = check_k2_diversity(doc, doc, planner_model="opus", sampler_model="sonnet-5")
    assert verdict.passed is False


def test_k2_diversity_n01_rule_blocks_same_model_sampling() -> None:
    doc_a = _doc(_node("D1"))
    doc_b = _doc(_node("D1"), _node("D2", depends_on=["D1"]))
    verdict = check_k2_diversity(doc_a, doc_b, planner_model="opus", sampler_model="opus")
    assert verdict.passed is False
    assert "N01" in verdict.details[0]


def test_k2_diversity_passes_on_genuinely_divergent_independent_samples() -> None:
    doc_a = _doc(_node("D1", agent_persona="hermes"))
    doc_b = _doc(
        _node("D1", agent_persona="scout"),
        _node("D2", agent_persona="pipeline-async", depends_on=["D1"]),
    )
    verdict = check_k2_diversity(doc_a, doc_b, planner_model="opus", sampler_model="sonnet-5")
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# opt-in gate
# ---------------------------------------------------------------------------


def test_gate_requires_probes_false_for_low_risk_reversible_plan() -> None:
    doc = _doc(_node("X1", risk_tier="T0"), _node("X2", risk_tier="T1"))
    assert gate_requires_probes(doc) is False


def test_gate_requires_probes_true_for_t2_node() -> None:
    doc = _doc(_node("X1", risk_tier="T0"), _node("X2", risk_tier="T2"))
    assert gate_requires_probes(doc) is True


def test_gate_requires_probes_true_for_irreversible_node() -> None:
    doc = _doc(_node("X1", risk_tier="T0", irreversible=True))
    assert gate_requires_probes(doc) is True


def test_run_probes_returns_none_for_low_risk_plan_without_force() -> None:
    doc = _doc(_node("X1", risk_tier="T0"))
    assert run_probes(doc) is None


def test_run_probes_runs_when_forced() -> None:
    doc = _doc(_node("Y1"))
    result = run_probes(doc, force=True, repo_root=REPO_ROOT)
    assert result is not None
    assert "citation_coverage" in result
    assert "stub_mutation" in result
    assert "overall_pass" in result


def test_run_probes_runs_automatically_for_t2_plan() -> None:
    doc = _doc(_node("Y1", risk_tier="T2"))
    result = run_probes(doc, repo_root=REPO_ROOT)
    assert result is not None


def test_run_probes_includes_diversity_only_when_sample_supplied() -> None:
    doc = _doc(_node("Y1", risk_tier="T2"))
    without_sample = run_probes(doc, repo_root=REPO_ROOT)
    assert "k2_diversity" not in without_sample

    second = _doc(_node("Y1", risk_tier="T2", agent_persona="scout"))
    with_sample = run_probes(
        doc,
        repo_root=REPO_ROOT,
        diversity_sample=second,
        planner_model="opus",
        sampler_model="sonnet-5",
    )
    assert "k2_diversity" in with_sample


# ---------------------------------------------------------------------------
# acceptance criterion 2 — opt-in: zero probe import on the default path
# ---------------------------------------------------------------------------


def test_default_invocation_runs_zero_probes(tmp_path: Path) -> None:
    """Runs in a fresh subprocess so this test file's OWN direct imports of the
    probe submodules (needed for the unit tests above) cannot mask a real
    regression: importing `broker.plan_validation.probes.gate` and calling
    `run_probes` on a T0/T1, non-irreversible plan must import zero probe
    submodules (`citation`, `stub_mutation`, `diversity`)."""
    script = tmp_path / "check_no_probe_leak.py"
    script.write_text(
        "import sys\n"
        "from broker.plan_validation.probes.gate import run_probes\n"
        "doc = {\n"
        "    'schema_version': 2,\n"
        "    'nodes': [{\n"
        "        'node_id': 'X1', 'depends_on': [], 'downstream_consumers': [],\n"
        "        'agent_persona': 'hermes', 'work_type': 'meta', 'goal': 'g',\n"
        "        'context_files': [], 'acceptance_criteria': ['a'],\n"
        "        'verification_method': {'type': 'command', 'command': 'pytest -q'},\n"
        "        'risk_tier': 'T0', 'skills_required': [], 'budget': 'S',\n"
        "        'irreversible': False, 'do_not_touch': [], 'notepad_topic': 't',\n"
        "    }],\n"
        "}\n"
        "result = run_probes(doc)\n"
        "assert result is None, result\n"
        "leaked = [m for m in sys.modules\n"
        "          if m.startswith('broker.plan_validation.probes.')\n"
        "          and m != 'broker.plan_validation.probes.gate']\n"
        "assert not leaked, leaked\n"
        "print('OK')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT / "nexus-broker" / "src",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# acceptance criterion 3 — disjointness + static guards
# ---------------------------------------------------------------------------


def test_probe_files_live_only_under_probes_directory() -> None:
    """Acceptance criterion 3: probe source lives entirely under
    plan_validation/probes/, disjoint from N08's core files
    (plan_validation/*.py) and from N11's plan_validation/checks/ module."""
    pkg_dir = REPO_ROOT / "nexus-broker" / "src" / "broker" / "plan_validation"
    probes_dir = pkg_dir / "probes"
    probe_files = {p.name for p in probes_dir.glob("*.py")}
    assert probe_files >= {"__init__.py", "gate.py", "citation.py", "stub_mutation.py", "diversity.py"}

    n08_core_files = {"score.py", "plan_doc.py", "skill_map.py"}
    core_dir_files = {p.name for p in pkg_dir.glob("*.py")}
    assert n08_core_files <= core_dir_files
    # probes/ and the package root are different directories by construction;
    # this asserts N08's core modules were not touched/duplicated inside probes/.
    assert not (probe_files & n08_core_files)


def test_no_network_or_model_client_imported_in_probes_package() -> None:
    probes_dir = REPO_ROOT / "nexus-broker" / "src" / "broker" / "plan_validation" / "probes"
    banned_tokens = (
        "import requests",
        "import httpx",
        "import urllib",
        "import socket",
        "anthropic",
        "openai",
        "import http.client",
    )
    py_files = sorted(probes_dir.glob("*.py"))
    assert py_files, "expected probes package to contain .py files"
    for py_file in py_files:
        src = py_file.read_text(encoding="utf-8")
        for banned in banned_tokens:
            assert banned not in src, f"{py_file}: unexpected network/model dependency: {banned}"


def _run_all() -> int:
    """__main__ runner (NATIVE-6 integrity guard): executes every test_* function
    in this module and exits non-zero on any failure, for plain-script invocation."""
    import tempfile
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
            if name == "test_default_invocation_runs_zero_probes":
                with tempfile.TemporaryDirectory() as td:
                    fn(Path(td))
            else:
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

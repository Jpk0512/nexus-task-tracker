"""Tests for broker.node_contract — schema_version 2 DAG validator (R3-T01)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from broker.node_contract import (
    SUPPORTED_SCHEMA_VERSION,
    load_dag,
    main,
    validate_dag,
    validate_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "node_contract"


def test_good_dag_is_valid() -> None:
    errors = validate_file(FIXTURES / "good_dag.yaml")
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


def test_bad_cycle_detected() -> None:
    errors = validate_file(FIXTURES / "bad_cycle.yaml")
    assert errors, "expected cycle to be flagged"
    assert any(e.code == "cycle" for e in errors)


def test_bad_prose_verification_detected() -> None:
    errors = validate_file(FIXTURES / "bad_prose_verification.yaml")
    assert errors, "expected prose verification to be flagged"
    assert any(e.code == "prose-verification" for e in errors)


def test_bad_orphan_leaf_detected() -> None:
    errors = validate_file(FIXTURES / "bad_orphan_leaf.yaml")
    assert errors, "expected orphan leaf to be flagged"
    assert any(e.code == "orphan-leaf" for e in errors)


def test_bad_missing_field_detected() -> None:
    errors = validate_file(FIXTURES / "bad_missing_field.yaml")
    assert errors, "expected missing fields to be flagged"
    assert any(e.code == "missing-field" for e in errors)


def test_unsupported_schema_version_rejected() -> None:
    doc = load_dag(FIXTURES / "good_dag.yaml")
    doc["schema_version"] = 99
    errors = validate_dag(doc)
    assert any(e.code == "unsupported-schema-version" for e in errors)


def test_unsupported_schema_version_is_not_silently_coerced() -> None:
    doc = load_dag(FIXTURES / "good_dag.yaml")
    doc["schema_version"] = SUPPORTED_SCHEMA_VERSION + 1
    errors = validate_dag(doc)
    assert len(errors) == 1
    assert errors[0].code == "unsupported-schema-version"


def test_dangling_depends_on_edge_detected() -> None:
    doc = load_dag(FIXTURES / "good_dag.yaml")
    doc["nodes"][1]["depends_on"] = ["DOES_NOT_EXIST"]
    errors = validate_dag(doc)
    assert any(e.code == "dangling-edge" for e in errors)


def test_no_model_call_or_network_import() -> None:
    """Static guard: the module must not import network/model-call libraries."""
    src = Path(__file__).parent.parent.joinpath("src", "broker", "node_contract.py").read_text()
    for banned in ("import requests", "import httpx", "import urllib", "import socket", "anthropic"):
        assert banned not in src, f"unexpected network/model dependency: {banned}"


def test_performance_under_100ms_on_30_node_dag() -> None:
    import time

    nodes = []
    for i in range(30):
        deps = [f"N{i - 1}"] if i > 0 else []
        nodes.append(
            {
                "node_id": f"N{i}",
                "depends_on": deps,
                "downstream_consumers": [f"N{i + 1}"] if i < 29 else [],
                "agent_persona": "scout",
                "goal": f"node {i}",
                "context_files": [],
                "acceptance_criteria": ["ok"],
                "verification_method": {"type": "command", "command": f"echo {i}"},
                "risk_tier": "T0",
                "skills_required": [],
                "do_not_touch": [],
            }
        )
    doc = {"schema_version": 2, "nodes": nodes}
    start = time.perf_counter()
    errors = validate_dag(doc)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert errors == []
    assert elapsed_ms < 100, f"validation took {elapsed_ms:.2f}ms, expected <100ms"


def test_cli_validate_exits_0_on_good_dag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "broker.node_contract", "validate", str(FIXTURES / "good_dag.yaml")],
        cwd=str(Path(__file__).parent.parent / "src"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "fixture_name",
    ["bad_cycle.yaml", "bad_prose_verification.yaml", "bad_orphan_leaf.yaml", "bad_missing_field.yaml"],
)
def test_cli_validate_exits_1_on_each_bad_fixture(fixture_name: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "broker.node_contract", "validate", str(FIXTURES / fixture_name)],
        cwd=str(Path(__file__).parent.parent / "src"),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, f"{fixture_name}: expected exit 1, got {result.returncode}; stderr={result.stderr}"


def _run_all() -> int:
    """__main__ fallback runner — executes every test function, non-zero exit on failure."""
    import traceback

    failures = 0
    test_fns = [
        (name, obj)
        for name, obj in list(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for name, fn in test_fns:
        try:
            if name == "test_cli_validate_exits_1_on_each_bad_fixture":
                for fixture_name in [
                    "bad_cycle.yaml",
                    "bad_prose_verification.yaml",
                    "bad_orphan_leaf.yaml",
                    "bad_missing_field.yaml",
                ]:
                    fn(fixture_name)
            else:
                fn()
            print(f"PASS: {name}")
        except Exception:
            failures += 1
            print(f"FAIL: {name}")
            traceback.print_exc()
    print(f"\n{len(test_fns)} test functions, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())

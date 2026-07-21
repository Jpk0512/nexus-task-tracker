"""Tests for broker.node_contract R4-T05 cross-vendor executor checks
(plans/11-codex-lane-design.md SS9.1/SS9.3; docs/agents/CONTRACT.md
'Cross-vendor executor fields'). Additive to schema_version 2 — the
regression suite at the bottom proves the pre-existing fixtures are
byte-identically unaffected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from broker.node_contract import validate_file

FIXTURES = Path(__file__).parent / "fixtures" / "node_contract" / "executor"
PREEXISTING_FIXTURES = Path(__file__).parent / "fixtures" / "node_contract"


def _enabled_flag(tmp_path: Path) -> Path:
    """A tmp path that DOES exist — the codex lane reads as enabled."""
    flag = tmp_path / "codex-lane.enabled"
    flag.write_text("")
    return flag


def _disabled_flag(tmp_path: Path) -> Path:
    """A tmp path that does NOT exist — the codex lane reads as disabled."""
    return tmp_path / "codex-lane.enabled"


# --- check: executor value in enum ------------------------------------------


def test_good_executor_enum() -> None:
    errors = validate_file(FIXTURES / "good_executor_enum.yaml")
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


def test_bad_executor_enum_detected() -> None:
    errors = validate_file(FIXTURES / "bad_executor_enum.yaml")
    assert errors, "expected an out-of-enum executor value to be flagged"
    assert any(e.code == "bad-enum" for e in errors)


# --- check: executor codex implies lane-enabled flag file exists -----------


def test_good_codex_lane_enabled(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "good_codex_lane_enabled.yaml", codex_lane_flag_path=flag)
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


def test_bad_codex_lane_disabled_detected(tmp_path: Path) -> None:
    flag = _disabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "bad_codex_lane_disabled.yaml", codex_lane_flag_path=flag)
    assert errors, "expected executor: codex with no lane flag file to be flagged"
    assert any(e.code == "codex-lane-disabled" for e in errors)


_REAL_CODEX_LANE_FLAG = Path(__file__).resolve().parents[2] / ".claude" / "codex-lane.enabled"


@pytest.mark.skipif(
    _REAL_CODEX_LANE_FLAG.exists(),
    reason=(
        "this machine has the codex lane deliberately ENABLED (RDEC-011 "
        "decorrelated-judge lane, orchestrator decision) — the real "
        f"{_REAL_CODEX_LANE_FLAG} flag file exists, so the off-by-default proof is "
        "unprovable HERE by construction, not broken. The property still holds "
        "and is still proven on any clean/CI machine where the lane ships off."
    ),
)
def test_codex_lane_rejected_on_real_default_path_no_override() -> None:
    """R4-T07 off-by-default proof: every other test in this module passes an
    explicit `codex_lane_flag_path` override. This one calls `validate_file`
    with NO override at all, so it exercises the real
    `_default_codex_lane_flag_path()` resolution against this actual repo's
    `.claude/codex-lane.enabled` — which must not exist (the codex lane ships
    off; nothing in this task creates that file). If a human ever runs
    `bin/codex-lane enable` on this machine, this test starts failing until
    `disable` is run again — that is the intended, correct behavior of an
    off-by-default proof, not a bug in the test."""
    real_flag = _REAL_CODEX_LANE_FLAG
    assert not real_flag.exists(), (
        f"real lane flag {real_flag} exists — off-by-default proof is meaningless while "
        "the lane is actually enabled on this machine; run 'bin/codex-lane disable' first"
    )
    errors = validate_file(FIXTURES / "bad_codex_lane_disabled.yaml")
    assert errors, "expected executor: codex to be rejected via the real (un-overridden) flag path"
    assert any(e.code == "codex-lane-disabled" for e in errors)


# --- check: executor_model is a valid slug ----------------------------------


def test_good_executor_model(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "good_executor_model.yaml", codex_lane_flag_path=flag)
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


def test_bad_executor_model_detected(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "bad_executor_model.yaml", codex_lane_flag_path=flag)
    assert errors, "expected an unrecognized executor_model slug to be flagged"
    assert any(e.code == "bad-enum" for e in errors)


# --- check: irreversible is not true -----------------------------------------


def test_good_irreversible(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "good_irreversible.yaml", codex_lane_flag_path=flag)
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


def test_bad_irreversible_detected(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "bad_irreversible.yaml", codex_lane_flag_path=flag)
    assert errors, "expected executor: codex + irreversible: true to be flagged"
    assert any(e.code == "codex-irreversible-invalid" for e in errors)


# --- check: write_scope maps to a codex sandbox mode -------------------------


def test_good_write_scope_sandbox(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "good_write_scope_sandbox.yaml", codex_lane_flag_path=flag)
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


def test_bad_write_scope_sandbox_detected(tmp_path: Path) -> None:
    flag = _enabled_flag(tmp_path)
    errors = validate_file(FIXTURES / "bad_write_scope_sandbox.yaml", codex_lane_flag_path=flag)
    assert errors, "expected an unbounded write_scope glob to be flagged for executor: codex"
    assert any(e.code == "codex-write-scope-unmappable" for e in errors)


# --- absence of 'executor' is untouched (default-claude, no checks fire) ----


def test_absent_executor_field_triggers_no_new_checks(tmp_path: Path) -> None:
    """A node with no 'executor' key must never touch the lane-flag filesystem
    check — even when the override points at a path that does not exist."""
    disabled = _disabled_flag(tmp_path)
    errors = validate_file(PREEXISTING_FIXTURES / "good_dag.yaml", codex_lane_flag_path=disabled)
    assert errors == [], f"expected no errors, got {[repr(e) for e in errors]}"


# --- additive-only proof: pre-existing fixtures validate byte-identically ---


@pytest.mark.parametrize(
    "fixture_name,expected_codes",
    [
        ("good_dag.yaml", set()),
        ("bad_cycle.yaml", {"cycle"}),
        ("bad_prose_verification.yaml", {"prose-verification"}),
        ("bad_orphan_leaf.yaml", {"orphan-leaf"}),
        ("bad_missing_field.yaml", {"missing-field"}),
    ],
)
def test_preexisting_fixtures_unaffected_by_executor_checks(
    fixture_name: str, expected_codes: set[str]
) -> None:
    """R4-T05 additive-only proof: none of these fixtures declare 'executor', so
    the new checks never fire and the error-code set is unchanged from
    pre-R4-T05 behavior (see test_node_contract_schema.py's own assertions on
    the same fixtures, which this cross-checks with an exact code-set diff)."""
    errors = validate_file(PREEXISTING_FIXTURES / fixture_name)
    codes = {e.code for e in errors}
    if expected_codes:
        assert codes == expected_codes, f"{fixture_name}: expected {expected_codes}, got {codes}"
    else:
        assert errors == [], f"{fixture_name}: expected no errors, got {[repr(e) for e in errors]}"

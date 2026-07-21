"""F1-06 — validate the typed return-envelope JSON Schema against golden fixtures.

The schema (nexus-broker/src/broker/schemas/return_envelope.schema.json) is the
future load-bearing routing signal that supersedes by-eye parsing of the
'## NEXUS:*' marker string (nexus-foundation/plans/wave-1.md track c). These
tests are the acceptance gate for F1-06:

  - every VALID golden fixture passes schema validation. The valid set covers
    both CONTRACT.md tiers (DEC-039): the FULL-tier fixtures (one per marker
    class) collectively represent every marker-vocabulary status value, and the
    LEAN-tier fixture (status-less: completion_marker + files_changed +
    verification_result) plus a FULL fixture that OMITS summary/schema_version
    lock in that neither is required (F1-06 REVISE findings #1 and #2 — a schema
    that required summary or rejected a LEAN return would silently pass every
    earlier hand-written fixture, the weak-test-masks-green failure mode);
  - every INVALID fixture (LEAN missing verification_result, unknown status,
    wrong-typed files_changed, evidence-less DONE, status/marker mismatch,
    unknown schema_version) is REJECTED.

jsonschema>=4.23 is a first-class project dependency (pyproject.toml).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_HERE = Path(__file__).resolve().parent
_SCHEMA_PATH = _HERE.parent / "src" / "broker" / "schemas" / "return_envelope.schema.json"
_FIXTURE_DIR = _HERE / "fixtures" / "return_envelope"

# The full current completion-marker vocabulary (docs/agents/CONTRACT.md).
_EXPECTED_STATUSES = {
    "DONE",
    "BLOCKED",
    "NEEDS-DECISION",
    "CHECKPOINT",
    "REVISE",
    "DEFER-REQUEST",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict:
    return _load_json(_SCHEMA_PATH)


@pytest.fixture(scope="module")
def validator(schema: dict) -> Draft202012Validator:
    # check_schema raises if the schema itself is malformed — a real gate, not
    # just a lint: a broken schema would silently pass every fixture otherwise.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_fixtures() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("valid_*.json"))


def _invalid_fixtures() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("invalid_*.json"))


def test_schema_declares_version_one(schema: dict) -> None:
    assert schema["properties"]["schema_version"]["const"] == 1


def test_status_enum_is_full_marker_vocabulary(schema: dict) -> None:
    assert set(schema["properties"]["status"]["enum"]) == _EXPECTED_STATUSES


def test_valid_and_invalid_fixtures_both_exist() -> None:
    assert _valid_fixtures(), "no valid_*.json golden fixtures found"
    assert len(_invalid_fixtures()) >= 3, "need at least 3 invalid fixtures"


@pytest.mark.parametrize("fixture", _valid_fixtures(), ids=lambda p: p.name)
def test_valid_fixture_passes(validator: Draft202012Validator, fixture: Path) -> None:
    envelope = _load_json(fixture)
    errors = sorted(validator.iter_errors(envelope), key=str)
    assert not errors, (
        f"{fixture.name} should validate but did not:\n"
        + "\n".join(f"  - {e.message} (at {list(e.absolute_path)})" for e in errors)
    )


@pytest.mark.parametrize("fixture", _invalid_fixtures(), ids=lambda p: p.name)
def test_invalid_fixture_fails(validator: Draft202012Validator, fixture: Path) -> None:
    envelope = _load_json(fixture)
    with pytest.raises(ValidationError):
        validator.validate(envelope)


def test_all_marker_classes_have_a_valid_fixture(validator: Draft202012Validator) -> None:
    """MECE coverage: the FULL-tier valid fixtures collectively cover every
    status value, and each valid fixture is individually schema-valid. LEAN-tier
    fixtures carry no `status` field (CONTRACT.md line 64) and are skipped for
    the coverage tally — they are still validated."""
    seen = set()
    for fixture in _valid_fixtures():
        envelope = _load_json(fixture)
        validator.validate(envelope)
        status = envelope.get("status")
        if status is not None:
            seen.add(status)
    assert seen == _EXPECTED_STATUSES, f"missing marker classes: {_EXPECTED_STATUSES - seen}"


def test_top_level_required_is_minimal(schema: dict) -> None:
    """REVISE findings #1/#2: only completion_marker + files_changed are
    universally required. `summary` (ungrounded), `status` (FULL-tier only), and
    `schema_version` (optional/additive) must NOT be top-level required, else a
    CONTRACT.md-canonical or LEAN-tier envelope is wrongly rejected."""
    assert set(schema["required"]) == {"completion_marker", "files_changed"}


def test_lean_envelope_validates(validator: Draft202012Validator) -> None:
    """CONTRACT.md line 64 LEAN tier: a status-less envelope carrying only
    completion_marker + files_changed + verification_result is VALID."""
    lean = {
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["docs/agents/CONTRACT.md"],
        "verification_result": "$ uv run pytest -q\n3 passed",
    }
    validator.validate(lean)


def test_lean_envelope_without_verification_is_rejected(
    validator: Draft202012Validator,
) -> None:
    """The LEAN-tier branch requires verification_result when status is absent —
    a status-less envelope missing it is REJECTED (guards against the branch
    silently no-firing)."""
    lean_no_verify = {
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["docs/agents/CONTRACT.md"],
    }
    with pytest.raises(ValidationError):
        validator.validate(lean_no_verify)


def test_full_envelope_without_summary_validates(
    validator: Draft202012Validator,
) -> None:
    """REVISE finding #1: a FULL-tier envelope that omits `summary` (and
    `schema_version`), mirroring CONTRACT.md's canonical Required Output
    template, MUST validate — summary is not a CONTRACT.md field."""
    full = {
        "status": "DONE",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["nexus-broker/src/broker/schemas/return_envelope.schema.json"],
        "verification_result": "$ uv run pytest -q\n18 passed",
        "acceptance_met": [
            {"criterion": "validates without summary", "met": True, "evidence": "no summary key present"}
        ],
    }
    validator.validate(full)


def test_status_less_envelope_does_not_trip_marker_consistency(
    validator: Draft202012Validator,
) -> None:
    """REVISE finding #2: without the `required: [status]` guard on the six
    consistency branches, a status-less envelope would match all six `if`
    clauses at once and be forced to satisfy six mutually exclusive
    completion_marker consts — unsatisfiable. Any LEAN completion_marker must
    validate; here a REVISE-marker LEAN return (unusual but well-formed) passes
    because no consistency branch fires when status is absent."""
    lean_revise = {
        "completion_marker": "## NEXUS:REVISE",
        "files_changed": ["docs/agents/CONTRACT.md"],
        "verification_result": "$ uv run pytest -q\n1 failed",
    }
    validator.validate(lean_revise)


def test_done_with_empty_verification_result_is_rejected(
    validator: Draft202012Validator,
) -> None:
    """F1-06 REVISE — the DONE evidence gate must reject empty-but-present, not
    only absent. A DONE whose verification_result is "" (present, so the old
    `required` check passed) must FAIL on the top-level minLength:1. acceptance_met
    is populated here to ISOLATE the verification_result constraint — if this
    test failed to raise, the minLength guard silently regressed."""
    bad = {
        "status": "DONE",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["nexus-broker/src/broker/schemas/return_envelope.schema.json"],
        "verification_result": "",
        "acceptance_met": [{"criterion": "c", "met": True, "evidence": "e"}],
    }
    with pytest.raises(ValidationError):
        validator.validate(bad)


def test_done_with_empty_acceptance_met_is_rejected(
    validator: Draft202012Validator,
) -> None:
    """F1-06 REVISE — companion to the above: a DONE with a non-empty
    verification_result but an EMPTY acceptance_met[] must FAIL on the DONE
    branch's minItems:1 (mirroring BLOCKED's blockers). verification_result is
    non-empty here to ISOLATE the acceptance_met constraint."""
    bad = {
        "status": "DONE",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["nexus-broker/src/broker/schemas/return_envelope.schema.json"],
        "verification_result": "$ uv run pytest -q\n1 passed",
        "acceptance_met": [],
    }
    with pytest.raises(ValidationError):
        validator.validate(bad)


def test_done_evidence_gate_constraints_present_in_schema(schema: dict) -> None:
    """Structural guard against silent regression of the two evidence-gate
    constraints: verification_result carries minLength:1, and the DONE allOf
    branch's `then` adds acceptance_met minItems:1."""
    assert schema["properties"]["verification_result"]["minLength"] == 1
    done_then = next(
        branch["then"]
        for branch in schema["allOf"]
        if branch.get("if", {}).get("properties", {}).get("status", {}).get("const") == "DONE"
        and "acceptance_met" in branch.get("then", {}).get("required", [])
    )
    assert done_then["properties"]["acceptance_met"]["minItems"] == 1


def test_unknown_schema_version_is_rejected(validator: Draft202012Validator) -> None:
    """schema_version is optional, but when present it MUST be const 1; an
    unrecognized version is rejected, never best-effort parsed."""
    bad = {
        "status": "DONE",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["x"],
        "verification_result": "$ uv run pytest -q\n1 passed",
        "acceptance_met": [{"criterion": "c", "met": True, "evidence": "e"}],
        "schema_version": 2,
    }
    with pytest.raises(ValidationError):
        validator.validate(bad)

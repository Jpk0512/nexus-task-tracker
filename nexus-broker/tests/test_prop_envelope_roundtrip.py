"""F3-05 property suite 2/5 — return-envelope encode/decode round-trip INVARIANT.

The typed return envelope (`src/broker/schemas/return_envelope.schema.json`) is
the load-bearing routing signal a dispatched persona emits; the broker/hook
serializes it to the transport and re-parses it. The INVARIANT under test:

    ∀ schema-valid envelope e:
        json.loads(json.dumps(e)) == e            # decode∘encode = identity
    and validate(json.loads(json.dumps(e)))        # schema-validity is preserved
        has no errors

i.e. a JSON round-trip is structure-preserving AND validity-preserving, so a
conformant envelope can never silently drift into a non-conformant one on the
wire. This targets the SCHEMA (the artifact under test): envelopes are generated
from the schema's OWN vocabulary — the six marker/status pairs, the LEAN and
FULL(DONE) tiers, the real field subshapes — not hand-picked examples.

Regression corpus: explicit `@example(...)` (checked-in, reviewable) — one LEAN
and one FULL(DONE) envelope pinned inline. Choice documented per the F3-05 leaf.

Real data shape: envelopes match exactly what CONTRACT.md's Required Output and
the schema's `properties` admit — no invented fields (the schema is
`additionalProperties: false`, so an invented field would fail validation and
the generator would be provably wrong).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.property

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "broker"
    / "schemas"
    / "return_envelope.schema.json"
)

_MARKERS = [
    "## NEXUS:DONE",
    "## NEXUS:BLOCKED",
    "## NEXUS:NEEDS-DECISION",
    "## NEXUS:CHECKPOINT",
    "## NEXUS:REVISE",
    "## NEXUS:DEFER-REQUEST",
]

# JSON-safe scalars/collections: text, bool, bounded int, and lists thereof.
# Restricting to these guarantees the round-trip is lossless (no NaN/Inf, no
# tuple/set, no non-string dict keys) so any identity failure is a REAL schema
# round-trip defect, never a JSON-encoding artifact of the generator.
_path = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=1, max_size=40
)
_nonempty_text = st.text(min_size=1, max_size=60)


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


_acceptance_item = st.fixed_dictionaries(
    {
        "criterion": _nonempty_text,
        "met": st.booleans(),
        "evidence": _nonempty_text,
    }
)


@st.composite
def _lean_envelope(draw: st.DrawFn) -> dict:
    """A LEAN-tier envelope (no `status`): only completion_marker +
    files_changed + verification_result. Every marker string is valid at this
    tier because no marker/status consistency branch fires when status is
    absent (schema REVISE finding #2)."""
    return {
        "completion_marker": draw(st.sampled_from(_MARKERS)),
        "files_changed": draw(st.lists(_path, min_size=0, max_size=5)),
        "verification_result": draw(_nonempty_text),
    }


@st.composite
def _full_done_envelope(draw: st.DrawFn) -> dict:
    """A FULL-tier DONE envelope: status=DONE forces completion_marker
    '## NEXUS:DONE' and requires a non-empty verification_result and a non-empty
    acceptance_met (minItems 1)."""
    return {
        "status": "DONE",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": draw(st.lists(_path, min_size=1, max_size=5)),
        "verification_result": draw(_nonempty_text),
        "acceptance_met": draw(st.lists(_acceptance_item, min_size=1, max_size=4)),
    }


_envelopes = st.one_of(_lean_envelope(), _full_done_envelope())


@given(envelope=_envelopes)
@example(
    envelope={
        "completion_marker": "## NEXUS:REVISE",
        "files_changed": [],
        "verification_result": "$ uv run pytest -q\n1 failed",
    }
)
@example(
    envelope={
        "status": "DONE",
        "completion_marker": "## NEXUS:DONE",
        "files_changed": ["nexus-broker/tests/test_prop_envelope_roundtrip.py"],
        "verification_result": "$ uv run pytest -q -m property\n5 passed",
        "acceptance_met": [
            {"criterion": "round-trip identity", "met": True, "evidence": "line 1"}
        ],
    }
)
def test_envelope_roundtrip_is_identity_and_validity_preserving(
    validator: Draft202012Validator, envelope: dict
) -> None:
    """INVARIANT: encode→decode preserves both structure and schema validity."""
    # The generator only emits schema-valid envelopes — assert that up front so a
    # generator bug can never mask a round-trip defect (weak-test guard).
    assert not list(validator.iter_errors(envelope)), (
        "generator emitted a non-schema-valid envelope: "
        + "; ".join(e.message for e in validator.iter_errors(envelope))
    )

    decoded = json.loads(json.dumps(envelope))

    # decode∘encode == identity
    assert decoded == envelope

    # schema-validity survives the round-trip
    assert not list(validator.iter_errors(decoded))

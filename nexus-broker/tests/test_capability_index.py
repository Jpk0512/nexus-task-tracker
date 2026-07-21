"""R5-T03 N48 — capability registry metadata schema + index builder
(docs/agents/SKILL-METADATA-SCHEMA.md, plans/15-r5-dag.yaml).

Covers the node's acceptance criteria:
  1. Metadata schema validator rejects a fixture skill missing token_budget
     (negative test) and accepts the reference shape.
  2. capabilities.json index builds deterministically from registry
     content; discover() returns ranked candidates with estimated_tokens.
"""
from __future__ import annotations

import copy
import json

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.registry_index.discover import discover, estimate_tokens
from broker.registry_index.index import (
    DEFAULT_CAPABILITIES_RELPATH,
    IndexBuildError,
    build_index,
    lookup,
    lookup_many,
    render_capabilities_json,
    write_capabilities_json,
)
from broker.registry_index.metadata import MetadataValidationError, validate_metadata

# Worked-example shape, verbatim, from docs/agents/SKILL-METADATA-SCHEMA.md
# "Worked example" block (sourced from the proposal doc's SS5 section).
TDD_PATTERNS_META: dict = {
    "id": "tdd-patterns",
    "category": "persona-contract",
    "authority": "hard-rule",
    "applies_to": ["quill-ts", "quill-py", "lens"],
    "requires_profile": [],
    "summary": "Split-workflow TDD contract; stubs must be complete assertions and lint-clean.",
    "token_budget": {"summary": 200, "contract": 900, "full": 2500},
}

TABLEAU_SKILL_META: dict = {
    "id": "tableau-client-patterns",
    "category": "domain-reference",
    "authority": "advisory",
    "applies_to": ["pipeline-async"],
    "requires_profile": [],
    "summary": "Tableau REST/VizQL/Metadata API patterns via httpx async.",
    "token_budget": {"summary": 150, "contract": 700, "full": 1800},
}

PIPELINE_ASYNC_AGENT_META: dict = {
    "id": "pipeline-async",
    "category": "persona",
    "authority": "hard-rule",
    "applies_to": [],
    "requires_profile": [],
    "summary": "Dramatiq workers, Redis broker wiring, Tableau REST/VDS/Metadata clients.",
    "token_budget": {"summary": 180, "contract": 800, "full": 2200},
}


# --- 1. metadata schema validator -------------------------------------------


def test_reference_shape_validates_clean() -> None:
    validate_metadata(TDD_PATTERNS_META)  # must not raise


def test_missing_token_budget_is_rejected() -> None:
    broken = copy.deepcopy(TDD_PATTERNS_META)
    del broken["token_budget"]
    with pytest.raises(MetadataValidationError, match="token_budget"):
        validate_metadata(broken)


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda m: m.pop("id"), "id"),
        (lambda m: m.pop("category"), "category"),
        (lambda m: m.pop("authority"), "authority"),
        (lambda m: m.pop("summary"), "summary"),
        (lambda m: m.pop("applies_to"), "applies_to"),
        (lambda m: m.pop("requires_profile"), "requires_profile"),
        (lambda m: m.__setitem__("applies_to", "not-a-list"), "applies_to"),
        (lambda m: m["token_budget"].pop("summary"), "summary"),
        (lambda m: m["token_budget"].pop("contract"), "contract"),
        (lambda m: m["token_budget"].pop("full"), "full"),
        (lambda m: m["token_budget"].__setitem__("summary", -1), "non-negative"),
        (lambda m: m["token_budget"].__setitem__("summary", "200"), "non-negative"),
        (lambda m: m.__setitem__("id", ""), "id"),
    ],
)
def test_schema_nonconformant_metadata_is_rejected(mutator, match) -> None:
    broken = copy.deepcopy(TDD_PATTERNS_META)
    mutator(broken)
    with pytest.raises(MetadataValidationError, match=match):
        validate_metadata(broken)


def test_empty_lists_are_valid() -> None:
    meta = copy.deepcopy(TDD_PATTERNS_META)
    meta["applies_to"] = []
    validate_metadata(meta)  # must not raise — [] is explicitly legal (SS5 example)


# --- 2. index builder: deterministic, validated, no fabrication ------------


def test_build_index_validates_and_attaches_kind() -> None:
    index = build_index([("skill", TDD_PATTERNS_META), ("agent", PIPELINE_ASYNC_AGENT_META)])
    assert index["schema_version"] == "1"
    by_id = {record["id"]: record for record in index["capabilities"]}
    assert by_id["tdd-patterns"]["kind"] == "skill"
    assert by_id["pipeline-async"]["kind"] == "agent"


def test_build_index_rejects_unknown_kind() -> None:
    with pytest.raises(IndexBuildError, match="unknown capability kind"):
        build_index([("workflow", TDD_PATTERNS_META)])


def test_build_index_rejects_nonconformant_metadata() -> None:
    broken = copy.deepcopy(TDD_PATTERNS_META)
    del broken["token_budget"]
    with pytest.raises(IndexBuildError, match="token_budget"):
        build_index([("skill", broken)])


def test_build_index_rejects_duplicate_ids() -> None:
    dupe = copy.deepcopy(TDD_PATTERNS_META)
    with pytest.raises(IndexBuildError, match="duplicate capability id"):
        build_index([("skill", TDD_PATTERNS_META), ("skill", dupe)])


def test_build_index_is_sorted_by_id() -> None:
    index = build_index(
        [("skill", TABLEAU_SKILL_META), ("skill", TDD_PATTERNS_META), ("agent", PIPELINE_ASYNC_AGENT_META)]
    )
    ids = [record["id"] for record in index["capabilities"]]
    assert ids == sorted(ids)


def test_render_capabilities_json_is_deterministic_regardless_of_input_order() -> None:
    forward = build_index([("skill", TDD_PATTERNS_META), ("skill", TABLEAU_SKILL_META)])
    reverse = build_index([("skill", TABLEAU_SKILL_META), ("skill", TDD_PATTERNS_META)])
    assert render_capabilities_json(forward) == render_capabilities_json(reverse)


def test_render_capabilities_json_is_valid_json() -> None:
    index = build_index([("skill", TDD_PATTERNS_META)])
    parsed = json.loads(render_capabilities_json(index))
    assert parsed == index


def test_write_capabilities_json_lands_at_the_proposal_layout_path(tmp_path) -> None:
    out_path = write_capabilities_json(tmp_path, [("skill", TDD_PATTERNS_META)])
    assert out_path == tmp_path / DEFAULT_CAPABILITIES_RELPATH
    assert out_path.is_file()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["capabilities"][0]["id"] == "tdd-patterns"


def test_lookup_returns_none_for_unknown_id_never_fabricates() -> None:
    index = build_index([("skill", TDD_PATTERNS_META)])
    assert lookup(index, "does-not-exist") is None
    assert lookup(index, "tdd-patterns", kind="agent") is None  # wrong kind -> no match
    found = lookup(index, "tdd-patterns", kind="skill")
    assert found is not None
    assert found["id"] == "tdd-patterns"


def test_lookup_many_raises_naming_the_first_unresolved_id() -> None:
    index = build_index([("skill", TDD_PATTERNS_META)])
    with pytest.raises(IndexBuildError, match="unknown-skill"):
        lookup_many(index, ["tdd-patterns", "unknown-skill"], kind="skill")


# --- 3. discover(): ranked candidates with estimated_tokens ----------------


def test_discover_returns_estimated_tokens_from_summary_budget() -> None:
    index = build_index([("skill", TDD_PATTERNS_META)])
    [candidate] = discover(index, "tdd")
    assert candidate["estimated_tokens"] == TDD_PATTERNS_META["token_budget"]["summary"]
    assert candidate["estimated_tokens"] == estimate_tokens(index["capabilities"][0])


def test_discover_candidate_shape(snapshot: SnapshotAssertion) -> None:
    index = build_index([("skill", TDD_PATTERNS_META)])
    [candidate] = discover(index, "tdd")
    # envelope fixture: the rendered discover() candidate shape, reviewed via
    # snapshot (F3-04) — a field added/renamed/reworded now shows as a
    # readable snapshot diff instead of a silent inline-dict edit.
    assert candidate == snapshot


def test_discover_filters_by_query_text() -> None:
    index = build_index([("skill", TDD_PATTERNS_META), ("skill", TABLEAU_SKILL_META)])
    results = discover(index, "tableau")
    assert [r["id"] for r in results] == ["tableau-client-patterns"]


def test_discover_empty_query_lists_everything_ranked_by_id() -> None:
    index = build_index([("skill", TABLEAU_SKILL_META), ("skill", TDD_PATTERNS_META)])
    results = discover(index, "")
    assert [r["id"] for r in results] == ["tableau-client-patterns", "tdd-patterns"]


def test_discover_filters_by_kind() -> None:
    index = build_index([("skill", TDD_PATTERNS_META), ("agent", PIPELINE_ASYNC_AGENT_META)])
    results = discover(index, "", kinds=("agent",))
    assert [r["kind"] for r in results] == ["agent"]


def test_discover_respects_limit() -> None:
    index = build_index([("skill", TDD_PATTERNS_META), ("skill", TABLEAU_SKILL_META)])
    results = discover(index, "", limit=1)
    assert len(results) == 1


def test_discover_no_match_returns_empty_not_fabricated() -> None:
    index = build_index([("skill", TDD_PATTERNS_META)])
    assert discover(index, "nonexistent-query-term-xyz") == []

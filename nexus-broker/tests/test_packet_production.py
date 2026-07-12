"""R5-T03 N48 — real capability-packet production against the frozen v1
schema (docs/agents/CAPABILITY-PACKET-SCHEMA.md), plans/15-r5-dag.yaml.

Covers the node's acceptance criteria: real packets produced against the
frozen v1 schema (no field added/removed), and a body-hash-verified
round-trip through `broker.daemon.packet_store` — the same serving
mechanism `nexus_prepare` will eventually call, proving a produced packet
is not just schema-valid in isolation but actually servable.
"""
from __future__ import annotations

import pytest

from broker.daemon.packet_store import PacketRecord, PacketStore, compute_content_hash
from broker.packets.producer import FROZEN_V1_FIELDS, PacketProductionError, produce_packet
from broker.registry_index.index import build_index

TDD_PATTERNS_META: dict = {
    "id": "tdd-patterns",
    "category": "persona-contract",
    "authority": "hard-rule",
    "applies_to": ["quill-ts", "quill-py", "lens"],
    "requires_profile": [],
    "summary": "Split-workflow TDD contract; stubs must be complete assertions and lint-clean.",
    "token_budget": {"summary": 200, "contract": 900, "full": 2500},
}

AGENT_PROTOCOL_META: dict = {
    "id": "agent-protocol",
    "category": "persona-contract",
    "authority": "hard-rule",
    "applies_to": [],
    "requires_profile": [],
    "summary": "Universal execution protocol every code-writing agent carries.",
    "token_budget": {"summary": 120, "contract": 600, "full": 1500},
}


@pytest.fixture
def registry_index() -> dict:
    return build_index([("skill", TDD_PATTERNS_META), ("skill", AGENT_PROTOCOL_META)])


def _produce(index: dict, **overrides) -> dict:
    kwargs = {
        "role_id": "pipeline-async",
        "objective": "Wire Dramatiq worker retry discipline for the ingest queue.",
        "boundaries": {"allow": ["ingestion/src/workers/**"], "deny_route": ["app/**"]},
        "allowed_tools": ["Read", "Edit", "Bash(uv run pytest)"],
        "verification_method": {"type": "command", "command": "uv run pytest ingestion/tests -q"},
        "risk_tier": "T2",
        "skill_ids": ["agent-protocol", "tdd-patterns"],
        "index": index,
    }
    kwargs.update(overrides)
    return produce_packet(**kwargs)


# --- 1. real packet production against the frozen v1 schema ----------------


def test_produce_packet_has_exactly_the_frozen_v1_field_set(registry_index) -> None:
    packet = _produce(registry_index)
    assert set(packet.keys()) == set(FROZEN_V1_FIELDS)
    assert len(packet) == 11


def test_produce_packet_resolves_skills_required_from_the_real_registry(registry_index) -> None:
    packet = _produce(registry_index)
    assert packet["skills_required"] == ["agent-protocol", "tdd-patterns"]


def test_produce_packet_unknown_skill_id_is_rejected_not_fabricated(registry_index) -> None:
    with pytest.raises(PacketProductionError, match="unknown-skill-xyz"):
        _produce(registry_index, skill_ids=["agent-protocol", "unknown-skill-xyz"])


def test_produce_packet_carries_through_dispatch_fields(registry_index) -> None:
    packet = _produce(registry_index)
    assert packet["role_id"] == "pipeline-async"
    assert packet["schema_version"] == "1"
    assert packet["risk_tier"] == "T2"
    assert packet["boundaries"] == {
        "allow": ["ingestion/src/workers/**"],
        "deny_route": ["app/**"],
    }
    assert packet["verification_method"] == {
        "type": "command",
        "command": "uv run pytest ingestion/tests -q",
    }


def test_produce_packet_id_is_deterministic_for_identical_inputs(registry_index) -> None:
    first = _produce(registry_index)
    second = _produce(registry_index)
    assert first["packet_id"] == second["packet_id"]
    assert compute_content_hash(first) == compute_content_hash(second)


def test_produce_packet_id_changes_when_content_changes(registry_index) -> None:
    first = _produce(registry_index)
    second = _produce(registry_index, objective="A materially different objective sentence.")
    assert first["packet_id"] != second["packet_id"]


def test_produce_packet_explicit_packet_id_is_honored(registry_index) -> None:
    packet = _produce(registry_index, packet_id="pkt-explicit-001")
    assert packet["packet_id"] == "pkt-explicit-001"


def test_produce_packet_rejects_empty_role_id(registry_index) -> None:
    with pytest.raises(PacketProductionError):
        _produce(registry_index, role_id="")


# --- 2. nexus_prepare-equivalent round-trip: body-hash-verified ------------


def test_produced_packet_round_trips_through_the_packet_store(registry_index) -> None:
    packet = _produce(registry_index)
    content_hash = compute_content_hash(packet)

    store = PacketStore.from_fixtures({content_hash: packet})
    record = store.get_by_hash(content_hash)

    assert isinstance(record, PacketRecord)
    assert record.content_hash == content_hash
    assert record.packet["packet_id"] == packet["packet_id"]
    assert record.packet["role_id"] == "pipeline-async"
    assert list(record.packet["skills_required"]) == ["agent-protocol", "tdd-patterns"]


def _thaw(value):
    """Undo PacketStore's deep-freeze (MappingProxyType/tuple) so the served
    body can be re-hashed with the same plain-dict/list shape it was
    produced in."""
    if isinstance(value, dict):
        return {k: _thaw(v) for k, v in value.items()}
    if hasattr(value, "keys"):  # MappingProxyType
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def test_round_trip_body_hash_matches_the_registry_source(registry_index) -> None:
    packet = _produce(registry_index)
    content_hash = compute_content_hash(packet)
    store = PacketStore.from_fixtures({content_hash: packet})
    record = store.get_by_hash(content_hash)

    assert isinstance(record, PacketRecord)
    # The served body's content_hash, recomputed independently, matches the
    # hash it was addressed by — this is the "body-hash-verified" round trip.
    assert compute_content_hash(_thaw(record.packet)) == content_hash
    for skill_id in record.packet["skills_required"]:
        assert skill_id in {"agent-protocol", "tdd-patterns"}


def test_two_distinct_produced_packets_are_addressed_independently(registry_index) -> None:
    first = _produce(registry_index, role_id="pipeline-async")
    second = _produce(registry_index, role_id="pipeline-data")
    h1, h2 = compute_content_hash(first), compute_content_hash(second)
    assert h1 != h2

    store = PacketStore.from_fixtures({h1: first, h2: second})
    r1, r2 = store.get_by_hash(h1), store.get_by_hash(h2)
    assert isinstance(r1, PacketRecord) and isinstance(r2, PacketRecord)
    assert r1.packet["role_id"] != r2.packet["role_id"]

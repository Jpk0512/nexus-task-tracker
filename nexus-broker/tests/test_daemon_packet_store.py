"""R4-T09 N19 — capability-packet serving vs the frozen v1 schema
(docs/agents/CAPABILITY-PACKET-SCHEMA.md), plans/13-r4-conductor-lane-plan.md
SS2.B item 2.2.

R5-T03 (the real packet producer) is unbuilt, so these tests exercise the
content-hash serving mechanism against schema-conformant FIXTURE packets
only — never against a live producer. Acceptance criteria under test:

  1. Serving is content_hash lookup of pre-built fixture packets conforming
     to the frozen v1 schema (schema-validated here).
  2. A dispatch-time-assembly path does not exist: the store exposes
     get-by-hash only, no compose/build API (structural assertion).
  3. Unknown content_hash returns a typed miss, never a synthesized packet.
"""
from __future__ import annotations

import copy
import inspect
from types import MappingProxyType

import pytest

from broker.daemon.packet_store import (
    PacketMiss,
    PacketRecord,
    PacketStore,
    PacketValidationError,
    compute_content_hash,
    validate_packet,
)

# Worked-example shape, verbatim field set, from
# docs/agents/CAPABILITY-PACKET-SCHEMA.md "Worked example" block.
VALID_PACKET: dict = {
    "packet_id": "pkt-2026-07-05-forge-wire-001",
    "schema_version": "1",
    "role_id": "forge-wire",
    "objective": "one-sentence task goal",
    "boundaries": {
        "allow": ["app/api/**", "app/actions/**"],
        "deny_route": ["models/**"],
    },
    "skills_required": ["agent-protocol", "forge-wire-conventions"],
    "references_to_load": ["server-action-contract.md"],
    "examples": ["worked-task-forge-wire.md"],
    "allowed_tools": ["Read", "Edit", "Bash(rtk tsc)"],
    "verification_method": {"type": "command", "command": "rtk tsc && rtk lint"},
    "risk_tier": "T1",
}


def _fixture_store(*packets: dict) -> tuple[PacketStore, list[str]]:
    keyed = {compute_content_hash(p): p for p in packets}
    return PacketStore.from_fixtures(keyed), list(keyed.keys())


# --- 1. schema-conformant fixture packets, served by content_hash ---------


def test_valid_fixture_packet_validates_clean() -> None:
    validate_packet(VALID_PACKET)  # must not raise


def test_get_by_hash_returns_the_matching_fixture_packet() -> None:
    store, [content_hash] = _fixture_store(VALID_PACKET)
    result = store.get_by_hash(content_hash)
    assert isinstance(result, PacketRecord)
    assert result.content_hash == content_hash
    assert result.packet["packet_id"] == VALID_PACKET["packet_id"]
    assert result.packet["role_id"] == "forge-wire"
    assert list(result.packet["skills_required"]) == VALID_PACKET["skills_required"]


def test_content_hash_is_deterministic_and_key_order_independent() -> None:
    reordered = dict(reversed(list(VALID_PACKET.items())))
    assert compute_content_hash(VALID_PACKET) == compute_content_hash(reordered)


def test_content_hash_changes_with_any_field_change() -> None:
    mutated = copy.deepcopy(VALID_PACKET)
    mutated["objective"] = "a different one-sentence task goal"
    assert compute_content_hash(mutated) != compute_content_hash(VALID_PACKET)


def test_two_distinct_fixture_packets_addressed_independently() -> None:
    second = copy.deepcopy(VALID_PACKET)
    second["packet_id"] = "pkt-2026-07-05-pipeline-async-002"
    second["role_id"] = "pipeline-async"
    store, hashes = _fixture_store(VALID_PACKET, second)
    assert len(hashes) == 2
    r0 = store.get_by_hash(hashes[0])
    r1 = store.get_by_hash(hashes[1])
    assert isinstance(r0, PacketRecord) and isinstance(r1, PacketRecord)
    assert r0.packet["role_id"] != r1.packet["role_id"]


def test_served_packet_is_immutable() -> None:
    store, [content_hash] = _fixture_store(VALID_PACKET)
    record = store.get_by_hash(content_hash)
    assert isinstance(record, PacketRecord)
    assert isinstance(record.packet, MappingProxyType)
    with pytest.raises(TypeError):
        record.packet["role_id"] = "tampered"  # type: ignore[index]
    assert isinstance(record.packet["boundaries"], MappingProxyType)
    assert isinstance(record.packet["skills_required"], tuple)


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda p: p.pop("objective"), "objective"),
        (lambda p: p.pop("role_id"), "role_id"),
        (lambda p: p.pop("boundaries"), "boundaries"),
        (lambda p: p.pop("verification_method"), "verification_method"),
        (lambda p: p.__setitem__("schema_version", "2"), "schema_version"),
        (lambda p: p.__setitem__("skills_required", "not-a-list"), "skills_required"),
        (lambda p: p.__setitem__("boundaries", {"allow": ["x"]}), "deny_route"),
        (
            lambda p: p.__setitem__(
                "verification_method", {"type": "command"}  # missing "command"
            ),
            "command",
        ),
        (lambda p: p.__setitem__("packet_id", ""), "packet_id"),
    ],
)
def test_schema_nonconformant_fixture_is_rejected(mutator, match) -> None:
    broken = copy.deepcopy(VALID_PACKET)
    mutator(broken)
    with pytest.raises(PacketValidationError, match=match):
        validate_packet(broken)
    with pytest.raises(PacketValidationError):
        PacketStore.from_fixtures({compute_content_hash(VALID_PACKET): broken})


def test_content_hash_mismatch_between_key_and_packet_is_rejected() -> None:
    wrong_key = "sha256:" + "0" * 64
    with pytest.raises(PacketValidationError, match="content_hash mismatch"):
        PacketStore.from_fixtures({wrong_key: VALID_PACKET})


# --- 2. no compose/build API — structural assertion ------------------------

_FORBIDDEN_NAME_FRAGMENTS = ("compose", "assemble", "synthesize", "generate")


def test_packet_store_public_surface_is_get_and_load_only() -> None:
    public_methods = {
        name
        for name, _ in inspect.getmembers(PacketStore, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    public_classmethods = {
        name
        for name in dir(PacketStore)
        if not name.startswith("_")
        and inspect.ismethod(getattr(PacketStore, name, None))
    }
    all_public = public_methods | public_classmethods
    assert all_public == {"from_fixtures", "get_by_hash"}, (
        f"PacketStore exposes unexpected public members: {all_public}"
    )


def test_no_public_name_in_module_suggests_dispatch_time_assembly() -> None:
    import broker.daemon.packet_store as module

    public_names = [n for n in dir(module) if not n.startswith("_")]
    for name in public_names:
        lowered = name.lower()
        for fragment in _FORBIDDEN_NAME_FRAGMENTS:
            assert fragment not in lowered, f"{name!r} suggests a compose/build API ({fragment})"
    assert "build" not in [n.lower() for n in public_names]


def test_get_by_hash_signature_takes_only_a_hash_no_packet_fields() -> None:
    sig = inspect.signature(PacketStore.get_by_hash)
    params = [p for p in sig.parameters if p != "self"]
    assert params == ["content_hash"]
    packet_field_names = {
        "role_id",
        "objective",
        "boundaries",
        "skills_required",
        "references_to_load",
        "examples",
        "allowed_tools",
        "verification_method",
        "risk_tier",
    }
    assert not (set(sig.parameters) & packet_field_names)


def test_from_fixtures_signature_takes_only_a_packet_mapping_no_discrete_fields() -> None:
    sig = inspect.signature(PacketStore.from_fixtures)
    params = [p for p in sig.parameters if p not in ("cls",)]
    assert params == ["packets"]


# --- 3. unknown content_hash -> typed miss, never a synthesized packet -----


def test_unknown_hash_returns_typed_miss_not_none_not_exception() -> None:
    store, _ = _fixture_store(VALID_PACKET)
    result = store.get_by_hash("sha256:" + "f" * 64)
    assert isinstance(result, PacketMiss)
    assert result.content_hash == "sha256:" + "f" * 64


def test_miss_carries_no_packet_payload() -> None:
    result = PacketMiss(content_hash="sha256:deadbeef")
    assert not hasattr(result, "packet")
    assert set(vars(result).keys()) == {"content_hash"}


def test_empty_store_never_fabricates_a_packet_for_any_hash() -> None:
    store = PacketStore.from_fixtures({})
    for probe in ("sha256:" + "1" * 64, "", "not-even-a-hash", VALID_PACKET["packet_id"]):
        result = store.get_by_hash(probe)
        assert isinstance(result, PacketMiss)
        assert result.content_hash == probe


def test_miss_is_distinguishable_from_record_by_type_not_by_truthiness() -> None:
    store, [content_hash] = _fixture_store(VALID_PACKET)
    hit = store.get_by_hash(content_hash)
    miss = store.get_by_hash("sha256:" + "2" * 64)
    assert type(hit) is not type(miss)
    assert isinstance(hit, PacketRecord)
    assert isinstance(miss, PacketMiss)

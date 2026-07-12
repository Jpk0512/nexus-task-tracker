"""Daemon capability-packet store — R4-T09 N19 (plans/13 SS2.B item 2.2).

Serves pre-built, immutable, content-addressed capability packets by
`content_hash` lookup against the FROZEN v1 schema
(`docs/agents/CAPABILITY-PACKET-SCHEMA.md`). R5-T03, the real packet
producer, is unbuilt — this store is fixture-backed until R5-T03 lands
(plans/13 SS2.B cross-release note). No `depends_on` into R5.

Governance (plans/03 SS3.2's four hard rules, restated for this module): the
daemon SELECTS pre-built packets, it NEVER composes/assembles/builds one at
dispatch time. That is why this module's only mutating entry point is
`PacketStore.from_fixtures` — a straight ingest of already-built packet dicts
keyed by their own `content_hash` — and the only read entry point is
`PacketStore.get_by_hash`. There is deliberately no `compose`/`build`/
`assemble` function anywhere in this module, and no method accepts discrete
packet fields (`role_id=`, `objective=`, ...) that would let a caller
synthesize a packet at call time.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION_SUPPORTED = ("1",)

_REQUIRED_STR_FIELDS = ("packet_id", "schema_version", "role_id", "objective", "risk_tier")
_REQUIRED_LIST_FIELDS = ("skills_required", "references_to_load", "examples", "allowed_tools")


class PacketValidationError(ValueError):
    """A fixture packet does not conform to the frozen v1 schema."""


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    return value


def validate_packet(packet: Mapping[str, Any]) -> None:
    """Raise `PacketValidationError` unless `packet` conforms to the frozen v1
    schema (`docs/agents/CAPABILITY-PACKET-SCHEMA.md`, all 11 fields required).
    """
    if not isinstance(packet, Mapping):
        raise PacketValidationError(f"packet must be a mapping, got {type(packet).__name__}")

    for field in _REQUIRED_STR_FIELDS:
        if field not in packet:
            raise PacketValidationError(f"missing required field: {field!r}")
        if not isinstance(packet[field], str) or not packet[field]:
            raise PacketValidationError(f"field {field!r} must be a non-empty string")

    if packet["schema_version"] not in SCHEMA_VERSION_SUPPORTED:
        raise PacketValidationError(
            f"unsupported schema_version {packet['schema_version']!r}; "
            f"supported: {SCHEMA_VERSION_SUPPORTED}"
        )

    for field in _REQUIRED_LIST_FIELDS:
        if field not in packet:
            raise PacketValidationError(f"missing required field: {field!r}")
        value = packet[field]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PacketValidationError(f"field {field!r} must be a list of strings")

    boundaries = packet.get("boundaries")
    if not isinstance(boundaries, Mapping):
        raise PacketValidationError("field 'boundaries' must be an object")
    for sub in ("allow", "deny_route"):
        if sub not in boundaries:
            raise PacketValidationError(f"boundaries missing required field: {sub!r}")
        sub_value = boundaries[sub]
        if not isinstance(sub_value, list) or not all(isinstance(v, str) for v in sub_value):
            raise PacketValidationError(f"boundaries.{sub!r} must be a list of strings")

    verification_method = packet.get("verification_method")
    if not isinstance(verification_method, Mapping):
        raise PacketValidationError("field 'verification_method' must be an object")
    for sub in ("type", "command"):
        sub_value = verification_method.get(sub)
        if not isinstance(sub_value, str) or not sub_value:
            raise PacketValidationError(f"verification_method.{sub!r} must be a non-empty string")


def compute_content_hash(packet: Mapping[str, Any]) -> str:
    """Deterministic sha256 content-address over the packet's canonical JSON form.

    `sort_keys=True` + fixed separators make the address depend only on the
    packet's content, never on caller-supplied key ordering.
    """
    canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PacketRecord:
    """An immutable, pre-built capability packet, addressed by its own content_hash."""

    content_hash: str
    packet: Mapping[str, Any]


@dataclass(frozen=True)
class PacketMiss:
    """Typed miss: `content_hash` has no matching pre-built packet.

    Carries only the hash that was looked up — never a synthesized packet.
    """

    content_hash: str


class PacketStore:
    """Serves pre-built fixture packets by `content_hash`.

    No compose/build API: a dispatch-time-assembly path does not exist on
    this class. The only entry points are a straight ingest of already-built
    packets (`from_fixtures`) and a content-addressed read (`get_by_hash`).
    """

    def __init__(self) -> None:
        self._by_hash: dict[str, PacketRecord] = {}

    @classmethod
    def from_fixtures(cls, packets: Mapping[str, Mapping[str, Any]]) -> PacketStore:
        """Ingest already-built fixture packets keyed by their own content_hash.

        Each packet is schema-validated (frozen v1) and its content_hash is
        recomputed and checked against the supplied key — a mismatch means the
        fixture was tampered with or mis-addressed, and is rejected rather
        than silently re-keyed. This is a straight load, not composition:
        every field in `packets` arrives pre-built from the caller (a fixture
        in tests today; R5-T03's real producer later).
        """
        store = cls()
        for claimed_hash, packet in packets.items():
            validate_packet(packet)
            actual_hash = compute_content_hash(packet)
            if actual_hash != claimed_hash:
                raise PacketValidationError(
                    f"content_hash mismatch for packet_id={packet.get('packet_id')!r}: "
                    f"claimed {claimed_hash!r}, computed {actual_hash!r}"
                )
            store._by_hash[claimed_hash] = PacketRecord(
                content_hash=claimed_hash, packet=_deep_freeze(packet)
            )
        return store

    def get_by_hash(self, content_hash: str) -> PacketRecord | PacketMiss:
        """Content-addressed lookup only. Unknown hash -> typed `PacketMiss`,
        never a synthesized packet.
        """
        record = self._by_hash.get(content_hash)
        if record is None:
            return PacketMiss(content_hash=content_hash)
        return record

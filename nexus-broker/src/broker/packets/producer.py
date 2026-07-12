"""Real capability-packet producer — R5-T03 N48 (plans/15-r5-dag.yaml).

Until this node, `broker.daemon.packet_store` served only hand-authored
FIXTURE packets (`nexus-broker/tests/test_daemon_packet_store.py`'s
docstring: "R5-T03 (the real packet producer) is unbuilt"). This module is
that producer: it assembles a `docs/agents/CAPABILITY-PACKET-SCHEMA.md`
frozen-v1-conformant packet from real dispatch parameters plus a real
`registry_index` capability index — `skills_required` is cross-checked
against actual registry entries (`registry_index.index.lookup_many`) rather
than an invented list, so a typo'd or nonexistent skill id fails production
instead of silently shipping.

Governance parity with `packet_store`'s SS3.2 hard rules: this module
PRODUCES a packet from real inputs, it does not let a caller hand it
discrete packet fields *and* have it silently accept fields the frozen
schema does not define — `produce_packet` always returns exactly the 11
frozen fields (`FROZEN_V1_FIELDS`), never more, never fewer.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from broker.daemon.packet_store import PacketValidationError, validate_packet
from broker.registry_index.index import IndexBuildError, lookup_many

# Mirrors docs/agents/CAPABILITY-PACKET-SCHEMA.md "The frozen schema (v1, all
# 11 fields)" verbatim, in the source doc's field order. Single authored home
# for the field LIST is that doc; this constant exists only so a test can
# assert "no field added/removed" without hand-copying the list a second time.
FROZEN_V1_FIELDS: tuple[str, ...] = (
    "packet_id",
    "schema_version",
    "role_id",
    "objective",
    "boundaries",
    "skills_required",
    "references_to_load",
    "examples",
    "allowed_tools",
    "verification_method",
    "risk_tier",
)


class PacketProductionError(ValueError):
    """A real packet could not be produced: an unknown `skill_id` referenced
    against the supplied registry index, or the assembled packet failed
    frozen-v1 schema validation.
    """


def _derive_packet_id(role_id: str, pre_packet: Mapping[str, Any]) -> str:
    canonical = json.dumps(pre_packet, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"pkt-{role_id}-{digest[:12]}"


def produce_packet(
    *,
    role_id: str,
    objective: str,
    boundaries: Mapping[str, Sequence[str]],
    allowed_tools: Sequence[str],
    verification_method: Mapping[str, str],
    risk_tier: str,
    skill_ids: Sequence[str],
    index: Mapping[str, Any],
    references_to_load: Sequence[str] = (),
    examples: Sequence[str] = (),
    packet_id: str | None = None,
) -> dict[str, Any]:
    """Produce a real, frozen-v1-conformant capability packet.

    `skill_ids` is resolved against `index` (a `registry_index.index.build_index`
    result, kind="skill") via `lookup_many` — an id absent from the registry
    raises `PacketProductionError` rather than being silently included in
    `skills_required`. If `packet_id` is not supplied, it is derived
    deterministically from every other field (same inputs -> same id -> same
    content_hash), so production is repeatable, not timestamp-random.

    Returns a plain (unfrozen) dict with exactly `FROZEN_V1_FIELDS` as its
    keys. Freezing/content-addressing happens at the `PacketStore` ingestion
    boundary (`broker.daemon.packet_store.PacketStore.from_fixtures`), which
    this module deliberately does not call — production and serving stay
    separate, matching packet_store's "select, never compose" governance.
    """
    try:
        resolved_skills = lookup_many(index, skill_ids, kind="skill")
    except IndexBuildError as exc:
        raise PacketProductionError(str(exc)) from exc
    skills_required = [record["id"] for record in resolved_skills]

    pre_packet: dict[str, Any] = {
        "schema_version": "1",
        "role_id": role_id,
        "objective": objective,
        "boundaries": {
            "allow": list(boundaries.get("allow", [])),
            "deny_route": list(boundaries.get("deny_route", [])),
        },
        "skills_required": skills_required,
        "references_to_load": list(references_to_load),
        "examples": list(examples),
        "allowed_tools": list(allowed_tools),
        "verification_method": dict(verification_method),
        "risk_tier": risk_tier,
    }

    resolved_packet_id = packet_id or _derive_packet_id(role_id, pre_packet)
    packet: dict[str, Any] = {"packet_id": resolved_packet_id, **pre_packet}

    if set(packet.keys()) != set(FROZEN_V1_FIELDS):
        raise PacketProductionError(
            f"produced packet field set {sorted(packet.keys())} != frozen v1 field set "
            f"{sorted(FROZEN_V1_FIELDS)}"
        )

    try:
        validate_packet(packet)
    except PacketValidationError as exc:
        raise PacketProductionError(f"produced packet failed schema validation: {exc}") from exc

    return {field: packet[field] for field in FROZEN_V1_FIELDS}

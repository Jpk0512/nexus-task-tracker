"""Capability registry index builder — R5-T03 N48 (plans/15-r5-dag.yaml).

Renders `.nexus/registry/capabilities.json` (the searchable capability
index proposed in `nexus-redesign/research/PROPOSAL-context-slimming-broker-disclosure.md`,
"Proposed Target File Layout") from validated skill/agent/tool metadata
records (`registry_index.metadata`, docs/agents/SKILL-METADATA-SCHEMA.md).

Deterministic by construction: `build_index` sorts records by `id` before
returning, and `render_capabilities_json` renders with `sort_keys=True` and
fixed separators, so the same input content always produces byte-identical
output regardless of caller-supplied ordering.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from broker.registry_index.metadata import MetadataValidationError, validate_metadata

SCHEMA_VERSION = "1"
CAPABILITY_KINDS: tuple[str, ...] = ("skill", "agent", "tool")

# Relative to a registry root (e.g. an installed nexus-package tree), matching
# the proposal's "Proposed Target File Layout" section verbatim.
DEFAULT_CAPABILITIES_RELPATH = Path(".nexus/registry/capabilities.json")


class IndexBuildError(ValueError):
    """Raised when the supplied (kind, metadata) entries cannot be built into
    a valid capability index: an unknown kind, a schema-nonconformant
    metadata record, or a duplicate id across the registry's shared id-space.
    """


def build_index(entries: Iterable[tuple[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """Validate and assemble `entries` (each a `(kind, metadata)` pair) into
    the capability index shape: `{"schema_version": "1", "capabilities": [...]}`,
    sorted deterministically by `id`.

    Each `metadata` is schema-validated (`registry_index.metadata.validate_metadata`)
    before being folded into a capability record. `kind` is attached to the
    record here — it is not part of the authored metadata itself (see
    docs/agents/SKILL-METADATA-SCHEMA.md's "frozen schema" note).
    """
    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for kind, metadata in entries:
        if kind not in CAPABILITY_KINDS:
            raise IndexBuildError(f"unknown capability kind {kind!r}; expected one of {CAPABILITY_KINDS}")
        try:
            validate_metadata(metadata)
        except MetadataValidationError as exc:
            raise IndexBuildError(f"metadata for kind={kind!r} failed validation: {exc}") from exc

        item_id = metadata["id"]
        if item_id in seen_ids:
            raise IndexBuildError(f"duplicate capability id across registry: {item_id!r}")
        seen_ids.add(item_id)

        records.append(
            {
                "id": item_id,
                "kind": kind,
                "category": metadata["category"],
                "authority": metadata["authority"],
                "applies_to": list(metadata["applies_to"]),
                "requires_profile": list(metadata["requires_profile"]),
                "summary": metadata["summary"],
                "token_budget": {
                    "summary": metadata["token_budget"]["summary"],
                    "contract": metadata["token_budget"]["contract"],
                    "full": metadata["token_budget"]["full"],
                },
            }
        )

    records.sort(key=lambda record: record["id"])
    return {"schema_version": SCHEMA_VERSION, "capabilities": records}


def render_capabilities_json(index: Mapping[str, Any]) -> str:
    """Deterministic JSON rendering of a built index (sorted keys, fixed
    separators, trailing newline) — same content always renders identically.
    """
    return json.dumps(index, indent=2, sort_keys=True) + "\n"


def write_capabilities_json(
    root: Path, entries: Iterable[tuple[str, Mapping[str, Any]]]
) -> Path:
    """Build the index from `entries` and write it to
    `root / DEFAULT_CAPABILITIES_RELPATH`, creating parent directories as
    needed. Returns the path written.
    """
    index = build_index(entries)
    out_path = root / DEFAULT_CAPABILITIES_RELPATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_capabilities_json(index), encoding="utf-8")
    return out_path


def lookup(
    index: Mapping[str, Any], capability_id: str, *, kind: str | None = None
) -> dict[str, Any] | None:
    """Return the capability record matching `capability_id` (and `kind`, if
    given), or `None` if absent. Never fabricates a record for an unknown id.
    """
    for record in index.get("capabilities", []):
        if record["id"] != capability_id:
            continue
        if kind is not None and record["kind"] != kind:
            continue
        return record
    return None


def lookup_many(
    index: Mapping[str, Any], capability_ids: Sequence[str], *, kind: str | None = None
) -> list[dict[str, Any]]:
    """`lookup` for each id in `capability_ids`, in the same order. Raises
    `IndexBuildError` naming the first unresolved id rather than silently
    dropping it — callers that need a real (not fixture) registry
    round-trip depend on every referenced id actually being present.
    """
    found: list[dict[str, Any]] = []
    for capability_id in capability_ids:
        record = lookup(index, capability_id, kind=kind)
        if record is None:
            raise IndexBuildError(f"unknown capability id: {capability_id!r} (kind={kind!r})")
        found.append(record)
    return found

"""Capability-registry metadata validator — R5-T03 N48
(docs/agents/SKILL-METADATA-SCHEMA.md, proposal SS5 shape).

Validates a single skill/agent/tool metadata record against the frozen v1
schema (7 fields: `id`, `category`, `authority`, `applies_to`,
`requires_profile`, `summary`, `token_budget` with its three sub-fields).
`kind` is deliberately not part of this record — see the schema doc's "The
frozen schema" note; the index builder (`registry_index.index`) attaches
`kind` alongside a validated record, it is never authored into the record
itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_REQUIRED_STR_FIELDS = ("id", "category", "authority", "summary")
_REQUIRED_LIST_FIELDS = ("applies_to", "requires_profile")
_TOKEN_BUDGET_SUBFIELDS = ("summary", "contract", "full")


class MetadataValidationError(ValueError):
    """A registry metadata record does not conform to the frozen v1 schema
    (`docs/agents/SKILL-METADATA-SCHEMA.md`)."""


def validate_metadata(meta: Mapping[str, Any]) -> None:
    """Raise `MetadataValidationError` unless `meta` conforms to the frozen
    v1 skill/agent/tool metadata schema (all 7 fields required).
    """
    if not isinstance(meta, Mapping):
        raise MetadataValidationError(f"metadata must be a mapping, got {type(meta).__name__}")

    for field in _REQUIRED_STR_FIELDS:
        if field not in meta:
            raise MetadataValidationError(f"missing required field: {field!r}")
        if not isinstance(meta[field], str) or not meta[field]:
            raise MetadataValidationError(f"field {field!r} must be a non-empty string")

    for field in _REQUIRED_LIST_FIELDS:
        if field not in meta:
            raise MetadataValidationError(f"missing required field: {field!r}")
        value = meta[field]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise MetadataValidationError(f"field {field!r} must be a list of strings")

    if "token_budget" not in meta:
        raise MetadataValidationError("missing required field: 'token_budget'")
    token_budget = meta["token_budget"]
    if not isinstance(token_budget, Mapping):
        raise MetadataValidationError("field 'token_budget' must be an object")
    for sub in _TOKEN_BUDGET_SUBFIELDS:
        if sub not in token_budget:
            raise MetadataValidationError(f"token_budget missing required field: {sub!r}")
        sub_value = token_budget[sub]
        if isinstance(sub_value, bool) or not isinstance(sub_value, int) or sub_value < 0:
            raise MetadataValidationError(
                f"token_budget.{sub!r} must be a non-negative integer"
            )

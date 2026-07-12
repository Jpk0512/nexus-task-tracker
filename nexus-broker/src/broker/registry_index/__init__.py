"""Capability registry index — R5-T03 N48 (plans/15-r5-dag.yaml).

Public surface: metadata validation (`metadata`), index building/rendering
(`index`), and search ranking (`discover`). See
docs/agents/SKILL-METADATA-SCHEMA.md for the frozen record schema this
package enforces and indexes.
"""
from __future__ import annotations

from broker.registry_index.discover import discover, estimate_tokens
from broker.registry_index.index import (
    CAPABILITY_KINDS,
    DEFAULT_CAPABILITIES_RELPATH,
    IndexBuildError,
    build_index,
    lookup,
    lookup_many,
    render_capabilities_json,
    write_capabilities_json,
)
from broker.registry_index.metadata import MetadataValidationError, validate_metadata

__all__ = [
    "CAPABILITY_KINDS",
    "DEFAULT_CAPABILITIES_RELPATH",
    "IndexBuildError",
    "MetadataValidationError",
    "build_index",
    "discover",
    "estimate_tokens",
    "lookup",
    "lookup_many",
    "render_capabilities_json",
    "validate_metadata",
    "write_capabilities_json",
]

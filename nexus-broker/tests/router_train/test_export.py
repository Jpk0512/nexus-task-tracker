"""validate() / export() / versions() — schema gate + emitter (00-DESIGN.md 'EXPORT')."""
from __future__ import annotations

import json
from typing import Any

import pytest
from broker.router_train import (
    ValidationError,
    export,
    is_valid,
    label,
    validate,
    versions,
)


def test_valid_pairs_pass_validation(
    clean_record: dict[str, Any], clean_dispatch: dict[str, Any]
) -> None:
    pairs = label([clean_record], [clean_dispatch])
    assert validate(pairs) == []
    assert is_valid(pairs)


def test_export_refuses_on_validate_fail() -> None:
    """A pair missing required label fields must make export() REFUSE to emit."""
    bad_pair = {"prompt": "no label, no provenance"}
    assert validate([bad_pair]), "expected schema violations"
    with pytest.raises(ValidationError):
        export([bad_pair])


def test_export_refuses_on_general_purpose_label() -> None:
    """Even if a general-purpose row reaches export, the schema 'not const' refuses it."""
    gp_pair = {
        "prompt": "p",
        "prompt_hash": "0" * 64,
        "label_persona": "general-purpose",
        "label_source": "dispatch_sidecar",
        "label_confidence": 1.0,
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
    }
    assert validate([gp_pair]), "general-purpose label must violate the schema"
    with pytest.raises(ValidationError):
        export([gp_pair])


def test_export_completion_format(
    clean_record: dict[str, Any], clean_dispatch: dict[str, Any]
) -> None:
    pairs = label([clean_record], [clean_dispatch])
    out = export(pairs, fmt="completion")
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[0]["prompt"] == clean_record["prompt"]
    assert rows[0]["completion"] == "pipeline-data"
    assert rows[0]["_meta"]["router_model"] == "granite-4.1-3b"


def test_export_messages_format(
    clean_record: dict[str, Any], clean_dispatch: dict[str, Any]
) -> None:
    pairs = label([clean_record], [clean_dispatch])
    out = export(pairs, fmt="messages")
    row = json.loads(out.splitlines()[0])
    assert row["messages"][0]["role"] == "user"
    assert row["messages"][1] == {"role": "assistant", "content": "pipeline-data"}


def test_export_rejects_unknown_format(
    clean_record: dict[str, Any], clean_dispatch: dict[str, Any]
) -> None:
    pairs = label([clean_record], [clean_dispatch])
    with pytest.raises(ValueError):
        export(pairs, fmt="csv")


def test_versions_is_deterministic_for_same_set(
    clean_record: dict[str, Any], clean_dispatch: dict[str, Any]
) -> None:
    pairs = label([clean_record], [clean_dispatch])
    stamp = versions(pairs)
    assert set(stamp) == {"router_model", "prompt_template_hash", "eval_set_id"}
    assert stamp == versions(pairs)
    assert len(stamp["eval_set_id"]) == 64

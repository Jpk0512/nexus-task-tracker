"""VALIDATE + EXPORT — schema gate and fine-tune JSONL emitter
(00-DESIGN.md 'VALIDATE' / 'EXPORT').

validate(pairs) checks every pair against router_training_record.schema.json and
returns the per-row violations. export(pairs, fmt) emits ONLY training-grade rows
(label_status == "ok") and REFUSES to emit if validate() finds any violation in
that filtered set (a half-built set fails a gate instead of shipping). The
non-ok rows (generic / retired / buggy) are dropped from the training set here,
but remain visible upstream to the check report. Every export is lineage-stamped
by versions(): {router_model, prompt_template_hash, eval_set_id}.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from broker.router_train.label import LABEL_STATUS_OK

SCHEMA_PATH = Path(__file__).parent / "router_training_record.schema.json"

ROUTER_MODEL = "granite-4.1-3b"

_PROMPT_TEMPLATE_FIELD = "system_prompt_sha256"


def training_grade(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only TRAINING-GRADE rows (label_status == "ok").

    Rows missing label_status are treated as ``ok`` for back-compat with callers
    that build pairs without classifying (e.g. legacy fixtures); only an explicit
    non-ok status (dropped_generic / quarantined_retired / quarantined_buggy)
    excludes a row from the training set.
    """
    return [p for p in pairs if p.get("label_status", LABEL_STATUS_OK) == LABEL_STATUS_OK]


def _load_validator() -> Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    return Draft7Validator(schema)


class ValidationError(Exception):
    """Raised by export() when validate() FAILs — refuses to emit a bad set."""


def validate(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one violation dict per failing (row_index, path, message). Empty == PASS."""
    validator = _load_validator()
    violations: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs):
        for err in validator.iter_errors(pair):
            violations.append(
                {
                    "row_index": index,
                    "path": list(err.absolute_path),
                    "message": err.message,
                }
            )
    return violations


def is_valid(pairs: list[dict[str, Any]]) -> bool:
    return not validate(pairs)


def versions(pairs: list[dict[str, Any]]) -> dict[str, str]:
    """Stamp lineage onto an export: {router_model, prompt_template_hash, eval_set_id}.

    prompt_template_hash is the sha256 over the sorted set of system_prompt_sha256
    values present in the set (the rendered-template lineage). eval_set_id is the
    sha256 over the sorted prompt_hash set — a deterministic id for this exact
    corpus, so the same pairs always stamp the same id.
    """
    template_inputs = sorted(
        {str(p.get(_PROMPT_TEMPLATE_FIELD, "")) for p in pairs if p.get(_PROMPT_TEMPLATE_FIELD)}
    )
    prompt_hashes = sorted({str(p.get("prompt_hash", "")) for p in pairs})
    prompt_template_hash = hashlib.sha256(
        "\n".join(template_inputs).encode("utf-8")
    ).hexdigest()
    eval_set_id = hashlib.sha256("\n".join(prompt_hashes).encode("utf-8")).hexdigest()
    return {
        "router_model": ROUTER_MODEL,
        "prompt_template_hash": prompt_template_hash,
        "eval_set_id": eval_set_id,
    }


def _to_completion_row(pair: dict[str, Any], lineage: dict[str, str]) -> dict[str, Any]:
    return {
        "prompt": pair["prompt"],
        "completion": pair["label_persona"],
        "_meta": lineage,
    }


def _to_messages_row(pair: dict[str, Any], lineage: dict[str, str]) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "user", "content": pair["prompt"]},
            {"role": "assistant", "content": pair["label_persona"]},
        ],
        "_meta": lineage,
    }


def export(pairs: list[dict[str, Any]], fmt: str = "completion") -> str:
    """Emit fine-tune-ready JSONL from TRAINING-GRADE rows only.

    Non-ok rows (label_status ∈ {dropped_generic, quarantined_retired,
    quarantined_buggy}) are filtered out FIRST — they stay visible to the check
    report but never enter the training set. REFUSES (raises ValidationError) if
    validate() FAILs on the remaining ok rows.

    fmt ∈ {"completion", "messages"}:
      completion → {"prompt", "completion"}
      messages   → {"messages": [{user}, {assistant}]}
    Every row carries the versions() lineage stamp under "_meta".
    """
    if fmt not in ("completion", "messages"):
        raise ValueError(f"unknown export fmt: {fmt!r}")
    grade = training_grade(pairs)
    violations = validate(grade)
    if violations:
        raise ValidationError(
            f"export refused: {len(violations)} schema violation(s); "
            f"first: {violations[0]}"
        )
    lineage = versions(grade)
    builder = _to_completion_row if fmt == "completion" else _to_messages_row
    return "\n".join(json.dumps(builder(p, lineage)) for p in grade)

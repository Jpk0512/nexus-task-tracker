"""Anti-regression tests for the producer↔consumer field contract in the
router_train labeling pipeline.

ROOT CAUSE (confirmed 2026-06-05):
  broker/router_train/label.py reads `disp.get("dispatched_persona")` for the gold
  persona, but the dispatch rows built by the transcript miner
  (_extract_prompts_and_dispatches) or any test fixture that used the key
  `label_persona` instead of `dispatched_persona` would silently emit an empty
  gold persona and be dropped → 0 training pairs with NO error.

Three tests:

  1. CONTRACT TEST — pin the exact field that mine_transcripts() synthetic-dispatch
     rows must carry so label() can pick it up. A synthetic capture + synthetic
     mined-dispatch (same session, temporally aligned) must produce exactly one pair
     whose label_persona == the dispatched persona (non-empty). This test fails on
     field-name mismatch and goes GREEN automatically once the contract is honored.

  2. END-TO-END >0 TEST — drive label() → training_grade() → export() with a small
     fixture (N=3 prompts, temporally-alignable dispatches within the same session)
     and assert >0 JSONL lines are emitted, each with a non-empty label_persona drawn
     from NEXUS_PERSONAS.

  3. JSONSCHEMA DEP TEST — assert `import jsonschema` succeeds, guarding the
     undeclared-dep regression (export.py imports Draft7Validator but jsonschema was
     absent from pyproject.toml).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from broker.router_train.label import LABEL_STATUS_OK, label
from broker.router_train.export import export, training_grade, validate
from broker.registry import DISPATCHABLE_PERSONAS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _capture(
    session_id: str,
    prompt: str,
    ts: str,
    *,
    router_version: str = "fixed",
    model_id: str = "granite-4.1-3b",
    schema_version: int = 2,
) -> dict[str, Any]:
    """Minimal capture-shaped record matching real broker schema."""
    return {
        "session_id": session_id,
        "prompt": prompt,
        "prompt_hash": _hash(prompt),
        "timestamp": ts,
        "router_version": router_version,
        "model_id": model_id,
        "schema_version": schema_version,
    }


def _dispatch(
    session_id: str,
    persona: str,
    ts: str,
    *,
    prompt_hash: str = "",
) -> dict[str, Any]:
    """Synthetic dispatch row — shape emitted by _extract_prompts_and_dispatches.

    The ONLY key the labeler reads for the gold persona is `dispatched_persona`.
    If the producer emitted any other key (e.g. `label_persona`) the labeler
    silently drops the pair → 0 training rows.
    """
    return {
        "session_id": session_id,
        "prompt_hash": prompt_hash,
        "dispatched_persona": persona,
        "ts": ts,
    }


# ---------------------------------------------------------------------------
# Test 1 — CONTRACT: dispatched_persona field reaches label() unchanged
# ---------------------------------------------------------------------------

def test_contract_dispatched_persona_field_survives_join() -> None:
    """Given a capture and a temporal dispatch whose gold persona is in NEXUS_PERSONAS,
    When label() joins them within the same session,
    Then exactly one pair is emitted whose label_persona is non-empty and equals the
    dispatch's `dispatched_persona` — proving the producer field reaches the consumer.

    This test fails today because any field-name mismatch between the producer
    (miner/sidecar) and the consumer (label()) silently drops the pair → 0 rows.
    It becomes GREEN automatically once the contract is honored.
    """
    # GIVEN — a single capture with a temporally-following dispatch in the same session
    session = "sess-contract-guard"
    persona = "quill-py"
    assert persona in DISPATCHABLE_PERSONAS, (
        "fixture persona must be in DISPATCHABLE_PERSONAS for label_status==ok"
    )

    capture = _capture(
        session_id=session,
        prompt="Write failing tests for the router labeling pipeline.",
        ts="2026-06-05T10:00:00+00:00",
    )
    # Temporal dispatch: ts > capture ts, same session, no exact hash match
    # (mirrors the transcript-miner path where prompt_hash is "")
    dispatch = _dispatch(
        session_id=session,
        persona=persona,
        ts="2026-06-05T10:00:30+00:00",
    )

    # WHEN
    pairs = label([capture], [dispatch])

    # THEN — must yield exactly one pair with the correct non-empty gold persona
    assert len(pairs) == 1, (
        f"Expected 1 labeled pair; got {len(pairs)}. "
        "Likely cause: `dispatched_persona` field in dispatch dict does not match "
        "the field name label() reads, so the gold persona is empty and the pair is dropped."
    )
    pair = pairs[0]
    assert pair.get("label_persona"), (
        "label_persona must be non-empty; an empty value means field-name mismatch "
        "between the dispatch producer and label() consumer."
    )
    assert pair["label_persona"] == persona, (
        f"Expected label_persona={persona!r}; got {pair.get('label_persona')!r}. "
        "The gold persona from `dispatched_persona` did not propagate through label()."
    )
    assert pair["label_status"] == LABEL_STATUS_OK, (
        f"label_status must be 'ok' for a valid NEXUS_PERSONAS persona; got {pair.get('label_status')!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — END-TO-END >0: label → validate → training_grade → export yields rows
# ---------------------------------------------------------------------------

def test_end_to_end_pipeline_yields_nonzero_training_pairs() -> None:
    """Given N captures and a matching transcript-style dispatch list that should align,
    When the full chain label() → training_grade() → export() runs,
    Then >0 JSONL lines are emitted, each with a non-empty label_persona from NEXUS_PERSONAS.

    This test fails today because the field mismatch or temporal-fallback failure
    causes 0 labeled → 0 training_grade → 0 export lines.
    """
    # GIVEN — 3 prompts in one session, each followed by a dispatch to a valid persona
    session = "sess-e2e-chain"
    fixtures: list[tuple[str, str, str, str]] = [
        # (prompt, capture_ts, dispatch_ts, persona)
        (
            "Investigate why the router labeling pipeline yields 0 pairs.",
            "2026-06-05T09:00:00+00:00",
            "2026-06-05T09:00:30+00:00",
            "scout",
        ),
        (
            "Write a failing test that pins the dispatched_persona field contract.",
            "2026-06-05T09:01:00+00:00",
            "2026-06-05T09:01:30+00:00",
            "quill-py",
        ),
        (
            "Implement the fix so dispatched_persona reaches label() unchanged.",
            "2026-06-05T09:02:00+00:00",
            "2026-06-05T09:02:30+00:00",
            "pipeline-data",
        ),
    ]
    for _, _, _, persona in fixtures:
        assert persona in DISPATCHABLE_PERSONAS, (
            f"fixture persona {persona!r} must be in DISPATCHABLE_PERSONAS"
        )

    captures = [
        _capture(session_id=session, prompt=prompt, ts=cap_ts)
        for prompt, cap_ts, _disp_ts, _persona in fixtures
    ]
    dispatches = [
        _dispatch(session_id=session, persona=persona, ts=disp_ts)
        for _prompt, _cap_ts, disp_ts, persona in fixtures
    ]

    # WHEN — full pipeline
    pairs = label(captures, dispatches)
    grade = training_grade(pairs)
    violations = validate(grade)

    # THEN — must produce >0 training-grade rows
    assert len(pairs) > 0, (
        f"label() returned 0 pairs from {len(captures)} captures + {len(dispatches)} dispatches. "
        "Temporal alignment failed or `dispatched_persona` field mismatch dropped all rows."
    )
    assert len(grade) > 0, (
        f"training_grade() returned 0 rows from {len(pairs)} labeled pairs. "
        "All rows were either dropped_generic, quarantined_retired, or quarantined_buggy."
    )
    assert violations == [], (
        f"validate() found {len(violations)} schema violation(s): {violations[:2]}"
    )

    # Each training-grade pair must carry a non-empty label_persona from NEXUS_PERSONAS
    for i, pair in enumerate(grade):
        lp = pair.get("label_persona", "")
        assert lp, f"grade[{i}].label_persona is empty"
        assert lp in DISPATCHABLE_PERSONAS, (
            f"grade[{i}].label_persona={lp!r} is not in DISPATCHABLE_PERSONAS"
        )

    # Verify export() emits >0 JSONL lines (completion format)
    jsonl = export(grade)
    lines = [ln for ln in jsonl.splitlines() if ln.strip()]
    assert len(lines) > 0, (
        f"export() emitted 0 JSONL lines from {len(grade)} training-grade pairs."
    )

    # Each exported line must deserialize and carry a non-empty completion
    for i, line in enumerate(lines):
        row: dict[str, Any] = json.loads(line)
        assert row.get("prompt"), f"line[{i}].prompt is empty"
        assert row.get("completion"), f"line[{i}].completion is empty"
        assert row["completion"] in DISPATCHABLE_PERSONAS, (
            f"line[{i}].completion={row['completion']!r} is not in DISPATCHABLE_PERSONAS"
        )


# ---------------------------------------------------------------------------
# Test 3 — JSONSCHEMA DEP: `import jsonschema` must not raise ModuleNotFoundError
# ---------------------------------------------------------------------------

def test_jsonschema_importable_guards_undeclared_dep() -> None:
    """Given jsonschema is used by export.py (Draft7Validator),
    When we import jsonschema at test time,
    Then no ModuleNotFoundError is raised — proving it is a declared project dependency.

    export.py imports `from jsonschema import Draft7Validator` but jsonschema was
    absent from nexus-broker/pyproject.toml; it was only present by luck in the
    live .venv. A fresh `uv sync` or new install env would raise ModuleNotFoundError.
    """
    # GIVEN/WHEN — bare import; the module must resolve in the uv env
    try:
        import jsonschema  # noqa: F401
        from jsonschema import Draft7Validator  # noqa: F401
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"jsonschema not importable: {exc}. "
            "Add `jsonschema` to the [project].dependencies in nexus-broker/pyproject.toml "
            "and run `uv sync` to fix this."
        )

    # THEN — also confirm export() itself runs without crashing on the import path
    # (uses a minimal valid pair that satisfies the schema)
    minimal_pair: dict[str, Any] = {
        "prompt": "a real user prompt",
        "prompt_hash": _hash("a real user prompt"),
        "label_persona": "scout",
        "label_source": "dispatch_sidecar",
        "label_confidence": 1.0,
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
        "label_status": LABEL_STATUS_OK,
    }
    try:
        result = export([minimal_pair])
    except ModuleNotFoundError as exc:
        pytest.fail(
            f"export() raised ModuleNotFoundError: {exc}. "
            "jsonschema must be declared in pyproject.toml."
        )
    assert result.strip(), "export() must return non-empty JSONL for one valid pair"

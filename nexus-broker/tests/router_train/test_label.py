"""label() — the join + label_status classification (00-DESIGN.md 'LABEL').

label() no longer DROPS quarantined rows: it RETAINS every aligned row stamped
with a label_status so the check report can surface label pollution, and export()
is the gate that keeps only training-grade (label_status=="ok") rows.
"""
from __future__ import annotations

import hashlib
from typing import Any

from broker.router_train import (
    LABEL_STATUS_DROPPED_GENERIC,
    LABEL_STATUS_OK,
    LABEL_STATUS_QUARANTINED_BUGGY,
    label,
    training_grade,
)


def _hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def test_label_emits_pair_from_seeded_record_and_dispatch(
    clean_record: dict[str, Any], clean_dispatch: dict[str, Any]
) -> None:
    pairs = label([clean_record], [clean_dispatch])
    assert len(pairs) > 0
    pair = pairs[0]
    assert pair["label_persona"] == "pipeline-data"
    assert pair["label_status"] == LABEL_STATUS_OK
    assert pair["label_source"] == "dispatch_sidecar"
    assert pair["label_confidence"] == 1.0
    assert pair["prompt"] == clean_record["prompt"]
    assert pair["agree"] is True


def test_buggy_router_version_is_quarantined_and_excluded(
    buggy_record: dict[str, Any], buggy_dispatch: dict[str, Any]
) -> None:
    pairs = label([buggy_record], [buggy_dispatch])
    assert len(pairs) == 1, "buggy rows are RETAINED (stamped), visible to the check"
    assert pairs[0]["label_status"] == LABEL_STATUS_QUARANTINED_BUGGY
    assert training_grade(pairs) == [], (
        "router_version=='buggy' must never enter the training set"
    )


def test_general_purpose_label_is_dropped_generic(
    general_purpose_record: dict[str, Any],
    general_purpose_dispatch: dict[str, Any],
) -> None:
    pairs = label([general_purpose_record], [general_purpose_dispatch])
    assert len(pairs) == 1
    assert pairs[0]["label_status"] == LABEL_STATUS_DROPPED_GENERIC
    assert training_grade(pairs) == [], (
        "general-purpose is the Claude default, not a Nexus persona"
    )


def test_mixed_set_keeps_all_rows_but_only_clean_is_training_grade(
    clean_record: dict[str, Any],
    clean_dispatch: dict[str, Any],
    buggy_record: dict[str, Any],
    buggy_dispatch: dict[str, Any],
    general_purpose_record: dict[str, Any],
    general_purpose_dispatch: dict[str, Any],
) -> None:
    pairs = label(
        [clean_record, buggy_record, general_purpose_record],
        [clean_dispatch, buggy_dispatch, general_purpose_dispatch],
    )
    assert len(pairs) == 3, "every aligned row is retained, stamped with a status"
    grade = training_grade(pairs)
    assert len(grade) == 1
    assert grade[0]["session_id"] == "sess-clean"
    assert all(p["label_persona"] != "general-purpose" for p in grade)
    assert all(p.get("router_version") != "buggy" for p in grade)


def test_record_with_no_following_dispatch_is_unlabeled(
    clean_record: dict[str, Any],
) -> None:
    pairs = label([clean_record], [])
    assert pairs == [], "no dispatch == no ground truth == no pair"


def test_nearest_following_dispatch_alignment_intra_session() -> None:
    """Two prompts in one session map to their nearest-FOLLOWING dispatch, not smeared."""
    p1 = "first prompt routed early"
    p2 = "second prompt routed later"
    records = [
        {
            "session_id": "s",
            "prompt": p1,
            "prompt_hash": _hash(p1),
            "timestamp": "2026-06-03T10:00:00+00:00",
            "router_version": "fixed",
            "model_id": "granite-4.1-3b",
            "schema_version": 2,
        },
        {
            "session_id": "s",
            "prompt": p2,
            "prompt_hash": _hash(p2),
            "timestamp": "2026-06-03T10:05:00+00:00",
            "router_version": "fixed",
            "model_id": "granite-4.1-3b",
            "schema_version": 2,
        },
    ]
    dispatches = [
        {
            "session_id": "s",
            "prompt_hash": "",
            "dispatched_persona": "scout",
            "ts": "2026-06-03T10:00:30+00:00",
        },
        {
            "session_id": "s",
            "prompt_hash": "",
            "dispatched_persona": "lens",
            "ts": "2026-06-03T10:05:30+00:00",
        },
    ]
    pairs = {p["prompt"]: p["label_persona"] for p in label(records, dispatches)}
    assert pairs[p1] == "scout"
    assert pairs[p2] == "lens"


def test_orphan_tail_record_excluded_when_no_following_dispatch() -> None:
    """A session-tail prompt with no following dispatch emits NO training pair.

    The record ts is AFTER all dispatches in the session — previously the
    disps[-1] fallback would attach the last dispatch (bad training pair);
    now _align leaves the record unaligned and label() skips it.
    """
    prompt = "the final prompt, nothing dispatched after it"
    records = [
        {
            "session_id": "s-tail",
            "prompt": prompt,
            "prompt_hash": _hash(prompt),
            "timestamp": "2026-06-03T10:10:00+00:00",
            "router_version": "fixed",
            "model_id": "granite-4.1-3b",
            "schema_version": 2,
        }
    ]
    dispatches = [
        {
            "session_id": "s-tail",
            "prompt_hash": "",
            "dispatched_persona": "scout",
            "ts": "2026-06-03T10:05:00+00:00",
        }
    ]
    pairs = label(records, dispatches)
    assert pairs == [], (
        "orphan-tail record (no dispatch >= record ts) must emit no training pair"
    )


def test_exact_prompt_hash_match_wins_over_time_alignment() -> None:
    prompt = "the prompt with an exact sidecar match"
    ph = _hash(prompt)
    record = {
        "session_id": "s",
        "prompt": prompt,
        "prompt_hash": ph,
        "timestamp": "2026-06-03T10:00:00+00:00",
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
        "schema_version": 2,
    }
    dispatches = [
        {
            "session_id": "s",
            "prompt_hash": "",
            "dispatched_persona": "scout",
            "ts": "2026-06-03T09:59:00+00:00",
        },
        {
            "session_id": "s",
            "prompt_hash": ph,
            "dispatched_persona": "atlas",
            "ts": "2026-06-03T12:00:00+00:00",
        },
    ]
    pairs = label([record], dispatches)
    assert len(pairs) == 1
    assert pairs[0]["label_persona"] == "atlas"

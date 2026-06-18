"""label_status classification + agree semantics (00-DESIGN.md 'LABEL').

Pins the two data-quality fixes the live acceptance run surfaced:
  1. LABEL POLLUTION — generic Claude built-ins are dropped_generic, retired Nexus
     base names are quarantined_retired, buggy router_version is quarantined_buggy;
     only label_status=="ok" rows are training-grade and exported.
  2. FABRICATED AGREE — agree is a real bool only when BOTH pred_persona and
     label_persona are present; otherwise it is unknown (None → absent), never False.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from broker.router_train import (
    LABEL_STATUS_DROPPED_GENERIC,
    LABEL_STATUS_OK,
    LABEL_STATUS_QUARANTINED_BUGGY,
    LABEL_STATUS_QUARANTINED_RETIRED,
    classify_label,
    export,
    label,
    mine_transcripts,  # noqa: F401 — imported to assert package re-export (DEC-005 loose end)
    training_grade,
)


def _hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _record(prompt: str, *, pred: str | None = None, router_version: str = "fixed") -> dict[str, Any]:
    rec: dict[str, Any] = {
        "session_id": "s",
        "prompt": prompt,
        "prompt_hash": _hash(prompt),
        "timestamp": "2026-06-03T10:00:00+00:00",
        "schema_version": 2,
        "router_version": router_version,
        "model_id": "granite-4.1-3b",
    }
    if pred is not None:
        rec["pred_persona"] = pred
    return rec


def _dispatch(persona: str) -> dict[str, Any]:
    return {
        "session_id": "s",
        "prompt_hash": "",
        "dispatched_persona": persona,
        "ts": "2026-06-03T10:00:30+00:00",
    }


# ── classify_label (unit) ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "persona",
    ["general-purpose", "general", "Explore", "claude", "statusline-setup", "output-style-setup"],
)
def test_generic_builtins_classify_dropped_generic(persona: str) -> None:
    assert classify_label(persona, "fixed") == LABEL_STATUS_DROPPED_GENERIC


@pytest.mark.parametrize("persona", ["forge", "pipeline", "quill"])
def test_retired_base_names_classify_quarantined_retired(persona: str) -> None:
    assert classify_label(persona, "fixed") == LABEL_STATUS_QUARANTINED_RETIRED


@pytest.mark.parametrize(
    "persona",
    ["scout", "pipeline-data", "forge-ui", "forge-wire", "quill-py", "lens", "atlas"],
)
def test_real_nexus_personas_classify_ok(persona: str) -> None:
    assert classify_label(persona, "fixed") == LABEL_STATUS_OK


def test_buggy_router_version_overrides_persona_class() -> None:
    """router_version=='buggy' wins even for an otherwise-valid persona."""
    assert classify_label("scout", "buggy") == LABEL_STATUS_QUARANTINED_BUGGY


@pytest.mark.parametrize(
    "persona",
    [
        "claude-code-guide",  # Claude built-in guide agent
        "codex:codex-rescue",  # plugin-namespaced (":" in name)
        "00-orchestrator:orch-supervisor",  # plugin-namespaced orchestrator
        "researcher",  # ad-hoc, not on the Nexus roster
        "nexus-ops",  # ad-hoc probe agent
        "tool-prober",  # ad-hoc probe agent
    ],
)
def test_non_roster_agents_classify_dropped_generic(persona: str) -> None:
    """Built-ins, plugin-namespaced, and ad-hoc agents are not route targets."""
    assert classify_label(persona, "fixed") == LABEL_STATUS_DROPPED_GENERIC


# ── label() end-to-end: status stamping + export exclusion ───────────────────


def test_generic_type_dropped_generic_and_excluded_from_export() -> None:
    """'Explore'/'claude' → dropped_generic, never reaches export()."""
    for generic in ("Explore", "claude"):
        pairs = label([_record(f"p-{generic}")], [_dispatch(generic)])
        assert len(pairs) == 1
        assert pairs[0]["label_status"] == LABEL_STATUS_DROPPED_GENERIC
        assert training_grade(pairs) == []
        assert export(pairs) == "", "generic rows must be excluded from the export"


def test_retired_base_quarantined_and_excluded_from_export() -> None:
    """A retired base ('forge') → quarantined_retired, excluded from export()."""
    pairs = label([_record("p-forge")], [_dispatch("forge")])
    assert len(pairs) == 1
    assert pairs[0]["label_status"] == LABEL_STATUS_QUARANTINED_RETIRED
    assert training_grade(pairs) == []
    assert export(pairs) == ""


def test_valid_personas_stay_ok_and_export() -> None:
    """'scout' and 'pipeline-data' stay ok and survive into the export. Aligned by
    exact prompt_hash so each record gets its intended dispatch."""
    records = [_record("p-scout", pred="scout"), _record("p-pd", pred="atlas")]
    dispatches = [
        {
            "session_id": "s",
            "prompt_hash": _hash("p-scout"),
            "dispatched_persona": "scout",
            "ts": "2026-06-03T10:00:30+00:00",
        },
        {
            "session_id": "s",
            "prompt_hash": _hash("p-pd"),
            "dispatched_persona": "pipeline-data",
            "ts": "2026-06-03T10:00:31+00:00",
        },
    ]
    pairs = label(records, dispatches)
    by_persona = {p["label_persona"]: p for p in pairs}
    assert by_persona["scout"]["label_status"] == LABEL_STATUS_OK
    assert by_persona["pipeline-data"]["label_status"] == LABEL_STATUS_OK
    assert len(training_grade(pairs)) == 2
    assert export(pairs) != ""


# ── agree semantics: never fabricate False ───────────────────────────────────


def test_agree_is_unknown_when_pred_absent() -> None:
    """No pred_persona → agree is unknown (absent), NOT False."""
    pairs = label([_record("p-no-pred")], [_dispatch("scout")])
    assert len(pairs) == 1
    assert "agree" not in pairs[0], "agree must be absent (None), never fabricated False"


def test_agree_is_real_bool_when_both_present() -> None:
    """pred_persona present → agree is a real bool (True on match, False on mismatch)."""
    match = label([_record("p-match", pred="scout")], [_dispatch("scout")])
    mismatch = label([_record("p-mismatch", pred="lens")], [_dispatch("scout")])
    assert match[0]["agree"] is True
    assert mismatch[0]["agree"] is False


def test_legacy_qwen_persona_normalized_on_read() -> None:
    """BACK-COMPAT: a pre-rename record carrying qwen_persona resolves to pred_persona.

    The 604 already-captured rows + any v2 rows written before the rename carry the
    model's guess under qwen_persona. normalize-on-read must surface it as
    pred_persona so old data is not orphaned: agree still computes and the emitted
    pair carries pred_persona (never qwen_persona)."""
    legacy = {
        "session_id": "s",
        "prompt": "x",
        "qwen_persona": "scout",
        "schema_version": 1,
    }
    pairs = label([legacy], [_dispatch("scout")])
    assert len(pairs) == 1
    assert pairs[0]["pred_persona"] == "scout", "qwen_persona must normalize to pred_persona"
    assert "qwen_persona" not in pairs[0], "the legacy key must never leak into the pair"
    assert pairs[0]["agree"] is True, "agree must still compute off the normalized pred_persona"


# ── DEC-005 loose end: package re-export ─────────────────────────────────────


def test_mine_transcripts_importable_from_package() -> None:
    """from broker.router_train import mine_transcripts must work (DEC-005 loose end)."""
    from broker.router_train import mine_transcripts as mt

    assert callable(mt)

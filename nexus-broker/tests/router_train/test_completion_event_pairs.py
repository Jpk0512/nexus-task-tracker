"""Tests pinning completion_event label-source wiring in aggregate.py (WF-C3 / BUG #3 fix).

Criterion 2 (broker layer):
  _read_completion_events excludes persona=='unknown' rows; includes known-persona rows.

Criterion 3:
  collect_labeled_pairs includes label_source=='completion_event' pairs from fixture
  completion_events; dedup precedence (sidecar > completion_event) is respected;
  persona=='unknown' events produce no pairs; the join recovers prompt text from the
  matched decision row.

Run from nexus-broker/:
    uv run pytest tests/router_train/test_completion_event_pairs.py -v
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from broker.router_train.aggregate import (
    LABEL_CONFIDENCE_COMPLETION_EVENT,
    _read_completion_events,
    collect_labeled_pairs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ph(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _write_events(install_root: Path, rows: list[dict[str, Any]]) -> None:
    files_dir = install_root / ".memory" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    path = files_dir / "completion_events.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _good_event(
    session_id: str,
    persona: str,
    prompt_hash: str,
    marker: str = "DONE",
) -> dict[str, Any]:
    return {
        "ts": "2026-06-21T00:00:00+00:00",
        "session_id": session_id,
        "persona": persona,
        "marker": marker,
        "files_changed_count": 1,
        "prompt_hash": prompt_hash,
    }


# ---------------------------------------------------------------------------
# Criterion 2 (broker layer) — _read_completion_events filtering
# ---------------------------------------------------------------------------


class TestReadCompletionEvents:
    """_read_completion_events must exclude persona=='unknown' or empty-prompt_hash rows
    and include rows with a known persona + non-empty prompt_hash.
    """

    def test_unknown_persona_rows_excluded(self, tmp_path: Path) -> None:
        """Given events with persona=='unknown'
        When _read_completion_events reads them
        Then the result is empty (unknown rows are not labelable).
        """
        install_root = tmp_path / "install"
        unknown_row: dict[str, Any] = {
            "ts": "2026-06-21T00:00:00+00:00",
            "session_id": "S-unk",
            "persona": "unknown",
            "marker": "DONE",
            "files_changed_count": 0,
            "prompt_hash": _ph("some prompt"),
        }
        _write_events(install_root, [unknown_row])

        result = _read_completion_events(install_root)
        assert result == [], (
            f"_read_completion_events must exclude persona=='unknown' rows; got {result}"
        )

    def test_empty_persona_rows_excluded(self, tmp_path: Path) -> None:
        """Given an event with persona=='' (empty string)
        When _read_completion_events reads it
        Then the result is empty.
        """
        install_root = tmp_path / "install"
        empty_persona_row: dict[str, Any] = {
            "ts": "2026-06-21T00:00:00+00:00",
            "session_id": "S-empty",
            "persona": "",
            "marker": "DONE",
            "files_changed_count": 0,
            "prompt_hash": _ph("some prompt"),
        }
        _write_events(install_root, [empty_persona_row])

        result = _read_completion_events(install_root)
        assert result == [], (
            f"_read_completion_events must exclude empty-persona rows; got {result}"
        )

    def test_missing_prompt_hash_rows_excluded(self, tmp_path: Path) -> None:
        """Given an event with no prompt_hash field
        When _read_completion_events reads it
        Then the result is empty (row is not joinable without a hash).
        """
        install_root = tmp_path / "install"
        no_hash_row: dict[str, Any] = {
            "ts": "2026-06-21T00:00:00+00:00",
            "session_id": "S-nohash",
            "persona": "pipeline-data",
            "marker": "DONE",
            "files_changed_count": 2,
            # prompt_hash intentionally absent
        }
        _write_events(install_root, [no_hash_row])

        result = _read_completion_events(install_root)
        assert result == [], (
            f"_read_completion_events must exclude rows without prompt_hash; got {result}"
        )

    def test_known_persona_with_hash_included(self, tmp_path: Path) -> None:
        """Given an event with a known persona and a non-empty prompt_hash
        When _read_completion_events reads it
        Then the row IS included in the result.
        """
        install_root = tmp_path / "install"
        good_row = _good_event("S-good", "quill-py", _ph("real developer prompt"))
        _write_events(install_root, [good_row])

        result = _read_completion_events(install_root)
        assert len(result) == 1
        assert result[0]["persona"] == "quill-py"
        assert result[0]["session_id"] == "S-good"

    def test_mixed_rows_only_good_ones_included(self, tmp_path: Path) -> None:
        """Given a mix of good and bad rows
        When _read_completion_events reads them
        Then only the rows with a known persona + prompt_hash are returned.
        """
        install_root = tmp_path / "install"
        ph = _ph("good prompt")
        rows: list[dict[str, Any]] = [
            _good_event("S-1", "scout", ph),
            {
                "ts": "2026-06-21T00:00:00+00:00",
                "session_id": "S-2",
                "persona": "unknown",
                "marker": "DONE",
                "files_changed_count": 0,
                "prompt_hash": _ph("noise"),
            },
            {
                "ts": "2026-06-21T00:00:00+00:00",
                "session_id": "S-3",
                "persona": "hermes",
                "marker": "REVISE",
                # no prompt_hash
                "files_changed_count": 1,
            },
            _good_event("S-4", "atlas", _ph("another real prompt")),
        ]
        _write_events(install_root, rows)

        result = _read_completion_events(install_root)
        assert len(result) == 2
        returned_sessions = {r["session_id"] for r in result}
        assert returned_sessions == {"S-1", "S-4"}

    def test_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Given an install root with no completion_events.jsonl
        When _read_completion_events reads it
        Then an empty list is returned (no error).
        """
        install_root = tmp_path / "no-events-install"
        install_root.mkdir()
        result = _read_completion_events(install_root)
        assert result == []


# ---------------------------------------------------------------------------
# Criterion 3 — collect_labeled_pairs completion_event wiring
# ---------------------------------------------------------------------------


class TestCollectLabeledPairsCompletionEvents:
    """collect_labeled_pairs must include label_source=='completion_event' pairs
    with correct confidence, dedup precedence, and join behaviour.
    """

    def test_completion_event_pairs_included_with_correct_confidence(self) -> None:
        """Given a completion event with known persona + matching decision row
        When collect_labeled_pairs runs with _completion_events_override
        Then a pair with label_source='completion_event' appears,
        with label_confidence == LABEL_CONFIDENCE_COMPLETION_EVENT (0.7).
        """
        prompt = "Deploy the ingestion pipeline to staging"
        ph = _ph(prompt)
        decision: dict[str, Any] = {
            "session_id": "S-ce-01",
            "prompt": prompt,
            "prompt_hash": ph,
            "pred_persona": "pipeline-data",
            "router_version": "fixed",
            "schema_version": 2,
        }
        event = _good_event("S-ce-01", "pipeline-data", ph)

        pairs = collect_labeled_pairs(
            sidecar_decisions=[decision],
            sidecar_dispatches=[],
            _completion_events_override=[event],
            include_synthetic=False,
        )
        ce_pairs = [p for p in pairs if p.get("label_source") == "completion_event"]
        assert ce_pairs, "Expected at least one pair with label_source='completion_event'"
        pair = ce_pairs[0]
        assert pair["label_persona"] == "pipeline-data"
        assert pair["label_confidence"] == pytest.approx(LABEL_CONFIDENCE_COMPLETION_EVENT)
        assert pair["session_id"] == "S-ce-01"
        assert pair["prompt_hash"] == ph

    def test_sidecar_wins_over_completion_event_on_same_key(self) -> None:
        """Given a completion event AND a sidecar dispatch for the same (session_id, prompt_hash)
        When collect_labeled_pairs deduplicates
        Then the sidecar pair wins (confidence 1.0 > LABEL_CONFIDENCE_COMPLETION_EVENT).
        """
        prompt = "Write the router training data export script"
        ph = _ph(prompt)
        decision: dict[str, Any] = {
            "session_id": "S-dedup-01",
            "prompt": prompt,
            "prompt_hash": ph,
            "pred_persona": "quill-py",
            "router_version": "fixed",
            "schema_version": 2,
        }
        dispatch: dict[str, Any] = {
            "session_id": "S-dedup-01",
            "prompt_hash": ph,
            "dispatched_persona": "quill-py",
            "ts": "2026-06-21T10:00:05+00:00",
        }
        event = _good_event("S-dedup-01", "quill-py", ph)

        pairs = collect_labeled_pairs(
            sidecar_decisions=[decision],
            sidecar_dispatches=[dispatch],
            _completion_events_override=[event],
            include_synthetic=False,
        )
        key_pairs = [
            p
            for p in pairs
            if p.get("session_id") == "S-dedup-01" and p.get("prompt_hash") == ph
        ]
        assert len(key_pairs) == 1, (
            f"Expected exactly 1 deduped pair for the key, got {key_pairs}"
        )
        surviving = key_pairs[0]
        assert surviving.get("label_source") != "completion_event", (
            "Sidecar pair (confidence 1.0) must win over completion_event "
            f"({LABEL_CONFIDENCE_COMPLETION_EVENT})"
        )
        assert (surviving.get("label_confidence") or 0.0) > LABEL_CONFIDENCE_COMPLETION_EVENT, (
            f"Winning pair confidence must be > {LABEL_CONFIDENCE_COMPLETION_EVENT}, "
            f"got {surviving.get('label_confidence')}"
        )

    def test_unknown_persona_events_produce_no_pairs(self) -> None:
        """Given a completion event with persona=='unknown'
        When collect_labeled_pairs runs with _completion_events_override
        Then NO completion_event pair is emitted.
        """
        prompt = "Run the nightly sync job"
        ph = _ph(prompt)
        unknown_event: dict[str, Any] = {
            "ts": "2026-06-21T10:00:00+00:00",
            "session_id": "S-unk-01",
            "persona": "unknown",
            "marker": "DONE",
            "files_changed_count": 0,
            "prompt_hash": ph,
        }

        pairs = collect_labeled_pairs(
            sidecar_decisions=[],
            sidecar_dispatches=[],
            _completion_events_override=[unknown_event],
            include_synthetic=False,
        )
        ce_pairs = [p for p in pairs if p.get("label_source") == "completion_event"]
        assert ce_pairs == [], (
            f"persona=='unknown' events must produce NO completion_event pairs; got {ce_pairs}"
        )

    def test_completion_event_join_recovers_prompt_text(self) -> None:
        """Given a completion event that joins to a router_decisions row
        When _completion_events_to_pairs is called with a matching decisions_by_key
        Then the pair includes the prompt text recovered from the matched decision.

        We test _completion_events_to_pairs directly because collect_labeled_pairs
        builds its decisions_by_key from aggregate() (the live registry), which
        does not contain our fixture decision.  The join logic under test lives
        entirely in _completion_events_to_pairs.
        """
        from broker.router_train.aggregate import _completion_events_to_pairs  # noqa: PLC0415

        prompt = "Instrument the broker MCP with arize tracing"
        ph = _ph(prompt)
        decision: dict[str, Any] = {
            "session_id": "S-join-01",
            "prompt": prompt,
            "prompt_hash": ph,
            "pred_persona": "atlas",
            "router_version": "fixed",
            "schema_version": 2,
        }
        event = _good_event("S-join-01", "atlas", ph)
        decisions_by_key: dict[tuple[str, str], dict[str, Any]] = {
            ("S-join-01", ph): decision,
        }

        pairs = _completion_events_to_pairs([event], decisions_by_key)
        assert pairs, "Expected at least one pair from _completion_events_to_pairs"
        pair = pairs[0]
        assert pair.get("label_source") == "completion_event"
        assert pair.get("prompt") == prompt, (
            f"Expected pair to carry prompt text from matched decision; "
            f"got {pair.get('prompt')!r}"
        )

    def test_completion_event_without_matching_decision_still_emitted(self) -> None:
        """Given a completion event with no matching decision row
        When collect_labeled_pairs produces the pair
        Then a pair IS still emitted (prompt field absent/empty, label_persona present).
        """
        ph = _ph("undocumented task prompt")
        event: dict[str, Any] = {
            "ts": "2026-06-21T12:00:00+00:00",
            "session_id": "S-nojoin-01",
            "persona": "hermes",
            "marker": "DONE",
            "files_changed_count": 1,
            "prompt_hash": ph,
        }

        pairs = collect_labeled_pairs(
            sidecar_decisions=[],  # no decision row to join to
            sidecar_dispatches=[],
            _completion_events_override=[event],
            include_synthetic=False,
        )
        ce_pairs = [p for p in pairs if p.get("label_source") == "completion_event"]
        assert ce_pairs, (
            "completion_event pair must be emitted even without a matching decision row"
        )
        pair = ce_pairs[0]
        assert pair["label_persona"] == "hermes"
        assert pair["prompt_hash"] == ph

    def test_multiple_completion_events_all_included(self) -> None:
        """Given multiple completion events with distinct (session_id, prompt_hash) keys
        When collect_labeled_pairs runs
        Then all of them appear as completion_event pairs (no accidental dedup).
        """
        prompts = [
            "Scaffold the new UI component library",
            "Add rate-limiting middleware to the API gateway",
            "Write end-to-end tests for the checkout flow",
        ]
        personas = ["forge-ui", "forge-wire", "quill-py"]
        events = [
            _good_event(f"S-multi-{i}", persona, _ph(prompt))
            for i, (prompt, persona) in enumerate(zip(prompts, personas, strict=True))
        ]

        pairs = collect_labeled_pairs(
            sidecar_decisions=[],
            sidecar_dispatches=[],
            _completion_events_override=events,
            include_synthetic=False,
        )
        ce_pairs = [p for p in pairs if p.get("label_source") == "completion_event"]
        assert len(ce_pairs) == 3, (
            f"Expected 3 completion_event pairs (one per event), got {len(ce_pairs)}: {ce_pairs}"
        )
        returned_personas = {p["label_persona"] for p in ce_pairs}
        assert returned_personas == {"forge-ui", "forge-wire", "quill-py"}

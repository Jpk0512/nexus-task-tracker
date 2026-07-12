"""TDD RED stubs — no-dispatch mining + first-class training label.

Guards five acceptance criteria from the WF-B brief:

  1. A user prompt with NO following Agent dispatch emits a training-grade pair
     with label_persona=='no-dispatch', label_source=='transcript_no_dispatch',
     label_confidence==0.6.
  2. A prompt that IS followed by an Agent dispatch is NOT emitted by
     mine_no_dispatch() (it belongs only to mine_transcripts()).
  3. classify_label / training_grade treat 'no-dispatch' as 'ok' via a DEDICATED
     TRAINING_LABELS allow-set (NEXUS_PERSONAS MUST NOT contain 'no-dispatch').
  4. max_pairs cap: mine_no_dispatch(max_pairs=N) returns exactly N pairs, chosen
     by stable (session_id, prompt_hash) sort — two calls produce identical order.
  5. collect_labeled_pairs() includes no-dispatch pairs, and a prompt that already
     has a real-persona dispatch label is NOT double-counted as no-dispatch.

All fixtures are DETERMINISTIC synthetic temp dirs — no real ~/.claude/projects reads.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

# ── Seams under test ──────────────────────────────────────────────────────────
# mine_no_dispatch and LABEL_SOURCE_NO_DISPATCH do not exist yet → ImportError = RED
from broker.router_train.transcript import (  # type: ignore[attr-defined]
    LABEL_CONFIDENCE_NO_DISPATCH,
    LABEL_SOURCE_NO_DISPATCH,
    mine_no_dispatch,
)

# TRAINING_LABELS (the dedicated allow-set) does not exist yet in label.py → RED
from broker.router_train.label import (  # type: ignore[attr-defined]
    NEXUS_PERSONAS,
    TRAINING_LABELS,
    classify_label,
)

from broker.router_train.aggregate import collect_labeled_pairs
from broker.router_train.export import training_grade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _user_event(session_id: str, prompt: str, ts: str) -> str:
    return json.dumps({
        "timestamp": ts,
        "sessionId": session_id,
        "message": {"role": "user", "content": prompt},
    })


def _agent_event(session_id: str, persona: str, ts: str) -> str:
    return json.dumps({
        "timestamp": ts,
        "sessionId": session_id,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "input": {"subagent_type": persona},
                }
            ],
        },
    })


# ── Fixtures ──────────────────────────────────────────────────────────────────

# Prompt text constants — stable strings so prompt_hash values are deterministic.
_NO_DISPATCH_PROMPT = "What time is it in UTC right now?"
_DISPATCH_PROMPT = "Write the pipeline-data ingestion tests for the export gate"
_EXTRA_PROMPT_A = "Can you summarise the last three session logs?"
_EXTRA_PROMPT_B = "Show me the broker registry persona list"
_EXTRA_PROMPT_C = "What does the CONSTITUTION say about worktrees?"


@pytest.fixture
def single_no_dispatch_root(tmp_path: Path) -> Path:
    """Session with ONE user prompt and NO following Agent dispatch (criterion 1)."""
    projects = tmp_path / "projects"
    proj = projects / "proj-alpha"
    proj.mkdir(parents=True)
    lines = [
        _user_event("sess-no-dispatch", _NO_DISPATCH_PROMPT, "2026-06-20T09:00:00+00:00"),
        # No Agent tool_use follows — this prompt is a no-dispatch candidate.
    ]
    (proj / "sess-no-dispatch.jsonl").write_text("\n".join(lines))
    return projects


@pytest.fixture
def mixed_session_root(tmp_path: Path) -> Path:
    """Session with two prompts: first has NO dispatch, second DOES (criterion 2)."""
    projects = tmp_path / "projects"
    proj = projects / "proj-beta"
    proj.mkdir(parents=True)
    lines = [
        # Prompt 1 — no following dispatch before Prompt 2
        _user_event("sess-mixed", _NO_DISPATCH_PROMPT, "2026-06-20T10:00:00+00:00"),
        # Prompt 2 — followed by an Agent dispatch
        _user_event("sess-mixed", _DISPATCH_PROMPT, "2026-06-20T10:01:00+00:00"),
        _agent_event("sess-mixed", "pipeline-data", "2026-06-20T10:01:05+00:00"),
    ]
    (proj / "sess-mixed.jsonl").write_text("\n".join(lines))
    return projects


@pytest.fixture
def multi_no_dispatch_root(tmp_path: Path) -> Path:
    """Session with MORE than max_pairs no-dispatch candidates (criterion 4 — cap)."""
    projects = tmp_path / "projects"
    proj = projects / "proj-gamma"
    proj.mkdir(parents=True)
    # Build 5 user prompts with no Agent dispatches across the whole session.
    prompts = [
        _EXTRA_PROMPT_A,
        _EXTRA_PROMPT_B,
        _EXTRA_PROMPT_C,
        _NO_DISPATCH_PROMPT,
        _DISPATCH_PROMPT,   # included here WITHOUT a following Agent event → also no-dispatch
    ]
    lines: list[str] = []
    for i, prompt in enumerate(prompts):
        ts = f"2026-06-20T12:0{i}:00+00:00"
        lines.append(_user_event("sess-cap", prompt, ts))
    (proj / "sess-cap.jsonl").write_text("\n".join(lines))
    return projects


@pytest.fixture
def combined_root(tmp_path: Path) -> Path:
    """Two sessions: one no-dispatch, one real-persona dispatch (criterion 5)."""
    projects = tmp_path / "projects"
    proj = projects / "proj-delta"
    proj.mkdir(parents=True)

    # Session A: no-dispatch turn
    lines_a = [
        _user_event("sess-nd", _NO_DISPATCH_PROMPT, "2026-06-20T14:00:00+00:00"),
    ]
    (proj / "sess-nd.jsonl").write_text("\n".join(lines_a))

    # Session B: real dispatch (same prompt text as _DISPATCH_PROMPT)
    lines_b = [
        _user_event("sess-dispatch", _DISPATCH_PROMPT, "2026-06-20T14:01:00+00:00"),
        _agent_event("sess-dispatch", "pipeline-data", "2026-06-20T14:01:05+00:00"),
    ]
    (proj / "sess-dispatch.jsonl").write_text("\n".join(lines_b))
    return projects


# ── Criterion 1 — no-dispatch pair shape ──────────────────────────────────────

class TestMineNoDispatchPairShape:
    """mine_no_dispatch() emits correctly shaped, training-grade pairs."""

    def test_yields_pair_for_prompt_with_no_following_dispatch(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given a session with a user prompt and NO Agent dispatch,
        when mine_no_dispatch(root) is called,
        then it yields exactly one pair for that prompt.
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        assert len(pairs) >= 1, (
            f"Expected >= 1 pair from a session with a no-dispatch prompt; "
            f"got {len(pairs)}"
        )
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert len(nd_pairs) >= 1, (
            f"Expected label_persona=='no-dispatch'; "
            f"got label_personas={[p.get('label_persona') for p in pairs]!r}"
        )

    def test_pair_label_source_is_transcript_no_dispatch(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given a no-dispatch session,
        when mine_no_dispatch() returns a pair,
        then label_source == 'transcript_no_dispatch'.
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert nd_pairs, "Expected at least one no-dispatch pair"
        pair = nd_pairs[0]
        assert pair.get("label_source") == LABEL_SOURCE_NO_DISPATCH, (
            f"label_source must be {LABEL_SOURCE_NO_DISPATCH!r}; "
            f"got {pair.get('label_source')!r}"
        )

    def test_pair_label_confidence_is_0_6(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given a no-dispatch session,
        when mine_no_dispatch() returns a pair,
        then label_confidence == 0.6.
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert nd_pairs, "Expected at least one no-dispatch pair"
        pair = nd_pairs[0]
        conf = pair.get("label_confidence")
        assert conf == LABEL_CONFIDENCE_NO_DISPATCH, (
            f"label_confidence must be {LABEL_CONFIDENCE_NO_DISPATCH}; got {conf!r}"
        )
        assert conf == 0.6, (
            f"label_confidence must be 0.6 exactly; got {conf!r}"
        )

    def test_pair_label_status_is_ok(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given a no-dispatch session,
        when mine_no_dispatch() returns a pair,
        then label_status == 'ok' (training-grade).
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert nd_pairs, "Expected at least one no-dispatch pair"
        pair = nd_pairs[0]
        assert pair.get("label_status") == "ok", (
            f"label_status must be 'ok' for training-grade; "
            f"got {pair.get('label_status')!r}"
        )

    def test_pair_has_required_fields(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given a no-dispatch session,
        when mine_no_dispatch() returns a pair,
        then session_id, prompt, prompt_hash, timestamp are all present.
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert nd_pairs, "Expected at least one no-dispatch pair"
        pair = nd_pairs[0]
        for field in ("session_id", "prompt", "prompt_hash", "timestamp"):
            assert pair.get(field), (
                f"Required field {field!r} is missing or empty in pair: {pair!r}"
            )
        assert isinstance(pair["prompt_hash"], str) and len(pair["prompt_hash"]) == 64, (
            f"prompt_hash must be a 64-char hex sha256; got {pair['prompt_hash']!r}"
        )

    def test_agree_field_is_absent(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given a no-dispatch pair (no model guess on this path),
        when mine_no_dispatch() returns a pair,
        then 'agree' is NOT present (no model guess fabricated).
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert nd_pairs, "Expected at least one no-dispatch pair"
        pair = nd_pairs[0]
        assert "agree" not in pair, (
            f"'agree' must be absent on no-dispatch path (no model guess); "
            f"pair has keys: {sorted(pair.keys())!r}"
        )


# ── Criterion 2 — dispatched prompts NOT emitted ──────────────────────────────

class TestMineNoDispatchExcludesDispatchedPrompts:
    """A prompt followed by an Agent dispatch is NOT emitted by mine_no_dispatch()."""

    def test_dispatched_prompt_excluded(self, mixed_session_root: Path) -> None:
        """Given a session where prompt-2 is followed by an Agent dispatch,
        when mine_no_dispatch() is called,
        then _DISPATCH_PROMPT does NOT appear in any returned pair.
        """
        pairs = mine_no_dispatch(root=mixed_session_root)
        dispatch_prompt_pairs = [
            p for p in pairs if p.get("prompt") == _DISPATCH_PROMPT
        ]
        assert len(dispatch_prompt_pairs) == 0, (
            f"_DISPATCH_PROMPT was followed by an Agent dispatch and must NOT be "
            f"emitted by mine_no_dispatch(); got {dispatch_prompt_pairs!r}"
        )

    def test_no_dispatch_prompt_included(self, mixed_session_root: Path) -> None:
        """Given a session where prompt-1 has NO following dispatch,
        when mine_no_dispatch() is called,
        then _NO_DISPATCH_PROMPT IS present in the returned pairs.
        """
        pairs = mine_no_dispatch(root=mixed_session_root)
        nd_pairs = [p for p in pairs if p.get("prompt") == _NO_DISPATCH_PROMPT]
        assert len(nd_pairs) >= 1, (
            f"_NO_DISPATCH_PROMPT has no following Agent dispatch and MUST appear "
            f"in mine_no_dispatch() output; got pairs with prompts: "
            f"{[p.get('prompt') for p in pairs]!r}"
        )


# ── Criterion 3 — TRAINING_LABELS allow-set + NEXUS_PERSONAS unchanged ────────

class TestTrainingLabelsAllowSet:
    """TRAINING_LABELS contains 'no-dispatch'; NEXUS_PERSONAS does NOT."""

    def test_nexus_personas_does_not_contain_no_dispatch(self) -> None:
        """NEXUS_PERSONAS (the dispatch roster) must NEVER contain 'no-dispatch'."""
        assert "no-dispatch" not in NEXUS_PERSONAS, (
            "'no-dispatch' must NOT be in NEXUS_PERSONAS (the dispatch roster). "
            "It belongs only in the TRAINING_LABELS allow-set."
        )

    def test_training_labels_exists_and_contains_no_dispatch(self) -> None:
        """TRAINING_LABELS (dedicated training allow-set) must contain 'no-dispatch'."""
        assert isinstance(TRAINING_LABELS, frozenset), (
            f"TRAINING_LABELS must be a frozenset; got {type(TRAINING_LABELS)!r}"
        )
        assert "no-dispatch" in TRAINING_LABELS, (
            f"'no-dispatch' must be in TRAINING_LABELS; "
            f"got TRAINING_LABELS={sorted(TRAINING_LABELS)!r}"
        )

    def test_training_labels_is_superset_of_nexus_personas(self) -> None:
        """TRAINING_LABELS must be a superset of NEXUS_PERSONAS
        (every dispatchable persona is also training-grade).
        """
        assert NEXUS_PERSONAS <= TRAINING_LABELS, (
            f"TRAINING_LABELS must include all NEXUS_PERSONAS; "
            f"missing: {NEXUS_PERSONAS - TRAINING_LABELS!r}"
        )

    def test_classify_label_returns_ok_for_no_dispatch(self) -> None:
        """classify_label('no-dispatch', 'fixed') must return 'ok'
        (training-grade via TRAINING_LABELS, not via NEXUS_PERSONAS).
        """
        status = classify_label("no-dispatch", "fixed")
        assert status == "ok", (
            f"classify_label('no-dispatch', 'fixed') must return 'ok'; got {status!r}"
        )

    def test_training_grade_keeps_no_dispatch_pairs(
        self, single_no_dispatch_root: Path
    ) -> None:
        """Given no-dispatch pairs from mine_no_dispatch(),
        when training_grade() is applied,
        then no-dispatch pairs are KEPT (not filtered out).
        """
        pairs = mine_no_dispatch(root=single_no_dispatch_root)
        nd_before = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert nd_before, "Precondition: mine_no_dispatch must yield pairs"

        kept = training_grade(pairs)
        nd_after = [p for p in kept if p.get("label_persona") == "no-dispatch"]
        assert len(nd_after) >= 1, (
            f"training_grade() must retain no-dispatch pairs (label_status='ok'); "
            f"before={len(nd_before)}, after={len(nd_after)}. "
            f"Check that training_grade() uses TRAINING_LABELS not only NEXUS_PERSONAS."
        )


# ── Criterion 4 — max_pairs cap: stable deterministic selection ────────────────

class TestMineNoDispatchMaxPairsCap:
    """max_pairs cap selects a stable, deterministic subset."""

    def test_max_pairs_limits_result_count(self, multi_no_dispatch_root: Path) -> None:
        """Given > max_pairs candidates,
        when mine_no_dispatch(root, max_pairs=2) is called,
        then exactly 2 pairs are returned.
        """
        pairs = mine_no_dispatch(root=multi_no_dispatch_root, max_pairs=2)
        assert len(pairs) == 2, (
            f"mine_no_dispatch(max_pairs=2) must return exactly 2 pairs; "
            f"got {len(pairs)}"
        )

    def test_max_pairs_none_returns_all(self, multi_no_dispatch_root: Path) -> None:
        """Given no max_pairs cap,
        when mine_no_dispatch(root) is called,
        then all candidates are returned (>= 2 for our 5-prompt fixture).
        """
        pairs = mine_no_dispatch(root=multi_no_dispatch_root)
        assert len(pairs) >= 2, (
            f"mine_no_dispatch with no cap must return all candidates; "
            f"fixture has 5 prompts with no dispatches, got {len(pairs)}"
        )

    def test_max_pairs_deterministic_order(self, multi_no_dispatch_root: Path) -> None:
        """Given max_pairs=3 cap,
        when mine_no_dispatch() is called TWICE,
        then both calls return pairs in IDENTICAL order (stable sort, no randomness).
        """
        first_call = mine_no_dispatch(root=multi_no_dispatch_root, max_pairs=3)
        second_call = mine_no_dispatch(root=multi_no_dispatch_root, max_pairs=3)
        assert len(first_call) == len(second_call) == 3, (
            f"Both calls must return 3 pairs; got {len(first_call)}, {len(second_call)}"
        )
        first_keys = [(p.get("session_id"), p.get("prompt_hash")) for p in first_call]
        second_keys = [(p.get("session_id"), p.get("prompt_hash")) for p in second_call]
        assert first_keys == second_keys, (
            f"mine_no_dispatch(max_pairs=3) must return pairs in identical, stable order "
            f"on repeated calls. First call order: {first_keys!r}. "
            f"Second call order: {second_keys!r}."
        )

    def test_max_pairs_sorted_by_session_id_prompt_hash(
        self, multi_no_dispatch_root: Path
    ) -> None:
        """Given max_pairs=2 cap,
        when mine_no_dispatch() is called,
        then the returned pairs are the FIRST 2 when sorted by (session_id, prompt_hash).
        """
        # Get uncapped candidates to know what stable order should give us
        all_pairs = mine_no_dispatch(root=multi_no_dispatch_root)
        capped_pairs = mine_no_dispatch(root=multi_no_dispatch_root, max_pairs=2)

        # Compute the expected stable-sort order
        sorted_all = sorted(
            all_pairs,
            key=lambda p: (str(p.get("session_id") or ""), str(p.get("prompt_hash") or "")),
        )
        expected_keys = [
            (p.get("session_id"), p.get("prompt_hash")) for p in sorted_all[:2]
        ]
        actual_keys = [
            (p.get("session_id"), p.get("prompt_hash")) for p in capped_pairs
        ]
        assert actual_keys == expected_keys, (
            f"Stable cap must be the first max_pairs items from sort by "
            f"(session_id, prompt_hash). "
            f"Expected {expected_keys!r}, got {actual_keys!r}"
        )


# ── Criterion 5 — collect_labeled_pairs() includes no-dispatch; no double-count ─

class TestCollectLabeledPairsNoDispatch:
    """collect_labeled_pairs() wires in no-dispatch; dispatched prompts not double-counted."""

    def test_collect_includes_no_dispatch_pairs(
        self, combined_root: Path
    ) -> None:
        """Given a combined fixture (one no-dispatch session, one dispatch session),
        when collect_labeled_pairs(transcripts_root=...) is called,
        then at least one pair with label_source=='transcript_no_dispatch' is present.
        """
        pairs = collect_labeled_pairs(
            sidecar_decisions=[],
            sidecar_dispatches=[],
            transcripts_root=combined_root,
        )
        nd_pairs = [
            p for p in pairs if p.get("label_source") == LABEL_SOURCE_NO_DISPATCH
        ]
        assert len(nd_pairs) >= 1, (
            f"collect_labeled_pairs() must include no-dispatch pairs from "
            f"mine_no_dispatch(); "
            f"found label_sources={list({p.get('label_source') for p in pairs})!r}"
        )

    def test_collect_dispatch_prompt_not_double_counted_as_no_dispatch(
        self, combined_root: Path
    ) -> None:
        """Given _DISPATCH_PROMPT is labeled by mine_transcripts() (real persona),
        when collect_labeled_pairs() is called,
        then _DISPATCH_PROMPT appears ONCE and its label_persona is NOT 'no-dispatch'.
        """
        pairs = collect_labeled_pairs(
            sidecar_decisions=[],
            sidecar_dispatches=[],
            transcripts_root=combined_root,
        )
        dispatch_hash = _sha256(_DISPATCH_PROMPT)
        dispatch_rows = [
            p for p in pairs
            if p.get("prompt_hash") == dispatch_hash
            and p.get("session_id") == "sess-dispatch"
        ]
        # Must appear at most once
        assert len(dispatch_rows) <= 1, (
            f"_DISPATCH_PROMPT must appear at most once in the union; "
            f"got {len(dispatch_rows)} rows"
        )
        if dispatch_rows:
            persona = dispatch_rows[0].get("label_persona")
            assert persona != "no-dispatch", (
                f"_DISPATCH_PROMPT was followed by a real Agent dispatch; "
                f"its label_persona must be the real persona ('pipeline-data'), "
                f"not 'no-dispatch'. Got {persona!r}"
            )

    def test_no_dispatch_pairs_are_training_grade_in_collect(
        self, combined_root: Path
    ) -> None:
        """Given no-dispatch pairs in the collect union,
        when training_grade() is applied,
        then no-dispatch pairs survive the filter.
        """
        all_pairs = collect_labeled_pairs(
            sidecar_decisions=[],
            sidecar_dispatches=[],
            transcripts_root=combined_root,
        )
        grade = training_grade(all_pairs)
        nd_in_grade = [p for p in grade if p.get("label_persona") == "no-dispatch"]
        assert len(nd_in_grade) >= 1, (
            f"no-dispatch pairs must survive training_grade() filter; "
            f"label_personas in grade: {[p.get('label_persona') for p in grade]!r}"
        )

    def test_collect_accepts_no_dispatch_cap_kwarg(
        self, combined_root: Path
    ) -> None:
        """Given collect_labeled_pairs supports a no_dispatch_max_pairs kwarg,
        when called with no_dispatch_max_pairs=1,
        then at most 1 no-dispatch pair appears in the result.
        """
        pairs = collect_labeled_pairs(
            sidecar_decisions=[],
            sidecar_dispatches=[],
            transcripts_root=combined_root,
            no_dispatch_max_pairs=1,
        )
        nd_pairs = [p for p in pairs if p.get("label_persona") == "no-dispatch"]
        assert len(nd_pairs) <= 1, (
            f"collect_labeled_pairs(no_dispatch_max_pairs=1) must cap no-dispatch "
            f"pairs at 1; got {len(nd_pairs)}"
        )


# ── Re-export from broker.router_train.__init__ ───────────────────────────────

class TestReExportFromInit:
    """mine_no_dispatch is accessible via the top-level broker.router_train namespace."""

    def test_mine_no_dispatch_in_init_namespace(self) -> None:
        """Given the __init__ re-exports mine_no_dispatch,
        when imported from broker.router_train,
        then it is callable.
        """
        from broker.router_train import mine_no_dispatch as _fn  # type: ignore[attr-defined]
        assert callable(_fn), (
            "broker.router_train.mine_no_dispatch must be importable and callable; "
            "add it to __init__.py imports + __all__"
        )

    def test_label_confidence_no_dispatch_in_init_namespace(self) -> None:
        """LABEL_CONFIDENCE_NO_DISPATCH is re-exported from broker.router_train."""
        from broker.router_train import (  # type: ignore[attr-defined]
            LABEL_CONFIDENCE_NO_DISPATCH as _conf,
        )
        assert _conf == 0.6, (
            f"LABEL_CONFIDENCE_NO_DISPATCH must equal 0.6; got {_conf!r}"
        )

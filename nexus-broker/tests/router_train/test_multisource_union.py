"""Integration test: collect_labeled_pairs() multi-source union (TDD RED).

Guards against mine_transcripts() being orphaned again by asserting that
broker.router_train.aggregate.collect_labeled_pairs() (the new public seam) returns
the union of sidecar + transcript sources — more training-grade rows than sidecar
alone, at least one transcript_mining row, and correct dedup on (session_id,
prompt_hash) keeping the higher-confidence row.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

# The seam under test — does NOT yet exist (RED phase).
from broker.router_train.aggregate import collect_labeled_pairs  # type: ignore[attr-defined]
from broker.router_train.export import training_grade
from broker.router_train.label import label
from broker.router_train.transcript import LABEL_SOURCE_TRANSCRIPT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_session_jsonl(
    session_id: str,
    user_prompt: str,
    dispatched_persona: str,
    ts_user: str = "2026-06-20T10:00:00+00:00",
    ts_agent: str = "2026-06-20T10:00:05+00:00",
) -> list[str]:
    """Return JSONL lines for a single-turn transcript session."""
    user_event = {
        "timestamp": ts_user,
        "sessionId": session_id,
        "message": {"role": "user", "content": user_prompt},
    }
    assistant_event = {
        "timestamp": ts_agent,
        "sessionId": session_id,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "input": {"subagent_type": dispatched_persona},
                }
            ],
        },
    }
    return [json.dumps(user_event), json.dumps(assistant_event)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def transcripts_root(tmp_path: Path) -> Path:
    """A synthetic ~/.claude/projects-shaped tree with 2 session files."""
    projects = tmp_path / "projects"
    project_dir = projects / "proj-alpha"
    project_dir.mkdir(parents=True)

    # Session 1: unique transcript-only prompt
    session1_lines = _make_session_jsonl(
        session_id="sess-transcript-only",
        user_prompt="Design the pipeline-async retry strategy",
        dispatched_persona="pipeline-async",
        ts_user="2026-06-20T10:00:00+00:00",
        ts_agent="2026-06-20T10:00:05+00:00",
    )
    (project_dir / "sess-transcript-only.jsonl").write_text("\n".join(session1_lines))

    # Session 2: prompt that is ALSO in the sidecar (dedup test), but with
    # lower confidence because transcript confidence is 0.8 vs sidecar 1.0.
    session2_lines = _make_session_jsonl(
        session_id="sess-shared",
        user_prompt="Write the export gate for router training data",
        dispatched_persona="quill-py",
        ts_user="2026-06-20T11:00:00+00:00",
        ts_agent="2026-06-20T11:00:05+00:00",
    )
    (project_dir / "sess-shared.jsonl").write_text("\n".join(session2_lines))

    return projects


_SHARED_PROMPT = "Write the export gate for router training data"
_SHARED_HASH = _sha256(_SHARED_PROMPT)
_SIDECAR_CONFIDENCE = 1.0  # label() sets label_confidence to 1.0 for sidecar


@pytest.fixture
def sidecar_decisions() -> list[dict[str, Any]]:
    """One capture record for the shared prompt (appears in both sources)."""
    return [
        {
            "session_id": "sess-shared",
            "prompt": _SHARED_PROMPT,
            "prompt_hash": _SHARED_HASH,
            "decision": "prefill",
            "latency_ms": 100.0,
            "timestamp": "2026-06-20T11:00:00+00:00",
            "pred_persona": "quill-py",
            "schema_version": 2,
            "router_version": "fixed",
            "model_id": "granite-4.1-3b",
            "messages": [
                {"role": "system", "content": "router prompt"},
                {"role": "user", "content": _SHARED_PROMPT},
            ],
            "system_prompt_sha256": _sha256("router prompt"),
            "router_code_sha": "abc1234",
            "source_project": "/test",
        }
    ]


@pytest.fixture
def sidecar_dispatches() -> list[dict[str, Any]]:
    """One dispatch row for the shared prompt, persona quill-py."""
    return [
        {
            "session_id": "sess-shared",
            "prompt_hash": _SHARED_HASH,
            "dispatched_persona": "quill-py",
            "ts": "2026-06-20T11:00:05+00:00",
        }
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCollectLabeledPairsUnion:
    """collect_labeled_pairs() merges transcript + sidecar sources correctly."""

    def test_a_union_contains_transcript_mining_row(
        self,
        sidecar_decisions: list[dict[str, Any]],
        sidecar_dispatches: list[dict[str, Any]],
        transcripts_root: Path,
    ) -> None:
        """Given sidecar + transcript fixtures, when collect_labeled_pairs() is called,
        then the training-grade subset contains at least one label_source=='transcript_mining' row.
        """
        union = collect_labeled_pairs(
            sidecar_decisions=sidecar_decisions,
            sidecar_dispatches=sidecar_dispatches,
            transcripts_root=transcripts_root,
        )
        grade = training_grade(union)
        transcript_rows = [
            r for r in grade if r.get("label_source") == LABEL_SOURCE_TRANSCRIPT
        ]
        assert len(transcript_rows) >= 1, (
            f"expected >=1 transcript_mining row in training-grade union; "
            f"got label_sources={[r.get('label_source') for r in grade]!r}"
        )

    def test_b_union_training_grade_exceeds_sidecar_alone(
        self,
        sidecar_decisions: list[dict[str, Any]],
        sidecar_dispatches: list[dict[str, Any]],
        transcripts_root: Path,
    ) -> None:
        """Given sidecar-only baseline vs the union, when training_grade() is applied,
        then the union total is strictly greater than the sidecar-only total.
        """
        sidecar_pairs = label(sidecar_decisions, sidecar_dispatches)
        sidecar_grade_count = len(training_grade(sidecar_pairs))

        union = collect_labeled_pairs(
            sidecar_decisions=sidecar_decisions,
            sidecar_dispatches=sidecar_dispatches,
            transcripts_root=transcripts_root,
        )
        union_grade_count = len(training_grade(union))

        assert union_grade_count > sidecar_grade_count, (
            f"union training-grade ({union_grade_count}) must be strictly greater "
            f"than sidecar-only ({sidecar_grade_count}); "
            f"mine_transcripts() appears not wired into collect_labeled_pairs()"
        )

    def test_c_duplicate_prompt_appears_once_with_higher_confidence(
        self,
        sidecar_decisions: list[dict[str, Any]],
        sidecar_dispatches: list[dict[str, Any]],
        transcripts_root: Path,
    ) -> None:
        """Given a prompt present in BOTH sidecar (confidence=1.0) and transcript
        (confidence=0.8), when collect_labeled_pairs() is called,
        then the shared prompt appears exactly once and carries the higher confidence (1.0).
        """
        union = collect_labeled_pairs(
            sidecar_decisions=sidecar_decisions,
            sidecar_dispatches=sidecar_dispatches,
            transcripts_root=transcripts_root,
        )
        shared_rows = [
            r for r in union
            if r.get("session_id") == "sess-shared"
            and r.get("prompt_hash") == _SHARED_HASH
        ]
        assert len(shared_rows) == 1, (
            f"shared prompt (session_id=sess-shared, prompt_hash={_SHARED_HASH[:12]}…) "
            f"must appear exactly once in the union; got {len(shared_rows)} rows. "
            f"Dedup by (session_id, prompt_hash) is not implemented."
        )
        kept_confidence = float(shared_rows[0].get("label_confidence") or 0.0)
        assert kept_confidence == _SIDECAR_CONFIDENCE, (
            f"on key collision the row with HIGHER confidence must be kept; "
            f"expected label_confidence={_SIDECAR_CONFIDENCE}, got {kept_confidence!r}"
        )

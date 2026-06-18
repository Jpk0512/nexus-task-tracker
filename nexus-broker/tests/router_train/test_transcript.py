"""transcript mining — nearest-FOLLOWING alignment, multi-turn safe
(00-DESIGN.md 'GROUND-TRUTH … BOOTSTRAP/FALLBACK', T3).

The load-bearing assertion: in a multi-turn session each user prompt maps to its
nearest-FOLLOWING ``Agent`` dispatch, NOT one persona smeared across the session.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from broker.router_train import training_grade
from broker.router_train.label import (
    LABEL_STATUS_DROPPED_GENERIC,
    LABEL_STATUS_OK,
    LABEL_STATUS_QUARANTINED_RETIRED,
)
from broker.router_train.transcript import (
    LABEL_CONFIDENCE_TRANSCRIPT,
    LABEL_SOURCE_TRANSCRIPT,
    mine_transcripts,
)


def _hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


PROMPT_1 = "First turn: design the dispatch sidecar"
PROMPT_2 = "Second turn: now write the transcript miner"


def _user_line(prompt: str, ts: str, session_id: str) -> dict:
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {"role": "user", "content": prompt},
    }


def _agent_line(persona: str, ts: str, session_id: str, desc: str) -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"toolu_{persona}",
                    "name": "Agent",
                    "input": {
                        "description": desc,
                        "prompt": "…brief…",
                        "subagent_type": persona,
                    },
                }
            ],
        },
    }


def _write_session(root: Path, session_id: str, lines: list[dict]) -> Path:
    project_dir = root / "-Users-john-keeney-some-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    with path.open("w") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return path


def test_multi_turn_each_prompt_maps_to_nearest_following_agent(
    tmp_path: Path,
) -> None:
    """Two prompts, two interleaved Agent dispatches → distinct nearest-following labels."""
    session_id = "11111111-2222-3333-4444-555555555555"
    lines = [
        _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", session_id),
        _agent_line("scout", "2026-06-03T10:00:30.000Z", session_id, "recon"),
        _user_line(PROMPT_2, "2026-06-03T10:05:00.000Z", session_id),
        _agent_line("pipeline-async", "2026-06-03T10:05:30.000Z", session_id, "mine"),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)
    by_prompt = {p["prompt"]: p["label_persona"] for p in pairs}

    assert by_prompt[PROMPT_1] == "scout"
    assert by_prompt[PROMPT_2] == "pipeline-async"
    assert by_prompt[PROMPT_1] != by_prompt[PROMPT_2], (
        "a session-level smear would give both prompts the same persona"
    )


def test_label_source_and_confidence_are_transcript_mined(tmp_path: Path) -> None:
    session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    lines = [
        _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", session_id),
        _agent_line("lens", "2026-06-03T10:00:30.000Z", session_id, "verify"),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["label_source"] == LABEL_SOURCE_TRANSCRIPT == "transcript_mining"
    assert pair["label_confidence"] == LABEL_CONFIDENCE_TRANSCRIPT == 0.8
    assert pair["label_persona"] == "lens"
    assert pair["prompt"] == PROMPT_1
    assert pair["prompt_hash"] == _hash(PROMPT_1)


def test_prompt_with_no_following_agent_is_unlabeled(tmp_path: Path) -> None:
    """A trailing prompt with no following dispatch is excluded (orphan-tail).

    PROMPT_1 has a following dispatch (scout) and is labeled. PROMPT_2 is a
    session-tail orphan — no dispatch follows it — so it emits no training pair.
    The old disps[-1] fallback that back-labeled it with scout is intentionally
    removed; orphan-tail records produce bad training pairs and must be excluded.
    """
    session_id = "00000000-0000-0000-0000-000000000000"
    lines = [
        _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", session_id),
        _agent_line("scout", "2026-06-03T10:00:30.000Z", session_id, "recon"),
        _user_line(PROMPT_2, "2026-06-03T10:10:00.000Z", session_id),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)
    by_prompt = {p["prompt"]: p["label_persona"] for p in pairs}
    assert by_prompt[PROMPT_1] == "scout"
    assert PROMPT_2 not in by_prompt, "orphan-tail prompt must emit no training pair"


def test_general_purpose_dispatch_is_dropped_generic(tmp_path: Path) -> None:
    """general-purpose is the Claude default, not a Nexus persona — stamped
    dropped_generic and excluded from the training set (never produces a pair)."""
    session_id = "99999999-8888-7777-6666-555555555555"
    lines = [
        _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", session_id),
        _agent_line(
            "general-purpose", "2026-06-03T10:00:30.000Z", session_id, "default"
        ),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)
    assert len(pairs) == 1, "the row is RETAINED (stamped), visible to the check"
    assert pairs[0]["label_status"] == LABEL_STATUS_DROPPED_GENERIC
    assert training_grade(pairs) == [], "general-purpose is never training-grade"


def test_retired_base_name_dispatch_is_quarantined_retired(tmp_path: Path) -> None:
    """A mined RETIRED base name (forge) is ambiguous — quarantined for a human
    split, never silently mapped to a successor or trained on."""
    session_id = "77777777-6666-5555-4444-333333333333"
    lines = [
        _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", session_id),
        _agent_line("forge", "2026-06-03T10:00:30.000Z", session_id, "build"),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)
    assert len(pairs) == 1
    assert pairs[0]["label_status"] == LABEL_STATUS_QUARANTINED_RETIRED
    assert training_grade(pairs) == [], "retired base names need a human split first"


def test_sessions_do_not_cross_contaminate(tmp_path: Path) -> None:
    """A dispatch in session B must never label a prompt in session A."""
    sess_a = "aaaaaaaa-0000-0000-0000-000000000000"
    sess_b = "bbbbbbbb-0000-0000-0000-000000000000"
    _write_session(
        tmp_path,
        sess_a,
        [
            _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", sess_a),
            _agent_line("scout", "2026-06-03T10:00:30.000Z", sess_a, "recon"),
        ],
    )
    _write_session(
        tmp_path,
        sess_b,
        [
            _user_line(PROMPT_2, "2026-06-03T10:00:10.000Z", sess_b),
            _agent_line("forge-ui", "2026-06-03T10:00:40.000Z", sess_b, "build"),
        ],
    )

    pairs = mine_transcripts(root=tmp_path)
    by_prompt = {p["prompt"]: p["label_persona"] for p in pairs}
    assert by_prompt[PROMPT_1] == "scout"
    assert by_prompt[PROMPT_2] == "forge-ui"


def test_transcript_mining_leaves_agree_unknown(tmp_path: Path) -> None:
    """Mining has no pred_persona, so agree must be unknown (absent), never False."""
    session_id = "55555555-4444-3333-2222-111111111111"
    lines = [
        _user_line(PROMPT_1, "2026-06-03T10:00:00.000Z", session_id),
        _agent_line("scout", "2026-06-03T10:00:30.000Z", session_id, "recon"),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)
    assert len(pairs) == 1
    assert pairs[0]["label_status"] == LABEL_STATUS_OK
    assert "agree" not in pairs[0], "agree must be absent (None), not fabricated False"


def test_missing_root_returns_empty() -> None:
    assert mine_transcripts(root=Path("/nonexistent/claude/projects")) == []

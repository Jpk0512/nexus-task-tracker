"""Corpus hygiene — WF-C2 acceptance tests.

Four criterion classes:

  1. is_genuine_user_prompt: rejects each injected-wrapper marker, short/long
     length pathologies, and accepts a normal query in the 12-1500 char range.

  2. mine_transcripts drops noise: a session with a '<task-notification>' user
     turn emits ZERO pairs for that turn; genuine turns are still emitted.

  3. mine_no_dispatch drops noise: a session with a '<system-reminder>' user
     turn is not captured as a no-dispatch pair.

  4. collect_labeled_pairs sidecar filtering + dedup: a noisy sidecar decision
     (prompt contains an injected wrapper) is dropped after is_genuine_user_prompt
     is applied; a clean sidecar pair appears with label_source='dispatch_sidecar';
     on a (session_id, prompt_hash) collision between a clean sidecar pair
     (label_confidence 1.0) and a transcript_mining pair (0.8), the sidecar wins.

All tests are SPLIT-WORKFLOW RED stubs: they contain full GWT assertions that
fail ONLY because the implementation is absent.  The implementer's boundary is
src/ — nobody touches this file; tests go GREEN automatically once the code
lands.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

# The predicate under test — does not exist yet; ImportError makes every test
# in this file RED until the implementer adds is_genuine_user_prompt to transcript.py.
from broker.router_train.transcript import (
    is_genuine_user_prompt,
    mine_no_dispatch,
    mine_transcripts,
)
from broker.router_train.aggregate import collect_labeled_pairs, prompt_hash
from broker.router_train.label import LABEL_SOURCE_SIDECAR


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _user_line(text: str, ts: str, session_id: str) -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session_id,
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _agent_line(persona: str, ts: str, session_id: str) -> dict[str, Any]:
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
                        "description": "persona brief",
                        "prompt": "do the thing",
                        "subagent_type": persona,
                    },
                }
            ],
        },
    }


def _write_session(root: Path, session_id: str, lines: list[dict[str, Any]]) -> None:
    project_dir = root / "-Users-test-hygiene-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    with path.open("w") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


# ---------------------------------------------------------------------------
# Class 1 — is_genuine_user_prompt predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "noise_text",
    [
        "<task-notification>task 42 completed</task-notification>",
        "<system-reminder>You are an AI assistant.</system-reminder>",
        "<command-name>tdd-patterns</command-name>",
        "<local-command-stdout>Exit code: 0</local-command-stdout>",
        "<command-message>some hook context</command-message>",
        "Caveat: The messages below are from a prior context window",
        "[ctx: session_id=abc123 persona=scout]",
    ],
)
def test_is_genuine_rejects_injected_marker(noise_text: str) -> None:
    """Given a string containing an injected-wrapper marker,
    When is_genuine_user_prompt is called,
    Then it returns False (the string is NOT a genuine user prompt).
    """
    # Pad to length >12 so length alone is not the rejection reason —
    # the marker is the cause.
    padded = noise_text if len(noise_text) >= 12 else noise_text + " " * (12 - len(noise_text))
    result: bool = is_genuine_user_prompt(padded)
    assert result is False, (
        f"is_genuine_user_prompt should reject noise marker text but returned True for: {padded!r}"
    )


def test_is_genuine_rejects_too_short() -> None:
    """Given a string shorter than 12 chars after strip,
    When is_genuine_user_prompt is called,
    Then it returns False.
    """
    assert is_genuine_user_prompt("hi") is False
    assert is_genuine_user_prompt("   short  ") is False
    assert is_genuine_user_prompt("") is False


def test_is_genuine_rejects_too_long() -> None:
    """Given a string longer than 1500 chars,
    When is_genuine_user_prompt is called,
    Then it returns False (paste-blob / tool-dump, not a routing query).
    """
    long_text = "A" * 1501
    assert is_genuine_user_prompt(long_text) is False


def test_is_genuine_accepts_normal_query() -> None:
    """Given a normal 12–1500 char routing query with no injected markers,
    When is_genuine_user_prompt is called,
    Then it returns True.
    """
    queries = [
        "Design the auth module for the SPA frontend",
        "Write tests for the router_train aggregate",
        "A" * 12,   # exactly at minimum
        "B" * 1500, # exactly at maximum
    ]
    for q in queries:
        result: bool = is_genuine_user_prompt(q)
        assert result is True, (
            f"is_genuine_user_prompt should accept normal query but returned False for: {q[:60]!r}"
        )


# ---------------------------------------------------------------------------
# Class 2 — mine_transcripts drops task-notification noise turns
# ---------------------------------------------------------------------------


def test_mine_transcripts_drops_task_notification_turn(tmp_path: Path) -> None:
    """Given a session with a '<task-notification>' user turn followed by an
    Agent dispatch, and a subsequent genuine user prompt also followed by a
    dispatch,
    When mine_transcripts is called on that session root,
    Then the task-notification turn emits ZERO pairs and the genuine turn
    emits exactly one pair with the clean prompt text.
    """
    session_id = "hygiene-mine-task-notif-001"
    noise_text = (
        "<task-notification>Task #42 completed: ingestion pipeline ran "
        "successfully.</task-notification>"
    )
    genuine_text = "Write unit tests for the new transcript hygiene predicate"

    lines = [
        # Noise user turn — should be dropped
        _user_line(noise_text, "2026-06-21T10:00:00.000Z", session_id),
        _agent_line("scout", "2026-06-21T10:00:05.000Z", session_id),
        # Genuine user turn — should be kept
        _user_line(genuine_text, "2026-06-21T10:01:00.000Z", session_id),
        _agent_line("quill-py", "2026-06-21T10:01:05.000Z", session_id),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)

    # POSITIVE assertion 1: exactly one pair (the genuine turn)
    assert len(pairs) == 1, (
        f"Expected 1 pair (genuine turn only) but got {len(pairs)}; "
        f"prompts found: {[p['prompt'][:80] for p in pairs]}"
    )

    # POSITIVE assertion 2: the surviving pair carries the genuine prompt
    surviving_prompt: str = pairs[0]["prompt"]
    assert surviving_prompt == genuine_text, (
        f"Surviving pair should carry genuine prompt text, got: {surviving_prompt[:120]!r}"
    )

    # POSITIVE assertion 3: no pair carries the noise text
    noise_prompts = [p for p in pairs if "<task-notification" in p.get("prompt", "")]
    assert len(noise_prompts) == 0, (
        f"Found {len(noise_prompts)} pair(s) with task-notification noise in prompt"
    )


def test_mine_transcripts_drops_system_reminder_turn(tmp_path: Path) -> None:
    """Given a session where the first user turn is a '<system-reminder>' blob,
    When mine_transcripts is called,
    Then zero pairs are emitted for that turn.
    """
    session_id = "hygiene-mine-sysreminder-001"
    noise_text = (
        "<system-reminder>The following deferred tools are now available: "
        "CronCreate, CronDelete, CronList ...</system-reminder>"
    )
    lines = [
        _user_line(noise_text, "2026-06-21T10:00:00.000Z", session_id),
        _agent_line("pipeline-data", "2026-06-21T10:00:10.000Z", session_id),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_transcripts(root=tmp_path)

    assert len(pairs) == 0, (
        f"Expected 0 pairs (system-reminder noise only) but got {len(pairs)}"
    )


# ---------------------------------------------------------------------------
# Class 3 — mine_no_dispatch drops system-reminder noise turns
# ---------------------------------------------------------------------------


def test_mine_no_dispatch_drops_system_reminder_turn(tmp_path: Path) -> None:
    """Given a session where the only user turn is a '<system-reminder>' blob
    with no following Agent dispatch,
    When mine_no_dispatch is called,
    Then zero no-dispatch pairs are emitted (the noise turn is not a genuine prompt).
    """
    session_id = "hygiene-nodispatch-sysreminder-001"
    noise_text = (
        "<system-reminder>The following skills are available for use with "
        "the Skill tool: tdd-patterns, pytest-idioms ...</system-reminder>"
    )
    lines = [
        _user_line(noise_text, "2026-06-21T10:00:00.000Z", session_id),
        # No agent dispatch follows — under the old code this would be a no-dispatch pair.
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_no_dispatch(root=tmp_path)

    assert len(pairs) == 0, (
        f"Expected 0 no-dispatch pairs (system-reminder is not a genuine prompt) "
        f"but got {len(pairs)}: {[p['prompt'][:80] for p in pairs]}"
    )


def test_mine_no_dispatch_keeps_genuine_no_dispatch_turn(tmp_path: Path) -> None:
    """Given a session with a genuine user prompt that has no following Agent
    dispatch (a true no-dispatch scenario),
    When mine_no_dispatch is called,
    Then exactly one pair is emitted for the genuine prompt.
    """
    session_id = "hygiene-nodispatch-genuine-001"
    genuine_text = "What is the current status of the WF-C2 pipeline hygiene task?"
    lines = [
        _user_line(genuine_text, "2026-06-21T10:00:00.000Z", session_id),
        # No agent dispatch — this is a genuine no-dispatch event.
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_no_dispatch(root=tmp_path)

    assert len(pairs) == 1, (
        f"Expected 1 no-dispatch pair for genuine prompt, got {len(pairs)}"
    )
    assert pairs[0]["prompt"] == genuine_text
    assert pairs[0]["label_persona"] == "no-dispatch"


def test_mine_no_dispatch_drops_task_notification_turn(tmp_path: Path) -> None:
    """Given a session where the user turn is a '<task-notification>' blob
    with no following Agent dispatch,
    When mine_no_dispatch is called,
    Then zero pairs are emitted.
    """
    session_id = "hygiene-nodispatch-tasknotif-001"
    noise_text = (
        "<task-notification>Background task #7 (WF-C synthesis) "
        "completed successfully. 168 pairs written.</task-notification>"
    )
    lines = [
        _user_line(noise_text, "2026-06-21T10:00:00.000Z", session_id),
    ]
    _write_session(tmp_path, session_id, lines)

    pairs = mine_no_dispatch(root=tmp_path)

    assert len(pairs) == 0, (
        f"Expected 0 no-dispatch pairs (task-notification noise) but got {len(pairs)}"
    )


# ---------------------------------------------------------------------------
# Class 4 — collect_labeled_pairs: sidecar filtering + dedup priority
# ---------------------------------------------------------------------------


def test_collect_labeled_pairs_noisy_sidecar_decision_is_dropped(
    tmp_path: Path,
) -> None:
    """Given sidecar_decisions that include a row whose prompt is a
    '<task-notification>' blob (noise captured at hook time),
    When collect_labeled_pairs is called with those decisions + dispatches,
    Then NO pair with the noisy prompt text appears in the result with
    label_source='dispatch_sidecar', because is_genuine_user_prompt filters it.
    """
    noise_prompt = (
        "<task-notification>Task #99 finished — aggregate now contains "
        "1038 rows of which 472 are noise.</task-notification>"
    )
    noise_ph = prompt_hash(noise_prompt)

    noisy_decision: dict[str, Any] = {
        "session_id": "sidecar-hygiene-noise-001",
        "prompt": noise_prompt,
        "prompt_hash": noise_ph,
        "decision": "prefill",
        "latency_ms": 50.0,
        "timestamp": "2026-06-21T10:00:00+00:00",
        "pred_persona": "scout",
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
    }
    noisy_dispatch: dict[str, Any] = {
        "session_id": "sidecar-hygiene-noise-001",
        "prompt_hash": noise_ph,
        "dispatched_persona": "scout",
        "ts": "2026-06-21T10:00:05+00:00",
    }

    pairs = collect_labeled_pairs(
        sidecar_decisions=[noisy_decision],
        sidecar_dispatches=[noisy_dispatch],
        include_synthetic=False,
        _synthetic_pairs_override=[],
    )

    # POSITIVE assertion: no dispatch_sidecar pair carries the noise prompt
    sidecar_noise_pairs = [
        p for p in pairs
        if p.get("label_source") == LABEL_SOURCE_SIDECAR
        and "<task-notification" in (p.get("prompt") or "")
    ]
    assert len(sidecar_noise_pairs) == 0, (
        f"Expected 0 sidecar pairs with task-notification noise prompt, "
        f"found {len(sidecar_noise_pairs)}"
    )


def test_collect_labeled_pairs_includes_clean_sidecar_pair(tmp_path: Path) -> None:
    """Given sidecar_decisions with a clean genuine prompt and a matching
    dispatch,
    When collect_labeled_pairs is called,
    Then at least one pair with label_source='dispatch_sidecar' and
    label_confidence=1.0 appears in the result.
    """
    clean_prompt = "Implement the dispatch sidecar router hook in settings.json"
    clean_ph = prompt_hash(clean_prompt)

    clean_decision: dict[str, Any] = {
        "session_id": "sidecar-hygiene-clean-001",
        "prompt": clean_prompt,
        "prompt_hash": clean_ph,
        "decision": "prefill",
        "latency_ms": 120.0,
        "timestamp": "2026-06-21T10:00:00+00:00",
        "pred_persona": "forge-wire",
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
    }
    clean_dispatch: dict[str, Any] = {
        "session_id": "sidecar-hygiene-clean-001",
        "prompt_hash": clean_ph,
        "dispatched_persona": "forge-wire",
        "ts": "2026-06-21T10:00:05+00:00",
    }

    pairs = collect_labeled_pairs(
        sidecar_decisions=[clean_decision],
        sidecar_dispatches=[clean_dispatch],
        include_synthetic=False,
        _synthetic_pairs_override=[],
    )

    sidecar_pairs = [p for p in pairs if p.get("label_source") == LABEL_SOURCE_SIDECAR]
    assert len(sidecar_pairs) >= 1, (
        f"Expected >= 1 pair with label_source='dispatch_sidecar', "
        f"got {len(sidecar_pairs)}"
    )

    # The surviving sidecar pair must carry the clean prompt
    matching = [p for p in sidecar_pairs if p.get("prompt") == clean_prompt]
    assert len(matching) == 1, (
        f"Expected exactly 1 sidecar pair with clean prompt text, got {len(matching)}"
    )
    assert matching[0]["label_confidence"] == 1.0
    assert matching[0]["label_status"] == "ok"


def test_collect_labeled_pairs_sidecar_wins_over_transcript_on_collision(
    tmp_path: Path,
) -> None:
    """Given the same (session_id, prompt_hash) key present in BOTH the sidecar
    (label_confidence=1.0, clean prompt from sidecar) AND a transcript-mined pair
    (label_confidence=0.8),
    When collect_labeled_pairs is called with both sources,
    Then the SIDECAR pair wins: the surviving pair carries
    label_source='dispatch_sidecar' and label_confidence=1.0, not the
    transcript-mined values.
    """
    genuine_prompt = "Audit the router_train miner for injected-turn contamination"
    session_id = "sidecar-vs-transcript-dedup-001"
    ph = prompt_hash(genuine_prompt)

    # Build sidecar pair (confidence 1.0)
    sidecar_decision: dict[str, Any] = {
        "session_id": session_id,
        "prompt": genuine_prompt,
        "prompt_hash": ph,
        "decision": "prefill",
        "latency_ms": 90.0,
        "timestamp": "2026-06-21T10:00:00+00:00",
        "pred_persona": "quill-py",
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
    }
    sidecar_dispatch: dict[str, Any] = {
        "session_id": session_id,
        "prompt_hash": ph,
        "dispatched_persona": "quill-py",
        "ts": "2026-06-21T10:00:05+00:00",
    }

    # Build a transcript-mined pair for the same (session_id, prompt_hash) as an
    # override — same key, lower confidence, different label_source.
    transcript_pair: dict[str, Any] = {
        "session_id": session_id,
        "prompt": genuine_prompt,
        "prompt_hash": ph,
        "label_persona": "quill-py",
        "label_status": "ok",
        "label_source": "transcript_mining",
        "label_confidence": 0.8,
    }

    # Inject the transcript pair alongside real sidecar data.
    # _real_pairs_override bypasses live sources so transcript_pair competes with
    # the sidecar pair under dedup.
    pairs = collect_labeled_pairs(
        sidecar_decisions=[sidecar_decision],
        sidecar_dispatches=[sidecar_dispatch],
        include_synthetic=False,
        _synthetic_pairs_override=[],
        _real_pairs_override=[transcript_pair],
    )

    # Find the pair that matches our (session_id, prompt_hash)
    matching = [
        p for p in pairs
        if p.get("session_id") == session_id and p.get("prompt_hash") == ph
    ]
    assert len(matching) == 1, (
        f"Expected exactly 1 deduped pair for (session_id, prompt_hash), "
        f"got {len(matching)}"
    )

    winner = matching[0]
    assert winner["label_source"] == LABEL_SOURCE_SIDECAR, (
        f"Sidecar pair (conf 1.0) should win over transcript (conf 0.8) on "
        f"dedup collision; winner label_source={winner['label_source']!r}"
    )
    assert winner["label_confidence"] == 1.0, (
        f"Winner label_confidence should be 1.0 (sidecar), got {winner['label_confidence']}"
    )

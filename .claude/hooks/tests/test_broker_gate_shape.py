"""Regression tests for broker-gate.py block-convention normalization (WF7).

broker-gate.py is the PreToolUse(Task) dispatch gate. Before the fix its block()
emitted the LEGACY flat shape ``{"decision":"block","reason":...}`` + ``exit 2``
(shape C). The fix normalizes the JSON to the nested ``permissionDecision: deny``
object the deny gates use — WHILE KEEPING ``exit 2`` so the block stays durable on
BOTH channels and cannot fail open if the harness ignores either one. The predicate
(WHEN it blocks) and the reason strings are UNCHANGED.

Three block paths must each still ``exit 2`` AND now emit a valid nested
``permissionDecision: deny`` object:
  1. broker rejected the dispatch (``approved`` is False),
  2. no ``called_at`` timestamp (validate not called this turn),
  3. state is stale (``called_at`` older than TURN_STALE_SECONDS).

The allow path (fresh approved state) must still ``exit 0`` with no deny output
(requires a fresh notepad_logged_at in state; the hook gates notepad for
standard-tier code-writing dispatches — P2-07).

Fail-CLOSED paths (missing/malformed/unreadable state): the hook is fail-CLOSED
(P2-10) — a down broker must be LOUD, not silently bypassed.  Missing or malformed
broker_state.json produces ``exit 2``, not ``exit 0``.

All tests pass a real subagent_type ("forge-ui") so the hook proceeds past the
bookkeeping early-out (payloads with no persona are native task bookkeeping and
are silently allowed without broker validation).

State is injected via the NEXUS_BROKER_STATE_PATH env override the hook already
honors for test isolation. Mirrors the subprocess-with-stdin-JSON style of
tests/test_p2_hooks.py.

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_broker_gate_shape.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

HOOK_FILE = Path(__file__).resolve().parent.parent / "broker-gate.py"

# Must match TURN_STALE_SECONDS in broker-gate.py.
TURN_STALE_SECONDS = 120


def _run(state_path: Path | None, db_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # A stray state-path override from the dev shell must never leak in.
    env.pop("NEXUS_BROKER_STATE_PATH", None)
    env.pop("_HOOK_DB_PATH", None)
    if state_path is not None:
        env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    if db_path is not None:
        env["_HOOK_DB_PATH"] = str(db_path)
    # Include subagent_type so the hook proceeds past the bookkeeping early-out.
    # A payload with no persona is treated as native task bookkeeping and is
    # silently allowed without broker validation — not what these tests exercise.
    return subprocess.run(
        ["python3", str(HOOK_FILE)],
        input=json.dumps({"tool_name": "Task", "subagent_type": "forge-ui", "tool_input": {}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _hook_specific(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


def _write_state(path: Path, state: dict) -> Path:
    path.write_text(json.dumps(state))
    return path


def _assert_nested_deny(result: subprocess.CompletedProcess[str]) -> str:
    """Common assertions for every block path: exit 2 (durable backstop) AND a
    valid nested permissionDecision=deny object the harness will not drop.
    Returns the permissionDecisionReason for path-specific reason checks."""
    assert result.returncode == 2, (
        f"block path must exit 2 (durable backstop), got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    ho = _hook_specific(result.stdout)
    assert ho.get("hookEventName") == "PreToolUse", (
        f"Expected nested hookEventName=PreToolUse, got: {result.stdout!r}"
    )
    assert ho.get("permissionDecision") == "deny", (
        f"Expected permissionDecision=deny (not legacy flat decision-block), "
        f"got: {result.stdout!r}"
    )
    reason = ho.get("permissionDecisionReason", "")
    assert reason, f"Deny must carry a permissionDecisionReason, got: {result.stdout!r}"
    # The reason string is preserved verbatim from the legacy shape.
    assert reason.startswith("Task dispatch blocked: "), (
        f"Reason prefix must be preserved unchanged, got: {reason!r}"
    )
    # The old flat decision-block keys must be gone (the silent-drop-safe nested
    # shape replaces them).
    payload = json.loads(result.stdout)
    assert "decision" not in payload, (
        f"Legacy flat 'decision' key must be removed, got: {result.stdout!r}"
    )
    assert "reason" not in payload, (
        f"Legacy flat 'reason' key must be removed, got: {result.stdout!r}"
    )
    return reason


def test_rejected_dispatch_denies_exit2_nested(tmp_path: Path) -> None:
    """Given broker state with approved=False, When the gate runs, Then it
    blocks: exit 2 AND a nested permissionDecision=deny naming the rejected
    persona — the broker-rejected predicate is unchanged."""
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {"approved": False, "persona": "forge-ui"},
    )
    result = _run(state_path)
    reason = _assert_nested_deny(result)
    assert "broker rejected dispatch to 'forge-ui'" in reason, (
        f"Reason must name the rejected persona unchanged, got: {reason!r}"
    )
    assert "nexus_validate_brief" in reason


def test_no_called_at_denies_exit2_nested(tmp_path: Path) -> None:
    """Given approved state with NO called_at timestamp, When the gate runs,
    Then it blocks: exit 2 AND nested permissionDecision=deny — the
    missing-called_at predicate is unchanged."""
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {"approved": True, "persona": "forge-ui"},
    )
    result = _run(state_path)
    reason = _assert_nested_deny(result)
    assert "no called_at timestamp" in reason, (
        f"Reason must name the missing-timestamp cause unchanged, got: {reason!r}"
    )


def test_stale_state_denies_exit2_nested(tmp_path: Path) -> None:
    """Given approved state whose called_at is older than TURN_STALE_SECONDS,
    When the gate runs, Then it blocks: exit 2 AND nested permissionDecision=deny
    — the staleness predicate (>120s) is unchanged."""
    stale = datetime.now(tz=UTC) - timedelta(seconds=TURN_STALE_SECONDS + 60)
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {"approved": True, "persona": "forge-ui", "called_at": stale.isoformat()},
    )
    result = _run(state_path)
    reason = _assert_nested_deny(result)
    assert "is stale" in reason, (
        f"Reason must name the staleness cause unchanged, got: {reason!r}"
    )


def test_fresh_approved_state_allows_exit0(tmp_path: Path) -> None:
    """Given approved state with a fresh called_at (well within the turn
    window) AND a fresh notepad_logged_at, When the gate runs, Then it ALLOWS:
    exit 0 with no deny output — the allow predicate is unchanged.

    notepad_logged_at must be present for standard-tier code-writing dispatches
    (P2-07); the hook blocks if it is absent or stale."""
    fresh = datetime.now(tz=UTC) - timedelta(seconds=5)
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {
            "approved": True,
            "persona": "forge-ui",
            "called_at": fresh.isoformat(),
            "notepad_logged_at": fresh.isoformat(),
        },
    )
    # Point the planning-gate DB lookup at a non-existent path so the check
    # exits warn-and-allow (None path) rather than querying the live project.db.
    # This keeps the test hermetic against the host repo's planning-gate history.
    result = _run(state_path, db_path=tmp_path / "no_project.db")
    assert result.returncode == 0, (
        f"fresh approved state must allow (exit 0), got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    assert "permissionDecision" not in _hook_specific(result.stdout), (
        f"allow path must not emit a deny, got: {result.stdout!r}"
    )


def test_approved_brief_task_tier_simple_exempts_planning_gate(tmp_path: Path) -> None:
    """TASK-083 single-source: Given approved state whose persisted
    approved_brief carries task_tier='simple' AND the dispatch payload has NO
    prompt JSON block, When the gate runs, Then it ALLOWS (exit 0) — the
    planning-gate (and notepad) requirements scope to standard/complex tiers, so
    a simple-tier brief resolved straight from broker_state.approved_brief is
    exempt without any prompt-embedded brief.

    This proves _resolve_gate_fields reads task_tier from broker_state FIRST: the
    payload supplies only subagent_type (no prompt block), so the only source of
    task_tier='simple' is the persisted approved_brief. Without single-sourcing,
    task_tier would default to 'standard' and the notepad/planning gate would
    fire."""
    fresh = datetime.now(tz=UTC) - timedelta(seconds=5)
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {
            "approved": True,
            "persona": "forge-ui",
            "called_at": fresh.isoformat(),
            "approved_brief": {"task_tier": "simple", "intent": "implement_ui"},
        },
    )
    result = _run(state_path, db_path=tmp_path / "no_project.db")
    assert result.returncode == 0, (
        f"simple-tier approved_brief must allow without a prompt block (exit 0), "
        f"got {result.returncode}: {result.stdout!r} / {result.stderr!r}"
    )
    assert "permissionDecision" not in _hook_specific(result.stdout), (
        f"simple-tier dispatch must not emit a deny, got: {result.stdout!r}"
    )


def test_approved_brief_task_tier_standard_still_gates_notepad(tmp_path: Path) -> None:
    """TASK-083 back-stop: Given approved state whose approved_brief carries
    task_tier='standard' AND NO notepad_logged_at AND NO prompt block, When the
    gate runs, Then it BLOCKS (exit 2) on the absent notepad — single-sourcing
    task_tier from broker_state must still ARM the standard-tier gates, not just
    relax them. The tier resolves to 'standard' from approved_brief, so the
    notepad load-bearing check (P2-07) fires exactly as a prompt-supplied
    standard tier would."""
    fresh = datetime.now(tz=UTC) - timedelta(seconds=5)
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {
            "approved": True,
            "persona": "forge-ui",
            "called_at": fresh.isoformat(),
            "approved_brief": {"task_tier": "standard"},
        },
    )
    result = _run(state_path, db_path=tmp_path / "no_project.db")
    reason = _assert_nested_deny(result)
    assert "notepad_logged_at is absent" in reason, (
        f"standard-tier from approved_brief must arm the notepad gate, got: {reason!r}"
    )


def test_prompt_block_overrides_when_state_brief_absent(tmp_path: Path) -> None:
    """TASK-083 back-compat: Given approved state with NO approved_brief but a
    prompt JSON block carrying task_tier='simple', When the gate runs, Then it
    ALLOWS (exit 0) — _resolve_gate_fields falls back per-field to the
    prompt-JSON values when broker_state lacks approved_brief, so existing
    prompt-embedded briefs keep working unchanged."""
    fresh = datetime.now(tz=UTC) - timedelta(seconds=5)
    state_path = _write_state(
        tmp_path / "broker_state.json",
        {
            "approved": True,
            "persona": "forge-ui",
            "called_at": fresh.isoformat(),
        },
    )
    env = dict(os.environ)
    env.pop("NEXUS_BROKER_STATE_PATH", None)
    env.pop("_HOOK_DB_PATH", None)
    env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    env["_HOOK_DB_PATH"] = str(tmp_path / "no_project.db")
    prompt = "Do the work\n```json\n" + json.dumps({"task_tier": "simple"}) + "\n```\n"
    result = subprocess.run(
        ["python3", str(HOOK_FILE)],
        input=json.dumps({
            "tool_name": "Task",
            "subagent_type": "forge-ui",
            "tool_input": {"subagent_type": "forge-ui", "prompt": prompt},
        }),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"prompt-block simple tier must allow when state has no approved_brief "
        f"(exit 0), got {result.returncode}: {result.stdout!r} / {result.stderr!r}"
    )
    assert "permissionDecision" not in _hook_specific(result.stdout), (
        f"prompt-block simple-tier dispatch must not emit a deny, got: {result.stdout!r}"
    )


def test_missing_state_fails_closed_exit2(tmp_path: Path) -> None:
    """Fail-CLOSED (P2-10): a missing broker_state.json BLOCKS the Task (exit 2)
    so a down broker is LOUD and never silently bypassed.  The deny reason names
    the file-not-found cause.  Set NEXUS_BROKER_ALLOW_DEGRADED=1 to opt out."""
    result = _run(tmp_path / "does_not_exist.json")
    assert result.returncode == 2, (
        f"missing state must fail closed (exit 2), got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    ho = _hook_specific(result.stdout)
    assert ho.get("permissionDecision") == "deny", (
        f"missing state must emit a deny, got: {result.stdout!r}"
    )


def test_malformed_state_fails_closed_exit2(tmp_path: Path) -> None:
    """Fail-CLOSED (P2-10): a malformed broker_state.json BLOCKS the Task (exit 2)
    so a corrupt state file is LOUD and never silently bypassed.  The deny reason
    names the parse-error cause."""
    bad = tmp_path / "broker_state.json"
    bad.write_text("{ this is not json")
    result = _run(bad)
    assert result.returncode == 2, (
        f"malformed state must fail closed (exit 2), got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    ho = _hook_specific(result.stdout)
    assert ho.get("permissionDecision") == "deny", (
        f"malformed state must emit a deny, got: {result.stdout!r}"
    )

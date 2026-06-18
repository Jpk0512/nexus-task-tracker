"""Phase 3 — ADVISORY pre-dispatch decomposition forcing-function.

`broker.server.nexus_validate_brief` now appends ONE advisory warning when a
session has accumulated >= NEXUS_DECOMP_NUDGE_THRESHOLD (default 3) CONSECUTIVE
single-agent dispatches with no Workflow/fanout. The nudge is STRICTLY advisory:
it may NEVER flip `approved` from true to false, it is suppressed for read-only /
recon personas, and it is suppressed when the brief declares the work serial via
the optional `decomposition` field.

These tests drive the REAL async validator (not a re-implementation) and pin:
  (a) nudge FIRES at threshold for a work persona,
  (b) NO nudge below threshold,
  (c) NO nudge for a read-only persona (scout),
  (d) nudge SUPPRESSED when `decomposition` is declared,
  (e) approved stays True in ALL cases — asserted as the POSITIVE invariant.

Plus a direct unit test of `_consecutive_single_dispatches` against a temp JSONL
(tail-run-since-last-fanout semantics + fail-open).

Hermeticity mirrors test_validate_brief.py: read_state/write_state/
log_broker_validation are neutralized via the captured_state fixture, and the
dispatch-count helper is monkeypatched per-test so the suite never reads the live
router_dispatches.jsonl.
"""
from __future__ import annotations

import datetime
import json
from typing import Any

import pytest

import broker.server as srv


def _fresh_ts() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat()


def _well_formed_brief(**overrides: Any) -> dict[str, Any]:
    """A CONTRACT-complete brief the validator ACCEPTS (fresh notepad)."""
    brief: dict[str, Any] = {
        "goal": "Implement the next slice of the pipeline",
        "context_files": ["src/broker/server.py"],
        "acceptance_criteria": ["slice landed", "tests green"],
        "verification_required": ["uv run pytest -q"],
        "do_not_touch": ["pyproject.toml"],
        "notepad_topic": "pipeline-slice",
        "task_tier": "standard",
        "skills_required": ["pipeline-data-conventions"],
    }
    brief.update(overrides)
    return brief


@pytest.fixture
def captured_state(monkeypatch) -> dict[str, Any]:
    """Neutralize side effects; fresh notepad so the ritual is satisfied."""
    box: dict[str, Any] = {"written": None}
    monkeypatch.setattr(srv, "read_state", lambda: {"notepad_logged_at": _fresh_ts()})

    def _capture_write(state: Any) -> None:
        box["written"] = state

    monkeypatch.setattr(srv, "write_state", _capture_write)
    monkeypatch.setattr(srv, "log_broker_validation", lambda **kwargs: None)
    return box


def _set_count(monkeypatch, n: int) -> None:
    """Force the consecutive-single-dispatch count the validator sees."""
    monkeypatch.setattr(srv, "_consecutive_single_dispatches", lambda: n)


async def _validate(
    brief: dict[str, Any],
    *,
    persona: str = "pipeline-data",
    intent: str = "implement_ingestion",
    turn_id: str = "turn-decomp",
    **kwargs: Any,
) -> srv.BrokerResult:
    return await srv.nexus_validate_brief(
        persona=persona,
        intent=intent,
        brief_json=json.dumps(brief),
        turn_id=turn_id,
        **kwargs,
    )


def _has_decomp_nudge(result: srv.BrokerResult) -> bool:
    return any("[decomposition]" in w for w in result["warnings"])


# (a) nudge FIRES at threshold for a work persona ─────────────────────────────
async def test_nudge_fires_at_threshold_for_work_persona(monkeypatch, captured_state):
    _set_count(monkeypatch, 3)
    result = await _validate(_well_formed_brief())
    assert _has_decomp_nudge(result), result["warnings"]
    assert result["approved"] is True  # POSITIVE invariant


async def test_nudge_fires_above_threshold(monkeypatch, captured_state):
    _set_count(monkeypatch, 7)
    result = await _validate(_well_formed_brief())
    assert _has_decomp_nudge(result), result["warnings"]
    # The escalating count is surfaced verbatim.
    assert any("7th consecutive" in w for w in result["warnings"])
    assert result["approved"] is True


# (b) NO nudge below threshold ────────────────────────────────────────────────
async def test_no_nudge_below_threshold(monkeypatch, captured_state):
    _set_count(monkeypatch, 2)
    result = await _validate(_well_formed_brief())
    assert not _has_decomp_nudge(result), result["warnings"]
    assert result["approved"] is True


# (c) NO nudge for a read-only / recon persona (scout) ────────────────────────
async def test_no_nudge_for_readonly_persona_scout(monkeypatch, captured_state):
    _set_count(monkeypatch, 9)
    result = await _validate(
        _well_formed_brief(), persona="scout", intent="investigate"
    )
    assert not _has_decomp_nudge(result), result["warnings"]
    assert result["approved"] is True


async def test_no_nudge_for_readonly_persona_lens(monkeypatch, captured_state):
    _set_count(monkeypatch, 9)
    result = await _validate(_well_formed_brief(), persona="lens", intent="validate")
    assert not _has_decomp_nudge(result)
    assert result["approved"] is True


# (d) nudge SUPPRESSED when decomposition declared ────────────────────────────
async def test_nudge_suppressed_by_serial_justification(monkeypatch, captured_state):
    _set_count(monkeypatch, 5)
    brief = _well_formed_brief(
        decomposition={
            "independent_units": 4,
            "serial_justification": "each step strictly depends on the prior write",
        }
    )
    result = await _validate(brief)
    assert not _has_decomp_nudge(result), result["warnings"]
    assert result["approved"] is True


async def test_nudge_suppressed_by_single_independent_unit(monkeypatch, captured_state):
    _set_count(monkeypatch, 5)
    brief = _well_formed_brief(decomposition={"independent_units": 1})
    result = await _validate(brief)
    assert not _has_decomp_nudge(result)
    assert result["approved"] is True


async def test_decomposition_absence_never_blocks(monkeypatch, captured_state):
    """The OPTIONAL decomposition field's ABSENCE must never error or block."""
    _set_count(monkeypatch, 0)
    brief = _well_formed_brief()
    assert "decomposition" not in brief
    result = await _validate(brief)
    assert result["approved"] is True
    assert result["errors"] == []


async def test_malformed_decomposition_does_not_suppress_or_error(
    monkeypatch, captured_state
):
    """A garbage decomposition shape fails open (no suppression, no error)."""
    _set_count(monkeypatch, 4)
    brief = _well_formed_brief(decomposition="not-a-dict")
    result = await _validate(brief)
    assert _has_decomp_nudge(result), result["warnings"]
    assert result["approved"] is True
    assert result["errors"] == []


# (e) approved stays True in ALL cases — explicit POSITIVE-invariant sweep ─────
@pytest.mark.parametrize("count", [0, 2, 3, 5, 50])
async def test_approved_stays_true_regardless_of_count(
    monkeypatch, captured_state, count
):
    _set_count(monkeypatch, count)
    result = await _validate(_well_formed_brief())
    assert result["approved"] is True
    assert result["errors"] == []
    assert result["approved_brief"] is not None


async def test_exempt_persona_never_calls_count_helper(monkeypatch, captured_state):
    """An exempt persona short-circuits BEFORE the dispatch-log read.

    The validator's nudge branch is guarded by the exempt-persona check FIRST, so
    a read-only persona must approve without ever touching the count helper — if it
    did, this booby-trapped helper would surface.
    """

    def _boom() -> int:
        raise RuntimeError("count helper must not be called for exempt personas")

    monkeypatch.setattr(srv, "_consecutive_single_dispatches", _boom)
    result = await _validate(
        _well_formed_brief(), persona="scout", intent="investigate"
    )
    assert result["approved"] is True


# ── direct unit tests of the helper (tail-run-since-last-fanout) ──────────────
def _write_log(tmp_path, rows: list[dict[str, Any]]):
    p = tmp_path / "router_dispatches.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_helper_counts_tail_run_since_last_fanout(monkeypatch, tmp_path):
    files_dir = tmp_path / ".memory" / "files"
    files_dir.mkdir(parents=True)
    rows = [
        {"session_id": "S", "dispatch_kind": "single"},
        {"session_id": "S", "dispatch_kind": "single"},
        {"session_id": "S", "dispatch_kind": "fanout"},  # resets
        {"session_id": "S", "dispatch_kind": "single"},
        {"session_id": "S", "dispatch_kind": "single"},
        {"session_id": "S", "dispatch_kind": "single"},
    ]
    _write_log(files_dir, rows)
    monkeypatch.setattr(srv, "REPO_ROOT", tmp_path)
    assert srv._consecutive_single_dispatches() == 3


def test_helper_only_counts_current_session(monkeypatch, tmp_path):
    files_dir = tmp_path / ".memory" / "files"
    files_dir.mkdir(parents=True)
    rows = [
        {"session_id": "OLD", "dispatch_kind": "single"},
        {"session_id": "OLD", "dispatch_kind": "single"},
        {"session_id": "CUR", "dispatch_kind": "single"},
        {"session_id": "CUR", "dispatch_kind": "single"},
    ]
    _write_log(files_dir, rows)
    monkeypatch.setattr(srv, "REPO_ROOT", tmp_path)
    # last row's session is CUR → only its two singles counted.
    assert srv._consecutive_single_dispatches() == 2


def test_helper_treats_missing_kind_as_single(monkeypatch, tmp_path):
    files_dir = tmp_path / ".memory" / "files"
    files_dir.mkdir(parents=True)
    rows = [
        {"session_id": "S"},  # pre-Phase-3 row, no dispatch_kind
        {"session_id": "S", "dispatch_kind": "single"},
    ]
    _write_log(files_dir, rows)
    monkeypatch.setattr(srv, "REPO_ROOT", tmp_path)
    assert srv._consecutive_single_dispatches() == 2


def test_helper_fail_open_when_log_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "REPO_ROOT", tmp_path)  # no .memory/files at all
    assert srv._consecutive_single_dispatches() == 0


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("NEXUS_DECOMP_NUDGE_THRESHOLD", "5")
    assert srv._decomp_nudge_threshold() == 5
    monkeypatch.setenv("NEXUS_DECOMP_NUDGE_THRESHOLD", "garbage")
    assert srv._decomp_nudge_threshold() == 3
    monkeypatch.delenv("NEXUS_DECOMP_NUDGE_THRESHOLD", raising=False)
    assert srv._decomp_nudge_threshold() == 3

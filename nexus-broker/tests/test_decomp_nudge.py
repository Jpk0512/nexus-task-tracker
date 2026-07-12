"""Phase 3 — decomposition forcing-function (3-tier escalation).

`broker.server.nexus_validate_brief` implements three escalating tiers based on
CONSECUTIVE single-agent dispatches with no Workflow/fanout this session:

  Tier 1 — N >= NEXUS_DECOMP_NUDGE_THRESHOLD (default 3):
    ADVISORY warning only; approved stays True.

  Tier 2 — N >= 6 (FORCED PAUSE):
    approved=False (error added to errors list) UNLESS the brief carries a
    non-empty decomposition.serial_justification (escape hatch).

  Tier 3 — N >= 9 (HARD BLOCK):
    approved=False; escapable ONLY by decomposition.serial_override=true
    (with non-empty serial_justification), or by actually fanning out (resets
    the counter to 0).

NEVER DEADLOCK INVARIANT: there is always a forward path.

Suppressed for read-only/recon personas.

These tests drive the REAL async validator (not a re-implementation) and pin:
  (a) N=3 advisory-only-still-approved,
  (b) NO nudge below threshold,
  (c) NO nudge for a read-only persona (scout),
  (d) N=6 no-decomposition -> not approved (forced pause),
  (e) N=6 with serial_justification -> approved (escape hatch),
  (f) N=9 hard block -> not approved,
  (g) serial_override escapes N=9,
  (h) fan-out resets counter (count=0 after fanout => no nudge),
  (i) approved stays True for N<6 in ALL cases.

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


async def test_nudge_fires_above_threshold_but_below_forced_pause(monkeypatch, captured_state):
    # N=5 is above the advisory threshold (3) but below forced-pause (6):
    # advisory warning fires, approved stays True.
    _set_count(monkeypatch, 5)
    result = await _validate(_well_formed_brief())
    assert _has_decomp_nudge(result), result["warnings"]
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


# (d) N=6 forced pause — no decomposition -> not approved ────────────────────
async def test_forced_pause_at_n6_no_decomposition(monkeypatch, captured_state):
    _set_count(monkeypatch, 6)
    result = await _validate(_well_formed_brief())
    assert result["approved"] is False
    assert any("[decomposition] FORCED PAUSE" in e for e in result["errors"])


# (e) N=6 with serial_justification -> approved (escape hatch) ────────────────
async def test_forced_pause_escaped_by_serial_justification(monkeypatch, captured_state):
    _set_count(monkeypatch, 6)
    brief = _well_formed_brief(
        decomposition={
            "independent_units": 4,
            "serial_justification": "each step writes an artifact the next reads",
        }
    )
    result = await _validate(brief)
    assert result["approved"] is True
    assert not any("[decomposition] FORCED PAUSE" in e for e in result["errors"])


# (f) N=9 hard block -> not approved ─────────────────────────────────────────
async def test_hard_block_at_n9(monkeypatch, captured_state):
    _set_count(monkeypatch, 9)
    result = await _validate(_well_formed_brief())
    assert result["approved"] is False
    assert any("[decomposition] HARD BLOCK" in e for e in result["errors"])


# (g) serial_override escapes N=9 ─────────────────────────────────────────────
async def test_serial_override_escapes_hard_block(monkeypatch, captured_state):
    _set_count(monkeypatch, 9)
    brief = _well_formed_brief(
        decomposition={
            "serial_override": True,
            "serial_justification": "single write-locked migration; cannot parallelize",
        }
    )
    result = await _validate(brief)
    assert result["approved"] is True
    assert not any("[decomposition] HARD BLOCK" in e for e in result["errors"])


async def test_serial_override_without_justification_still_blocks(monkeypatch, captured_state):
    # serial_override=True requires a non-empty justification to escape.
    _set_count(monkeypatch, 9)
    brief = _well_formed_brief(
        decomposition={
            "serial_override": True,
            # no serial_justification provided
        }
    )
    result = await _validate(brief)
    assert result["approved"] is False
    assert any("[decomposition] HARD BLOCK" in e for e in result["errors"])


# (h) fan-out resets counter (N=0 after fanout => no block) ───────────────────
async def test_fanout_resets_counter_no_block(monkeypatch, captured_state):
    # A fan-out dispatch writes dispatch_kind=fanout which resets the tail-run
    # counter to 0.  _consecutive_single_dispatches() returning 0 means no tier
    # fires at all — approved stays True with no decomp errors.
    _set_count(monkeypatch, 0)
    result = await _validate(_well_formed_brief())
    assert result["approved"] is True
    assert result["errors"] == []
    assert not _has_decomp_nudge(result)


# (i) approved stays True for N<6 in ALL cases ────────────────────────────────
@pytest.mark.parametrize("count", [0, 2, 3, 5])
async def test_approved_stays_true_below_forced_pause(
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


# ── Width-disjoint trigger unit tests (_width_disjoint_trigger) ───────────────

def _wide_brief(**overrides: Any) -> dict[str, Any]:
    """Brief with >=4 context_files and no_read_after_write=True (base case)."""
    b = _well_formed_brief(
        context_files=["a.py", "b.py", "c.py", "d.py"],
        decomposition={"no_read_after_write": True},
    )
    b.update(overrides)
    return b


def test_width_trigger_fires_on_4_context_files():
    brief = _wide_brief()
    result = srv._width_disjoint_trigger(brief)
    assert result is not None
    assert result.startswith("[decomposition]")
    assert "no_read_after_write" in result


def test_width_trigger_fires_on_files_touched_estimate():
    # When files_touched_estimate is present it takes precedence over context_files.
    brief = _wide_brief(
        context_files=["a.py"],  # only 1 — would not trigger alone
        files_touched_estimate=5,
    )
    result = srv._width_disjoint_trigger(brief)
    assert result is not None
    assert "5" in result


def test_width_trigger_silent_below_threshold():
    brief = _wide_brief(
        context_files=["a.py", "b.py", "c.py"],  # 3, below threshold of 4
    )
    result = srv._width_disjoint_trigger(brief)
    assert result is None


def test_width_trigger_silent_without_signal():
    # no_read_after_write absent — no trigger even with many files.
    brief = _well_formed_brief(
        context_files=["a.py", "b.py", "c.py", "d.py"],
        decomposition={"independent_units": 4},  # no no_read_after_write key
    )
    result = srv._width_disjoint_trigger(brief)
    assert result is None


def test_width_trigger_silent_when_signal_is_false():
    # Explicit False must not trigger.
    brief = _wide_brief(decomposition={"no_read_after_write": False})
    result = srv._width_disjoint_trigger(brief)
    assert result is None


def test_width_trigger_silent_when_signal_is_truthy_not_true():
    # Signal must be literal True, not truthy (1, "yes", etc.).
    for truthy in (1, "true", "yes", [True]):
        brief = _wide_brief(decomposition={"no_read_after_write": truthy})
        assert srv._width_disjoint_trigger(brief) is None, f"failed for signal={truthy!r}"


def test_width_trigger_silent_when_no_decomposition():
    brief = _well_formed_brief(context_files=["a.py", "b.py", "c.py", "d.py"])
    result = srv._width_disjoint_trigger(brief)
    assert result is None


def test_width_trigger_silent_when_decomposition_not_dict():
    brief = _well_formed_brief(
        context_files=["a.py", "b.py", "c.py", "d.py"],
        decomposition="no_read_after_write=True",
    )
    result = srv._width_disjoint_trigger(brief)
    assert result is None


# Integration: width trigger fires through the full validator ─────────────────

async def test_width_trigger_fires_in_validator(monkeypatch, captured_state):
    # Consecutive count below serial nudge threshold — only width advisory fires.
    _set_count(monkeypatch, 0)
    brief = _wide_brief()
    result = await _validate(brief)
    assert any("[decomposition]" in w and "no_read_after_write" in w for w in result["warnings"])
    assert result["approved"] is True


async def test_width_trigger_suppressed_for_exempt_persona(monkeypatch, captured_state):
    _set_count(monkeypatch, 0)
    brief = _wide_brief()
    result = await _validate(brief, persona="scout", intent="investigate")
    assert not any("no_read_after_write" in w for w in result["warnings"])
    assert result["approved"] is True


async def test_width_trigger_suppressed_by_serial_justification(monkeypatch, captured_state):
    _set_count(monkeypatch, 0)
    brief = _wide_brief(
        decomposition={
            "no_read_after_write": True,
            "serial_justification": "step 3 reads the output of step 2",
        }
    )
    result = await _validate(brief)
    assert not any("no_read_after_write" in w for w in result["warnings"])
    assert result["approved"] is True


async def test_width_trigger_never_flips_approved(monkeypatch, captured_state):
    # POSITIVE invariant: even with a wide disjoint brief, approved stays True.
    _set_count(monkeypatch, 0)
    brief = _wide_brief(files_touched_estimate=20)
    result = await _validate(brief)
    assert result["approved"] is True
    assert result["errors"] == []

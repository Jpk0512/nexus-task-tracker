"""R3-T02/N05 — measured prepare+run ritual wall-clock, replacing the 120s framing.

N04 recon (scout-reports/r3-t02/r3-t02-broker-map.md) established there is no
literal sleep(120) anywhere in the broker — TURN_STALE_SECONDS is a staleness
THRESHOLD, and the measured per-dispatch ritual cost (nexus_validate_brief
round-trip) is ~13ms. This test proves the SAME order-of-magnitude holds for
the new nexus_prepare + nexus_run groundwork pair, well under the <=5s N05
acceptance target — by measurement, not by removing a sleep that never existed.
"""
from __future__ import annotations

import time
from pathlib import Path

import broker.state as state_mod

RITUAL_BUDGET_SECONDS = 5.0


def _patch_state_path(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "broker_state.json"
    monkeypatch.setattr(state_mod, "STATE_PATH", target)
    return target


def test_prepare_then_run_ritual_under_budget(tmp_path: Path, monkeypatch) -> None:
    _patch_state_path(monkeypatch, tmp_path)
    from broker.discovery import nexus_prepare_impl, nexus_run_impl

    start = time.perf_counter()
    prepared = nexus_prepare_impl(persona="scout", intent="investigate", turn_id="latency-turn")
    ran = nexus_run_impl(turn_id="latency-turn")
    elapsed = time.perf_counter() - start

    assert prepared["ok"] is True
    assert ran["ok"] is True
    assert elapsed <= RITUAL_BUDGET_SECONDS, (
        f"prepare+run ritual took {elapsed:.3f}s, budget is {RITUAL_BUDGET_SECONDS}s"
    )


def test_prepare_then_run_ritual_well_under_budget(tmp_path: Path, monkeypatch) -> None:
    """Tighter bound matching N04's ledger order-of-magnitude (~13-40ms per call,
    not just the acceptance-criteria 5s ceiling) — this is the number that
    justifies calling the ritual 'fast', not merely 'in budget'."""
    _patch_state_path(monkeypatch, tmp_path)
    from broker.discovery import nexus_prepare_impl, nexus_run_impl

    start = time.perf_counter()
    nexus_prepare_impl(persona="scout", intent="investigate", turn_id="latency-turn-2")
    nexus_run_impl(turn_id="latency-turn-2")
    elapsed = time.perf_counter() - start

    # Generous multiple of N04's ~13-40ms ledger figure to absorb CI jitter/disk
    # I/O variance, while still being two orders of magnitude below the 5s target.
    assert elapsed <= 0.5, (
        f"prepare+run took {elapsed * 1000:.1f}ms — expected roughly N04's "
        "13-40ms ledger order of magnitude, not seconds"
    )


def test_no_sleep_anywhere_in_discovery_module() -> None:
    """Deterministic companion to the grep in N05's verification_method: the
    module source contains no time.sleep call at all (not just no sleep(120))."""
    src = (
        Path(__file__).resolve().parents[1] / "src" / "broker" / "discovery.py"
    ).read_text()
    assert "sleep(" not in src, "discovery.py must not contain any sleep() call"

"""Conformance health-tier tests (TASK-106).

check_conformance_stack_profile re-runs detect-vs-groundtruth on an
ALREADY-INSTALLED project: it FAILs when the committed persona_set no longer
covers what detect_stack would now produce (the insites under-install class —
frontend present but forge-ui never installed).

Every assertion pins a POSITIVE invariant: the check RETURNS the right severity
for the scenario, and the FAIL message NAMES the missing persona. The detector
import is MOCKED (monkeypatching health._load_detect_stack) so the test fully
controls detect_stack's output — it never imports stack_profile from
nexus-package nor reads any nexus-package path, so this file does NOT belong in
PLEXUS_SELF_TESTS.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections.abc import Callable
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load health.py from the LIVE .memory/ (mirrors test_health_tier_rendering).
# ---------------------------------------------------------------------------

_MEMORY_DIR = Path(__file__).resolve().parents[2] / ".memory"


def _load_health() -> types.ModuleType:
    mod_name = "health_live"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _MEMORY_DIR / "health.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


health = _load_health()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_stack(project: Path, profile: dict) -> None:
    """Write a committed .memory/nexus-stack.json (the GROUND TRUTH)."""
    mem = project / ".memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "nexus-stack.json").write_text(json.dumps(profile), encoding="utf-8")


def _patch_detector(
    monkeypatch: pytest.MonkeyPatch, detected: dict | None
) -> None:
    """Force health._load_detect_stack to yield a controlled detect_stack.

    ``detected=None`` simulates the bare-target case where the installer-side
    detector is unavailable (the guarded-import skip path).
    """
    if detected is None:
        monkeypatch.setattr(health, "_load_detect_stack", lambda: None)
        return

    def _fake_detect(_root: Path) -> dict:
        return detected

    monkeypatch.setattr(
        health,
        "_load_detect_stack",
        lambda: _fake_detect,
    )


def _by_name(results: list, name: str) -> list:
    return [r for r in results if r.name == name]


# ---------------------------------------------------------------------------
# PASS: committed roster covers detected roster, prefixes aligned.
# ---------------------------------------------------------------------------


def test_pass_when_roster_covers_and_prefixes_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Committed roster covers detected EXACTLY (no missing, no extra), prefixes
    # identical, and all framework/db fields agree -> the only result is PASS.
    profile = {
        "persona_set": ["nexus-orchestrator", "forge-ui", "atlas"],
        "socraticode_watched_prefixes": ["/app/", "/ingestion/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "none"},
        "data": {"db": "duckdb"},
    }
    _write_stack(tmp_path, profile)
    _patch_detector(monkeypatch, dict(profile))

    results = health.check_conformance_stack_profile(str(tmp_path))

    # POSITIVE invariant: exactly one PASS, no FAIL/WARN — the roster is conformant.
    assert len(results) == 1
    assert results[0].severity == "PASS"
    assert results[0].name == "conformance.stack_profile"


# ---------------------------------------------------------------------------
# FAIL: detected requires a persona the committed roster is missing (insites).
# ---------------------------------------------------------------------------


def test_fail_when_detected_persona_missing_from_committed_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # insites case: a frontend is now detected, requiring forge-ui, but the
    # committed roster (a stale backend-only install) never installed it.
    committed = {
        "persona_set": ["nexus-orchestrator", "forge-wire"],
        "socraticode_watched_prefixes": ["/app/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "fastapi"},
        "data": {"db": "none"},
    }
    detected = {
        "persona_set": ["nexus-orchestrator", "forge-wire", "forge-ui"],
        "socraticode_watched_prefixes": ["/app/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "fastapi"},
        "data": {"db": "none"},
    }
    _write_stack(tmp_path, committed)
    _patch_detector(monkeypatch, detected)

    results = health.check_conformance_stack_profile(str(tmp_path))

    # POSITIVE invariant: a FAIL is emitted AND it NAMES the missing persona.
    fails = [r for r in results if r.severity == "FAIL"]
    assert len(fails) == 1, f"expected one FAIL, got {results}"
    assert "forge-ui" in fails[0].message
    assert fails[0].name == "conformance.stack_profile"


def test_extra_installed_persona_is_info_never_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Committed roster has MORE than detected — stacks legitimately shrink; this
    # must NEVER FAIL (at most INFO).
    committed = {
        "persona_set": ["nexus-orchestrator", "forge-ui", "atlas"],
        "socraticode_watched_prefixes": ["/app/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "none"},
        "data": {"db": "none"},
    }
    detected = {
        "persona_set": ["nexus-orchestrator", "forge-ui"],
        "socraticode_watched_prefixes": ["/app/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "none"},
        "data": {"db": "none"},
    }
    _write_stack(tmp_path, committed)
    _patch_detector(monkeypatch, detected)

    results = health.check_conformance_stack_profile(str(tmp_path))

    # POSITIVE invariant: no FAIL, and the extra persona is surfaced as INFO.
    assert not [r for r in results if r.severity == "FAIL"]
    infos = [r for r in results if r.severity == "INFO"]
    assert any("atlas" in r.message for r in infos)


# ---------------------------------------------------------------------------
# WARN: watched_prefixes jaccard < 0.5 (code-layout drift).
# ---------------------------------------------------------------------------


def test_warn_when_watched_prefixes_jaccard_below_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = {
        "persona_set": ["nexus-orchestrator", "forge-ui"],
        "socraticode_watched_prefixes": ["/app/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "none"},
        "data": {"db": "none"},
    }
    detected = {
        # roster still covered (no FAIL) but prefixes diverge: intersection {} ,
        # union {/app/, /ingestion/, /workers/} -> jaccard 0.0 < 0.5.
        "persona_set": ["nexus-orchestrator", "forge-ui"],
        "socraticode_watched_prefixes": ["/ingestion/", "/workers/"],
        "frontend": {"framework": "next"},
        "backend": {"framework": "none"},
        "data": {"db": "none"},
    }
    _write_stack(tmp_path, committed)
    _patch_detector(monkeypatch, detected)

    results = health.check_conformance_stack_profile(str(tmp_path))

    # POSITIVE invariant: prefix drift produces a WARN, and never a FAIL.
    warns = [r for r in results if r.severity == "WARN"]
    assert any("watched_prefixes" in r.message for r in warns)
    assert not [r for r in results if r.severity == "FAIL"]


# ---------------------------------------------------------------------------
# SKIP / non-FAIL: detector unavailable, and nexus-stack.json absent.
# ---------------------------------------------------------------------------


def test_skip_when_detector_import_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a bare target: the installer-side detector cannot be imported.
    _write_stack(tmp_path, {"persona_set": ["nexus-orchestrator"]})
    _patch_detector(monkeypatch, None)

    results = health.check_conformance_stack_profile(str(tmp_path))

    # POSITIVE invariant: a single non-FAIL SKIP, with the target-safe message.
    assert len(results) == 1
    assert results[0].severity == "SKIP"
    assert results[0].severity != "FAIL"
    assert "detector unavailable" in results[0].message


def test_warn_not_fail_when_nexus_stack_json_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Detector available, but project predates profile-aware install.
    detected = {
        "persona_set": ["nexus-orchestrator", "forge-ui"],
        "socraticode_watched_prefixes": ["/app/"],
    }
    _patch_detector(monkeypatch, detected)
    # no _write_stack -> .memory/nexus-stack.json absent

    results = health.check_conformance_stack_profile(str(tmp_path))

    # POSITIVE invariant: a single WARN naming the predates-profile cause, NOT FAIL.
    assert len(results) == 1
    assert results[0].severity == "WARN"
    assert results[0].severity != "FAIL"
    assert "nexus-stack.json" in results[0].message


def test_force_no_detector_env_drives_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real _load_detect_stack honours NEXUS_HEALTH_FORCE_NO_DETECTOR — exercise
    # the guarded-import seam end-to-end (no monkeypatch of the loader itself).
    _write_stack(tmp_path, {"persona_set": ["nexus-orchestrator"]})
    monkeypatch.setenv("NEXUS_HEALTH_FORCE_NO_DETECTOR", "1")

    results = health.check_conformance_stack_profile(str(tmp_path))

    assert len(results) == 1
    assert results[0].severity == "SKIP"


# ---------------------------------------------------------------------------
# Registration: the check actually runs under the DRIFT tier (--drift).
# ---------------------------------------------------------------------------


def test_conformance_registered_in_drift_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # POSITIVE invariant: run_checks(drift=True) actually emits a
    # conformance.stack_profile result (the check is wired into drift_checks),
    # and run_checks(drift=False) does NOT.
    monkeypatch.setattr(health, "_load_detect_stack", lambda: None)

    def _stub(_path: str) -> list:
        return []

    # Neutralise the other (filesystem/registry-touching) checks so the run is
    # hermetic on tmp_path; we only care that conformance fires under drift.
    for fn_name in (
        "check_agents_canonical_inventory",
        "check_agents_skill_declarations",
        "check_hooks_settings_resolves",
        "check_hooks_executable",
        "check_mcp_config_valid",
        "check_version_matches_registry",
        "check_ledger_present_and_consistent",
        "check_schema_vec_dim_aligned",
        "check_vec_memory_available",
        "check_core_tables_present",
        "check_broker_static_structure",
        "check_leaks_prior_project",
        "check_heartbeat_recent",
        "check_router_recent_decisions",
        "check_session_has_open",
        "check_drift_agents",
        "check_drift_hooks",
        "check_drift_skills",
    ):
        monkeypatch.setattr(health, fn_name, _stub)

    with_drift = health.run_checks(
        str(tmp_path), runtime=False, drift=True, leak_check=False
    )
    without_drift = health.run_checks(
        str(tmp_path), runtime=False, drift=False, leak_check=False
    )

    names_with = {r.name for r in with_drift.results}
    names_without = {r.name for r in without_drift.results}
    assert "conformance.stack_profile" in names_with
    assert "conformance.stack_profile" not in names_without


def test_conformance_callable_signature() -> None:
    # Guard the public contract: callable taking project_path -> list[CheckResult].
    fn: Callable[[str], list] = health.check_conformance_stack_profile
    assert callable(fn)

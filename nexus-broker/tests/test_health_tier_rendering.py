"""Regression tests for health.py tier rendering correctness (C4-health fixes).

Assertions:
  - Every emitted check name maps to exactly ONE tier (no orphans, no duplicates).
  - core_tables.present renders (is assigned to STATIC tier).
  - mcp_boot.servers renders under RUNTIME tier (not STATIC).
  - mcp.config_valid renders under STATIC tier.
  - Forcing a core_tables FAIL shows it in the ASCII table (visible, not hidden).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import health from the LIVE .memory/ tree
# ---------------------------------------------------------------------------

_MEMORY_DIR = Path(__file__).resolve().parents[2] / ".memory"


def _load_health() -> types.ModuleType:
    """Import health from the live .memory/ without bytecode side-effects.

    Registers the module under its full name in sys.modules so that dataclass
    introspection (which reads sys.modules[cls.__module__]) works correctly.
    """
    mod_name = "health_live"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name, _MEMORY_DIR / "health.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register BEFORE exec so dataclass __module__ resolves
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


health = _load_health()

# ---------------------------------------------------------------------------
# Tier map: mirrors the tier_order defined in _render_ascii / _render_rich
# (single source of expected grouping — test must match the actual definition).
# ---------------------------------------------------------------------------

_TIER_ORDER: list[tuple[str, list[str]]] = [
    ("STATIC", ["agents", "hooks", "mcp.", "version", "ledger", "schema", "leaks", "broker.static", "core_tables"]),
    ("RUNTIME", ["broker.mcp", "db", "embeddings", "prism", "mcp_boot"]),
    ("SESSION", ["heartbeat", "router", "session"]),
    ("DRIFT", ["drift"]),
]


def _assign_tier(name: str) -> str | None:
    """Return the tier label for a check name, or None if no tier matches."""
    for tier_label, prefixes in _TIER_ORDER:
        if any(name.startswith(p) for p in prefixes):
            return tier_label
    return None


# ---------------------------------------------------------------------------
# Helpers: build a minimal HealthReport from a list of (name, severity)
# ---------------------------------------------------------------------------

def _make_report(checks: list[tuple[str, str]]) -> health.HealthReport:
    results = [
        health.CheckResult(name=n, severity=s, message="test")
        for n, s in checks
    ]
    return health.HealthReport(
        project_path="/tmp/test",
        version="0.0.0",
        session_id="",
        elapsed=0.0,
        results=results,
    )


# ---------------------------------------------------------------------------
# Real check names emitted by run_checks (static tier only, no runtime/drift)
# These are the names the actual check functions emit.
# ---------------------------------------------------------------------------

_KNOWN_STATIC_CHECK_NAMES: list[str] = [
    "agents.canonical_inventory",
    "agents.skill_declarations",
    "hooks.settings_resolves",
    "hooks.executable",
    "mcp.config_valid",
    "version.matches_registry",
    "ledger.present_and_consistent",
    "schema.vec_dim_aligned",
    "schema.vec_available",
    "core_tables.present",
    "broker.static_structure",
    "leaks.prior_project",
]

_KNOWN_RUNTIME_CHECK_NAMES: list[str] = [
    "broker.mcp_boots",
    "hooks.execute_with_stub",
    "db.write_probe",
    "embeddings.endpoint_reachable",
    "prism.mcp_boots",
    "mcp_boot.servers",
]

_KNOWN_SESSION_CHECK_NAMES: list[str] = [
    "heartbeat.recent",
    "router.recent_decisions",
    "session.has_open",
]

_KNOWN_DRIFT_CHECK_NAMES: list[str] = [
    "drift.agents",
    "drift.hooks",
    "drift.skills",
]

_ALL_KNOWN_NAMES = (
    _KNOWN_STATIC_CHECK_NAMES
    + _KNOWN_RUNTIME_CHECK_NAMES
    + _KNOWN_SESSION_CHECK_NAMES
    + _KNOWN_DRIFT_CHECK_NAMES
)


# ---------------------------------------------------------------------------
# Test: every known check name maps to exactly one tier
# ---------------------------------------------------------------------------


def test_every_check_name_maps_to_exactly_one_tier() -> None:
    """No check name is an orphan (no tier) or double-bucketed (two tiers)."""
    orphans: list[str] = []
    doubles: list[tuple[str, list[str]]] = []

    for name in _ALL_KNOWN_NAMES:
        matched: list[str] = []
        for tier_label, prefixes in _TIER_ORDER:
            if any(name.startswith(p) for p in prefixes):
                matched.append(tier_label)
        if len(matched) == 0:
            orphans.append(name)
        elif len(matched) > 1:
            doubles.append((name, matched))

    assert not orphans, (
        f"Orphaned check names (no tier assigned): {orphans}\n"
        "Add their prefix to the correct tier in _TIER_ORDER in health.py."
    )
    assert not doubles, (
        f"Double-bucketed check names (two tiers): {doubles}\n"
        "Fix the prefix list so names only match one tier."
    )


# ---------------------------------------------------------------------------
# Test: core_tables.present is in STATIC tier
# ---------------------------------------------------------------------------


def test_core_tables_present_in_static_tier() -> None:
    tier = _assign_tier("core_tables.present")
    assert tier == "STATIC", (
        f"core_tables.present should be in STATIC but got: {tier!r}.\n"
        "Add 'core_tables' to the STATIC prefix list in both renderers."
    )


# ---------------------------------------------------------------------------
# Test: mcp_boot.servers is in RUNTIME (not STATIC)
# ---------------------------------------------------------------------------


def test_mcp_boot_servers_in_runtime_not_static() -> None:
    tier = _assign_tier("mcp_boot.servers")
    assert tier == "RUNTIME", (
        f"mcp_boot.servers should be in RUNTIME but got: {tier!r}.\n"
        "The STATIC tier prefix 'mcp' must be 'mcp.' (with dot) to avoid "
        "matching mcp_boot.*."
    )


# ---------------------------------------------------------------------------
# Test: mcp.config_valid is in STATIC (not mis-bucketed)
# ---------------------------------------------------------------------------


def test_mcp_config_valid_in_static() -> None:
    tier = _assign_tier("mcp.config_valid")
    assert tier == "STATIC", (
        f"mcp.config_valid should be in STATIC but got: {tier!r}."
    )


# ---------------------------------------------------------------------------
# Test: a forced core-tables FAIL is VISIBLE in the ASCII table output
# (not silently hidden because of an orphaned prefix)
# ---------------------------------------------------------------------------


def test_core_tables_fail_visible_in_ascii_table() -> None:
    """A FAIL on core_tables.present must appear in the rendered ASCII output."""
    report = _make_report([("core_tables.present", "FAIL")])
    rendered = health._render_ascii(report)
    assert "core_tables.present" in rendered, (
        f"core_tables.present FAIL is not visible in ASCII table output.\n"
        f"Rendered:\n{rendered}"
    )
    assert "STATIC" in rendered, (
        "STATIC tier header missing from output — core_tables tier not rendering."
    )


# ---------------------------------------------------------------------------
# Test: _tier_status() is gone (dead code removed)
# ---------------------------------------------------------------------------


def test_tier_status_dead_code_removed() -> None:
    """_tier_status() must not exist in health module (dead code removed per MH-04)."""
    assert not hasattr(health, "_tier_status"), (
        "_tier_status() still exists in health.py. Remove the dead function."
    )


# ---------------------------------------------------------------------------
# Test: _roll_up_status helper exists and works correctly
# ---------------------------------------------------------------------------


def test_roll_up_status_helper_exists_and_correct() -> None:
    assert hasattr(health, "_roll_up_status"), (
        "_roll_up_status() not found in health.py — collapse helper not added."
    )
    fn = health._roll_up_status
    fail_r = health.CheckResult("x", "FAIL", "m")
    warn_r = health.CheckResult("x", "WARN", "m")
    pass_r = health.CheckResult("x", "PASS", "m")
    assert fn([fail_r]) == "✗"
    assert fn([warn_r]) == "⚠"
    assert fn([pass_r]) == "✓"
    assert fn([fail_r, warn_r]) == "✗", "FAIL must beat WARN"
    assert fn([warn_r, pass_r]) == "⚠", "WARN must beat PASS"


# ---------------------------------------------------------------------------
# Test: check_db_write_probe PASSes on a healthy DB, FAILs on an unwritable one,
# and leaves NO residue (TASK-101 — probe must satisfy context_log NOT NULL FK).
# ---------------------------------------------------------------------------

import sqlite3  # noqa: E402

# Minimal DDL for the two tables the probe touches. We do NOT apply the full
# schema.sql because it creates a sqlite-vec vec0 virtual table that requires the
# extension to be loaded; the probe only depends on sessions + context_log.
_PROBE_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, summary TEXT,
    last_step TEXT, next_step TEXT, branch TEXT DEFAULT 'main', context_json TEXT,
    user_message_count INTEGER DEFAULT 0, last_reset_at TIMESTAMP
);
CREATE TABLE context_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    logged_at TEXT NOT NULL, action_type TEXT, files_modified TEXT,
    decision_refs TEXT, task_updates TEXT, summary TEXT
);
"""


def _make_probe_db(project_dir: Path, *, with_session: bool) -> Path:
    mem = project_dir / ".memory"
    mem.mkdir(parents=True, exist_ok=True)
    db_path = mem / "project.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_PROBE_DDL)
        if with_session:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                ("S-real", "2026-06-14T00:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.mark.parametrize("with_session", [True, False])
def test_db_write_probe_passes_on_healthy_db_and_leaves_no_residue(
    tmp_path: Path, with_session: bool
) -> None:
    """The probe must PASS whether or not a real session already exists, and it
    must leave ZERO residue — no probe context_log row, and no sentinel session.

    with_session=True  exercises the reuse-existing-session path.
    with_session=False exercises the insert-sentinel-then-rollback path.
    """
    db_path = _make_probe_db(tmp_path, with_session=with_session)

    results = health.check_db_write_probe(str(tmp_path))
    assert len(results) == 1
    res = results[0]
    assert res.severity == "PASS", (
        f"write probe must PASS on a healthy DB (with_session={with_session}), "
        f"got {res.severity}: {res.message}"
    )

    # Positive residue invariant: probe row gone, session count unchanged.
    conn = sqlite3.connect(str(db_path))
    try:
        probe_rows = conn.execute(
            "SELECT count(*) FROM context_log WHERE action_type='health_probe'"
        ).fetchone()[0]
        sentinel_rows = conn.execute(
            "SELECT count(*) FROM sessions WHERE id='S-health-probe'"
        ).fetchone()[0]
        total_sessions = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()
    assert probe_rows == 0, "probe left a context_log row — rollback failed"
    assert sentinel_rows == 0, "probe left a sentinel session row — rollback failed"
    assert total_sessions == (1 if with_session else 0), (
        "probe altered the sessions table"
    )


def test_db_write_probe_fails_on_unwritable_db(tmp_path: Path) -> None:
    """A genuinely unwritable DB must still surface a FAIL — the probe must not
    mask write errors."""
    db_path = _make_probe_db(tmp_path, with_session=True)
    mem_dir = db_path.parent
    db_path.chmod(0o444)
    mem_dir.chmod(0o555)
    try:
        results = health.check_db_write_probe(str(tmp_path))
        assert len(results) == 1
        assert results[0].severity == "FAIL", (
            f"unwritable DB must FAIL, got {results[0].severity}: {results[0].message}"
        )
    finally:
        mem_dir.chmod(0o755)
        db_path.chmod(0o644)


def test_db_write_probe_skips_when_db_missing(tmp_path: Path) -> None:
    (tmp_path / ".memory").mkdir(parents=True, exist_ok=True)
    results = health.check_db_write_probe(str(tmp_path))
    assert len(results) == 1
    assert results[0].severity == "SKIP"

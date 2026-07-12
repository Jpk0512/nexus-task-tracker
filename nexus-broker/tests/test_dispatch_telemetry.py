"""Tests for NATIVE-42 / R1-T01: per-dispatch token+time telemetry.

Exercises the `dispatch record` CLI end-to-end against a temp DB, mirroring
the _run(*args, db_path) subprocess pattern in test_registry_list_fresh_db.py.
Also exercises the health.py check_dispatch_telemetry_kpi INFO-only summary,
asserting the POSITIVE invariant (row present, exact column values) rather
than the absence of a phrase.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_LOG_PY: Path = _REPO_ROOT / ".memory" / "log.py"
_HEALTH_PY: Path = _REPO_ROOT / ".memory" / "health.py"


def _run(
    *args: str,
    db_path: Path,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NEXUS_DB_PATH": str(db_path), "NEXUS_DISABLE_VEC": "1"}
    return subprocess.run(
        [sys.executable, str(_LOG_PY), *args],
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


def _load_health_module():
    """Import health.py from the live .memory/ tree.

    Registers the module under its full name in sys.modules BEFORE exec so
    dataclass introspection (which reads sys.modules[cls.__module__]) works
    correctly — mirrors test_health_tier_rendering.py's _load_health().
    """
    mod_name = "nexus_health_dispatch_telemetry"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _HEALTH_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# AC-1  dispatch record round-trips via the CLI
# ---------------------------------------------------------------------------


def test_dispatch_record_exact_round_trips(tmp_path: Path) -> None:
    """`dispatch record` with default token_source stores an 'exact' row."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    result = _run(
        "dispatch", "record",
        "--persona", "hermes",
        "--model", "sonnet",
        "--task-id", "NATIVE-42",
        "--marker", "DONE",
        "--tokens", "12345",
        "--tool-uses", "7",
        "--duration-ms", "98765",
        db_path=db,
    )
    assert result.returncode == 0, (
        f"dispatch record failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["persona"] == "hermes"
    assert payload["tokens"] == 12345
    assert payload["token_source"] == "exact"
    assert payload["duration_ms"] == 98765
    assert isinstance(payload["dispatch_telemetry_id"], int)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE id=?", (payload["dispatch_telemetry_id"],)
    ).fetchone()
    conn.close()
    assert row is not None, "dispatch_telemetry row not found after CLI insert"
    assert row["persona"] == "hermes"
    assert row["model"] == "sonnet"
    assert row["task_id"] == "NATIVE-42"
    assert row["marker"] == "DONE"
    assert row["tokens"] == 12345
    assert row["token_source"] == "exact"
    assert row["tool_uses"] == 7
    assert row["duration_ms"] == 98765
    assert row["run_context"] == "local"


def test_dispatch_record_approx_token_source(tmp_path: Path) -> None:
    """`dispatch record --token-source approx` stores 'approx' (char/4 fallback)."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    result = _run(
        "dispatch", "record",
        "--persona", "forge-ui",
        "--tokens", "500",
        "--token-source", "approx",
        "--duration-ms", "1200",
        "--run-context", "ci",
        db_path=db,
    )
    assert result.returncode == 0, (
        f"dispatch record failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["token_source"] == "approx"

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE id=?", (payload["dispatch_telemetry_id"],)
    ).fetchone()
    conn.close()
    assert row["token_source"] == "approx"
    assert row["run_context"] == "ci"
    assert row["model"] is None
    assert row["task_id"] is None
    assert row["marker"] is None


def test_dispatch_table_created_on_fresh_init(tmp_path: Path) -> None:
    """A fresh `init` (no prior DB) creates dispatch_telemetry with the expected columns."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_telemetry)")}
    conn.close()
    expected = {
        "id", "session_id", "dispatch_id", "persona", "model", "task_id",
        "marker", "tokens", "token_source", "tool_uses", "duration_ms",
        "run_context", "recorded_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_dispatch_record_idempotent_reinit(tmp_path: Path) -> None:
    """Re-running `init` on an existing DB (already-initialized) is a safe no-op
    for dispatch_telemetry — table survives, no crash (covers 'ensure the table
    is created on BOTH fresh AND already-initialized DBs')."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)
    second = _run("init", db_path=db)
    assert second.returncode == 0, (
        f"Second init failed:\nstdout={second.stdout}\nstderr={second.stderr}"
    )

    result = _run(
        "dispatch", "record",
        "--persona", "atlas",
        "--tokens", "10",
        "--duration-ms", "50",
        db_path=db,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["persona"] == "atlas"


# ---------------------------------------------------------------------------
# AC-2  health.py check_dispatch_telemetry_kpi — graceful empty state + aggregates
# ---------------------------------------------------------------------------


def test_health_check_empty_state_no_table(tmp_path: Path) -> None:
    """A project.db without dispatch_telemetry renders a graceful INFO, never an error."""
    health = _load_health_module()
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    conn = sqlite3.connect(str(project / ".memory" / "project.db"))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, ended_at TEXT)")
    conn.commit()
    conn.close()

    results = health.check_dispatch_telemetry_kpi(str(project))
    assert len(results) == 1
    assert results[0].severity == "INFO"
    assert "dispatch_telemetry table not present" in results[0].message


def test_health_check_empty_state_missing_db(tmp_path: Path) -> None:
    """A project with no project.db at all renders a graceful INFO, never an error."""
    health = _load_health_module()
    project = tmp_path / "no-db-proj"
    project.mkdir()

    results = health.check_dispatch_telemetry_kpi(str(project))
    assert len(results) == 1
    assert results[0].severity == "INFO"
    assert "not found" in results[0].message


def test_health_check_aggregates_by_persona_and_model(tmp_path: Path) -> None:
    """Populated dispatch_telemetry yields per-persona AND per-model aggregate rows."""
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    db = project / ".memory" / "project.db"
    _run("init", db_path=db, check=True)

    _run("dispatch", "record", "--persona", "hermes", "--model", "sonnet",
         "--tokens", "100", "--duration-ms", "1000", db_path=db, check=True)
    _run("dispatch", "record", "--persona", "hermes", "--model", "sonnet",
         "--tokens", "300", "--duration-ms", "3000", db_path=db, check=True)
    _run("dispatch", "record", "--persona", "atlas", "--model", "opus",
         "--tokens", "50", "--token-source", "approx", "--duration-ms", "500", db_path=db, check=True)

    health = _load_health_module()
    results = health.check_dispatch_telemetry_kpi(str(project))
    assert all(r.severity == "INFO" for r in results), "KPI check must be INFO-only, never FAIL"

    summary = results[0]
    assert "3 dispatch(es) recorded" in summary.message

    persona_msgs = " | ".join(
        r.message for r in results if r.name == "dispatch_telemetry.by_persona"
    )
    assert "hermes: n=2 avg_tokens=200 avg_duration_ms=2000 (2/2 exact)" in persona_msgs
    assert "atlas: n=1 avg_tokens=50 avg_duration_ms=500 (0/1 exact)" in persona_msgs

    model_msgs = " | ".join(
        r.message for r in results if r.name == "dispatch_telemetry.by_model"
    )
    assert "sonnet: n=2 avg_tokens=200 avg_duration_ms=2000 (2/2 exact)" in model_msgs
    assert "opus: n=1 avg_tokens=50 avg_duration_ms=500 (0/1 exact)" in model_msgs


def test_health_check_aggregates_scoped_to_recent_window_not_all_time(tmp_path: Path) -> None:
    """LENS REVISE regression: once the table exceeds _DISPATCH_KPI_RECENT_N (20)
    rows, by_persona/by_model aggregates must reflect only the most-recent-N
    window named in the summary line — not silently drift to all-time
    aggregates while the header still claims 'last N'.

    Seeds 25 'hermes' rows: the oldest 5 carry tokens=9999 (a sentinel that
    would blow the average far off if included), the newest 20 carry
    tokens=100. If the aggregate query is unscoped (GROUP BY over the whole
    table), avg_tokens would be pulled toward the 9999 sentinel; scoped to the
    most recent 20 it must be exactly 100.
    """
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    db = project / ".memory" / "project.db"
    _run("init", db_path=db, check=True)

    conn = sqlite3.connect(str(db))
    conn.execute("SELECT id FROM sessions LIMIT 0")  # sanity: schema present
    # Insert 5 old sentinel rows, then 20 recent normal rows, all persona=hermes.
    # recorded_at is explicit and strictly increasing so ORDER BY recorded_at DESC
    # deterministically ranks the 20 "recent" rows above the 5 "old" ones.
    rows = []
    for i in range(5):
        rows.append((f"2020-01-01T00:00:{i:02d}Z", "hermes", 9999, "exact", 9999))
    for i in range(20):
        rows.append((f"2026-01-01T00:00:{i:02d}Z", "hermes", 100, "exact", 100))
    conn.executemany(
        """INSERT INTO dispatch_telemetry
               (recorded_at, persona, tokens, token_source, duration_ms)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()

    health = _load_health_module()
    results = health.check_dispatch_telemetry_kpi(str(project))

    summary = results[0]
    assert "25 dispatch(es) recorded" in summary.message
    assert "20" in summary.message, "summary must name the recent-N window size"

    persona_msgs = " | ".join(
        r.message for r in results if r.name == "dispatch_telemetry.by_persona"
    )
    assert "hermes: n=20 avg_tokens=100 avg_duration_ms=100 (20/20 exact)" in persona_msgs, (
        f"by_persona aggregate must be scoped to the recent-20 window, not all-time "
        f"(would show n=25 avg_tokens~2100 if unscoped): {persona_msgs}"
    )

    model_msgs = " | ".join(
        r.message for r in results if r.name == "dispatch_telemetry.by_model"
    )
    assert "unknown: n=20 avg_tokens=100 avg_duration_ms=100 (20/20 exact)" in model_msgs, (
        f"by_model aggregate must be scoped to the recent-20 window, not all-time: {model_msgs}"
    )

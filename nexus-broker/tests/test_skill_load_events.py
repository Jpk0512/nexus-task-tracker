"""Tests for R2-T15: skill_load_events table + dispatch_telemetry nullable columns.

Spec: nexus-redesign/plans/03-r2e2-design-APPROVED.md §7 (FIX-2 corrected design),
tracked as R2-T15 in nexus-redesign/TASKS.md.

Pins three acceptance criteria (GWT):

  AC-1: a `skill_load_events` row can be written via the CLI/module function and
        is correctly FK-linked to a `dispatch_telemetry` row by dispatch_id
        (dispatch_telemetry.dispatch_id == skill_load_events.dispatch_id).

  AC-2: dispatch_telemetry's one-row-per-dispatch shape, and health.py's
        completion-time KPI panel + its existing test, still pass with the two
        new nullable columns (independent_subtask_count, decomposition_considered)
        present on the table but left unset (NULL) — i.e. the N-per-dispatch
        skill_load_events table does NOT collapse into dispatch_telemetry, and
        adding the two genuinely one-row-per-dispatch nullable columns does not
        multiply dispatch_telemetry rows or break the KPI aggregate.

Verified GREEN against `log.py skill record-load` + the dispatch_telemetry
nullable-column migration once Pipeline landed both (this file's assertions
were authored RED, pinned to the intended CLI shape from spec §7, and went
green with only a CLI-subcommand-name correction needed — `skill record-load`
vs. the initially-guessed `skill-load record` — no assertion logic changed).
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
_SCHEMA_SQL: Path = _REPO_ROOT / ".memory" / "schema.sql"


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
    """Import health.py from the live .memory/ tree (mirrors test_dispatch_telemetry.py)."""
    mod_name = "nexus_health_skill_load_events"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _HEALTH_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# AC-1: skill_load_events row is written and FK-linked to a dispatch_telemetry
# row via dispatch_id.
# ---------------------------------------------------------------------------


def test_skill_load_events_table_exists_with_expected_columns(tmp_path: Path) -> None:
    """Given a fresh `init`, the skill_load_events table exists with the
    dispatch_id/skill_id/ts/byte_len shape declared in schema.sql (already
    landed there per R2-T15). This proves schema.sql's CREATE TABLE actually
    gets applied by `init`, independent of any CLI write path."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    conn = sqlite3.connect(str(db))
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_load_events'"
        )
    }
    assert "skill_load_events" in tables, (
        "skill_load_events table must be created by `init` (schema.sql already "
        "declares it per R2-T15 — this asserts it is actually applied)."
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skill_load_events)")}
    conn.close()
    expected = {"id", "dispatch_id", "skill_id", "ts", "byte_len", "recorded_at"}
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_skill_load_event_write_is_fk_linked_to_dispatch_telemetry_row(
    tmp_path: Path,
) -> None:
    """Given a recorded dispatch_telemetry row with dispatch_id='D-100',
    When a skill_load_events row is written via the CLI/module function for
    that same dispatch_id,
    Then the skill_load_events row's dispatch_id matches the dispatch_telemetry
    row's dispatch_id — the FK link the spec requires (§7: "FK-linked to the
    dispatch row").

    This exercises `log.py skill record-load`.
    """
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    dispatch_result = _run(
        "dispatch", "record",
        "--persona", "pipeline-data",
        "--model", "sonnet",
        "--task-id", "R2-T15",
        "--marker", "DONE",
        "--tokens", "5000",
        "--duration-ms", "42000",
        "--dispatch-id", "D-100",
        db_path=db,
        check=True,
    )
    dispatch_payload = json.loads(dispatch_result.stdout)
    assert dispatch_payload["dispatch_telemetry_id"] is not None

    skill_result = _run(
        "skill", "record-load",
        "--dispatch-id", "D-100",
        "--skill-id", "polars-test-fixtures",
        "--ts", "2026-07-05T12:00:00Z",
        "--byte-len", "2048",
        db_path=db,
    )
    assert skill_result.returncode == 0, (
        f"`skill record-load` must exist and succeed:\n"
        f"stdout={skill_result.stdout}\nstderr={skill_result.stderr}"
    )
    skill_payload = json.loads(skill_result.stdout)
    assert isinstance(skill_payload["skill_load_event_id"], int)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    event_row = conn.execute(
        "SELECT * FROM skill_load_events WHERE id=?",
        (skill_payload["skill_load_event_id"],),
    ).fetchone()
    dispatch_row = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE dispatch_id=?", ("D-100",)
    ).fetchone()
    conn.close()

    assert event_row is not None, "skill_load_events row not found after write"
    assert dispatch_row is not None, "dispatch_telemetry row not found for D-100"
    assert event_row["dispatch_id"] == dispatch_row["dispatch_id"] == "D-100", (
        "skill_load_events.dispatch_id must FK-link to the dispatch_telemetry "
        "row's dispatch_id."
    )
    assert event_row["skill_id"] == "polars-test-fixtures"
    assert event_row["byte_len"] == 2048


def test_skill_load_events_are_n_per_dispatch_not_collapsed_into_telemetry(
    tmp_path: Path,
) -> None:
    """Given one dispatch_telemetry row, When THREE skill_load_events rows are
    written for that same dispatch_id, Then all three persist independently
    (N-per-dispatch) and dispatch_telemetry still has exactly ONE row for that
    dispatch — proving the two tables are not collapsed (spec §9: "do not
    collapse these tables for 'simplicity'")."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    _run(
        "dispatch", "record",
        "--persona", "quill-py",
        "--tokens", "1000",
        "--duration-ms", "5000",
        "--dispatch-id", "D-200",
        db_path=db,
        check=True,
    )
    for skill_id in ("tdd-patterns", "pytest-idioms", "polars-test-fixtures"):
        result = _run(
            "skill", "record-load",
            "--dispatch-id", "D-200",
            "--skill-id", skill_id,
            "--ts", "2026-07-05T12:00:00Z",
            db_path=db,
        )
        assert result.returncode == 0, (
            f"skill-load record for {skill_id} failed: {result.stderr}"
        )

    conn = sqlite3.connect(str(db))
    dispatch_count = conn.execute(
        "SELECT COUNT(*) FROM dispatch_telemetry WHERE dispatch_id='D-200'"
    ).fetchone()[0]
    event_count = conn.execute(
        "SELECT COUNT(*) FROM skill_load_events WHERE dispatch_id='D-200'"
    ).fetchone()[0]
    conn.close()

    assert dispatch_count == 1, (
        "dispatch_telemetry must stay one-row-per-dispatch even with 3 "
        f"skill_load_events rows written against it; got {dispatch_count} rows."
    )
    assert event_count == 3, (
        f"All 3 skill_load_events rows must persist independently (N-per-dispatch); "
        f"got {event_count}."
    )


# ---------------------------------------------------------------------------
# AC-2: dispatch_telemetry stays one-row-per-dispatch + completion-KPI panel
# still passes with the two new nullable columns present but unset.
# ---------------------------------------------------------------------------


def test_dispatch_telemetry_has_new_nullable_columns_after_migration(
    tmp_path: Path,
) -> None:
    """Given a fresh `init`, the dispatch_telemetry table carries the two new
    nullable columns documented in schema.sql (independent_subtask_count,
    decomposition_considered) — applied via an idempotent ALTER migration
    (`_migrate_dispatch_telemetry_columns` or equivalent), the same convention
    used for validation_log's nullable columns."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dispatch_telemetry)")}
    conn.close()
    assert "independent_subtask_count" in cols, (
        "dispatch_telemetry must gain independent_subtask_count via migration "
        "(schema.sql documents this ALTER; log.py cmd_init must apply it)."
    )
    assert "decomposition_considered" in cols, (
        "dispatch_telemetry must gain decomposition_considered via migration."
    )


def test_dispatch_telemetry_stays_one_row_per_dispatch_with_new_columns_unset(
    tmp_path: Path,
) -> None:
    """Given a `dispatch record` call that does NOT pass the two new columns,
    When the row is inserted, Then exactly one dispatch_telemetry row exists
    and the two new nullable columns are NULL (unset) — proving the migration
    is additive and does not change the one-row-per-dispatch cardinality or
    require the new columns to be populated."""
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    result = _run(
        "dispatch", "record",
        "--persona", "hermes",
        "--model", "sonnet",
        "--tokens", "777",
        "--duration-ms", "3000",
        "--dispatch-id", "D-300",
        db_path=db,
        check=True,
    )
    assert result.returncode == 0

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM dispatch_telemetry WHERE dispatch_id='D-300'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1, (
        f"Expected exactly one dispatch_telemetry row for D-300 (one-row-per-"
        f"dispatch invariant), got {len(rows)}."
    )
    assert rows[0]["independent_subtask_count"] is None, (
        "independent_subtask_count must be NULL when not supplied by the caller."
    )
    assert rows[0]["decomposition_considered"] is None, (
        "decomposition_considered must be NULL when not supplied by the caller."
    )


def test_completion_kpi_panel_still_passes_with_new_columns_present_unset(
    tmp_path: Path,
) -> None:
    """Given dispatch_telemetry rows recorded with the new nullable columns
    present-but-unset (post-migration schema, pre-population data), When
    health.py's check_dispatch_telemetry_kpi runs, Then it still renders the
    same INFO-only aggregate summary as before the migration — proving the
    R1-landed completion-time KPI panel is unaffected by the additive schema
    change (spec §7: 'the completion-KPI panel + its test must stay green')."""
    project = tmp_path / "proj"
    (project / ".memory").mkdir(parents=True)
    db = project / ".memory" / "project.db"
    _run("init", db_path=db, check=True)

    _run(
        "dispatch", "record", "--persona", "hermes", "--model", "sonnet",
        "--tokens", "100", "--duration-ms", "1000", "--dispatch-id", "D-400",
        db_path=db, check=True,
    )
    _run(
        "dispatch", "record", "--persona", "hermes", "--model", "sonnet",
        "--tokens", "300", "--duration-ms", "3000", "--dispatch-id", "D-401",
        db_path=db, check=True,
    )

    health = _load_health_module()
    results = health.check_dispatch_telemetry_kpi(str(project))
    assert all(r.severity == "INFO" for r in results), (
        "KPI check must stay INFO-only after the migration, never FAIL."
    )
    summary = results[0]
    assert "2 dispatch(es) recorded" in summary.message

    persona_msgs = " | ".join(
        r.message for r in results if r.name == "dispatch_telemetry.by_persona"
    )
    assert "hermes: n=2 avg_tokens=200 avg_duration_ms=2000 (2/2 exact)" in persona_msgs, (
        f"by_persona aggregate must be unaffected by the two new unset nullable "
        f"columns: {persona_msgs}"
    )

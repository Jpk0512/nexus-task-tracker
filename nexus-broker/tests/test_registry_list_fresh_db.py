"""Regression test for S2-06: fresh-DB registry list crash.

BUG: cmd_registry_list SELECTs columns legacy_id, include_prism, has_ledger,
last_validated from project_registry, but schema.sql omits them. Those columns
only exist after migrations/002_project_registry_legacy_fields.sql is applied,
which cmd_init never did. On a fresh DB: `init` then `registry list` raised
sqlite3.OperationalError: no such column: legacy_id.

Test-first protocol:
  Phase 1 (RED)  — xfail removed; test asserts the crash reproduces pre-fix.
  Phase 2 (GREEN) — after fix, both plain `registry list` and
                    `registry list --project-path X` return rc=0.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_LOG_PY: Path = _REPO_ROOT / ".memory" / "log.py"


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


# ---------------------------------------------------------------------------
# AC-1  Fresh DB: init + registry list must not crash
# ---------------------------------------------------------------------------


def test_fresh_db_registry_list_no_crash(tmp_path: Path) -> None:
    """After init on a fresh DB, `registry list` must return rc=0 with valid JSON."""
    db = tmp_path / "project.db"

    init_result = _run("init", db_path=db)
    assert init_result.returncode == 0, (
        f"init failed:\nstdout={init_result.stdout}\nstderr={init_result.stderr}"
    )

    list_result = _run("registry", "list", db_path=db)
    assert list_result.returncode == 0, (
        f"registry list crashed on fresh DB (S2-06 regression):\n"
        f"stdout={list_result.stdout}\nstderr={list_result.stderr}"
    )
    # Output must be valid JSON (an empty list is fine)
    parsed = json.loads(list_result.stdout)
    assert isinstance(parsed, list)


def test_fresh_db_registry_list_by_path_no_crash(tmp_path: Path) -> None:
    """After init on a fresh DB, `registry list --project-path X` must return rc=0."""
    db = tmp_path / "project.db"

    _run("init", db_path=db, check=True)

    list_result = _run("registry", "list", "--project-path", "/some/project", db_path=db)
    assert list_result.returncode == 0, (
        f"registry list --project-path crashed on fresh DB:\n"
        f"stdout={list_result.stdout}\nstderr={list_result.stderr}"
    )
    parsed = json.loads(list_result.stdout)
    assert isinstance(parsed, list)
    assert parsed == []  # no entries registered


# ---------------------------------------------------------------------------
# AC-2  cmd_init is idempotent (re-running on an existing DB is safe)
# ---------------------------------------------------------------------------


def test_init_is_idempotent(tmp_path: Path) -> None:
    """Running init twice on the same DB must not raise or corrupt the schema."""
    db = tmp_path / "project.db"

    _run("init", db_path=db, check=True)
    second = _run("init", db_path=db)
    assert second.returncode == 0, (
        f"Second init failed (idempotency broken):\n"
        f"stdout={second.stdout}\nstderr={second.stderr}"
    )

    # registry list still works after the second init
    list_result = _run("registry", "list", db_path=db)
    assert list_result.returncode == 0
    assert isinstance(json.loads(list_result.stdout), list)


# ---------------------------------------------------------------------------
# AC-3  registry add + list round-trip on a fresh DB
# ---------------------------------------------------------------------------


def test_registry_add_then_list(tmp_path: Path) -> None:
    """After init, add a project, then list it back — all on a fresh DB."""
    db = tmp_path / "project.db"

    _run("init", db_path=db, check=True)

    add_result = _run(
        "registry", "add",
        "--project-path", "/test/project",
        "--version", "v1.2.3",
        "--action", "installed",
        db_path=db,
    )
    assert add_result.returncode == 0, (
        f"registry add failed:\nstdout={add_result.stdout}\nstderr={add_result.stderr}"
    )

    list_result = _run("registry", "list", db_path=db)
    assert list_result.returncode == 0
    rows = json.loads(list_result.stdout)
    assert len(rows) == 1
    assert rows[0]["project_path"] == "/test/project"
    assert rows[0]["current_version"] == "v1.2.3"
    # Migration-002 columns must be present and have correct defaults
    assert rows[0]["legacy_id"] is None
    assert rows[0]["include_prism"] == 0
    assert rows[0]["has_ledger"] == 0
    assert rows[0]["last_validated"] is None

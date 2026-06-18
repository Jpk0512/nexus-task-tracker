"""DEC-019 — broker self-feedback tool (`nexus_submit_feedback`).

POSITIVE invariants pinned here:

  T1. GIVEN a valid (severity, category, message)
      WHEN nexus_submit_feedback runs against a REAL temp project (its own
           .memory/log.py + project.db)
      THEN it returns {"ok": True, "id": <int>} AND a matching nexus_feedback
           row (source='tool') is retrievable from that project's DB.

  T2. GIVEN an invalid severity / category / empty message
      THEN the tool returns ok=False and writes NO row (validated before shelling out).

  T3. GIVEN context_json is supplied
      THEN the stored context_json is enriched with the broker_state turn/persona.

These drive the REAL async tool (not a re-implementation). REPO_ROOT is the only
side-effecting dependency we redirect (to a temp project) so the subprocess writes
into a scratch DB; read_state is injected to control the enrichment context.

This file targets `broker.server` (the broker package itself) and the bundled
`.memory/log.py`, so it ships with the broker snapshot test contract.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import broker.server as srv
import pytest

# The live memory CLI + schema live two levels above nexus-broker/tests/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LIVE_LOG_PY = _REPO_ROOT / ".memory" / "log.py"
_LIVE_SCHEMA = _REPO_ROOT / ".memory" / "schema.sql"


@pytest.fixture()
def temp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch project rooted at tmp_path with an initialized project.db.

    Copies the LIVE log.py + schema.sql so the tool's subprocess writes into an
    isolated DB. Redirects srv.REPO_ROOT so the tool shells out against it.
    """
    mem = tmp_path / ".memory"
    mem.mkdir(parents=True)
    (mem / "log.py").write_bytes(_LIVE_LOG_PY.read_bytes())
    (mem / "schema.sql").write_bytes(_LIVE_SCHEMA.read_bytes())
    subprocess.run(
        [sys.executable, str(mem / "log.py"), "init"],
        cwd=str(tmp_path),
        env={"NEXUS_DB_PATH": str(mem / "project.db"), "PATH": __import__("os").environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    monkeypatch.setattr(srv, "REPO_ROOT", tmp_path)
    # Default state context for enrichment assertions.
    monkeypatch.setattr(
        srv,
        "read_state",
        lambda: {"turn_id": "turn-xyz", "persona": "forge-ui", "team_name": "team-1"},
    )
    return tmp_path


def _rows(project_root: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(project_root / ".memory" / "project.db")
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM nexus_feedback"))
    finally:
        conn.close()


async def test_t1_valid_feedback_returns_ok_and_persists_row(temp_project: Path) -> None:
    """T1 — happy path: ok=True, id present, retrievable source='tool' row."""
    result = await srv.nexus_submit_feedback(
        severity="high",
        category="workflow_friction",
        message="parallel-first ladder unclear for a 2-subtask split",
    )
    assert result["ok"] is True, result
    assert isinstance(result["id"], int) and result["id"] >= 1

    rows = _rows(temp_project)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "tool"
    assert row["severity"] == "high"
    assert row["category"] == "workflow_friction"
    assert "parallel-first ladder" in row["message"]


async def test_t1b_tool_wrapper_returns_ok(temp_project: Path) -> None:
    """The @mcp.tool wrapper delegates to the same impl and returns ok=True."""
    result = await srv.nexus_submit_feedback_tool(
        severity="critical",
        category="roster_mismatch",
        message="no persona owns ingestion/embeddings/",
    )
    assert result["ok"] is True
    assert isinstance(result["id"], int)
    rows = _rows(temp_project)
    assert any(r["category"] == "roster_mismatch" for r in rows)


@pytest.mark.parametrize(
    ("severity", "category", "message"),
    [
        ("nuclear", "workflow_friction", "bad severity"),
        ("high", "not_a_category", "bad category"),
        ("high", "workflow_friction", "   "),
    ],
)
async def test_t2_invalid_input_returns_not_ok_and_writes_no_row(
    temp_project: Path, severity: str, category: str, message: str
) -> None:
    """T2 — validation happens BEFORE the subprocess; no row is written."""
    result = await srv.nexus_submit_feedback(
        severity=severity, category=category, message=message
    )
    assert result["ok"] is False
    assert result["id"] is None
    assert _rows(temp_project) == []


async def test_t3_context_is_enriched_with_dispatch_state(temp_project: Path) -> None:
    """T3 — supplied context_json is merged with broker_state turn/persona."""
    import json

    result = await srv.nexus_submit_feedback(
        severity="medium",
        category="unclear_skill",
        message="which skill covers DuckDB write-path?",
        context_json=json.dumps({"note": "from pipeline-data"}),
    )
    assert result["ok"] is True
    rows = _rows(temp_project)
    assert len(rows) == 1
    stored = json.loads(rows[0]["context_json"])
    assert stored["note"] == "from pipeline-data"
    assert stored["turn_id"] == "turn-xyz"
    assert stored["persona"] == "forge-ui"

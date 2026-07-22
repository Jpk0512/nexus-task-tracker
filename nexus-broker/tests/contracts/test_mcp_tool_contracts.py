"""test_mcp_tool_contracts.py — NEX-001 (DEC-100 pillar 4): schema/interface
contract tests for the broker's highest-value MCP tool I/O.

Each test drives the REAL `@mcp.tool()`-decorated function (FastMCP's
decorator returns the original callable unchanged — verified: `type(tool_fn)
is function`) and validates the returned value against the explicit JSON
Schema in `schemas.py`, which is derived from the tool's own TypedDict /
literal return statements. A test here FAILS the moment a tool's return
shape drifts (a renamed/removed/added key, a type change) — that is
independent of, and does not re-litigate, the business-logic assertions
already covered by test_validate_brief.py / test_discovery_tools.py /
test_feedback_broker_tool.py.

Hermeticity: every test isolates its own state/token/registry files under
tmp_path via monkeypatch — never the real repo's .memory/files/*.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

import broker.capability_token as token_mod
import broker.server as srv
import broker.state as state_mod
import broker.worktree_registry as worktree_mod

from .schemas import (
    BROKER_RESULT_SCHEMA,
    DISCOVER_RESULT_SCHEMA,
    FEEDBACK_TOOL_RESULT_SCHEMA,
    NOTEPAD_PING_RESULT_SCHEMA,
    RELEASE_WORKTREE_RESULT_SCHEMA,
    WORKTREE_RECORD_SCHEMA,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _fresh_ts() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat()


def _well_formed_brief(**overrides: Any) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "goal": "Investigate the failing broker gate and report root cause",
        "context_files": ["src/broker/server.py"],
        "acceptance_criteria": ["root cause identified"],
        "verification_required": ["uv run pytest -q"],
        "do_not_touch": ["pyproject.toml"],
        "notepad_topic": "nex-001-contract",
        "task_tier": "standard",
    }
    brief.update(overrides)
    return brief


@pytest.fixture(autouse=True)
def _isolated_token_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F1-04: an approved validate call mints a capability token — isolate the
    signing key + deny-list into tmp_path (mirrors test_validate_brief.py)."""
    monkeypatch.setattr(token_mod, "KEY_PATH", tmp_path / "broker_token_key.json")
    monkeypatch.setattr(token_mod, "DENYLIST_PATH", tmp_path / "token_denylist.jsonl")


@pytest.fixture
def _neutralized_validate_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize nexus_validate_brief's side effects (mirrors test_validate_brief.py's
    captured_state fixture) so this contract test never touches the live
    broker_state.json / router_dispatches.jsonl."""
    monkeypatch.setattr(srv, "read_state", lambda: {"notepad_logged_at": _fresh_ts()})
    monkeypatch.setattr(srv, "write_state", lambda state: None)
    monkeypatch.setattr(srv, "log_broker_validation", lambda **kwargs: None)
    monkeypatch.setattr(srv, "_consecutive_single_dispatches", lambda: 0)


# ---------------------------------------------------------------------------
# nexus_validate_brief_tool -> BrokerResult
# ---------------------------------------------------------------------------

async def test_validate_brief_tool_approved_shape_matches_schema(
    _neutralized_validate_state: None,
) -> None:
    """Given: a well-formed brief for a legal (persona, intent).
    When: nexus_validate_brief_tool runs.
    Then: the returned dict validates against BROKER_RESULT_SCHEMA and is approved.
    """
    result = await srv.nexus_validate_brief_tool(
        persona="scout",
        intent="investigate",
        brief_json=json.dumps(_well_formed_brief()),
        turn_id="turn-contract-approved",
    )
    jsonschema.validate(dict(result), BROKER_RESULT_SCHEMA)
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"] is not None


async def test_validate_brief_tool_rejected_shape_matches_schema(
    _neutralized_validate_state: None,
) -> None:
    """Given: an invalid persona (a HARD error — never normalized, unlike a
              missing brief field which the validator coerces + warns on).
    When: nexus_validate_brief_tool runs.
    Then: the returned dict still validates against BROKER_RESULT_SCHEMA
          (approved_brief is null on rejection, not omitted).
    """
    result = await srv.nexus_validate_brief_tool(
        persona="totally-made-up-persona",
        intent="investigate",
        brief_json=json.dumps(_well_formed_brief()),
        turn_id="turn-contract-rejected",
    )
    jsonschema.validate(dict(result), BROKER_RESULT_SCHEMA)
    assert result["approved"] is False
    assert result["approved_brief"] is None
    assert result["errors"], "a rejected validation must carry at least one error"


# ---------------------------------------------------------------------------
# nexus_discover -> DiscoverResult
# ---------------------------------------------------------------------------

async def test_discover_shape_matches_schema() -> None:
    """Given: the static persona/intent registry.
    When: nexus_discover runs (pure, no state touched).
    Then: the returned dict validates against DISCOVER_RESULT_SCHEMA.
    """
    result = await srv.nexus_discover()
    jsonschema.validate(dict(result), DISCOVER_RESULT_SCHEMA)
    assert result["personas"], "discover must list at least one dispatchable persona"


# ---------------------------------------------------------------------------
# nexus_notepad_ping -> {"notepad_logged_at": str, "status": "recorded"}
# ---------------------------------------------------------------------------

async def test_notepad_ping_shape_matches_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given: no prior broker_state.json (empty state).
    When: nexus_notepad_ping runs.
    Then: the returned dict validates against NOTEPAD_PING_RESULT_SCHEMA.
    """
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "broker_state.json")
    result = await srv.nexus_notepad_ping()
    jsonschema.validate(dict(result), NOTEPAD_PING_RESULT_SCHEMA)


# ---------------------------------------------------------------------------
# nexus_submit_feedback_tool -> {"ok": bool, ...}
# ---------------------------------------------------------------------------

@pytest.fixture()
def _temp_feedback_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scratch project rooted at tmp_path with an initialized project.db
    (mirrors test_feedback_broker_tool.py's temp_project fixture) so the
    ok=True path's real subprocess write is exercised hermetically."""
    mem = tmp_path / ".memory"
    mem.mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / ".memory" / "log.py", mem / "log.py")
    shutil.copy2(_REPO_ROOT / ".memory" / "schema.sql", mem / "schema.sql")
    subprocess.run(
        [sys.executable, str(mem / "log.py"), "init"],
        cwd=str(tmp_path),
        env={
            "NEXUS_DB_PATH": str(mem / "project.db"),
            "PATH": __import__("os").environ["PATH"],
        },
        capture_output=True,
        text=True,
        check=True,
    )
    monkeypatch.setattr(srv, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        srv,
        "read_state",
        lambda: {"turn_id": "turn-contract", "persona": "quill-py", "team_name": None},
    )
    return tmp_path


async def test_feedback_tool_ok_true_shape_matches_schema(
    _temp_feedback_project: Path,
) -> None:
    """Given: a valid (severity, category, message).
    When: nexus_submit_feedback_tool runs against a real scratch project.db.
    Then: the returned dict validates against FEEDBACK_TOOL_RESULT_SCHEMA (ok=True branch).
    """
    result = await srv.nexus_submit_feedback_tool(
        severity="high",
        category="workflow_friction",
        message="contract-test probe message",
    )
    jsonschema.validate(dict(result), FEEDBACK_TOOL_RESULT_SCHEMA)
    assert result["ok"] is True


async def test_feedback_tool_ok_false_shape_matches_schema(
    _temp_feedback_project: Path,
) -> None:
    """Given: an invalid severity value.
    When: nexus_submit_feedback_tool runs.
    Then: the returned dict validates against FEEDBACK_TOOL_RESULT_SCHEMA (ok=False branch).
    """
    result = await srv.nexus_submit_feedback_tool(
        severity="not-a-real-severity",
        category="workflow_friction",
        message="contract-test probe message",
    )
    jsonschema.validate(dict(result), FEEDBACK_TOOL_RESULT_SCHEMA)
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# nexus_register_worktree -> WorktreeRecord / nexus_release_worktree -> bool
# ---------------------------------------------------------------------------

@pytest.fixture()
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    registry_path = tmp_path / "worktree_registry.json"
    monkeypatch.setattr(worktree_mod, "REGISTRY_PATH", registry_path)
    return registry_path


async def test_register_worktree_shape_matches_schema(_isolated_registry: Path) -> None:
    """Given: a fresh worktree grant request.
    When: nexus_register_worktree runs.
    Then: the returned WorktreeRecord validates against WORKTREE_RECORD_SCHEMA
          (the `path` argument is the registry KEY, never part of the record).
    """
    result = await srv.nexus_register_worktree(
        path="/tmp/some-worktree",
        owner_id="contract-test",
        branch="feat/contract-test",
        ttl_seconds=3600,
    )
    jsonschema.validate(dict(result), WORKTREE_RECORD_SCHEMA)
    assert result["branch"] == "feat/contract-test"


async def test_release_worktree_shape_matches_schema(_isolated_registry: Path) -> None:
    """Given: a previously registered worktree grant.
    When: nexus_release_worktree runs.
    Then: the return value is a bare JSON boolean (True) validating against
          RELEASE_WORKTREE_RESULT_SCHEMA.
    """
    await srv.nexus_register_worktree(
        path="/tmp/some-other-worktree",
        owner_id="contract-test",
        branch="feat/contract-test-2",
    )
    result = await srv.nexus_release_worktree(path="/tmp/some-other-worktree")
    jsonschema.validate(result, RELEASE_WORKTREE_RESULT_SCHEMA)
    assert result is True

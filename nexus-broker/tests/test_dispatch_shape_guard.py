"""
Tests for .claude/hooks/dispatch-shape-guard.sh (R1-T10).

Run with:  cd nexus-broker && uv run pytest tests/test_dispatch_shape_guard.py -q

This is the default-deny BACKSTOP for the dispatch-shape parsing contract:
broker-gate.py, skills-required-guard.sh, persona-alias-resolver.sh, and
dispatch-announce.sh each independently parse subagent_type/agent_type out of
the PreToolUse payload. If the harness ever renames those fields, every one of
those parsers silently returns "" and fails OPEN. This hook turns that
silent-empty case into a loud deny (exit 2) instead of letting an ungoverned
dispatch through — but only for tool_name in {Task, TeamCreate, Agent}; every
other tool_name is a silent pass regardless of payload shape, which is the
literal regression guard against the incident where a top-level `agent_type`
(the CALLING agent's own identity, present on every PreToolUse event) was
misread as a dispatch target and bricked a session by denying every tool call.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / ".claude" / "hooks"
GUARD_SCRIPT = HOOKS_DIR / "dispatch-shape-guard.sh"


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARD_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# Regression guard: the 4 confirmed incident payload shapes.
#
# Each carries a top-level agent_type = the CALLING persona ("plexus") and NO
# persona fields nested in tool_input, since none of these are dispatches.
# MUST exit 0 — this is the literal regression guard against the incident.
# ---------------------------------------------------------------------------

def test_plain_bash_call_exits_zero():
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "agent_type": "plexus",
        "session_id": "S-incident-guard",
    }
    result = _run(payload)
    assert result.returncode == 0, result.stderr


def test_plain_read_call_exits_zero():
    payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": "/project/CLAUDE.md"},
        "agent_type": "plexus",
        "session_id": "S-incident-guard",
    }
    result = _run(payload)
    assert result.returncode == 0, result.stderr


def test_task_list_call_exits_zero():
    payload = {
        "tool_name": "TaskList",
        "tool_input": {},
        "agent_type": "plexus",
        "session_id": "S-incident-guard",
    }
    result = _run(payload)
    assert result.returncode == 0, result.stderr


def test_mcp_tool_call_exits_zero():
    payload = {
        "tool_name": "mcp__nexus-broker__nexus_validate_brief_tool",
        "tool_input": {"brief": "{}"},
        "agent_type": "plexus",
        "session_id": "S-incident-guard",
    }
    result = _run(payload)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Well-formed dispatches to a KNOWN registered persona -> exit 0.
# ---------------------------------------------------------------------------

def test_task_shaped_dispatch_to_known_persona_exits_zero():
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": "hermes",
            "description": "wire up an env var",
        },
        "session_id": "S-dispatch",
    }
    result = _run(payload)
    assert result.returncode == 0, result.stderr


def test_agent_shaped_dispatch_to_known_persona_exits_zero():
    payload = {
        "tool_name": "Agent",
        "tool_input": {
            "agent_type": "atlas",
            "description": "add a duckdb migration",
        },
        "session_id": "S-dispatch",
    }
    result = _run(payload)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Malformed / unregistered dispatches -> exit 2, deny reason present.
# ---------------------------------------------------------------------------

def test_task_shaped_dispatch_to_unregistered_persona_denies():
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": "forge-uxx",
            "description": "typo'd persona name",
        },
        "session_id": "S-dispatch",
    }
    result = _run(payload)
    assert result.returncode == 2, result.stdout
    assert "forge-uxx" in result.stdout
    assert "permissionDecision" in result.stdout
    assert '"deny"' in result.stdout


def test_task_shaped_dispatch_with_no_persona_field_denies():
    """Simulates a future harness field-rename: tool_input carries NEITHER
    subagent_type NOR agent_type at all."""
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "description": "no persona field present at all",
        },
        "session_id": "S-dispatch",
    }
    result = _run(payload)
    assert result.returncode == 2, result.stdout
    assert "permissionDecision" in result.stdout
    assert '"deny"' in result.stdout
    assert "unrecognized/renamed dispatch shape" in result.stdout


def test_team_create_shaped_dispatch_with_no_persona_field_denies():
    payload = {
        "tool_name": "TeamCreate",
        "tool_input": {
            "description": "no persona field present at all",
        },
        "session_id": "S-dispatch",
    }
    result = _run(payload)
    assert result.returncode == 2, result.stdout
    assert '"deny"' in result.stdout


def test_unparseable_json_denies():
    result = subprocess.run(
        ["bash", str(GUARD_SCRIPT)],
        input="not-json-at-all{{{",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2, result.stdout
    assert '"deny"' in result.stdout


# ---------------------------------------------------------------------------
# 3.9 import-safety: no datetime.UTC, no def-time X|None, no match/case in the
# embedded python. (The hook body has no Python file of its own — it is a
# heredoc-free `python3 -c "..."` block inline in the .sh file — so scan the
# .sh source text directly.)
# ---------------------------------------------------------------------------

def test_no_py311_only_idioms_in_embedded_python():
    src = GUARD_SCRIPT.read_text()
    assert "datetime.UTC" not in src
    assert "from datetime import UTC" not in src
    assert "match " not in src or "case " not in src  # no match/case block
    # def-time PEP-604 union without `from __future__ import annotations` guard.
    # The embedded python here declares no function signatures with type hints
    # at all, so this is a structural assertion that none were introduced.
    assert ": str | None" not in src
    assert "-> str | None" not in src


def test_hook_is_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(GUARD_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

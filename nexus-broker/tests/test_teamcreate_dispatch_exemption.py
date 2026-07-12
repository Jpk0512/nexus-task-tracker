"""S2-10/S2-21/S2-30 — TeamCreate dispatch-gate matcher widening.

Proves three things about the PACKAGE copies (nexus-package/):

  (a) Bookkeeping payload — no subagent_type / agent_type / team_name
      → broker-gate.py exits 0 (LOCKOUT-SAFETY early-out, not blocked).

  (b) Teammate payload carrying team_name that matches approved broker_state.json
      (team_approval_ok path) → allowed even beyond the 120s freshness window.

  (c) Ordinary persona Task (subagent_type set) with NO approval / missing
      broker_state → blocked (exit 2, fail-CLOSED).

  (d) The package settings.json dispatch-gate matcher for PreToolUse contains
      "TeamCreate" (widened from "Task" only).

The LIVE tree is already correct.  These tests pin the PACKAGE copy.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths — package broker-gate.py and settings.json
#
# This test file lives in BOTH:
#   - nexus-broker/tests/           (live source, parents[2] = repo root)
#   - nexus-package/nexus-broker/tests/ (package copy, parents[2] = nexus-package/)
#
# In the live tree, the package is under REPO_ROOT/nexus-package/.
# In the package tree, .claude/ is directly under parents[2]/.
# We detect which context by probing for .claude/settings.json at parents[2].
# ---------------------------------------------------------------------------

_PARENTS2 = Path(__file__).resolve().parents[2]
if (_PARENTS2 / ".claude" / "settings.json").exists():
    # Running from within the package tree: parents[2] IS the package root.
    _PKG_ROOT = _PARENTS2
else:
    # Running from the live source tree: package is a subdirectory.
    _PKG_ROOT = _PARENTS2 / "nexus-package"

PKG_HOOKS_DIR = _PKG_ROOT / ".claude" / "hooks"
PKG_BROKER_GATE = PKG_HOOKS_DIR / "broker-gate.py"
PKG_SETTINGS = _PKG_ROOT / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# Subprocess helper — mirrors test_invariant_adversarial._run_broker_gate
# but targets the PACKAGE copy
# ---------------------------------------------------------------------------

def _run_pkg_broker_gate(
    payload,
    state_path="",
    allow_degraded="",
    db_path="",
):
    """Invoke the PACKAGE broker-gate.py with optional env overrides."""
    env = dict(os.environ)
    if state_path:
        env["NEXUS_BROKER_STATE_PATH"] = state_path
    if allow_degraded:
        env["NEXUS_BROKER_ALLOW_DEGRADED"] = allow_degraded
    if db_path:
        env["_HOOK_DB_PATH"] = db_path
    return subprocess.run(
        [sys.executable, str(PKG_BROKER_GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _now_iso():
    return datetime.now(tz=timezone.utc).isoformat()  # noqa: UP017


def _write_state(path, *, approved, team_name="", persona="forge", age_seconds=0):
    """Write a minimal broker_state.json for testing."""
    ts = (datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds)).isoformat()  # noqa: UP017
    state = {
        "approved": approved,
        "called_at": ts,
        "notepad_logged_at": ts,
        "persona": persona,
        "goal": "test dispatch",
        "context_files": [],
        "acceptance_criteria": ["tests pass"],
        "verification_required": True,
        "do_not_touch": [],
    }
    if team_name:
        state["team_name"] = team_name
    Path(path).write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Case (a): bookkeeping payload — no persona/team_name → exits 0
# ---------------------------------------------------------------------------

class TestBookkeepingPayloadExemption:
    """LOCKOUT-SAFETY: a payload with no subagent_type/agent_type/team_name
    (e.g. a TaskUpdate status edit) must NOT be blocked by broker-gate.
    """

    def test_no_persona_no_team_exits_zero(self, tmp_path):
        """Given: a payload carrying no subagent_type, agent_type, or team_name.
        When: broker-gate.py evaluates it (missing broker_state is a side issue).
        Then: exit code is 0 — bookkeeping traffic is never blocked.
        """
        # Deliberately omit broker_state to confirm the LOCKOUT-SAFETY fires
        # before the state read, not because the state happens to be valid.
        payload = {
            "tool_name": "Task",
            "description": "TaskUpdate status=done",
            # NO subagent_type, NO agent_type, NO team_name
        }
        missing_state = str(tmp_path / "no_state.json")
        proc = _run_pkg_broker_gate(
            payload,
            state_path=missing_state,
            db_path=str(tmp_path / "project.db"),
        )
        assert proc.returncode == 0, (
            "Bookkeeping payload (no persona/team_name) must exit 0 (LOCKOUT-SAFETY), "
            f"got {proc.returncode}\nstdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )

    def test_empty_string_persona_exits_zero(self, tmp_path):
        """Given: subagent_type and agent_type are explicitly empty strings.
        When: broker-gate evaluates the payload with a missing broker_state.
        Then: exit code is 0 — empty persona is treated as bookkeeping.
        """
        payload = {
            "tool_name": "TeamCreate",
            "subagent_type": "",
            "agent_type": "",
            # no team_name
        }
        missing_state = str(tmp_path / "no_state.json")
        proc = _run_pkg_broker_gate(
            payload,
            state_path=missing_state,
            db_path=str(tmp_path / "project.db"),
        )
        assert proc.returncode == 0, (
            "Empty persona/team_name payload must exit 0, "
            f"got {proc.returncode}\nstderr: {proc.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Case (b): teammate payload with matching team_name → allowed past 120s window
# ---------------------------------------------------------------------------

class TestTeammateTeamApprovalPath:
    """team_approval_ok relaxation: a TeamCreate spawn carrying team_name that
    matches an approved broker_state.json is allowed even beyond 120s freshness.
    """

    def test_team_approval_allows_past_120s_window(self, tmp_path):
        """Given: broker_state.json has approved=True, team_name='my-workflow',
               persona='scout' (non-code), and called_at is 200s ago (> 120s).
        When: a teammate payload carrying team_name='my-workflow' hits broker-gate.
        Then: exit code is 0 — team_approval_ok supersedes the freshness check.
        """
        state_file = tmp_path / "broker_state.json"
        _write_state(
            state_file,
            approved=True,
            team_name="my-workflow",
            persona="scout",
            age_seconds=200,  # well beyond 120s stale threshold
        )
        payload = {
            "tool_name": "TeamCreate",
            "agent_type": "scout",
            "team_name": "my-workflow",
            "description": json.dumps({
                "persona": "scout",
                "goal": "investigate root cause",
                "task_tier": "simple",
            }),
        }
        proc = _run_pkg_broker_gate(
            payload,
            state_path=str(state_file),
            db_path=str(tmp_path / "project.db"),
        )
        assert proc.returncode == 0, (
            "team_approval_ok (matching team_name) must allow past the 120s window, "
            f"got {proc.returncode}\nstdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )

    def test_mismatched_team_name_still_blocked_when_stale(self, tmp_path):
        """Given: broker_state.json carries team_name='other-team' but the payload
               carries team_name='my-workflow' (mismatch), and called_at is 200s ago.
        When: broker-gate evaluates the dispatch.
        Then: exit code is 2 — team_approval_ok requires an exact team_name match.
        """
        state_file = tmp_path / "broker_state.json"
        _write_state(
            state_file,
            approved=True,
            team_name="other-team",
            persona="scout",
            age_seconds=200,
        )
        payload = {
            "tool_name": "TeamCreate",
            "agent_type": "scout",
            "team_name": "my-workflow",
        }
        proc = _run_pkg_broker_gate(
            payload,
            state_path=str(state_file),
            db_path=str(tmp_path / "project.db"),
        )
        assert proc.returncode == 2, (
            "Mismatched team_name must still BLOCK when state is stale, "
            f"got {proc.returncode}\nstderr: {proc.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Case (c): ordinary persona Task with no approval → blocked (exit 2)
# ---------------------------------------------------------------------------

class TestOrdinaryPersonaTaskBlocked:
    """A plain Task dispatch with subagent_type set but no valid broker approval
    must be blocked (fail-CLOSED design).
    """

    def test_persona_task_no_state_exits_two(self, tmp_path):
        """Given: payload has subagent_type='forge' but broker_state.json is missing.
        When: broker-gate evaluates the dispatch.
        Then: exit code is 2 (BLOCK, fail-CLOSED).
        """
        payload = {
            "tool_name": "Task",
            "subagent_type": "forge",
            "description": json.dumps({
                "persona": "forge",
                "goal": "implement feature",
                "work_type": "wiring",
                "task_tier": "standard",
            }),
        }
        missing_state = str(tmp_path / "no_state.json")
        proc = _run_pkg_broker_gate(
            payload,
            state_path=missing_state,
            db_path=str(tmp_path / "project.db"),
        )
        assert proc.returncode == 2, (
            "forge Task with missing broker_state must BLOCK (exit 2), "
            f"got {proc.returncode}\nstdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
        )

    def test_persona_task_approved_false_exits_two(self, tmp_path):
        """Given: broker_state.json has approved=False (fresh timestamp).
        When: broker-gate evaluates a forge Task dispatch.
        Then: exit code is 2 (BLOCK) — unapproved state must not open the gate.
        """
        state_file = tmp_path / "broker_state.json"
        _write_state(state_file, approved=False, persona="forge", age_seconds=5)
        payload = {
            "tool_name": "Task",
            "subagent_type": "forge",
            "description": json.dumps({
                "persona": "forge",
                "goal": "implement feature",
                "work_type": "wiring",
                "task_tier": "standard",
            }),
        }
        proc = _run_pkg_broker_gate(
            payload,
            state_path=str(state_file),
            db_path=str(tmp_path / "project.db"),
        )
        assert proc.returncode == 2, (
            "approved=False must BLOCK (exit 2), "
            f"got {proc.returncode}\nstderr: {proc.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Case (d): package settings.json PreToolUse dispatch matcher contains TeamCreate
# ---------------------------------------------------------------------------

class TestSettingsJsonMatcherWidened:
    """The package settings.json dispatch-gate block must use Task|TeamCreate,
    not Task alone — so TeamCreate teammate spawns also pass through
    broker-gate/skills/alias/parallel-first.
    """

    def _load_settings(self):
        return json.loads(PKG_SETTINGS.read_text())

    def test_pretooluse_dispatch_gate_matcher_contains_teamcreate(self):
        """Given: nexus-package/.claude/settings.json is the installed package.
        When: we read the PreToolUse hooks block for the dispatch gate.
        Then: the matcher string contains 'TeamCreate'.

        TASK-048 cut the deployable over to the redesigned gate_runner spine:
        the PreToolUse dispatch-gate command is no longer a direct
        `broker-gate.py` invocation — it's `_py.sh gate_runner.py
        pretooluse-dispatch`, whose EVENT_CHAINS entry still runs broker-gate
        (among other checks) as one step. Match on either invocation shape so
        this test tracks whichever command is actually wired, live or package.
        """
        settings = self._load_settings()
        pre_hooks = settings["hooks"]["PreToolUse"]
        dispatch_block = None
        for block in pre_hooks:
            cmds = [h["command"] for h in block.get("hooks", [])]
            if any(
                "broker-gate" in c or ("gate_runner" in c and "pretooluse-dispatch" in c)
                for c in cmds
            ):
                dispatch_block = block
                break
        assert dispatch_block is not None, (
            "Could not find the dispatch-gate PreToolUse hook block "
            "(broker-gate.py or gate_runner.py pretooluse-dispatch) in package settings.json"
        )
        matcher = dispatch_block.get("matcher", "")
        assert "TeamCreate" in matcher, (
            f"PreToolUse dispatch-gate matcher must contain 'TeamCreate' (S2-10/21/30). "
            f"Got: {matcher!r}"
        )

    def test_posttooluse_stall_counter_matcher_contains_teamcreate(self):
        """Given: nexus-package/.claude/settings.json PostToolUse stall-counter block.
        When: we read its matcher.
        Then: the matcher string contains 'TeamCreate'.
        """
        settings = self._load_settings()
        post_hooks = settings["hooks"]["PostToolUse"]
        stall_block = None
        for block in post_hooks:
            cmds = [h["command"] for h in block.get("hooks", [])]
            if any("stall-counter" in c for c in cmds):
                stall_block = block
                break
        assert stall_block is not None, (
            "Could not find stall-counter.sh PostToolUse hook block in package settings.json"
        )
        matcher = stall_block.get("matcher", "")
        assert "TeamCreate" in matcher, (
            f"PostToolUse stall-counter matcher must contain 'TeamCreate' (S2-10/21/30). "
            f"Got: {matcher!r}"
        )

    def test_settings_json_is_valid_json(self):
        """Given: nexus-package/.claude/settings.json.
        When: parsed with json.load.
        Then: no exception is raised (valid JSON).
        """
        # json.loads will raise ValueError on malformed JSON — the test fails loudly.
        data = json.loads(PKG_SETTINGS.read_text())
        assert isinstance(data, dict), "settings.json root must be a JSON object"

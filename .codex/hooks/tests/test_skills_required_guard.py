"""
Tests for .claude/hooks/skills-required-guard.sh (Phase C).

Run with:  python3 -m pytest .claude/hooks/tests/test_skills_required_guard.py -v

The hook enforces CONTRACT R19: brief-driven skill loading.
- DENIES code-writing personas with empty skills_required (exit 2 + real-object
  permissionDecision="deny", mirroring no-direct-push-to-main.sh)
- Advises (additionalContext, exit 0) when mandatory SKILL_MAP skills are missing
- Fails open (exit 0 + stderr WARN) when SKILL_MAP.md is absent
- Allows read-only personas (scout, lens) with no skills
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
REPO_ROOT = HOOKS_DIR.parent.parent
GUARD_SCRIPT = HOOKS_DIR / "skills-required-guard.sh"
SKILL_MAP_PATH = REPO_ROOT / "docs" / "agents" / "SKILL_MAP.md"


def _build_payload(
    subagent_type: str,
    skills_required: list[str] | None,
    work_type: str = "component",
    task_description: str = "add a Tremor card to the dashboard",
) -> dict:
    """Build a PreToolUse Task payload with a JSON brief in the value field."""
    brief: dict = {
        "subagent_type": subagent_type,
        "work_type": work_type,
        "task_description": task_description,
    }
    if skills_required is not None:
        brief["skills_required"] = skills_required
    return {
        "tool_name": "Task",
        "input": {
            "subagent_type": subagent_type,
            "description": json.dumps(brief),
        },
        "session_id": "S-guard-test",
    }


def _build_team_payload(
    agent_type: str,
    skills_required: list[str] | None,
    work_type: str = "component",
    team_name: str = "feat-dash-team",
    task_description: str = "add a Tremor card to the dashboard",
) -> dict:
    """Build an Agent/Team teammate dispatch payload (P6-01).

    The team teammate spawn surfaces tool_name=Task but carries the persona under
    `agent_type` (not `subagent_type`) plus a `team_name`. The guard must read
    `agent_type` so a code-writing teammate with empty skills is still denied.
    """
    brief: dict = {
        "agent_type": agent_type,
        "work_type": work_type,
        "task_description": task_description,
    }
    if skills_required is not None:
        brief["skills_required"] = skills_required
    return {
        "tool_name": "Task",
        "input": {
            "agent_type": agent_type,
            "team_name": team_name,
            "description": json.dumps(brief),
        },
        "session_id": "S-guard-team-test",
    }


def _run_guard(
    payload: dict,
    extra_env: dict | None = None,
) -> tuple[int, str, str]:
    """Invoke skills-required-guard.sh as a Python subprocess."""
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(GUARD_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# Test 1 — empty skills_required for code-writing persona → DENY
# ---------------------------------------------------------------------------
# Given: a Task dispatch to forge-ui with skills_required=[]
# When:  skills-required-guard.sh processes the PreToolUse event
# Then:  exit code 2, hookSpecificOutput is a REAL OBJECT with
#        permissionDecision="deny" (mirrors no-direct-push-to-main.sh).
#        The old stringified-payload + exit 0 NEVER blocked the harness.


def test_denies_empty_skills_for_forge_ui() -> None:
    """Empty skills_required for forge-ui dispatch → exit 2 + object deny."""
    payload = _build_payload("forge-ui", skills_required=[])
    code, out, err = _run_guard(payload)
    assert code == 2, (
        f"Guard MUST exit 2 to actually block the harness. Got {code}. stderr={err}"
    )

    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        pytest.fail(f"Expected JSON stdout from guard, got: {out!r}")

    inner = result.get("hookSpecificOutput")
    assert isinstance(inner, dict), (
        f"hookSpecificOutput MUST be a real object, not a stringified payload. "
        f"Got: {inner!r}"
    )
    assert inner.get("hookEventName") == "PreToolUse", f"Got: {inner}"
    assert inner.get("permissionDecision") == "deny", (
        f"Expected permissionDecision=deny for empty skills, got: {inner}"
    )
    assert "skills_required" in inner.get("permissionDecisionReason", "").lower(), (
        f"Deny reason must reference skills_required. Got: {inner}"
    )
    # Fail-loud: the reason is also written to stderr.
    assert "skills_required" in err.lower(), (
        f"Deny reason must surface on stderr too. Got stderr: {err!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — non-empty skills_required for forge-ui → pass-through
# ---------------------------------------------------------------------------
# Given: a Task dispatch to forge-ui with skills_required=[forge-ui-conventions]
# When:  skills-required-guard.sh processes the event
# Then:  exit code 0, no block decision (may emit warn or empty output)


def test_allows_non_empty_skills_for_forge_ui() -> None:
    """Non-empty (complete) skills_required for forge-ui → exit 0, no deny."""
    payload = _build_payload(
        "forge-ui",
        skills_required=["forge-ui-conventions", "tremor-patterns", "tailwind-design-tokens"],
    )
    code, out, _err = _run_guard(payload)
    assert code == 0, f"Expected exit 0, got {code}"

    if out.strip():
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            return  # non-JSON output is fine (pass-through)
        inner = result.get("hookSpecificOutput")
        if isinstance(inner, dict):
            assert inner.get("permissionDecision") != "deny", (
                f"Must not deny when skills_required is complete. Got: {inner}"
            )


# ---------------------------------------------------------------------------
# Test 3 — missing mandated skill → warn (exit 0, advisory output)
# ---------------------------------------------------------------------------
# Given: forge-ui with skills_required=[forge-ui-conventions] only
#        but SKILL_MAP says tremor-patterns is also required for component work_type
# When:  guard processes it
# Then:  exit 0, hookSpecificOutput decision=warn (not block)


def test_advises_on_missing_mandated_skill() -> None:
    """Missing SKILL_MAP-mandated skill → advisory additionalContext (exit 0), not deny."""
    if not SKILL_MAP_PATH.exists():
        pytest.skip("SKILL_MAP.md not present — Phase C not fully deployed")

    # forge-ui/component mandates tremor-patterns + tailwind-design-tokens beyond
    # the foundational forge-ui-conventions, so this brief is deliberately partial.
    payload = _build_payload(
        "forge-ui",
        skills_required=["forge-ui-conventions"],
        work_type="component",
    )
    code, out, _err = _run_guard(payload)
    assert code == 0, f"Missing mandated skill must not deny (exit 0), got {code}"

    # Output must be a real-object advisory (additionalContext), never a deny.
    assert out.strip(), "Expected an advisory on a known missing mandatory skill"
    result = json.loads(out)
    inner = result.get("hookSpecificOutput")
    assert isinstance(inner, dict), f"Advisory must be a real object. Got: {inner!r}"
    assert inner.get("permissionDecision") != "deny", (
        f"Missing mandated skill must advise, not deny. Got: {inner}"
    )
    ctx = inner.get("additionalContext", "")
    assert "tremor-patterns" in ctx, (
        f"Advisory must name the missing mandatory skill(s). Got: {inner}"
    )


# ---------------------------------------------------------------------------
# Test 4a — SKILL_MAP.md missing + non-empty skills → fail-open (exit 0 + WARN)
# ---------------------------------------------------------------------------
# Given: SKILL_MAP.md pointed at a non-existent path via env override, and a
#        brief whose skills_required is non-empty (so Gate 1 does not fire)
# When:  guard processes the dispatch
# Then:  exit 0 (Gate 2 disabled / fails open) AND a stderr WARN names the map


def test_fail_open_when_skill_map_missing(tmp_path: Path) -> None:
    """Gate 2 fails open (exit 0 + stderr WARN) when SKILL_MAP.md is absent."""
    fake_map = str(tmp_path / "NONEXISTENT_SKILL_MAP.md")
    payload = _build_payload(
        "forge-ui", skills_required=["forge-ui-conventions"], work_type="component"
    )
    code, _out, err = _run_guard(
        payload,
        extra_env={"_HOOK_SKILL_MAP_PATH": fake_map},
    )
    assert code == 0, (
        f"Gate 2 must fail-open (exit 0) when SKILL_MAP is missing. "
        f"Got exit {code}. stderr={err}"
    )
    assert "SKILL_MAP.md not found" in err, (
        f"Fail-open must emit a LOUD stderr WARN naming the map. Got stderr: {err!r}"
    )


# ---------------------------------------------------------------------------
# Test 4b — SKILL_MAP.md missing + empty skills → Gate 1 STILL denies
# ---------------------------------------------------------------------------
# Gate 1 (empty-skills deny) does not depend on the map, so an absent map must
# NOT weaken it: an empty-skills code-writing dispatch is still blocked.


def test_gate1_denies_even_when_skill_map_missing(tmp_path: Path) -> None:
    """Empty skills are denied (exit 2) regardless of SKILL_MAP presence."""
    fake_map = str(tmp_path / "NONEXISTENT_SKILL_MAP.md")
    payload = _build_payload("forge-ui", skills_required=[])
    code, _out, _err = _run_guard(
        payload,
        extra_env={"_HOOK_SKILL_MAP_PATH": fake_map},
    )
    assert code == 2, (
        f"Gate 1 must deny empty skills even with no SKILL_MAP. Got exit {code}"
    )


# ---------------------------------------------------------------------------
# Test 5 — read-only personas (scout, lens) with empty skills → allow
# ---------------------------------------------------------------------------
# Given: a Task dispatch to scout or lens with skills_required=[]
# When:  guard processes the event
# Then:  exit 0, no block


def test_allows_read_only_persona_empty_skills() -> None:
    """Scout and lens with empty skills_required must be allowed (exit 0, no deny)."""
    for persona in ("scout", "lens"):
        payload = _build_payload(
            persona,
            skills_required=[],
            task_description="investigate the DuckDB lock behaviour",
        )
        code, out, err = _run_guard(payload)
        assert code == 0, (
            f"Read-only persona '{persona}' with empty skills must be allowed. "
            f"Got {code}. stderr={err}"
        )
        if out.strip():
            try:
                result = json.loads(out)
            except json.JSONDecodeError:
                continue
            inner = result.get("hookSpecificOutput")
            if isinstance(inner, dict):
                assert inner.get("permissionDecision") != "deny", (
                    f"Must not deny read-only persona '{persona}'. Got: {inner}"
                )


# ---------------------------------------------------------------------------
# Test 6 — REPO_ROOT/SKILL_MAP resolve inside the repo when env is unset
# ---------------------------------------------------------------------------
# Given: _HOOK_REPO_ROOT and _HOOK_SKILL_MAP_PATH are unset
# When:  the guard module resolves its paths from the script location
# Then:  REPO_ROOT == the repo containing .memory, and SKILL_MAP_PATH points at
#        docs/agents/SKILL_MAP.md INSIDE that repo (no foreign superset path)


def test_repo_root_resolves_from_script_location() -> None:
    """With env unset, the guard derives REPO_ROOT from its own location."""
    import importlib.util
    from importlib.machinery import SourceFileLoader

    env_repo = os.environ.pop("_HOOK_REPO_ROOT", None)
    env_map = os.environ.pop("_HOOK_SKILL_MAP_PATH", None)
    try:
        loader = SourceFileLoader("guard_under_test", str(GUARD_SCRIPT))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        assert mod.REPO_ROOT == REPO_ROOT, (
            f"REPO_ROOT must resolve to the repo containing .memory ({REPO_ROOT}), "
            f"got {mod.REPO_ROOT}"
        )
        assert mod.SKILL_MAP_PATH == SKILL_MAP_PATH, (
            f"SKILL_MAP_PATH must be {SKILL_MAP_PATH}, got {mod.SKILL_MAP_PATH}"
        )
        assert str(mod.REPO_ROOT) not in str(mod.SKILL_MAP_PATH).replace(
            str(mod.REPO_ROOT), "", 1
        ), "sanity: SKILL_MAP_PATH lives under REPO_ROOT"
    finally:
        if env_repo is not None:
            os.environ["_HOOK_REPO_ROOT"] = env_repo
        if env_map is not None:
            os.environ["_HOOK_SKILL_MAP_PATH"] = env_map


# ---------------------------------------------------------------------------
# Test 7 — Agent/Team teammate shape (agent_type) is gated like the Task shape
# ---------------------------------------------------------------------------
# A dynamic-Workflow teammate spawn carries the persona under `agent_type` (plus
# a `team_name`), not `subagent_type`. Gate 1 (empty-skills deny) MUST read
# agent_type so a code-writing teammate with empty skills is still blocked.


def test_denies_empty_skills_for_team_agent_type() -> None:
    """A code-writing teammate (agent_type) with empty skills → exit 2 + deny."""
    payload = _build_team_payload("forge-ui", skills_required=[])
    code, out, err = _run_guard(payload)
    assert code == 2, (
        f"Guard MUST exit 2 on an empty-skills team teammate (agent_type). "
        f"Got {code}. stderr={err}"
    )
    result = json.loads(out)
    inner = result.get("hookSpecificOutput")
    assert isinstance(inner, dict), f"Deny must be a real object. Got: {inner!r}"
    assert inner.get("permissionDecision") == "deny", f"Got: {inner}"
    assert "forge-ui" in inner.get("permissionDecisionReason", "").lower(), (
        f"Deny reason must name the agent_type persona. Got: {inner}"
    )


def test_allows_non_empty_skills_for_team_agent_type() -> None:
    """A teammate (agent_type) with complete skills → exit 0, no deny."""
    payload = _build_team_payload(
        "forge-ui",
        skills_required=["forge-ui-conventions", "tremor-patterns", "tailwind-design-tokens"],
    )
    code, out, _err = _run_guard(payload)
    assert code == 0, f"Complete-skills teammate must pass. Got {code}"
    if out.strip():
        try:
            inner = json.loads(out).get("hookSpecificOutput")
        except json.JSONDecodeError:
            return
        if isinstance(inner, dict):
            assert inner.get("permissionDecision") != "deny", (
                f"Must not deny a complete teammate brief. Got: {inner}"
            )


# ---------------------------------------------------------------------------
# Test 8 — LOCKOUT SAFETY: plain TaskCreate / TaskUpdate must NOT be blocked
# ---------------------------------------------------------------------------
# CRITICAL: TaskCreate/TaskUpdate are native task-list bookkeeping tools, NOT
# agent dispatches. They carry no subagent_type / agent_type. The guard must be
# transparent (silent pass, exit 0, no deny) to them — gating them would lock
# Plexus out of its own visible task list.


def test_taskupdate_status_change_is_silent_pass() -> None:
    """A TaskUpdate status change (no persona) → exit 0, no output, no deny."""
    payload = {
        "tool_name": "TaskUpdate",
        "input": {
            "task_id": "task-12",
            "status": "in_progress",
        },
        "session_id": "S-bookkeeping",
    }
    code, out, err = _run_guard(payload)
    assert code == 0, (
        f"TaskUpdate bookkeeping MUST pass (exit 0), never be gated. "
        f"Got {code}. stderr={err}"
    )
    # No persona → the guard returns 0 before emitting any decision object.
    if out.strip():
        inner = json.loads(out).get("hookSpecificOutput")
        if isinstance(inner, dict):
            assert inner.get("permissionDecision") != "deny", (
                f"TaskUpdate must NEVER be denied. Got: {inner}"
            )


def test_taskcreate_is_silent_pass() -> None:
    """A TaskCreate (no persona) → exit 0, no deny — bookkeeping is ungated."""
    payload = {
        "tool_name": "TaskCreate",
        "input": {
            "title": "Audit the broker registry",
            "description": "track a unit of work in the native task list",
        },
        "session_id": "S-bookkeeping",
    }
    code, out, err = _run_guard(payload)
    assert code == 0, (
        f"TaskCreate bookkeeping MUST pass (exit 0). Got {code}. stderr={err}"
    )
    if out.strip():
        inner = json.loads(out).get("hookSpecificOutput")
        if isinstance(inner, dict):
            assert inner.get("permissionDecision") != "deny", (
                f"TaskCreate must NEVER be denied. Got: {inner}"
            )


# ---------------------------------------------------------------------------
# Test 9 — approved_brief backfill: prompt lacks skills block but approved_brief carries them
# ---------------------------------------------------------------------------
# Given: a dispatch prompt with NO fenced skills_required field, but
#        broker_state.json carries approved_brief.skills_required = [hermes-auth-patterns]
# When:  skills-required-guard processes it for a code-writing persona (hermes)
# Then:  exit 0 (backfill from approved_brief prevents Gate 1 from firing)
#
# Guardrail: the GATE itself is not relaxed — if NEITHER the prompt NOR the
# approved_brief has skills, Gate 1 still DENIES (verified by test 10).


def test_approved_brief_backfill_passes_when_prompt_has_no_skills(tmp_path: Path) -> None:
    """Prompt with no skills_required passes when approved_brief carries them."""
    # broker_state.json with an approved_brief that carries skills_required
    broker_state = {
        "approved": True,
        "called_at": "2026-06-25T00:00:00+00:00",
        "persona": "hermes",
        "approved_brief": {
            "subagent_type": "hermes",
            "work_type": "wiring",
            "skills_required": ["hermes-auth-patterns"],
        },
    }
    state_file = tmp_path / "broker_state.json"
    state_file.write_text(json.dumps(broker_state))

    # Dispatch prompt: JSON brief with NO skills_required field
    bare_brief = {
        "subagent_type": "hermes",
        "work_type": "wiring",
        "task_description": "wire the Tableau PAT auth wrapper",
    }
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": "hermes",
            "prompt": json.dumps(bare_brief),
        },
        "session_id": "S-backfill-test",
    }
    code, out, err = _run_guard(
        payload,
        extra_env={"NEXUS_BROKER_STATE_PATH": str(state_file)},
    )
    assert code == 0, (
        f"Prompt with no skills but approved_brief with skills MUST pass (exit 0). "
        f"Got {code}. stdout={out!r} stderr={err!r}"
    )
    assert "backfilled" in err.lower(), (
        f"Guard must emit a stderr note when it backfills from approved_brief. "
        f"Got stderr: {err!r}"
    )
    if out.strip():
        try:
            inner = json.loads(out).get("hookSpecificOutput")
        except json.JSONDecodeError:
            inner = None
        if isinstance(inner, dict):
            assert inner.get("permissionDecision") != "deny", (
                f"Must not deny when approved_brief supplies skills. Got: {inner}"
            )


# ---------------------------------------------------------------------------
# Test 10 — no skills in prompt AND no approved_brief → Gate 1 still DENIES
# ---------------------------------------------------------------------------
# The backfill must never silently pass a dispatch where skills are genuinely
# absent from both sources. Gate 1 must still fire when approved_brief is also
# empty / missing.


def test_gate1_still_denies_when_neither_prompt_nor_approved_brief_has_skills(
    tmp_path: Path,
) -> None:
    """No skills in prompt AND no approved_brief → Gate 1 denies (exit 2)."""
    # broker_state.json with approved_brief but NO skills_required
    broker_state = {
        "approved": True,
        "called_at": "2026-06-25T00:00:00+00:00",
        "persona": "hermes",
        "approved_brief": {
            "subagent_type": "hermes",
            "work_type": "wiring",
        },
    }
    state_file = tmp_path / "broker_state.json"
    state_file.write_text(json.dumps(broker_state))

    bare_brief = {
        "subagent_type": "hermes",
        "work_type": "wiring",
        "task_description": "wire something",
    }
    payload = {
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": "hermes",
            "prompt": json.dumps(bare_brief),
        },
        "session_id": "S-no-skills-anywhere",
    }
    code, out, err = _run_guard(
        payload,
        extra_env={"NEXUS_BROKER_STATE_PATH": str(state_file)},
    )
    assert code == 2, (
        f"Gate 1 MUST deny (exit 2) when neither prompt nor approved_brief has "
        f"skills_required. Got {code}. stdout={out!r} stderr={err!r}"
    )
    try:
        inner = json.loads(out).get("hookSpecificOutput")
    except (json.JSONDecodeError, AttributeError):
        inner = None
    if isinstance(inner, dict):
        assert inner.get("permissionDecision") == "deny", (
            f"Expected deny decision. Got: {inner}"
        )

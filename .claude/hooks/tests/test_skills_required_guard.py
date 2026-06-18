"""Regression tests for skills-required-guard.sh (PreToolUse, matcher: Task).

Pins the WF6 enforcement fix: Gate 1 (the block branch) was a COMPLETE NO-OP —
it emitted a flat-string ``{"hookSpecificOutput": json.dumps({"decision":
"block", ...})}`` AND returned 0, so the harness silently dropped the flat
string and the dispatch was never denied (fail-open). Gate 1 now emits a proper
NESTED deny object
``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
"permissionDecision": "deny", "permissionDecisionReason": ...}}`` AND exits 2,
mirroring worktree-guard.sh — so a code-writing dispatch missing
skills_required is actually blocked.

Gate 2 (warn) is unchanged: a nested advisory object with additionalContext,
exit 0.

These tests assert BOTH directions (deny when it should, allow when it should):
  1. Gate 1 (deny): a code-writing persona with empty skills_required now emits
     a nested permissionDecision=='deny' object AND exits 2.
  2. Gate 1 allow: a code-writing persona WITH skills_required does NOT trip
     Gate 1 — exit 0, no deny.
  3. Gate 2 (warn) produces a valid nested object the harness will surface:
     hookSpecificOutput is a dict with hookEventName=="PreToolUse" and a
     non-empty additionalContext naming the missing skills; exit 0, no deny.
  4. Pass case (all mandatory skills present) emits nothing and exits 0.
  5. Fail-open / no-over-block paths (bad JSON, no subagent_type, non-code
     persona w/o skills, missing SKILL_MAP.md) all exit 0 with no deny.

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_skills_required_guard.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_FILE = (
    Path(__file__).resolve().parent.parent / "skills-required-guard.sh"
)

# A minimal SKILL_MAP.md table mirroring the real format
# (docs/agents/SKILL_MAP.md): `| persona | work_type | required_skills |`.
_SKILL_MAP = """# Skill Map

| persona    | work_type | required_skills                                  |
|------------|-----------|--------------------------------------------------|
| forge-ui   | component | forge-ui-conventions, tremor-patterns, tdd-patterns |
"""


@pytest.fixture()
def repo_root(tmp_path: Path) -> Path:
    """A fixture repo whose docs/agents/SKILL_MAP.md the hook will read via
    the _HOOK_REPO_ROOT env override."""
    skill_map = tmp_path / "docs" / "agents" / "SKILL_MAP.md"
    skill_map.parent.mkdir(parents=True, exist_ok=True)
    skill_map.write_text(_SKILL_MAP, encoding="utf-8")
    return tmp_path


def _run(event: dict, repo_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    if repo_root is not None:
        env["_HOOK_REPO_ROOT"] = str(repo_root)
    return subprocess.run(
        [sys.executable, str(HOOK_FILE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Gate 1 — deny (THE FIX: nested permissionDecision object + exit 2)
# ---------------------------------------------------------------------------


class TestGate1Denies:
    def test_code_persona_empty_skills_denies_with_exit_2(self, repo_root: Path) -> None:
        """Given a code-writing persona with absent skills_required, When the
        guard runs, Then it emits a NESTED hookSpecificOutput with
        permissionDecision=='deny' AND exits 2 — actually blocking the dispatch
        (the old no-op emitted a flat string and exited 0, never blocking)."""
        event = {
            "subagent_type": "forge-ui",
            "input": {"description": "do a thing", "prompt": "no brief json here"},
        }
        result = _run(event, repo_root)
        assert result.returncode == 2, (
            f"Gate 1 must exit 2 to actually deny, got {result.returncode}: "
            f"{result.stdout!r} / {result.stderr!r}"
        )
        assert result.stdout.strip(), "Gate 1 produced no output (deny lost)."
        payload = json.loads(result.stdout)
        hso = payload["hookSpecificOutput"]
        assert isinstance(hso, dict), (
            f"hookSpecificOutput must be a nested object the harness honours, "
            f"got {type(hso)}: {hso!r}"
        )
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        reason = hso["permissionDecisionReason"]
        assert isinstance(reason, str) and "forge-ui" in reason
        # The old dropped flat-string shape must be gone: hso is a dict, not a
        # JSON string carrying decision='block'.
        with pytest.raises((TypeError, json.JSONDecodeError)):
            json.loads(hso)  # would only succeed on the old flat-string shape

    def test_code_persona_with_skills_allows(self, repo_root: Path) -> None:
        """Given a code-writing persona WITH a non-empty skills_required, When
        the guard runs, Then Gate 1 does NOT fire — exit 0 and no deny anywhere
        (no over-blocking of a legitimate dispatch). All mandatory skills are
        present so Gate 2 stays silent too."""
        brief = {
            "work_type": "component",
            "skills_required": [
                "forge-ui-conventions",
                "tremor-patterns",
                "tdd-patterns",
            ],
        }
        event = {
            "subagent_type": "forge-ui",
            "input": {"prompt": f"```json\n{json.dumps(brief)}\n```"},
        }
        result = _run(event, repo_root)
        assert result.returncode == 0, result.stderr
        assert '"permissionDecision": "deny"' not in result.stdout
        assert '"permissionDecision":"deny"' not in result.stdout


# ---------------------------------------------------------------------------
# Gate 2 — warn (THE FIX: now a nested object the harness will surface)
# ---------------------------------------------------------------------------


class TestGate2WarnNestedObject:
    def test_missing_mandatory_emits_nested_object(self, repo_root: Path) -> None:
        """Given a non-empty skills_required missing a mandatory skill, When the
        guard runs, Then hookSpecificOutput is a NESTED OBJECT (dict) with
        hookEventName=='PreToolUse' and a non-empty additionalContext naming the
        missing skill — not the old dropped flat string. Decision is advisory:
        exit 0, no deny/allow change."""
        brief = {
            "work_type": "component",
            "skills_required": ["forge-ui-conventions"],  # missing tremor + tdd
        }
        event = {
            "subagent_type": "forge-ui",
            "input": {"prompt": f"```json\n{json.dumps(brief)}\n```"},
        }
        result = _run(event, repo_root)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), (
            "Gate 2 produced no output — the warn went unsurfaced (the bug)."
        )
        payload = json.loads(result.stdout)
        hso = payload["hookSpecificOutput"]
        assert isinstance(hso, dict), (
            f"hookSpecificOutput must be a nested object, got {type(hso)}: {hso!r}"
        )
        assert hso["hookEventName"] == "PreToolUse", (
            f"Expected hookEventName 'PreToolUse', got: {payload}"
        )
        ctx = hso["additionalContext"]
        assert isinstance(ctx, str) and ctx.strip()
        assert "tremor-patterns" in ctx and "tdd-patterns" in ctx, (
            f"Expected the missing skills named in additionalContext, got: {ctx}"
        )
        # The fix must NOT introduce a decision key that could be read as a deny.
        assert "decision" not in hso

    def test_gate2_is_advisory_only_no_block(self, repo_root: Path) -> None:
        """Gate 2 must never deny: exit code is 0 and no deny decision appears
        anywhere in the output (a persona WITH skills_required must not be
        treated as the empty-skills Gate-1 deny case)."""
        brief = {"work_type": "component", "skills_required": ["forge-ui-conventions"]}
        event = {
            "subagent_type": "forge-ui",
            "input": {"prompt": f"```json\n{json.dumps(brief)}\n```"},
        }
        result = _run(event, repo_root)
        assert result.returncode == 0, result.stderr
        assert '"permissionDecision": "deny"' not in result.stdout
        assert '"permissionDecision":"deny"' not in result.stdout


# ---------------------------------------------------------------------------
# Pass + fail-open — allow path UNCHANGED
# ---------------------------------------------------------------------------


class TestAllowPathsUnchanged:
    def test_all_mandatory_present_emits_nothing(self, repo_root: Path) -> None:
        """Given all mandatory skills present, When the guard runs, Then no
        output and exit 0 (pass)."""
        brief = {
            "work_type": "component",
            "skills_required": [
                "forge-ui-conventions",
                "tremor-patterns",
                "tdd-patterns",
            ],
        }
        event = {
            "subagent_type": "forge-ui",
            "input": {"prompt": f"```json\n{json.dumps(brief)}\n```"},
        }
        result = _run(event, repo_root)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", (
            f"Pass case must emit nothing, got: {result.stdout!r}"
        )

    def test_bad_json_fails_open(self, repo_root: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(HOOK_FILE)],
            input="not json at all",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_no_subagent_type_fails_open(self, repo_root: Path) -> None:
        event = {"input": {"prompt": "anything"}}
        result = _run(event, repo_root)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_non_code_persona_empty_skills_no_block(self, repo_root: Path) -> None:
        """A non-code-writing persona (e.g. scout) with empty skills_required
        must NOT trip Gate 1 — exit 0, no output."""
        event = {
            "subagent_type": "scout",
            "input": {"prompt": "no brief"},
        }
        result = _run(event, repo_root)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_missing_skill_map_fails_open_for_warn(self, tmp_path: Path) -> None:
        """With no SKILL_MAP.md, Gate 2 has no mandatory skills to check, so a
        non-empty skills_required passes silently (fail-open)."""
        brief = {"work_type": "component", "skills_required": ["forge-ui-conventions"]}
        event = {
            "subagent_type": "forge-ui",
            "input": {"prompt": f"```json\n{json.dumps(brief)}\n```"},
        }
        result = _run(event, tmp_path)  # tmp_path has no docs/agents/SKILL_MAP.md
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

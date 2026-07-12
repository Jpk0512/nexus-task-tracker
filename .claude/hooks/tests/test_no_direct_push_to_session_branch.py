"""R1-T02 regression + telemetry tests for
.claude/hooks/no-direct-push-to-session-branch.sh (package twin of the live
no-direct-push-to-main.sh — the package version detects the session branch
dynamically via `git branch --show-current` rather than hardcoding `main`).

Only the nexus-orchestrator or the user (CLAUDE_AGENT_TYPE unset) may push the
session branch; a sub-agent must commit and let the orchestrator push, unless
the bypass token is present on the push segment.

R1-T02 additions: fire (hook_heartbeat.jsonl) telemetry on every exit path.
This hook does its own inline 'jq -n' deny and does not source gate-lib.sh,
so it has no gate_blocks.jsonl integration either before or after this
change — out of scope for this task.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "no-direct-push-to-session-branch.sh"


def _run(command: str, env: dict | None = None) -> tuple[int, str, str]:
    merged = {**os.environ}
    merged.pop("CLAUDE_AGENT_TYPE", None)
    if env:
        merged.update(env)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged,
        cwd=str(HOOKS_DIR.parent.parent),  # run inside the repo so a session branch resolves
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _hook_out(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


class TestRegression:
    def test_non_push_command_is_silent(self) -> None:
        code, out, err = _run("git status")
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

    def test_push_by_unset_agent_type_is_allowed(self) -> None:
        code, out, _err = _run("git push origin HEAD")
        assert code == 0
        assert out.strip() == ""

    def test_push_by_subagent_is_denied(self) -> None:
        code, out, err = _run("git push origin HEAD", env={"CLAUDE_AGENT_TYPE": "hermes"})
        assert code == 2
        ho = _hook_out(out)
        assert ho.get("permissionDecision") == "deny"
        assert "PUSH_SESSION_BRANCH_DENIED" in ho.get("permissionDecisionReason", "")
        assert "PUSH_SESSION_BRANCH_DENIED" in err

    def test_bypass_token_allows_subagent_push(self) -> None:
        code, out, _err = _run(
            "git push origin HEAD # BYPASS:USER-APPROVED-PUSH",
            env={"CLAUDE_AGENT_TYPE": "hermes"},
        )
        assert code == 0
        assert out.strip() == ""


# ─── R1-T02: fire telemetry on every exit path ──────────────────────────────


def _run_scratch(command: str, env: dict | None = None) -> tuple[int, str, str, Path]:
    tmp_path = Path(tempfile.mkdtemp())
    scratch_root = tmp_path / "repo"
    scratch_hooks = scratch_root / ".claude" / "hooks"
    scratch_hooks.mkdir(parents=True)
    for name in ("heartbeat-emitter.sh", "no-direct-push-to-session-branch.sh"):
        shutil.copy(HOOKS_DIR / name, scratch_hooks / name)
    (scratch_root / ".memory" / "files").mkdir(parents=True)

    merged = {**os.environ}
    merged.pop("CLAUDE_AGENT_TYPE", None)
    if env:
        merged.update(env)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    result = subprocess.run(
        ["/bin/bash", str(scratch_hooks / "no-direct-push-to-session-branch.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged,
        # Run outside any git repo so `git branch --show-current` returns
        # empty and the hook exercises its "session branch unknown" fallback
        # deterministically, independent of the real repo's branch state.
        cwd=str(scratch_root),
        timeout=15,
    )
    heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
    return result.returncode, result.stdout, result.stderr, heartbeat_path


class TestTelemetry:
    def test_silent_pass_emits_heartbeat_allow(self) -> None:
        code, out, _err, heartbeat_path = _run_scratch("git status")
        assert code == 0, "regression: silent-pass exit code unchanged"
        assert out.strip() == ""
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "no-direct-push-to-session-branch"
        assert hb["decision"] == "allow"

    def test_denied_push_emits_heartbeat_deny(self) -> None:
        code, out, _err, heartbeat_path = _run_scratch(
            "git push origin somebranch", env={"CLAUDE_AGENT_TYPE": "hermes"}
        )
        assert code == 2, "regression: deny exit code unchanged"
        assert _hook_out(out).get("permissionDecision") == "deny"
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "no-direct-push-to-session-branch"
        assert hb["decision"] == "deny"

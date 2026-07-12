"""R1-T02 regression + telemetry tests for .claude/hooks/worktree-guard.sh
(package twin — N71 PARITY: brought up to the live registry-ownership model;
the NEXUS_ALLOW_WORKTREE=1 env escape-hatch is RETIRED and has zero effect).

Contract (package version, post-N71-parity):
  - `git worktree add ...`         -> DENY (exit 2) unless the resolved
                                       absolute path has a LIVE (non-expired)
                                       registry record in
                                       .memory/files/worktree_registry.json
                                       (see test_worktree_guard.py's live
                                       twin / test_worktree_registry_guard.py
                                       for the full ALLOW/DENY matrix). The
                                       old NEXUS_ALLOW_WORKTREE=1 escape hatch
                                       no longer has any effect.
  - `git checkout -b <new>` / branch -> DENY (exit 2) by default; ALLOW (exit
                                       0, LOUD additionalContext) with the
                                       '# BYPASS:USER-APPROVED-BRANCH' token
  - plain git / non-creating branch  -> silent pass (exit 0, empty stdout)

R1-T02 additions: fire (hook_heartbeat.jsonl) telemetry on every exit path.
N71 parity also added a `source gate-lib.sh` dependency (previously this hook
did its own inline 'jq -n' deny/advise) -- _run_scratch() now copies
gate-lib.sh alongside the hook so the scratch harness doesn't fail to source it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "worktree-guard.sh"


def run_guard(command: str, env: dict | None = None) -> tuple[int, str, str]:
    merged = {**os.environ}
    merged.pop("NEXUS_ALLOW_WORKTREE", None)
    if env:
        merged.update(env)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged,
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
    def test_worktree_add_denied_by_default(self) -> None:
        code, out, _err = run_guard("git worktree add ../wt-foo feat/foo")
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_worktree_add_env_escape_hatch_is_retired(self) -> None:
        # NEXUS_ALLOW_WORKTREE=1 must NOT bypass the registry check anymore --
        # an unregistered path still denies even with the old env var set.
        code, out, _err = run_guard(
            "git worktree add ../wt-foo feat/foo", env={"NEXUS_ALLOW_WORKTREE": "1"}
        )
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_new_branch_denied_by_default(self) -> None:
        code, out, _err = run_guard("git checkout -b feat/foo")
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_new_branch_allowed_with_bypass(self) -> None:
        code, out, _err = run_guard("git checkout -b feat/foo # BYPASS:USER-APPROVED-BRANCH")
        assert code == 0
        assert "additionalContext" in _hook_out(out)

    def test_plain_git_is_silent(self) -> None:
        code, out, err = run_guard("git status")
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""


# ─── R1-T02: fire (heartbeat) telemetry on every exit path ──────────────────


def _run_scratch(command: str, env: dict | None = None) -> tuple[int, str, str, Path]:
    tmp_path = Path(tempfile.mkdtemp())
    scratch_root = tmp_path / "repo"
    scratch_hooks = scratch_root / ".claude" / "hooks"
    scratch_hooks.mkdir(parents=True)
    for name in ("gate-lib.sh", "heartbeat-emitter.sh", "worktree-guard.sh"):
        shutil.copy(HOOKS_DIR / name, scratch_hooks / name)
    (scratch_root / ".memory" / "files").mkdir(parents=True)

    merged = {**os.environ}
    merged.pop("NEXUS_ALLOW_WORKTREE", None)
    if env:
        merged.update(env)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    result = subprocess.run(
        ["/bin/bash", str(scratch_hooks / "worktree-guard.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged,
        timeout=15,
    )
    heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
    return result.returncode, result.stdout, result.stderr, heartbeat_path


class TestTelemetry:
    def test_empty_command_emits_heartbeat_allow(self) -> None:
        code, _out, _err, heartbeat_path = _run_scratch("")
        assert code == 0, "regression: empty-command exit code unchanged"
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "worktree-guard"
        assert hb["event"] == "PreToolUse"
        assert hb["decision"] == "allow"
        assert "ts" in hb and "latency_ms" in hb

    def test_worktree_add_denied_emits_heartbeat_deny(self) -> None:
        code, out, _err, heartbeat_path = _run_scratch("git worktree add ../wt-foo feat/foo")
        assert code == 2, "regression: worktree-add deny exit code unchanged"
        assert _hook_out(out).get("permissionDecision") == "deny"
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "worktree-guard"
        assert hb["decision"] == "deny"

    def test_worktree_add_env_escape_hatch_emits_heartbeat_deny(self) -> None:
        # Retired escape hatch -- still denies (and still emits telemetry).
        code, _out, _err, heartbeat_path = _run_scratch(
            "git worktree add ../wt-foo feat/foo", env={"NEXUS_ALLOW_WORKTREE": "1"}
        )
        assert code == 2, "retired escape hatch must no longer allow"
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "worktree-guard"
        assert hb["decision"] == "deny"

    def test_new_branch_denied_emits_heartbeat_deny(self) -> None:
        code, _out, _err, heartbeat_path = _run_scratch("git checkout -b feat/foo")
        assert code == 2, "regression: new-branch deny exit code unchanged"
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "worktree-guard"
        assert hb["decision"] == "deny"

    def test_silent_pass_emits_heartbeat_allow(self) -> None:
        code, out, _err, heartbeat_path = _run_scratch("git status")
        assert code == 0, "regression: silent-pass exit code unchanged"
        assert out.strip() == ""
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "worktree-guard"
        assert hb["decision"] == "allow"

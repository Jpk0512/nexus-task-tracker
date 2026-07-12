"""R1-T02 regression + telemetry tests for
.claude/hooks/persona-alias-resolver.sh (package twin — differs from live only
in its deny-JSON emission style: inline python3 -c heredocs instead of
gate-lib.sh's gate_deny(), since the package copy predates the live gate-lib.sh
adoption for this hook).

Enforces base-name retirement: forge/pipeline/quill are RETIRED dispatch
targets.

R1-T02 additions: fire (hook_heartbeat.jsonl) telemetry on every exit path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "persona-alias-resolver.sh"


def _run(payload: dict) -> tuple[int, str, str]:
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _task_payload(subagent_type: str, description: str = "") -> dict:
    return {
        "tool_name": "Task",
        "tool_input": {"subagent_type": subagent_type, "description": description},
    }


def _hook_out(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


class TestRegression:
    def test_no_subagent_type_is_silent(self) -> None:
        code, out, err = _run({"tool_name": "TaskUpdate", "tool_input": {}})
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

    def test_canonical_persona_is_silent(self) -> None:
        code, out, _err = _run(_task_payload("hermes", "wire up auth"))
        assert code == 0
        assert out.strip() == ""

    def test_forge_resolves_with_hint(self) -> None:
        code, out, _err = _run(_task_payload("forge", "build app/components/WorkbookList.tsx"))
        assert code == 0
        assert "forge-ui" in _hook_out(out).get("additionalContext", "")

    def test_forge_denied_without_hint(self) -> None:
        code, out, err = _run(_task_payload("forge", "do something vague"))
        assert code == 2
        ho = _hook_out(out)
        assert ho.get("permissionDecision") == "deny"
        assert "\"forge\"" in ho.get("permissionDecisionReason", "")


class TestTelemetry:
    def _run_scratch(self, payload: dict) -> tuple[int, str, str, Path]:
        tmp_path = Path(tempfile.mkdtemp())
        scratch_root = tmp_path / "repo"
        scratch_hooks = scratch_root / ".claude" / "hooks"
        scratch_hooks.mkdir(parents=True)
        for name in ("gate-lib.sh", "heartbeat-emitter.sh", "persona-alias-resolver.sh"):
            shutil.copy(HOOKS_DIR / name, scratch_hooks / name)
        (scratch_root / ".memory" / "files").mkdir(parents=True)
        result = subprocess.run(
            ["/bin/bash", str(scratch_hooks / "persona-alias-resolver.sh")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={**os.environ},
            timeout=15,
        )
        heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
        return result.returncode, result.stdout, result.stderr, heartbeat_path

    def test_no_subagent_type_emits_heartbeat_allow(self) -> None:
        code, out, _err, heartbeat_path = self._run_scratch({"tool_name": "TaskUpdate", "tool_input": {}})
        assert code == 0, "regression: silent-pass exit code unchanged"
        assert out.strip() == ""
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "persona-alias-resolver"
        assert hb["decision"] == "allow"

    def test_resolved_stale_name_emits_heartbeat_allow(self) -> None:
        code, out, _err, heartbeat_path = self._run_scratch(
            _task_payload("forge", "build app/components/WorkbookList.tsx")
        )
        assert code == 0, "regression: resolved-advisory exit code unchanged"
        assert "forge-ui" in _hook_out(out).get("additionalContext", "")
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["decision"] == "allow"

    def test_unresolvable_stale_name_emits_heartbeat_deny(self) -> None:
        code, out, _err, heartbeat_path = self._run_scratch(
            _task_payload("forge", "do something vague")
        )
        assert code == 2, "regression: deny exit code unchanged"
        assert _hook_out(out).get("permissionDecision") == "deny"
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "persona-alias-resolver"
        assert hb["decision"] == "deny"

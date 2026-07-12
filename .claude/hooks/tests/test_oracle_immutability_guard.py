"""R1-T02 regression + telemetry tests for
.claude/hooks/oracle-immutability-guard.sh (package twin — byte-identical to
the live copy at time of writing).

Denies Write/Edit/MultiEdit/NotebookEdit to a path matching the active task's
approved_brief.do_not_touch globs (read from broker_state.json).

R1-T02 additions: fire (hook_heartbeat.jsonl) telemetry on every exit path, and
block (gate_blocks.jsonl) telemetry on deny — this hook does its own inline
'jq -cn' deny and does not source gate-lib.sh, so its denies previously never
reached gate_blocks.jsonl.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "oracle-immutability-guard.sh"


def _state_with_do_not_touch(tmp_path: Path, globs: list[str]) -> Path:
    state_path = tmp_path / "broker_state.json"
    state_path.write_text(json.dumps({"approved_brief": {"do_not_touch": globs}}))
    return state_path


def _write_payload(path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def _hook_out(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


def _run(payload: dict, state_path: Path | None) -> tuple[int, str, str]:
    env = {**os.environ}
    if state_path is not None:
        env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _run_isolated(
    payload: dict, tmp_path: Path, state_path: Path | None
) -> tuple[int, str, str, Path, Path]:
    scratch_root = tmp_path / "repo"
    scratch_hooks = scratch_root / ".claude" / "hooks"
    scratch_hooks.mkdir(parents=True)
    for name in ("heartbeat-emitter.sh", "oracle-immutability-guard.sh"):
        shutil.copy(HOOKS_DIR / name, scratch_hooks / name)
    (scratch_root / ".memory" / "files").mkdir(parents=True)

    env = {**os.environ}
    if state_path is not None:
        env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    result = subprocess.run(
        ["/bin/bash", str(scratch_hooks / "oracle-immutability-guard.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
    gate_blocks_path = scratch_root / ".memory" / "files" / "gate_blocks.jsonl"
    return result.returncode, result.stdout, result.stderr, heartbeat_path, gate_blocks_path


class TestRegression:
    def test_write_to_protected_path_is_denied(self, tmp_path: Path) -> None:
        state = _state_with_do_not_touch(tmp_path, ["nexus-package/"])
        code, out, err = _run(_write_payload("nexus-package/install.sh"), state)
        assert code == 2
        ho = _hook_out(out)
        assert ho.get("permissionDecision") == "deny"
        assert "[GATE:ORACLE-IMMUTABILITY/WRITE-DENIED]" in ho.get("permissionDecisionReason", "")
        assert "[GATE:ORACLE-IMMUTABILITY/WRITE-DENIED]" in err

    def test_write_outside_do_not_touch_is_allowed(self, tmp_path: Path) -> None:
        state = _state_with_do_not_touch(tmp_path, ["nexus-package/"])
        code, out, err = _run(_write_payload("app/page.tsx"), state)
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

    def test_no_state_file_is_allowed(self, tmp_path: Path) -> None:
        code, out, _err = _run(_write_payload("app/page.tsx"), tmp_path / "absent.json")
        assert code == 0
        assert out.strip() == ""


class TestTelemetry:
    def test_denied_write_emits_heartbeat_and_gate_block(self, tmp_path: Path) -> None:
        state = _state_with_do_not_touch(tmp_path, ["nexus-package/"])
        code, out, _err, heartbeat_path, gate_blocks_path = _run_isolated(
            _write_payload("nexus-package/install.sh"), tmp_path, state
        )
        assert code == 2, "regression: deny exit code unchanged"
        assert _hook_out(out).get("permissionDecision") == "deny"

        assert heartbeat_path.exists()
        hb_lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(hb_lines) == 1
        hb = json.loads(hb_lines[0])
        assert hb["hook"] == "oracle-immutability-guard"
        assert hb["decision"] == "deny"

        assert gate_blocks_path.exists(), (
            "oracle-immutability-guard denies must now also reach gate_blocks.jsonl"
        )
        gb_lines = [ln for ln in gate_blocks_path.read_text().splitlines() if ln.strip()]
        assert len(gb_lines) == 1
        gb = json.loads(gb_lines[0])
        assert gb["hook"] == "oracle-immutability-guard"
        assert gb["code"] == "WRITE-DENIED"

    def test_allowed_write_emits_heartbeat_only(self, tmp_path: Path) -> None:
        state = _state_with_do_not_touch(tmp_path, ["nexus-package/"])
        code, out, err, heartbeat_path, gate_blocks_path = _run_isolated(
            _write_payload("app/page.tsx"), tmp_path, state
        )
        assert code == 0, "regression: allow exit code unchanged"
        assert out.strip() == ""
        assert err.strip() == ""

        hb_lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(hb_lines) == 1
        hb = json.loads(hb_lines[0])
        assert hb["hook"] == "oracle-immutability-guard"
        assert hb["decision"] == "allow"
        assert not gate_blocks_path.exists()

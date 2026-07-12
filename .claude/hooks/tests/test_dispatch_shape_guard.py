"""R1-T02 regression + telemetry tests for .claude/hooks/dispatch-shape-guard.sh
(package twin — byte-identical to the live copy at time of writing).

Default-deny backstop for the dispatch-shape parsing contract: Task|TeamCreate|
Agent payloads must resolve to a registered persona in the dispatchable-persona
roster or team_name; anything else denies loud.

R1-T02 additions: fire (hook_heartbeat.jsonl) telemetry on every exit path.
This hook already sources gate-lib.sh, so its denies already reach
gate_blocks.jsonl via gate_deny() — no separate block-telemetry gap here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "dispatch-shape-guard.sh"


def _run(payload: dict, env: dict | None = None) -> tuple[int, str, str]:
    merged = {**os.environ}
    if env:
        merged.update(env)
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
    def test_non_dispatch_tool_is_silent(self) -> None:
        code, out, err = _run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

    def test_known_persona_is_allowed(self) -> None:
        code, out, _err = _run({"tool_name": "Task", "tool_input": {"subagent_type": "hermes"}})
        assert code == 0
        assert out.strip() == ""

    def test_unknown_persona_is_denied(self) -> None:
        code, out, err = _run({"tool_name": "Task", "tool_input": {"subagent_type": "not-real"}})
        assert code == 2
        ho = _hook_out(out)
        assert ho.get("permissionDecision") == "deny"
        assert "[GATE:DISPATCH-SHAPE/UNRECOGNIZED]" in ho.get("permissionDecisionReason", "")
        assert "[GATE:DISPATCH-SHAPE/UNRECOGNIZED]" in err


# ─── R1-T02: fire telemetry on every exit path ──────────────────────────────


def _run_scratch(payload: dict, env: dict | None = None) -> tuple[int, str, str, Path]:
    tmp_path = Path(tempfile.mkdtemp())
    scratch_root = tmp_path / "repo"
    scratch_hooks = scratch_root / ".claude" / "hooks"
    scratch_hooks.mkdir(parents=True)
    for name in ("gate-lib.sh", "heartbeat-emitter.sh", "dispatch-shape-guard.sh"):
        shutil.copy(HOOKS_DIR / name, scratch_hooks / name)
    (scratch_root / ".memory" / "files").mkdir(parents=True)

    merged = {**os.environ}
    if env:
        merged.update(env)
    result = subprocess.run(
        ["/bin/bash", str(scratch_hooks / "dispatch-shape-guard.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged,
        timeout=15,
    )
    heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
    return result.returncode, result.stdout, result.stderr, heartbeat_path


class TestTelemetry:
    def test_allowed_dispatch_emits_heartbeat_allow(self) -> None:
        code, out, _err, heartbeat_path = _run_scratch(
            {"tool_name": "Task", "tool_input": {"subagent_type": "hermes"}}
        )
        assert code == 0, "regression: allow exit code unchanged"
        assert out.strip() == ""
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "dispatch-shape-guard"
        assert hb["decision"] == "allow"

    def test_denied_dispatch_emits_heartbeat_deny_and_gate_block(self, tmp_path: Path) -> None:
        sink = tmp_path / "gate_blocks.jsonl"
        code, out, _err, heartbeat_path = _run_scratch(
            {"tool_name": "Task", "tool_input": {"subagent_type": "not-real"}},
            env={"NEXUS_GATE_BLOCKS_PATH": str(sink)},
        )
        assert code == 2, "regression: deny exit code unchanged"
        assert _hook_out(out).get("permissionDecision") == "deny"

        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "dispatch-shape-guard"
        assert hb["decision"] == "deny"

        assert sink.exists(), "gate_deny() must still append to gate_blocks.jsonl (unchanged)"
        row = json.loads(sink.read_text().strip().splitlines()[-1])
        assert row["hook"] == "DISPATCH-SHAPE"
        assert row["code"] == "UNRECOGNIZED"

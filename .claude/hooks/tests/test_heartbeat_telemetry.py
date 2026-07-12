"""test_heartbeat_telemetry.py — R1-T02 workstream B: Python gate FIRE telemetry
(package tree).

Package-side twin of .claude/hooks/tests/test_heartbeat_telemetry.py, scoped to
the deployable copies under nexus-package/.claude/hooks/. These package copies
are hand-reconciled (never rsynced from the live tree — see
tools/build_snapshot.sh's header note), and three of them
(no-deferral-gate.sh, lens-gate.sh, root-cause-gate.sh) are SELF-CONTAINED —
no _gate_deny.py import — so their block-telemetry (gate_blocks.jsonl) had to
be added inline rather than inherited from a shared helper.

Run via R4e (tools/build_snapshot.sh:verify_hook_tests) from this directory's
parent: `cd nexus-package/.claude/hooks && pytest tests/ -q`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent


def _read_jsonl(path: str) -> list:
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _run_hook(hook_name: str, payload: dict, *, heartbeat_sink: str, blocks_sink: str, extra_env=None):
    env = {**os.environ}
    env["NEXUS_HEARTBEAT_PATH"] = heartbeat_sink
    env["NEXUS_GATE_BLOCKS_PATH"] = blocks_sink
    if extra_env:
        env.update(extra_env)
    hook_path = HOOKS_DIR / hook_name
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


@pytest.fixture
def sinks(tmp_path: Path):
    return str(tmp_path / "hook_heartbeat.jsonl"), str(tmp_path / "gate_blocks.jsonl")


_ALLOW_CASES = [
    ("broker-gate.py", {"tool_input": {}}),
    ("skills-required-guard.sh", {"tool_input": {}}),
    (
        "no-deferral-gate.sh",
        {"subagent_type": "hermes", "last_assistant_message": "all good, no issues"},
    ),
    (
        "lens-gate.sh",
        {"subagent_type": "hermes", "last_assistant_message": "still working"},
    ),
    (
        "root-cause-gate.sh",
        {"subagent_type": "hermes", "last_assistant_message": "still working"},
    ),
]


@pytest.mark.parametrize("hook_name,payload", _ALLOW_CASES, ids=[c[0] for c in _ALLOW_CASES])
def test_package_hook_emits_one_heartbeat_row_on_allow(hook_name, payload, sinks) -> None:
    hb_sink, gb_sink = sinks
    assert (HOOKS_DIR / hook_name).exists(), f"hook not found: {HOOKS_DIR / hook_name}"
    proc = _run_hook(hook_name, payload, heartbeat_sink=hb_sink, blocks_sink=gb_sink)
    rows = _read_jsonl(hb_sink)
    assert len(rows) == 1, f"expected 1 heartbeat row, got {len(rows)}: {rows}"
    row = rows[0]
    assert set(row.keys()) == {"ts", "hook", "event", "decision", "latency_ms"}
    assert row["decision"] == "allow"
    assert proc.returncode == 0, f"expected allow (exit 0), got {proc.returncode}: {proc.stderr}"


def test_package_no_deferral_gate_deny_emits_block_heartbeat_and_gate_block(sinks) -> None:
    """Self-contained package build — block-telemetry was added inline (no
    _gate_deny.py). This is the R1-T02 step-3 regression guard."""
    hb_sink, gb_sink = sinks
    payload = {
        "subagent_type": "hermes",
        "last_assistant_message": "I found a bug but will fix it separately in a follow-up.",
    }
    # Shadow-mode-first (N12 §4): exercise the enforced (calibration-flag-on) path.
    env = {"NEXUS_NO_DEFERRAL_ENFORCE": "1"}
    proc = _run_hook(
        "no-deferral-gate.sh", payload, heartbeat_sink=hb_sink, blocks_sink=gb_sink, extra_env=env
    )
    assert proc.returncode == 2, f"expected deny (exit 2), got {proc.returncode}: {proc.stderr}"
    hb_rows = _read_jsonl(hb_sink)
    assert len(hb_rows) == 1
    assert hb_rows[0]["decision"] == "block"
    gb_rows = _read_jsonl(gb_sink)
    assert len(gb_rows) == 1, f"expected 1 gate_blocks row, got {gb_rows}"
    assert gb_rows[0]["hook"] == "DEFER"
    assert gb_rows[0]["code"] == "FIX-DEFERRED"


def test_package_lens_gate_revise_no_criterion_deny_emits_block_heartbeat_and_gate_block(sinks) -> None:
    """Self-contained package build — the REVISE-no-criterion path is reachable
    without needing /app/apps/, /app/packages/ rendered or a validation_log row."""
    hb_sink, gb_sink = sinks
    payload = {
        "subagent_type": "lens",
        "last_assistant_message": "## NEXUS:REVISE\nsomething is wrong, no criterion here",
    }
    proc = _run_hook("lens-gate.sh", payload, heartbeat_sink=hb_sink, blocks_sink=gb_sink)
    assert proc.returncode == 2, f"expected deny (exit 2), got {proc.returncode}: {proc.stderr}"
    hb_rows = _read_jsonl(hb_sink)
    assert len(hb_rows) == 1
    assert hb_rows[0]["decision"] == "block"
    gb_rows = _read_jsonl(gb_sink)
    assert len(gb_rows) == 1, f"expected 1 gate_blocks row, got {gb_rows}"
    assert gb_rows[0]["hook"] == "LENS"
    assert gb_rows[0]["code"] == "REVISE-NO-CRITERION"


def test_package_skills_required_guard_deny_unchanged_exit_code(sinks) -> None:
    """Regression guard: telemetry wiring must not change this gate's exit code."""
    hb_sink, gb_sink = sinks
    payload = {"tool_input": {"subagent_type": "hermes", "description": "{}"}}
    proc = _run_hook(
        "skills-required-guard.sh", payload, heartbeat_sink=hb_sink, blocks_sink=gb_sink
    )
    assert proc.returncode == 2, f"expected deny (exit 2), got {proc.returncode}: {proc.stderr}"
    hb_rows = _read_jsonl(hb_sink)
    assert len(hb_rows) == 1
    assert hb_rows[0]["decision"] == "block"


def test_package_broker_gate_missing_state_unchanged_exit_code(sinks, tmp_path: Path) -> None:
    """Regression guard: fail-closed exit 2 behavior must survive instrumentation."""
    hb_sink, gb_sink = sinks
    (tmp_path / ".memory" / "files").mkdir(parents=True, exist_ok=True)
    env = {
        "_HOOK_REPO_ROOT": str(tmp_path),
        "NEXUS_BROKER_STATE_PATH": str(tmp_path / "absent.json"),
    }
    proc = _run_hook(
        "broker-gate.py",
        {"tool_input": {"subagent_type": "hermes"}},
        heartbeat_sink=hb_sink,
        blocks_sink=gb_sink,
        extra_env=env,
    )
    assert proc.returncode == 2, f"expected deny (exit 2), got {proc.returncode}: {proc.stderr}"
    hb_rows = _read_jsonl(hb_sink)
    assert len(hb_rows) == 1
    assert hb_rows[0]["decision"] == "block"

"""test_gate_stats.py — R1-T02 workstream D: `gate stats` aggregation layer.

Covers the CLI subcommand `python3 .memory/log.py gate stats` that JOINs the
two raw telemetry sinks (hook_heartbeat.jsonl fires, gate_blocks.jsonl denies)
grouped by hook name. Pure read-only aggregation — no project.db involved.

Acceptance:
  1. Missing/empty JSONL files -> empty stats, no crash (best-effort).
  2. Synthetic fixture of both files -> correct fire/block/rate/latency math.
  3. --since filters both files to ts >= the given ISO8601 timestamp.
  4. --hook filters to a single hook name.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_LOG_PY: Path = _REPO_ROOT / ".memory" / "log.py"


def _load_log_module():
    """Import .memory/log.py directly to exercise compute_gate_stats() in-process."""
    mod_name = "nexus_log_gate_stats"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _LOG_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _run(
    *args: str,
    blocks_path: Path,
    heartbeat_path: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "NEXUS_GATE_BLOCKS_PATH": str(blocks_path),
        "NEXUS_HEARTBEAT_PATH": str(heartbeat_path),
        "NEXUS_DISABLE_VEC": "1",
    }
    return subprocess.run(
        [sys.executable, str(_LOG_PY), "gate", "stats", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


HEARTBEAT_FIXTURE = [
    {"ts": "2026-07-01T00:00:00Z", "hook": "broker-gate", "event": "PreToolUse", "decision": "allow", "latency_ms": 10},
    {"ts": "2026-07-01T00:01:00Z", "hook": "broker-gate", "event": "PreToolUse", "decision": "deny", "latency_ms": 20},
    {"ts": "2026-07-01T00:02:00Z", "hook": "broker-gate", "event": "PreToolUse", "decision": "allow", "latency_ms": 30},
    {"ts": "2026-07-02T00:00:00Z", "hook": "socraticode-gate", "event": "PreToolUse", "decision": "allow", "latency_ms": 5},
]

BLOCKS_FIXTURE = [
    {"ts": "2026-07-01T00:01:00Z", "event": "PreToolUse", "hook": "broker-gate", "code": "NO_BRIEF", "reason": "missing brief"},
]


# ---------------------------------------------------------------------------
# AC-1: missing/empty files -> empty stats, no crash
# ---------------------------------------------------------------------------


def test_missing_files_yield_empty_stats_no_crash(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    # neither file created

    result = _run(blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "No gate telemetry recorded." in result.stdout


def test_missing_files_yield_empty_json(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"

    result = _run("--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hooks"] == {}


def test_compute_gate_stats_empty_dataset_in_process(tmp_path: Path) -> None:
    mod = _load_log_module()
    stats = mod.compute_gate_stats(tmp_path / "nope.jsonl", tmp_path / "nope2.jsonl")
    assert stats["hooks"] == {}


# ---------------------------------------------------------------------------
# AC-2: synthetic fixture -> correct fire/block/rate/latency computation
# ---------------------------------------------------------------------------


def test_fixture_computes_correct_aggregates(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, BLOCKS_FIXTURE)

    result = _run("--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)

    broker = payload["hooks"]["broker-gate"]
    assert broker["fire_count"] == 3
    assert broker["block_count"] == 1
    assert broker["block_rate"] == pytest.approx(1 / 3)
    assert broker["avg_latency_ms"] == pytest.approx(20.0)  # (10+20+30)/3

    socrati = payload["hooks"]["socraticode-gate"]
    assert socrati["fire_count"] == 1
    assert socrati["block_count"] == 0
    assert socrati["block_rate"] == 0.0
    assert socrati["avg_latency_ms"] == pytest.approx(5.0)



def test_fixture_table_output_sorted_by_fire_count_desc(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, BLOCKS_FIXTURE)

    result = _run(blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0
    lines = [ln for ln in result.stdout.splitlines() if "gate" in ln and "hook" not in ln]
    # broker-gate (3 fires) must appear before socraticode-gate (1 fire)
    broker_idx = next(i for i, ln in enumerate(lines) if "broker-gate" in ln)
    socrati_idx = next(i for i, ln in enumerate(lines) if "socraticode-gate" in ln)
    assert broker_idx < socrati_idx


def test_hook_with_no_fires_but_blocks_has_zero_block_rate(tmp_path: Path) -> None:
    """block_rate must be 0 (not a crash) when fire_count is 0 for a hook that only appears in blocks."""
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, [])
    _write_jsonl(blocks, [
        {"ts": "2026-07-01T00:00:00Z", "event": "PreToolUse", "hook": "ghost-gate", "code": "X", "reason": "r"},
    ])

    result = _run("--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    ghost = payload["hooks"]["ghost-gate"]
    assert ghost["fire_count"] == 0
    assert ghost["block_count"] == 1
    assert ghost["block_rate"] == 0.0


def test_malformed_lines_are_skipped_not_fatal(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    heartbeat.write_text(
        '{"ts":"2026-07-01T00:00:00Z","hook":"broker-gate","event":"PreToolUse","decision":"allow","latency_ms":10}\n'
        "not valid json\n"
        "\n"
    )
    blocks.write_text("")

    result = _run("--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout)
    assert payload["hooks"]["broker-gate"]["fire_count"] == 1


# ---------------------------------------------------------------------------
# AC-3: --since filters both JSONL files
# ---------------------------------------------------------------------------


def test_since_filters_to_rows_at_or_after_timestamp(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, BLOCKS_FIXTURE)

    result = _run("--since", "2026-07-02T00:00:00Z", "--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert "broker-gate" not in payload["hooks"]
    assert payload["hooks"]["socraticode-gate"]["fire_count"] == 1


def test_since_boundary_is_inclusive(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, [])

    result = _run("--since", "2026-07-01T00:01:00Z", "--json", blocks_path=blocks, heartbeat_path=heartbeat)
    payload = json.loads(result.stdout)
    # rows at exactly 00:01:00 and 00:02:00 survive -> 2 fires for broker-gate
    assert payload["hooks"]["broker-gate"]["fire_count"] == 2


# ---------------------------------------------------------------------------
# AC-4: --hook filters to a single hook name
# ---------------------------------------------------------------------------


def test_hook_filter_isolates_single_hook(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, BLOCKS_FIXTURE)

    result = _run("--hook", "socraticode-gate", "--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert list(payload["hooks"].keys()) == ["socraticode-gate"]
    assert payload["hooks"]["socraticode-gate"]["fire_count"] == 1


def test_hook_filter_nonexistent_hook_yields_empty(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, BLOCKS_FIXTURE)

    result = _run("--hook", "does-not-exist", "--json", blocks_path=blocks, heartbeat_path=heartbeat)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["hooks"] == {}


def test_since_and_hook_combined(tmp_path: Path) -> None:
    blocks = tmp_path / "gate_blocks.jsonl"
    heartbeat = tmp_path / "hook_heartbeat.jsonl"
    _write_jsonl(heartbeat, HEARTBEAT_FIXTURE)
    _write_jsonl(blocks, BLOCKS_FIXTURE)

    result = _run(
        "--since", "2026-07-01T00:01:00Z", "--hook", "broker-gate", "--json",
        blocks_path=blocks, heartbeat_path=heartbeat,
    )
    payload = json.loads(result.stdout)
    broker = payload["hooks"]["broker-gate"]
    assert broker["fire_count"] == 2  # 00:01 and 00:02 rows only
    assert broker["block_count"] == 1  # the one block row is at 00:01, in-window

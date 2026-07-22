"""test_hook_envelope_contracts.py — NEX-001 (DEC-100 pillar 4): schema/interface
contract tests for the hook JSON envelopes the broker consumes.

Every PreToolUse-deny / advisory-and-SubagentStop hook in this repo emits the
SAME `{"hookSpecificOutput": {...}}` shape via `.claude/hooks/_gate_deny.py`'s
`deny()` / `advise()` (or a hand-inlined byte-identical copy, e.g.
dispatch-capture.py's `_redispatch_advisory`, return-validator.py's
`_emit_advisory`) — broker-gate.py is the load-bearing consumer: a shape drift
here is exactly what would silently break the harness's own JSON-envelope
parsing. These tests validate the REAL emitted JSON (never a re-implemented
string) against the explicit schema in `schemas.py`, derived from
`_gate_deny.py`'s own literal `_emit()` payloads.

Two layers are exercised:
  1. `_gate_deny.py`'s `deny()`/`advise()` called directly (library-level
     shape, in-process, output captured via capsys).
  2. `broker-gate.py` and `return-validator.py` driven as real subprocesses
     (end-to-end proof that a LIVE hook's stdout matches the same schema).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from .schemas import HOOK_ADVISE_ENVELOPE_SCHEMA, HOOK_DENY_ENVELOPE_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
GATE_DENY_PY = HOOKS_DIR / "_gate_deny.py"
BROKER_GATE = HOOKS_DIR / "broker-gate.py"
RETURN_VALIDATOR = HOOKS_DIR / "return-validator.py"


def _load_gate_deny_module():
    spec = importlib.util.spec_from_file_location("_gate_deny_contract", GATE_DENY_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _parse_stdout(text: str) -> dict:
    stripped = text.strip()
    assert stripped, "expected non-empty JSON stdout"
    return json.loads(stripped)


# ---------------------------------------------------------------------------
# Layer 1 — _gate_deny.py deny()/advise() library-level shape
# ---------------------------------------------------------------------------

def test_gate_deny_py_deny_matches_envelope_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given: any hard-deny call.
    When: _gate_deny.deny() emits its stdout JSON.
    Then: the parsed object validates against HOOK_DENY_ENVELOPE_SCHEMA.
    """
    monkeypatch.setenv("NEXUS_GATE_BLOCKS_PATH", str(tmp_path / "gate_blocks.jsonl"))
    module = _load_gate_deny_module()
    exit_code = module.deny("PreToolUse", "BROKER/DISPATCH-BLOCKED", "contract test reason")
    out = capsys.readouterr().out
    obj = _parse_stdout(out)
    jsonschema.validate(obj, HOOK_DENY_ENVELOPE_SCHEMA)
    assert exit_code == 2


def test_gate_deny_py_advise_matches_envelope_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given: any advisory (non-blocking) call.
    When: _gate_deny.advise() emits its stdout JSON.
    Then: the parsed object validates against HOOK_ADVISE_ENVELOPE_SCHEMA
          (and therefore carries no permissionDecision — additionalProperties
          is false on the inner object).
    """
    module = _load_gate_deny_module()
    exit_code = module.advise("SubagentStop", "DEFER/WARN", "contract test advisory")
    out = capsys.readouterr().out
    obj = _parse_stdout(out)
    jsonschema.validate(obj, HOOK_ADVISE_ENVELOPE_SCHEMA)
    assert exit_code == 0


# ---------------------------------------------------------------------------
# Layer 2 — broker-gate.py driven live (PreToolUse deny + advise)
# ---------------------------------------------------------------------------

def _run_broker_gate(payload: dict, *, state_path: Path, db_path: Path, allow_degraded: bool = False):
    # Isolation (Lens REVISE on the original NEX-001 leg): without _HOOK_REPO_ROOT,
    # block()/allow_with_warning() resolve _gate_deny._repo_root() to THIS repo,
    # so emit_gate_span's span.emit RPC targets whatever resident daemon is live
    # for it — a real trace_id="S-contract-test" span lands in the LIVE
    # .memory/spans.duckdb. state_path.parent (a fresh tmp_path per test) both
    # redirects _repo_root() AND — via a dedicated empty socket dir — guarantees
    # _daemon_rpc.socket_path()'s digest can never resolve to a real daemon's
    # socket, mirroring test_install_selfverify.py's daemon-absent isolation.
    repo_root = state_path.parent
    empty_socket_dir = repo_root / "no-daemon-sockets"
    empty_socket_dir.mkdir(exist_ok=True)
    env = {**os.environ}
    env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    env["_HOOK_DB_PATH"] = str(db_path)
    env["_HOOK_REPO_ROOT"] = str(repo_root)
    env["NEXUS_DAEMON_SOCKET_DIR"] = str(empty_socket_dir)
    env.pop("NEXUS_BROKER_ALLOW_DEGRADED", None)
    if allow_degraded:
        env["NEXUS_BROKER_ALLOW_DEGRADED"] = "1"
    return subprocess.run(
        [sys.executable, str(BROKER_GATE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )


def _task_payload(persona: str = "forge-wire") -> dict:
    brief = {
        "goal": "do a thing",
        "context_files": ["a.py"],
        "acceptance_criteria": ["it works"],
        "do_not_touch": ["nexus-package/"],
        "task_tier": "standard",
        "notepad_topic": "TASK-nex-001",
    }
    return {
        "tool_name": "Task",
        "input": {
            "subagent_type": persona,
            "description": "```json\n" + json.dumps(brief) + "\n```",
        },
        "session_id": "S-contract-test",
    }


def test_broker_gate_missing_state_deny_matches_envelope_schema(tmp_path: Path) -> None:
    """Given: broker_state.json does not exist (fail-CLOSED per P2-10).
    When: broker-gate.py evaluates a Task dispatch.
    Then: the deny JSON on stdout validates against HOOK_DENY_ENVELOPE_SCHEMA.
    """
    proc = _run_broker_gate(
        _task_payload(),
        state_path=tmp_path / "absent.json",
        db_path=tmp_path / "project.db",
    )
    assert proc.returncode == 2, f"stderr={proc.stderr!r}"
    obj = _parse_stdout(proc.stdout)
    jsonschema.validate(obj, HOOK_DENY_ENVELOPE_SCHEMA)
    assert obj["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_broker_gate_degraded_allow_advise_matches_envelope_schema(tmp_path: Path) -> None:
    """Given: broker_state.json missing AND NEXUS_BROKER_ALLOW_DEGRADED=1.
    When: broker-gate.py evaluates a Task dispatch.
    Then: the Task is allowed (exit 0) and the LOUD warning JSON on stdout
          validates against HOOK_ADVISE_ENVELOPE_SCHEMA.
    """
    proc = _run_broker_gate(
        _task_payload(),
        state_path=tmp_path / "absent.json",
        db_path=tmp_path / "project.db",
        allow_degraded=True,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    obj = _parse_stdout(proc.stdout)
    jsonschema.validate(obj, HOOK_ADVISE_ENVELOPE_SCHEMA)
    assert obj["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


# ---------------------------------------------------------------------------
# Layer 2b — return-validator.py driven live (SubagentStop advisory)
# ---------------------------------------------------------------------------

def _subagentstop_payload(
    marker: str = "NEXUS:DONE", agent: str = "forge-ui", extra: str = ""
) -> str:
    content = f"## {marker}\n{extra}"
    return json.dumps(
        {
            "hook_event_name": "SubagentStop",
            "stop_hook_active": True,
            "session_id": "S-contract-return-validator",
            "last_assistant_message": content,
            "agent_persona": agent,
            "task_description": "",
            "files_changed": [],
        }
    )


def test_return_validator_evidence_absent_advisory_matches_envelope_schema() -> None:
    """Given: a `## NEXUS:DONE` return with NO verification_result field and
              no verbatim passing block anywhere in the text.
    When: return-validator.py evaluates the SubagentStop payload.
    Then: it exits 0 (fail-soft) and the advisory JSON on stdout validates
          against HOOK_ADVISE_ENVELOPE_SCHEMA.
    """
    payload = _subagentstop_payload(extra="Finished the task, everything looks good.")
    proc = subprocess.run(
        [sys.executable, str(RETURN_VALIDATOR)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    obj = _parse_stdout(proc.stdout)
    jsonschema.validate(obj, HOOK_ADVISE_ENVELOPE_SCHEMA)
    assert obj["hookSpecificOutput"]["hookEventName"] == "SubagentStop"

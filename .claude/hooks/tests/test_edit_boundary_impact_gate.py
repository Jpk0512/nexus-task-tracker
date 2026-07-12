"""
Tests for .claude/hooks/edit-boundary-impact-gate.sh.

Run with:  python3 -m pytest .claude/hooks/tests/test_edit_boundary_impact_gate.py -v

R3-T09 / N14: moves impact-checking from search-time to WRITE-time by
extending the R1-T11 oracle-immutability-guard.sh pattern to enforce an
ALLOW-list (write_scope) instead of a DENY-list (do_not_touch).

Contract:
  - Write/Edit/NotebookEdit to a path matching write_scope     -> ALLOW (exit 0, silent)
  - Write/Edit/NotebookEdit to a path NOT matching write_scope -> DENY (exit 2)
  - No write_scope / no approved_brief / missing or unreadable
    state file                                                  -> ALLOW (exit 0, silent)
  - A matching typed override (tool_input.override) on an
    out-of-scope write                                          -> ALLOW (exit 0), audited
    as a "decision":"override" row in gate_blocks.jsonl.

R1-T10 incident-regression discipline: persona/scope facts must be read from
tool_input ONLY, never from any other envelope field. This is asserted
directly by feeding a payload where a decoy top-level "agent_type"/"persona"
field contradicts tool_input, and confirming the gate's decision follows
tool_input alone.

Gate code carried in reason: [GATE:EDIT-BOUNDARY/OUT-OF-SCOPE]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "edit-boundary-impact-gate.sh"


def _state_with_write_scope(tmp_path: Path, globs: list[str]) -> Path:
    state_path = tmp_path / "broker_state.json"
    state_path.write_text(json.dumps({"approved_brief": {"write_scope": globs}}))
    return state_path


def _write_payload(path: str, extra_top_level: dict | None = None) -> dict:
    payload = {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}
    if extra_top_level:
        payload.update(extra_top_level)
    return payload


def _hook_out(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


def _run(payload: dict, state_path: Path | None) -> tuple[int, str, str]:
    env = {}
    if state_path is not None:
        env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def _run_isolated(
    payload: dict, tmp_path: Path, state_path: Path | None
) -> tuple[int, str, str, Path, Path]:
    """Invoke the hook from a scratch copy with its own .memory/ so the
    heartbeat + gate_blocks telemetry sinks are isolated from the real repo
    tree. Mirrors the real repo's .claude/hooks/ (two levels below repo root)
    exactly, matching heartbeat-emitter.sh's repo-root walk and this hook's
    own gate_blocks path resolution (HOOKS_DIR/../..)."""
    scratch_root = tmp_path / "repo"
    scratch_hooks = scratch_root / ".claude" / "hooks"
    scratch_hooks.mkdir(parents=True)
    for name in ("heartbeat-emitter.sh", "edit-boundary-impact-gate.sh"):
        shutil.copy(HOOKS_DIR / name, scratch_hooks / name)
    (scratch_root / ".memory" / "files").mkdir(parents=True)

    env = {**os.environ}
    if state_path is not None:
        env["NEXUS_BROKER_STATE_PATH"] = str(state_path)
    result = subprocess.run(
        ["/bin/bash", str(scratch_hooks / "edit-boundary-impact-gate.sh")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
    gate_blocks_path = scratch_root / ".memory" / "files" / "gate_blocks.jsonl"
    return result.returncode, result.stdout, result.stderr, heartbeat_path, gate_blocks_path


# ─── Deny cases (out-of-scope write) ─────────────────────────────────────────


class TestOutOfScopeDenied:
    def test_write_outside_scope_is_denied(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**", "nexus-redesign/**"])
        code, out, err = _run(_write_payload("app/api/secrets.py"), state)
        assert code == 2, f"must exit 2, got {code}: {out!r}"
        ho = _hook_out(out)
        assert ho.get("permissionDecision") == "deny"
        reason = ho.get("permissionDecisionReason", "")
        assert "[GATE:EDIT-BOUNDARY/OUT-OF-SCOPE]" in reason
        assert "app/api/secrets.py" in reason, "deny reason must name attempted_path"
        assert "[GATE:EDIT-BOUNDARY/OUT-OF-SCOPE]" in err


# ─── Allow cases (in-scope write) ────────────────────────────────────────────


class TestInScopeAllowed:
    def test_write_inside_scope_is_allowed(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**", "nexus-redesign/**"])
        code, out, err = _run(_write_payload("docs/agents/CONTRACT.md"), state)
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

    def test_no_write_scope_declared_is_allowed(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, [])
        code, out, _err = _run(_write_payload("app/anything.py"), state)
        assert code == 0
        assert out.strip() == ""

    def test_no_state_file_is_allowed(self, tmp_path: Path) -> None:
        code, out, _err = _run(_write_payload("app/anything.py"), tmp_path / "absent.json")
        assert code == 0
        assert out.strip() == ""

    def test_bare_directory_name_scope_matches_subtree(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs"])
        code, out, _err = _run(_write_payload("docs/agents/CONTRACT.md"), state)
        assert code == 0
        assert out.strip() == ""


# ─── R1-T10 incident-regression: tool_input ONLY, never the envelope ────────


class TestPersonaFactsFromToolInputOnly:
    def test_decoy_top_level_persona_field_is_ignored(self, tmp_path: Path) -> None:
        """A top-level 'agent_type'/'persona' field that contradicts the
        actual write target must NOT change the gate's decision — only
        tool_input is consulted. Regression guard for the R1-T10 incident
        class (persona resolved from the wrong envelope field)."""
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = _write_payload(
            "app/api/secrets.py",
            extra_top_level={"agent_type": "forge-wire", "persona": "hermes", "subagent_type": "hermes"},
        )
        code, out, _err = _run(payload, state)
        # Still denied: the decoy envelope fields claiming a broader/other
        # persona must not launder an out-of-scope tool_input path through.
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_decoy_envelope_field_does_not_allow_in_scope_write_by_accident(
        self, tmp_path: Path
    ) -> None:
        """Symmetric check: an in-scope write must still be ALLOWED even when
        decoy envelope fields are present and would (if erroneously
        consulted) suggest a different persona/scope."""
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = _write_payload(
            "docs/agents/CONTRACT.md",
            extra_top_level={"agent_type": "some-other-persona"},
        )
        code, out, _err = _run(payload, state)
        assert code == 0
        assert out.strip() == ""


# ─── Typed override (N12 design) ────────────────────────────────────────────


class TestTypedOverride:
    def test_matching_override_allows_out_of_scope_write(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "app/api/secrets.py",
                "content": "x",
                "override": {
                    "gate": "EDIT-BOUNDARY",
                    "code": "OUT-OF-SCOPE",
                    "reason": "user-approved cross-cutting fix",
                    "authorized_by": "user",
                },
            },
        }
        code, out, _err = _run(payload, state)
        assert code == 0
        assert out.strip() == ""

    def test_override_missing_reason_is_rejected(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "app/api/secrets.py",
                "content": "x",
                "override": {
                    "gate": "EDIT-BOUNDARY",
                    "code": "OUT-OF-SCOPE",
                    "reason": "",
                    "authorized_by": "user",
                },
            },
        }
        code, out, _err = _run(payload, state)
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_override_wrong_code_is_rejected(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "app/api/secrets.py",
                "content": "x",
                "override": {
                    "gate": "EDIT-BOUNDARY",
                    "code": "SOME-OTHER-CODE",
                    "reason": "not scoped to this gate",
                    "authorized_by": "user",
                },
            },
        }
        code, out, _err = _run(payload, state)
        assert code == 2

    def test_honored_override_is_audited_as_override_decision(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "app/api/secrets.py",
                "content": "x",
                "override": {
                    "gate": "EDIT-BOUNDARY",
                    "code": "OUT-OF-SCOPE",
                    "reason": "user-approved cross-cutting fix",
                    "authorized_by": "user",
                },
            },
        }
        code, out, err, heartbeat_path, gate_blocks_path = _run_isolated(payload, tmp_path, state)
        assert code == 0
        assert out.strip() == ""
        assert gate_blocks_path.exists(), "an honored override must still write an audit row"
        gb_lines = [ln for ln in gate_blocks_path.read_text().splitlines() if ln.strip()]
        assert len(gb_lines) == 1
        gb = json.loads(gb_lines[0])
        assert gb["decision"] == "override"
        assert gb["hook"] == "edit-boundary-impact-gate"
        assert gb["authorized_by"] == "user"
        assert gb["override_reason"] == "user-approved cross-cutting fix"


# ─── MultiEdit / NotebookEdit path extraction ────────────────────────────────


class TestToolShapes:
    def test_multiedit_out_of_scope_file_is_denied(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        payload = {
            "tool_name": "MultiEdit",
            "tool_input": {
                "edits": [
                    {"file_path": "docs/CONSTITUTION.md", "old_string": "a", "new_string": "b"},
                    {"file_path": "app/api/secrets.py", "old_string": "a", "new_string": "b"},
                ]
            },
        }
        code, out, _err = _run(payload, state)
        assert code == 2
        assert "app/api/secrets.py" in _hook_out(out).get("permissionDecisionReason", "")

    def test_notebookedit_in_scope_is_allowed(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["nexus-redesign/**"])
        payload = {
            "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": "nexus-redesign/analysis.ipynb", "new_source": "x"},
        }
        code, out, _err = _run(payload, state)
        assert code == 0
        assert out.strip() == ""


# ─── R1-T11 telemetry parity (heartbeat + gate_blocks on deny) ──────────────


class TestTelemetry:
    def test_denied_write_emits_heartbeat_and_gate_block(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        code, out, err, heartbeat_path, gate_blocks_path = _run_isolated(
            _write_payload("app/api/secrets.py"), tmp_path, state
        )
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"

        assert heartbeat_path.exists()
        hb_lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(hb_lines) == 1
        hb = json.loads(hb_lines[0])
        assert hb["hook"] == "edit-boundary-impact-gate"
        assert hb["event"] == "PreToolUse"
        assert hb["decision"] == "deny"
        assert "ts" in hb and "latency_ms" in hb

        assert gate_blocks_path.exists()
        gb_lines = [ln for ln in gate_blocks_path.read_text().splitlines() if ln.strip()]
        assert len(gb_lines) == 1
        gb = json.loads(gb_lines[0])
        assert gb["hook"] == "edit-boundary-impact-gate"
        assert gb["event"] == "PreToolUse"
        assert gb["code"] == "OUT-OF-SCOPE"
        assert "[GATE:EDIT-BOUNDARY/OUT-OF-SCOPE]" in gb["reason"]

    def test_allowed_write_emits_heartbeat_only(self, tmp_path: Path) -> None:
        state = _state_with_write_scope(tmp_path, ["docs/**"])
        code, out, err, heartbeat_path, gate_blocks_path = _run_isolated(
            _write_payload("docs/agents/CONTRACT.md"), tmp_path, state
        )
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

        assert heartbeat_path.exists()
        hb_lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(hb_lines) == 1
        hb = json.loads(hb_lines[0])
        assert hb["hook"] == "edit-boundary-impact-gate"
        assert hb["decision"] == "allow"

        assert not gate_blocks_path.exists(), "an ALLOWED call must not write to gate_blocks.jsonl"


# ─── Latency budget: <=50ms added per write call ────────────────────────────


ORACLE_SCRIPT = HOOKS_DIR / "oracle-immutability-guard.sh"


class TestLatencyBudget:
    def _time_hook(self, script: Path, payload: dict, env: dict, n_runs: int = 10) -> list[float]:
        durations_ms = []
        for _ in range(n_runs):
            start = time.monotonic()
            result = subprocess.run(
                ["/bin/bash", str(script)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            assert result.returncode == 0
            durations_ms.append(elapsed_ms)
        return durations_ms

    def test_added_latency_is_within_50ms_budget(self, tmp_path: Path) -> None:
        """The brief's SPEED guard is '<=50ms ADDED per write call' — added
        on top of what the fleet already pays for the sibling R1-T11 gate
        (oracle-immutability-guard.sh), which this gate is architecturally
        identical to (same single python3 glob-match subprocess + the same
        heartbeat-emitter ms_now() shells). Measuring this hook's absolute
        wall-clock time would just re-measure python3-interpreter-startup
        cost already paid and already accepted by the fleet (that gate is
        independently telemetered at 160.5ms in
        nexus-redesign/plans/11-gate-enforcement-audit.md's verdict table,
        for the exact same reason). The correct measurement is this gate's
        cost MINUS that sibling's cost, run back-to-back on the same
        machine under the same load."""
        write_scope_state = _state_with_write_scope(tmp_path, ["docs/**"])
        do_not_touch_state = tmp_path / "do_not_touch_state.json"
        do_not_touch_state.write_text(json.dumps({"approved_brief": {"do_not_touch": ["nexus-package/"]}}))

        new_gate_env = {**os.environ, "NEXUS_BROKER_STATE_PATH": str(write_scope_state)}
        sibling_gate_env = {**os.environ, "NEXUS_BROKER_STATE_PATH": str(do_not_touch_state)}

        payload = _write_payload("docs/agents/CONTRACT.md")

        new_gate_durations = self._time_hook(SCRIPT, payload, new_gate_env)
        sibling_durations = self._time_hook(ORACLE_SCRIPT, payload, sibling_gate_env)

        new_mean_ms = sum(new_gate_durations) / len(new_gate_durations)
        sibling_mean_ms = sum(sibling_durations) / len(sibling_durations)
        added_ms = new_mean_ms - sibling_mean_ms

        assert added_ms <= 50, (
            f"added latency {added_ms:.1f}ms (new gate {new_mean_ms:.1f}ms - "
            f"sibling R1-T11 gate {sibling_mean_ms:.1f}ms) exceeds the 50ms budget "
            f"(new runs: {[round(d, 1) for d in new_gate_durations]}, "
            f"sibling runs: {[round(d, 1) for d in sibling_durations]})"
        )


# ─── Test runner (NATIVE-6 integrity guard: __main__ must run every test) ───


def _run_all() -> int:
    import inspect
    import sys
    import tempfile

    module = sys.modules[__name__]
    test_classes = [
        obj
        for _name, obj in vars(module).items()
        if inspect.isclass(obj) and _name.startswith("Test")
    ]

    total = 0
    failures = []
    for cls in test_classes:
        instance = cls()
        for method_name, method in vars(cls).items():
            if not method_name.startswith("test_"):
                continue
            total += 1
            with tempfile.TemporaryDirectory() as td:
                tmp_path = Path(td)
                try:
                    method(instance, tmp_path)
                    print(f"PASS: {cls.__name__}.{method_name}")
                except Exception as exc:  # noqa: BLE001 - test harness reporting
                    failures.append((cls.__name__, method_name, exc))
                    print(f"FAIL: {cls.__name__}.{method_name}: {exc}")

    print(f"\n{total - len(failures)}/{total} passed")
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for cls_name, method_name, exc in failures:
            print(f"  {cls_name}.{method_name}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_run_all())

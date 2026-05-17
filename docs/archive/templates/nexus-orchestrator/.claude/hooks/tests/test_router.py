"""
Failing stubs for Phase E1: router.py + router_core.py (single-stage Qwen classifier).

Run with:  python3 -m pytest .claude/hooks/tests/test_router.py -v

router.py does NOT exist yet (Phase E1 pending). These stubs:
- Import from the eventual final path (router.py in .claude/hooks/)
- Assert the expected behaviour with real types
- All produce FAIL (pytest.fail) because the module doesn't exist

After Phase E1 ships, these should all PASS.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
REPO_ROOT = HOOKS_DIR.parent.parent
ROUTER_SCRIPT = HOOKS_DIR / "router.py"
ROUTER_CORE_MODULE = HOOKS_DIR / "router_core.py"
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

THRESHOLD_LLM = 0.85


def _skip_if_not_implemented(label: str = "") -> None:
    """Fail with a clear message when the router module doesn't exist."""
    if not ROUTER_SCRIPT.exists():
        pytest.fail(
            f"router.py not found at {ROUTER_SCRIPT} — Phase E1 not implemented"
            + (f" [{label}]" if label else "")
        )


def _run_router(
    user_prompt: str,
    extra_env: dict | None = None,
    files_dir: str | None = None,
) -> tuple[int, str, str]:
    """Invoke router.py as a subprocess."""
    env = {**os.environ}
    if files_dir:
        env["_HOOK_MEMORY_FILES_DIR"] = files_dir
    if extra_env:
        env.update(extra_env)
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": user_prompt,
        "session_id": "S-router-test",
    }
    result = subprocess.run(
        [sys.executable, str(ROUTER_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


# ---------------------------------------------------------------------------
# STUB 1 — router.py exists (basic existence gate)
# ---------------------------------------------------------------------------
# Given: Phase E1 has been implemented
# When:  we check for router.py at .claude/hooks/router.py
# Then:  the file exists and is executable


def test_router_script_exists() -> None:
    """STUB: router.py must exist at .claude/hooks/router.py after Phase E1."""
    if not ROUTER_SCRIPT.exists():
        pytest.fail(
            f"router.py not found at {ROUTER_SCRIPT} — Phase E1 not implemented. "
            "Implement router.py (UserPromptSubmit hook) and router_core.py."
        )
    assert os.access(ROUTER_SCRIPT, os.X_OK), (
        f"router.py must be executable. Got: {ROUTER_SCRIPT}"
    )


# ---------------------------------------------------------------------------
# STUB 2 — router_core.py exposes build_persona_enum()
# ---------------------------------------------------------------------------
# Given: router_core.py exists
# When:  we import it and call build_persona_enum(".claude/agents")
# Then:  returns a list of persona names from .md files in the agents directory


def test_router_core_build_persona_enum(tmp_path: Path) -> None:
    """STUB: router_core.build_persona_enum() must return dynamic list from agents dir."""
    _skip_if_not_implemented("router_core import")
    if not ROUTER_CORE_MODULE.exists():
        pytest.fail(
            f"router_core.py not found at {ROUTER_CORE_MODULE} — Phase E1 not implemented"
        )

    spec = importlib.util.spec_from_file_location("router_core", ROUTER_CORE_MODULE)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "forge-ui.md").write_text("---\nname: forge-ui\n---\nBody.")
    (agents_dir / "pipeline-data.md").write_text("---\nname: pipeline-data\n---\nBody.")
    (agents_dir / "_internal.md").write_text("---\nname: _internal\n---\nBody.")

    personas = mod.build_persona_enum(str(agents_dir))  # type: ignore[attr-defined]
    assert isinstance(personas, list), f"Expected list, got {type(personas)}"
    assert "forge-ui" in personas, f"Expected 'forge-ui' in enum. Got: {personas}"
    assert "pipeline-data" in personas, f"Expected 'pipeline-data' in enum. Got: {personas}"
    assert "_internal" not in personas, (
        "Files starting with '_' must be excluded from enum"
    )


# ---------------------------------------------------------------------------
# STUB 3 — router emits pre-fill block at confidence >= THRESHOLD_LLM
# ---------------------------------------------------------------------------
# Given: Qwen returns confidence=0.90 (above 0.85 threshold)
# When:  router.py processes UserPromptSubmit for "add Tremor card to dashboard"
# Then:  stdout is hookSpecificOutput JSON with <routing-pre-fill> block


def test_emits_prefill_above_threshold(tmp_path: Path) -> None:
    """STUB: router must emit <routing-pre-fill> when Qwen confidence >= threshold.

    Uses realistic LM Studio shape: confidence as integer 90 (not 0.90) to exercise
    the normalization path in router_core._normalize_confidence().
    """
    _skip_if_not_implemented("prefill above threshold")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    # Realistic Qwen output: confidence as 0-100 integer
    mock_qwen_response = json.dumps({
        "persona": "forge-ui",
        "difficulty": "standard",
        "confidence": 90,
        "required_skills": ["forge-ui-conventions", "tremor-patterns"],
        "tdd_required": True,
    })

    code, out, err = _run_router(
        "add a Tremor card showing active workbook count to the dashboard",
        extra_env={"_MOCK_QWEN_RESPONSE": mock_qwen_response},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 (fail-open). Got {code}. stderr={err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = (
        hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    )
    assert "<routing-pre-fill" in additional_context, (
        f"Expected <routing-pre-fill> in additionalContext at confidence=0.90. "
        f"hookSpecificOutput: {hook_out!r}"
    )
    assert "forge-ui" in additional_context, (
        f"Expected 'forge-ui' in pre-fill block. Got: {additional_context!r}"
    )


# ---------------------------------------------------------------------------
# STUB 4 — router falls through when confidence < THRESHOLD_LLM
# ---------------------------------------------------------------------------
# Given: Qwen returns confidence=0.70 (below 0.85 threshold)
# When:  router.py processes UserPromptSubmit
# Then:  no <routing-pre-fill> in output


def test_falls_through_below_threshold(tmp_path: Path) -> None:
    """STUB: router must NOT emit pre-fill when confidence < threshold."""
    _skip_if_not_implemented("fallthrough below threshold")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    mock_qwen_response = json.dumps({
        "persona": "forge-ui",
        "difficulty": "standard",
        "confidence": 0.70,
        "required_skills": ["forge-ui-conventions"],
        "tdd_required": True,
    })

    code, out, err = _run_router(
        "do something with the dashboard",
        extra_env={"_MOCK_QWEN_RESPONSE": mock_qwen_response},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 (fail-open). Got {code}. stderr={err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = (
        hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    )
    assert "<routing-pre-fill" not in additional_context, (
        f"Must not emit pre-fill at confidence=0.70. Got: {additional_context!r}"
    )


# ---------------------------------------------------------------------------
# STUB 5 — malformed Qwen JSON → fall-through, no crash
# ---------------------------------------------------------------------------


def test_malformed_qwen_json_falls_through(tmp_path: Path) -> None:
    """STUB: router must fall through gracefully on malformed Qwen JSON."""
    _skip_if_not_implemented("malformed json fallthrough")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    code, out, err = _run_router(
        "add a new chart to the workbooks page",
        extra_env={"_MOCK_QWEN_RESPONSE": "{invalid json!!!"},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 on malformed JSON. Got {code}. stderr={err}"
    assert "Traceback" not in err, f"Unhandled traceback on malformed Qwen JSON:\n{err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = (
        hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    )
    assert "<routing-pre-fill" not in additional_context, (
        "Must not emit pre-fill on malformed JSON"
    )


# ---------------------------------------------------------------------------
# STUB 6 — LM Studio down → fail-open, no crash
# ---------------------------------------------------------------------------


def test_lmstudio_down_falls_through(tmp_path: Path) -> None:
    """STUB: router must fail-open (exit 0, no pre-fill) when LM Studio is unreachable."""
    _skip_if_not_implemented("lmstudio down")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    code, out, err = _run_router(
        "fix the Tableau auth refresh",
        extra_env={"_MOCK_QWEN_CONNECT_ERROR": "1"},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 when LM Studio is down. Got {code}. stderr={err}"
    assert "Traceback" not in err, f"Unhandled traceback when LM Studio is down:\n{err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = (
        hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    )
    assert "<routing-pre-fill" not in additional_context, (
        "Must not emit pre-fill when LM Studio is down"
    )


# ---------------------------------------------------------------------------
# STUB 7 — shadow mode uses <routing-shadow> tag
# ---------------------------------------------------------------------------


def test_shadow_mode_uses_different_tag(tmp_path: Path) -> None:
    """STUB: personas in shadow mode must emit <routing-shadow> not <routing-pre-fill>."""
    _skip_if_not_implemented("shadow mode")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    mock_qwen_response = json.dumps({
        "persona": "atlas",
        "difficulty": "standard",
        "confidence": 0.91,
        "required_skills": ["atlas-schema-patterns"],
        "tdd_required": False,
    })

    code, out, err = _run_router(
        "add a DuckDB migration for the new stall_count column",
        extra_env={
            "_MOCK_QWEN_RESPONSE": mock_qwen_response,
            "_HOOK_SHADOW_PERSONAS": "atlas,hermes,palette",
        },
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0. Got {code}. stderr={err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = (
        hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    )
    assert "<routing-shadow" in additional_context, (
        f"Expected <routing-shadow> tag for shadow-mode persona 'atlas'. "
        f"Got: {additional_context!r}"
    )
    assert "<routing-pre-fill" not in additional_context, (
        "Shadow-mode personas must NOT emit <routing-pre-fill>"
    )


# ---------------------------------------------------------------------------
# STUB 8 — heartbeat appended to hook_heartbeat.jsonl on every call
# ---------------------------------------------------------------------------


def test_heartbeat_emitted(tmp_path: Path) -> None:
    """STUB: router must append one heartbeat line to hook_heartbeat.jsonl per call."""
    _skip_if_not_implemented("heartbeat")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()
    heartbeat_path = Path(files_dir) / "hook_heartbeat.jsonl"

    mock_qwen_response = json.dumps({
        "persona": "forge-ui",
        "difficulty": "trivial",
        "confidence": 0.88,
        "required_skills": [],
        "tdd_required": False,
    })

    _run_router(
        "fix a typo in WorkbookList.tsx",
        extra_env={"_MOCK_QWEN_RESPONSE": mock_qwen_response},
        files_dir=files_dir,
    )

    assert heartbeat_path.exists(), (
        f"hook_heartbeat.jsonl not created at {heartbeat_path}"
    )
    lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1, "Expected at least one heartbeat line"
    heartbeat = json.loads(lines[-1])
    assert heartbeat.get("hook") == "router", (
        f"Expected hook='router' in heartbeat, got: {heartbeat}"
    )
    assert "ts" in heartbeat, f"Heartbeat must include 'ts' timestamp. Got: {heartbeat}"
    assert "decision" in heartbeat, (
        f"Heartbeat must include 'decision' field. Got: {heartbeat}"
    )


# ---------------------------------------------------------------------------
# STUB 9 — router_decisions.jsonl appended on every call
# ---------------------------------------------------------------------------


def test_decision_log_appended(tmp_path: Path) -> None:
    """STUB: router must append one decision log line to router_decisions.jsonl per call.

    Uses realistic LM Studio shape: confidence as integer 89 to verify normalization
    is reflected correctly in the decision log (stored as 0.89, not 89).
    """
    _skip_if_not_implemented("decision log")

    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()
    decisions_path = Path(files_dir) / "router_decisions.jsonl"

    # Realistic Qwen output: confidence as 0-100 integer
    mock_qwen_response = json.dumps({
        "persona": "pipeline-data",
        "difficulty": "standard",
        "confidence": 89,
        "required_skills": ["pipeline-data-conventions", "polars-duckdb-mapping"],
        "tdd_required": True,
    })

    _run_router(
        "update the Polars transform to include the new embedding dimension",
        extra_env={"_MOCK_QWEN_RESPONSE": mock_qwen_response},
        files_dir=files_dir,
    )

    assert decisions_path.exists(), (
        f"router_decisions.jsonl not created at {decisions_path}"
    )
    lines = [ln for ln in decisions_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1, "Expected at least one decision log line"
    entry = json.loads(lines[-1])

    required_fields = {"timestamp", "qwen_persona", "qwen_confidence", "decision", "latency_ms"}
    missing = required_fields - set(entry.keys())
    assert not missing, (
        f"Decision log entry missing required fields {missing}. Got: {entry}"
    )
    assert entry["qwen_persona"] == "pipeline-data", (
        f"Expected qwen_persona=pipeline-data, got {entry['qwen_persona']}"
    )
    assert entry["qwen_confidence"] <= 1.0, (
        f"qwen_confidence must be normalized to 0.0-1.0. Got {entry['qwen_confidence']} "
        "(Qwen returns 0-100 int; router_core must normalize at call boundary)"
    )

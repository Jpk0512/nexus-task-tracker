"""
Contract tests for the single-stage router (router.py + router_core.py).

These ship WITH the package so the router contract cannot drift from the tested
behaviour undetected (the snapshot used to copy source but not its tests).

Covered contracts:
  - router.py exists and is executable.
  - build_persona_enum() is dynamic, excludes _-prefixed / DOMAIN-AGENT-TEMPLATE /
    orchestrator-only personas, INCLUDES -pro escalation variants (OPT-062), and
    always includes 'meta'.
  - build_schema()'s persona enum matches the shipped roster.
  - prefill above threshold / fallthrough below threshold / malformed JSON /
    LM-Studio-down / shadow-mode / heartbeat / decision-log / meta-persona.
  - P3-04 degraded-error contract: unexpected errors are LOUD ("[router]
    degraded: <Err>" stderr + decision:"error" row); benign connection failures
    stay silent.
  - OPT-012: _compute_logprob_margin is present and returns correct margin from
    an OpenAI-style logprobs block; None on absent/malformed input.

Run with:  python3 -m pytest .claude/hooks/tests/test_router.py -q
"""

from __future__ import annotations

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

THRESHOLD_LLM = 0.70


def _load_router_core():
    """Import router_core.py as a standalone module from its file path."""
    if not ROUTER_CORE_MODULE.exists():
        pytest.fail(f"router_core.py not found at {ROUTER_CORE_MODULE}")
    spec = importlib.util.spec_from_file_location("router_core", ROUTER_CORE_MODULE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


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
# Existence + executability
# ---------------------------------------------------------------------------


def test_router_script_exists() -> None:
    """router.py must exist at .claude/hooks/router.py and be executable."""
    assert ROUTER_SCRIPT.exists(), f"router.py not found at {ROUTER_SCRIPT}"
    assert os.access(ROUTER_SCRIPT, os.X_OK), (
        f"router.py must be executable. Got: {ROUTER_SCRIPT}"
    )


# ---------------------------------------------------------------------------
# build_persona_enum — dynamic, exclusions, meta
# ---------------------------------------------------------------------------


def test_router_core_build_persona_enum(tmp_path: Path) -> None:
    """build_persona_enum() returns a dynamic list from the agents dir."""
    mod = _load_router_core()

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


def test_build_persona_enum_includes_pro_excludes_lens_fast_includes_meta(
    tmp_path: Path,
) -> None:
    """build_persona_enum INCLUDES the -pro escalation variants and 'meta', and
    EXCLUDES orchestrator-only personas (lens-fast).

    OPT-002 single-sources the roster from CLASSIFIER_PERSONAS — a mirror of
    broker.registry.CLASSIFIER_PERSONAS. The enum is that roster intersected with
    the agent files on disk, plus 'meta'. The four -pro variants ARE in
    CLASSIFIER_PERSONAS (OPT-062: the old endswith('-pro') filter left the
    classifier structurally unable to escalate); lens-fast is NOT
    (orchestrator-only mechanism persona).
    """
    mod = _load_router_core()

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "forge-ui.md").write_text("---\nname: forge-ui\n---")
    (agents_dir / "forge-ui-pro.md").write_text("---\nname: forge-ui-pro\n---")
    (agents_dir / "pipeline-data.md").write_text("---\nname: pipeline-data\n---")
    (agents_dir / "pipeline-data-pro.md").write_text("---\nname: pipeline-data-pro\n---")
    # lens-fast HAS an agent file but is orchestrator-only — must still be excluded.
    (agents_dir / "lens-fast.md").write_text("---\nname: lens-fast\n---")
    (agents_dir / "_internal.md").write_text("---\nname: _internal\n---")
    (agents_dir / "DOMAIN-AGENT-TEMPLATE.md").write_text("---\nname: template\n---")

    personas = mod.build_persona_enum(str(agents_dir))  # type: ignore[attr-defined]
    assert "forge-ui" in personas, f"forge-ui must be in enum. Got: {personas}"
    assert "pipeline-data" in personas, f"pipeline-data must be in enum. Got: {personas}"
    assert "meta" in personas, f"'meta' must be in enum. Got: {personas}"
    assert "forge-ui-pro" in personas, (
        f"-pro escalation variants must be INCLUDED (OPT-062). Got: {personas}"
    )
    assert "pipeline-data-pro" in personas, (
        f"-pro escalation variants must be INCLUDED (OPT-062). Got: {personas}"
    )
    assert "lens-fast" not in personas, (
        f"orchestrator-only personas (lens-fast) must be excluded. Got: {personas}"
    )
    assert "_internal" not in personas, f"_-prefixed files must be excluded. Got: {personas}"
    assert "DOMAIN-AGENT-TEMPLATE" not in personas, (
        f"DOMAIN-AGENT-TEMPLATE must be excluded. Got: {personas}"
    )


def test_build_persona_enum_excludes_retired_base_names(tmp_path: Path) -> None:
    """build_persona_enum drops retired base names (forge/pipeline/quill) present
    on disk as tombstone files. nexus-orchestrator is also excluded (main-session
    agent, never a dispatch target).

    These are excluded by virtue of being absent from CLASSIFIER_PERSONAS —
    the roster is intersected with on-disk files, and retired/orchestrator names
    are not in the roster.
    """
    mod = _load_router_core()

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    # Tombstones + main-session agent (must be excluded)
    for name in ("forge", "pipeline", "quill", "nexus-orchestrator"):
        (agents_dir / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: present-on-disk\n---\n"
        )
    # Legitimate split personas that MUST remain
    for name in (
        "forge-ui",
        "forge-wire",
        "pipeline-data",
        "pipeline-async",
        "quill-ts",
        "quill-py",
        "scout",
    ):
        (agents_dir / f"{name}.md").write_text(f"---\nname: {name}\n---\n")

    personas = mod.build_persona_enum(str(agents_dir))  # type: ignore[attr-defined]

    for excluded in ("forge", "pipeline", "quill", "nexus-orchestrator"):
        assert excluded not in personas, (
            f"Retired/orchestrator name '{excluded}' must NOT be in the enum. Got: {personas}"
        )
    for kept in ("forge-ui", "forge-wire", "pipeline-data", "pipeline-async", "quill-ts", "quill-py", "scout"):
        assert kept in personas, (
            f"Split persona '{kept}' must remain in the enum. Got: {personas}"
        )
    assert "meta" in personas


def test_real_agents_dir_enum_excludes_retired_and_orchestrator() -> None:
    """Against the REAL shipped agents dir: retired base names and nexus-orchestrator
    must not appear in the enum.
    """
    assert AGENTS_DIR.is_dir(), f"Real agents dir not found: {AGENTS_DIR}"
    mod = _load_router_core()
    personas = mod.build_persona_enum(str(AGENTS_DIR))  # type: ignore[attr-defined]
    for excluded in ("forge", "pipeline", "quill", "nexus-orchestrator"):
        assert excluded not in personas, (
            f"Name '{excluded}' must not be in the real-agents enum. "
            f"Full enum: {personas}"
        )


def test_build_schema_enum_matches_roster(tmp_path: Path) -> None:
    """build_schema's persona enum must equal the build_persona_enum roster."""
    mod = _load_router_core()

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "forge-ui.md").write_text("---\nname: forge-ui\n---")
    (agents_dir / "scout.md").write_text("---\nname: scout\n---")
    (agents_dir / "forge.md").write_text("---\nname: forge\n---")  # tombstone, excluded

    personas = mod.build_persona_enum(str(agents_dir))  # type: ignore[attr-defined]
    schema = mod.build_schema(personas)  # type: ignore[attr-defined]
    enum = schema["properties"]["persona"]["enum"]
    assert enum == personas, f"Schema enum {enum} must match roster {personas}"
    assert "forge" not in enum, "Schema enum must not contain a retired base name"


# ---------------------------------------------------------------------------
# Prefill / fallthrough / malformed / down / shadow / meta
# ---------------------------------------------------------------------------


def test_emits_prefill_above_threshold(tmp_path: Path) -> None:
    """Router emits <routing-pre-fill> when confidence >= threshold (normalizes int 90 → 0.90)."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    mock_router_response = json.dumps({
        "persona": "forge-ui",
        "difficulty": "standard",
        "confidence": 90,
        "required_skills": ["forge-ui-conventions", "tremor-patterns"],
        "tdd_required": True,
    })

    code, out, err = _run_router(
        "add a Tremor card showing active workbook count to the dashboard",
        extra_env={"_MOCK_ROUTER_RESPONSE": mock_router_response},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 (fail-open). Got {code}. stderr={err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "<routing-pre-fill" in additional_context, (
        f"Expected <routing-pre-fill> in additionalContext. hookSpecificOutput: {hook_out!r}"
    )
    assert "forge-ui" in additional_context, (
        f"Expected 'forge-ui' in pre-fill block. Got: {additional_context!r}"
    )


def test_falls_through_below_threshold(tmp_path: Path) -> None:
    """Router must NOT emit pre-fill when confidence < threshold (0.70)."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    mock_router_response = json.dumps({
        "persona": "forge-ui",
        "difficulty": "standard",
        "confidence": 0.60,
        "required_skills": ["forge-ui-conventions"],
        "tdd_required": True,
    })

    code, out, err = _run_router(
        "do something with the dashboard",
        extra_env={"_MOCK_ROUTER_RESPONSE": mock_router_response},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 (fail-open). Got {code}. stderr={err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "<routing-pre-fill" not in additional_context, (
        f"Must not emit pre-fill at confidence=0.60 (threshold=0.70). Got: {additional_context!r}"
    )


def test_malformed_router_json_falls_through(tmp_path: Path) -> None:
    """Router must fall through gracefully on malformed router-model JSON."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    code, out, err = _run_router(
        "add a new chart to the workbooks page",
        extra_env={"_MOCK_ROUTER_RESPONSE": "{invalid json!!!"},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 on malformed JSON. Got {code}. stderr={err}"
    assert "Traceback" not in err, f"Unhandled traceback on malformed router-model JSON:\n{err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "<routing-pre-fill" not in additional_context, "Must not emit pre-fill on malformed JSON"


def test_lmstudio_down_falls_through(tmp_path: Path) -> None:
    """Router must fail-open (exit 0, no pre-fill) when LM Studio is unreachable."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    code, out, err = _run_router(
        "fix the Tableau auth refresh",
        extra_env={"_MOCK_ROUTER_CONNECT_ERROR": "1"},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 when LM Studio is down. Got {code}. stderr={err}"
    assert "Traceback" not in err, f"Unhandled traceback when LM Studio is down:\n{err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "<routing-pre-fill" not in additional_context, (
        "Must not emit pre-fill when LM Studio is down"
    )


def test_shadow_mode_uses_different_tag(tmp_path: Path) -> None:
    """Personas in shadow mode must emit <routing-shadow> not <routing-pre-fill>."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()

    mock_router_response = json.dumps({
        "persona": "atlas",
        "difficulty": "standard",
        "confidence": 0.91,
        "required_skills": ["atlas-schema-patterns"],
        "tdd_required": False,
    })

    code, out, err = _run_router(
        "add a DuckDB migration for the new stall_count column",
        extra_env={
            "_MOCK_ROUTER_RESPONSE": mock_router_response,
            "_HOOK_SHADOW_PERSONAS": "atlas,hermes,palette",
        },
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0. Got {code}. stderr={err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "<routing-shadow" in additional_context, (
        f"Expected <routing-shadow> tag for shadow-mode persona 'atlas'. Got: {additional_context!r}"
    )
    assert "<routing-pre-fill" not in additional_context, (
        "Shadow-mode personas must NOT emit <routing-pre-fill>"
    )


def test_heartbeat_emitted(tmp_path: Path) -> None:
    """Router appends one heartbeat line to hook_heartbeat.jsonl per call."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()
    heartbeat_path = Path(files_dir) / "hook_heartbeat.jsonl"

    mock_router_response = json.dumps({
        "persona": "forge-ui",
        "difficulty": "trivial",
        "confidence": 0.88,
        "required_skills": [],
        "tdd_required": False,
    })

    _run_router(
        "fix a typo in WorkbookList.tsx",
        extra_env={"_MOCK_ROUTER_RESPONSE": mock_router_response},
        files_dir=files_dir,
    )

    assert heartbeat_path.exists(), f"hook_heartbeat.jsonl not created at {heartbeat_path}"
    lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1, "Expected at least one heartbeat line"
    heartbeat = json.loads(lines[-1])
    assert heartbeat.get("hook") == "router", f"Expected hook='router'. Got: {heartbeat}"
    assert "ts" in heartbeat, f"Heartbeat must include 'ts' timestamp. Got: {heartbeat}"
    assert "decision" in heartbeat, f"Heartbeat must include 'decision' field. Got: {heartbeat}"


def test_decision_log_appended(tmp_path: Path) -> None:
    """Router appends one decision log line to router_decisions.jsonl per call (normalizes 89 → 0.89)."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()
    decisions_path = Path(files_dir) / "router_decisions.jsonl"

    mock_router_response = json.dumps({
        "persona": "pipeline-data",
        "difficulty": "standard",
        "confidence": 89,
        "required_skills": ["pipeline-data-conventions", "polars-duckdb-mapping"],
        "tdd_required": True,
    })

    _run_router(
        "update the Polars transform to include the new embedding dimension",
        extra_env={"_MOCK_ROUTER_RESPONSE": mock_router_response},
        files_dir=files_dir,
    )

    assert decisions_path.exists(), f"router_decisions.jsonl not created at {decisions_path}"
    lines = [ln for ln in decisions_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1, "Expected at least one decision log line"
    entry = json.loads(lines[-1])

    required_fields = {"timestamp", "pred_persona", "pred_confidence", "decision", "latency_ms"}
    missing = required_fields - set(entry.keys())
    assert not missing, f"Decision log entry missing required fields {missing}. Got: {entry}"
    assert entry["pred_persona"] == "pipeline-data", (
        f"Expected pred_persona=pipeline-data, got {entry['pred_persona']}"
    )
    assert entry["pred_confidence"] <= 1.0, (
        f"pred_confidence must be normalized to 0.0-1.0. Got {entry['pred_confidence']}"
    )


def test_meta_persona_no_routing_chip(tmp_path: Path) -> None:
    """meta persona must suppress the routing chip and log decision='meta'."""
    files_dir = str(tmp_path / "memory_files")
    Path(files_dir).mkdir()
    decisions_path = Path(files_dir) / "router_decisions.jsonl"

    mock_router_response = json.dumps({
        "persona": "meta",
        "difficulty": "trivial",
        "confidence": 0.95,
        "required_skills": [],
        "tdd_required": False,
    })

    code, out, err = _run_router(
        "what's the status of open tasks?",
        extra_env={"_MOCK_ROUTER_RESPONSE": mock_router_response},
        files_dir=files_dir,
    )
    assert code == 0, f"Router must exit 0 for meta persona. Got {code}. stderr={err}"
    assert "Traceback" not in err, f"Unhandled traceback for meta persona:\n{err}"

    hook_out = json.loads(out) if out.strip() else {}
    additional_context = hook_out.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "<routing-pre-fill" not in additional_context, "meta persona must NOT emit <routing-pre-fill>"
    assert "<routing-shadow" not in additional_context, "meta persona must NOT emit <routing-shadow>"

    assert decisions_path.exists(), "router_decisions.jsonl not created for meta persona"
    lines = [ln for ln in decisions_path.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1, "Expected at least one decision log line for meta"
    entry = json.loads(lines[-1])
    assert entry.get("decision") == "meta", f"Expected decision='meta'. Got: {entry}"
    assert entry.get("pred_persona") == "meta", f"Expected pred_persona='meta'. Got: {entry}"


# ===========================================================================
# P3-04 — router degradation visibility
# ===========================================================================
# router_core import / HTTP / unexpected failures must be LOUD: a "[router]
# degraded: <ErrClass>" stderr line AND a decision:"error" row in
# router_decisions.jsonl. Benign LM-Studio-down failures (ConnectionError /
# URLError / timeout) stay silent. These tests lock that contract.


def test_router_core_classifies_benign_vs_unexpected_errors() -> None:
    """_is_benign_call_error: connection/timeout-class is benign; logic errors are not."""
    import urllib.error as _urlerr

    mod = _load_router_core()
    classify = mod._is_benign_call_error  # type: ignore[attr-defined]

    # Benign — LM Studio down/slow → silent fallthrough.
    assert classify(ConnectionRefusedError("refused")) is True
    assert classify(TimeoutError("slow")) is True
    assert classify(_urlerr.URLError(ConnectionRefusedError("refused"))) is True
    assert classify(_urlerr.URLError(TimeoutError("to"))) is True
    assert classify(_urlerr.URLError(OSError("unreachable"))) is True

    # Unexpected — a real router-path bug → must be surfaced.
    assert classify(KeyError("choices")) is False, (
        "A KeyError on the response shape is unexpected and must NOT be benign"
    )
    assert classify(ValueError("bad json")) is False
    assert classify(TypeError("boom")) is False
    assert classify(_urlerr.URLError("some non-OSError string reason")) is False


def test_router_core_unexpected_error_emits_degraded_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    """call_router_model logs '[router] degraded: <Err>' to stderr on an UNEXPECTED error, returns None.

    Stands up a mock LM Studio returning 200 with a body missing the 'choices'
    key, forcing a KeyError inside call_router_model's try block (an unexpected error).
    """
    import http.server
    import socketserver
    import threading

    body = b'{"unexpected":"shape"}'

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    mod = _load_router_core()
    mod.ROUTER_URL = f"http://127.0.0.1:{port}/v1/chat/completions"  # type: ignore[attr-defined]

    try:
        result = mod.call_router_model(  # type: ignore[attr-defined]
            "do a thing",
            agents_dir=str(AGENTS_DIR),
            skills_dir=str(REPO_ROOT / ".claude" / "skills"),
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert result is None, "call_router_model must return None on an unexpected error"
    err = capfd.readouterr().err
    assert "[router] degraded:" in err, (
        f"Unexpected error must emit a '[router] degraded:' stderr line. Got: {err!r}"
    )


def test_router_core_benign_error_is_silent() -> None:
    """call_router_model stays silent (no degraded line) when LM Studio is simply unreachable."""
    import io
    import socketserver
    from contextlib import redirect_stderr

    mod = _load_router_core()
    with socketserver.TCPServer(("127.0.0.1", 0), None) as s:  # type: ignore[arg-type]
        dead_port = s.server_address[1]
    mod.ROUTER_URL = f"http://127.0.0.1:{dead_port}/v1/chat/completions"  # type: ignore[attr-defined]

    buf = io.StringIO()
    with redirect_stderr(buf):
        result = mod.call_router_model(  # type: ignore[attr-defined]
            "do a thing",
            agents_dir=str(AGENTS_DIR),
            skills_dir=str(REPO_ROOT / ".claude" / "skills"),
        )
    assert result is None, "call_router_model must return None when LM Studio is down"
    assert "[router] degraded:" not in buf.getvalue(), (
        f"A benign connection failure must NOT emit a degraded line. Got: {buf.getvalue()!r}"
    )


def test_router_records_decision_error_on_core_import_failure(tmp_path: Path) -> None:
    """router.py: a router_core import failure → degraded stderr + decision:'error' row, exit 0.

    Copies router.py into a temp hooks dir alongside a poisoned router_core.py that
    raises on import. router.py inserts its own dir at sys.path[0], so the shadow
    module is imported first and the import fails — exercising the error path.
    """
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    files_dir = tmp_path / ".memory" / "files"
    files_dir.mkdir(parents=True)

    (hooks_dir / "router.py").write_text(ROUTER_SCRIPT.read_text())
    (hooks_dir / "router_core.py").write_text('raise RuntimeError("boom on import")\n')

    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "route this please",
        "session_id": "S-core-import-fail",
    }
    result = subprocess.run(
        [sys.executable, str(hooks_dir / "router.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, "_HOOK_MEMORY_FILES_DIR": str(files_dir)},
        timeout=15,
    )

    assert result.returncode == 0, (
        f"Router must fail open (exit 0) on a router_core import failure. "
        f"Got {result.returncode}. stderr={result.stderr}"
    )
    assert "[router] degraded:" in result.stderr, (
        f"Import failure must emit a '[router] degraded:' stderr line. Got: {result.stderr!r}"
    )

    decisions_path = files_dir / "router_decisions.jsonl"
    assert decisions_path.exists(), "router_decisions.jsonl must be written on error"
    lines = [ln for ln in decisions_path.read_text().splitlines() if ln.strip()]
    assert lines, "Expected a decision:'error' row in router_decisions.jsonl"
    entry = json.loads(lines[-1])
    assert entry.get("decision") == "error", f"Expected decision='error'. Got: {entry}"
    assert "RuntimeError" in entry.get("error", ""), (
        f"Error row must carry the exception repr. Got: {entry.get('error')!r}"
    )


# ===========================================================================
# OPT-012 — logprob margin (_compute_logprob_margin)
# ===========================================================================


def test_compute_logprob_margin_returns_correct_margin() -> None:
    """_compute_logprob_margin extracts P(top1) - P(top2) from an OpenAI logprobs block."""
    import math

    mod = _load_router_core()
    compute = mod._compute_logprob_margin  # type: ignore[attr-defined]

    logprob_top1 = -0.1
    logprob_top2 = -2.4
    logprobs = {
        "content": [
            {
                "token": "scout",
                "logprob": logprob_top1,
                "top_logprobs": [
                    {"token": "scout", "logprob": logprob_top1},
                    {"token": "atlas", "logprob": logprob_top2},
                ],
            }
        ]
    }
    margin = compute(logprobs)
    assert margin is not None, "margin must not be None for a valid logprobs block"
    expected = math.exp(logprob_top1) - math.exp(logprob_top2)
    assert abs(margin - expected) < 1e-9, f"margin {margin} != expected {expected}"
    assert 0.0 <= margin <= 1.0, f"margin must be clamped to [0, 1]. Got: {margin}"


def test_compute_logprob_margin_returns_none_on_absent_logprobs() -> None:
    """_compute_logprob_margin returns None when logprobs is None or not a dict."""
    mod = _load_router_core()
    compute = mod._compute_logprob_margin  # type: ignore[attr-defined]

    assert compute(None) is None, "None logprobs must yield None"
    assert compute("not-a-dict") is None, "Non-dict logprobs must yield None"
    assert compute({}) is None, "Empty dict (no 'content') must yield None"
    assert compute({"content": []}) is None, "Empty content list must yield None"
    assert compute({"content": [{"token": "x", "top_logprobs": [{"token": "x", "logprob": -0.1}]}]}) is None, (
        "Single top_logprob entry (< 2) must yield None"
    )


def test_margin_passed_through_mock_envelope() -> None:
    """call_router_model parses a mock logprobs envelope and surfaces confidence_margin."""
    import math

    mod = _load_router_core()

    logprob_top1 = -0.1
    logprob_top2 = -2.4
    mock_envelope = json.dumps({
        "classification": {
            "persona": "scout",
            "difficulty": "simple",
            "confidence": 0.88,
            "required_skills": [],
            "tdd_required": False,
        },
        "logprobs": {
            "content": [
                {
                    "token": "scout",
                    "logprob": logprob_top1,
                    "top_logprobs": [
                        {"token": "scout", "logprob": logprob_top1},
                        {"token": "atlas", "logprob": logprob_top2},
                    ],
                }
            ]
        },
    })

    import tempfile
    with tempfile.TemporaryDirectory() as agents_dir_str:
        agents_dir = Path(agents_dir_str)
        (agents_dir / "scout.md").write_text("---\nname: scout\n---")

        os.environ["_MOCK_ROUTER_RESPONSE"] = mock_envelope
        try:
            result = mod.call_router_model(  # type: ignore[attr-defined]
                "investigate the failing build",
                agents_dir=agents_dir_str,
                skills_dir=agents_dir_str,
            )
        finally:
            del os.environ["_MOCK_ROUTER_RESPONSE"]

    assert result is not None, "call_router_model must return a result for a valid mock"
    margin = result.get("confidence_margin")
    assert margin is not None, f"confidence_margin must be set when logprobs provided. Got: {result}"
    expected = math.exp(logprob_top1) - math.exp(logprob_top2)
    assert abs(margin - expected) < 1e-9, f"confidence_margin {margin} != expected {expected}"

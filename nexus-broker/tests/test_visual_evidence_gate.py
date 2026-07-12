"""Tests for visual-evidence-gate.sh — all 8 branches.

Gate logic (SubagentStop):
  1. Skip (exit 0) if marker != NEXUS:DONE
  2. Skip (exit 0) if agent not in GATED_AGENTS
  3. Skip (exit 0) if files_changed is empty (fail-open)
  4. Skip (exit 0) if no UI/API globs touched
  5. Allow (exit 0) if visual_skip_reason is non-empty
  6. Deny (exit 2) if UI touched + no screenshot evidence
  7. Allow (exit 0) if UI touched + screenshot_before/after refs present
  8. Deny (exit 2) if API touched + no invocation evidence
  9. Allow (exit 0) if API touched + invocation evidence present
 10. Fail-open (exit 0) on malformed JSON payload

Also covers profile-aware globs: next vs vite vs none/python-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / ".claude" / "hooks" / "visual-evidence-gate.sh"
# PKG_GATE resolves to None when this test file runs from inside the package
# (build_snapshot syncs it to nexus-package/nexus-broker/tests/), because
# REPO_ROOT then equals the package root and nexus-package/ doesn't nest further.
# Tests that reference PKG_GATE are skipped in that context.
_pkg_gate_candidate = REPO_ROOT / "nexus-package" / ".claude" / "hooks" / "visual-evidence-gate.sh"
PKG_GATE: Path | None = _pkg_gate_candidate if _pkg_gate_candidate.exists() else None

# Use the uv-managed interpreter (>=3.11) for running the gate in tests.
# The gate itself must also survive /usr/bin/python3 (tested in test_hooks_py39_import.py).
PYTHON = sys.executable


def _run_gate(
    payload: dict,
    gate: Path = GATE,
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run the gate with `payload` on stdin. Returns (returncode, stdout, stderr)."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [PYTHON, str(gate)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _done_payload(
    agent: str,
    files: list[str],
    vr: dict | str | None = None,
) -> dict:
    """Build a minimal NEXUS:DONE SubagentStop payload with a JSON block."""
    inner: dict = {
        "completion_marker": "## NEXUS:DONE",
        "status": "complete",
        "files_changed": files,
    }
    if vr is not None:
        inner["verification_result"] = vr
    text = "## NEXUS:DONE\n\n```json\n" + json.dumps(inner) + "\n```\n"
    return {
        "agent_persona": agent,
        "last_assistant_message": text,
    }


# ── Branch 1: skip on non-DONE marker ──────────────────────────────────────

def test_skip_non_done_marker() -> None:
    payload = {
        "agent_persona": "forge-ui",
        "last_assistant_message": "## NEXUS:CHECKPOINT\n\n```json\n{\"files_changed\":[\"app/page.tsx\"]}\n```\n",
    }
    rc, _, _ = _run_gate(payload)
    assert rc == 0


# ── Branch 2: skip exempt persona ──────────────────────────────────────────

def test_skip_exempt_persona() -> None:
    payload = _done_payload("lens", ["app/page.tsx"])
    rc, _, _ = _run_gate(payload)
    assert rc == 0


def test_skip_orchestrator_persona() -> None:
    payload = _done_payload("nexus", ["app/page.tsx"])
    rc, _, _ = _run_gate(payload)
    assert rc == 0


# ── Branch 3: fail-open on empty files_changed ─────────────────────────────

def test_failopen_empty_files_changed() -> None:
    payload = _done_payload("forge-ui", [])
    rc, _, _ = _run_gate(payload)
    assert rc == 0


# ── Branch 4: skip when no UI/API paths matched ────────────────────────────

def test_skip_no_ui_api_touched() -> None:
    """Files outside UI/API globs do not trigger the gate."""
    payload = _done_payload("forge-ui", ["docs/README.md", "nexus-broker/tests/foo.py"])
    rc, _, _ = _run_gate(payload)
    assert rc == 0


# ── Shared helper: create a tmp repo root with next-framework stack ─────────

def _next_root(tmp_path: Path) -> str:
    """Create a .memory/nexus-stack.json with next framework; return REPO_ROOT str."""
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    (memory_dir / "nexus-stack.json").write_text(
        json.dumps({"frontend": {"framework": "next"}})
    )
    return str(tmp_path)


# ── Branch 5: allow via visual_skip_reason ─────────────────────────────────

def test_allow_with_skip_reason_ui(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload(
        "forge-ui",
        ["app/page.tsx"],
        vr={"visual_skip_reason": "Static export — no running app to screenshot."},
    )
    rc, stdout, _ = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 0
    assert "VISUAL/SKIP" in stdout


def test_allow_with_skip_reason_api(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload(
        "hermes",
        ["app/api/health/route.ts"],
        vr={"visual_skip_reason": "Endpoint not yet deployed; verified via unit test only."},
    )
    rc, _, _ = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 0


# ── Branch 6: deny UI touched + no evidence ────────────────────────────────

def test_deny_ui_no_evidence(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload("forge-ui", ["app/page.tsx"], vr={"notes": "looks good"})
    rc, stdout, stderr = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 2
    combined = stdout + stderr
    assert "VISUAL/NO-SCREENSHOT" in combined
    assert "aside" in combined.lower()


def test_deny_ui_no_verification_result(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload("forge-ui", ["app/components/Button.tsx"], vr=None)
    rc, stdout, stderr = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 2
    assert "VISUAL/NO-SCREENSHOT" in (stdout + stderr)


# ── Branch 7: allow UI touched + screenshot refs present ───────────────────

def test_allow_ui_with_structured_screenshot_keys(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload(
        "forge-ui",
        ["app/page.tsx"],
        vr={
            "screenshot_before": "/tmp/before.png",
            "screenshot_after": "/tmp/after.png",
        },
    )
    rc, stdout, _ = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 0
    assert "VISUAL/PASS" in stdout


def test_allow_ui_with_png_refs_in_text(tmp_path: Path) -> None:
    """Two .png absolute paths anywhere in verification_result text satisfy the gate."""
    root = _next_root(tmp_path)
    payload = _done_payload(
        "forge-ui",
        ["app/page.tsx"],
        vr=(
            "Before: /tmp/agent-session/before.png\n"
            "After: /tmp/agent-session/after.png\n"
            "Both screenshots captured with aside repl."
        ),
    )
    rc, _, _ = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 0


# ── Branch 8: API needs invocation result ──────────────────────────────────

def test_deny_api_no_invocation_evidence(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload(
        "hermes",
        ["app/api/auth/route.ts"],
        vr={"notes": "endpoint implemented"},
    )
    rc, stdout, stderr = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 2
    combined = stdout + stderr
    assert "VISUAL/NO-API-INVOCATION" in combined


def test_allow_api_with_curl_evidence(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload(
        "hermes",
        ["app/api/auth/route.ts"],
        vr={"invocation": "curl -s http://localhost:3000/api/auth → HTTP/1.1 200 OK"},
    )
    rc, _, _ = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 0


def test_allow_api_with_aside_exec_evidence(tmp_path: Path) -> None:
    root = _next_root(tmp_path)
    payload = _done_payload(
        "hermes",
        ["app/api/health/route.ts"],
        vr={"result": "aside exec 'GET http://localhost:3000/api/health' → {\"status\":\"ok\"}"},
    )
    rc, _, _ = _run_gate(payload, env_overrides={"_HOOK_REPO_ROOT": root})
    assert rc == 0


# ── Branch 9: fail-open on malformed JSON ──────────────────────────────────

def test_failopen_malformed_json_payload() -> None:
    proc = subprocess.run(
        [PYTHON, str(GATE)],
        input="this is not json {{{",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0


def test_failopen_missing_assistant_message() -> None:
    payload = {"agent_persona": "forge-ui"}  # no last_assistant_message
    rc, _, _ = _run_gate(payload)
    assert rc == 0


# ── Branch 10: profile-aware globs (nexus-stack.json) ──────────────────────

def test_profile_aware_next_framework(tmp_path: Path) -> None:
    """next framework: app/ and components/ are UI paths."""
    stack = {"frontend": {"framework": "next"}}
    stack_path = tmp_path / "nexus-stack.json"
    stack_path.write_text(json.dumps(stack))
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    (memory_dir / "nexus-stack.json").write_text(json.dumps(stack))

    payload = _done_payload("forge-ui", ["app/dashboard/page.tsx"], vr={"notes": "no screenshots"})
    rc, stdout, stderr = _run_gate(
        payload,
        env_overrides={"_HOOK_REPO_ROOT": str(tmp_path)},
    )
    assert rc == 2
    assert "VISUAL/NO-SCREENSHOT" in (stdout + stderr)


def test_profile_aware_vite_framework(tmp_path: Path) -> None:
    """vite framework: src/ is the UI path."""
    stack = {"frontend": {"framework": "vite"}}
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    (memory_dir / "nexus-stack.json").write_text(json.dumps(stack))

    payload = _done_payload("forge-ui", ["src/components/Button.tsx"], vr={"notes": "no screenshots"})
    rc, _, _ = _run_gate(
        payload,
        env_overrides={"_HOOK_REPO_ROOT": str(tmp_path)},
    )
    assert rc == 2  # src/ matched as UI; no evidence → deny


def test_profile_aware_no_ui_framework(tmp_path: Path) -> None:
    """python-only / no frontend: UI globs are empty; src/ is NOT a UI path."""
    stack = {"frontend": {"framework": ""}}
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    (memory_dir / "nexus-stack.json").write_text(json.dumps(stack))

    payload = _done_payload("forge-ui", ["src/main.py"], vr=None)
    rc, _, _ = _run_gate(
        payload,
        env_overrides={"_HOOK_REPO_ROOT": str(tmp_path)},
    )
    assert rc == 0  # no UI globs → skip


def test_profile_aware_explicit_globs_override(tmp_path: Path) -> None:
    """visual_review.ui_globs/api_globs in stack take precedence over framework derivation."""
    stack = {
        "frontend": {"framework": "next"},
        "visual_review": {
            "ui_globs": ["frontend/src/"],
            "api_globs": ["backend/routes/"],
        },
    }
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    (memory_dir / "nexus-stack.json").write_text(json.dumps(stack))

    # app/ is a next-framework default but NOT in explicit ui_globs → should not trigger
    payload_app = _done_payload("forge-ui", ["app/page.tsx"], vr=None)
    rc, _, _ = _run_gate(
        payload_app,
        env_overrides={"_HOOK_REPO_ROOT": str(tmp_path)},
    )
    assert rc == 0  # app/ not in explicit ui_globs

    # frontend/src/ IS in explicit ui_globs → should trigger (no evidence → deny)
    payload_fe = _done_payload("forge-ui", ["frontend/src/App.tsx"], vr={"notes": "done"})
    rc2, _, _ = _run_gate(
        payload_fe,
        env_overrides={"_HOOK_REPO_ROOT": str(tmp_path)},
    )
    assert rc2 == 2


# ── Both gate copies exist; live vs package path tokens differ by design ───
# Live copy hardcodes the Plexus root (so it resolves correctly in-repo).
# Package copy uses __INSTALL_ROOT__ (substituted at install time per render_install).
# This mirrors the established lens-gate.sh pattern — they are NOT byte-identical.
# These tests are skipped when running from inside the package (build_snapshot
# syncs this file to nexus-package/nexus-broker/tests/; PKG_GATE is then None
# because the path would double-nest as nexus-package/nexus-package/...).

@pytest.mark.skipif(PKG_GATE is None, reason="running from within package — PKG_GATE would double-nest")
def test_both_gate_copies_exist() -> None:
    assert GATE.exists(), f"live gate missing: {GATE}"
    assert PKG_GATE is not None and PKG_GATE.exists(), f"package gate missing: {PKG_GATE}"


@pytest.mark.skipif(PKG_GATE is None, reason="running from within package — PKG_GATE would double-nest")
def test_package_gate_uses_install_root_token() -> None:
    """Package copy must use __INSTALL_ROOT__ (not a hardcoded path) so render_install
    substitutes the real install path at install time."""
    assert PKG_GATE is not None
    pkg_src = PKG_GATE.read_text()
    assert "__INSTALL_ROOT__" in pkg_src, (
        "package gate must use __INSTALL_ROOT__ token — hardcoded path detected"
    )
    assert "/Users/" not in pkg_src, (
        "package gate must not contain a hardcoded /Users/ path"
    )


@pytest.mark.skipif(PKG_GATE is None, reason="running from within package — PKG_GATE would double-nest")
def test_live_gate_uses_hardcoded_plexus_root() -> None:
    """Live copy intentionally hardcodes the Plexus root so it resolves in-repo
    without _HOOK_REPO_ROOT being set — mirrors live lens-gate.sh pattern."""
    live_src = GATE.read_text()
    assert "__INSTALL_ROOT__" not in live_src, (
        "live gate must not contain __INSTALL_ROOT__ — use the hardcoded Plexus root"
    )
    assert "_HOOK_REPO_ROOT" in live_src, (
        "live gate must retain _HOOK_REPO_ROOT as the test-override seam"
    )

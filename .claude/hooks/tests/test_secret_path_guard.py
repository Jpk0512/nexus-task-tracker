"""Regression tests for secret-path-guard.sh (PreToolUse, standalone).

Pins the fail-open fix: the hook previously sourced gate-lib.sh which is
absent from installed projects, causing the hook to exit 1 (source error) and
silently ALLOW writes to secret files.  The hook now inlines its deny emitter
so it works standalone without gate-lib.sh present.

Also fixes key collection: Write uses file_path (not path) at the top level.
The hook now checks file_path, path, AND notebook_path so no variant is missed.

Contract:
  - Write with file_path=.env           -> DENY (exit 2, permissionDecision=deny)
  - Write with file_path=src/foo.py     -> ALLOW (exit 0, silent)
  - Write with path=.env                -> DENY (exit 2, legacy key still works)
  - NotebookEdit notebook_path=keys.pem -> DENY (exit 2)
  - MultiEdit edits[].file_path=id_rsa  -> DENY (exit 2)
  - gate-lib.sh NOT referenced          -> verified by source scan

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_secret_path_guard.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK_FILE = Path(__file__).resolve().parent.parent / "secret-path-guard.sh"


def _run(payload: dict) -> tuple[int, str, str]:
    result = subprocess.run(
        ["/bin/bash", str(HOOK_FILE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
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


# ─── Deny cases ───────────────────────────────────────────────────────────────


class TestDeny:
    def test_write_file_path_env_is_denied(self) -> None:
        """Write with file_path=.env must exit 2 with deny JSON."""
        payload = {"tool_name": "Write", "tool_input": {"file_path": ".env", "content": "x"}}
        code, out, err = _run(payload)
        assert code == 2, f"Expected exit 2, got {code}: stdout={out!r}"
        ho = _hook_out(out)
        assert ho.get("permissionDecision") == "deny", f"Expected deny, got: {out!r}"
        assert ho.get("hookEventName") == "PreToolUse"
        assert "[GATE:SECRET-PATH/WRITE-DENIED]" in ho.get("permissionDecisionReason", "")
        assert "[GATE:SECRET-PATH/WRITE-DENIED]" in err

    def test_write_legacy_path_key_env_is_denied(self) -> None:
        """Write with path=.env (legacy key) must also exit 2 with deny JSON."""
        payload = {"tool_name": "Write", "tool_input": {"path": ".env", "content": "x"}}
        code, out, err = _run(payload)
        assert code == 2, f"Expected exit 2, got {code}: stdout={out!r}"
        assert _hook_out(out).get("permissionDecision") == "deny"

    @pytest.mark.parametrize("secret", [
        ".env.local",
        "key.pem",
        "server.key",
        "id_rsa",
        "id_ed25519",
        "secrets.json",
        ".netrc",
        ".npmrc",
        "cert.p12",
        "keystore.pfx",
    ])
    def test_various_secret_paths_denied(self, secret: str) -> None:
        payload = {"tool_name": "Write", "tool_input": {"file_path": secret}}
        code, out, _ = _run(payload)
        assert code == 2, f"Write to {secret!r} must exit 2, got {code}"
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_notebook_path_denied(self) -> None:
        payload = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "keys.pem"}}
        code, out, err = _run(payload)
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"
        assert "[GATE:SECRET-PATH/WRITE-DENIED]" in err

    def test_multiedit_secret_denied(self) -> None:
        payload = {
            "tool_name": "MultiEdit",
            "tool_input": {
                "edits": [
                    {"file_path": "src/safe.py", "old_string": "a", "new_string": "b"},
                    {"file_path": "id_rsa", "old_string": "a", "new_string": "b"},
                ]
            },
        }
        code, out, err = _run(payload)
        assert code == 2, f"MultiEdit with secret must exit 2, got {code}"
        assert _hook_out(out).get("permissionDecision") == "deny"

    def test_subdirectory_secret_denied(self) -> None:
        payload = {"tool_name": "Write", "tool_input": {"file_path": "config/.env.production"}}
        code, out, _ = _run(payload)
        assert code == 2
        assert _hook_out(out).get("permissionDecision") == "deny"


# ─── Allow cases ──────────────────────────────────────────────────────────────


class TestAllow:
    def test_write_safe_path_allowed(self) -> None:
        """Write with file_path=src/foo.py must exit 0, silent."""
        payload = {"tool_name": "Write", "tool_input": {"file_path": "src/foo.py", "content": "x"}}
        code, out, err = _run(payload)
        assert code == 0, f"Expected exit 0, got {code}: stdout={out!r} stderr={err!r}"
        assert out.strip() == "", f"Expected no stdout, got: {out!r}"
        assert err.strip() == "", f"Expected no stderr, got: {err!r}"

    @pytest.mark.parametrize("safe", [
        "app/page.tsx",
        "README.md",
        "pyproject.toml",
        "environment.ts",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
        "src/index.ts",
    ])
    def test_various_safe_paths_allowed(self, safe: str) -> None:
        payload = {"tool_name": "Write", "tool_input": {"file_path": safe}}
        code, out, err = _run(payload)
        assert code == 0, f"Write to {safe!r} must be allowed, got {code}"
        assert out.strip() == ""
        assert err.strip() == ""

    def test_multiedit_all_safe_allowed(self) -> None:
        payload = {
            "tool_name": "MultiEdit",
            "tool_input": {
                "edits": [
                    {"file_path": "src/a.py"},
                    {"file_path": "src/b.py"},
                ]
            },
        }
        code, out, err = _run(payload)
        assert code == 0
        assert out.strip() == ""
        assert err.strip() == ""

    def test_empty_payload_allowed(self) -> None:
        code, out, err = _run({})
        assert code == 0
        assert out.strip() == ""


# ─── Standalone (no gate-lib.sh) ──────────────────────────────────────────────


class TestStandalone:
    def test_gate_lib_not_referenced(self) -> None:
        """The hook must not reference gate-lib.sh (would fail-open when absent)."""
        source = HOOK_FILE.read_text()
        assert "gate-lib.sh" not in source, (
            "secret-path-guard.sh must NOT source gate-lib.sh — "
            "it inlines the deny emitter to work standalone."
        )

    def test_deny_works_without_gate_lib(self, tmp_path: Path) -> None:
        """Running the hook from a directory where gate-lib.sh is absent must
        still produce exit 2 for a secret path — not exit 1 (source error)."""
        import shutil

        hook_copy = tmp_path / "secret-path-guard.sh"
        shutil.copy(HOOK_FILE, hook_copy)
        hook_copy.chmod(0o755)

        payload = {"tool_name": "Write", "tool_input": {"file_path": ".env"}}
        result = subprocess.run(
            ["/bin/bash", str(hook_copy)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=15,
        )
        assert result.returncode == 2, (
            f"Hook must exit 2 (deny) even without gate-lib.sh present, "
            f"got {result.returncode}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        ho = _hook_out(result.stdout)
        assert ho.get("permissionDecision") == "deny"

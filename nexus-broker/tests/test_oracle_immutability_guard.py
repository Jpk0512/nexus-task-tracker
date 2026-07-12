"""Tests for .claude/hooks/oracle-immutability-guard.sh — PreToolUse WRITE-time
gate (R1-T11).

Unlike do-not-touch-guard.sh (SubagentStop, advisory-only, fires AFTER the
fact), this hook runs BEFORE the write lands and DENIES it (exit 2) when the
write target matches an active brief's do_not_touch glob. It needs zero
persona resolution: it only compares the write target path against
do_not_touch globs extracted from broker_state.json's approved_brief.

Contract:
  - Write/Edit/MultiEdit/NotebookEdit whose target matches a do_not_touch
    glob -> DENY (exit 2, permissionDecision="deny", reason names path+glob).
  - No match                                  -> ALLOW (exit 0, silent).
  - No active approved brief (missing/malformed state, empty do_not_touch)
    -> ALLOW (exit 0, silent) for everything, no active oracle to protect.

Gate code carried in reason: [GATE:ORACLE-IMMUTABILITY/WRITE-DENIED]

Also asserts 3.9 import-safety: the hook's embedded Python body must run
clean under /usr/bin/python3 (this is a .sh file with embedded python, like
secret-path-guard.sh — not auto-covered by test_hooks_py39_import.py, which
only globs *.py files).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "oracle-immutability-guard.sh"
# This test file is itself mirrored into nexus-package/nexus-broker/tests/ as a
# hand-reconciled twin. When THIS copy is the one executing, REPO_ROOT already
# resolves inside nexus-package/ (parents[2] from the package-tree copy lands
# on nexus-package/, not the real repo root) — so appending "nexus-package"
# again would double the path into a nonexistent
# nexus-package/nexus-package/... location. Detect that case and treat HOOK as
# its own package twin (a trivial self-consistency check) instead.
_RUNNING_AS_PACKAGE_TWIN = "nexus-package" in REPO_ROOT.parts
PKG_HOOK = (
    HOOK
    if _RUNNING_AS_PACKAGE_TWIN
    else REPO_ROOT / "nexus-package" / ".claude" / "hooks" / "oracle-immutability-guard.sh"
)

SYSTEM_PYTHON = Path("/usr/bin/python3")


def _write_state(tmp_path: Path, do_not_touch, approved: bool = True) -> Path:
    state = {
        "turn_id": "t-test",
        "approved": approved,
        "persona": "forge",
        "called_at": "2026-06-14T00:00:00+00:00",
        "approved_brief": {
            "goal": "g",
            "do_not_touch": do_not_touch,
        },
    }
    p = tmp_path / "broker_state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _run(state_path: Path, payload: dict, repo_root: Path | None = None):
    env = {
        "_HOOK_REPO_ROOT": str(repo_root or state_path.parent),
        "NEXUS_BROKER_STATE_PATH": str(state_path),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    proc = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return proc


def _write_payload(path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def _edit_payload(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def _notebook_payload(path: str) -> dict:
    return {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": path}}


def _multiedit_payload(paths: list) -> dict:
    return {
        "tool_name": "MultiEdit",
        "tool_input": {
            "edits": [{"file_path": p, "old_string": "a", "new_string": "b"} for p in paths]
        },
    }


def _hook_out(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


# ─────────────────────────── Write: matching path denied ────────────────────


class TestWriteDenied:
    def test_write_matching_do_not_touch_glob_is_denied(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["nexus-package/"])
        proc = _run(state, _write_payload("nexus-package/install.sh"))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        reason = ho.get("permissionDecisionReason", "")
        assert "[GATE:ORACLE-IMMUTABILITY/WRITE-DENIED]" in reason
        assert "nexus-package/install.sh" in reason
        assert "nexus-package/" in reason
        assert "[GATE:ORACLE-IMMUTABILITY/WRITE-DENIED]" in proc.stderr

    def test_write_non_matching_path_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["nexus-package/"])
        proc = _run(state, _write_payload("app/api/auth/route.ts"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""
        assert proc.stderr.strip() == ""

    def test_write_matching_file_glob_is_denied(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["*.lock"])
        proc = _run(state, _write_payload("uv.lock"))
        assert proc.returncode == 2
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        assert "uv.lock" in ho.get("permissionDecisionReason", "")


# ─────────────────────────── Edit ────────────────────────────────────────────


class TestEditDenied:
    def test_edit_matching_glob_is_denied(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["models/"])
        proc = _run(state, _edit_payload("models/schema.py"))
        assert proc.returncode == 2
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        assert "models/schema.py" in ho.get("permissionDecisionReason", "")

    def test_edit_non_matching_path_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["models/"])
        proc = _run(state, _edit_payload("ingestion/src/clients/tableau.py"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


# ─────────────────────────── MultiEdit (array, mixed) ────────────────────────


class TestMultiEditDenied:
    def test_multiedit_one_matching_one_not_is_denied(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["ingestion/"])
        proc = _run(
            state,
            _multiedit_payload(["app/page.tsx", "ingestion/src/pipeline.py"]),
        )
        assert proc.returncode == 2, "any match in the batch must deny — fail-closed"
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        assert "ingestion/src/pipeline.py" in ho.get("permissionDecisionReason", "")

    def test_multiedit_all_non_matching_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["ingestion/"])
        proc = _run(
            state,
            _multiedit_payload(["app/page.tsx", "src/index.ts"]),
        )
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_multiedit_empty_edits_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["ingestion/"])
        proc = _run(state, _multiedit_payload([]))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


# ─────────────────────────── NotebookEdit ────────────────────────────────────


class TestNotebookEditDenied:
    def test_notebook_edit_matching_glob_is_denied(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["*.ipynb"])
        proc = _run(state, _notebook_payload("analysis.ipynb"))
        assert proc.returncode == 2
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        assert "analysis.ipynb" in ho.get("permissionDecisionReason", "")

    def test_notebook_edit_non_matching_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["*.ipynb"])
        proc = _run(state, _notebook_payload("docs/other.md"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


# ─────────────────────────── No active brief -> always allow ────────────────


class TestNoActiveBriefAllowsEverything:
    def test_missing_state_file_is_allowed(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.json"
        proc = _run(missing, _write_payload("nexus-package/install.sh"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_empty_do_not_touch_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, [])
        proc = _run(state, _write_payload("nexus-package/install.sh"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_malformed_state_json_is_allowed(self, tmp_path: Path) -> None:
        p = tmp_path / "broker_state.json"
        p.write_text("{not json", encoding="utf-8")
        proc = _run(p, _write_payload("nexus-package/install.sh"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_no_approved_brief_key_is_allowed(self, tmp_path: Path) -> None:
        p = tmp_path / "broker_state.json"
        p.write_text(json.dumps({"approved": False, "persona": "forge"}), encoding="utf-8")
        proc = _run(p, _write_payload("nexus-package/install.sh"))
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""

    def test_empty_payload_is_allowed(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["nexus-package/"])
        proc = _run(state, {})
        assert proc.returncode == 0
        assert proc.stdout.strip() == ""


# ─────────────────── Absolute path relativized against repo_root ────────────


class TestAbsolutePathRelativization:
    def test_absolute_in_repo_path_matches_repo_relative_glob(self, tmp_path: Path) -> None:
        """This is the exact case that was silently passing (never denying)
        before the relativization fix: a real Write/Edit call always carries
        an ABSOLUTE file_path, but do_not_touch globs are authored
        repo-relative (e.g. "nexus-package/"). Without relativizing against
        repo_root first, _matches('/repo/nexus-package/install.sh',
        'nexus-package/') returns False for every glob form -> the gate never
        fires against a real call."""
        repo_root = tmp_path / "repo"
        (repo_root / "nexus-package").mkdir(parents=True)
        state = _write_state(tmp_path, ["nexus-package/"])
        abs_path = str(repo_root / "nexus-package" / "install.sh")
        proc = _run(state, _write_payload(abs_path), repo_root=repo_root)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        reason = ho.get("permissionDecisionReason", "")
        assert "[GATE:ORACLE-IMMUTABILITY/WRITE-DENIED]" in reason
        assert abs_path in reason
        assert "nexus-package/" in reason

    def test_absolute_path_outside_repo_root_degrades_safely(self, tmp_path: Path) -> None:
        """A path that cannot be relativized against repo_root (outside the
        repo tree) must not crash and must not false-positive-match a
        repo-relative glob."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        state = _write_state(tmp_path, ["nexus-package/"])
        outside_path = str(tmp_path / "elsewhere" / "nexus-package" / "install.sh")
        proc = _run(state, _write_payload(outside_path), repo_root=repo_root)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip() == ""


# ─────────────────────────── Directory-prefix glob (trailing slash) ─────────


class TestDirectoryPrefixGlob:
    def test_trailing_slash_glob_matches_nested_file(self, tmp_path: Path) -> None:
        state = _write_state(tmp_path, ["nexus-package/"])
        proc = _run(state, _write_payload("nexus-package/.claude/hooks/deep/nested/file.sh"))
        assert proc.returncode == 2
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"
        assert "nexus-package/.claude/hooks/deep/nested/file.sh" in ho.get(
            "permissionDecisionReason", ""
        )

    def test_bare_directory_name_matches_subtree(self, tmp_path: Path) -> None:
        # No trailing slash, no wildcard -> still forbids the subtree per
        # do-not-touch-guard.sh's _matches semantics.
        state = _write_state(tmp_path, ["models"])
        proc = _run(state, _write_payload("models/schema.sql"))
        assert proc.returncode == 2
        ho = _hook_out(proc.stdout)
        assert ho.get("permissionDecision") == "deny"


# ─────────────────────────── 3.9 import-safety ───────────────────────────────


class TestPy39ImportSafety:
    @pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no system /usr/bin/python3 on this box")
    @pytest.mark.parametrize("hook_path", [HOOK, PKG_HOOK])
    def test_embedded_python_body_runs_under_system_python3(self, hook_path: Path) -> None:
        """The hook's embedded python heredoc must run clean under ambient
        python3 (3.9 on stock macOS) — same guarantee test_hooks_py39_import.py
        gives *.py hooks, but this is a .sh file with an embedded body so it
        isn't picked up by that file's glob."""
        assert hook_path.exists(), f"missing: {hook_path}"
        proc = subprocess.run(
            ["/bin/bash", str(hook_path)],
            input=json.dumps(_write_payload("does/not/exist.txt")),
            capture_output=True,
            text=True,
            env={
                "_HOOK_REPO_ROOT": "/tmp",
                "NEXUS_BROKER_STATE_PATH": "/tmp/does-not-exist-oracle-guard.json",
                "PATH": "/usr/bin:/bin",
            },
            timeout=15,
        )
        assert proc.returncode == 0, (
            f"embedded python body failed under system python3: {proc.stderr}"
        )


# ─────────────────────────── Package twin parity ─────────────────────────────


class TestPackageTwinExists:
    def test_package_twin_hook_exists_and_matches_bash_syntax(self) -> None:
        assert PKG_HOOK.exists(), f"missing package twin: {PKG_HOOK}"
        proc = subprocess.run(
            ["/bin/bash", "-n", str(PKG_HOOK)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr

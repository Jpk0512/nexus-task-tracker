"""Regression tests — unrendered-install-token fail-open-silent class (WF7 fix A).

return-summarizer.sh (SubagentStop) and reflection-capture.sh (PostToolUse) both
read an install-time-rendered /Users/john.keeney/nexus-task-tracker path. If install-time rendering was
skipped the literal token survives, the path does not exist, and the hook silently
no-ops — sub-agent returns and doc-edit snapshots are dropped with no signal.

These hooks are Python (shebang #!/usr/bin/env python3) despite the .sh extension,
so they are invoked with python3, mirroring the subprocess-with-stdin-JSON style in
nexus-package/tests/test_p2_hooks.py (see TestPrecompactReinject).

Pinned contract for BOTH hooks:
  - rendered (REPO resolves to a real dir via _HOOK_INSTALL_ROOT) -> NORMAL behavior:
    no unrendered-token warning is emitted (silent non-blocking path preserved).
  - unrendered (REPO is the literal /Users/john.keeney/nexus-task-tracker token, simulated by NOT setting
    _HOOK_INSTALL_ROOT and leaving the on-disk default) -> LOUD: a valid nested
    hookSpecificOutput object naming the unrendered token /Users/john.keeney/nexus-task-tracker, NOT a
    silent exit-0. The hook still exits 0 (fail SAFE — never block the return/edit).

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_unrendered_token_A.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent

RETURN_SUMMARIZER = "return-summarizer.sh"
REFLECTION_CAPTURE = "reflection-capture.sh"


def _run(hook_file: str, stdin: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke a Python hook (despite the .sh extension) with stdin JSON + env.

    env_overrides REPLACES rather than augments _HOOK_INSTALL_ROOT handling: a
    stray _HOOK_INSTALL_ROOT from the dev shell is stripped first so the
    "unrendered" cases truly see the literal on-disk default token.
    """
    env = {**os.environ}
    env.pop("_HOOK_INSTALL_ROOT", None)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / hook_file)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


# A SubagentStop payload carrying a long assistant message (so the rendered path
# would actually attempt to persist — exercising the normal branch end-to-end).
_SUBAGENT_PAYLOAD = json.dumps(
    {
        "agent_persona": "forge-ui",
        "last_assistant_message": "## NEXUS:DONE\n" + ("work " * 400),
    }
)

# A PostToolUse payload for a watched doc-critical edit with > MIN_LINE_DIFF lines.
_POSTTOOL_PAYLOAD = json.dumps(
    {
        "tool_name": "Edit",
        "session_id": "sess-xyz",
        "tool_input": {
            "file_path": "/proj/docs/features/FEAT-001.md",
            "old_string": "a\nb\nc\nd\ne\nf",
            "new_string": "a2\nb2\nc2\nd2\ne2\nf2",
        },
    }
)


def _is_loud_unrendered(result: subprocess.CompletedProcess[str], event_name: str) -> dict:
    """Assert the result is the fail-SAFE-LOUD shape and return the parsed payload.

    SAFE: exit 0 (never block). LOUD: valid nested hookSpecificOutput object whose
    additionalContext names the unrendered /Users/john.keeney/nexus-task-tracker token.
    """
    assert result.returncode == 0, (
        f"unrendered token must fail SAFE (exit 0, never block), got "
        f"{result.returncode}: {result.stdout!r} / {result.stderr!r}"
    )
    assert result.stdout.strip(), (
        "unrendered token must be LOUD on stdout — a silent exit-0 is the bug."
    )
    payload = json.loads(result.stdout)
    ho = payload["hookSpecificOutput"]
    assert isinstance(ho, dict), f"hookSpecificOutput must be a nested object, got: {ho!r}"
    assert ho["hookEventName"] == event_name, (
        f"Expected hookEventName {event_name!r}, got: {ho.get('hookEventName')!r}"
    )
    ctx = ho["additionalContext"]
    assert "/Users/john.keeney/nexus-task-tracker" in ctx, (
        f"The warning must name the unrendered /Users/john.keeney/nexus-task-tracker token, got: {ctx!r}"
    )
    return payload


# ===========================================================================
# return-summarizer.sh (SubagentStop)
# ===========================================================================


class TestReturnSummarizerUnrenderedToken:
    HOOK_FILE = RETURN_SUMMARIZER

    def test_rendered_is_normal_no_warning(self, tmp_path: Path) -> None:
        """Given _HOOK_INSTALL_ROOT pointing at a real dir (rendered), When the
        SubagentStop hook runs, Then it does NOT emit the unrendered-token
        warning — the normal non-blocking path is preserved (exit 0)."""
        (tmp_path / ".memory").mkdir()
        result = _run(
            self.HOOK_FILE,
            _SUBAGENT_PAYLOAD,
            {"_HOOK_INSTALL_ROOT": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"rendered path must exit 0, got {result.returncode}: {result.stderr!r}"
        )
        assert "/Users/john.keeney/nexus-task-tracker" not in result.stdout, (
            f"rendered run must NOT emit the unrendered-token warning, got: {result.stdout!r}"
        )
        assert "/Users/john.keeney/nexus-task-tracker" not in result.stderr

    def test_unrendered_is_loud_not_silent(self) -> None:
        """Given NO _HOOK_INSTALL_ROOT (so REPO is the literal on-disk
        /Users/john.keeney/nexus-task-tracker token), When the hook runs, Then it fails SAFE+LOUD:
        a nested SubagentStop hookSpecificOutput warning naming the token,
        not a silent exit-0."""
        result = _run(self.HOOK_FILE, _SUBAGENT_PAYLOAD, {})
        self._assert_loud(result)

    def _assert_loud(self, result: subprocess.CompletedProcess[str]) -> None:
        payload = _is_loud_unrendered(result, "SubagentStop")
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "return-summarizer" in ctx, (
            f"Warning should name the hook for traceability, got: {ctx!r}"
        )
        assert "/Users/john.keeney/nexus-task-tracker" in result.stderr, (
            "The warning must also be echoed to stderr (LOUD), got empty/other stderr."
        )


# ===========================================================================
# reflection-capture.sh (PostToolUse)
# ===========================================================================


class TestReflectionCaptureUnrenderedToken:
    HOOK_FILE = REFLECTION_CAPTURE

    def test_rendered_is_normal_no_warning(self, tmp_path: Path) -> None:
        """Given _HOOK_INSTALL_ROOT pointing at a real dir (rendered) holding a
        .memory/project.db, When the PostToolUse hook runs on a watched
        doc-critical edit, Then it records silently and does NOT emit the
        unrendered-token warning (exit 0, normal behavior preserved)."""
        (tmp_path / ".memory").mkdir()
        # Pre-create an empty DB file; the hook init_table()s it on connect.
        (tmp_path / ".memory" / "project.db").touch()
        result = _run(
            self.HOOK_FILE,
            _POSTTOOL_PAYLOAD,
            {"_HOOK_INSTALL_ROOT": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"rendered path must exit 0, got {result.returncode}: {result.stderr!r}"
        )
        assert "/Users/john.keeney/nexus-task-tracker" not in result.stdout, (
            f"rendered run must NOT emit the unrendered-token warning, got: {result.stdout!r}"
        )
        assert "/Users/john.keeney/nexus-task-tracker" not in result.stderr

    def test_unrendered_is_loud_not_silent(self) -> None:
        """Given NO _HOOK_INSTALL_ROOT (so REPO is the literal on-disk
        /Users/john.keeney/nexus-task-tracker token), When the hook runs, Then it fails SAFE+LOUD:
        a nested PostToolUse hookSpecificOutput warning naming the token,
        not a silent exit-0."""
        result = _run(self.HOOK_FILE, _POSTTOOL_PAYLOAD, {})
        payload = _is_loud_unrendered(result, "PostToolUse")
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "reflection-capture" in ctx, (
            f"Warning should name the hook for traceability, got: {ctx!r}"
        )
        assert "/Users/john.keeney/nexus-task-tracker" in result.stderr, (
            "The warning must also be echoed to stderr (LOUD), got empty/other stderr."
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

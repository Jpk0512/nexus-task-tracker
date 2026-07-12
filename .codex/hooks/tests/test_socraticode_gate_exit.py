"""Regression tests for socraticode-gate.sh exit-2 backstop + unrendered-token
fail-closed guard (WF6 BACKSTOP fix).

This is a *security* gate (SocratiCode-first, CONSTITUTION Article III + CONTRACT
Rule 2). Two enforcement modes:

  Mode 1 — PreToolUse(Bash): blocks grep/rg/find/ack/ag/fgrep/egrep at command
           position unless a SocratiCode discovery tool fired this session (the
           per-session FLAG file exists).
  Mode 2 — PreToolUse(Read): blocks Read of a watched-prefix path unless the
           FLAG exists or the path is cited in the task brief.

Before the fix the deny emitted a nested ``permissionDecision: deny`` over JSON
but ALWAYS ``exit 0`` — so if the harness ignores the JSON channel the block is
lost (fail-open). The fix adds ``exit 2`` on every deny path while KEEPING the
nested JSON, and makes the predicate UNCHANGED (same when-to-deny).

Separately, Mode 2 read ``/app/apps/, /app/packages/`` / ``/Users/john.keeney/nexus-task-tracker`` install
tokens directly: if install-time rendering was skipped, the watched-prefix tuple
became ``("/app/apps/, /app/packages/",)`` which no real path matches → Mode 2 silently
fails OPEN. The fix adds ``_HOOK_WATCHED_PREFIXES`` / ``_HOOK_INSTALL_ROOT`` env
overrides and, when the token is still literal at runtime, DENIES a watched-looking
Read loud (fail CLOSED) instead of open-silent.

These tests assert BOTH directions (deny when it should, allow when it should)
and that the deny emits a VALID nested object the harness will not drop. They
mirror the subprocess-with-stdin-JSON style of tests/test_p2_hooks.py.

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_socraticode_gate_exit.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

HOOK_FILE = Path(__file__).resolve().parent.parent / "socraticode-gate.sh"

# The watched prefixes a rendered ai-dash-style install would carry. Passed via
# the _HOOK_WATCHED_PREFIXES override so the test does not depend on install-time
# substitution having run against this snapshot.
_RENDERED_PREFIXES = "/app/,/ingestion/src/,/models/,/docs/features/,/.claude/agents/"


def _run(
    event: dict,
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # A stray gate-opener flag or a real rendered install token from the dev
    # shell must never leak into these cases.
    env.pop("_HOOK_WATCHED_PREFIXES", None)
    env.pop("_HOOK_INSTALL_ROOT", None)
    env.pop("CLAUDE_TASK_DESCRIPTION", None)
    env.pop("_HOOK_TOOL_DESC", None)
    # Default to a code-writing sub-agent persona so tests exercise the BLOCK
    # path, not the DEC-027 top-level-orchestrator exemption (NATIVE-27-2).
    # An unset CLAUDE_AGENT_TYPE now means "top-level orchestrator loop" and
    # exits 0 unconditionally; callers that need to test the exemption itself
    # must pass {"CLAUDE_AGENT_TYPE": ""} via env_overrides.
    env.setdefault("CLAUDE_AGENT_TYPE", "forge-wire")
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK_FILE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _set_flag(session_id: str) -> Path:
    """Create the per-session gate-opener flag the hook checks for, mirroring
    the FLAG="${TMPDIR:-/tmp}/claude-socraticode-${SID}.flag" path."""
    tmpdir = os.environ.get("TMPDIR", tempfile.gettempdir())
    flag = Path(tmpdir) / f"claude-socraticode-{session_id}.flag"
    flag.touch()
    return flag


def _hook_specific(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out).get("hookSpecificOutput", {})
    except json.JSONDecodeError:
        return {}


def _bash_event(command: str, session_id: str) -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": session_id,
    }


def _read_event(file_path: str, session_id: str) -> dict:
    return {
        "tool_name": "Read",
        "tool_input": {"file_path": file_path},
        "session_id": session_id,
    }


# ---------------------------------------------------------------------------
# Mode 1 — Bash grep gate: DENY direction (exit 2 + nested deny)
# ---------------------------------------------------------------------------


class TestMode1GrepDenied:
    def test_blocked_grep_exits_2_with_nested_deny(self) -> None:
        """Given a `grep` (banned at command position) with NO prior discovery
        flag, When the gate runs, Then it hard-denies: exit code 2 AND a VALID
        nested hookSpecificOutput object with permissionDecision=deny (the
        durable backstop — the block survives even if the harness drops JSON)."""
        result = _run(_bash_event("grep -r foo .", "sgexit-m1-deny"))
        assert result.returncode == 2, (
            f"blocked grep must exit 2 (durable backstop), got "
            f"{result.returncode}: {result.stdout!r} / {result.stderr!r}"
        )
        ho = _hook_specific(result.stdout)
        assert isinstance(ho, dict), (
            f"hookSpecificOutput must be a nested object, got: {result.stdout!r}"
        )
        assert ho.get("hookEventName") == "PreToolUse"
        assert ho.get("permissionDecision") == "deny", (
            f"Expected permissionDecision=deny, got: {result.stdout!r}"
        )
        reason = ho.get("permissionDecisionReason", "")
        assert "SocratiCode-first" in reason
        assert "grep -r foo ." in reason, (
            "The deny reason must echo the blocked command."
        )

    @pytest.mark.parametrize("tool", ["rg", "find", "ack", "ag", "fgrep", "egrep"])
    def test_other_banned_tools_also_exit_2(self, tool: str) -> None:
        """Every banned discovery tool at command position denies durably."""
        result = _run(_bash_event(f"{tool} pattern", f"sgexit-m1-{tool}"))
        assert result.returncode == 2, (
            f"{tool} must exit 2, got {result.returncode}: {result.stdout!r}"
        )
        assert _hook_specific(result.stdout).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# Mode 1 — Bash grep gate: ALLOW direction (must NOT fail closed)
# ---------------------------------------------------------------------------


class TestMode1GrepAllowed:
    def test_flagged_grep_is_allowed_exit_0(self) -> None:
        """Given a grep AFTER a SocratiCode discovery call (the per-session flag
        exists), When the gate runs, Then it allows: exit 0 and empty stdout.
        This is the load-bearing ALLOW assertion — the fix must not turn the gate
        into a permanent deny."""
        flag = _set_flag("sgexit-m1-allow")
        try:
            result = _run(_bash_event("grep -r foo .", "sgexit-m1-allow"))
        finally:
            flag.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"flagged grep must be allowed (exit 0), got {result.returncode}: "
            f"{result.stdout!r} / {result.stderr!r}"
        )
        assert result.stdout.strip() == "", (
            f"allowed grep must emit nothing, got: {result.stdout!r}"
        )

    def test_non_banned_command_passes_silently(self) -> None:
        """A non-search command (e.g. `ls`) passes untouched even with no flag:
        exit 0, empty stdout. Proves the predicate is unchanged."""
        result = _run(_bash_event("ls -la", "sgexit-m1-ls"))
        assert result.returncode == 0, (
            f"non-banned command must pass (exit 0), got {result.returncode}: "
            f"{result.stdout!r}"
        )
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Mode 2 — Read gate: DENY direction (exit 2 + nested deny)
# ---------------------------------------------------------------------------


class TestMode2ReadDenied:
    def test_watched_read_exits_2_with_nested_deny(self) -> None:
        """Given a Read of a watched-prefix path with NO prior discovery flag and
        the prefixes rendered (via _HOOK_WATCHED_PREFIXES), When the gate runs,
        Then it hard-denies: exit 2 AND a nested permissionDecision=deny."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-m2-deny"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": _RENDERED_PREFIXES},
        )
        assert result.returncode == 2, (
            f"watched Read must exit 2 (durable backstop), got "
            f"{result.returncode}: {result.stdout!r} / {result.stderr!r}"
        )
        ho = _hook_specific(result.stdout)
        assert ho.get("hookEventName") == "PreToolUse"
        assert ho.get("permissionDecision") == "deny", (
            f"Expected permissionDecision=deny, got: {result.stdout!r}"
        )
        assert "/app/foo.py" in ho.get("permissionDecisionReason", "")


# ---------------------------------------------------------------------------
# Mode 2 — Read gate: ALLOW direction (must NOT fail closed)
# ---------------------------------------------------------------------------


class TestMode2ReadAllowed:
    def test_flagged_watched_read_is_allowed(self) -> None:
        """A watched-prefix Read AFTER a discovery call (flag present) is allowed:
        exit 0, empty stdout."""
        flag = _set_flag("sgexit-m2-allow")
        try:
            result = _run(
                _read_event("/app/foo.py", "sgexit-m2-allow"),
                env_overrides={"_HOOK_WATCHED_PREFIXES": _RENDERED_PREFIXES},
            )
        finally:
            flag.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"flagged watched Read must be allowed (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        assert result.stdout.strip() == ""

    def test_non_watched_read_is_allowed(self) -> None:
        """A Read OUTSIDE the watched prefixes is allowed even without a flag:
        exit 0, empty stdout. Proves the deny predicate is unchanged."""
        result = _run(
            _read_event("/README.md", "sgexit-m2-nonwatch"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": _RENDERED_PREFIXES},
        )
        assert result.returncode == 0, (
            f"non-watched Read must be allowed (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        assert result.stdout.strip() == ""

    def test_brief_cited_path_is_allowed(self) -> None:
        """A watched path explicitly cited in the task brief
        (CLAUDE_TASK_DESCRIPTION) is allowed: exit 0, empty stdout. The
        documented exception must survive the backstop change."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-m2-brief"),
            env_overrides={
                "_HOOK_WATCHED_PREFIXES": _RENDERED_PREFIXES,
                "CLAUDE_TASK_DESCRIPTION": "please read /app/foo.py for context",
            },
        )
        assert result.returncode == 0, (
            f"brief-cited Read must be allowed (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Unrendered install token — fail CLOSED (not open-silent)
# ---------------------------------------------------------------------------


class TestUnrenderedTokenFailsClosed:
    def test_unrendered_watched_prefixes_denies_watched_read(self) -> None:
        """Given the install-time /app/apps/, /app/packages/ token was NEVER rendered
        (no _HOOK_WATCHED_PREFIXES override) and a Read of a watched-looking path
        with no flag, When the gate runs, Then it fails CLOSED: exit 2 + nested
        deny whose reason names the unrendered token — NOT a silent exit-0 (which
        was the latent fail-open the WF5 review flagged)."""
        result = _run(_read_event("/app/foo.py", "sgexit-unrendered-deny"))
        assert result.returncode == 2, (
            f"unrendered token + watched Read must fail CLOSED (exit 2), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        ho = _hook_specific(result.stdout)
        assert ho.get("permissionDecision") == "deny"
        assert "/app/apps/, /app/packages/" in ho.get("permissionDecisionReason", ""), (
            "The deny reason must name the unrendered install token so the "
            "operator knows to re-run the render step."
        )

    def test_unrendered_token_with_flag_still_allows(self) -> None:
        """The unrendered-token guard lives INSIDE the Mode-2 block, which is
        gated behind 'no flag yet'. A session that already did discovery (flag
        present) must NOT be blocked by an unrendered token — exit 0, empty
        stdout. This proves the fail-closed guard does not over-deny."""
        flag = _set_flag("sgexit-unrendered-flag")
        try:
            result = _run(_read_event("/app/foo.py", "sgexit-unrendered-flag"))
        finally:
            flag.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"unrendered token + flag present must allow (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Rendered-but-EMPTY prefixes — fail CLOSED (TASK-102: not vacuous-true allow)
# ---------------------------------------------------------------------------


class TestEmptyRenderedPrefixesFailClosed:
    def test_empty_rendered_prefixes_denies_watched_read(self) -> None:
        """Given the install RENDERED /app/apps/, /app/packages/ to an EMPTY string
        (socraticode_watched_prefixes detected no dirs → ``", ".join([]) == ""``)
        and a Read of a watched-looking path with no flag, When the gate runs,
        Then it fails CLOSED: exit 2 + nested deny whose reason names the empty
        watched_prefixes misconfig — NOT a vacuous-true silent exit-0 (the
        TASK-102 fail-open: an empty prefix tuple makes ``any(... for p in ())``
        False for every path so the gate would never fire)."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-empty-deny"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": ""},
        )
        assert result.returncode == 2, (
            f"empty rendered prefixes + watched Read must fail CLOSED (exit 2), "
            f"got {result.returncode}: {result.stdout!r}"
        )
        ho = _hook_specific(result.stdout)
        assert ho.get("permissionDecision") == "deny"
        reason = ho.get("permissionDecisionReason", "")
        assert "watched_prefixes is empty" in reason, (
            "The deny reason must name the empty-watched_prefixes misconfig so the "
            f"operator knows the gate is misconfigured.\nGot: {reason!r}"
        )
        assert "failing closed" in reason

    def test_empty_rendered_prefixes_distinct_from_unrendered_token(self) -> None:
        """The empty-after-render deny message must be DISTINCT from the
        unrendered-token deny (different misconfig, different remediation). The
        empty case must NOT claim the token was 'never rendered' — it WAS
        rendered, just to nothing."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-empty-distinct"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": ""},
        )
        reason = _hook_specific(result.stdout).get("permissionDecisionReason", "")
        assert "watched_prefixes is empty" in reason
        assert "/app/apps/, /app/packages/ token was never rendered" not in reason, (
            "empty-after-render must not be conflated with the unrendered-token case"
        )

    def test_empty_rendered_prefixes_with_flag_still_allows(self) -> None:
        """The empty-prefixes guard lives INSIDE the Mode-2 no-flag block. A
        session that already did discovery (flag present) must NOT be blocked by
        empty prefixes — exit 0, empty stdout. Proves the guard does not
        over-deny once the gate has legitimately opened."""
        flag = _set_flag("sgexit-empty-flag")
        try:
            result = _run(
                _read_event("/app/foo.py", "sgexit-empty-flag"),
                env_overrides={"_HOOK_WATCHED_PREFIXES": ""},
            )
        finally:
            flag.unlink(missing_ok=True)
        assert result.returncode == 0, (
            f"empty prefixes + flag present must allow (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        assert result.stdout.strip() == ""

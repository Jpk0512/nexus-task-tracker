"""Regression tests for socraticode-gate.sh's ADVISORY-ONLY contract.

This is a SocratiCode-first *preference* gate (CONSTITUTION Article III +
CONTRACT Rule 2) — not a block. Owner directive (this cycle): "we aren't
retiring it... make it known to coders it's available, see how it goes."
Two modes, BOTH now allow-and-nudge; NEITHER ever denies:

  Mode 1 — PreToolUse(Bash): grep/rg/find/ack/ag/fgrep/egrep at command
           position, run with no prior SocratiCode discovery call this
           session (no per-session FLAG file), gets an advisory nudge and
           is ALLOWED — for every code-writing persona, named-roster (see
           TestMode1GrepExemptForCodeWriters) or not (see
           TestMode1GrepAdvisory).
  Mode 2 — PreToolUse(Read): Read of a watched-prefix path with no prior
           discovery call gets the SAME advisory-allow treatment (see
           TestMode2ReadAdvisory). Exception unchanged: a path already
           cited in the task brief silences the nudge too.

Read-only personas (orchestrator/scout/lens/lens-fast/palette) remain fully
exempt from BOTH modes with NO nudge (DEC-027) — see the exemption case
built into `_run`'s default persona choice.

The gate NEVER exits nonzero and NEVER emits permissionDecision:deny. These
tests assert the advisory JSON shape (nested hookSpecificOutput.
additionalContext, no permissionDecision key) and that a flag/non-matching
path stays fully silent (nothing to nudge about).

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
    # Default to a code-writing sub-agent persona so tests exercise the
    # advisory path, not the DEC-027 top-level-orchestrator exemption
    # (NATIVE-27-2). An unset CLAUDE_AGENT_TYPE now means "top-level
    # orchestrator loop" and exits 0 unconditionally with no nudge; callers
    # that need to test the exemption itself must pass
    # {"CLAUDE_AGENT_TYPE": ""} via env_overrides.
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
# Mode 1 — Bash grep gate: advisory direction (exit 0 + nested additionalContext)
# ---------------------------------------------------------------------------


class TestMode1GrepAdvisory:
    """Mode 1 never denies — an agent_type outside the read-only-persona
    exemption list (an unrecognized/malformed persona value included) gets
    the SAME advisory nudge as a recognized code-writer (see
    TestMode1GrepExemptForCodeWriters below), never a block."""

    UNMAPPED_PERSONA = "totally-unrecognized-persona-xyz"

    def test_blocked_grep_is_advised_and_allowed(self) -> None:
        """Given a `grep` (a SocratiCode-preferred discovery tool) from an
        agent_type outside the read-only-persona exemption, with NO prior
        discovery flag, When the gate runs, Then it ALLOWS: exit 0, a VALID
        nested hookSpecificOutput.additionalContext nudge naming
        codebase_symbol / codebase_symbols, and NO permissionDecision key at
        all (the WF6 exit-2 backstop is retired for this gate — it is
        advisory-only now)."""
        result = _run(
            _bash_event("grep -r foo .", "sgexit-m1-deny"),
            env_overrides={"CLAUDE_AGENT_TYPE": self.UNMAPPED_PERSONA},
        )
        assert result.returncode == 0, (
            f"advisory grep must exit 0 (never blocked), got "
            f"{result.returncode}: {result.stdout!r} / {result.stderr!r}"
        )
        ho = _hook_specific(result.stdout)
        assert isinstance(ho, dict), (
            f"hookSpecificOutput must be a nested object, got: {result.stdout!r}"
        )
        assert ho.get("hookEventName") == "PreToolUse"
        assert "permissionDecision" not in ho, (
            f"advisory path must never emit permissionDecision, got: {result.stdout!r}"
        )
        reason = ho.get("additionalContext", "")
        assert "SocratiCode is available and preferred for code discovery" in reason
        assert "codebase_symbol" in reason and "codebase_symbols" in reason
        assert "grep is allowed" in reason
        assert "grep -r foo ." in reason, (
            "The advisory reason must still echo the command that triggered it."
        )

    @pytest.mark.parametrize("tool", ["rg", "find", "ack", "ag", "fgrep", "egrep"])
    def test_other_banned_tools_also_advised_and_allowed(self, tool: str) -> None:
        """Every banned discovery tool at command position gets the advisory
        nudge and exit 0 for an agent_type outside the read-only-persona list."""
        result = _run(
            _bash_event(f"{tool} pattern", f"sgexit-m1-{tool}"),
            env_overrides={"CLAUDE_AGENT_TYPE": self.UNMAPPED_PERSONA},
        )
        assert result.returncode == 0, (
            f"{tool} must be advised-and-allowed (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        ho = _hook_specific(result.stdout)
        assert "permissionDecision" not in ho
        assert "additionalContext" in ho


# ---------------------------------------------------------------------------
# Mode 1 — Bash grep gate: silent-allow direction (flag / non-banned command)
# ---------------------------------------------------------------------------


class TestMode1GrepAllowed:
    def test_flagged_grep_is_allowed_exit_0(self) -> None:
        """Given a grep AFTER a SocratiCode discovery call (the per-session flag
        exists), When the gate runs, Then it allows: exit 0 and empty stdout —
        no nudge, since discovery already happened this session."""
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
            f"flagged grep must emit no nudge, got: {result.stdout!r}"
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
# Mode 1 — code-writing personas get the SAME advisory nudge
# ---------------------------------------------------------------------------


class TestMode1GrepExemptForCodeWriters:
    """SocratiCode-first is an ADVISORY preference, not a hard grep gate, for
    every code-writing persona (named-roster or not — see
    TestMode1GrepAdvisory above for the unmapped-persona case). grep/rg/find
    is allowed with NO prior discovery flag, but the gate now surfaces a
    VISIBLE nudge (owner directive: "make it known to coders it's
    available") instead of the prior fully-silent pass-through."""

    @pytest.mark.parametrize(
        "persona",
        [
            "hermes",
            "forge-ui",
            "forge-wire",
            "pipeline-data",
            "pipeline-async",
            "atlas",
            "quill-ts",
            "quill-py",
            "forge-ui-pro",
        ],
    )
    def test_code_writer_grep_is_advised_and_allowed(self, persona: str) -> None:
        result = _run(
            _bash_event("grep -r foo .", f"sgexit-m1-exempt-{persona}"),
            env_overrides={"CLAUDE_AGENT_TYPE": persona},
        )
        assert result.returncode == 0, (
            f"{persona} must be allowed (exit 0) even with no discovery "
            f"flag, got {result.returncode}: {result.stdout!r} / {result.stderr!r}"
        )
        ho = _hook_specific(result.stdout)
        assert "permissionDecision" not in ho, (
            f"{persona} must never be denied, got: {result.stdout!r}"
        )
        reason = ho.get("additionalContext", "")
        assert "SocratiCode is available and preferred for code discovery" in reason, (
            f"{persona}'s grep must carry the advisory nudge (owner directive: "
            f"make it known to coders it's available), got: {result.stdout!r}"
        )
        assert "grep is allowed" in reason


# ---------------------------------------------------------------------------
# Mode 2 — Read gate: advisory direction (exit 0 + nested additionalContext)
# ---------------------------------------------------------------------------


class TestMode2ReadAdvisory:
    def test_watched_read_is_advised_and_allowed(self) -> None:
        """Given a Read of a watched-prefix path with NO prior discovery flag and
        the prefixes rendered (via _HOOK_WATCHED_PREFIXES), When the gate runs,
        Then it ALLOWS: exit 0, a nested additionalContext nudge, and NO
        permissionDecision — Mode 2 gets the SAME advisory-allow treatment as
        Mode 1 (owner directive: nothing is hard-blocked)."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-m2-deny"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": _RENDERED_PREFIXES},
        )
        assert result.returncode == 0, (
            f"watched Read must be allowed (exit 0), got "
            f"{result.returncode}: {result.stdout!r} / {result.stderr!r}"
        )
        ho = _hook_specific(result.stdout)
        assert ho.get("hookEventName") == "PreToolUse"
        assert "permissionDecision" not in ho, (
            f"advisory path must never emit permissionDecision, got: {result.stdout!r}"
        )
        reason = ho.get("additionalContext", "")
        assert "/app/foo.py" in reason
        assert "SocratiCode is available and preferred for code discovery" in reason


# ---------------------------------------------------------------------------
# Mode 2 — Read gate: silent-allow direction (flag / non-watched / brief-cited)
# ---------------------------------------------------------------------------


class TestMode2ReadAllowed:
    def test_flagged_watched_read_is_allowed(self) -> None:
        """A watched-prefix Read AFTER a discovery call (flag present) is allowed:
        exit 0, empty stdout — no nudge, discovery already happened."""
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
        exit 0, empty stdout. Proves the nudge predicate is unchanged."""
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
        documented exception must survive the advisory-only change."""
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
# Unrendered install token — advisory, not fail-closed
# ---------------------------------------------------------------------------


class TestUnrenderedTokenAdvisory:
    def test_unrendered_watched_prefixes_advises_and_allows(self) -> None:
        """Given the install-time /app/apps/, /app/packages/ token was NEVER rendered
        (no _HOOK_WATCHED_PREFIXES override) and a Read of a watched-looking path
        with no flag, When the gate runs, Then it ALLOWS with an advisory nudge
        naming the unrendered token — NOT a deny. Every Mode-2 deny path is
        retired; misconfiguration now just means the check couldn't run, so
        the tool call proceeds with a nudge instead of a block."""
        result = _run(_read_event("/app/foo.py", "sgexit-unrendered-deny"))
        assert result.returncode == 0, (
            f"unrendered token + watched Read must be allowed (exit 0), got "
            f"{result.returncode}: {result.stdout!r}"
        )
        ho = _hook_specific(result.stdout)
        assert "permissionDecision" not in ho
        reason = ho.get("additionalContext", "")
        assert "/app/apps/, /app/packages/" in reason, (
            "The advisory reason must still name the unrendered install token so "
            "the operator knows to re-run the render step."
        )

    def test_unrendered_token_with_flag_still_silently_allows(self) -> None:
        """The unrendered-token nudge lives INSIDE the Mode-2 block, which is
        gated behind 'no flag yet'. A session that already did discovery (flag
        present) gets NO nudge — exit 0, empty stdout."""
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
# Rendered-but-EMPTY prefixes — advisory, not fail-closed
# ---------------------------------------------------------------------------


class TestEmptyRenderedPrefixesAdvisory:
    def test_empty_rendered_prefixes_advises_and_allows(self) -> None:
        """Given the install RENDERED /app/apps/, /app/packages/ to an EMPTY string
        (socraticode_watched_prefixes detected no dirs → ``", ".join([]) == ""``)
        and a Read of a watched-looking path with no flag, When the gate runs,
        Then it ALLOWS with an advisory nudge naming the empty-prefixes
        misconfig — NOT a deny (every Mode-2 deny path is retired)."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-empty-deny"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": ""},
        )
        assert result.returncode == 0, (
            f"empty rendered prefixes + watched Read must be allowed (exit 0), "
            f"got {result.returncode}: {result.stdout!r}"
        )
        ho = _hook_specific(result.stdout)
        assert "permissionDecision" not in ho
        reason = ho.get("additionalContext", "")
        assert "watched_prefixes is empty" in reason, (
            "The advisory reason must name the empty-watched_prefixes misconfig so "
            f"the operator knows to fix the stack profile.\nGot: {reason!r}"
        )

    def test_empty_rendered_prefixes_distinct_from_unrendered_token(self) -> None:
        """The empty-after-render advisory must be DISTINCT from the
        unrendered-token advisory (different misconfig, different remediation). The
        empty case must NOT claim the token was 'never rendered' — it WAS
        rendered, just to nothing."""
        result = _run(
            _read_event("/app/foo.py", "sgexit-empty-distinct"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": ""},
        )
        reason = _hook_specific(result.stdout).get("additionalContext", "")
        assert "watched_prefixes is empty" in reason
        assert "/app/apps/, /app/packages/ token was never rendered" not in reason, (
            "empty-after-render must not be conflated with the unrendered-token case"
        )

    def test_empty_rendered_prefixes_with_flag_still_silently_allows(self) -> None:
        """The empty-prefixes advisory lives INSIDE the Mode-2 no-flag block. A
        session that already did discovery (flag present) gets NO nudge —
        exit 0, empty stdout."""
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


# ---------------------------------------------------------------------------
# R1-T02: fire (heartbeat) telemetry on every exit path
# ---------------------------------------------------------------------------
# heartbeat-emitter.sh has no env override; it walks up from its own script
# location for a .memory/ ancestor. Isolate via a scratch two-level
# .claude/hooks/ copy so these tests never touch the real repo's
# .memory/files/hook_heartbeat.jsonl.


def _run_scratch(
    event: dict, *, env_overrides: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    import shutil

    tmp_path = Path(tempfile.mkdtemp())
    scratch_root = tmp_path / "repo"
    scratch_hooks = scratch_root / ".claude" / "hooks"
    scratch_hooks.mkdir(parents=True)
    for name in ("heartbeat-emitter.sh", "socraticode-gate.sh"):
        shutil.copy(HOOK_FILE.parent / name, scratch_hooks / name)
    (scratch_root / ".memory" / "files").mkdir(parents=True)

    env = dict(os.environ)
    env.pop("_HOOK_WATCHED_PREFIXES", None)
    env.pop("_HOOK_INSTALL_ROOT", None)
    env.pop("CLAUDE_TASK_DESCRIPTION", None)
    env.pop("_HOOK_TOOL_DESC", None)
    env.setdefault("CLAUDE_AGENT_TYPE", "forge-wire")
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["bash", str(scratch_hooks / "socraticode-gate.sh")],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    heartbeat_path = scratch_root / ".memory" / "files" / "hook_heartbeat.jsonl"
    return result, heartbeat_path


class TestTelemetry:
    def test_persona_exempt_allow_emits_heartbeat(self) -> None:
        """The DEC-027 read-only-persona exemption exit is an early-return
        path — a common bug is instrumenting only the final/advise path and
        missing early returns like this one."""
        result, heartbeat_path = _run_scratch(
            _bash_event("grep -r foo .", "sgexit-hb-exempt"),
            env_overrides={"CLAUDE_AGENT_TYPE": "scout"},
        )
        assert result.returncode == 0, "regression: persona-exempt exit code unchanged"
        assert heartbeat_path.exists()
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "socraticode-gate"
        assert hb["event"] == "PreToolUse"
        assert hb["decision"] == "allow"
        assert "ts" in hb and "latency_ms" in hb

    def test_mode2_advisory_emits_heartbeat_advise(self) -> None:
        result, heartbeat_path = _run_scratch(
            _read_event("/app/foo.py", "sgexit-hb-m2deny"),
            env_overrides={"_HOOK_WATCHED_PREFIXES": _RENDERED_PREFIXES},
        )
        assert result.returncode == 0, "regression: Mode-2 advisory must never block"
        ho = _hook_specific(result.stdout)
        assert "permissionDecision" not in ho
        assert "additionalContext" in ho
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "socraticode-gate"
        assert hb["decision"] == "advise"

    def test_mode1_grep_advisory_emits_heartbeat_advise(self) -> None:
        result, heartbeat_path = _run_scratch(_bash_event("grep -r foo .", "sgexit-hb-m1deny"))
        assert result.returncode == 0, "regression: Mode-1 grep advisory must never block"
        ho = _hook_specific(result.stdout)
        assert "permissionDecision" not in ho
        assert "additionalContext" in ho
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "socraticode-gate"
        assert hb["decision"] == "advise"

    def test_final_silent_pass_emits_heartbeat_allow(self) -> None:
        result, heartbeat_path = _run_scratch(_bash_event("ls -la", "sgexit-hb-allow"))
        assert result.returncode == 0, "regression: silent-pass exit code unchanged"
        assert result.stdout.strip() == ""
        lines = [ln for ln in heartbeat_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        hb = json.loads(lines[0])
        assert hb["hook"] == "socraticode-gate"
        assert hb["decision"] == "allow"

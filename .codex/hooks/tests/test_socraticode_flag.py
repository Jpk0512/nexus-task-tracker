"""Regression tests for socraticode-flag.sh (WF6 ENFORCEMENT).

Contract (CLAUDE.md "Codebase Search"): the grep/rg/find gate opens only after a
SocratiCode discovery call that RETURNS indexed results. socraticode-flag.sh is
the PostToolUse hook that writes the session-scoped gate-opener flag — and it must
write that flag ONLY when the tool_response actually contains results.

The bug being fixed: the hook used to `touch` the flag unconditionally on tool
FIRE — even on a "No symbols matching" / empty / error response — opening the grep
gate before the index was confirmed. That is a fail-OPEN of a security-relevant
gate, the worst outcome. The fix gates the flag on RESULTS.

These tests assert BOTH directions:
  - results-bearing responses (across the tool_response shapes Claude Code emits)
    -> flag IS written;
  - no-result / empty / error / unindexed responses                  -> flag is
    NOT written (fail-safe: gate stays closed).

Invoked exactly as the harness does: a PostToolUse payload on stdin, with a
unique session_id per case so the temp flag path is isolated and assertable.
Mirrors the subprocess-with-stdin-JSON style in tests/test_p2_hooks.py.

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_socraticode_flag.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parent.parent / "socraticode-flag.sh"
)

# The discovery tools wired to this hook in .claude/settings.json.
DISCOVERY_TOOL = "mcp__plugin_socraticode_socraticode__codebase_symbols"


def _flag_path(session_id: str) -> Path:
    """Mirror the hook's own flag-path derivation."""
    tmp = os.environ.get("TMPDIR", "/tmp")
    return Path(tmp) / f"claude-socraticode-{session_id}.flag"


def _run(tool_response, *, tool_name: str = DISCOVERY_TOOL) -> tuple[bool, str]:
    """Invoke the hook with a fresh session_id; return (flag_written, stderr)."""
    session_id = f"test-{uuid.uuid4().hex}"
    flag = _flag_path(session_id)
    if flag.exists():
        flag.unlink()
    payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_response": tool_response,
    }
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, (
        f"hook must always exit 0 (advisory side-effect), got "
        f"{proc.returncode}: {proc.stderr!r}"
    )
    written = flag.exists()
    if written:
        flag.unlink()
    return written, proc.stderr


# ---------------------------------------------------------------------------
# POSITIVE — results-bearing responses MUST open the gate
# ---------------------------------------------------------------------------


class TestResultsBearingSetsFlag:
    def test_content_blocks_with_match_count_sets_flag(self) -> None:
        """The live MCP shape: tool_response.content[] text blocks whose header
        is "Symbols matching '...' (N):" with N>=1 -> flag IS written."""
        resp = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Symbols matching 'read_state' (3):\n"
                        "  - read_state  (broker/state.py:12)\n"
                        "  - read_state_cached  (broker/state.py:40)\n"
                        "  - read_state_raw  (broker/db.py:9)"
                    ),
                }
            ]
        }
        written, _ = _run(resp)
        assert written, "results-bearing discovery response must open the gate"

    def test_plain_string_response_with_locations_sets_flag(self) -> None:
        """tool_response as a plain string carrying path:line result rows."""
        resp = (
            "Matches (2):\n"
            "  - nexus_validate_brief  (broker/server.py:88)\n"
            "  - nexus_validate_brief_tool  (broker/server.py:120)"
        )
        written, _ = _run(resp)
        assert written, "string response with N>=1 matches must open the gate"

    def test_found_n_phrasing_sets_flag(self) -> None:
        """A graph/impact response phrased as "Found N ..." with N>=1."""
        resp = {"text": "Found 5 dependents of broker.state.read_state."}
        written, _ = _run(resp, tool_name="mcp__plugin_socraticode_socraticode__codebase_impact")
        assert written, "'Found 5 ...' must be treated as results"

    def test_bulleted_rows_without_count_header_sets_flag(self) -> None:
        """A response that lists hits as bullet rows but no explicit (N) header
        still counts as results (the rows ARE the result set)."""
        resp = {
            "content": [
                {
                    "type": "text",
                    "text": "- src/broker/db.py: open_db\n- src/broker/state.py: read_state",
                }
            ]
        }
        written, _ = _run(resp)
        assert written, "bulleted result rows must open the gate"


# ---------------------------------------------------------------------------
# NEGATIVE — no-result / empty / error responses MUST NOT open the gate
# ---------------------------------------------------------------------------


class TestNoResultDoesNotSetFlag:
    def test_no_symbols_matching_does_not_set_flag(self) -> None:
        """The canonical empty result: "No symbols matching '...'." -> flag NOT
        written (this is the contract violation the fix closes)."""
        resp = {"content": [{"type": "text", "text": "No symbols matching 'zzzz'."}]}
        written, _ = _run(resp)
        assert not written, (
            "a 'No symbols matching' response must NOT open the grep gate"
        )

    def test_empty_string_response_does_not_set_flag(self) -> None:
        """An empty tool_response -> no results -> gate stays closed."""
        written, _ = _run("")
        assert not written, "an empty response must NOT open the gate"

    def test_empty_content_list_does_not_set_flag(self) -> None:
        """tool_response.content == [] (no blocks) -> gate stays closed."""
        written, _ = _run({"content": []})
        assert not written, "an empty content list must NOT open the gate"

    def test_zero_count_header_does_not_set_flag(self) -> None:
        """A header that explicitly reports (0) matches -> gate stays closed."""
        resp = {"content": [{"type": "text", "text": "Symbols matching 'qqq' (0):"}]}
        written, _ = _run(resp)
        assert not written, "a (0) match header must NOT open the gate"

    def test_not_indexed_response_does_not_set_flag(self) -> None:
        """An 'not indexed / please index' response -> gate stays closed
        (the model must index, never fall back to grep)."""
        resp = {
            "content": [
                {"type": "text", "text": "Project not indexed. Please run codebase_index."}
            ]
        }
        written, _ = _run(resp)
        assert not written, "an unindexed response must NOT open the gate"

    def test_no_context_artifacts_does_not_set_flag(self) -> None:
        """'No context artifacts configured' (context_search miss) -> closed."""
        resp = "No context artifacts configured for this project."
        written, _ = _run(
            resp, tool_name="mcp__plugin_socraticode_socraticode__codebase_context_search"
        )
        assert not written, "'No context artifacts' must NOT open the gate"

    def test_error_dict_response_does_not_set_flag(self) -> None:
        """An MCP error-shaped response (isError true) -> gate stays closed."""
        resp = {
            "isError": True,
            "content": [{"type": "text", "text": "MCP error -32000: Connection closed"}],
        }
        written, _ = _run(resp)
        assert not written, "an error response must NOT open the gate"

    def test_traceback_response_does_not_set_flag(self) -> None:
        """A raw traceback in the response -> gate stays closed."""
        resp = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            "RuntimeError: index missing"
        )
        written, _ = _run(resp)
        assert not written, "a traceback response must NOT open the gate"


# ---------------------------------------------------------------------------
# Hardening — malformed input must not fail-open
# ---------------------------------------------------------------------------


def test_malformed_stdin_does_not_set_flag() -> None:
    """Non-JSON stdin -> hook exits 0 and does NOT open the gate (fail-safe)."""
    session_id = f"test-{uuid.uuid4().hex}"
    flag = _flag_path(session_id)
    if flag.exists():
        flag.unlink()
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input="this is not json",
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    # Unknown session_id falls back to "unknown" inside the hook; assert OUR
    # per-test flag was not created either way.
    assert not flag.exists(), "malformed input must NOT open this session's gate"


def test_missing_tool_response_key_does_not_set_flag() -> None:
    """A payload with no tool_response at all -> gate stays closed."""
    session_id = f"test-{uuid.uuid4().hex}"
    flag = _flag_path(session_id)
    if flag.exists():
        flag.unlink()
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"session_id": session_id, "tool_name": DISCOVERY_TOOL}),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert not flag.exists(), "absent tool_response must NOT open the gate"

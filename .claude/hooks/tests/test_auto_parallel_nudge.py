"""Tests for the auto-parallel-nudge.sh advisory hook.

The hook is a UserPromptSubmit advisory: when a user prompt reads as delegation /
implementation work (imperative verbs, multi-step, or an enumerated list) it
injects a brief additionalContext nudge to PREFER a Workflow. It must be LOW
NOISE (silent on trivial / conversational / pure-question prompts), must NEVER
block, and must ALWAYS exit 0.

Each hook is invoked as a subprocess exactly as the Claude Code harness does:
the JSON payload on stdin, exit code + stdout asserted. The hook body runs under
bare python3 (bash-shebang gate), so these assertions also exercise the
3.9-safe path the deployable uses.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = HOOKS_DIR / "auto-parallel-nudge.sh"

NUDGE_TAG = "[auto-parallel-nudge]"


def run_hook(prompt_payload: str, env: dict | None = None) -> tuple[int, str, str]:
    """Invoke the hook via /bin/bash with the given raw stdin string.

    Post-F2-03 auto-parallel-nudge.sh is `exec _ping_shim.py prompt.submitted
    auto-parallel-nudge`; the delegation-work classifier runs daemon-resident
    (handle_auto_parallel_nudge). `env` carries the DEFAULT `resident_daemon.env`
    seams so the shim reaches a live daemon. This package twin is a NON-meta
    (installed) tenant, so the handler emits `_NUDGE_TEXT_INSTALLED` — the same
    `[auto-parallel-nudge]` tag WITHOUT the meta-only DEC-017 citation."""
    merged = {**os.environ}
    if env:
        merged.update(env)
    result = subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        input=prompt_payload,
        capture_output=True,
        text=True,
        env=merged,
        timeout=15,
    )
    return result.returncode, result.stdout, result.stderr


def run_prompt(prompt: str, env: dict | None = None) -> tuple[int, str, str]:
    return run_hook(json.dumps({"prompt": prompt}), env=env)


def _nudge_context(out: str) -> str:
    out = out.strip()
    if not out:
        return ""
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return ""
    return parsed.get("hookSpecificOutput", {}).get("additionalContext", "")


def assert_nudges(prompt: str, env: dict | None = None) -> None:
    code, out, err = run_prompt(prompt, env=env)
    assert code == 0, f"hook must always exit 0, got {code}; stderr={err!r}"
    ctx = _nudge_context(out)
    assert NUDGE_TAG in ctx, f"expected a nudge for {prompt!r}, got stdout={out!r}"


def assert_silent(prompt: str) -> None:
    code, out, err = run_prompt(prompt)
    assert code == 0, f"hook must always exit 0, got {code}; stderr={err!r}"
    assert out.strip() == "", f"expected SILENCE for {prompt!r}, got stdout={out!r}"


# ─── nudges on delegation / implementation work ──────────────────────────────


class TestNudgesOnDelegationWork:
    def test_imperative_implement_and_fix(self, resident_daemon) -> None:
        assert_nudges(
            "Please implement the new caching layer and fix the flaky test in module X",
            env=dict(resident_daemon.env),
        )

    def test_build_request(self, resident_daemon) -> None:
        assert_nudges(
            "Build a health dashboard endpoint and wire it into the router pipeline",
            env=dict(resident_daemon.env),
        )

    def test_enumerated_dash_list(self, resident_daemon) -> None:
        assert_nudges(
            "Do these:\n- add logging\n- write tests\n- update the docs",
            env=dict(resident_daemon.env),
        )

    def test_numbered_list(self, resident_daemon) -> None:
        assert_nudges(
            "Tasks:\n1. refactor the parser\n2. add a regression test\n3. update CHANGELOG",
            env=dict(resident_daemon.env),
        )

    def test_lettered_subtasks(self, resident_daemon) -> None:
        assert_nudges(
            "Work items:\n(a) migrate the schema\n(b) backfill rows\n(c) verify counts",
            env=dict(resident_daemon.env),
        )

    def test_single_substantive_imperative(self, resident_daemon) -> None:
        # A nudge is valuable even for a SINGLE task.
        assert_nudges(
            "Refactor the broker state module to use a dataclass instead",
            env=dict(resident_daemon.env),
        )

    def test_investigate_and_diagnose(self, resident_daemon) -> None:
        assert_nudges(
            "Investigate why the lens gate is rejecting valid briefs and diagnose the cause",
            env=dict(resident_daemon.env),
        )


# ─── silence on trivial / conversational / question prompts ──────────────────


class TestSilentOnTrivialOrQuestions:
    def test_pure_question(self) -> None:
        assert_silent("what does the broker gate do?")

    def test_how_question_with_action_verb(self) -> None:
        # An action verb inside a question must NOT trip the nudge.
        assert_silent("how do I implement a custom hook in this repo?")

    def test_why_question(self) -> None:
        assert_silent("why is the router emitting no persona chip?")

    def test_greeting(self) -> None:
        assert_silent("hey, thanks for that!")

    def test_short_imperative_below_threshold(self) -> None:
        assert_silent("fix it")

    def test_conversational_acknowledgement(self) -> None:
        assert_silent("great, that worked")

    def test_empty_prompt(self) -> None:
        assert_silent("")

    def test_whitespace_only_prompt(self) -> None:
        assert_silent("   \n  \t ")


# ─── robustness: never blocks, always exit 0, fail-open ──────────────────────


class TestRobustness:
    def test_malformed_stdin_is_silent_and_exit_0(self) -> None:
        code, out, err = run_hook("not json at all")
        assert code == 0, f"malformed stdin must exit 0, got {code}; stderr={err!r}"
        assert out.strip() == "", f"malformed stdin must be silent, got {out!r}"

    def test_empty_stdin_is_silent_and_exit_0(self) -> None:
        code, out, _err = run_hook("")
        assert code == 0
        assert out.strip() == ""

    def test_missing_prompt_key_is_silent(self) -> None:
        code, out, _err = run_hook(json.dumps({"session_id": "S-1"}))
        assert code == 0
        assert out.strip() == ""

    def test_non_string_prompt_is_silent(self) -> None:
        code, out, _err = run_hook(json.dumps({"prompt": ["a", "b"]}))
        assert code == 0
        assert out.strip() == ""

    def test_never_emits_permission_decision(self, resident_daemon) -> None:
        # Advisory only: it must never carry a blocking permissionDecision.
        _code, out, _err = run_prompt(
            "implement the feature, add tests, and update the docs",
            env=dict(resident_daemon.env),
        )
        assert "permissionDecision" not in out
        ctx = _nudge_context(out)
        assert NUDGE_TAG in ctx

    def test_emitted_json_is_well_formed_userpromptsubmit(self, resident_daemon) -> None:
        _code, out, _err = run_prompt(
            "build the new endpoint and write integration tests for it",
            env=dict(resident_daemon.env),
        )
        parsed = json.loads(out.strip())
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert NUDGE_TAG in hso["additionalContext"]

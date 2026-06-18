"""Regression tests for verify-deliverables.sh (SubagentStop contract gate).

WF6 enforcement fix. The hook is a CONTRACT-ENFORCEMENT gate that previously
FAILED OPEN SILENTLY on any internal error:

  - `set -e` aborted the script the moment the python validator subprocess exited
    non-zero (an internal error), so the block jq never ran -> the violation was
    swallowed and the sub-agent was allowed to finish.
  - `2>&1` folded python tracebacks into the report; the downstream
    `jq ... 2>/dev/null` then masked the malformed report -> no signal at all.
  - the unrendered `/Users/john.keeney/nexus-task-tracker` install token made the manifest path
    nonexistent -> an early silent `exit 0` (gate inert).

The fix makes the gate FAIL CLOSED + LOUD on internal error (decision:block,
exit 2, reason echoed to stderr) while keeping the legitimate verification logic
intact: a real violation still blocks and a clean run still passes silently.

These tests assert BOTH directions (the critical fail-open guard for a gate):
  * a clean, conforming run still PASSES silently (exit 0, no decision)        [allow]
  * a real contract violation still BLOCKS (decision:block)                    [deny]
  * an INDUCED internal error (corrupt manifest / interpreter crash) no longer
    silently passes — it surfaces a LOUD block and exits 2 (fail-closed)       [deny]
  * a missing / unrendered-token manifest fails closed, not silently           [deny]
  * genuinely-nothing-to-check inputs (no persona / no message) still pass     [allow]

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_verify_deliverables.py -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOK_FILE = Path(__file__).resolve().parent.parent / "verify-deliverables.sh"

# A minimal deliverables manifest mirroring the real shape: forge may write
# under app/** and must NOT write under ingestion/**; a completion marker H2 is
# required.
_DELIVERABLES = {
    "forge": {
        "expected_paths": ["app/**/*.ts"],
        "forbidden_paths": ["ingestion/**"],
        "required_markers": ["## NEXUS:DONE", "## NEXUS:BLOCKED"],
    }
}


@pytest.fixture()
def manifest(tmp_path: Path) -> Path:
    """A valid deliverables.json the hook reads via _HOOK_DELIVERABLES."""
    p = tmp_path / "deliverables.json"
    p.write_text(json.dumps(_DELIVERABLES), encoding="utf-8")
    return p


def _run(
    event: dict,
    *,
    deliverables: Path | str | None = None,
) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    if deliverables is not None:
        env["_HOOK_DELIVERABLES"] = str(deliverables)
    return subprocess.run(
        ["bash", str(HOOK_FILE)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def _decision(out: str) -> dict:
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


# --- forge events --------------------------------------------------------- #


def _forge_event(files_changed: list[str], *, marker: str = "## NEXUS:DONE") -> dict:
    body = (
        f"{marker}\n"
        "```json\n"
        + json.dumps({"files_changed": files_changed})
        + "\n```"
    )
    return {"agent_persona": "forge", "last_assistant_message": body}


# =========================================================================== #
# ALLOW direction — a conforming run must still pass silently.
# =========================================================================== #


def test_clean_run_passes_silently(manifest: Path) -> None:
    """Given a forge return that writes only under app/** and carries a
    completion marker, When the gate runs, Then it passes silently
    (exit 0, no decision)."""
    result = _run(_forge_event(["app/x.ts"]), deliverables=manifest)
    assert result.returncode == 0, (
        f"clean run must exit 0, got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    assert result.stdout.strip() == "", (
        f"clean run must emit no decision, got: {result.stdout!r}"
    )
    assert _decision(result.stdout) == {}


def test_no_persona_passes_silently(manifest: Path) -> None:
    """An input with no identifiable persona has nothing to enforce — silent
    pass (exit 0). This guards against the fail-closed path over-firing."""
    result = _run({"last_assistant_message": "## NEXUS:DONE\nhi"}, deliverables=manifest)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_no_message_warns_extract_miss_not_block(manifest: Path) -> None:
    """S1-22 EXTRACT_OK canary: a non-empty JSON payload with NO extractable
    assistant text passes (exit 0 — never a fail-closed block) but warns
    LOUDLY via additionalContext, so harness schema drift cannot silently
    disarm the gate. Dedup'd once per session via a session_id flag file."""
    import uuid

    sid = f"pytest-vd-miss-{uuid.uuid4().hex}"
    payload = {"session_id": sid, "agent_persona": "forge"}
    result = _run(payload, deliverables=manifest)
    assert result.returncode == 0, result.stderr
    assert "EXTRACT-MISS" in result.stdout, (
        f"expected a LOUD EXTRACT-MISS warning, got: {result.stdout!r}"
    )
    assert '"decision"' not in result.stdout  # warn, never block
    # Once per session: the second identical return stays silent.
    result2 = _run(payload, deliverables=manifest)
    assert result2.returncode == 0
    assert result2.stdout.strip() == ""


def test_unknown_persona_passes_silently(manifest: Path) -> None:
    """A persona with no manifest entry is unconstrained — silent pass."""
    event = {
        "agent_persona": "scribe",
        "last_assistant_message": "## NEXUS:DONE\nnothing to check",
    }
    result = _run(event, deliverables=manifest)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# =========================================================================== #
# DENY direction — a real contract violation must still block.
# =========================================================================== #


def test_forbidden_path_violation_blocks(manifest: Path) -> None:
    """Given a forge return that wrote a file under the forbidden ingestion/**
    glob, When the gate runs, Then it emits decision:block naming the
    violation. This is the legitimate enforcement that must NOT regress."""
    result = _run(_forge_event(["ingestion/bad.py"]), deliverables=manifest)
    assert result.returncode == 0, result.stderr
    decision = _decision(result.stdout)
    assert decision.get("decision") == "block", (
        f"forbidden_paths violation must block, got: {result.stdout!r}"
    )
    assert "forbidden_paths violation" in decision.get("reason", "")
    assert "ingestion/bad.py" in decision.get("reason", "")


def test_missing_completion_marker_blocks(manifest: Path) -> None:
    """A forge return with NO completion marker H2 must block."""
    event = {
        "agent_persona": "forge",
        "last_assistant_message": (
            "done\n```json\n" + json.dumps({"files_changed": ["app/x.ts"]}) + "\n```"
        ),
    }
    result = _run(event, deliverables=manifest)
    assert result.returncode == 0, result.stderr
    decision = _decision(result.stdout)
    assert decision.get("decision") == "block", (
        f"missing marker must block, got: {result.stdout!r}"
    )
    assert "completion marker" in decision.get("reason", "").lower()


# =========================================================================== #
# FAIL-CLOSED — an internal error must surface LOUDLY, never silently allow.
# This is the core WF6 fix: previously `set -e` + `2>&1` swallowed the failure.
# =========================================================================== #


def test_corrupt_manifest_fails_closed_not_silent(tmp_path: Path) -> None:
    """Given a corrupt (non-JSON) deliverables manifest — which crashes the
    python validator with a JSONDecodeError — AND a return that WOULD be a
    forbidden_paths violation, When the gate runs, Then it does NOT silently
    pass: it emits decision:block, exits 2, and echoes the reason to stderr.

    This is the exact fail-open the bug used to produce: the validator crash
    aborted the script before the block jq, so the violation was swallowed and
    the sub-agent allowed to finish."""
    bad = tmp_path / "deliverables.json"
    bad.write_text("this is { not valid json", encoding="utf-8")
    result = _run(_forge_event(["ingestion/bad.py"]), deliverables=bad)

    # The worst-case fail-open would be: exit 0 + empty stdout. Assert the
    # OPPOSITE on every axis.
    assert result.returncode == 2, (
        f"internal error must fail CLOSED (exit 2), got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    decision = _decision(result.stdout)
    assert decision.get("decision") == "block", (
        f"internal error must emit a block decision, got: {result.stdout!r}"
    )
    assert "INTERNAL ERROR" in decision.get("reason", ""), (
        f"block reason must name the internal error, got: {decision!r}"
    )
    # LOUD: the reason is also echoed to stderr so it surfaces even if the
    # decision JSON is not rendered.
    assert "INTERNAL ERROR" in result.stderr, (
        f"internal error must be echoed loudly to stderr, got: {result.stderr!r}"
    )


def test_interpreter_crash_fails_closed(tmp_path: Path) -> None:
    """Given a manifest whose top-level JSON is a list (not a dict) — config
    iteration via .items() raises AttributeError inside the validator — the
    gate fails closed (exit 2 + block) rather than swallowing the traceback.

    This exercises the generic BaseException handler / non-zero-exit path, a
    DIFFERENT crash site than the json.load failure, proving the fail-closed
    behaviour is not specific to one error."""
    bad = tmp_path / "deliverables.json"
    bad.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    result = _run(_forge_event(["app/x.ts"]), deliverables=bad)
    assert result.returncode == 2, (
        f"a validator crash must fail closed (exit 2), got {result.returncode}: "
        f"{result.stdout!r} / {result.stderr!r}"
    )
    decision = _decision(result.stdout)
    assert decision.get("decision") == "block"
    assert "INTERNAL ERROR" in decision.get("reason", "")
    assert "INTERNAL ERROR" in result.stderr


def test_missing_manifest_fails_closed(tmp_path: Path) -> None:
    """Given a deliverables path that does not exist (the unrendered
    /Users/john.keeney/nexus-task-tracker token case), When the gate runs, Then it fails closed
    (exit 2 + block) instead of the old silent early exit 0."""
    missing = tmp_path / "nope" / "deliverables.json"
    result = _run(_forge_event(["ingestion/bad.py"]), deliverables=missing)
    assert result.returncode == 2, (
        f"missing manifest must fail closed (exit 2), got {result.returncode}: "
        f"{result.stdout!r}"
    )
    decision = _decision(result.stdout)
    assert decision.get("decision") == "block"
    reason = decision.get("reason", "")
    assert "INTERNAL ERROR" in reason
    assert "not found" in reason


def test_internal_error_block_reason_is_valid_json(tmp_path: Path) -> None:
    """The fail-closed block must be a SINGLE valid JSON object on stdout (the
    harness drops malformed output) — a regression guard ensuring the loud
    block is actually consumable, not just printed."""
    bad = tmp_path / "deliverables.json"
    bad.write_text("{ broken", encoding="utf-8")
    result = _run(_forge_event(["app/x.ts"]), deliverables=bad)
    assert result.returncode == 2
    # json.loads over the FULL stdout must succeed and yield exactly the two
    # expected keys — proving no traceback bled into stdout alongside the JSON.
    payload = json.loads(result.stdout)
    assert set(payload.keys()) == {"decision", "reason"}
    assert payload["decision"] == "block"

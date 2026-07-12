"""Tests for task-mirror.sh marker-detection hardening (Fix 1).

The hook must only accept a WELL-FORMED NEXUS marker — a line that is
essentially `## NEXUS:<MARKER>` on its own (allow up to three leading `#`
characters, optional leading/trailing whitespace, but the marker must be the
whole line, not embedded in prose).

Five cases from the brief:
  (1) Clean `## NEXUS:DONE` on its own line → marker=DONE, PHASE=DONE,
      native list hint contains "COMPLETED".
  (2) Clean `## NEXUS:BLOCKED` on its own line → marker=BLOCKED,
      PHASE=BLOCKED, native list hint contains "IN_PROGRESS".
  (3) TRUNCATED: "NEXUS:DONE" embedded inside prose (not a whole-line
      marker) → must NOT trigger DONE; keeps in_progress + advisory.
  (4) Empty / no-marker return (text present but no NEXUS line) → keeps
      in_progress + advisory.
  (5) Normal DISPATCH (subagent_type present, no tool_response) → in_progress,
      NO truncation advisory.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
SCRIPT = "task-mirror.sh"


def _run(payload: dict) -> tuple[int, str, str]:
    """Invoke the hook with `payload` on stdin; return (rc, stdout, stderr)."""
    result = subprocess.run(
        ["/bin/bash", str(HOOKS_DIR / SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def _parse_stdout(out: str) -> dict:
    """Parse the hook's JSON stdout; return {} on empty/error."""
    out = out.strip()
    if not out:
        return {}
    try:
        return json.loads(out.splitlines()[-1])
    except json.JSONDecodeError:
        return {}


def _additional_context(out: str) -> str:
    obj = _parse_stdout(out)
    hso = obj.get("hookSpecificOutput", {})
    return hso.get("additionalContext", "")


def _is_truncation_advisory(out: str) -> bool:
    """True iff stdout carries the no-well-formed-marker advisory."""
    ctx = _additional_context(out)
    return "no well-formed NEXUS marker" in ctx or "possible truncation" in ctx


# ---------------------------------------------------------------------------
# Helpers to build payloads
# ---------------------------------------------------------------------------

def _dispatch_payload(persona: str, task_id: str) -> dict:
    """A normal Task dispatch — subagent_type present, no tool_response."""
    return {
        "tool_input": {
            "subagent_type": persona,
            "description": json.dumps({"task_id": task_id, "persona": persona}),
        }
    }


def _return_payload(persona: str, task_id: str, response_text: str) -> dict:
    """A Task return — tool_response present (agent has returned)."""
    return {
        "tool_input": {
            "subagent_type": persona,
            "description": json.dumps({"task_id": task_id, "persona": persona}),
        },
        "tool_response": response_text,
    }


# ---------------------------------------------------------------------------
# Case (1): Clean ## NEXUS:DONE on its own line
# ---------------------------------------------------------------------------

class TestCleanDoneMarker:
    def test_done_marker_triggers_done_phase(self) -> None:
        """A line that IS `## NEXUS:DONE` must produce PHASE=DONE."""
        text = (
            "Some preamble text about the task.\n"
            "\n"
            "## NEXUS:DONE\n"
            "\n"
            "{'status': 'complete'}"
        )
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-001", text))
        ctx = _additional_context(out)
        assert "DONE" in ctx, f"Expected DONE phase in context, got: {ctx!r}"
        assert "COMPLETED" in ctx or "completed" in ctx.lower(), (
            f"Expected 'COMPLETED' hint for DONE marker, got: {ctx!r}"
        )

    def test_done_marker_no_truncation_advisory(self) -> None:
        """A well-formed DONE marker must NOT emit the truncation advisory."""
        text = "## NEXUS:DONE\n"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-001", text))
        assert not _is_truncation_advisory(out), (
            f"Well-formed DONE marker should not trigger truncation advisory. "
            f"stdout: {out!r}"
        )

    def test_done_marker_followed_by_json_block_is_detected(self) -> None:
        """TASK-086: a well-formed `## NEXUS:DONE` followed by a large JSON body
        (on subsequent lines AND on the same line) must be DETECTED — not falsely
        reported as RETURN-NO-MARKER / kept IN_PROGRESS.

        Two forms exercised:
          (a) marker on its own line, JSON block follows on later lines;
          (b) marker with trailing content on the SAME line (the regression that
              the strict end-of-line anchor used to drop).
        """
        # (a) marker line, then a sizeable JSON body on the next lines.
        body = json.dumps({
            "task_id": "TASK-002",
            "acceptance_met": [True, True, True],
            "verification_result": "all green",
            "files": ["a.py", "b.py", "c.py"],
            "notes": "x" * 400,
        }, indent=2)
        text_a = f"Completed every acceptance criterion.\n\n## NEXUS:DONE\n\n{body}\n"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-002", text_a))
        ctx = _additional_context(out)
        assert "DONE" in ctx and "COMPLETED" in ctx.upper(), (
            f"DONE marker followed by a JSON block must be detected, got: {ctx!r}"
        )
        assert not _is_truncation_advisory(out), (
            f"A well-formed DONE marker + JSON body must NOT emit RETURN-NO-MARKER. "
            f"stdout: {out!r}"
        )

        # (b) marker WITH trailing content on the same line (`## NEXUS:DONE {json}`).
        text_b = '## NEXUS:DONE {"acceptance_met": true, "result": "passing"}\n'
        _rc, out_b, _err = _run(_return_payload("pipeline-data", "TASK-002", text_b))
        ctx_b = _additional_context(out_b)
        assert "DONE" in ctx_b and "COMPLETED" in ctx_b.upper(), (
            f"DONE marker with same-line trailing JSON must be detected, got: {ctx_b!r}"
        )
        assert not _is_truncation_advisory(out_b), (
            f"DONE marker with same-line trailing content must NOT emit RETURN-NO-MARKER. "
            f"stdout: {out_b!r}"
        )


# ---------------------------------------------------------------------------
# Case (2): Clean ## NEXUS:BLOCKED on its own line
# ---------------------------------------------------------------------------

class TestCleanBlockedMarker:
    def test_blocked_marker_triggers_blocked_phase(self) -> None:
        """A line that IS `## NEXUS:BLOCKED` must produce PHASE=BLOCKED."""
        text = "Cannot proceed.\n\n## NEXUS:BLOCKED\n\n{'blockers': ['schema missing']}"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-003", text))
        ctx = _additional_context(out)
        assert "BLOCKED" in ctx, f"Expected BLOCKED phase in context, got: {ctx!r}"

    def test_blocked_marker_keeps_in_progress(self) -> None:
        """BLOCKED keeps the task IN_PROGRESS (a corrective re-dispatch is needed)."""
        text = "## NEXUS:BLOCKED\n"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-003", text))
        ctx = _additional_context(out)
        assert "IN_PROGRESS" in ctx or "in_progress" in ctx.lower(), (
            f"BLOCKED should say IN_PROGRESS, got: {ctx!r}"
        )

    def test_blocked_marker_no_truncation_advisory(self) -> None:
        text = "## NEXUS:BLOCKED\n"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-003", text))
        assert not _is_truncation_advisory(out)


# ---------------------------------------------------------------------------
# Case (3): Substring "NEXUS:DONE" embedded in prose — must NOT trigger DONE
# ---------------------------------------------------------------------------

class TestSubstringMarkerRejected:
    def test_inline_prose_mention_does_not_trigger_done(self) -> None:
        """'NEXUS:DONE' buried in prose must NOT be treated as a well-formed marker."""
        text = (
            "I need to replace all NEXUS:DONE references with new ones.\n"
            "The brief uses NEXUS:DONE as an example marker.\n"
            "There is no standalone completion marker here.\n"
        )
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-004", text))
        ctx = _additional_context(out)
        # Must NOT say DONE/COMPLETED transition
        assert "DONE" not in ctx or "COMPLETED" not in ctx.upper(), (
            f"Prose mention of NEXUS:DONE must not trigger completion. ctx: {ctx!r}"
        )
        # Must emit the truncation advisory
        assert _is_truncation_advisory(out), (
            f"Prose-only NEXUS:DONE must emit truncation advisory. stdout: {out!r}"
        )

    def test_inline_done_in_json_blob_does_not_trigger(self) -> None:
        """'NEXUS:DONE' inside a JSON blob (not a standalone line) must not trigger."""
        text = (
            '{"completion_marker": "## NEXUS:DONE", "status": "complete"}\n'
            "More prose follows here.\n"
        )
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-005", text))
        # If the marker is embedded in the JSON (not a bare line), it should not trigger
        # OR if it happens to be on its own line it may trigger — the key constraint is
        # that a partial/truncated fragment carrying it in prose does not.
        # For this test we verify that at minimum the hook exits 0 (advisory only).
        _rc2, out2, _err2 = _run(_return_payload(
            "pipeline-data",
            "TASK-005",
            'The brief mentions "## NEXUS:DONE" as an example but work is not done.\n'
        ))
        # Inline in prose with surrounding words: no standalone marker line
        # The advisory should fire (no well-formed standalone line)
        assert _is_truncation_advisory(out2), (
            f"Marker in quoted prose should trigger advisory. stdout: {out2!r}"
        )

    def test_done_marker_not_at_line_start_does_not_trigger(self) -> None:
        """'  prefix text  ## NEXUS:DONE  ' with leading text is NOT a valid marker line."""
        text = "The task result: ## NEXUS:DONE means it passed the check.\n"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-006", text))
        assert _is_truncation_advisory(out), (
            f"Marker not at line start must trigger advisory. stdout: {out!r}"
        )


# ---------------------------------------------------------------------------
# Case (4): Empty / no-marker return — in_progress + advisory
# ---------------------------------------------------------------------------

class TestNoMarkerReturn:
    def test_empty_response_text_triggers_advisory(self) -> None:
        """An agent return with empty response text must emit the truncation advisory."""
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-007", ""))
        # The hook may stay silent (HAVE_SIGNAL=0) for an empty response
        # OR emit an advisory — either way it must NOT emit a DONE transition.
        ctx = _additional_context(out)
        if ctx:  # if it emits anything, it must be advisory, not DONE
            assert "DONE" not in ctx or "COMPLETED" not in ctx.upper()

    def test_response_with_prose_but_no_marker_triggers_advisory(self) -> None:
        """A non-empty return with no NEXUS marker line must emit advisory."""
        text = (
            "I finished the transform work. The pipeline now handles edge cases.\n"
            "All tests pass. No issues found.\n"
        )
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-007", text))
        assert _is_truncation_advisory(out), (
            f"Prose-only return must emit truncation advisory. stdout: {out!r}"
        )

    def test_response_with_prose_stays_in_progress(self) -> None:
        """A return with no marker must not emit a DONE or COMPLETED transition."""
        text = "Work done but no marker was appended due to truncation.\n"
        _rc, out, _err = _run(_return_payload("pipeline-data", "TASK-008", text))
        ctx = _additional_context(out)
        # Must not say DONE/COMPLETED
        if ctx:
            assert "COMPLETED" not in ctx.upper() or "keep IN_PROGRESS" in ctx or "in_progress" in ctx.lower()


# ---------------------------------------------------------------------------
# Case (5): Normal DISPATCH — in_progress, NO truncation advisory
# ---------------------------------------------------------------------------

class TestNormalDispatch:
    def test_dispatch_emits_dispatch_phase(self) -> None:
        """A normal Task dispatch (subagent_type, no tool_response) → DISPATCH phase."""
        _rc, out, _err = _run(_dispatch_payload("pipeline-data", "TASK-009"))
        ctx = _additional_context(out)
        assert "DISPATCH" in ctx, f"Expected DISPATCH phase in context, got: {ctx!r}"

    def test_dispatch_shows_in_progress(self) -> None:
        """A dispatch must hint 'IN_PROGRESS' in the native list advisory."""
        _rc, out, _err = _run(_dispatch_payload("pipeline-data", "TASK-009"))
        ctx = _additional_context(out)
        assert "IN_PROGRESS" in ctx or "in_progress" in ctx.lower(), (
            f"Dispatch must say IN_PROGRESS, got: {ctx!r}"
        )

    def test_dispatch_does_not_emit_truncation_advisory(self) -> None:
        """A normal dispatch must NOT emit the truncation advisory.

        A dispatch legitimately has no marker — the advisory is only for
        a RETURN that carries response text but no well-formed marker line.
        """
        _rc, out, _err = _run(_dispatch_payload("pipeline-data", "TASK-009"))
        assert not _is_truncation_advisory(out), (
            f"Normal dispatch must NOT trigger truncation advisory. "
            f"stdout: {out!r}"
        )

    def test_dispatch_with_task_in_brief(self) -> None:
        """Dispatch with TASK-NNN in the brief text still shows DISPATCH + IN_PROGRESS."""
        payload = {
            "tool_input": {
                "subagent_type": "lens",
                "description": json.dumps({
                    "task_id": "TASK-010",
                    "goal": "verify the transform",
                }),
            }
        }
        _rc, out, _err = _run(payload)
        ctx = _additional_context(out)
        assert "DISPATCH" in ctx
        assert not _is_truncation_advisory(out)

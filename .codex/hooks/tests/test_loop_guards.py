"""Behavioral tests for the two Phase-2 advisory loop-guards.

Both guards are PURE ADVISORY — they emit a next-turn `additionalContext` nudge
and MUST NEVER block (no `permissionDecision: deny`, never a non-zero exit on the
advisory path). They address the "agents try the same combination over and over"
complaint, which the data showed is real but low-volume — hence advisory, not a
gate.

Guard #3 — analysis-paralysis-guard.sh (PostToolUse):
    A SECOND counter, independent of the read-class counter, keyed on the literal
    Bash command string. A run of >=4 identical Bash commands emits one advisory
    (emit-once-then-reset). A different command resets the run to 1. The existing
    read-class counter is untouched.

Guard #4 — dispatch-capture.py (PreToolUse):
    Each router_dispatches.jsonl row gains a `brief_hash`. Before appending the
    current row, the hook scans the last few same-session rows; a repeat of the
    same (dispatched_persona, brief_hash) emits one advisory. Read-only/recon
    personas are exempt. Fail-open: a missing/unreadable log writes the row anyway.

Run from nexus-broker/:
    uv run pytest ../.claude/hooks/tests/test_loop_guards.py -v
or from the repo root with the system interpreter — both guards are stdlib-only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
PARALYSIS = HOOKS_DIR / "analysis-paralysis-guard.sh"
DISPATCH_CAPTURE = HOOKS_DIR / "dispatch-capture.py"


# ─── Guard #3 — identical-command poll-loop ──────────────────────────────────


def _run_paralysis(payload: dict, tmpdir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(PARALYSIS)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "TMPDIR": str(tmpdir)},
        timeout=15,
    )


def _bash_payload(sid: str, command: str) -> dict:
    return {
        "session_id": sid,
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def _advisory_ctx(out: str) -> str:
    out = out.strip()
    if not out:
        return ""
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return ""
    return parsed.get("hookSpecificOutput", {}).get("additionalContext", "")


class TestPollLoopGuard:
    def test_fourth_identical_command_emits_advisory_and_never_blocks(
        self, tmp_path: Path
    ) -> None:
        sid = "poll-fire-01"
        # Runs 1-3 are silent and accumulate.
        for i in range(1, 4):
            res = _run_paralysis(_bash_payload(sid, "docker exec api curl :8080/health"), tmp_path)
            assert res.returncode == 0, f"call {i} must exit 0, got {res.returncode}"
            assert res.stdout.strip() == "", f"call {i} must be silent, got: {res.stdout!r}"

        # The 4th identical command trips the advisory.
        res = _run_paralysis(_bash_payload(sid, "docker exec api curl :8080/health"), tmp_path)
        assert res.returncode == 0, "advisory path must NEVER block (exit 0)"
        ctx = _advisory_ctx(res.stdout)
        assert "analysis-paralysis-guard" in ctx, f"expected poll advisory, got: {res.stdout!r}"
        assert "exact command" in ctx and "4x" in ctx, ctx
        # Pure advisory — no deny shape anywhere.
        assert "permissionDecision" not in res.stdout

    def test_emit_once_then_reset(self, tmp_path: Path) -> None:
        sid = "poll-once-01"
        cmd = "kubectl get pods"
        emits = []
        for _ in range(6):
            res = _run_paralysis(_bash_payload(sid, cmd), tmp_path)
            emits.append(bool(_advisory_ctx(res.stdout)))
        # Fires on the 4th call, resets, then needs 4 more to fire again — so a
        # 6-call run emits exactly once (no per-turn spam).
        assert emits == [False, False, False, True, False, False], emits

    def test_distinct_commands_never_fire(self, tmp_path: Path) -> None:
        sid = "poll-distinct-01"
        for cmd in ("ls", "pwd", "whoami", "date", "uname", "id"):
            res = _run_paralysis(_bash_payload(sid, cmd), tmp_path)
            assert res.returncode == 0
            assert _advisory_ctx(res.stdout) == "", f"distinct cmd {cmd!r} must not fire"

    def test_whitespace_only_difference_still_counts_as_same(self, tmp_path: Path) -> None:
        sid = "poll-trim-01"
        # Leading/trailing whitespace is trimmed, so these are the same run.
        variants = ["docker ps", "  docker ps", "docker ps  ", "\tdocker ps\n"]
        fired = False
        for cmd in variants:
            res = _run_paralysis(_bash_payload(sid, cmd), tmp_path)
            assert res.returncode == 0
            if _advisory_ctx(res.stdout):
                fired = True
        assert fired, "trimmed-identical commands must accumulate to the >=4 advisory"

    def test_read_class_counter_is_independent(self, tmp_path: Path) -> None:
        """The existing read-class counter behaviour is unchanged: 5 consecutive
        reads still trip the original advisory, untouched by the poll path."""
        sid = "poll-readclass-01"
        for i in range(1, 5):
            res = _run_paralysis({"session_id": sid, "tool_name": "Read"}, tmp_path)
            assert res.returncode == 0
            assert res.stdout.strip() == "", f"read {i} must be silent"
        res = _run_paralysis({"session_id": sid, "tool_name": "Read"}, tmp_path)
        ctx = _advisory_ctx(res.stdout)
        assert "5 consecutive" in ctx, f"read-class advisory regressed: {res.stdout!r}"

    def test_bash_does_not_emit_read_class_advisory(self, tmp_path: Path) -> None:
        """A Bash poll run must never produce the read-class '5 consecutive'
        message — the two counters stay separate."""
        sid = "poll-no-crosstalk-01"
        for _ in range(8):
            res = _run_paralysis(_bash_payload(sid, "tail -f log"), tmp_path)
            assert "5 consecutive" not in res.stdout


# ─── Guard #4 — same-goal re-dispatch ────────────────────────────────────────


def _run_dispatch(payload: dict, files_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DISPATCH_CAPTURE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"_HOOK_MEMORY_FILES_DIR": str(files_dir)},
        timeout=15,
    )


def _dispatch_payload(sid: str, persona: str, description: str, prompt: str) -> dict:
    return {
        "session_id": sid,
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": persona,
            "description": description,
            "prompt": prompt,
        },
    }


def _log_rows(files_dir: Path) -> list[dict]:
    path = files_dir / "router_dispatches.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestRedispatchGuard:
    def test_same_persona_and_goal_fires_advisory(self, tmp_path: Path) -> None:
        sid = "rd-fire-01"
        first = _run_dispatch(
            _dispatch_payload(sid, "forge-py", "add login", "build the login form"), tmp_path
        )
        assert first.returncode == 0
        assert first.stdout.strip() == "", "first dispatch of a brief must be silent"

        second = _run_dispatch(
            _dispatch_payload(sid, "forge-py", "add login", "build the login form"), tmp_path
        )
        assert second.returncode == 0, "re-dispatch advisory must NEVER block (exit 0)"
        parsed = json.loads(second.stdout)
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse", hso
        assert "permissionDecision" not in hso, "advisory only — never a deny"
        ctx = hso["additionalContext"]
        assert "re-dispatching the same persona+goal" in ctx and "forge-py" in ctx, ctx

    def test_records_brief_hash_on_every_row(self, tmp_path: Path) -> None:
        sid = "rd-hash-01"
        _run_dispatch(
            _dispatch_payload(sid, "atlas", "schema A", "design the table"), tmp_path
        )
        rows = _log_rows(tmp_path)
        assert rows, "a row must be written"
        assert rows[0]["brief_hash"], "brief_hash must be present and non-empty"
        assert rows[0]["dispatched_persona"] == "atlas"

    def test_different_goal_does_not_fire(self, tmp_path: Path) -> None:
        sid = "rd-diff-01"
        _run_dispatch(
            _dispatch_payload(sid, "forge-py", "add login", "build the login form"), tmp_path
        )
        res = _run_dispatch(
            _dispatch_payload(sid, "forge-py", "add logout", "build the logout button"), tmp_path
        )
        assert res.returncode == 0
        assert res.stdout.strip() == "", f"different goal must not fire: {res.stdout!r}"

    def test_different_persona_same_brief_does_not_fire(self, tmp_path: Path) -> None:
        sid = "rd-persona-01"
        _run_dispatch(
            _dispatch_payload(sid, "forge-py", "do X", "implement X"), tmp_path
        )
        res = _run_dispatch(
            _dispatch_payload(sid, "atlas", "do X", "implement X"), tmp_path
        )
        assert res.stdout.strip() == "", "same brief but a different persona must not fire"

    def test_recon_persona_is_exempt(self, tmp_path: Path) -> None:
        sid = "rd-scout-01"
        for persona in ("scout", "lens", "lens-fast", "palette", "plexus", "nexus"):
            _run_dispatch(_dispatch_payload(sid, persona, "recon", "map the repo"), tmp_path)
            res = _run_dispatch(_dispatch_payload(sid, persona, "recon", "map the repo"), tmp_path)
            assert res.returncode == 0
            assert res.stdout.strip() == "", f"{persona} must be exempt, got: {res.stdout!r}"

    def test_other_session_does_not_cross_match(self, tmp_path: Path) -> None:
        _run_dispatch(
            _dispatch_payload("rd-sA", "forge-py", "shared brief", "same text"), tmp_path
        )
        res = _run_dispatch(
            _dispatch_payload("rd-sB", "forge-py", "shared brief", "same text"), tmp_path
        )
        assert res.stdout.strip() == "", "a repeat in a DIFFERENT session must not fire"

    def test_fail_open_on_missing_log(self, tmp_path: Path) -> None:
        """A missing/unreadable log must not raise and must not emit an advisory —
        the hook still writes (or attempts to write) the row and exits 0."""
        missing = tmp_path / "does" / "not" / "exist"
        res = _run_dispatch(
            _dispatch_payload("rd-failopen-01", "forge-py", "x", "y"), missing
        )
        assert res.returncode == 0, "fail-open: missing log must exit 0"
        assert res.stdout.strip() == "", "no prior log => no advisory"

    def test_advisory_never_blocks_under_repeat_storm(self, tmp_path: Path) -> None:
        sid = "rd-storm-01"
        for _ in range(6):
            res = _run_dispatch(
                _dispatch_payload(sid, "forge-py", "loop", "same brief again"), tmp_path
            )
            assert res.returncode == 0, "every re-dispatch must exit 0 (advisory only)"
            assert "permissionDecision" not in res.stdout

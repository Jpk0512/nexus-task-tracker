"""WF7 regression: lock the confirmed-correct SubagentStop block shape for
lens-gate.sh and root-cause-gate.sh.

These two hooks fire on the SubagentStop event and block via plain stderr +
`exit 2` (the WF5 review called this "shape E"). The WF5 normalization sweep
converted several PreToolUse gates from a JSON-string / flat-decision shape to
the nested `hookSpecificOutput.permissionDecision` object. This test pins WHY
that conversion must NOT happen here.

Confirmed against the Claude Code hooks reference
(https://code.claude.com/docs/en/hooks, fetched 2026-06-01):

  - `permissionDecision` (allow/deny/ask) is EXCLUSIVE to PreToolUse. Stop and
    SubagentStop use the top-level `decision: "block"` + `reason` pattern, and
    only on exit 0.
  - Exit code 2 is a first-class block for SubagentStop ("Prevents the subagent
    from stopping"); stderr is fed back to the agent as the error message.
  - "Claude Code only processes JSON on exit 0. If you exit 2, any JSON is
    ignored." — JSON and exit-2 are mutually exclusive.

So exit-2 + stderr is the correct, strongest, durable SubagentStop block
mechanism. Converting it to nested permissionDecision JSON would be a fail-open
regression (the field is ignored on this event AND ignored on exit 2). This
test asserts:
  1. each gate exits 2 on its block condition, with the reason on STDERR,
  2. each gate exits 0 on its allow condition (no false block),
  3. on block neither gate emits the PreToolUse-only nested
     `hookSpecificOutput.permissionDecision` shape (proves no wrong conversion).

Behaviour was confirmed by direct execution before these assertions were
written (no mocked happy paths), mirroring tests/test_p2_hooks.py.

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_subagentstop_shape.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent


def _run(
    hook_file: str,
    payload: dict,
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke a SubagentStop hook exactly as the harness does: the hook's own
    interpreter (these gates are python3 despite the .sh suffix), a JSON payload
    on stdin, exit code + stderr captured."""
    env = {**os.environ}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / hook_file)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _has_nested_permission_decision(stdout: str) -> bool:
    """True if stdout is JSON carrying the PreToolUse-only nested
    hookSpecificOutput.permissionDecision shape (which must NOT appear here)."""
    out = stdout.strip()
    if not out:
        return False
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        return False
    return "permissionDecision" in obj.get("hookSpecificOutput", {})


# ---------------------------------------------------------------------------
# lens-gate.sh — Rule 17: gated persona NEXUS:DONE touching source must have a
# recent Lens validation row, else BLOCK.
# ---------------------------------------------------------------------------


class TestLensGateSubagentStopShape:
    HOOK_FILE = "lens-gate.sh"

    def _source_done_payload(self) -> dict:
        # NEXUS:DONE from a gated persona (forge) with a source file in
        # files_changed — triggers the Lens-validation requirement.
        return {
            "last_assistant_message": (
                "## NEXUS:DONE\n"
                '```json\n{"files_changed": ["app/foo.ts"]}\n```'
            ),
            "subagent_type": "forge",
        }

    def test_block_exits_2_with_reason_on_stderr(self, tmp_path: Path) -> None:
        """Given a gated-persona NEXUS:DONE touching source with NO Lens
        validation row, When the gate runs, Then it BLOCKS via exit 2 and the
        Rule-17 reason is on STDERR (the SubagentStop block contract)."""
        # Point the DB at a path with no validation row → not-validated → block.
        db_path = tmp_path / "project.db"
        result = _run(
            self.HOOK_FILE,
            self._source_done_payload(),
            env_overrides={"_HOOK_DB_PATH": str(db_path)},
        )
        assert result.returncode == 2, (
            f"source-touching NEXUS:DONE without a Lens row must exit 2, got "
            f"{result.returncode}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "[lens-gate]" in result.stderr and "BLOCK" in result.stderr, (
            f"The block reason must be surfaced on stderr (fed back on exit 2), "
            f"got: {result.stderr!r}"
        )
        assert "Rule 17" in result.stderr

    def test_block_does_not_emit_nested_permission_decision(
        self, tmp_path: Path
    ) -> None:
        """The block path must NOT emit the PreToolUse-only nested
        hookSpecificOutput.permissionDecision shape — that field is ignored on
        SubagentStop and ignored entirely on exit 2 (would be fail-open)."""
        db_path = tmp_path / "project.db"
        result = _run(
            self.HOOK_FILE,
            self._source_done_payload(),
            env_overrides={"_HOOK_DB_PATH": str(db_path)},
        )
        assert not _has_nested_permission_decision(result.stdout), (
            "lens-gate must keep the exit-2+stderr SubagentStop shape, not the "
            f"PreToolUse permissionDecision shape, got stdout: {result.stdout!r}"
        )

    def test_docs_only_change_allows(self, tmp_path: Path) -> None:
        """A pure-docs NEXUS:DONE (no source files) is out of the gate's scope
        and must ALLOW (exit 0)."""
        db_path = tmp_path / "project.db"
        result = _run(
            self.HOOK_FILE,
            {
                "last_assistant_message": (
                    "## NEXUS:DONE\n"
                    '```json\n{"files_changed": ["docs/x.md"]}\n```'
                ),
                "subagent_type": "forge",
            },
            env_overrides={"_HOOK_DB_PATH": str(db_path)},
        )
        assert result.returncode == 0, (
            f"pure-docs NEXUS:DONE must allow (exit 0), got "
            f"{result.returncode}: stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# root-cause-gate.sh — Article X: REVISE/BLOCKED (and fix-keyword DONE) require
# a ## Root Cause Analysis block with 5+ Why lines, else BLOCK.
# ---------------------------------------------------------------------------


class TestRootCauseGateSubagentStopShape:
    HOOK_FILE = "root-cause-gate.sh"

    def _revise_without_rca_payload(self) -> dict:
        return {
            "last_assistant_message": "## NEXUS:REVISE\nsomething went wrong",
            "subagent_type": "forge",
            "task_description": "fix the bug",
        }

    def test_block_exits_2_with_reason_on_stderr(self) -> None:
        """Given NEXUS:REVISE with NO Root Cause Analysis block, When the gate
        runs, Then it BLOCKS via exit 2 and the Article-X reason is on STDERR."""
        result = _run(self.HOOK_FILE, self._revise_without_rca_payload())
        assert result.returncode == 2, (
            f"NEXUS:REVISE without an RCA block must exit 2, got "
            f"{result.returncode}: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "[root-cause-gate]" in result.stderr and "BLOCK" in result.stderr, (
            f"The block reason must be surfaced on stderr (fed back on exit 2), "
            f"got: {result.stderr!r}"
        )
        assert "Root Cause Analysis" in result.stderr

    def test_block_does_not_emit_nested_permission_decision(self) -> None:
        """The block path must NOT emit the PreToolUse-only nested
        hookSpecificOutput.permissionDecision shape."""
        result = _run(self.HOOK_FILE, self._revise_without_rca_payload())
        assert not _has_nested_permission_decision(result.stdout), (
            "root-cause-gate must keep the exit-2+stderr SubagentStop shape, not "
            f"the PreToolUse permissionDecision shape, got stdout: {result.stdout!r}"
        )

    def test_done_nonfix_task_allows(self) -> None:
        """A NEXUS:DONE whose task description has no fix/bug/error keywords does
        not require an RCA block and must ALLOW (exit 0)."""
        result = _run(
            self.HOOK_FILE,
            {
                "last_assistant_message": "## NEXUS:DONE\nadded a feature",
                "subagent_type": "forge",
                "task_description": "add a new button",
            },
        )
        assert result.returncode == 0, (
            f"non-fix NEXUS:DONE must allow (exit 0), got "
            f"{result.returncode}: stderr={result.stderr!r}"
        )

    @pytest.mark.parametrize("marker", ["REVISE", "BLOCKED"])
    def test_block_on_revise_and_blocked_markers(self, marker: str) -> None:
        """Both NEXUS:REVISE and NEXUS:BLOCKED unconditionally require an RCA
        block — without one, each blocks via exit 2."""
        result = _run(
            self.HOOK_FILE,
            {
                "last_assistant_message": f"## NEXUS:{marker}\nno rca here",
                "subagent_type": "forge",
                "task_description": "anything",
            },
        )
        assert result.returncode == 2, (
            f"NEXUS:{marker} without an RCA block must exit 2, got "
            f"{result.returncode}: stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# lens-gate.sh — S2-14 ground-truth cross-check: git beats the files_changed
# self-report. Window: uncommitted working tree + the single HEAD commit.
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True, timeout=10,
    )


def _make_git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=root)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=root)
    return root


class TestLensGateGroundTruth:
    HOOK_FILE = "lens-gate.sh"

    def _repo_with_gated_change(self, root: Path, *, staged: bool) -> Path:
        # app/ is in the gate's fallback GATED_PATH_PREFIXES (unrendered token).
        repo = _make_git_repo(root)
        gated = repo / "app" / "newly-written.ts"
        gated.parent.mkdir(parents=True)
        gated.write_text("// gated source change\n")
        if staged:
            _git("add", "-A", cwd=repo)
        return repo

    def _done_payload(self, files_changed: list[str] | None) -> dict:
        text = "## NEXUS:DONE\nall done."
        if files_changed is not None:
            text = (
                "work\n\n```json\n"
                + json.dumps({"files_changed": files_changed})
                + "\n```\n\n## NEXUS:DONE\n"
            )
        return {
            "last_assistant_message": text,
            "session_id": "S-lens-gt",
            "subagent_type": "forge",
            "task_description": "S2-14 ground-truth fixture task",
        }

    def test_done_omitting_files_changed_with_staged_gated_change_blocks(
        self, tmp_path: Path
    ) -> None:
        """Self-report ABSENT + a staged gated-source change → BLOCK (exit 2)."""
        repo = self._repo_with_gated_change(tmp_path / "repo", staged=True)
        result = _run(
            self.HOOK_FILE,
            self._done_payload(None),
            env_overrides={
                "_HOOK_DB_PATH": str(tmp_path / "project.db"),
                "_HOOK_GIT_ROOT": str(repo),
            },
        )
        assert result.returncode == 2, (
            f"Omitted files_changed must not skip the gate when git shows gated "
            f"changes; got {result.returncode}: stderr={result.stderr!r}"
        )
        assert "Ground truth" in result.stderr

    def test_docs_only_self_report_with_gated_ground_truth_blocks(
        self, tmp_path: Path
    ) -> None:
        """Docs-only self-report + an uncommitted gated change → BLOCK (exit 2)."""
        repo = self._repo_with_gated_change(tmp_path / "repo", staged=False)
        result = _run(
            self.HOOK_FILE,
            self._done_payload(["docs/x.md"]),
            env_overrides={
                "_HOOK_DB_PATH": str(tmp_path / "project.db"),
                "_HOOK_GIT_ROOT": str(repo),
            },
        )
        assert result.returncode == 2, (
            f"Docs-washed self-report must not skip the gate when git shows "
            f"gated changes; got {result.returncode}: stderr={result.stderr!r}"
        )
        assert "Ground truth" in result.stderr

    def test_no_files_changed_done_clean_tree_allows(self, tmp_path: Path) -> None:
        """Self-report absent + CLEAN tree → gate does not apply (exit 0)."""
        repo = _make_git_repo(tmp_path / "repo")
        result = _run(
            self.HOOK_FILE,
            self._done_payload(None),
            env_overrides={
                "_HOOK_DB_PATH": str(tmp_path / "project.db"),
                "_HOOK_GIT_ROOT": str(repo),
            },
        )
        assert result.returncode == 0, (
            f"Clean tree must allow, got {result.returncode}: "
            f"stderr={result.stderr!r}"
        )

    def test_unrendered_git_root_token_fails_soft(self, tmp_path: Path) -> None:
        """Default GIT_ROOT is the literal /Users/john.keeney/nexus-task-tracker token when
        unrendered — git errors → fail-soft to self-report-only (exit 0)."""
        env = {**os.environ}
        env.pop("_HOOK_GIT_ROOT", None)
        env["_HOOK_DB_PATH"] = str(tmp_path / "project.db")
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / self.HOOK_FILE)],
            input=json.dumps(self._done_payload(None)),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Unrendered token must fail-soft, got {result.returncode}: "
            f"stderr={result.stderr!r}"
        )

    # --- TASK-068: docs-only self-report + dirty HEAD commit ---

    def test_docs_only_self_report_with_gated_head_commit_not_blocked(
        self, tmp_path: Path
    ) -> None:
        """TASK-068 false-block fix: docs-only self-report + gated files in HEAD
        commit (clean working tree) → NOT blocked.

        Scenario: a prior checkpoint commit touching app/ (a gated prefix in the
        fallback GATED_PATH_PREFIXES) sits at HEAD; now a docs-only NEXUS:DONE
        arrives. The gate must not include HEAD~1..HEAD in the window when the
        self-report is present-and-docs-only, so the earlier unrelated commit
        does NOT pollute the ground-truth check.
        """
        repo = _make_git_repo(tmp_path / "repo")
        # Commit a gated file to HEAD (simulates a prior checkpoint commit).
        gated = repo / "app" / "some-component.ts"
        gated.parent.mkdir(parents=True)
        gated.write_text("// prior unrelated source change\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "-q", "-m", "prior source checkpoint", cwd=repo)
        # Working tree is clean after the commit.
        result = _run(
            self.HOOK_FILE,
            self._done_payload(["docs/DECISIONS.md"]),
            env_overrides={
                "_HOOK_DB_PATH": str(tmp_path / "project.db"),
                "_HOOK_GIT_ROOT": str(repo),
            },
        )
        assert result.returncode == 0, (
            "Docs-only self-report must NOT be blocked by a gated file in the "
            f"HEAD commit (TASK-068 false-block). got {result.returncode}: "
            f"stderr={result.stderr!r}"
        )

    def test_absent_self_report_with_gated_head_commit_still_blocked(
        self, tmp_path: Path
    ) -> None:
        """TASK-068 fail-closed: absent self-report + gated HEAD commit → BLOCK.

        When files_changed is absent (self-report not present at all), the gate
        must still include the HEAD window — S2-14 anti-false-green preserved.
        """
        repo = _make_git_repo(tmp_path / "repo")
        gated = repo / "app" / "x.ts"
        gated.parent.mkdir(parents=True)
        gated.write_text("x = 1\n")
        _git("add", "-A", cwd=repo)
        _git("commit", "-q", "-m", "gated checkpoint", cwd=repo)
        result = _run(
            self.HOOK_FILE,
            self._done_payload(None),
            env_overrides={
                "_HOOK_DB_PATH": str(tmp_path / "project.db"),
                "_HOOK_GIT_ROOT": str(repo),
            },
        )
        assert result.returncode == 2, (
            "Absent self-report must keep HEAD window active (fail-closed). "
            f"got {result.returncode}: stderr={result.stderr!r}"
        )
        assert "Ground truth" in result.stderr

    def test_gated_self_report_blocks_regardless_of_head_commit(
        self, tmp_path: Path
    ) -> None:
        """TASK-068: gated self-report (source files listed) → still blocked
        without a Lens PASS, regardless of HEAD commit contents.

        The TASK-068 shortcut only applies when the self-report is docs-only;
        a self-report that itself lists gated paths must still be blocked.
        """
        repo = _make_git_repo(tmp_path / "repo")
        result = _run(
            self.HOOK_FILE,
            self._done_payload(["app/foo.ts"]),
            env_overrides={
                "_HOOK_DB_PATH": str(tmp_path / "project.db"),
                "_HOOK_GIT_ROOT": str(repo),
            },
        )
        assert result.returncode == 2, (
            "Gated self-report must still block without Lens PASS. "
            f"got {result.returncode}: stderr={result.stderr!r}"
        )
        assert "BLOCK" in result.stderr


# ---------------------------------------------------------------------------
# S1-22 EXTRACT_OK canary — a non-empty SubagentStop JSON payload yielding no
# extractable assistant text must warn LOUDLY (exit 0), not silently disarm.
# ---------------------------------------------------------------------------


class TestSubagentStopExtractMissCanary:
    PYTHON_GATES = [
        "lens-gate.sh",
        "no-deferral-gate.sh",
        "root-cause-gate.sh",
        "return-validator.py",
    ]

    @pytest.mark.parametrize("hook", PYTHON_GATES)
    def test_no_extractable_text_warns_once_per_session(self, hook: str) -> None:
        payload = {
            "session_id": f"pytest-pkg-miss-{uuid.uuid4().hex}",
            "unrecognized_future_key": "assistant text lives elsewhere now",
        }
        result = _run(hook, payload)
        assert result.returncode == 0, (
            f"{hook}: canary must warn, not block; got {result.returncode}"
        )
        assert "EXTRACT-MISS" in result.stdout, (
            f"{hook}: expected a LOUD EXTRACT-MISS additionalContext warning, "
            f"got stdout: {result.stdout!r}"
        )
        # Once per session: the second identical return stays silent.
        result2 = _run(hook, payload)
        assert result2.returncode == 0
        assert "EXTRACT-MISS" not in result2.stdout

    def test_verify_deliverables_no_extractable_text_warns(self) -> None:
        payload = {
            "session_id": f"pytest-pkg-miss-{uuid.uuid4().hex}",
            "unrecognized_future_key": "assistant text lives elsewhere now",
        }
        result = subprocess.run(
            ["/bin/bash", str(HOOKS_DIR / "verify-deliverables.sh")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=30,
        )
        assert result.returncode == 0
        assert "EXTRACT-MISS" in result.stdout, (
            f"verify-deliverables.sh: expected EXTRACT-MISS warning, got "
            f"stdout: {result.stdout!r}"
        )

    @pytest.mark.parametrize("hook", PYTHON_GATES)
    def test_empty_object_payload_stays_silent(self, hook: str) -> None:
        """'{}' is a trivially-empty payload, not schema drift — no warning."""
        result = _run(hook, {})
        assert result.returncode == 0
        assert "EXTRACT-MISS" not in result.stdout

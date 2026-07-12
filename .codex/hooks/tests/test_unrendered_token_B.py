"""Regression tests for the unrendered-install-token fail-open-silent class — part B.

Two advisory hooks read the install-time ``/Users/john.keeney/nexus-task-tracker`` token directly. If
install-time substitution was skipped the token stays literal at runtime and each
hook degrades to fail-open-SILENT — a dead no-op the operator never sees:

  * verify-after-edit.sh  — with PROJECT_ROOT still ``/Users/john.keeney/nexus-task-tracker`` every
    candidate file is skipped (it can never match ``"$PROJECT_ROOT"/*``), the
    accumulator stays empty, and the hook exits 0 producing nothing. The
    post-edit tsc/ruff safety net is OFF and invisible.
  * session-end-reminder.sh — with the db path still ``/Users/john.keeney/nexus-task-tracker/.memory/
    project.db`` the sqlite queries raise OperationalError, the bare ``except``
    swallows it, and no end-of-session reminder ever fires.

The fix mirrors part A (socraticode-gate.sh / verify-deliverables.sh): a
``_HOOK_*`` env override whose default is the literal ``/Users/john.keeney/nexus-task-tracker`` token,
plus a LOUD (not silent) path when the token is still unrendered. Both hooks are
ADVISORY (they must never block), so "loud" here means emitting a valid nested
``hookSpecificOutput`` advisory / a ``systemMessage`` announcing the inert gate —
never a deny, never exit 2.

These tests pin BOTH directions per hook:
  * rendered  → NORMAL behaviour preserved (no "INSTALL NOT RENDERED" banner)
  * unrendered → LOUD, not silent (a recognisable advisory on stdout, exit 0)

They mirror the subprocess-with-stdin-JSON style of tests/test_p2_hooks.py and
.claude/hooks/tests/test_socraticode_gate_exit.py.

Run from nexus-package/:
    uv run pytest .claude/hooks/tests/test_unrendered_token_B.py -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent
VERIFY_AFTER_EDIT = HOOKS_DIR / "verify-after-edit.sh"
SESSION_END_REMINDER = HOOKS_DIR / "session-end-reminder.sh"

_UNRENDERED_BANNER = "INSTALL NOT RENDERED"


def _clean_env() -> dict[str, str]:
    """A copy of the process env with the hook override vars stripped, so a stray
    value from the dev shell can never leak into a case under test."""
    env = dict(os.environ)
    for k in ("_HOOK_INSTALL_ROOT", "_HOOK_DB_PATH"):
        env.pop(k, None)
    return env


# ===========================================================================
# verify-after-edit.sh — PostToolUse advisory (Shape B, never blocks)
# ===========================================================================


class TestVerifyAfterEditUnrenderedToken:
    HOOK = VERIFY_AFTER_EDIT

    def _run(
        self, event: dict, *, env_overrides: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = _clean_env()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            ["bash", str(self.HOOK)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def test_unrendered_token_is_loud_not_silent(self) -> None:
        """Given the /Users/john.keeney/nexus-task-tracker token was never rendered (no _HOOK_INSTALL_ROOT
        override), When a PostToolUse Write event arrives, Then the hook does NOT
        exit silently: it emits a valid nested hookSpecificOutput advisory naming
        the inert install — and never blocks (exit 0)."""
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "app/foo.ts"},
        }
        result = self._run(event)
        assert result.returncode == 0, (
            f"advisory hook must never block, got {result.returncode}: "
            f"{result.stdout!r} / {result.stderr!r}"
        )
        assert result.stdout.strip(), (
            "Unrendered token made the hook a SILENT no-op — the dead safety net "
            "must be announced loudly, not swallowed."
        )
        payload = json.loads(result.stdout)
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "PostToolUse", (
            f"Must echo the live event name in the nested object, got: {payload}"
        )
        ctx = hso["additionalContext"]
        assert _UNRENDERED_BANNER in ctx, (
            f"Expected the '{_UNRENDERED_BANNER}' advisory, got: {ctx!r}"
        )
        # An advisory must never carry a deny — this is Shape B, not a gate.
        assert "permissionDecision" not in hso

    def test_unrendered_output_is_valid_nested_object(self) -> None:
        """The unrendered advisory must be a nested OBJECT (Shape B), not a JSON
        string value — a string-valued hookSpecificOutput is silently dropped by
        the harness."""
        event = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": "app/x.py"}}
        result = self._run(event)
        payload = json.loads(result.stdout)
        assert isinstance(payload["hookSpecificOutput"], dict), (
            "hookSpecificOutput must be an OBJECT (Shape B) — a string value is "
            "silently dropped."
        )

    def test_rendered_token_preserves_normal_behaviour(self, tmp_path: Path) -> None:
        """Given _HOOK_INSTALL_ROOT points at a real (rendered) project root, When a
        PostToolUse event names a file OUTSIDE that root, Then the hook behaves
        NORMALLY: the file is skipped and the hook exits 0 with NO output — and in
        particular NO 'INSTALL NOT RENDERED' banner fires."""
        project_root = tmp_path / "proj"
        project_root.mkdir()
        # A file path outside the rendered project root → normal skip path.
        event = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/somewhere/else/out_of_root.ts"},
        }
        result = self._run(
            event, env_overrides={"_HOOK_INSTALL_ROOT": str(project_root)}
        )
        assert result.returncode == 0, (
            f"normal path must exit 0, got {result.returncode}: {result.stderr!r}"
        )
        assert _UNRENDERED_BANNER not in result.stdout, (
            "The unrendered banner must NOT fire once the token is rendered — "
            f"normal behaviour was not preserved. stdout={result.stdout!r}"
        )
        assert result.stdout.strip() == "", (
            "A file outside the rendered project root must be skipped silently "
            f"(no findings), got: {result.stdout!r}"
        )


# ===========================================================================
# session-end-reminder.sh — Stop hook advisory (systemMessage, never blocks)
# ===========================================================================


class TestSessionEndReminderUnrenderedToken:
    HOOK = SESSION_END_REMINDER

    def _run(
        self, *, env_overrides: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = _clean_env()
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            ["bash", str(self.HOOK)],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    @staticmethod
    def _seed_db(db_path: Path, *, with_activity: bool) -> str:
        """Create a project.db with sessions/decisions/tasks tables and one open
        session. When with_activity, log a decision so the reminder fires."""
        sid = "SESS-END-001"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE decisions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT)"
            )
            conn.execute(
                "CREATE TABLE tasks ("
                "id TEXT PRIMARY KEY, status TEXT, updated_at TEXT)"
            )
            conn.execute(
                "INSERT INTO sessions (id, started_at, ended_at) VALUES (?, ?, NULL)",
                (sid, "2026-06-01T00:00:00"),
            )
            if with_activity:
                conn.execute(
                    "INSERT INTO decisions (session_id) VALUES (?)", (sid,)
                )
            conn.commit()
        finally:
            conn.close()
        return sid

    def test_unrendered_token_is_loud_not_silent(self) -> None:
        """Given the /Users/john.keeney/nexus-task-tracker token was never rendered (no _HOOK_DB_PATH
        override), When the Stop hook runs, Then it does NOT silently no-op: it
        emits a LOUD systemMessage naming the inert reminder — and exits 0
        (advisory, never blocks)."""
        result = self._run()
        assert result.returncode == 0, (
            f"advisory Stop hook must never block, got {result.returncode}: "
            f"{result.stderr!r}"
        )
        assert result.stdout.strip(), (
            "Unrendered token made the reminder a SILENT no-op — the inert gate "
            "must be announced loudly, not swallowed."
        )
        payload = json.loads(result.stdout)
        msg = payload["systemMessage"]
        assert _UNRENDERED_BANNER in msg, (
            f"Expected the '{_UNRENDERED_BANNER}' advisory in systemMessage, "
            f"got: {msg!r}"
        )

    def test_rendered_with_activity_emits_normal_reminder(
        self, tmp_path: Path
    ) -> None:
        """Given _HOOK_DB_PATH points at a seeded db with an open session that has
        activity, When the Stop hook runs, Then it emits the NORMAL session-end
        reminder (systemMessage naming the open session + log.py command) and NOT
        the unrendered banner."""
        db_path = tmp_path / "project.db"
        sid = self._seed_db(db_path, with_activity=True)
        result = self._run(env_overrides={"_HOOK_DB_PATH": str(db_path)})
        assert result.returncode == 0, (
            f"normal reminder must exit 0, got {result.returncode}: {result.stderr!r}"
        )
        assert result.stdout.strip(), "Expected a normal reminder, got nothing."
        payload = json.loads(result.stdout)
        msg = payload["systemMessage"]
        assert _UNRENDERED_BANNER not in msg, (
            "The unrendered banner must NOT fire once the db path is rendered — "
            f"normal behaviour was not preserved. msg={msg!r}"
        )
        assert sid in msg, (
            f"Normal reminder must name the open session {sid!r}, got: {msg!r}"
        )
        assert "session end" in msg, (
            f"Normal reminder must cite the log.py session-end call, got: {msg!r}"
        )

    def test_rendered_no_activity_is_silent(self, tmp_path: Path) -> None:
        """Given a rendered db with an open session but NO activity, When the hook
        runs, Then it stays silent (exit 0, empty stdout) — a genuine clean pass,
        not a swallowed error."""
        db_path = tmp_path / "project.db"
        self._seed_db(db_path, with_activity=False)
        result = self._run(env_overrides={"_HOOK_DB_PATH": str(db_path)})
        assert result.returncode == 0, (
            f"clean pass must exit 0, got {result.returncode}: {result.stderr!r}"
        )
        assert result.stdout.strip() == "", (
            "An open session with no activity must produce no reminder, got: "
            f"{result.stdout!r}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

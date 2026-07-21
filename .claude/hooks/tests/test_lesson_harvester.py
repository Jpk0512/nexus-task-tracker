"""Regression test for lesson-harvester.sh emission path (WF7 normalization).

lesson-harvester.sh is a SessionStart hook (a Python script despite the .sh
suffix). It surfaces prior-session decisions that match failure/revise/blocked
keywords but have no lesson yet.

Bug (HOOK-REVIEW.md, lesson-harvester.sh:93): it wrote its harvest reminder to
STDERR, which the settings.json `2>/dev/null` wrapper discards — and SessionStart
only surfaces STDOUT. So the reminder never reached the model.

Fix: emit a nested {"hookSpecificOutput": {"hookEventName": "SessionStart",
"additionalContext": <reminder>}} object on STDOUT (the reference SessionStart
shape used by health-banner.sh). The harvest logic and side-effects are
unchanged; only the emission path moved stderr -> nested-stdout.

These tests pin:
  (a) with a seeded _HOOK_INSTALL_ROOT temp DB holding a trigger-keyword decision
      and NO matching lesson, stdout is valid JSON whose
      hookSpecificOutput.hookEventName == 'SessionStart' (nested object, not a
      bare string, and NOT stderr);
  (b) the harvest reminder text (the '[lesson-harvester]' banner + the exact
      `log.py lesson add` command template) lands in additionalContext;
  (c) the hook is advisory — exit 0, NEVER a permissionDecision/decision block
      (no fail-open risk to guard: it never denied);
  (d) when no decision matches the trigger keywords, the hook stays silent
      (exit 0, empty stdout) — preserving the prior "nothing to harvest" path.

Run from nexus-package/ with the broker venv (rtk MASKS pytest):
    python -m pytest .claude/hooks/tests/test_lesson_harvester.py -q
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent
HOOK_FILE = HOOKS_DIR / "lesson-harvester.sh"

PRIOR_SESSION_ID = "sess-prior-001"
DECISION_ID = "DEC-LH-001"
DECISION_TITLE = "Redelegated forge after a blocked verification failure"


def _seed_db(db_path: Path, *, with_trigger: bool, with_lesson: bool = False) -> None:
    """Create a project.db with the schema lesson-harvester.sh queries:
      - sessions(id, ended_at)  — one ended prior session
      - decisions(id, title, rationale, context, session_id)
      - lessons(source_decision_id)
    `with_trigger` controls whether the decision's rationale/context contains a
    TRIGGER_KEYWORD ('blocked'/'failure'/...). `with_lesson` inserts a lesson
    that already references the decision (which must suppress the reminder)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, ended_at TEXT)")
        conn.execute(
            "CREATE TABLE decisions ("
            "id TEXT PRIMARY KEY, title TEXT, rationale TEXT, "
            "context TEXT, session_id TEXT)"
        )
        conn.execute("CREATE TABLE lessons (source_decision_id TEXT)")
        conn.execute(
            "INSERT INTO sessions (id, ended_at) VALUES (?, ?)",
            (PRIOR_SESSION_ID, "2026-05-31T12:00:00"),
        )
        rationale = (
            "Root cause: the first attempt was blocked by a verification failure, "
            "so we revised the brief and redelegated."
            if with_trigger
            else "Chose option A because it is simpler and faster to ship."
        )
        conn.execute(
            "INSERT INTO decisions (id, title, rationale, context, session_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (DECISION_ID, DECISION_TITLE, rationale, "", PRIOR_SESSION_ID),
        )
        if with_lesson:
            conn.execute(
                "INSERT INTO lessons (source_decision_id) VALUES (?)",
                (DECISION_ID,),
            )
        conn.commit()
    finally:
        conn.close()


def _run(
    install_root: Path, daemon_env: dict | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook exactly as the harness does — minimal stdin JSON — with
    _HOOK_DB_PATH pointing at the seeded DB.

    Post-F2-03 lesson-harvester.sh is the shared advisory ping shim; the harvest
    logic runs daemon-resident (handle_lesson_harvester), which reads the
    forwarded `_HOOK_DB_PATH` (an env-seam consumer served by the DEFAULT
    daemon). `daemon_env` carries `resident_daemon.env` (socket dir +
    _HOOK_REPO_ROOT + the widened ping-shim timeout) so the shim reaches that
    daemon; without it the shim fails OPEN (silent)."""
    env = {**os.environ, "_HOOK_DB_PATH": str(install_root / ".memory" / "project.db")}
    if daemon_env:
        env.update(daemon_env)
    return subprocess.run(
        [sys.executable, str(HOOK_FILE)],
        input="{}",
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def _make_install_root(tmp_path: Path, **seed_kwargs) -> Path:
    install_root = tmp_path / "install"
    (install_root / ".memory").mkdir(parents=True)
    _seed_db(install_root / ".memory" / "project.db", **seed_kwargs)
    return install_root


def test_reminder_is_nested_json_on_stdout(tmp_path: Path, resident_daemon) -> None:
    """Given a prior decision matching trigger keywords with no lesson, When the
    SessionStart hook runs, Then stdout is valid JSON with the nested
    hookSpecificOutput object (hookEventName == 'SessionStart') — NOT a bare
    string and NOT routed to stderr (the old, swallowed path)."""
    install_root = _make_install_root(tmp_path, with_trigger=True)
    result = _run(install_root, daemon_env=dict(resident_daemon.env))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), (
        "Hook produced no stdout — the harvest reminder still goes nowhere "
        "(the lesson-harvester.sh:93 stderr bug)."
    )
    payload = json.loads(result.stdout)
    hso = payload["hookSpecificOutput"]
    assert isinstance(hso, dict), (
        f"hookSpecificOutput must be an OBJECT (nested shape), not a string; "
        f"Claude Code silently drops the string form. Got: {hso!r}"
    )
    assert hso["hookEventName"] == "SessionStart", (
        f"Expected hookEventName 'SessionStart' (the wired event), got: {payload}"
    )
    assert isinstance(hso["additionalContext"], str) and hso["additionalContext"]
    # The reminder must NOT be left on stderr (the discarded path).
    assert "[lesson-harvester]" not in result.stderr, (
        "Reminder leaked to stderr — emission path was not fully moved to stdout."
    )


def test_reminder_text_lands_in_additional_context(tmp_path: Path, resident_daemon) -> None:
    """The harvest reminder content — the banner naming the prior session and the
    exact `log.py lesson add` command template — must appear in
    additionalContext, proving the surfaced message is the harvest payload."""
    install_root = _make_install_root(tmp_path, with_trigger=True)
    result = _run(install_root, daemon_env=dict(resident_daemon.env))

    assert result.returncode == 0, result.stderr
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "[lesson-harvester]" in ctx
    assert PRIOR_SESSION_ID in ctx, (
        f"Reminder must name the prior session id, got: {ctx}"
    )
    assert "python3 .memory/log.py lesson add" in ctx, (
        f"Reminder must carry the lesson-add command template, got: {ctx}"
    )
    assert DECISION_ID in ctx, (
        f"Reminder must reference the surfaced decision id, got: {ctx}"
    )


def test_hook_is_advisory_never_blocks(tmp_path: Path, resident_daemon) -> None:
    """The hook is advisory — exit 0 and NO block key (neither nested
    permissionDecision nor a flat decision:block). It never denied, so the fix
    must not introduce a block (no fail-open / no fail-closed flip)."""
    install_root = _make_install_root(tmp_path, with_trigger=True)
    result = _run(install_root, daemon_env=dict(resident_daemon.env))

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "decision" not in payload, (
        f"Advisory hook must not emit a flat decision block: {payload}"
    )
    assert "permissionDecision" not in payload["hookSpecificOutput"], (
        f"Advisory hook must not emit a permissionDecision: {payload}"
    )


def test_no_trigger_match_stays_silent(tmp_path: Path) -> None:
    """Given a prior decision that does NOT match any trigger keyword, When the
    hook runs, Then it emits nothing (exit 0, empty stdout) — the unchanged
    'nothing to harvest' path is preserved by the emission-path fix."""
    install_root = _make_install_root(tmp_path, with_trigger=False)
    result = _run(install_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        f"No trigger-keyword decision must yield no reminder, got: {result.stdout!r}"
    )


def test_decision_with_existing_lesson_stays_silent(tmp_path: Path) -> None:
    """Given a trigger-matching decision that ALREADY has a lesson referencing it,
    When the hook runs, Then no reminder is emitted (exit 0, empty stdout) — the
    lesson-exists suppression side-effect is unchanged."""
    install_root = _make_install_root(tmp_path, with_trigger=True, with_lesson=True)
    result = _run(install_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        "A decision that already has a lesson must not be re-surfaced, "
        f"got: {result.stdout!r}"
    )

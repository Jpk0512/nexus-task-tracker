"""Regression test for S2-04: cross-session NATIVE-<N> task-row clobber.

BUG: _upsert_native_task maps a session-scoped native integer id (restarts at 1
every session) to "NATIVE-<N>" with no session qualifier.  When session B's
first TaskCreate also gets native id 1, _upsert_native_task finds the existing
NATIVE-1 row and takes the UPDATE path, overwriting session A's title/status.

Fix contract:
  - When an existing NATIVE-<N> row has a *different* title AND its status is
    open (todo / in_progress), the upsert must NOT overwrite the title/status.
    Instead it inserts a fresh row under a surrogate id (NATIVE-<N>-<session>
    or similar) and emits a warning to stderr.
  - Existing NATIVE-1..29 rows are preserved as-is.
  - `task mirror-native --op update` on the *same* title (same task, intra-session
    status change) still works normally.

Test-first protocol:
  Phase 1 (RED)  — test marked xfail(strict=True); runs against unfixed code.
  Phase 2 (GREEN) — xfail removed after fix; both asserts must pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_LOG_PY: Path = _REPO_ROOT / ".memory" / "log.py"


def _run(
    *args: str,
    db_path: Path,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NEXUS_DB_PATH": str(db_path), "NEXUS_DISABLE_VEC": "1"}
    return subprocess.run(
        [sys.executable, str(_LOG_PY), *args],
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


def _mirror(
    db_path: Path,
    *,
    op: str,
    native_id: str,
    subject: str,
    status: str = "pending",
    session_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Call `log.py task mirror-native` with optional session_id env override."""
    env = {**os.environ, "NEXUS_DB_PATH": str(db_path), "NEXUS_DISABLE_VEC": "1"}
    if session_id is not None:
        env["NEXUS_SESSION_ID"] = session_id
    cmd = [
        sys.executable,
        str(_LOG_PY),
        "task",
        "mirror-native",
        "--op", op,
        "--native-id", native_id,
        "--subject", subject,
        "--status", status,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ---------------------------------------------------------------------------
# AC-1  Cross-session clobber: session B's native #1 must NOT overwrite
#       session A's open NATIVE-1 row when titles differ.
# ---------------------------------------------------------------------------


def test_cross_session_no_clobber(tmp_path: Path) -> None:
    """Session A mirrors native #1 (title 'Task-A', open); session B mirrors
    native #1 (title 'Task-B').  Session A's NATIVE-1 title/status must survive.
    """
    db = tmp_path / "project.db"

    # Initialise the DB.
    _run("init", db_path=db, check=True)

    # --- Session A: mirror native task #1 as open ---
    result_a = _mirror(
        db,
        op="create",
        native_id="1",
        subject="Task-A",
        status="pending",
        session_id="session-A",
    )
    assert result_a.returncode == 0, (
        f"Session A mirror failed:\nstdout={result_a.stdout}\nstderr={result_a.stderr}"
    )

    # --- Session B: mirror a *different* task that also gets native id #1 ---
    result_b = _mirror(
        db,
        op="create",
        native_id="1",
        subject="Task-B",
        status="pending",
        session_id="session-B",
    )
    assert result_b.returncode == 0, (
        f"Session B mirror failed:\nstdout={result_b.stdout}\nstderr={result_b.stderr}"
    )

    # --- Assert NATIVE-1 row from session A is intact ---
    rows_result = _run("task", "list", db_path=db)
    assert rows_result.returncode == 0, (
        f"task list failed:\nstdout={rows_result.stdout}\nstderr={rows_result.stderr}"
    )
    rows: list[dict] = json.loads(rows_result.stdout)

    native1_rows = [r for r in rows if r.get("id") == "NATIVE-1"]
    assert len(native1_rows) == 1, (
        f"Expected exactly one NATIVE-1 row; got {len(native1_rows)}: {native1_rows}"
    )

    native1 = native1_rows[0]
    assert native1["title"] == "Task-A", (
        f"S2-04 regression: NATIVE-1 title was clobbered by session B! "
        f"Expected 'Task-A', got '{native1['title']}'"
    )
    assert native1["status"] in ("todo", "pending"), (
        f"S2-04 regression: NATIVE-1 status unexpectedly changed: {native1['status']}"
    )

    # Session B's task must also exist under some id (surrogate or same).
    task_b_rows = [r for r in rows if r.get("title") == "Task-B"]
    assert len(task_b_rows) >= 1, (
        f"Session B's Task-B was not persisted at all: {rows}"
    )


# ---------------------------------------------------------------------------
# AC-2  Intra-session update on the same task must still work (no regression).
# ---------------------------------------------------------------------------


def test_intra_session_update_still_works(tmp_path: Path) -> None:
    """An update to NATIVE-1 within the same logical context (same title already
    in DB) must still patch status normally — no false-positive guard trigger.
    """
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    # Create the row.
    create = _mirror(db, op="create", native_id="1", subject="My Task", status="pending")
    assert create.returncode == 0

    # Update its status within the same task context (title matches or update op).
    update = _mirror(db, op="update", native_id="1", subject="My Task", status="completed")
    assert update.returncode == 0

    rows_result = _run("task", "list", db_path=db)
    assert rows_result.returncode == 0
    rows: list[dict] = json.loads(rows_result.stdout)

    native1 = next((r for r in rows if r.get("id") == "NATIVE-1"), None)
    assert native1 is not None, "NATIVE-1 row missing after update"
    assert native1["status"] == "done", (
        f"Expected status 'done' after completed update, got '{native1['status']}'"
    )


# ---------------------------------------------------------------------------
# AC-3  TASK-084 — EMPTY-PANEL collision: a new TaskCreate reuses native #1 onto
#       an existing CLOSED (done) NATIVE-1. The original S2-04 guard only fired
#       on OPEN rows, so a done NATIVE-1 was blind-overwritten (silent data loss).
#       The widened guard must preserve the closed NATIVE-1 and surrogate the new
#       task — regardless of the prior row's status.
# ---------------------------------------------------------------------------


def test_empty_panel_create_does_not_clobber_closed_native1(tmp_path: Path) -> None:
    """Prior session left NATIVE-1 DONE; a fresh session's first TaskCreate also
    gets native #1 with a different title. The done NATIVE-1 must survive intact
    and the new task lands under a surrogate id.
    """
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    # --- Prior session: native #1 created then completed (panel now empty) ---
    created = _mirror(db, op="create", native_id="1", subject="Old-Done-Task", status="pending")
    assert created.returncode == 0, f"create failed: {created.stderr}"
    done = _mirror(db, op="update", native_id="1", subject="Old-Done-Task", status="completed")
    assert done.returncode == 0, f"complete failed: {done.stderr}"

    # Sanity: NATIVE-1 is now closed (done).
    pre = json.loads(_run("task", "list", db_path=db).stdout)
    pre_n1 = next(r for r in pre if r["id"] == "NATIVE-1")
    assert pre_n1["status"] == "done", f"setup: NATIVE-1 should be done, got {pre_n1}"

    # --- New session, empty panel: a different task also gets native #1 ---
    reused = _mirror(db, op="create", native_id="1", subject="Brand-New-Task", status="pending")
    assert reused.returncode == 0, f"reuse create failed: {reused.stderr}"

    rows = json.loads(_run("task", "list", db_path=db).stdout)

    # The original closed NATIVE-1 must be untouched — title + done status intact.
    native1 = next((r for r in rows if r.get("id") == "NATIVE-1"), None)
    assert native1 is not None, "NATIVE-1 row vanished"
    assert native1["title"] == "Old-Done-Task", (
        f"TASK-084 regression: closed NATIVE-1 was clobbered by the empty-panel "
        f"reuse. Expected 'Old-Done-Task', got '{native1['title']}'"
    )
    assert native1["status"] == "done", (
        f"TASK-084 regression: closed NATIVE-1 status changed: {native1['status']}"
    )

    # The new task must have been persisted under a surrogate id (NOT NATIVE-1).
    new_rows = [r for r in rows if r.get("title") == "Brand-New-Task"]
    assert len(new_rows) == 1, f"Brand-New-Task not persisted exactly once: {rows}"
    assert new_rows[0]["id"] != "NATIVE-1", (
        f"new task must NOT reuse the NATIVE-1 id: {new_rows[0]}"
    )


# ---------------------------------------------------------------------------
# AC-4  NATIVE-16 — a direct `task mirror-native --op update` reaching
#       _upsert_native_task's UPDATE branch must refuse to patch a pre-existing
#       row that was NOT itself created by the mirror (no "mirrored from native
#       task #<N>" marker), mirroring _foreign_collision() in
#       .claude/hooks/_task_mirror.py:73 — closing the residual left by
#       NATIVE-13 (which only guarded the hook's own pre-check, not this
#       function reached directly).
# ---------------------------------------------------------------------------


def test_direct_update_refuses_foreign_row(tmp_path: Path) -> None:
    """A hand-authored NATIVE-<N> row (e.g. the redesign family, no mirror
    marker in notes) must survive a direct `task mirror-native --op update`
    untouched — the guard returns a structured refusal, not a silent patch.
    """
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    # Hand-author NATIVE-16 directly (bypassing the mirror entirely) — no
    # "mirrored from native task #..." marker in notes.
    add = _run(
        "task", "add",
        "--id", "NATIVE-16",
        "--domain", "nexus",
        "--title", "Hand-authored redesign task",
        "--status", "in_progress",
        db_path=db,
    )
    assert add.returncode == 0, f"task add failed: {add.stderr}"

    # A direct mirror-native update against that same id must refuse.
    update = _mirror(
        db,
        op="update",
        native_id="16",
        subject="Hand-authored redesign task",
        status="completed",
    )
    assert update.returncode == 0, (
        f"guard must no-op cleanly (never raise): {update.stderr}"
    )
    summary = json.loads(update.stdout)
    assert summary.get("action") == "refused_foreign_row", (
        f"expected a structured refusal, got: {summary}"
    )

    rows = json.loads(_run("task", "list", db_path=db).stdout)
    native16 = next((r for r in rows if r.get("id") == "NATIVE-16"), None)
    assert native16 is not None, "NATIVE-16 row vanished"
    assert native16["title"] == "Hand-authored redesign task", (
        f"NATIVE-16 title must survive the refused update: {native16}"
    )
    assert native16["status"] == "in_progress", (
        f"NATIVE-16 status must NOT be patched by the refused update: {native16}"
    )


def test_direct_update_on_legitimate_mirror_row_still_succeeds(tmp_path: Path) -> None:
    """A row the mirror itself created (marker present) must still accept a
    direct `task mirror-native --op update` normally — the guard must not
    false-positive on legitimate mirror traffic.
    """
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    create = _mirror(db, op="create", native_id="16", subject="Mirror-owned task", status="pending")
    assert create.returncode == 0, f"create failed: {create.stderr}"

    update = _mirror(db, op="update", native_id="16", subject="Mirror-owned task", status="completed")
    assert update.returncode == 0, f"update failed: {update.stderr}"
    summary = json.loads(update.stdout)
    assert summary.get("action") == "updated", f"expected a normal update, got: {summary}"

    rows = json.loads(_run("task", "list", db_path=db).stdout)
    native16 = next((r for r in rows if r.get("id") == "NATIVE-16"), None)
    assert native16 is not None, "NATIVE-16 row vanished"
    assert native16["status"] == "done", (
        f"legitimate mirror update must still land: {native16}"
    )


def test_empty_panel_recreate_same_title_updates_in_place(tmp_path: Path) -> None:
    """Happy path preserved: a create whose subject MATCHES the existing NATIVE-1
    title is the SAME task being re-mirrored — it must update in place, NOT
    allocate a surrogate (no spurious NATIVE-1-2).
    """
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    first = _mirror(db, op="create", native_id="1", subject="Same-Task", status="pending")
    assert first.returncode == 0
    again = _mirror(db, op="create", native_id="1", subject="Same-Task", status="in_progress")
    assert again.returncode == 0

    rows = json.loads(_run("task", "list", db_path=db).stdout)
    native1_rows = [r for r in rows if r.get("id") == "NATIVE-1"]
    assert len(native1_rows) == 1, f"expected one NATIVE-1, got {native1_rows}"
    surrogate_rows = [r for r in rows if str(r.get("id", "")).startswith("NATIVE-1-")]
    assert surrogate_rows == [], (
        f"same-title re-create must NOT spawn a surrogate: {surrogate_rows}"
    )
    assert native1_rows[0]["status"] == "in_progress", (
        f"same-title re-create should update status in place, got {native1_rows[0]}"
    )

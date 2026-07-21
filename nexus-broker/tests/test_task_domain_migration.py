"""TASK-108 / DEC-098 — task-system domain migration + owner-ruled bulk archive.

Covers the log.py surfaces added by DEC-098:
  * `task add` per-domain sequential id minting (NEX-/PLX-/KB-/OPS-/OTH-),
    --id override, and the required --domain argument.
  * `task migrate-domains` dry-run (writes nothing) vs --apply (backup + one
    transaction), each owner-disposition branch, and double-apply idempotency.
  * `context dump` grouped-by-domain structure, todo cap, count-only rendering
    of pending_review/archived, and backward-compatible JSON keys.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_LOG_PY: Path = _REPO_ROOT / ".memory" / "log.py"

_CUTOFF_BEFORE = "2026-07-10T00:00:00+00:00"
_CUTOFF_ON = "2026-07-17T12:00:00+00:00"  # ON the cutoff date — in scope (inclusive)
_CUTOFF_AFTER = "2026-07-18T12:00:00+00:00"
_REVIEWER = "triage-wf_1866137c"


def _run(*args: str, db_path: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "NEXUS_DB_PATH": str(db_path), "NEXUS_DISABLE_VEC": "1"}
    return subprocess.run(
        [sys.executable, str(_LOG_PY), *args],
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )


def _insert_task(
    db: Path,
    tid: str,
    *,
    status: str = "todo",
    priority: str = "medium",
    notes: str | None = None,
    description: str | None = None,
    domain: str | None = None,
) -> None:
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        if "domain" in cols:
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, notes, description,"
                " domain, created_at, updated_at) VALUES (?,?,?,?,?,?,?,'t','t')",
                (tid, f"title {tid}", status, priority, notes, description, domain),
            )
        else:
            conn.execute(
                "INSERT INTO tasks (id, title, status, priority, notes, description,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,'t','t')",
                (tid, f"title {tid}", status, priority, notes, description),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_feedback(db: Path, fid: int, captured_at: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO nexus_feedback (id, source, severity, category, message,"
            " captured_at) VALUES (?,?,?,?,?,?)",
            (fid, "hook", "medium", "other", f"friction {fid}", captured_at),
        )
        conn.commit()
    finally:
        conn.close()


def _task_rows(db: Path) -> dict[str, dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM tasks")}
    finally:
        conn.close()


def _feedback_rows(db: Path) -> dict[int, dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM nexus_feedback")}
    finally:
        conn.close()


def _write_verdicts(tmp_path: Path) -> Path:
    """A minimal verdict fixture exercising every owner-disposition branch."""
    verdicts = {
        "generated": "2026-07-18",
        "classifications": [
            {"id": "T-FIX", "verdict": "ALREADY-FIXED", "domain": "nexus",
             "reason": "fix landed in abc123", "confidence": "high"},
            {"id": "T-OBS", "verdict": "OBSOLETE-REDESIGN", "domain": "plexus",
             "reason": "superseded by the redesign", "confidence": "high"},
            {"id": "T-DUP", "verdict": "DUPLICATE", "domain": "nexus",
             "reason": "duplicate of T-FIX", "confidence": "med"},
            {"id": "T-KB", "verdict": "STILL-APPLIES", "domain": "kb",
             "reason": "vault content", "confidence": "high"},
            {"id": "TASK-054", "verdict": "NEEDS-OWNER", "domain": "plexus",
             "reason": "vault-skill migration hinges on Zen Notes", "confidence": "med"},
            {"id": "NATIVE-12-4", "verdict": "NON-NEXUS", "domain": "other",
             "reason": "junk row", "confidence": "high"},
            {"id": "T-NONNEX", "verdict": "NON-NEXUS", "domain": "ops",
             "reason": "belongs to ops tooling", "confidence": "high"},
            {"id": "TASK-001", "verdict": "NEEDS-OWNER", "domain": "plexus",
             "reason": "stale bak review", "confidence": "high"},
            {"id": "TASK-099", "verdict": "STILL-APPLIES", "domain": "nexus",
             "reason": "active work", "confidence": "high"},
            {"id": "NATIVE-9-4", "verdict": "NEEDS-OWNER", "domain": "nexus",
             "reason": "CL-11 moot, CL-23 landed, but GAP-09 survives "
                       "(advisory_handlers.py _READ_ALLOWLIST_SEGMENTS) and "
                       "hinges on the OPT-082 policy call", "confidence": "med"},
            {"id": "T-STILL", "verdict": "STILL-APPLIES", "domain": "nexus",
             "reason": "gap remains", "confidence": "high"},
        ],
        "feedback": {
            "still_valuable": [
                "Stop-hook re-fire cluster (ids 3, 4 — post-fix)",
                "ids 6-7: scout-protocol conflict",
                "id 9 (07-16): tier mismatch",
            ],
        },
    }
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(verdicts))
    return path


def _make_fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)
    _insert_task(db, "T-FIX", notes="original note")
    _insert_task(db, "T-OBS")
    _insert_task(db, "T-DUP")
    _insert_task(db, "T-KB")
    _insert_task(db, "TASK-054", notes="pre-existing")
    _insert_task(db, "NATIVE-12-4")
    _insert_task(db, "T-NONNEX")
    _insert_task(db, "TASK-001")
    _insert_task(db, "TASK-099", status="in_progress")
    _insert_task(db, "NATIVE-9-4", description="old multi-gap description")
    _insert_task(db, "T-STILL")
    _insert_task(db, "T-UNCLASSIFIED")
    for fid, captured in ((1, _CUTOFF_BEFORE), (2, _CUTOFF_BEFORE), (3, _CUTOFF_BEFORE),
                          (4, _CUTOFF_BEFORE), (6, _CUTOFF_BEFORE), (7, _CUTOFF_BEFORE),
                          (9, _CUTOFF_BEFORE), (10, _CUTOFF_AFTER), (11, _CUTOFF_ON)):
        _insert_feedback(db, fid, captured)
    return db


# ---------------------------------------------------------------------------
# task add — per-domain id minting
# ---------------------------------------------------------------------------


def test_domain_minting_sequence(tmp_path: Path) -> None:
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)

    first = _run("task", "add", "--domain", "nexus", "--title", "a", db_path=db)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["task_id"] == "NEX-001"

    second = _run("task", "add", "--domain", "nexus", "--title", "b", db_path=db)
    assert json.loads(second.stdout)["task_id"] == "NEX-002"

    plx = _run("task", "add", "--domain", "plexus", "--title", "c", db_path=db)
    assert json.loads(plx.stdout)["task_id"] == "PLX-001"

    ops = _run("task", "add", "--domain", "ops", "--title", "d", db_path=db)
    assert json.loads(ops.stdout)["task_id"] == "OPS-001"

    rows = _task_rows(db)
    assert rows["NEX-002"]["domain"] == "nexus"
    assert rows["PLX-001"]["domain"] == "plexus"


def test_domain_counter_ignores_grandfathered_ids(tmp_path: Path) -> None:
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)
    _insert_task(db, "TASK-500")
    _insert_task(db, "NATIVE-12")

    minted = _run("task", "add", "--domain", "nexus", "--title", "x", db_path=db)
    assert json.loads(minted.stdout)["task_id"] == "NEX-001"

    override = _run(
        "task", "add", "--domain", "kb", "--id", "KB-CUSTOM", "--title", "y",
        db_path=db,
    )
    assert json.loads(override.stdout)["task_id"] == "KB-CUSTOM"


def test_task_add_requires_domain(tmp_path: Path) -> None:
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)
    result = _run("task", "add", "--title", "no domain", db_path=db)
    assert result.returncode != 0
    assert "--domain" in result.stderr


# ---------------------------------------------------------------------------
# task migrate-domains — dry-run vs apply
# ---------------------------------------------------------------------------


def test_migrate_domains_dry_run_writes_nothing(tmp_path: Path) -> None:
    db = _make_fixture_db(tmp_path)
    verdicts = _write_verdicts(tmp_path)
    before = _task_rows(db)

    result = _run(
        "task", "migrate-domains", "--verdicts", str(verdicts), db_path=db,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["dry_run"] is True
    disp = out["dispositions"]
    assert disp["delete"] == 1
    assert disp["completed"] == 1
    assert disp["archived"] == 4  # T-OBS, T-DUP, T-NONNEX, TASK-001
    assert disp["archived_kb"] == 2  # T-KB + owner-ruled TASK-054
    assert disp["pending_review"] == 1  # T-STILL (TASK-099/NATIVE-9-4 are kept)
    assert disp["keep"] == 2
    assert disp["unhandled"] == 0
    # In-scope unresolved ids {1,2,3,4,6,7,9,11} (cutoff-date row 11 INCLUDED)
    # minus still-valuable {3,4,6,7,9}; post-cutoff row 10 out of scope.
    assert out["feedback"]["resolve_planned"] == 3
    assert sorted(out["feedback"]["kept_open_ids"]) == [3, 4, 6, 7, 9]
    assert out["backup"] is None

    # Projected after-counts, not applied ones.
    assert out["tasks_after"]["status"].get("pending_review") == 1
    # Nothing written: rows byte-identical, no backup dir, no domain backfill.
    assert _task_rows(db) == before
    assert not (db.parent / "backups").exists()


def test_migrate_domains_apply_dispositions(tmp_path: Path) -> None:
    db = _make_fixture_db(tmp_path)
    verdicts = _write_verdicts(tmp_path)

    result = _run(
        "task", "migrate-domains", "--verdicts", str(verdicts), "--apply",
        db_path=db,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["dry_run"] is False

    rows = _task_rows(db)
    # ALREADY-FIXED -> completed, triage note appended, completed_at stamped.
    assert rows["T-FIX"]["status"] == "completed"
    assert "original note" in rows["T-FIX"]["notes"]
    assert "[triage: fix landed in abc123]" in rows["T-FIX"]["notes"]
    assert rows["T-FIX"]["completed_at"]
    assert rows["T-FIX"]["domain"] == "nexus"
    # OBSOLETE-REDESIGN / DUPLICATE -> archived, reason appended.
    assert rows["T-OBS"]["status"] == "archived"
    assert "superseded by the redesign" in rows["T-OBS"]["notes"]
    assert rows["T-DUP"]["status"] == "archived"
    # kb domain (ANY verdict) + owner-ruled TASK-054 -> archived, parked note.
    assert rows["T-KB"]["status"] == "archived"
    assert "parked for Zen Notes; vault content" in rows["T-KB"]["notes"]
    assert rows["TASK-054"]["status"] == "archived"
    assert "parked for Zen Notes;" in rows["TASK-054"]["notes"]
    assert "pre-existing" in rows["TASK-054"]["notes"]
    # Junk row deleted outright.
    assert "NATIVE-12-4" not in rows
    # Remaining NON-NEXUS -> archived.
    assert rows["T-NONNEX"]["status"] == "archived"
    assert rows["T-NONNEX"]["domain"] == "ops"
    # TASK-001 owner fresh-start ruling -> archived.
    assert rows["TASK-001"]["status"] == "archived"
    # Keep-list rows: status untouched, domain backfilled.
    assert rows["TASK-099"]["status"] == "in_progress"
    assert rows["TASK-099"]["domain"] == "nexus"
    # NATIVE-9-4 special: live, description rewritten to the GAP-09 tail.
    assert rows["NATIVE-9-4"]["status"] == "todo"
    assert rows["NATIVE-9-4"]["description"].startswith("GAP-09 survives")
    assert "CL-11" not in rows["NATIVE-9-4"]["description"]
    # STILL-APPLIES non-keep -> pending_review.
    assert rows["T-STILL"]["status"] == "pending_review"
    # Unclassified row untouched.
    assert rows["T-UNCLASSIFIED"]["status"] == "todo"
    assert rows["T-UNCLASSIFIED"]["domain"] is None

    # Backup exists and holds the PRE-migration state.
    backup = Path(out["backup"])
    assert backup.exists()
    bconn = sqlite3.connect(backup)
    try:
        pre_status = bconn.execute(
            "SELECT status FROM tasks WHERE id='T-FIX'"
        ).fetchone()[0]
    finally:
        bconn.close()
    assert pre_status == "todo"

    # Feedback: in-scope rows (captured <= cutoff DATE, inclusive) resolved
    # EXCEPT the still-valuable ids; post-cutoff rows untouched.
    fb = _feedback_rows(db)
    for kept in (3, 4, 6, 7, 9):
        assert fb[kept]["resolved_at"] is None, f"id {kept} must stay open"
    for resolved in (1, 2, 11):
        assert fb[resolved]["resolved_at"], f"id {resolved} must be resolved"
        assert fb[resolved]["reviewed_by"] == _REVIEWER
    assert fb[10]["resolved_at"] is None, "post-cutoff row must stay open"


def test_migrate_domains_double_apply_is_noop(tmp_path: Path) -> None:
    db = _make_fixture_db(tmp_path)
    verdicts = _write_verdicts(tmp_path)

    first = _run(
        "task", "migrate-domains", "--verdicts", str(verdicts), "--apply",
        db_path=db, check=True,
    )
    rows_after_first = _task_rows(db)
    fb_after_first = _feedback_rows(db)
    backup = Path(json.loads(first.stdout)["backup"])
    backup_bytes = backup.read_bytes()

    second = _run(
        "task", "migrate-domains", "--verdicts", str(verdicts), "--apply",
        db_path=db, check=True,
    )
    out2 = json.loads(second.stdout)
    assert out2["rows_to_update"] == 0
    assert out2["rows_to_delete"] == []
    assert out2["dispositions"]["already_applied"] == 11
    assert out2["feedback"]["resolved"] == 0

    assert _task_rows(db) == rows_after_first
    assert _feedback_rows(db) == fb_after_first
    # The rerun must NOT clobber the true pre-state backup.
    assert backup.read_bytes() == backup_bytes


# ---------------------------------------------------------------------------
# context dump — grouped shape + backward compatibility
# ---------------------------------------------------------------------------


def _dump(db: Path) -> dict:
    result = _run("context", "dump", db_path=db, check=True)
    return json.loads(result.stdout)


def test_context_dump_grouped_by_domain(tmp_path: Path) -> None:
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)
    _insert_task(db, "NEX-100", status="in_progress", domain="nexus")
    _insert_task(db, "NEX-101", status="blocked", domain="nexus")
    for n in range(10):
        prio = "critical" if n == 9 else "low"
        _insert_task(db, f"NEX-2{n:02d}", status="todo", priority=prio, domain="nexus")
    _insert_task(db, "PLX-001", status="todo", domain="plexus")
    _insert_task(db, "T-NODOM", status="todo")
    _insert_task(db, "T-PENDING", status="pending_review", domain="nexus")
    _insert_task(db, "T-ARCHIVED", status="archived", domain="plexus")

    out = _dump(db)
    grouped = out["tasks_by_domain"]
    assert list(grouped) == ["nexus", "plexus", "unclassified"]

    nexus = grouped["nexus"]
    assert [t["id"] for t in nexus["in_progress"]] == ["NEX-100"]
    assert [t["id"] for t in nexus["blocked"]] == ["NEX-101"]
    # todo capped at 8, highest priority first (the single critical row leads).
    assert len(nexus["todo"]) == 8
    assert nexus["todo"][0]["id"] == "NEX-209"
    assert nexus["todo_more"] == 2
    assert grouped["plexus"]["todo_more"] == 0
    assert [t["id"] for t in grouped["unclassified"]["todo"]] == ["T-NODOM"]

    # pending_review / archived are count-only: never in open_tasks or groups.
    open_ids = {t["id"] for t in out["open_tasks"]}
    assert "T-PENDING" not in open_ids
    assert "T-ARCHIVED" not in open_ids
    assert out["pending_review_task_count"] == 1
    assert out["archived_task_count"] == 1
    assert "pending_review" not in out["task_status_counts"]


def test_context_dump_backward_compatible_keys(tmp_path: Path) -> None:
    db = tmp_path / "project.db"
    _run("init", db_path=db, check=True)
    _insert_task(db, "NEX-001", status="in_progress", domain="nexus")

    out = _dump(db)
    for legacy_key in (
        "last_session", "open_tasks", "task_status_counts",
        "archived_task_count", "recent_decisions",
    ):
        assert legacy_key in out, f"legacy key {legacy_key} missing"
    assert isinstance(out["open_tasks"], list)
    row = out["open_tasks"][0]
    for col in ("id", "title", "status", "priority", "assigned_to"):
        assert col in row
    assert out["task_status_counts"] == {"in_progress": 1}

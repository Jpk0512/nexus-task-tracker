"""quill-py test outline — context-reset-monitor.py re-injection (GAP-02 / group E).

STAGED DRAFT. On apply, lands at .claude/hooks/tests/test_context_reset_monitor.py
(or nexus-broker/tests/ if that is where hook tests live). Exercises the patched
.claude/hooks/context-reset-monitor.py via subprocess, driving stdin payloads and
the _HOOK_DB_PATH / _HOOK_INVARIANTS_PATH overrides the hook already honors.

Design rules under test (SOTA report citations):
  - 3.7 post-compaction re-injection: digest re-emitted on the first turn after a
    compaction/resume/clear boundary.
  - 3.6 Self-Reminder + 3.7 single-canonical-source: emitted digest is BYTE-IDENTICAL
    to .claude/INVARIANTS.md (NoLiMa 2502.05167 — no paraphrase).
  - JSON shape contract: hookSpecificOutput is an OBJECT; hookEventName=="UserPromptSubmit";
    additionalContext == verbatim digest; no permissionDecision key.
  - Advisory: exit 0 always; existing message-count + HIGH-CONTEXT warning preserved.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "context-reset-monitor.py"


# ------------------------- fixtures -------------------------

@pytest.fixture
def digest_file(tmp_path):
    """A stand-in canonical INVARIANTS.md with the exact load-bearing tokens
    (DEC-002, DEC-005) so the verbatim-match assertions are meaningful."""
    p = tmp_path / "INVARIANTS.md"
    p.write_text(
        "=== PLEXUS INVARIANTS ===\n"
        "- main-only (DEC-002): NO branches, NO worktrees.\n"
        "- no-deferral (DEC-005): resolve inline OR open a tracked TaskCreate.\n",
        encoding="utf-8",
    )
    return p


def _make_db(p, tasks=None):
    """project.db with one live (ended_at IS NULL) session row, and a tasks
    table seeded with the given rows (id, title, status, priority, assigned_to).
    Mirrors the real schema columns the OPT-025 open-state query reads."""
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, started_at TEXT, "
        "ended_at TEXT, user_message_count INTEGER)"
    )
    conn.execute(
        "INSERT INTO sessions (id, started_at, ended_at, user_message_count) "
        "VALUES (1, '2026-05-31T00:00:00', NULL, 5)"
    )
    conn.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, "
        "priority TEXT, assigned_to TEXT)"
    )
    for row in tasks or []:
        conn.execute(
            "INSERT INTO tasks (id, title, status, priority, assigned_to) "
            "VALUES (?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def db_file(tmp_path):
    """project.db with a live session and a representative open-task ledger:
    open (todo/in_progress/blocked) + terminal (done/cancelled) rows so the
    OPT-025 open-state query (status NOT IN done/cancelled) is exercised."""
    return _make_db(
        tmp_path / "project.db",
        tasks=[
            ("TASK-001", "Wire OPT-025 quarantine", "in_progress", "high", "plexus"),
            ("TASK-002", "Backfill adversarial corpus", "todo", "medium", None),
            ("TASK-003", "Blocked on broker restart", "blocked", "low", "forge"),
            ("TASK-004", "Already shipped — must NOT appear", "done", "high", "lens"),
            ("TASK-005", "Abandoned — must NOT appear", "cancelled", "low", None),
        ],
    )


def run_hook(payload, db_file, digest_file, env_extra=None):
    env = dict(os.environ)
    env["_HOOK_DB_PATH"] = str(db_file)
    env["_HOOK_INVARIANTS_PATH"] = str(digest_file)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    return proc


def _injection(stdout):
    if not stdout.strip():
        return None
    return json.loads(stdout)["hookSpecificOutput"]


# ------------------------- re-injection: positive cases -------------------------

@pytest.mark.parametrize(
    "field,value",
    [("source", "compact"), ("source", "resume"), ("source", "clear"),
     ("hook_event", "compaction"), ("trigger", "auto-compact"), ("reason", "RESUME")],
)
def test_reset_signal_triggers_injection(field, value, db_file, digest_file):
    """3.7: any conventional reset source field (case-insensitive) re-injects."""
    proc = run_hook({"prompt": "x", field: value}, db_file, digest_file)
    assert proc.returncode == 0
    inj = _injection(proc.stdout)
    assert inj is not None
    assert inj["hookEventName"] == "UserPromptSubmit"


def test_injected_digest_is_byte_identical(db_file, digest_file):
    """3.6/3.7 + NoLiMa: the verbatim canonical digest is emitted UNMODIFIED at
    the head of additionalContext. OPT-025 appends a quarantine + open-state
    addendum AFTER it, so the digest is now the byte-identical PREFIX (never
    paraphrased / never edited), not the entire string."""
    proc = run_hook({"prompt": "x", "source": "compact"}, db_file, digest_file)
    inj = _injection(proc.stdout)
    digest = digest_file.read_text(encoding="utf-8")
    # Verbatim prefix: the protected digest is emitted FIRST and untouched.
    assert inj["additionalContext"].startswith(digest)
    # And the full verbatim digest is present as a contiguous substring (no
    # token was reworded or split — NoLiMa associative-recall guarantee).
    assert digest in inj["additionalContext"]


def test_count_position_discontinuity_triggers_injection(db_file, digest_file):
    """3.7 branch (ii): non-zero persisted count + tiny live transcript ⇒ reset."""
    proc = run_hook({"prompt": "x", "transcript_length": 0}, db_file, digest_file)
    assert _injection(proc.stdout) is not None


# ------------------------- OPT-025 compaction-integrity addendum -------------------------

def test_compaction_injection_carries_invariants_quarantine_and_open_state(db_file, digest_file):
    """OPT-025: the compaction-path additionalContext contains ALL THREE —
    (1) the verbatim INVARIANTS digest, (2) the QUARANTINE caveat marking summary
    completion-claims as DATA needing re-verification before NEXUS:DONE, and
    (3) the AUTHORITATIVE open-state listing the open/in_progress task rows."""
    proc = run_hook({"prompt": "x", "source": "compact"}, db_file, digest_file)
    ctx = _injection(proc.stdout)["additionalContext"]

    # (1) verbatim INVARIANTS — full canonical file present, byte-identical.
    assert digest_file.read_text(encoding="utf-8") in ctx
    assert "DEC-002" in ctx and "DEC-005" in ctx  # load-bearing tokens intact

    # (2) QUARANTINE caveat — claims are DATA, re-verify before NEXUS:DONE.
    assert "COMPACTION QUARANTINE" in ctx
    assert "DATA, not ground truth" in ctx
    assert "NEXUS:DONE" in ctx

    # (3) AUTHORITATIVE open-state — open rows present; terminal rows excluded.
    assert "AUTHORITATIVE OPEN STATE" in ctx
    assert "TASK-001" in ctx and "TASK-002" in ctx and "TASK-003" in ctx
    assert "TASK-004" not in ctx  # status=done — excluded
    assert "TASK-005" not in ctx  # status=cancelled — excluded
    assert "3 open task(s)" in ctx


def test_open_state_uses_context_dump_query_semantics(db_file, digest_file):
    """The open-state mirrors `log.py context dump`: status NOT IN
    ('done','cancelled'). in_progress, todo, and blocked all surface."""
    proc = run_hook({"prompt": "x", "source": "resume"}, db_file, digest_file)
    ctx = _injection(proc.stdout)["additionalContext"]
    assert "[in_progress] TASK-001" in ctx
    assert "[todo] TASK-002" in ctx
    assert "[blocked] TASK-003" in ctx


def test_open_state_is_capped_and_reports_overflow(tmp_path, digest_file):
    """OPEN_STATE_CAP keeps the re-grounded tail concise; overflow is summarized
    rather than dumped in full."""
    many = [
        (f"TASK-{i:03d}", f"open item {i}", "todo", "medium", None)
        for i in range(20)
    ]
    db = _make_db(tmp_path / "many.db", tasks=many)
    proc = run_hook({"prompt": "x", "source": "compact"}, db, digest_file,
                    env_extra={"CONTEXT_OPEN_STATE_CAP": "5"})
    ctx = _injection(proc.stdout)["additionalContext"]
    assert "20 open task(s)" in ctx          # true total still reported
    assert "+15 more" in ctx                 # overflow summarized
    assert ctx.count("] TASK-") == 5         # only CAP rows listed verbatim


def test_open_state_empty_ledger_still_injects_digest_and_quarantine(tmp_path, digest_file):
    """A clean ledger (no open tasks) still re-injects the digest + quarantine,
    and reports (none open) — the absence of open work is itself ground truth."""
    db = _make_db(tmp_path / "empty.db", tasks=[])
    proc = run_hook({"prompt": "x", "source": "compact"}, db, digest_file)
    ctx = _injection(proc.stdout)["additionalContext"]
    assert digest_file.read_text(encoding="utf-8") in ctx
    assert "COMPACTION QUARANTINE" in ctx
    assert "0 open task(s)" in ctx
    assert "(none open)" in ctx


def test_missing_tasks_table_does_not_suppress_digest_or_quarantine(tmp_path, digest_file):
    """If project.db has no tasks table (sqlite error), the open-state is
    omitted but the digest + quarantine still re-inject (advisory degrade)."""
    p = tmp_path / "no-tasks.db"
    conn = sqlite3.connect(str(p))
    conn.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY, started_at TEXT, "
        "ended_at TEXT, user_message_count INTEGER)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (1, '2026-05-31T00:00:00', NULL, 5)"
    )
    conn.commit()
    conn.close()
    proc = run_hook({"prompt": "x", "source": "compact"}, p, digest_file)
    ctx = _injection(proc.stdout)["additionalContext"]
    assert digest_file.read_text(encoding="utf-8") in ctx
    assert "COMPACTION QUARANTINE" in ctx
    # The open-state BLOCK header (distinct from the quarantine's reference to it)
    # is absent — its query hit a sqlite error and degraded silently.
    assert "open task(s)" not in ctx


def test_no_db_but_reset_signal_still_injects(tmp_path, digest_file):
    """Resume before the session row exists must still re-ground."""
    missing_db = tmp_path / "absent.db"
    proc = run_hook({"prompt": "x", "source": "resume"}, missing_db, digest_file)
    assert proc.returncode == 0
    assert _injection(proc.stdout) is not None


# ------------------------- re-injection: negative cases (no false positives) -------------------------

def test_normal_turn_no_injection(db_file, digest_file):
    """A plain turn must NOT inject — re-injection only at boundaries."""
    proc = run_hook({"prompt": "hello"}, db_file, digest_file)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_discontinuity_not_asserted_without_live_length(db_file, digest_file):
    """No transcript-length signal ⇒ defer to branch (i); no inject on a bare turn."""
    proc = run_hook({"prompt": "hello"}, db_file, digest_file)
    assert _injection(proc.stdout) is None


def test_unrelated_source_value_does_not_inject(db_file, digest_file):
    """source='cli'/'startup' is not a reset marker."""
    proc = run_hook({"prompt": "x", "source": "cli"}, db_file, digest_file)
    assert _injection(proc.stdout) is None


# ------------------------- JSON shape contract -------------------------

def test_hookSpecificOutput_is_object_no_permission_key(db_file, digest_file):
    proc = run_hook({"prompt": "x", "source": "compact"}, db_file, digest_file)
    out = json.loads(proc.stdout)
    assert isinstance(out["hookSpecificOutput"], dict)
    assert set(out["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert "permissionDecision" not in out["hookSpecificOutput"]


def test_missing_canonical_file_stays_silent_no_paraphrase(db_file, tmp_path):
    """Missing INVARIANTS.md ⇒ no stdout (never improvise a paraphrase)."""
    absent = tmp_path / "no-invariants.md"
    proc = run_hook({"prompt": "x", "source": "compact"}, db_file, absent)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ------------------------- preserved legacy behavior -------------------------

def test_invalid_json_stdin_exits_zero(db_file, digest_file):
    env = dict(os.environ)
    env["_HOOK_DB_PATH"] = str(db_file)
    env["_HOOK_INVARIANTS_PATH"] = str(digest_file)
    proc = subprocess.run([sys.executable, str(HOOK)], input="not json",
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0


def test_db_error_exits_zero_and_warns(tmp_path, digest_file):
    """A corrupt DB surfaces a stderr error but never blocks (advisory)."""
    bad = tmp_path / "corrupt.db"
    bad.write_text("not a sqlite file", encoding="utf-8")
    proc = run_hook({"prompt": "x"}, bad, digest_file)
    assert proc.returncode == 0

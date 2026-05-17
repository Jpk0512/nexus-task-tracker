"""
Tests for .claude/hooks/ discipline enforcement scripts.

Each test invokes the hook script as a subprocess — exactly as Claude Code's
harness does. Input is written to stdin as JSON. Exit code and stderr/stdout
are asserted.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# =============================================================================
# IMPORTANT: These tests use paths derived from the repo root at runtime.
# By default REPO_ROOT is resolved from the test file's location.
# To override, set environment variables before running pytest:
#   REPO_ROOT - absolute path to your project root
#   DB_PATH   - absolute path to your project's .memory/project.db
# =============================================================================

HOOKS_DIR = Path(__file__).parent.parent
REPO_ROOT = Path(os.environ.get("REPO_ROOT", "")) or HOOKS_DIR.parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", "")) or REPO_ROOT / ".memory" / "project.db"
DB_PATH_DEFAULT = DB_PATH

# ─── helpers ────────────────────────────────────────────────────────────────


def run_hook(
    script: str,
    stdin_payload: dict,
    env: dict | None = None,
    db_path: str | None = None,
) -> tuple[int, str, str]:
    """Invoke a hook script, return (exit_code, stdout, stderr)."""
    hook_path = HOOKS_DIR / script
    merged_env = {**os.environ}
    if db_path:
        merged_env["_HOOK_DB_PATH"] = db_path
    if env:
        merged_env.update(env)

    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=merged_env,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


def make_db(schema_path: Path | None = None) -> str:
    """Create a temp in-memory DB file with the project schema."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    if schema_path and schema_path.exists():
        import sqlite_vec as _sv
        conn.enable_load_extension(True)
        _sv.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()
    return f.name


SCHEMA_PATH = REPO_ROOT / ".memory" / "schema.sql"


# ─── root-cause-gate.sh ─────────────────────────────────────────────────────

REVISE_WITHOUT_RCA = """\
Some analysis content.

## NEXUS:REVISE

The implementation is missing a guard.
"""

REVISE_WITH_RCA = """\
Some analysis content.

## Root Cause Analysis

Symptom: Sample refresh crashes on 500.
Why 1: Worker process exits before writing the response.
Why 2: Redis broker connection times out during peak load.
Why 3: Connection pool size is hard-coded to 1.
Why 4: Default dramatiq Redis config was not overridden in prod.
Why 5: No environment-specific dramatiq config exists; defaults were silently wrong.
Pattern fix: Always set broker connection pool size via env var in dramatiq init.

## NEXUS:REVISE

Fix the Redis pool size config.
"""

DONE_FIX_TASK_WITHOUT_RCA = """\
Shipping the fix.

## NEXUS:DONE
"""

DONE_FEATURE_TASK_WITHOUT_RCA = """\
New feature shipped.

## NEXUS:DONE
"""


class TestRootCauseGate:
    def test_revise_without_rca_blocks(self) -> None:
        payload = {
            "last_assistant_message": REVISE_WITHOUT_RCA,
            "session_id": "S-test-001",
            "agent_persona": "pipeline",
            "task_description": "fix the ingestion bug",
        }
        code, _out, err = run_hook("root-cause-gate.sh", payload)
        assert code == 2, f"Expected exit 2, got {code}. stderr={err}"
        assert "BLOCK" in err

    def test_revise_with_valid_rca_passes(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            payload = {
                "last_assistant_message": REVISE_WITH_RCA,
                "session_id": "S-test-002",
                "agent_persona": "pipeline",
                "task_description": "fix the ingestion bug",
            }
            env = {"_HOOK_DB_PATH": db}
            # Patch DB_PATH in the hook by passing env var — hook reads
            # _HOOK_DB_PATH if present (see hook implementation note below).
            # We test exit code 0 and a row in the DB.
            code, _out, err = run_hook("root-cause-gate.sh", payload, env=env)
            # Exit 0 (pass)
            assert code == 0, f"Expected exit 0, got {code}. stderr={err}"
        finally:
            os.unlink(db)

    def test_done_fix_task_without_rca_blocks(self) -> None:
        payload = {
            "last_assistant_message": DONE_FIX_TASK_WITHOUT_RCA,
            "session_id": "S-test-003",
            "agent_persona": "forge",
            "task_description": "fix the 500 error in sample refresh",
        }
        code, _out, err = run_hook("root-cause-gate.sh", payload)
        assert code == 2, f"Expected exit 2 for fix-task DONE, got {code}"
        assert "BLOCK" in err

    def test_done_feature_task_no_rca_required(self) -> None:
        payload = {
            "last_assistant_message": DONE_FEATURE_TASK_WITHOUT_RCA,
            "session_id": "S-test-004",
            "agent_persona": "forge",
            "task_description": "add new export button to workbooks page",
        }
        code, _out, _err = run_hook("root-cause-gate.sh", payload)
        assert code == 0, f"Expected exit 0 for feature task, got {code}"

    def test_empty_input_does_not_crash(self) -> None:
        code, _out, _err = run_hook("root-cause-gate.sh", {})
        assert code == 0

    def test_done_with_bug_keyword_in_description_blocks(self) -> None:
        payload = {
            "last_assistant_message": DONE_FEATURE_TASK_WITHOUT_RCA,
            "session_id": "S-test-005",
            "agent_persona": "pipeline",
            "task_description": "resolve bug in datasource connector",
        }
        code, _out, err = run_hook("root-cause-gate.sh", payload)
        assert code == 2, f"Expected block for 'bug' keyword, got {code}"
        assert "BLOCK" in err


# ─── lesson-harvester.sh ────────────────────────────────────────────────────


def _seed_session_and_decision(
    db_path: str,
    session_id: str,
    ended: bool,
    dec_id: str,
    rationale: str,
    has_lesson: bool = False,
) -> None:
    conn = sqlite3.connect(db_path)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    ended_at = now if ended else None
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, started_at, ended_at) VALUES (?, ?, ?)",
        (session_id, now, ended_at),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO decisions
            (id, title, context, decision, rationale, decided_at, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (dec_id, "Test decision", "some context", "do the thing", rationale, now, session_id),
    )
    if has_lesson:
        conn.execute(
            """
            INSERT INTO lessons (id, trigger, title, body, applies_to,
                                  source_session_id, source_decision_id, recorded_at)
            VALUES ('LSN-test-01', 'redelegation', 'test lesson', 'body text',
                    'all', ?, ?, ?)
            """,
            (session_id, dec_id, now),
        )
    conn.commit()
    conn.close()


class TestLessonHarvester:
    def test_prior_session_with_redelegation_emits_suggestion(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            _seed_session_and_decision(
                db,
                "S-harvest-001",
                ended=True,
                dec_id="DEC-HARVEST-01",
                rationale="This was caused by a redelegation from pipeline to forge.",
                has_lesson=False,
            )
            # Patch DB path via monkey-patch approach: write a temp wrapper.
            hook_src = (HOOKS_DIR / "lesson-harvester.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps({}),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)

            assert "lesson-harvester" in result.stderr, (
                f"Expected harvester output, got: {result.stderr}"
            )
            assert "DEC-HARVEST-01" in result.stderr
        finally:
            os.unlink(db)

    def test_prior_session_with_lesson_present_no_suggestion(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            _seed_session_and_decision(
                db,
                "S-harvest-002",
                ended=True,
                dec_id="DEC-HARVEST-02",
                rationale="This was caused by a revise loop that blocked progress.",
                has_lesson=True,
            )
            hook_src = (HOOKS_DIR / "lesson-harvester.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps({}),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)

            assert "lesson-harvester" not in result.stderr, (
                f"Expected no harvester output when lesson exists, got: {result.stderr}"
            )
        finally:
            os.unlink(db)

    def test_no_prior_session_exits_cleanly(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            hook_src = (HOOKS_DIR / "lesson-harvester.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps({}),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)
            assert result.returncode == 0
        finally:
            os.unlink(db)


# ─── reflection-capture.sh ──────────────────────────────────────────────────


class TestReflectionCapture:
    def _make_edit_payload(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        session_id: str = "S-reflect-001",
    ) -> dict:
        return {
            "tool_name": "Edit",
            "session_id": session_id,
            "tool_input": {
                "file_path": file_path,
                "old_string": old_string,
                "new_string": new_string,
            },
            "tool_result": {},
        }

    def test_constitution_edit_with_large_diff_inserts_row(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            hook_src = (HOOKS_DIR / "reflection-capture.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            old = "\n".join([f"Old line {i}" for i in range(10)])
            new = "\n".join([f"New line {i}" for i in range(10)])
            payload = self._make_edit_payload(
                str(REPO_ROOT / "docs" / "CONSTITUTION.md"),
                old,
                new,
            )
            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)

            assert result.returncode == 0
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT * FROM reflection_snapshot").fetchall()
            conn.close()
            assert len(rows) == 1, f"Expected 1 snapshot row, got {len(rows)}"
            assert rows[0][3] == "constitution_amend"
        finally:
            os.unlink(db)

    def test_small_diff_under_5_lines_no_row(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            hook_src = (HOOKS_DIR / "reflection-capture.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            payload = self._make_edit_payload(
                str(REPO_ROOT / "docs" / "CONSTITUTION.md"),
                old_string="Article I remains unchanged.",
                new_string="Article I remains unchanged. (minor tweak)",
            )
            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)

            assert result.returncode == 0
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT * FROM reflection_snapshot").fetchall()
            conn.close()
            assert len(rows) == 0, "Expected no row for tiny diff"
        finally:
            os.unlink(db)

    def test_non_watched_file_no_row(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            hook_src = (HOOKS_DIR / "reflection-capture.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            old = "\n".join([f"Old line {i}" for i in range(10)])
            new = "\n".join([f"New line {i}" for i in range(10)])
            payload = self._make_edit_payload(
                str(REPO_ROOT / "app" / "lib" / "db.ts"),
                old,
                new,
            )
            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)

            assert result.returncode == 0
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT * FROM reflection_snapshot").fetchall()
            conn.close()
            assert len(rows) == 0, "Expected no row for non-watched file"
        finally:
            os.unlink(db)

    def test_spec_update_classified_correctly(self) -> None:
        db = make_db(SCHEMA_PATH)
        try:
            hook_src = (HOOKS_DIR / "reflection-capture.sh").read_text()
            patched_src = hook_src.replace(
                'DB_PATH = os.environ.get("DB_PATH", os.path.join(os.getcwd(), ".memory", "project.db"))',
                f'DB_PATH = "{db}"',
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir="/tmp"
            ) as tmp:
                tmp.write(patched_src)
                tmp_path = tmp.name
            os.chmod(tmp_path, 0o755)

            old = "\n".join([f"Old spec line {i}" for i in range(10)])
            new = "\n".join([f"New spec line {i}" for i in range(10)])
            payload = self._make_edit_payload(
                str(REPO_ROOT / "docs" / "features" / "FEAT-010.md"),
                old,
                new,
            )
            result = subprocess.run(
                [sys.executable, tmp_path],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(tmp_path)

            assert result.returncode == 0
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT action_type FROM reflection_snapshot").fetchall()
            conn.close()
            assert len(rows) == 1
            assert rows[0][0] == "spec_update"
        finally:
            os.unlink(db)


# ─── socraticode-gate.sh Read-block ─────────────────────────────────────────

FLAG_DIR = os.environ.get("TMPDIR", "/tmp")


def _flag_path(session_id: str) -> str:
    return os.path.join(FLAG_DIR, f"claude-socraticode-{session_id}.flag")


class TestSocraticodeReadBlock:
    def _make_read_payload(self, file_path: str, session_id: str = "S-gate-test") -> dict:
        return {
            "tool_name": "Read",
            "session_id": session_id,
            "tool_input": {"file_path": file_path},
        }

    def _invoke_gate(
        self,
        payload: dict,
        session_id: str,
        task_description: str = "",
        has_flag: bool = False,
    ) -> tuple[int, str, str]:
        flag = _flag_path(session_id)
        if has_flag:
            Path(flag).touch()
        else:
            Path(flag).unlink(missing_ok=True)
        try:
            env = {}
            if task_description:
                env["CLAUDE_TASK_DESCRIPTION"] = task_description
            result = subprocess.run(
                ["/bin/bash", str(HOOKS_DIR / "socraticode-gate.sh")],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                env={**os.environ, **env},
                timeout=10,
            )
            return result.returncode, result.stdout, result.stderr
        finally:
            Path(flag).unlink(missing_ok=True)

    def test_read_on_app_path_without_flag_blocks(self) -> None:
        sid = "S-gate-read-01"
        payload = self._make_read_payload(
            str(REPO_ROOT / "app" / "lib" / "db.ts"), sid
        )
        _code, out, _err = self._invoke_gate(payload, sid, has_flag=False)
        assert "deny" in out or "BLOCK" in out, (
            f"Expected deny decision. stdout={out!r}"
        )

    def test_read_on_app_path_with_flag_allows(self) -> None:
        sid = "S-gate-read-02"
        payload = self._make_read_payload(
            str(REPO_ROOT / "app" / "lib" / "db.ts"), sid
        )
        _code, out, _err = self._invoke_gate(payload, sid, has_flag=True)
        # With flag: should NOT emit deny
        try:
            parsed = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        decision = (
            parsed.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
        )
        assert decision != "deny", f"Expected allow when flag set. stdout={out!r}"

    def test_read_on_brief_mentioned_path_allows(self) -> None:
        sid = "S-gate-read-03"
        path = str(REPO_ROOT / "app" / "lib" / "db.ts")
        payload = self._make_read_payload(path, sid)
        # Path appears in task brief — should be allowed.
        _code, out, _err = self._invoke_gate(
            payload,
            sid,
            task_description=f"Edit `{path}` to change the connection pool size.",
            has_flag=False,
        )
        try:
            parsed = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        decision = (
            parsed.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
        )
        assert decision != "deny", (
            f"Expected allow for brief-mentioned path. stdout={out!r}"
        )

    def test_read_on_non_watched_path_allows(self) -> None:
        sid = "S-gate-read-04"
        payload = self._make_read_payload(
            str(REPO_ROOT / ".memory" / "schema.sql"), sid
        )
        _code, out, _err = self._invoke_gate(payload, sid, has_flag=False)
        try:
            parsed = json.loads(out) if out.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        decision = (
            parsed.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
        )
        assert decision != "deny", (
            f"Expected allow for non-watched path. stdout={out!r}"
        )

    def test_existing_grep_block_still_works(self) -> None:
        """Regression: existing Bash grep block should still fire."""
        sid = "S-gate-grep-01"
        flag = _flag_path(sid)
        Path(flag).unlink(missing_ok=True)
        payload = {
            "tool_name": "Bash",
            "session_id": sid,
            "tool_input": {"command": "grep -r 'foo' ingestion/"},
        }
        result = subprocess.run(
            ["/bin/bash", str(HOOKS_DIR / "socraticode-gate.sh")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            timeout=10,
        )
        assert "deny" in result.stdout or "BLOCK" in result.stdout, (
            f"Expected grep to be blocked. stdout={result.stdout!r}"
        )


# ─── lens-gate.sh ────────────────────────────────────────────────────────────

import hashlib as _hashlib


def _lens_gate_payload(
    agent: str,
    marker: str = "DONE",
    files_changed: list[str] | None = None,
    task_description: str = "add search feature to ingestion pipeline",
) -> dict:
    """Build a minimal SubagentStop payload for lens-gate tests."""
    if files_changed is None:
        files_changed = ["ingestion/src/ingest.py"]
    fc_json = json.dumps(files_changed)
    assistant_text = f"""
Some implementation work.

```json
{{
  "status": "complete",
  "completion_marker": "## NEXUS:{marker}",
  "files_changed": {fc_json},
  "verification_result": "ruff check: OK",
  "acceptance_met": [],
  "blockers": [],
  "decisions_needed": [],
  "notes": ""
}}
```

## NEXUS:{marker}
"""
    return {
        "last_assistant_message": assistant_text,
        "session_id": "S-lens-test",
        "agent_persona": agent,
        "task_description": task_description,
    }


def _seed_lens_validation(
    db_path: str,
    target_agent: str,
    task_hash: str,
    verdict: str = "PASS",
    age_minutes: int = 0,
) -> None:
    """Insert a validation_log row for tests that need a passing Lens check."""
    from datetime import datetime, timedelta, timezone

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS validation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            agent_validated     TEXT NOT NULL,
            target_agent        TEXT NOT NULL,
            task_or_brief_hash  TEXT NOT NULL,
            verdict             TEXT NOT NULL,
            evidence_summary    TEXT,
            validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    conn.execute(
        """INSERT INTO validation_log
           (session_id, agent_validated, target_agent, task_or_brief_hash, verdict, validated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("S-lens-test", "lens", target_agent, task_hash, verdict, ts),
    )
    conn.commit()
    conn.close()


def _derive_hash(payload: dict, assistant_text: str) -> str:
    """Mirror the hash logic in lens-gate.sh for test reproducibility."""
    task_desc = payload.get("task_description", "")
    raw = task_desc or assistant_text[:500]
    return _hashlib.sha256(raw.encode()).hexdigest()[:16]


class TestLensGate:
    def test_forge_done_no_lens_row_blocks(self) -> None:
        """Case 1: Forge NEXUS:DONE with source files, no Lens row → BLOCK."""
        db = make_db(SCHEMA_PATH)
        try:
            payload = _lens_gate_payload("forge", files_changed=["app/lib/db.ts"])
            code, _out, err = run_hook("lens-gate.sh", payload, db_path=db)
            assert code == 2, f"Expected exit 2 (BLOCK), got {code}. stderr={err}"
            assert "lens-gate" in err and "BLOCK" in err
        finally:
            os.unlink(db)

    def test_forge_done_with_lens_row_passes(self) -> None:
        """Case 2: Forge NEXUS:DONE with Lens row written in last hour → PASS."""
        db = make_db(SCHEMA_PATH)
        try:
            payload = _lens_gate_payload("forge", files_changed=["app/lib/db.ts"])
            # Compute the same hash the hook will derive
            assistant_text = payload["last_assistant_message"]
            task_hash = _derive_hash(payload, assistant_text)
            _seed_lens_validation(db, "forge", task_hash, age_minutes=30)
            code, _out, err = run_hook("lens-gate.sh", payload, db_path=db)
            assert code == 0, f"Expected exit 0 (PASS), got {code}. stderr={err}"
        finally:
            os.unlink(db)

    def test_scout_done_passes_without_lens_row(self) -> None:
        """Case 3: Scout NEXUS:DONE → PASS (Scout is not a gated agent)."""
        db = make_db(SCHEMA_PATH)
        try:
            payload = _lens_gate_payload("scout", files_changed=["ingestion/src/ingest.py"])
            code, _out, _err = run_hook("lens-gate.sh", payload, db_path=db)
            assert code == 0, f"Expected exit 0 for Scout, got {code}"
        finally:
            os.unlink(db)

    def test_forge_done_pure_docs_passes(self) -> None:
        """Case 4: Forge NEXUS:DONE but only docs/ files changed → PASS (no source trigger)."""
        db = make_db(SCHEMA_PATH)
        try:
            payload = _lens_gate_payload(
                "forge",
                files_changed=["docs/DECISIONS.md", "docs/features/FEAT-010.md"],
            )
            code, _out, _err = run_hook("lens-gate.sh", payload, db_path=db)
            assert code == 0, f"Expected exit 0 for docs-only change, got {code}"
        finally:
            os.unlink(db)

    def test_forge_revise_passes_without_lens_row(self) -> None:
        """Case 5: Forge NEXUS:REVISE → PASS (gate only applies to DONE)."""
        db = make_db(SCHEMA_PATH)
        try:
            payload = _lens_gate_payload(
                "forge",
                marker="REVISE",
                files_changed=["app/lib/db.ts"],
            )
            code, _out, _err = run_hook("lens-gate.sh", payload, db_path=db)
            assert code == 0, f"Expected exit 0 for REVISE marker, got {code}"
        finally:
            os.unlink(db)


# ─── return-summarizer tests ─────────────────────────────────────────────────

LOG_PY = REPO_ROOT / ".memory" / "log.py"
SCHEMA_PATH_MEMORY = REPO_ROOT / ".memory" / "schema.sql"


def _invoke_subagent_return_record(
    agent: str,
    response_text: str,
    db_path: str,
    returns_dir: str,
) -> tuple[int, str, str]:
    """Directly call log.py subagent-return record with a temp file."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(response_text)
        tmp = f.name
    try:
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
        result = subprocess.run(
            [
                sys.executable,
                str(LOG_PY),
                "subagent-return",
                "record",
                "--agent", agent,
                "--full-response-file", tmp,
            ],
            capture_output=True,
            text=True,
            env={
                **env,
                # Point the DB at the test temp DB via monkeypatching the module path.
                # log.py uses DB_PATH = Path(__file__).parent / "project.db", so we
                # symlink the test DB into a tmpdir that matches the module's expectation.
                # Simpler: pass via env var the subagent-returns dir and DB override.
                "_TEST_DB_PATH": db_path,
                "_TEST_RETURNS_DIR": returns_dir,
            },
            timeout=15,
        )
        return result.returncode, result.stdout, result.stderr
    finally:
        Path(tmp).unlink(missing_ok=True)


def _make_large_response(tokens: int = 1500) -> str:
    """Build a fake subagent response of approximately `tokens` tokens."""
    # ~4 chars/token.
    body = (
        "## NEXUS:DONE\n\n"
        '{"files_changed": ["ingestion/src/pipeline.py", "ingestion/tests/test_pipeline.py"], '
        '"verdict": "PASS", "blockers": []}\n\n'
        "## Root Cause Analysis\nThe ingestion job failed because of a missing env var. "
        "Fixed by adding os.environ lookup.\n\n"
    )
    # Pad to ~target chars.
    filler = "This is context filler text to pad the response body. " * 20
    target_chars = tokens * 4
    while len(body) < target_chars:
        body += filler
    return body[:target_chars]


class TestReturnSummarizer:
    """Three acceptance cases for subagent-return record (Mitigation A)."""

    def test_large_response_persisted_and_notepad_written(self, tmp_path: Path) -> None:
        """Case 1: Large response (>1K tokens) → file written + summary ≤500 chars in notepad."""
        # Use a real project.db copy so notepad table exists.
        db_file = str(tmp_path / "project.db")
        # Initialise schema from the real schema.sql.
        if SCHEMA_PATH_MEMORY.exists():
            conn = sqlite3.connect(db_file)
            import sqlite_vec as _sv
            conn.enable_load_extension(True)
            _sv.load(conn)
            conn.enable_load_extension(False)
            conn.executescript(SCHEMA_PATH_MEMORY.read_text())
            # Seed an open session so notepad can insert.
            conn.execute(
                "INSERT INTO sessions (id, started_at, branch) VALUES ('S-test', '2026-01-01T00:00:00+00:00', 'main')"
            )
            conn.commit()
            conn.close()

        returns_dir = str(tmp_path / "subagent-returns")
        large_text = _make_large_response(tokens=2000)

        # We invoke log.py directly (same boundary as the hook invokes it).
        # Because log.py hard-codes DB_PATH, we instead write a temp wrapper
        # that patches the path before importing.
        wrapper = tmp_path / "run_record.py"
        wrapper.write_text(f"""
import sys, pathlib, importlib.util
spec = importlib.util.spec_from_file_location("log", r"{LOG_PY}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
# Patch AFTER exec_module so module-level assignments don't clobber us.
mod.DB_PATH = pathlib.Path(r"{db_file}")
mod._SUBAGENT_RETURNS_DIR = pathlib.Path(r"{returns_dir}")
sys.argv = ["log.py", "subagent-return", "record", "--agent", "pipeline", "--full-response-file", sys.argv[1]]
mod.main()
""")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(large_text)
            resp_file = f.name

        try:
            result = subprocess.run(
                [sys.executable, str(wrapper), resp_file],
                capture_output=True, text=True, timeout=15,
            )
        finally:
            Path(resp_file).unlink(missing_ok=True)

        assert result.returncode == 0, f"record exited {result.returncode}: {result.stderr}"
        # stdout may have multiple JSON lines (notepad_add + record); take the last one.
        last_line = [l for l in result.stdout.strip().splitlines() if l.strip()][-1]
        out = json.loads(last_line)

        # File was persisted.
        assert out.get("persisted"), "Expected 'persisted' key with path"
        persisted_path = Path(out["persisted"])
        assert persisted_path.exists(), f"Persisted file not found: {persisted_path}"

        # Summary ≤500 chars was written to notepad (check via notepad_id in stdout).
        insight = out.get("insight", "")
        assert len(insight) <= 490, f"Insight too long: {len(insight)} chars"
        assert insight, "Insight must not be empty"

        # Notepad row should exist in the DB.
        conn = sqlite3.connect(db_file)
        rows = conn.execute("SELECT note FROM agent_notepad").fetchall()
        conn.close()
        assert rows, "Expected at least one notepad row after large response"
        note_text = rows[0][0]
        assert len(note_text) <= 500, f"Notepad note exceeds 500 chars: {len(note_text)}"

    def test_tiny_response_skipped(self, tmp_path: Path) -> None:
        """Case 2: Tiny response (<1K tokens) → skipped, no file written."""
        db_file = str(tmp_path / "project.db")
        if SCHEMA_PATH_MEMORY.exists():
            conn = sqlite3.connect(db_file)
            import sqlite_vec as _sv
            conn.enable_load_extension(True)
            _sv.load(conn)
            conn.enable_load_extension(False)
            conn.executescript(SCHEMA_PATH_MEMORY.read_text())
            conn.commit()
            conn.close()

        returns_dir = str(tmp_path / "subagent-returns")
        small_text = "OK\n"  # definitely < 1K tokens

        wrapper = tmp_path / "run_record_tiny.py"
        wrapper.write_text(f"""
import sys, pathlib, importlib.util
spec = importlib.util.spec_from_file_location("log", r"{LOG_PY}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.DB_PATH = pathlib.Path(r"{db_file}")
mod._SUBAGENT_RETURNS_DIR = pathlib.Path(r"{returns_dir}")
sys.argv = ["log.py", "subagent-return", "record", "--agent", "scout", "--full-response-file", sys.argv[1]]
mod.main()
""")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(small_text)
            resp_file = f.name

        try:
            result = subprocess.run(
                [sys.executable, str(wrapper), resp_file],
                capture_output=True, text=True, timeout=15,
            )
        finally:
            Path(resp_file).unlink(missing_ok=True)

        assert result.returncode == 0, f"Unexpected failure: {result.stderr}"
        out = json.loads(result.stdout)
        assert out.get("skipped") is True, f"Expected skipped=True for tiny response, got {out}"

        # No files created.
        returns_path = Path(returns_dir)
        files = list(returns_path.glob("*.txt")) if returns_path.exists() else []
        assert not files, f"Expected no persisted files for tiny response, found {files}"

    def test_malformed_response_no_crash(self, tmp_path: Path) -> None:
        """Case 3: Malformed / non-JSON response → graceful exit 0, no crash."""
        db_file = str(tmp_path / "project.db")
        if SCHEMA_PATH_MEMORY.exists():
            conn = sqlite3.connect(db_file)
            conn.executescript(SCHEMA_PATH_MEMORY.read_text())
            conn.execute(
                "INSERT INTO sessions (id, started_at, branch) VALUES ('S-test2', '2026-01-01T00:00:00+00:00', 'main')"
            )
            conn.commit()
            conn.close()

        returns_dir = str(tmp_path / "subagent-returns")
        # Large enough to exceed token threshold but with no extractable markers.
        malformed_text = "\x00\xff\xfe" * 2000 + ("garbage " * 500)

        wrapper = tmp_path / "run_record_malformed.py"
        wrapper.write_text(f"""
import sys, pathlib, importlib.util
spec = importlib.util.spec_from_file_location("log", r"{LOG_PY}")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.DB_PATH = pathlib.Path(r"{db_file}")
mod._SUBAGENT_RETURNS_DIR = pathlib.Path(r"{returns_dir}")
sys.argv = ["log.py", "subagent-return", "record", "--agent", "forge", "--full-response-file", sys.argv[1]]
mod.main()
""")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8", errors="replace") as f:
            f.write(malformed_text)
            resp_file = f.name

        try:
            result = subprocess.run(
                [sys.executable, str(wrapper), resp_file],
                capture_output=True, text=True, timeout=15,
            )
        finally:
            Path(resp_file).unlink(missing_ok=True)

        # Must not crash — exit 0 is required.
        assert result.returncode == 0, (
            f"Malformed response caused non-zero exit {result.returncode}: {result.stderr}"
        )


# ─── context-reset-monitor.sh tests ─────────────────────────────────────────


def _make_context_reset_db(schema_path: Path, user_message_count: int = 0) -> str:
    """Create a temp DB with an open session and the given user_message_count."""
    db = make_db(schema_path)
    conn = sqlite3.connect(db)
    for ddl in (
        "ALTER TABLE sessions ADD COLUMN user_message_count INTEGER DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN last_reset_at TIMESTAMP",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        "INSERT INTO sessions (id, started_at, user_message_count) VALUES (?, ?, ?)",
        ("S-crm-test-001", "2026-01-01T00:00:00+00:00", user_message_count),
    )
    conn.commit()
    conn.close()
    return db


class TestContextResetMonitor:
    """Tests for .claude/hooks/context-reset-monitor.sh (Python hook)."""

    HOOK = "context-reset-monitor.sh"

    def test_tenth_message_emits_warning_and_increments(self) -> None:
        """10th user message: count reaches 10, stderr warning emitted, count stored."""
        db = _make_context_reset_db(SCHEMA_PATH, user_message_count=9)
        try:
            code, _out, err = run_hook(
                self.HOOK,
                stdin_payload={"hook_event_name": "UserPromptSubmit"},
                env={"CONTEXT_RESET_AT": "10"},
                db_path=db,
            )
            assert code == 0, f"Hook must exit 0 (advisory). Got {code}. stderr={err}"
            assert "[context-reset]" in err, (
                f"Expected '[context-reset]' warning in stderr at message 10. Got: {err!r}"
            )
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT user_message_count FROM sessions WHERE id='S-crm-test-001'"
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[0] == 10, f"Expected count=10 after increment. Got {row[0]}"
        finally:
            os.unlink(db)

    def test_eleventh_message_no_warning(self) -> None:
        """11th user message: count reaches 11, no warning (only on multiples of 10)."""
        db = _make_context_reset_db(SCHEMA_PATH, user_message_count=10)
        try:
            code, _out, err = run_hook(
                self.HOOK,
                stdin_payload={"hook_event_name": "UserPromptSubmit"},
                env={"CONTEXT_RESET_AT": "10"},
                db_path=db,
            )
            assert code == 0, f"Hook must exit 0. Got {code}. stderr={err}"
            assert "[context-reset]" not in err, (
                f"Did not expect warning at message 11. Got: {err!r}"
            )
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT user_message_count FROM sessions WHERE id='S-crm-test-001'"
            ).fetchone()
            conn.close()
            assert row[0] == 11, f"Expected count=11. Got {row[0]}"
        finally:
            os.unlink(db)

    def test_session_reset_cli_ends_old_starts_new_writes_notepad(self) -> None:
        """session reset: closes old session, opens new one, writes notepad entry."""
        db = _make_context_reset_db(SCHEMA_PATH, user_message_count=10)
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS agent_notepad ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  topic TEXT NOT NULL,"
            "  agent_name TEXT NOT NULL,"
            "  session_id TEXT,"
            "  written_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
            "  note TEXT NOT NULL CHECK (length(note) <= 500),"
            "  note_kind TEXT DEFAULT 'fyi'"
            ");"
        )
        conn.commit()
        conn.close()

        wrapper = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        )
        wrapper.write(
            "import sys, importlib.util, pathlib\n"
            f"spec = importlib.util.spec_from_file_location('log', {str(LOG_PY)!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            # Patch AFTER exec_module; exec_module re-runs module-level code so
            # pre-patching the dict is overwritten.  Post-patch is stable.
            f"mod.DB_PATH = pathlib.Path({db!r})\n"
            "sys.argv = ['log.py', 'session', 'reset',\n"
            "            '--summary', 'test reset summary',\n"
            "            '--handoff-notepad-topic', 'mitigation-b-context-reset']\n"
            "mod.main()\n"
        )
        wrapper.close()
        try:
            result = subprocess.run(
                [sys.executable, wrapper.name],
                capture_output=True,
                text=True,
                timeout=15,
            )
        finally:
            Path(wrapper.name).unlink(missing_ok=True)

        assert result.returncode == 0, f"session reset CLI failed: {result.stderr}"
        out = json.loads(result.stdout)
        assert out["closed_session_id"] == "S-crm-test-001"
        assert out["new_session_id"] != "S-crm-test-001"
        assert out["handoff_topic"] == "mitigation-b-context-reset"

        conn = sqlite3.connect(db)
        old = conn.execute(
            "SELECT ended_at, last_reset_at FROM sessions WHERE id='S-crm-test-001'"
        ).fetchone()
        assert old[0] is not None, "Old session ended_at must be set after reset"
        assert old[1] is not None, "last_reset_at must be set on old session"
        new_open = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL AND id != 'S-crm-test-001'"
        ).fetchone()
        assert new_open is not None, "New open session must exist after reset"
        notepad_rows = conn.execute(
            "SELECT note FROM agent_notepad WHERE topic='mitigation-b-context-reset'"
        ).fetchall()
        conn.close()
        os.unlink(db)
        assert notepad_rows, "Notepad entry must be written on reset with handoff topic"
        assert "S-crm-test-001" in notepad_rows[0][0], (
            "Notepad entry must reference the old session id"
        )


# ─── no-direct-push-to-main.sh ──────────────────────────────────────────────

HOOK_PUSH = HOOKS_DIR / "no-direct-push-to-main.sh"


def _run_push_hook(
    command: str,
    agent_type: str | None = None,
) -> tuple[int, str, str]:
    """Invoke no-direct-push-to-main.sh with a Bash tool_input payload."""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": "S-push-test",
    }
    env = {**os.environ}
    if agent_type is not None:
        env["CLAUDE_AGENT_TYPE"] = agent_type
    else:
        env.pop("CLAUDE_AGENT_TYPE", None)

    result = subprocess.run(
        ["/bin/bash", str(HOOK_PUSH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return result.returncode, result.stdout, result.stderr


class TestNoDirectPushToMain:
    def test_push_to_feature_branch_is_allowed(self) -> None:
        """git push origin feat/foo from a sub-agent must pass through."""
        code, _out, _err = _run_push_hook(
            "git push origin feat/foo",
            agent_type="pipeline",
        )
        assert code == 0, f"Expected ALLOW for feature branch push, got exit {code}"

    def test_push_to_main_from_subagent_is_blocked(self) -> None:
        """git push origin main from a sub-agent must be blocked (exit 2)."""
        code, _out, err = _run_push_hook(
            "git push origin main",
            agent_type="pipeline",
        )
        assert code == 2, f"Expected BLOCK (exit 2) for sub-agent push to main, got {code}"
        assert "BLOCK" in err, f"Expected BLOCK in stderr, got: {err!r}"

    def test_push_to_main_from_nexus_is_allowed(self) -> None:
        """git push origin main from nexus-orchestrator must pass through."""
        code, _out, _err = _run_push_hook(
            "git push origin main",
            agent_type="nexus-orchestrator",
        )
        assert code == 0, f"Expected ALLOW for nexus-orchestrator push to main, got {code}"

    def test_push_to_main_with_bypass_token_is_allowed(self) -> None:
        """A sub-agent command containing the bypass token must pass through."""
        code, _out, _err = _run_push_hook(
            "git push origin main  # BYPASS:USER-APPROVED-PUSH-TO-MAIN",
            agent_type="forge",
        )
        assert code == 0, f"Expected ALLOW when bypass token present, got {code}"

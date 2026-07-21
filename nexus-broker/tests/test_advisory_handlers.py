"""F2-03 tranche-A daemon-resident handlers — `nexus-foundation/plans/
artifacts/event-bus-design.md` §2a, `advisory_handlers.py`.

Covers the 8 migrated consumers' ported behavior (skill.loaded +
session.start's memory-health-check/health-banner/lesson-harvester/
router-health-check/memory-errors-banner/session-task-reconcile/
feedback-harvest-banner), the `compute_advisory` dispatch/fail-open
contract, and one end-to-end `event_bus.handle_event_emit` glue check
proving the `consumer` param actually reaches the ported handler.

Every handler is invoked directly — a fake, minimal `.memory/log.py` stands
in for the real CLI so these tests stay fast and deterministic (no sqlite-vec
/ LM Studio dependency), per tdd-core's real-data-SHAPE rule (the JSON shapes
returned mirror the real `log.py` subcommands' documented output).
"""
from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.daemon import advisory_handlers, event_bus


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".memory" / "files").mkdir(parents=True)
    return proj


def _write_fake_log_py(project_path: Path, body: str) -> Path:
    log_py = project_path / ".memory" / "log.py"
    log_py.write_text("#!/usr/bin/env python3\nimport json, sys\n" + body)
    return log_py


# ── skill.loaded / skill-load-capture ───────────────────────────────────


def test_skill_loaded_empty_skill_id_is_noop(project):
    result = advisory_handlers.handle_skill_loaded(project, {"tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_skill_loaded_records_via_log_py(project):
    captured = project / "captured.json"
    _write_fake_log_py(
        project,
        f"""
argv = sys.argv[1:]
with open({str(captured)!r}, "w") as fh:
    json.dump(argv, fh)
""",
    )
    payload = {
        "tool_input": {"skill": "dispatch", "dispatch_id": "TASK-1"},
        "tool_response": "hello",
    }
    result = advisory_handlers.handle_skill_loaded(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    argv = json.loads(captured.read_text())
    assert argv[:4] == ["skill", "record-load", "--dispatch-id", "TASK-1"]
    assert "--skill-id" in argv and "dispatch" in argv
    assert "--byte-len" in argv and "5" in argv  # len(b"hello")


# ── session.start / memory-health-check ─────────────────────────────────


def test_memory_health_check_unwritable_dir(project, monkeypatch):
    monkeypatch.setattr(advisory_handlers, "_preflight_writable", lambda d: False)
    result = advisory_handlers.handle_memory_health_check(project, {}, {})
    assert result["exit_code"] == 0
    assert "MEMORY UNWRITABLE" in result["stderr"]
    assert "MEMORY UNWRITABLE" in result["stdout"]["hookSpecificOutput"]["additionalContext"]


def test_memory_health_check_missing_venv_and_logpy(project, monkeypatch):
    monkeypatch.setattr(advisory_handlers, "_http_get_json", lambda url, timeout: None)
    result = advisory_handlers.handle_memory_health_check(project, {}, {})
    assert result["exit_code"] == 0
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "memory-health-check" in ctx
    assert "check(s) failed" in ctx
    assert "venv python missing" in result["stderr"]
    assert "log.py not found" in result["stderr"]
    assert "LM Studio unreachable" in result["stderr"]


def test_memory_health_check_all_green_is_quiet(project, monkeypatch):
    monkeypatch.setattr(advisory_handlers, "_preflight_writable", lambda d: True)
    monkeypatch.setattr(
        advisory_handlers,
        "_http_get_json",
        lambda url, timeout: {"data": [{"id": "text-embedding-mxbai-embed-large-v1"}, {"id": "granite-4.1-3b"}]},
    )
    venv_py = project / ".memory" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/usr/bin/env python3\n")
    venv_py.chmod(0o755)
    (project / ".memory" / "project.db").touch()
    _write_fake_log_py(project, "print('[{}]')")

    def _fake_run(cmd, **kwargs):
        if "import sqlite_vec" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "recall" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "[{}]", "")
        return subprocess.CompletedProcess(cmd, 0, "ROWS:5", "")  # the vec_memory probe script

    monkeypatch.setattr(advisory_handlers.subprocess, "run", _fake_run)
    result = advisory_handlers.handle_memory_health_check(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


# ── session.start / health-banner ────────────────────────────────────────


def test_health_banner_install_incomplete(project):
    result = advisory_handlers.handle_health_banner(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "NEXUS INSTALL INCOMPLETE" in ctx


def test_health_banner_healthy_shows_version_only(project):
    (project / ".memory" / ".nexus-version").write_text("1.14.0\n")
    _write_fake_log_py(
        project,
        'print(json.dumps({"summary": {"passes": 5, "warns": 0, "fails": 0}, "results": []}))',
    )
    result = advisory_handlers.handle_health_banner(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert ctx == "Nexus v1.14.0"


def test_health_banner_broken_self_test(project):
    (project / ".memory" / ".nexus-version").write_text("1.14.0\n")
    _write_fake_log_py(project, "import sys; sys.stderr.write('boom'); sys.exit(1)")
    result = advisory_handlers.handle_health_banner(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "Nexus v1.14.0" in ctx
    assert "HEALTH SELF-TEST BROKEN" in ctx
    assert "boom" in ctx


def test_health_banner_unwritable_memory_dir_short_circuits(project, monkeypatch):
    """F2-03 package port (NATIVE-58): the writability preflight must fire
    BEFORE the version/self-test banner and skip it entirely — proves the
    port did not just append the check but actually gates the rest.
    """
    (project / ".memory" / ".nexus-version").write_text("1.14.0\n")
    monkeypatch.setattr(advisory_handlers, "_preflight_writable", lambda d: False)
    result = advisory_handlers.handle_health_banner(project, {}, {})
    assert result["exit_code"] == 0
    assert "MEMORY UNWRITABLE" in result["stderr"]
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "MEMORY UNWRITABLE" in ctx
    assert "Nexus v1.14.0" not in ctx  # short-circuited, never reached the version line


# ── session.start / lesson-harvester ─────────────────────────────────────


def _seed_lesson_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, ended_at TEXT);
        CREATE TABLE decisions (id TEXT PRIMARY KEY, session_id TEXT, title TEXT, rationale TEXT, context TEXT);
        CREATE TABLE lessons (id TEXT PRIMARY KEY, source_decision_id TEXT);
        """
    )
    conn.execute("INSERT INTO sessions VALUES ('S1', '2026-01-01T00:00:00Z')")
    conn.execute(
        "INSERT INTO decisions VALUES ('D1', 'S1', 'Some fix', 'this was a revise after failure', '')"
    )
    conn.commit()
    conn.close()


def test_lesson_harvester_flags_decision_without_lesson(project):
    db_path = project / ".memory" / "project.db"
    _seed_lesson_db(db_path)
    result = advisory_handlers.handle_lesson_harvester(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "1 decision(s)" in ctx
    assert "D1" in ctx


def test_lesson_harvester_no_prior_session_is_noop(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, ended_at TEXT)")
    conn.commit()
    conn.close()
    result = advisory_handlers.handle_lesson_harvester(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


# ── session.start / router-health-check ──────────────────────────────────


def test_router_health_check_unreachable(project, monkeypatch):
    monkeypatch.setattr(advisory_handlers, "_http_get_json", lambda url, timeout: None)
    result = advisory_handlers.handle_router_health_check(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "LM Studio unreachable" in ctx


def test_router_health_check_missing_model(project, monkeypatch):
    monkeypatch.setattr(
        advisory_handlers, "_http_get_json", lambda url, timeout: {"data": [{"id": "granite-4.1-3b"}]}
    )
    result = advisory_handlers.handle_router_health_check(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "missing models" in ctx
    assert "text-embedding-mxbai-embed-large-v1" in ctx


def test_router_health_check_all_present(project, monkeypatch):
    monkeypatch.setattr(
        advisory_handlers,
        "_http_get_json",
        lambda url, timeout: {"data": [{"id": "granite-4.1-3b"}, {"id": "text-embedding-mxbai-embed-large-v1"}]},
    )
    result = advisory_handlers.handle_router_health_check(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_router_health_check_url_derived_from_hook_router_url(project, monkeypatch):
    """F2-03 package port: absent an explicit LM_STUDIO_MODELS_URL override,
    the probe URL must follow router_core.py's actual configured endpoint
    (_HOOK_ROUTER_URL), not a hardcoded localhost default.
    """
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return {"data": [{"id": "granite-4.1-3b"}, {"id": "text-embedding-mxbai-embed-large-v1"}]}

    monkeypatch.setattr(advisory_handlers, "_http_get_json", fake_get)
    env = {"_HOOK_ROUTER_URL": "http://10.0.0.5:9999/v1/chat/completions"}
    result = advisory_handlers.handle_router_health_check(project, {}, env)
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    assert seen["url"] == "http://10.0.0.5:9999/v1/models"


def test_router_health_check_url_falls_back_to_deprecated_qwen_url(project, monkeypatch):
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return {"data": [{"id": "granite-4.1-3b"}, {"id": "text-embedding-mxbai-embed-large-v1"}]}

    monkeypatch.setattr(advisory_handlers, "_http_get_json", fake_get)
    env = {"_HOOK_QWEN_URL": "http://10.0.0.6:1234/v1/chat/completions"}
    advisory_handlers.handle_router_health_check(project, {}, env)
    assert seen["url"] == "http://10.0.0.6:1234/v1/models"


def test_router_health_check_explicit_override_wins_over_derivation(project, monkeypatch):
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return {"data": [{"id": "granite-4.1-3b"}, {"id": "text-embedding-mxbai-embed-large-v1"}]}

    monkeypatch.setattr(advisory_handlers, "_http_get_json", fake_get)
    env = {"LM_STUDIO_MODELS_URL": "http://explicit:1234/v1/models", "_HOOK_ROUTER_URL": "http://ignored:1/x"}
    advisory_handlers.handle_router_health_check(project, {}, env)
    assert seen["url"] == "http://explicit:1234/v1/models"


# ── session.start / memory-errors-banner ─────────────────────────────────


def test_memory_errors_banner_no_log_is_noop(project):
    result = advisory_handlers.handle_memory_errors_banner(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_memory_errors_banner_detects_new_signature(project):
    err_log = project / ".memory" / "files" / "memory-errors.log"
    err_log.write_text("some line\nrecall: nothing found\n")
    result = advisory_handlers.handle_memory_errors_banner(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "1 new memory error(s)" in ctx
    assert "recall: nothing found" in result["stderr"]
    seen = project / ".memory" / "files" / ".memory-errors.seen"
    assert seen.read_text().strip() == str(err_log.stat().st_size)


def test_memory_errors_banner_no_growth_since_last_seen(project):
    err_log = project / ".memory" / "files" / "memory-errors.log"
    err_log.write_text("recall: boom\n")
    (project / ".memory" / "files" / ".memory-errors.seen").write_text(str(err_log.stat().st_size))
    result = advisory_handlers.handle_memory_errors_banner(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


# ── session.start / session-task-reconcile ───────────────────────────────


def test_session_task_reconcile_no_tasks(project):
    _write_fake_log_py(project, 'print(json.dumps({"open_tasks": []}))')
    result = advisory_handlers.handle_session_task_reconcile(project, {}, {})
    assert result["stdout"] is None
    assert "No open tasks" in result["stderr"]


def test_session_task_reconcile_uncapped_lists_in_progress(project):
    _write_fake_log_py(
        project,
        'print(json.dumps({"open_tasks": ['
        '{"id": "TASK-1", "status": "in_progress", "priority": "high", "assigned_to": "atlas", "title": "Do thing"},'
        '{"id": "TASK-2", "status": "todo", "priority": "low"}]}))',
    )
    result = advisory_handlers.handle_session_task_reconcile(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "1 in_progress" in ctx
    assert "TASK-1" in ctx
    assert "TASK-2" not in ctx  # uncapped mode omits the backlog from model context


def test_session_task_reconcile_capped_writes_report(project, tmp_path):
    cap_flag = project / ".claude" / "sessionstart-cap.enabled"
    cap_flag.parent.mkdir(parents=True)
    cap_flag.touch()
    _write_fake_log_py(
        project,
        'print(json.dumps({"open_tasks": ['
        '{"id": "TASK-9", "status": "in_progress", "priority": "high"}]}))',
    )
    result = advisory_handlers.handle_session_task_reconcile(project, {}, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "capped" in ctx
    report = project / ".memory" / "files" / "session-task-reconcile-latest.md"
    assert report.is_file()
    assert "TASK-9" in report.read_text()


# ── session.start / feedback-harvest-banner ──────────────────────────────


def test_feedback_harvest_banner_no_rows_is_noop(project):
    _write_fake_log_py(project, 'print(json.dumps({"feedback_rows": 0}))')
    result = advisory_handlers.handle_feedback_harvest_banner(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_feedback_harvest_banner_rows_present_is_raw_string(project):
    _write_fake_log_py(
        project,
        'print(json.dumps({"feedback_rows": 3, "items": [{"project_path": "/a"}, {"project_path": "/b"}]}))',
    )
    result = advisory_handlers.handle_feedback_harvest_banner(project, {}, {})
    assert isinstance(result["stdout"], str)  # raw print, NOT a hookSpecificOutput envelope
    assert "3 unresolved item(s) from 2 project(s)" in result["stdout"]


# ── session.stop / session-end-reminder ──────────────────────────────────


def test_session_end_reminder_no_open_session_is_noop(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT)")
    conn.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, session_id TEXT)")
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, updated_at TEXT, status TEXT)")
    conn.commit()
    conn.close()
    result = advisory_handlers.handle_session_end_reminder(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_session_end_reminder_unrendered_install_token_is_loud(project):
    """F2-03 package port: an un-rendered __INSTALL_ROOT__ literal must never
    silently resolve into a dead sqlite path — it short-circuits BEFORE any
    sqlite3.connect attempt and returns a systemMessage, never `_noop()`.
    """
    env = {"_HOOK_DB_PATH": "/__INSTALL_ROOT__/.memory/project.db"}
    result = advisory_handlers.handle_session_end_reminder(project, {}, env)
    assert result["exit_code"] == 0
    assert result["stderr"] is None
    msg = result["stdout"]["systemMessage"]
    assert "INSTALL NOT RENDERED" in msg
    assert "__INSTALL_ROOT__" in msg


# ── session.stop / lens-tier-backstop ─────────────────────────────────────


def test_lens_tier_backstop_no_gaps_is_noop(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE validation_log (target_agent TEXT, task_or_brief_hash TEXT, agent_validated TEXT, "
        "verdict TEXT, risk_tier TEXT, lens_type TEXT, validated_at TEXT)"
    )
    conn.commit()
    conn.close()
    result = advisory_handlers.handle_lens_tier_backstop(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_lens_tier_backstop_redesign_mode_short_circuits(project):
    marker = project / ".claude" / "redesign-mode.enabled"
    marker.parent.mkdir(parents=True)
    marker.touch()
    result = advisory_handlers.handle_lens_tier_backstop(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


# ── dispatch.pre.observe / dispatch-announce ──────────────────────────────


def test_dispatch_announce_no_persona_is_noop(project):
    result = advisory_handlers.handle_dispatch_announce(project, {"tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_dispatch_announce_task_shape(project, snapshot: SnapshotAssertion):
    payload = {"tool_input": {"subagent_type": "forge-ui", "description": "  build the   thing  "}}
    result = advisory_handlers.handle_dispatch_announce(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    # agent-render: this string is injected as additionalContext into the
    # NEXT agent turn — a reviewed golden snapshot (F3-04) so wording drift
    # shows as a readable diff instead of a silent inline-string edit.
    assert ctx == snapshot(name="additional_context")
    assert result["stdout"]["hookSpecificOutput"]["hookEventName"] == "PreToolUse"


def test_dispatch_announce_agent_type_fallback(project, snapshot: SnapshotAssertion):
    payload = {"input": {"agent_type": "lens", "prompt": "verify the change"}}
    result = advisory_handlers.handle_dispatch_announce(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert ctx == snapshot(name="additional_context")


def test_dispatch_announce_no_description_uses_placeholder(project, snapshot: SnapshotAssertion):
    payload = {"tool_input": {"subagent_type": "hermes"}}
    result = advisory_handlers.handle_dispatch_announce(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert ctx == snapshot(name="additional_context")


def test_dispatch_announce_truncates_long_goal(project):
    payload = {"tool_input": {"subagent_type": "atlas", "description": "x" * 100}}
    result = advisory_handlers.handle_dispatch_announce(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    goal = ctx.split("goal=", 1)[1]
    assert len(goal) == 80
    assert goal.endswith("…")


# ── prompt.submitted / auto-parallel-nudge ────────────────────────────────


def test_auto_parallel_nudge_missing_prompt_is_noop(project):
    result = advisory_handlers.handle_auto_parallel_nudge(project, {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_auto_parallel_nudge_pure_question_is_quiet(project):
    payload = {"prompt": "What does the build script do?"}
    result = advisory_handlers.handle_auto_parallel_nudge(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_auto_parallel_nudge_short_greeting_is_quiet(project):
    payload = {"prompt": "hey thanks!"}
    result = advisory_handlers.handle_auto_parallel_nudge(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_auto_parallel_nudge_action_verb_prompt_nudges(project):
    payload = {"prompt": "Please implement the new caching layer for the router and add tests for it."}
    result = advisory_handlers.handle_auto_parallel_nudge(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "authoring a Workflow" in ctx
    assert result["stdout"]["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_auto_parallel_nudge_enumerated_list_nudges(project):
    payload = {"prompt": "Here's the plan:\n1. implement a\n2. build b\n3. deploy c"}
    result = advisory_handlers.handle_auto_parallel_nudge(project, payload, {})
    assert "authoring a Workflow" in result["stdout"]["hookSpecificOutput"]["additionalContext"]


def test_auto_parallel_nudge_installed_tenant_omits_dec_citation(project):
    """F2-03 package port: an installed tenant (no
    nexus-foundation/plans/artifacts/event-taxonomy.json) has no DEC-017 to
    look up — the package pre-migration body scrubbed the citation, ported
    here as a real branch rather than merged into one universal text.
    """
    payload = {"prompt": "Please implement the new caching layer for the router and add tests for it."}
    result = advisory_handlers.handle_auto_parallel_nudge(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "DEC-017" not in ctx
    assert ctx.endswith("waste. Advisory only — not blocking.")


def test_auto_parallel_nudge_meta_repo_tenant_keeps_dec_citation(project):
    taxonomy = project / "nexus-foundation" / "plans" / "artifacts" / "event-taxonomy.json"
    taxonomy.parent.mkdir(parents=True)
    taxonomy.write_text("{}")
    payload = {"prompt": "Please implement the new caching layer for the router and add tests for it."}
    result = advisory_handlers.handle_auto_parallel_nudge(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert ctx.endswith("waste (DEC-017). Advisory only — not blocking.")


# ── search.completed / socraticode-flag ──────────────────────────────────


def test_socraticode_flag_results_touches_flag_no_banner(project, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    payload = {
        "session_id": "sc-results",
        "tool_name": "codebase_search",
        "tool_response": {"content": [{"type": "text", "text": "Symbols matching foo (3):\n  - foo (a.py:1)"}]},
    }
    result = advisory_handlers.handle_socraticode_flag(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    assert (tmp_path / "claude-socraticode-sc-results.flag").exists()


def test_socraticode_flag_unindexed_emits_reminder_no_flag(project, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    payload = {
        "session_id": "sc-unindexed",
        "tool_name": "codebase_search",
        "tool_response": {"content": [{"type": "text", "text": "Project not indexed. Please index this project first."}]},
    }
    result = advisory_handlers.handle_socraticode_flag(project, payload, {})
    assert result["stdout"]["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "Do NOT fall back to grep" in result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert not (tmp_path / "claude-socraticode-sc-unindexed.flag").exists()


def test_socraticode_flag_no_results_is_quiet(project, monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    payload = {
        "session_id": "sc-none",
        "tool_name": "codebase_search",
        "tool_response": {"content": [{"type": "text", "text": "No matches"}]},
    }
    result = advisory_handlers.handle_socraticode_flag(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    assert not (tmp_path / "claude-socraticode-sc-none.flag").exists()


# ── task.tool.completed / stall-counter ──────────────────────────────────


def test_stall_counter_no_marker_is_noop(project):
    result = advisory_handlers.handle_stall_counter(project, {"tool_response": "all good"}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_stall_counter_no_task_or_persona_is_noop(project):
    payload = {"tool_response": "## NEXUS:REVISE fix it", "tool_input": {}}
    result = advisory_handlers.handle_stall_counter(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_stall_counter_missing_log_py_errors_loudly(project):
    payload = {
        "tool_response": "## NEXUS:REVISE fix it",
        "tool_input": {"value": '{"subagent_type": "forge-ui", "task_id": "TASK-1"}'},
    }
    result = advisory_handlers.handle_stall_counter(project, payload, {})
    assert "DISABLED" in result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "cannot locate" in result["stderr"]


def test_stall_counter_escalation_block_at_three(project):
    _write_fake_log_py(project, "print(json.dumps({'task_id': 'TASK-1', 'stall_count': 3, 'action': 'incremented'}))")
    payload = {
        "tool_response": "## NEXUS:REVISE fix it",
        "tool_input": {"value": '{"subagent_type": "forge-ui", "task_id": "TASK-1"}'},
    }
    result = advisory_handlers.handle_stall_counter(project, payload, {})
    assert result["stdout"]["decision"] == "block"
    assert result["exit_code"] == 2
    assert "ESCALATION" in result["stdout"]["hookSpecificOutput"]["additionalContext"]


def test_stall_counter_warns_at_two(project):
    _write_fake_log_py(project, "print(json.dumps({'task_id': 'TASK-1', 'stall_count': 2, 'action': 'incremented'}))")
    payload = {
        "tool_response": "## NEXUS:BLOCKED fix it",
        "tool_input": {"value": '{"subagent_type": "forge-ui-pro", "task_id": "TASK-1"}'},
    }
    result = advisory_handlers.handle_stall_counter(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "quill-ts" in ctx  # "forge-ui-pro" has no "py" substring -> defaults to ts
    assert "Use forge-ui-pro variant" in ctx  # -pro suffix stripped from persona, then re-appended
    assert result["exit_code"] == 0


def test_stall_counter_below_threshold_is_quiet(project):
    _write_fake_log_py(project, "print(json.dumps({'task_id': 'TASK-1', 'stall_count': 1, 'action': 'incremented'}))")
    payload = {
        "tool_response": "## NEXUS:REVISE fix it",
        "tool_input": {"value": '{"subagent_type": "forge-ui", "task_id": "TASK-1"}'},
    }
    result = advisory_handlers.handle_stall_counter(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


# ── task.tool.completed / task-mirror ────────────────────────────────────


def test_task_mirror_no_signal_is_noop(project):
    result = advisory_handlers.handle_task_mirror(project, {"tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_task_mirror_dispatch_phase(project):
    payload = {"tool_input": {"subagent_type": "forge-ui", "description": "TASK-42 do the thing"}}
    result = advisory_handlers.handle_task_mirror(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "DISPATCH persona=forge-ui task=TASK-42" in ctx


def test_task_mirror_done_phase(project):
    payload = {
        "tool_input": {"subagent_type": "forge-ui", "description": "TASK-42"},
        "tool_response": "## NEXUS:DONE all good",
    }
    result = advisory_handlers.handle_task_mirror(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "DONE persona=forge-ui task=TASK-42" in ctx
    assert "COMPLETED" in ctx


def test_task_mirror_truncation_advisory(project):
    payload = {
        "tool_input": {"subagent_type": "forge-ui", "description": "TASK-42"},
        "tool_response": "partial output with no marker at all",
    }
    result = advisory_handlers.handle_task_mirror(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "RETURN-NO-MARKER" in ctx


# ── task.record.changed / task-db-mirror ─────────────────────────────────


def test_task_db_mirror_no_id_is_noop(project):
    result = advisory_handlers.handle_task_db_mirror(project, {"tool_name": "SomeTool", "tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_task_db_mirror_refuses_foreign_collision(project):
    db_path = project / ".memory" / "project.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, created_at TEXT, notes TEXT)")
    conn.execute("INSERT INTO tasks (id, title, status, created_at, notes) VALUES ('NATIVE-42', 'x', 'todo', 'now', '')")
    conn.commit()
    conn.close()

    payload = {"tool_name": "TaskUpdate", "tool_input": {"taskId": "42", "status": "in_progress"}}
    result = advisory_handlers.handle_task_db_mirror(project, payload, {})
    assert result["stdout"] is None
    assert result["exit_code"] == 0
    assert "REFUSED (NATIVE-13)" in result["stderr"]


def test_task_db_mirror_missing_log_py_warns(project):
    payload = {"tool_name": "TaskCreate", "tool_input": {"subject": "x"}, "tool_response": "Task #7 created successfully: x"}
    result = advisory_handlers.handle_task_db_mirror(project, payload, {})
    assert result["stdout"] is None
    assert "not found" in result["stderr"]
    assert result["exit_code"] == 0


def test_task_db_mirror_success_emits_context(project, snapshot: SnapshotAssertion):
    _write_fake_log_py(project, "print(json.dumps({'task_id': 'NATIVE-7', 'action': 'created'}))")
    payload = {"tool_name": "TaskCreate", "tool_input": {"subject": "x"}, "tool_response": "Task #7 created successfully: x"}
    result = advisory_handlers.handle_task_db_mirror(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    # agent-render: reviewed golden snapshot of the mirrored-task context string.
    assert ctx == snapshot(name="additional_context")
    assert result["stdout"]["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert result["exit_code"] == 0


def test_task_db_mirror_log_py_failure_is_quiet_warning(project):
    _write_fake_log_py(project, "import sys\nprint('boom', file=sys.stderr)\nsys.exit(1)")
    payload = {"tool_name": "TaskCreate", "tool_input": {"subject": "x"}, "tool_response": "Task #7 created successfully: x"}
    result = advisory_handlers.handle_task_db_mirror(project, payload, {})
    assert result["stdout"] is None
    assert "WARNING: mirror of native #7 failed" in result["stderr"]
    err_log = project / ".memory" / "files" / "memory-errors.log"
    assert "boom" in err_log.read_text()


# ── write.post.observe / post-sync-code-knowledge ────────────────────────


def test_post_sync_code_knowledge_no_file_path_is_noop(project):
    result = advisory_handlers.handle_post_sync_code_knowledge(project, {"tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_post_sync_code_knowledge_non_canonical_path_is_noop(project):
    payload = {"tool_input": {"file_path": "src/foo.py"}}
    result = advisory_handlers.handle_post_sync_code_knowledge(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_post_sync_code_knowledge_outside_root_is_noop(project, tmp_path):
    outside = tmp_path / "elsewhere" / "CLAUDE.md"
    payload = {"tool_input": {"file_path": str(outside)}}
    result = advisory_handlers.handle_post_sync_code_knowledge(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_post_sync_code_knowledge_canonical_glob_runs_sync(project):
    (project / "bin").mkdir()
    (project / "bin" / "sync-code-knowledge.py").write_text("print('synced ok')")
    payload = {"tool_input": {"file_path": ".claude/agents/pipeline-async.md"}}
    result = advisory_handlers.handle_post_sync_code_knowledge(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_post_sync_code_knowledge_sync_failure_warns(project):
    (project / "bin").mkdir()
    (project / "bin" / "sync-code-knowledge.py").write_text("import sys; print('boom'); sys.exit(1)")
    payload = {"tool_input": {"file_path": "CLAUDE.md"}}
    result = advisory_handlers.handle_post_sync_code_knowledge(project, payload, {})
    assert result["stdout"] is None
    assert result["exit_code"] == 0
    assert "sync failed" in result["stderr"]
    assert "boom" in result["stderr"]


def test_post_sync_code_knowledge_jq_or_empty_string_wins(project):
    """jq `//` precedence: an empty-string file_path from tool_input still
    wins over a present tool_response.filePath (parity with the
    read-injection-scanner jq-chain quirk) — here it resolves to "" which is
    falsy in Python's own `if not file_path`, so the net effect is still a
    noop, but via the CORRECT branch (empty tool_input.file_path never falls
    through to tool_response.filePath).
    """
    payload = {"tool_input": {"file_path": ""}, "tool_response": {"filePath": "CLAUDE.md"}}
    result = advisory_handlers.handle_post_sync_code_knowledge(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


# ── write.post.observe / reflection-capture ───────────────────────────────


def test_reflection_capture_no_file_path_is_noop(project):
    result = advisory_handlers.handle_reflection_capture(project, {"tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_reflection_capture_non_watched_path_is_noop(project):
    payload = {"tool_input": {"file_path": "src/foo.py", "old_string": "a", "new_string": "b" * 10}}
    result = advisory_handlers.handle_reflection_capture(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_reflection_capture_small_diff_is_noop(project):
    payload = {
        "tool_input": {
            "file_path": "docs/features/FEAT-1.md",
            "old_string": "one\ntwo",
            "new_string": "one\nTWO",
        }
    }
    result = advisory_handlers.handle_reflection_capture(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    assert not (project / ".memory" / "files" / "reflection_snapshot.jsonl").exists()


def test_reflection_capture_large_diff_records_row(project):
    old = "\n".join(f"old{i}" for i in range(6))
    new = "\n".join(f"new{i}" for i in range(6))
    payload = {
        "tool_input": {"file_path": "docs/CONSTITUTION.md", "old_string": old, "new_string": new},
        "session_id": "sess-1",
    }
    result = advisory_handlers.handle_reflection_capture(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    journal = project / ".memory" / "files" / "reflection_snapshot.jsonl"
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "sess-1"
    assert row["file_path"] == "docs/CONSTITUTION.md"
    assert row["action_type"] == "constitution_amend"
    assert row["one_line_summary"]
    assert row["captured_at"]


def test_reflection_capture_journal_write_error_is_reported(project):
    # A FILE at .memory/files (not a dir) makes mkdir/open fail with OSError.
    import shutil

    shutil.rmtree(project / ".memory" / "files")
    (project / ".memory" / "files").write_text("not a directory")
    old = "\n".join(f"old{i}" for i in range(6))
    new = "\n".join(f"new{i}" for i in range(6))
    payload = {"tool_input": {"file_path": "docs/DECISIONS.md", "old_string": old, "new_string": new}}
    result = advisory_handlers.handle_reflection_capture(project, payload, {})
    assert result["stdout"] is None
    assert result["exit_code"] == 0
    assert "journal write error" in result["stderr"]


def test_reflection_summarize_diff_is_multiset_not_naive_set():
    """A naive `x not in set(other)` diff misses a REPEATED line's true
    delta (dropping one of three 'x' lines still shows 'x' as unchanged,
    since 'x' remains present in both sets). The Counter-based multiset
    diff (meta-repo tenant) correctly counts it as 1 removed line — the
    installed-package tenant's own naive diff genuinely does NOT (0 changes,
    the real, ported divergence, not a bug in this port)."""
    old_content = "x\nx\nx\nkeep"
    new_content = "x\nx\nkeep"
    summary, changed_count = advisory_handlers._reflection_summarize_diff(old_content, new_content)
    assert changed_count == 1
    assert "removed" in summary

    naive_summary, naive_count = advisory_handlers._reflection_summarize_diff_naive(old_content, new_content)
    assert naive_count == 0
    assert naive_summary == "no significant changes"


def test_reflection_capture_installed_tenant_uses_naive_diff(project):
    """handle_reflection_capture routes to the naive diff for a non-meta
    (installed) project — the repeated-'x'-line case that the multiset diff
    would count as changed stays BELOW MIN_LINE_DIFF here, so no row is
    recorded at all (the real, tenant-divergent outcome)."""
    payload = {
        "tool_input": {
            "file_path": "docs/CONSTITUTION.md",
            "old_string": "x\nx\nx\nkeep",
            "new_string": "x\nx\nkeep",
        }
    }
    result = advisory_handlers.handle_reflection_capture(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}
    assert not (project / ".memory" / "files" / "reflection_snapshot.jsonl").exists()


def test_reflection_classify_action_variants():
    assert advisory_handlers._reflection_classify_action("docs/CONSTITUTION.md") == "constitution_amend"
    assert advisory_handlers._reflection_classify_action("docs/DECISIONS.md") == "decision_amend"
    assert advisory_handlers._reflection_classify_action("docs/features/FEAT-1.md") == "spec_update"
    assert advisory_handlers._reflection_classify_action("README.md") == "other"


# ── write.post.observe / verify-after-edit (PostToolUse path only) ───────


def _write_meta_marker(project: Path) -> None:
    marker = project / "nexus-foundation" / "plans" / "artifacts" / "event-taxonomy.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("{}")


def test_verify_after_edit_no_file_path_is_noop(project):
    result = advisory_handlers.handle_verify_after_edit(project, {"tool_input": {}}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_verify_after_edit_outside_project_root_is_noop(project, tmp_path):
    outside = tmp_path / "elsewhere" / "foo.py"
    payload = {"tool_input": {"file_path": str(outside)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_verify_after_edit_nonexistent_file_is_noop(project):
    payload = {"tool_input": {"file_path": "does/not/exist.py"}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_verify_after_edit_venv_path_always_skipped(project):
    _write_meta_marker(project)
    venv_file = project / ".memory" / ".venv" / "lib" / "broken.py"
    venv_file.parent.mkdir(parents=True)
    venv_file.write_text("def broken(:\n")
    payload = {"tool_input": {"file_path": str(venv_file)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_verify_after_edit_meta_py_compile_error_is_reported(project):
    _write_meta_marker(project)
    bad = project / "src" / "broken.py"
    bad.parent.mkdir(parents=True)
    bad.write_text("def broken(:\n")
    payload = {"tool_input": {"file_path": str(bad)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result["exit_code"] == 0
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "py_compile" in ctx
    assert str(bad) in ctx


def test_verify_after_edit_meta_ruff_finding_no_py_compile_prefix(project):
    _write_meta_marker(project)
    lint_bad = project / "src" / "lint_bad.py"
    lint_bad.parent.mkdir(parents=True)
    # Valid syntax, but an unused import ruff flags (F401).
    lint_bad.write_text("import os\n")
    payload = {"tool_input": {"file_path": str(lint_bad)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "uv run ruff check" in ctx
    assert "py_compile" not in ctx


def test_verify_after_edit_meta_sh_bash_syntax_error(project):
    _write_meta_marker(project)
    bad_sh = project / "src" / "broken.sh"
    bad_sh.parent.mkdir(parents=True)
    bad_sh.write_text('#!/bin/bash\necho "unterminated\n')
    payload = {"tool_input": {"file_path": str(bad_sh)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "bash -n" in ctx


def test_verify_after_edit_meta_sh_python_shebang_uses_py_compile(project):
    _write_meta_marker(project)
    bad_sh = project / "src" / "broken_py.sh"
    bad_sh.parent.mkdir(parents=True)
    bad_sh.write_text("#!/usr/bin/env python3\ndef broken(:\n")
    payload = {"tool_input": {"file_path": str(bad_sh)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "python3 -m py_compile" in ctx


def test_verify_after_edit_meta_hooks_exception_is_checked(project):
    """Meta tenant: .claude/hooks/*.py is a carve-out — DO check, unlike the
    rest of .claude/ which is skipped."""
    _write_meta_marker(project)
    hook_file = project / ".claude" / "hooks" / "broken_hook.py"
    hook_file.parent.mkdir(parents=True)
    hook_file.write_text("def broken(:\n")
    payload = {"tool_input": {"file_path": str(hook_file)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result["stdout"] is not None
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "py_compile" in ctx


def test_verify_after_edit_meta_other_claude_path_is_skipped(project):
    _write_meta_marker(project)
    skill_file = project / ".claude" / "skills" / "broken.py"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("def broken(:\n")
    payload = {"tool_input": {"file_path": str(skill_file)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_verify_after_edit_installed_hooks_path_is_skipped(project):
    """Installed tenant: NO carve-out for .claude/hooks/* — the package body
    skips ALL of .claude/ uniformly (a real, not cosmetic, divergence)."""
    hook_file = project / ".claude" / "hooks" / "broken_hook.py"
    hook_file.parent.mkdir(parents=True)
    hook_file.write_text("def broken(:\n")
    payload = {"tool_input": {"file_path": str(hook_file)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_verify_after_edit_installed_py_runs_ruff_only(project):
    """Installed tenant: only ruff runs for .py — no py_compile step, even
    on a file with a genuine syntax error (py_compile would have caught it;
    ruff's own parse failure surfaces instead, but never a 'py_compile' line)."""
    lint_bad = project / "src" / "lint_bad.py"
    lint_bad.parent.mkdir(parents=True)
    lint_bad.write_text("import os\n")
    payload = {"tool_input": {"file_path": str(lint_bad)}}
    result = advisory_handlers.handle_verify_after_edit(project, payload, {})
    ctx = result["stdout"]["hookSpecificOutput"]["additionalContext"]
    assert "uv run ruff check" in ctx
    assert "py_compile" not in ctx


def test_verify_after_edit_installed_uses_stack_json_py_check_dir(project):
    """Installed tenant reads .memory/nexus-stack.json for the PY_CHECK_DIR
    equivalent — assert the resolved cwd is actually used (fall back to
    ingestion_dir when backend.py_check_dir is empty, mirroring
    PY_CHECK_DIR="${PY_CHECK_DIR:-$INGESTION_DIR}")."""
    stack_json = project / ".memory" / "nexus-stack.json"
    (project / "ingestion").mkdir()
    stack_json.write_text(
        json.dumps({"frontend": {}, "backend": {"py_check_dir": ""}, "data": {"ingestion_dir": "ingestion"}})
    )
    dirs = advisory_handlers._vae_stack_dirs(project)
    assert dirs["py_check_dir"] == "ingestion"


def test_verify_after_edit_missing_stack_json_falls_back_to_empty(project):
    dirs = advisory_handlers._vae_stack_dirs(project)
    assert dirs == {"ts_check_dir": "", "py_check_dir": ""}


# ── compute_advisory dispatch ────────────────────────────────────────────


def test_compute_advisory_unknown_consumer_is_noop(project):
    result = advisory_handlers.compute_advisory(project, "not-a-real-consumer", {}, {})
    assert result == {"stdout": None, "stderr": None, "exit_code": 0}


def test_all_tranche_a_scenarios_are_registered_in_handlers():
    """Regression guard for a real bug caught in F2-03: a handler function can
    be fully written and unit-tested directly, yet never actually reachable
    via `event.emit` if its `_HANDLERS` dict entry is missing — `compute_advisory`
    then silently degrades to `_noop()` for that consumer, which can produce a
    FALSE-GREEN `hook_parity.sh` PASS if the old body also happens to be quiet
    for unrelated reasons. Every consumer this module defines a `handle_*` for
    must be reachable through `_HANDLERS`.
    """
    import inspect

    handler_funcs = {
        name: obj
        for name, obj in inspect.getmembers(advisory_handlers, inspect.isfunction)
        if name.startswith("handle_") and obj.__module__ == advisory_handlers.__name__
    }
    registered = set(advisory_handlers._HANDLERS.values())
    unregistered = {name: fn for name, fn in handler_funcs.items() if fn not in registered}
    assert not unregistered, f"handle_* functions missing from _HANDLERS: {sorted(unregistered)}"


def test_compute_advisory_handler_exception_is_caught(project, monkeypatch):
    def _boom(project_path, payload, env):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(advisory_handlers._HANDLERS, "skill-load-capture", _boom)
    result = advisory_handlers.compute_advisory(project, "skill-load-capture", {}, {})
    assert result["exit_code"] == 0
    assert "kaboom" in result["stderr"]


# ── end-to-end: event_bus.handle_event_emit routes to the ported handler ──


def test_event_emit_routes_consumer_to_advisory_handler(project, monkeypatch):
    taxonomy_path = project / "nexus-foundation" / "plans" / "artifacts" / "event-taxonomy.json"
    taxonomy_path.parent.mkdir(parents=True)
    taxonomy_path.write_text(
        json.dumps(
            {
                "fail_policy_classes": {"advisory-fail-open": "..."},
                "events": [
                    {
                        "name": "skill.loaded",
                        "tranche": "A",
                        "fail_policy": "advisory-fail-open",
                        "payload_sketch": {},
                        "producing_hook_events": [],
                        "consumers": ["skill-load-capture"],
                    }
                ],
            }
        )
    )
    state = event_bus.EventBusState(project)
    captured = project / "captured.json"
    _write_fake_log_py(
        project,
        f"""
with open({str(captured)!r}, "w") as fh:
    json.dump(sys.argv[1:], fh)
""",
    )
    result = event_bus.handle_event_emit(
        state,
        {
            "name": "skill.loaded",
            "consumer": "skill-load-capture",
            "payload": {"tool_input": {"skill": "my-skill"}},
            "env": {},
        },
    )
    assert result["ok"] is True
    assert result["advisory_context"] == {"stdout": None, "stderr": None, "exit_code": 0}
    argv = json.loads(captured.read_text())
    assert "my-skill" in argv

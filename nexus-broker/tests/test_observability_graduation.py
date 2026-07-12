"""Tests for R5-T06 observability graduation (N58, plans/15-r5-dag.yaml).

Covers exactly N58's acceptance criteria:
  1. the obs report renders plan-gate accuracy + cost panels from REAL DB
     rows (fixture DBs shaped byte-for-byte like the real `validation_log`/
     `dispatch_telemetry`/`skill_load_events` tables — never a hand-rolled
     schema);
  2. `broker.daemon.{bus,skill_load_recorder,tracing}` are wired as real
     inputs via `LiveFeed` — driven through their REAL public APIs, never a
     canned fixture dict standing in for the panel;
  3. the R1-T06 B4 eval suite is graduated to a repeatable job with a
     recorded run artifact (run id cited) — run against the REAL
     `research/scripts/b4_eval.py` CLI and the REAL corpus, twice, proving
     re-runnability;
  4. `.memory/health.py::check_observability_report` renders the report via
     the same subprocess-not-import convention `check_broker_mcp_boots`
     already uses.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from broker.observability import cost, eval_job, live_feed, metrics, report

# router_cost_summary windows on "now" (ROUTER_WINDOW_HOURS) — a hardcoded
# calendar timestamp ages out of that window as real time passes (it did:
# "2026-07-10T05:00:00" was already >24h stale the day after this file was
# authored). Compute "recent" relative to the actual test run instead.
_RECENT_TS = datetime.now(UTC).replace(microsecond=0).isoformat()

BROKER_ROOT = Path(__file__).resolve().parent.parent  # nexus-broker/
REPO_ROOT = BROKER_ROOT.parent  # nexus-installer/ (live) or nexus-package/ (package)

# `research/` is a live-meta-repo-only workspace — deliberately NOT shipped
# into nexus-package/ (see nexus-redesign/README.md's ship-boundary note for
# the same pattern applied to nexus-redesign/). The two B4 eval-job tests
# below subprocess-invoke the REAL `research/scripts/b4_eval.py` CLI against
# the REAL corpus, so they can only run where that tree actually exists.
B4_SCRIPT_PRESENT = (REPO_ROOT / eval_job.B4_SCRIPT_REL).is_file()
_B4_SKIP_REASON = (
    f"{eval_job.B4_SCRIPT_REL} not present under {REPO_ROOT} — research/ is a "
    "live-meta-repo-only workspace, deliberately not shipped into nexus-package/"
)

# `.mcp.json` ships PACKAGE-side as a template carrying the literal
# `__INSTALL_ROOT__` placeholder that `install.sh` substitutes at install time
# (see health.py::check_observability_report's own docstring). Run from the
# as-shipped nexus-package/ tree itself, REPO_ROOT's `.mcp.json` is that very
# unsubstituted template — a state a real installed target never has — so
# check_observability_report correctly (and intentionally) short-circuits to a
# single "not yet configured" INFO row instead of the full per-panel report.
# The live meta-repo's own `.mcp.json` is already substituted (it IS an
# installed target), so this only ever skips in the package tree.
_MCP_JSON = REPO_ROOT / ".mcp.json"
MCP_JSON_UNSUBSTITUTED = (
    _MCP_JSON.is_file() and "__INSTALL_ROOT__" in _MCP_JSON.read_text(encoding="utf-8")
)
_MCP_JSON_SKIP_REASON = (
    f"{_MCP_JSON} still carries the __INSTALL_ROOT__ placeholder — true only for the "
    "as-shipped package template, never a real installed target"
)


# ---------------------------------------------------------------------------
# Fixture DB — exact real column shapes (PRAGMA-confirmed against the live
# `.memory/schema.sql`-migrated project.db, 2026-07-10), matching the
# `test_daemon_skill_load_recorder.py` convention of mirroring the real
# table byte-for-byte rather than a hand-rolled shape.
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE validation_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT,
    agent_validated     TEXT NOT NULL,
    target_agent        TEXT NOT NULL,
    task_or_brief_hash  TEXT NOT NULL,
    verdict             TEXT NOT NULL,
    evidence_summary    TEXT,
    validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    files_changed_json  TEXT,
    revise_reason       TEXT,
    dispatch_started_at TEXT,
    lens_type           TEXT,
    risk_tier           TEXT
);

CREATE TABLE dispatch_telemetry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT,
    dispatch_id   TEXT,
    persona       TEXT NOT NULL,
    model         TEXT,
    task_id       TEXT,
    marker        TEXT,
    tokens        INTEGER,
    token_source  TEXT NOT NULL DEFAULT 'exact',
    tool_uses     INTEGER,
    duration_ms   INTEGER,
    run_context   TEXT DEFAULT 'local',
    recorded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE skill_load_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_id TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    byte_len    INTEGER,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_fixture_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def fixture_db(tmp_path) -> Path:
    db_path = tmp_path / "project.db"
    _make_fixture_db(db_path)
    return db_path


@pytest.fixture()
def fixture_conn(fixture_db):
    conn = sqlite3.connect(fixture_db)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _insert_validation_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for row in rows:
        conn.execute(
            """INSERT INTO validation_log
               (target_agent, task_or_brief_hash, agent_validated, verdict,
                revise_reason, validated_at, dispatch_started_at)
               VALUES (?, ?, 'lens', ?, ?, ?, ?)""",
            (
                row["target_agent"],
                row["task_or_brief_hash"],
                row["verdict"],
                row.get("revise_reason"),
                row["validated_at"],
                row.get("dispatch_started_at"),
            ),
        )
    conn.commit()


def _insert_dispatch_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    for row in rows:
        conn.execute(
            "INSERT INTO dispatch_telemetry (persona, model, tokens) VALUES (?, ?, ?)",
            (row["persona"], row.get("model"), row.get("tokens")),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# metrics.py — plan-gate accuracy panel (W0 metrics #1, #3, #4, #5)
# ---------------------------------------------------------------------------


class TestPlanGateMetrics:
    def test_accuracy_and_lens_fail_rate_over_real_rows(self, fixture_conn) -> None:
        _insert_validation_rows(
            fixture_conn,
            [
                {"target_agent": "quill-py", "task_or_brief_hash": "h1", "verdict": "PASS",
                 "revise_reason": None, "validated_at": "2026-07-10T00:00:00+00:00"},
                {"target_agent": "quill-py", "task_or_brief_hash": "h2", "verdict": "FAIL",
                 "revise_reason": "bad diff", "validated_at": "2026-07-10T00:01:00+00:00"},
                {"target_agent": "pipeline-data", "task_or_brief_hash": "h3", "verdict": "PASS",
                 "revise_reason": None, "validated_at": "2026-07-10T00:02:00+00:00"},
                {"target_agent": "pipeline-data", "task_or_brief_hash": "h4", "verdict": "PARTIAL",
                 "revise_reason": "missed edge case", "validated_at": "2026-07-10T00:03:00+00:00"},
            ],
        )

        accuracy = metrics.plan_gate_accuracy(fixture_conn)
        assert accuracy == {
            "available": True, "window": 4, "revise_count": 2,
            "reject_rate": 0.5, "accuracy": 0.5,
        }

        fail_rate = metrics.lens_fail_rate(fixture_conn)
        assert fail_rate == {
            "available": True, "window": 4, "fail_count": 1, "fail_rate": 0.25,
        }

    def test_revise_loop_summary_counts_group_minus_one(self, fixture_conn) -> None:
        # h1 has 3 rows under the same (target_agent, hash) group -> 2 loops.
        # h2 has 1 row -> 0 loops (never negative).
        _insert_validation_rows(
            fixture_conn,
            [
                {"target_agent": "quill-py", "task_or_brief_hash": "h1", "verdict": "FAIL",
                 "revise_reason": "r1", "validated_at": "2026-07-10T00:00:00+00:00"},
                {"target_agent": "quill-py", "task_or_brief_hash": "h1", "verdict": "FAIL",
                 "revise_reason": "r2", "validated_at": "2026-07-10T00:01:00+00:00"},
                {"target_agent": "quill-py", "task_or_brief_hash": "h1", "verdict": "PASS",
                 "revise_reason": None, "validated_at": "2026-07-10T00:02:00+00:00"},
                {"target_agent": "quill-py", "task_or_brief_hash": "h2", "verdict": "PASS",
                 "revise_reason": None, "validated_at": "2026-07-10T00:03:00+00:00"},
            ],
        )

        summary = metrics.revise_loop_summary(fixture_conn)
        assert summary["available"] is True
        assert summary["groups"] == 2
        assert summary["revise_loop_max"] == 2
        assert summary["revise_loop_mean"] == pytest.approx(1.0)

    def test_dispatch_latency_p50_p90_over_real_timestamps(self, fixture_conn) -> None:
        rows = [
            {"target_agent": "pipeline-data", "task_or_brief_hash": f"h{i}", "verdict": "PASS",
             "validated_at": f"2026-07-10T00:00:{10 + i}+00:00",
             "dispatch_started_at": "2026-07-10T00:00:00+00:00"}
            for i in range(10)
        ]
        _insert_validation_rows(fixture_conn, rows)

        latency = metrics.dispatch_latency(fixture_conn)
        assert latency["available"] is True
        assert latency["n"] == 10
        assert latency["p50_s"] > 0
        assert latency["p90_s"] >= latency["p50_s"]

    def test_dispatch_latency_unavailable_when_no_start_timestamps(self, fixture_conn) -> None:
        _insert_validation_rows(
            fixture_conn,
            [{"target_agent": "pipeline-data", "task_or_brief_hash": "h1", "verdict": "PASS",
              "validated_at": "2026-07-10T00:00:00+00:00", "dispatch_started_at": None}],
        )
        latency = metrics.dispatch_latency(fixture_conn)
        assert latency == {"available": False, "reason": "no rows carry both timestamps"}

    def test_empty_table_degrades_gracefully_never_raises(self, fixture_conn) -> None:
        for fn in (
            metrics.plan_gate_accuracy, metrics.lens_fail_rate,
            metrics.revise_loop_summary, metrics.dispatch_latency,
        ):
            result = fn(fixture_conn)
            assert result["available"] is False
            assert "reason" in result

    def test_missing_table_degrades_gracefully(self, tmp_path) -> None:
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(empty_db)
        conn.row_factory = sqlite3.Row
        try:
            result = metrics.plan_gate_report(conn)
            for panel in result.values():
                assert panel["available"] is False
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# cost.py — dispatch + router cost panels (W0 metric #2, derived-$ view)
# ---------------------------------------------------------------------------


class TestCostPanel:
    def test_dispatch_cost_summary_prices_known_models(self, fixture_conn) -> None:
        _insert_dispatch_rows(
            fixture_conn,
            [
                {"persona": "pipeline-data", "model": "claude-sonnet-4-5", "tokens": 1000},
                {"persona": "quill-py", "model": "claude-haiku-4-5", "tokens": 2000},
                {"persona": "scout", "model": "unknown-model", "tokens": 500},
            ],
        )
        summary = cost.dispatch_cost_summary(fixture_conn)
        assert summary["available"] is True
        assert summary["window"] == 3
        assert summary["total_tokens"] == 3500
        assert summary["priced_dispatches"] == 2  # sonnet + haiku priced, unknown-model not
        assert summary["estimated_cost_usd"] == pytest.approx(
            (1000 / 1000) * cost.MODEL_RATES_PER_1K["sonnet"]
            + (2000 / 1000) * cost.MODEL_RATES_PER_1K["haiku"]
        )

    def test_dispatch_cost_summary_empty_table(self, fixture_conn) -> None:
        assert cost.dispatch_cost_summary(fixture_conn) == {
            "available": False, "reason": "dispatch_telemetry is empty",
        }

    def test_router_cost_summary_filters_to_window_and_sums_tokens(self, tmp_path) -> None:
        router_path = tmp_path / "router_decisions.jsonl"
        recent = {"timestamp": _RECENT_TS, "input_tokens": 800, "output_tokens": 30}
        stale = {"timestamp": "2020-01-01T00:00:00+00:00", "input_tokens": 999, "output_tokens": 999}
        router_path.write_text(
            json.dumps(recent) + "\n" + json.dumps(stale) + "\n" + "not-json\n",
            encoding="utf-8",
        )

        # Freeze "now" implicitly by asserting the stale 2020 entry never counts
        # regardless of when the test runs (window is 24h in the past).
        summary = cost.router_cost_summary(router_path, hours=24)
        assert summary["available"] is True
        assert summary["decisions"] == 1
        assert summary["input_tokens"] == 800
        assert summary["output_tokens"] == 30

    def test_router_cost_summary_missing_file(self, tmp_path) -> None:
        summary = cost.router_cost_summary(tmp_path / "does-not-exist.jsonl")
        assert summary == {"available": False, "reason": "router_decisions.jsonl not found"}


# ---------------------------------------------------------------------------
# live_feed.py — real (non-fixture) bus/skill_load_recorder/tracing wiring
# ---------------------------------------------------------------------------


class TestLiveFeed:
    def test_record_dispatch_wires_bus_and_tracing_together(self) -> None:
        feed = live_feed.LiveFeed(subscriber_id="test-sub")

        trace_id = feed.record_dispatch(
            dispatch_id="d-1", persona="pipeline-data",
            skills=("agent-protocol", "deployable-engineering"),
        )

        bus_stats = feed.bus_panel()
        # dispatch_started + 2x skill_load_observed + dispatch_completed = 4
        assert bus_stats["published_total"] == 4
        assert bus_stats["dropped_total"] == 0

        tracing_stats = feed.tracing_panel()
        assert len(tracing_stats["traces"]) == 1
        trace_summary = tracing_stats["traces"][0]
        assert trace_summary["trace_id"] == trace_id
        assert trace_summary["span_count"] == 4
        assert trace_summary["kinds"] == [
            "dispatch_started", "skill_load_observed", "skill_load_observed", "dispatch_completed",
        ]
        assert tracing_stats["journal_stats"]["trace_count"] == 1
        assert tracing_stats["journal_stats"]["event_count"] == 4

    def test_record_dispatch_reuses_supplied_trace_id(self) -> None:
        feed = live_feed.LiveFeed(subscriber_id="test-sub-2")
        resolved = feed.record_dispatch(
            dispatch_id="d-2", persona="scout", trace_id="trace-explicit-123",
        )
        assert resolved == "trace-explicit-123"

    def test_multiple_dispatches_produce_multiple_traces(self) -> None:
        feed = live_feed.LiveFeed(subscriber_id="test-sub-3")
        t1 = feed.record_dispatch(dispatch_id="d-a", persona="scout")
        t2 = feed.record_dispatch(dispatch_id="d-b", persona="lens")
        assert t1 != t2
        traces = feed.tracing_panel()["traces"]
        assert {t["trace_id"] for t in traces} == {t1, t2}

    def test_flush_skill_events_lands_real_rows_then_skills_panel_reads_them_back(
        self, fixture_db
    ) -> None:
        feed = live_feed.LiveFeed(subscriber_id="test-sub-4")
        feed.record_dispatch(
            dispatch_id="d-100", persona="pipeline-data",
            skills=("agent-protocol", "deployable-engineering", "agent-protocol"),
        )

        flushed = feed.flush_skill_events(fixture_db)
        assert flushed == 3

        panel = live_feed.skills_panel(fixture_db)
        assert panel["available"] is True
        by_skill = {row["skill_id"]: row["count"] for row in panel["skills"]}
        assert by_skill == {"agent-protocol": 2, "deployable-engineering": 1}

    def test_skills_panel_missing_db_degrades_gracefully(self, tmp_path) -> None:
        panel = live_feed.skills_panel(tmp_path / "nope.db")
        assert panel == {"available": False, "reason": "db not found"}


# ---------------------------------------------------------------------------
# eval_job.py — R1-T06 B4 suite graduated to a repeatable, recorded job
# ---------------------------------------------------------------------------


class TestEvalJob:
    @pytest.mark.skipif(not B4_SCRIPT_PRESENT, reason=_B4_SKIP_REASON)
    def test_run_eval_job_against_real_b4_script_and_corpus(self, tmp_path) -> None:
        runs_path = tmp_path / "eval_runs.jsonl"

        record = eval_job.run_eval_job(split="dev", top_k=5, runs_path=runs_path)

        assert record["run_id"].startswith("b4-run-")
        assert record["split"] == "dev"
        assert isinstance(record["recall_at_k_overall"], float)
        assert 0.0 <= record["recall_at_k_overall"] <= 1.0
        assert record["eval_result"]["schema"] == "b4-eval-run/1"

        # The job must own its OWN ledger, never mutate the out-of-write-scope
        # research/_meta/eval-history.jsonl as a side effect.
        history_path = REPO_ROOT / "research" / "_meta" / "eval-history.jsonl"
        before = history_path.read_text(encoding="utf-8") if history_path.is_file() else ""
        eval_job.run_eval_job(split="dev", top_k=5, runs_path=runs_path)
        after = history_path.read_text(encoding="utf-8") if history_path.is_file() else ""
        assert before == after

    @pytest.mark.skipif(not B4_SCRIPT_PRESENT, reason=_B4_SKIP_REASON)
    def test_run_eval_job_is_re_runnable_with_a_growing_recorded_ledger(self, tmp_path) -> None:
        runs_path = tmp_path / "eval_runs.jsonl"

        first = eval_job.run_eval_job(split="dev", runs_path=runs_path)
        second = eval_job.run_eval_job(split="dev", runs_path=runs_path)

        assert first["run_id"] != second["run_id"]

        recorded = eval_job.read_runs(runs_path)
        assert len(recorded) == 2
        assert {r["run_id"] for r in recorded} == {first["run_id"], second["run_id"]}

    def test_read_runs_on_absent_ledger_returns_empty_list(self, tmp_path) -> None:
        assert eval_job.read_runs(tmp_path / "never-written.jsonl") == []

    def test_run_eval_job_missing_script_raises(self, tmp_path) -> None:
        fake_root = tmp_path / "fake-repo"
        fake_root.mkdir()
        with pytest.raises(FileNotFoundError):
            eval_job.run_eval_job(split="dev", repo_root=fake_root)


# ---------------------------------------------------------------------------
# report.py — the composed obs report (N58 acceptance criterion #1)
# ---------------------------------------------------------------------------


def _make_fake_project(tmp_path: Path) -> Path:
    project_root = tmp_path / "fake-project"
    memory_dir = project_root / ".memory"
    files_dir = memory_dir / "files"
    files_dir.mkdir(parents=True)

    db_path = memory_dir / "project.db"
    _make_fixture_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        _insert_validation_rows(
            conn,
            [
                {"target_agent": "pipeline-data", "task_or_brief_hash": "h1", "verdict": "PASS",
                 "revise_reason": None, "validated_at": "2026-07-10T00:00:00+00:00"},
                {"target_agent": "pipeline-data", "task_or_brief_hash": "h2", "verdict": "FAIL",
                 "revise_reason": "oops", "validated_at": "2026-07-10T00:01:00+00:00"},
            ],
        )
        _insert_dispatch_rows(
            conn, [{"persona": "pipeline-data", "model": "claude-sonnet-4-5", "tokens": 1200}]
        )
    finally:
        conn.close()

    router_path = files_dir / "router_decisions.jsonl"
    router_path.write_text(
        json.dumps({"timestamp": _RECENT_TS, "input_tokens": 500, "output_tokens": 20})
        + "\n",
        encoding="utf-8",
    )
    return project_root


class TestBuildReport:
    def test_build_report_renders_plan_gate_and_cost_from_real_db_rows(self, tmp_path) -> None:
        project_root = _make_fake_project(tmp_path)

        rendered = report.build_report(project_root)

        assert rendered["schema"] == "obs-report/1"
        assert rendered["plan_gate"]["accuracy"] == {
            "available": True, "window": 2, "revise_count": 1,
            "reject_rate": 0.5, "accuracy": 0.5,
        }
        assert rendered["cost"]["dispatch"]["available"] is True
        assert rendered["cost"]["dispatch"]["total_tokens"] == 1200
        assert rendered["cost"]["router"]["available"] is True
        assert rendered["cost"]["router"]["input_tokens"] == 500

    def test_build_report_wires_live_feed_with_real_bus_and_tracing(self, tmp_path) -> None:
        project_root = _make_fake_project(tmp_path)
        rendered = report.build_report(project_root)

        assert rendered["live_feed"]["wired"] is True
        assert rendered["live_feed"]["probe_trace_id"]
        assert rendered["live_feed"]["bus"]["published_total"] == 3
        assert rendered["live_feed"]["tracing"]["traces"][0]["span_count"] == 3

    def test_build_report_missing_db_degrades_but_still_reports(self, tmp_path) -> None:
        project_root = tmp_path / "empty-project"
        project_root.mkdir()
        rendered = report.build_report(project_root)
        assert rendered["plan_gate"] == {"available": False, "reason": "project.db not found"}
        assert rendered["cost"] == {"available": False, "reason": "project.db not found"}
        # live_feed self-check is independent of project.db and still wires.
        assert rendered["live_feed"]["wired"] is True

    def test_report_cli_prints_valid_json_matching_build_report(self, tmp_path) -> None:
        project_root = _make_fake_project(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "broker.observability.report", "--project-path", str(project_root)],
            cwd=str(BROKER_ROOT), capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["schema"] == "obs-report/1"
        assert payload["plan_gate"]["accuracy"]["available"] is True


# ---------------------------------------------------------------------------
# .memory/health.py::check_observability_report — same subprocess-not-
# import convention `check_broker_mcp_boots` already uses.
# ---------------------------------------------------------------------------

_MEMORY_DIR = REPO_ROOT / ".memory"


def _load_health() -> types.ModuleType:
    mod_name = "health_live_obs_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, _MEMORY_DIR / "health.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestHealthCheckObservabilityReport:
    def test_skips_when_nexus_broker_dir_absent(self, tmp_path) -> None:
        health = _load_health()
        results = health.check_observability_report(str(tmp_path))
        assert len(results) == 1
        assert results[0].severity == "SKIP"

    @pytest.mark.skipif(MCP_JSON_UNSUBSTITUTED, reason=_MCP_JSON_SKIP_REASON)
    def test_real_repo_renders_info_rows_never_fail(self) -> None:
        """Real (non-fixture) integration: runs the actual subprocess against
        THIS repo's own nexus-broker/ + project.db — the same call
        `run_checks` makes in production. Must render INFO rows (data may or
        may not be present) and must never FAIL on a healthy install.
        """
        health = _load_health()
        results = health.check_observability_report(str(REPO_ROOT))
        assert results, "expected at least one CheckResult"
        names = {r.name for r in results}
        assert "observability.plan_gate_accuracy" in names
        assert "observability.cost" in names
        assert "observability.live_feed" in names
        assert all(r.severity != "FAIL" for r in results), results

    def test_registered_in_runtime_checks_not_session_checks(self) -> None:
        """R5-T06 note: this check spawns a `uv run` subprocess, so it must
        live in RUNTIME (only runs when runtime=True), never SESSION (which
        the --no-runtime SessionStart banner path always runs)."""
        health = _load_health()
        import inspect

        source = inspect.getsource(health.run_checks)
        runtime_block = source.split("session_checks: list")[0]
        assert "check_observability_report" in runtime_block

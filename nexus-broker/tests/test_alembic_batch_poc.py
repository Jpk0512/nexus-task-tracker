"""Alembic-batch PoC (R5-T11 / plans/15-r5-dag.yaml N65).

Covers the node's acceptance criteria:
  1. A 3-revision chain applies cleanly across >=3 fixture installs, each
     stamping its own `alembic_version_poc` table (per-install versioning,
     not a shared ledger).
  2. A seeded fault on the batch's Nth chain-step invocation halts the batch
     mid-way (`stop_on_error`) leaving the failed install's own progress
     partially stamped and every later install untouched.
  3. Re-running the SAME `BatchRunner.run(...)` call with the fault cleared
     resumes correctly: the already-complete install is a no-op, the
     previously-failed install continues from its last stamp, and the
     never-reached install runs in full — all three land at chain HEAD.

Never touches the real `.memory/schema.sql` — every DB here is a throwaway
sqlite file under `tmp_path`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from broker.migrations import BatchRunner, Migration, current_version, sql_step


class _FlakyStep:
    """Raises on its `fail_on_call`-th invocation across the WHOLE batch,
    before touching the DB — so a retry always resumes onto clean state
    regardless of the sqlite3 DDL-doesn't-rollback-on-close nuance
    (`BatchRunner`'s docstring)."""

    def __init__(self, fail_on_call: int) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def __call__(self, conn: sqlite3.Connection) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("seeded failure: simulated fleet-node fault")
        conn.execute("ALTER TABLE widgets ADD COLUMN price INTEGER DEFAULT 0")


def _make_chain(flaky: _FlakyStep) -> list[Migration]:
    return [
        Migration("0001_create_widgets", sql_step(
            "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )),
        Migration("0002_add_price", flaky),
        Migration("0003_seed_default", sql_step(
            "INSERT INTO widgets (name, price) VALUES ('default-widget', 100)"
        )),
    ]


def _fixture_installs(tmp_path: Path, n: int) -> list[Path]:
    paths = [tmp_path / f"install-{i}" / "project.db" for i in range(n)]
    for p in paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(str(p)).close()
    return paths


def _stamped_version(path: Path) -> str | None:
    conn = sqlite3.connect(str(path))
    try:
        return current_version(conn)
    finally:
        conn.close()


def test_batch_migrates_three_fixture_installs_to_head(tmp_path: Path) -> None:
    installs = _fixture_installs(tmp_path, 3)
    flaky = _FlakyStep(fail_on_call=-1)  # never fires
    runner = BatchRunner(_make_chain(flaky))

    result = runner.run(installs)

    assert result.ok
    assert len(result.results) == 3
    for install, path in zip(result.results, installs, strict=True):
        assert install.applied == ["0001_create_widgets", "0002_add_price", "0003_seed_default"]
        assert install.stamped_version == "0003_seed_default"
        assert _stamped_version(path) == "0003_seed_default"
        conn = sqlite3.connect(str(path))
        row = conn.execute("SELECT name, price FROM widgets").fetchone()
        conn.close()
        assert row == ("default-widget", 100)


def test_mid_batch_failure_halts_and_resumes_cleanly(tmp_path: Path) -> None:
    installs = _fixture_installs(tmp_path, 3)
    # Chain step "0002_add_price" is invoked once per install in order; the
    # 2nd invocation overall lands during install #1 (0-indexed) — i.e. the
    # 2nd of 3 fixture installs, a genuine MID-batch fault.
    flaky = _FlakyStep(fail_on_call=2)
    runner = BatchRunner(_make_chain(flaky))

    first = runner.run(installs, stop_on_error=True)

    assert not first.ok
    assert len(first.results) == 2, "batch must halt before touching install #2"
    assert first.results[0].ok
    assert first.results[0].applied == ["0001_create_widgets", "0002_add_price", "0003_seed_default"]
    assert not first.results[1].ok
    assert "seeded failure" in first.results[1].error
    assert first.results[1].applied == ["0001_create_widgets"], (
        "install #1 must be stamped through its last SUCCESSFUL revision only"
    )
    assert _stamped_version(installs[0]) == "0003_seed_default"
    assert _stamped_version(installs[1]) == "0001_create_widgets"
    assert _stamped_version(installs[2]) is None, "install #2 must be untouched"

    # Resume: same runner (fault already spent), same install list.
    second = runner.run(installs, stop_on_error=True)

    assert second.ok
    assert len(second.results) == 3
    assert second.results[0].applied == [], "already-at-head install must be a no-op on resume"
    assert second.results[1].applied == ["0002_add_price", "0003_seed_default"], (
        "previously-failed install must resume from its last stamp, not from scratch"
    )
    assert second.results[2].applied == ["0001_create_widgets", "0002_add_price", "0003_seed_default"]
    for path in installs:
        assert _stamped_version(path) == "0003_seed_default"
        conn = sqlite3.connect(str(path))
        row = conn.execute("SELECT name, price FROM widgets").fetchone()
        conn.close()
        assert row == ("default-widget", 100)


def test_current_version_is_none_for_unmigrated_db(tmp_path: Path) -> None:
    (path,) = _fixture_installs(tmp_path, 1)
    assert _stamped_version(path) is None


def test_empty_chain_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        BatchRunner([])

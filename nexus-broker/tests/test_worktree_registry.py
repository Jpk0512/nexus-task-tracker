"""NATIVE-52 — broker-side worktree ownership registry.

DEC-008 permits worktrees for parallel workflows ONLY when the workflow owns
the full lifecycle (auto-merge-back + removal mandatory). worktree-guard.sh
needs ground truth to check 'git worktree add <path>' against; this registry
is that ground truth. These tests pin: register/read round-trip, release,
TTL-boundary liveness, sweep of expired records, atomic-write leaves no temp
file, and fail-soft {} on a corrupt/missing file.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import broker.worktree_registry as registry_mod
from broker.worktree_registry import (
    is_live,
    read_registry,
    register_worktree,
    release_worktree,
    sweep,
)


def _patch_path(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(registry_mod, "REGISTRY_PATH", path)


def test_register_then_read_round_trip(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)

    record = register_worktree(
        path="/repo/.worktrees/wt-1", owner_id="workflow-42", branch="feat/x"
    )

    assert record["owner_id"] == "workflow-42"
    assert record["branch"] == "feat/x"
    assert record["ttl_seconds"] == 14400
    assert "created_at" in record

    registry = read_registry()
    assert registry["/repo/.worktrees/wt-1"] == record


def test_release_removes_record(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)

    register_worktree(path="/repo/.worktrees/wt-2", owner_id="agent-1", branch="feat/y")
    assert release_worktree("/repo/.worktrees/wt-2") is True

    registry = read_registry()
    assert "/repo/.worktrees/wt-2" not in registry


def test_release_missing_path_returns_false(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)

    # No prior registration for this path.
    assert release_worktree("/repo/.worktrees/never-registered") is False


def test_is_live_true_within_ttl(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(tz=UTC)
    record = {
        "owner_id": "x",
        "branch": "b",
        "created_at": (now - timedelta(seconds=100)).isoformat(),
        "ttl_seconds": 200,
    }
    assert is_live(record, now) is True


def test_is_live_false_past_ttl_boundary(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(tz=UTC)
    record = {
        "owner_id": "x",
        "branch": "b",
        "created_at": (now - timedelta(seconds=201)).isoformat(),
        "ttl_seconds": 200,
    }
    assert is_live(record, now) is False


def test_is_live_false_exactly_at_boundary(tmp_path: Path, monkeypatch) -> None:
    """now == created_at + ttl_seconds is NOT live — the boundary is strict '<'."""
    created = datetime.now(tz=UTC) - timedelta(seconds=200)
    record = {
        "owner_id": "x",
        "branch": "b",
        "created_at": created.isoformat(),
        "ttl_seconds": 200,
    }
    now = created + timedelta(seconds=200)
    assert is_live(record, now) is False


def test_is_live_false_on_unparseable_created_at() -> None:
    record = {"owner_id": "x", "branch": "b", "created_at": "not-a-timestamp", "ttl_seconds": 200}
    assert is_live(record, datetime.now(tz=UTC)) is False


def test_sweep_drops_expired_keeps_live() -> None:
    now = datetime.now(tz=UTC)
    live_rec = {
        "owner_id": "live",
        "branch": "b",
        "created_at": (now - timedelta(seconds=10)).isoformat(),
        "ttl_seconds": 100,
    }
    expired_rec = {
        "owner_id": "expired",
        "branch": "b",
        "created_at": (now - timedelta(seconds=1000)).isoformat(),
        "ttl_seconds": 100,
    }
    registry = {"/live/path": live_rec, "/expired/path": expired_rec}

    swept = sweep(registry, now)

    assert "/live/path" in swept
    assert "/expired/path" not in swept
    # Original registry is untouched (sweep returns a new dict).
    assert "/expired/path" in registry


def test_register_sweeps_expired_records_on_write(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)

    # Seed an already-expired record directly.
    now = datetime.now(tz=UTC)
    expired_created = (now - timedelta(seconds=999999)).isoformat()
    target.write_text(
        json.dumps(
            {
                "/expired/path": {
                    "owner_id": "stale",
                    "branch": "b",
                    "created_at": expired_created,
                    "ttl_seconds": 10,
                }
            }
        )
    )

    register_worktree(path="/new/path", owner_id="fresh", branch="b2")

    registry = read_registry()
    assert "/expired/path" not in registry
    assert "/new/path" in registry


def test_atomic_write_leaves_no_temp_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)

    register_worktree(path="/repo/.worktrees/wt-3", owner_id="agent-2", branch="feat/z")

    assert target.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != target.name]
    assert leftovers == [], f"temp file(s) left behind: {leftovers}"


def test_corrupt_file_reads_as_empty_dict(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)
    target.write_text("{not valid json::")

    assert read_registry() == {}


def test_missing_file_reads_as_empty_dict(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)
    assert not target.exists()

    assert read_registry() == {}


def test_non_dict_json_reads_as_empty_dict(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "worktree_registry.json"
    _patch_path(monkeypatch, target)
    target.write_text(json.dumps(["not", "a", "dict"]))

    assert read_registry() == {}

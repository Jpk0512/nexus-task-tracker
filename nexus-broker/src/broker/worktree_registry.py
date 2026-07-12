"""worktree_registry.json read/write helpers — NATIVE-52 worktree ownership registry.

DEC-008 permits worktrees for parallel workflows ONLY when the workflow owns the
full lifecycle (auto-merge-back + removal is mandatory). This module gives
worktree-guard.sh a ground truth to check 'git worktree add <path>' against: a
path is only legitimate if something REGISTERED it as an owned, live grant.
Fail-closed by design — an unreadable/missing/corrupt registry must DENY, not
silently allow, because the guard cannot verify ownership without it.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

DEFAULT_TTL_SECONDS = 14400  # 4 hours


def _find_repo_root() -> Path:
    """Walk up from this file to find the repo root (.memory/ dir is the marker)."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    # Fallback: CWD (original behavior, works when run from repo root)
    return Path.cwd()


REPO_ROOT: Path = _find_repo_root()
REGISTRY_PATH: Path = REPO_ROOT / ".memory" / "files" / "worktree_registry.json"


class WorktreeRecord(TypedDict):
    owner_id: str
    branch: str
    created_at: str  # ISO8601 UTC timestamp
    ttl_seconds: int


WorktreeRegistry = dict[str, WorktreeRecord]


def read_registry() -> WorktreeRegistry:
    """Fresh read of worktree_registry.json. Fail-soft: any read error -> {}.

    This is the fail-CLOSED-friendly primitive at the CALLER level: a guard that
    gets {} back sees no live record for any path and correctly denies. read_
    registry itself just reports what's on disk (or nothing), it does not decide
    allow/deny — that's the guard's job.
    """
    try:
        raw = json.loads(REGISTRY_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _write_registry(registry: WorktreeRegistry) -> None:
    """Atomically write worktree_registry.json.

    Mirrors broker.state.write_state: temp file in the SAME directory (so
    os.replace stays a rename, not a cross-device copy) then os.replace() onto
    the target — atomic on POSIX and Windows, so a racing reader always sees a
    complete old or new file, never a torn one.
    """
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = REGISTRY_PATH.with_name(f"{REGISTRY_PATH.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(registry, indent=2))
        os.replace(tmp_path, REGISTRY_PATH)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def is_live(record: WorktreeRecord, now: datetime) -> bool:
    """True iff now < created_at + ttl_seconds.

    A record with an unparseable created_at is treated as NOT live (fail-closed
    at the record level, matching the module's overall fail-closed posture).
    """
    try:
        created = datetime.fromisoformat(record["created_at"])
    except (KeyError, ValueError, TypeError):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    ttl = record.get("ttl_seconds", DEFAULT_TTL_SECONDS)
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_SECONDS
    return now < created + timedelta(seconds=ttl)


def sweep(registry: WorktreeRegistry, now: datetime) -> WorktreeRegistry:
    """Drop expired records. Returns a NEW dict; does not mutate the input."""
    return {path: rec for path, rec in registry.items() if is_live(rec, now)}


def register_worktree(
    path: str,
    owner_id: str,
    branch: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> WorktreeRecord:
    """Register a worktree grant for `path`, sweeping expired records first.

    `path` is stored as the caller supplies it — callers (the guard, the MCP
    tool wrapper) are responsible for resolving to an absolute path before
    calling, since the registry key must match what the guard resolves
    'git worktree add <path>' to.
    """
    now = datetime.now(tz=UTC)
    registry = sweep(read_registry(), now)
    record: WorktreeRecord = {
        "owner_id": owner_id,
        "branch": branch,
        "created_at": now.isoformat(),
        "ttl_seconds": int(ttl_seconds),
    }
    registry[path] = record
    _write_registry(registry)
    return record


def release_worktree(path: str) -> bool:
    """Remove `path`'s record if present. Returns True iff a record was removed."""
    registry = read_registry()
    if path not in registry:
        return False
    del registry[path]
    _write_registry(registry)
    return True

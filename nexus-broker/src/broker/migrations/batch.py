"""Linear revision chain + batch runner — see package docstring for scope.

Resume contract (the part a fleet rollout depends on): progress is stamped
INTO each target DB, not into a shared/external ledger. A batch that dies
partway through and is re-invoked with the *same* `install_paths` list is
therefore safe to just re-run:

  - an install already at the chain HEAD is a no-op (its stamp short-circuits
    `_pending` to an empty list);
  - the install that was mid-migration when the batch died resumes from its
    LAST STAMPED revision, not from scratch (a revision only stamps after its
    `upgrade` returns without raising, so a half-applied revision is retried,
    never skipped or double-counted);
  - installs the batch never reached simply run in full.

`stop_on_error=True` (the default) halts the batch at the first failing
install rather than racing ahead into installs whose state a fleet operator
has not yet been told is inconsistent — the canary gate in
`nexus-redesign/plans/16-fleet-rollback-plan.md` depends on this halt.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

UpgradeFn = Callable[[sqlite3.Connection], None]

_VERSION_TABLE = "alembic_version_poc"


@dataclass(frozen=True)
class Migration:
    """One step in the linear chain (mirrors a single Alembic revision)."""

    revision: str
    upgrade: UpgradeFn


@dataclass
class InstallResult:
    install_path: str
    applied: list[str] = field(default_factory=list)
    stamped_version: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class BatchResult:
    results: list[InstallResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failed(self) -> list[InstallResult]:
        return [r for r in self.results if not r.ok]


def sql_step(sql: str) -> UpgradeFn:
    """Build a trivial single-statement `Migration.upgrade` callable."""

    def _step(conn: sqlite3.Connection) -> None:
        conn.execute(sql)

    return _step


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} (version_num TEXT NOT NULL)")


def current_version(conn: sqlite3.Connection) -> str | None:
    """Read the stamped revision, or None if this DB has never been migrated."""
    _ensure_version_table(conn)
    row = conn.execute(f"SELECT version_num FROM {_VERSION_TABLE} LIMIT 1").fetchone()
    return row[0] if row else None


def _stamp(conn: sqlite3.Connection, revision: str) -> None:
    conn.execute(f"DELETE FROM {_VERSION_TABLE}")
    conn.execute(f"INSERT INTO {_VERSION_TABLE} (version_num) VALUES (?)", (revision,))
    conn.commit()


def _pending(chain: list[Migration], current: str | None) -> list[Migration]:
    if current is None:
        return list(chain)
    idx = next((i for i, m in enumerate(chain) if m.revision == current), None)
    if idx is None:
        raise ValueError(f"unknown stamped revision {current!r} — chain drift")
    return chain[idx + 1 :]


class BatchRunner:
    """Applies a linear `Migration` chain across N target DBs, one at a time.

    Landmine for migration authors: sqlite3's default isolation level wraps
    DML (INSERT/UPDATE/DELETE) in an implicit transaction that `conn.close()`
    rolls back if never committed, but DDL (CREATE/ALTER/DROP TABLE)
    auto-commits immediately regardless — it is NOT undone by a later raise
    or an uncommitted close. An `upgrade()` that runs DDL and then fails
    partway leaves that DDL applied even though its revision never stamps, so
    a step must either raise before any DDL it cannot safely re-run, or make
    the DDL itself idempotent — the chain's resume logic has no rollback
    safety net for DDL to fall back on.
    """

    def __init__(self, chain: list[Migration]) -> None:
        if not chain:
            raise ValueError("migration chain must not be empty")
        self._chain = chain

    def run(self, install_paths: list[Path], *, stop_on_error: bool = True) -> BatchResult:
        results: list[InstallResult] = []
        for path in install_paths:
            result = self._run_one(path)
            results.append(result)
            if not result.ok and stop_on_error:
                break
        return BatchResult(results=results)

    def _run_one(self, path: Path) -> InstallResult:
        result = InstallResult(install_path=str(path))
        conn = sqlite3.connect(str(path))
        try:
            current = current_version(conn)
            pending = _pending(self._chain, current)
            for migration in pending:
                migration.upgrade(conn)
                _stamp(conn, migration.revision)
                result.applied.append(migration.revision)
                result.stamped_version = migration.revision
            if result.stamped_version is None:
                result.stamped_version = current
        except Exception as exc:  # noqa: BLE001 — captured per-install, batch decides whether to halt
            result.error = str(exc)
        finally:
            # DML rolls back on an uncommitted close; DDL does not (see class
            # docstring) — this close is a safety net for the former only.
            conn.close()
        return result

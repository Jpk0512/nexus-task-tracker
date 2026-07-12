"""Fail-closed wrappers: try the daemon, fall back to a direct (uncached) read
or write on `DaemonUnavailable` — the "no daemon-required invariant" contract
R4-T06's charter requires (plans/07 §1 constraint 2). This is what a future
hook integration (out of this pilot's scope; do_not_touch: `.claude/hooks/**`)
is expected to import: the daemon is purely additive, its absence must cost
only cache warmth, never data or a wrong answer.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from broker.daemon.client import DaemonUnavailable, call
from broker.daemon.registry_scan import filter_registry, scan_registry
from broker.daemon.schema_scan import scan_schema
from broker.daemon.telemetry_store import insert_rows


def get_registry(
    project_path: Path,
    query_context: str | None = None,
    *,
    allow_spawn: bool = True,
) -> dict[str, Any]:
    """Registry query with fail-closed fallback. `source` tells the caller which path answered."""
    try:
        result = call(
            project_path,
            "query_registry",
            {"query_context": query_context},
            spawn_if_missing=allow_spawn,
        )
        return {"entries": result["entries"], "source": "daemon"}
    except DaemonUnavailable:
        entries = filter_registry(scan_registry(project_path), query_context)
        return {"entries": entries, "source": "direct-fallback"}


def get_schema_snapshot(project_path: Path, *, allow_spawn: bool = True) -> dict[str, Any]:
    """Schema-snapshot query with fail-closed fallback."""
    try:
        result = call(project_path, "schema_snapshot", {}, spawn_if_missing=allow_spawn)
        return {"tables": result["tables"], "source": "daemon"}
    except DaemonUnavailable:
        db_path = Path(project_path) / ".memory" / "project.db"
        return {"tables": scan_schema(db_path), "source": "direct-fallback"}


def record_telemetry(
    project_path: Path,
    table: str,
    row: dict[str, Any],
    *,
    allow_spawn: bool = True,
) -> dict[str, Any]:
    """Telemetry write with fail-closed fallback: a down daemon must not drop the row —
    it falls back to the exact same direct-write path this cache sits in front of.
    """
    try:
        result = call(
            project_path,
            "record_telemetry",
            {"table": table, "row": row},
            spawn_if_missing=allow_spawn,
        )
        return {"accepted": result["accepted"], "source": "daemon"}
    except DaemonUnavailable:
        db_path = Path(project_path) / ".memory" / "project.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=15000")
            with conn:
                insert_rows(conn, table, [row])
            return {"accepted": True, "source": "direct-fallback"}
        finally:
            conn.close()

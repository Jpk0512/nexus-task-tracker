"""In-memory pending buffer + batched flush-to-sqlite — the write-through half
of R4-T06's charter (plans/08 §1.5), scoped to exactly the three tables named
in plans/13 N11's goal text: `dispatch_telemetry`, `skill_load_events`,
`agent_activity` (`.memory/schema.sql`).

NOT the source of truth (plans/07 §1 constraint 1): `project.db` stays
authoritative. A daemon crash before a flush cycle loses only the still-
pending rows — cache warmth, not durable data, exactly like any other
write-behind cache. Rows that already made it through `flush()` must survive
a `kill -9` of the daemon process untouched (the acceptance criterion this
module exists to satisfy); WAL journal mode + a real COMMIT inside
`flush()` is what makes that true.

Column allow-lists are hardcoded constants (never caller-supplied identifiers),
so the f-string table/column interpolation in `flush()` is not an injection
surface — only bound parameter VALUES come from the caller.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

ALLOWED_TABLES: dict[str, tuple[str, ...]] = {
    "dispatch_telemetry": (
        "session_id",
        "dispatch_id",
        "persona",
        "model",
        "task_id",
        "marker",
        "tokens",
        "token_source",
        "tool_uses",
        "duration_ms",
        "run_context",
        # F3-03 dual-write (DEC-097 Option B): allow-listed so a caller-supplied
        # `recorded_at` flows through VERBATIM instead of defaulting to
        # CURRENT_TIMESTAMP — the dual-write stamps this row and the event log
        # from ONE timestamp so the parity clock's (dispatch_id, session_id,
        # recorded_at) key lines up across both stores (event-store-design §5.2).
        # The column already exists on `.memory/schema.sql`'s dispatch_telemetry.
        "recorded_at",
    ),
    "skill_load_events": ("dispatch_id", "skill_id", "ts", "byte_len"),
    "agent_activity": (
        "agent",
        "task",
        "started",
        "elapsed",
        "status",
        "current_action",
        "session_id",
        "updated_at",
    ),
}


def _harden(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> int:
    """Insert `rows` into `table` inside the caller's transaction. Returns count inserted."""
    if table not in ALLOWED_TABLES:
        raise ValueError(f"unknown telemetry table: {table!r}")
    allowed_cols = ALLOWED_TABLES[table]
    n = 0
    for row in rows:
        present_cols = [c for c in allowed_cols if c in row]
        if not present_cols:
            continue
        placeholders = ",".join("?" for _ in present_cols)
        col_list = ",".join(present_cols)
        conn.execute(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            [row[c] for c in present_cols],
        )
        n += 1
    return n


class TelemetryStore:
    """Thread-safe pending-row buffer + batched flush. One instance per daemon."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, list[dict[str, Any]]] = {t: [] for t in ALLOWED_TABLES}
        self.rows_flushed = 0
        self.flush_count = 0

    def record(self, table: str, row: dict[str, Any]) -> None:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"unknown telemetry table: {table!r}")
        allowed_cols = ALLOWED_TABLES[table]
        clean = {k: v for k, v in row.items() if k in allowed_cols}
        with self._lock:
            self._pending[table].append(clean)

    def pending_count(self) -> int:
        with self._lock:
            return sum(len(rows) for rows in self._pending.values())

    def flush(self, db_path: Path) -> int:
        """Flush all pending rows to db_path in ONE transaction. Returns rows flushed.

        Rows are drained from `_pending` before the write so a concurrent
        `record()` during the flush is never lost and never double-flushed —
        it lands in the next cycle's batch instead.
        """
        with self._lock:
            batch = {t: rows for t, rows in self._pending.items() if rows}
            for t in batch:
                self._pending[t] = []
        if not batch:
            return 0
        conn = sqlite3.connect(db_path)
        try:
            _harden(conn)
            n = 0
            with conn:
                for table, rows in batch.items():
                    n += insert_rows(conn, table, rows)
            self.rows_flushed += n
            self.flush_count += 1
            return n
        finally:
            conn.close()

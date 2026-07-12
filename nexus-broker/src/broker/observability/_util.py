"""Small shared helpers for the observability package. Not part of the
public surface (leading underscore) — imported by `metrics.py`/`cost.py`
only, to avoid duplicating the same two three-line functions in both.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def parse_ts(value: str | None) -> datetime | None:
    """Best-effort ISO-8601 parse tolerant of a trailing 'Z' (which
    `datetime.fromisoformat` cannot read on <3.11's fromisoformat).
    Returns None on any malformed/missing input rather than raising —
    every caller in this package must degrade gracefully, never crash,
    on a real but imperfect row.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None

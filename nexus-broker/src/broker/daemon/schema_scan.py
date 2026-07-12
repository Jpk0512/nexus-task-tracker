"""Schema-agnostic PRAGMA introspection of a project's `.memory/project.db`.

Deliberately does NOT assume any fixed table/column shape (plans/07 §1
constraint 5, §2 Option C named risk (a)): each fleet project's schema.sql
may sit at a different migration level, and this cache must be a row-
passthrough over whatever shape is actually present — never a
re-implementation of `.memory/log.py`'s typed query logic. Table names read
back come from `sqlite_master` itself (not caller input), so the
`PRAGMA table_info` f-string below is not an injection surface.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def scan_schema(db_path: Path) -> dict[str, list[str]]:
    """{table_name: [column_name, ...]} for every user table in db_path.

    Missing/unreadable db_path -> {} (schema-agnostic: an absent DB is just
    an empty shape, not an error the cache should raise on).
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return {}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        shape: dict[str, list[str]] = {}
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            shape[table] = [c[1] for c in cols]
        return shape
    finally:
        conn.close()

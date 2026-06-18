"""SQLite connection helpers for broker.vault.

vault_db_conn() opens the project DB with sqlite-vec loaded — used by readers
for vector queries and by the writer daemon for full mutations.

job_enqueue_conn() opens the same DB in write mode but ONLY for vault_jobs
INSERTs. Per plan §7.1 (B3), the reader processes (stdio/http) write nothing
except vault_jobs rows.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec


def open_db(db_path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open project.db with sqlite-vec extension loaded."""
    if read_only:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    # WAL is set DB-wide; harmless to (re-)apply on each writer connection.
    if not read_only:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
    return conn

#!/usr/bin/env python3
"""Apply M-001: sqlite-vec virtual table for semantic memory (Phase D Layer 2)."""
import sqlite3
import sqlite_vec
from pathlib import Path

DB = Path(__file__).parent.parent / "project.db"


def apply(db_path: Path = DB) -> None:
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.executescript("""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    kind TEXT PARTITION KEY,
    ref_id TEXT,
    text_blob TEXT,
    created_at TEXT,
    embedding float[768]
);
""")
    conn.close()
    print("M-001 applied.")


if __name__ == "__main__":
    apply()

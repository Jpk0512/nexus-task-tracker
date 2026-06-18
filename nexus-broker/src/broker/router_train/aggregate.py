"""STORE / AGGREGATE — read every registered install's router_decisions.jsonl
into one deduped in-memory set (00-DESIGN.md 'STORE / AGGREGATE').

Install paths come from the broker project_registry (.memory/project.db). Each
install's capture log lives at <install>/.memory/files/router_decisions.jsonl.
Rows are deduped on (session_id, prompt_hash); when a row predates the v2 schema
and carries no prompt_hash, it is recovered from sha256(prompt) so legacy v1 rows
still join under the shared convention.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from broker.state import REPO_ROOT

DECISIONS_RELPATH = Path(".memory") / "files" / "router_decisions.jsonl"

# Back-compat: the 604 already-captured rows + any v2 rows written before the
# qwen→pred rename carry the model's-guess fields under the legacy ``qwen_*`` keys.
# normalize-on-read maps each ``qwen_*`` to its ``pred_*`` successor IFF the pred_*
# key is absent (a fresh pred_* write always wins). After normalization the rest of
# the pipeline sees ONLY pred_* — the legacy keys never leak past the read boundary,
# so the 604 legacy rows stay labelable and validate against the renamed schema.
_QWEN_TO_PRED = {
    "qwen_persona": "pred_persona",
    "qwen_confidence": "pred_confidence",
    "qwen_difficulty": "pred_difficulty",
    "qwen_required_skills": "pred_required_skills",
    "qwen_tdd_required": "pred_tdd_required",
}


def prompt_hash(prompt: str) -> str:
    """Shared convention: full-hex sha256 of the UTF-8 prompt."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Map legacy ``qwen_*`` keys to ``pred_*`` (only when the pred_* key is absent).

    Returns the SAME dict when nothing needs migrating (no allocation on the hot
    path of already-pred rows); otherwise returns a shallow copy with the legacy
    keys renamed so the caller's record is never mutated in place. Idempotent: a
    record already carrying pred_* (or carrying neither) passes through unchanged.
    """
    if not any(old in record for old in _QWEN_TO_PRED):
        return record
    migrated = dict(record)
    for old, new in _QWEN_TO_PRED.items():
        if old in migrated:
            value = migrated.pop(old)
            migrated.setdefault(new, value)
    return migrated


def _dedupe_key(record: dict[str, Any]) -> tuple[str, str]:
    session_id = str(record.get("session_id") or "")
    ph = record.get("prompt_hash")
    if not ph:
        prompt = record.get("prompt")
        ph = prompt_hash(prompt) if isinstance(prompt, str) and prompt else ""
    return session_id, str(ph)


def registry_install_paths(db_path: Path | None = None) -> list[Path]:
    """Active install roots from project_registry, the local repo first.

    The local repo is always included (it produces capture too and may not have a
    self-referential registry row); remaining active installs are appended.
    """
    paths: list[Path] = [REPO_ROOT]
    db = db_path or (REPO_ROOT / ".memory" / "project.db")
    if not db.exists():
        return paths
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT project_path FROM project_registry WHERE status = 'active'"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return paths
    seen = {REPO_ROOT.resolve()}
    for (project_path,) in rows:
        p = Path(project_path)
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        paths.append(p)
    return paths


def _read_decisions(install_root: Path) -> list[dict[str, Any]]:
    path = install_root / DECISIONS_RELPATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    rec = _normalize_record(rec)
                    rec.setdefault("source_project", str(install_root))
                    records.append(rec)
    except OSError:
        return records
    return records


def aggregate(install_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    """Read each install's router_decisions.jsonl into one deduped record list.

    Dedupe is on (session_id, prompt_hash); later rows win (idempotent re-runs).
    With no install_paths the broker registry supplies them.
    """
    roots = install_paths if install_paths is not None else registry_install_paths()
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for root in roots:
        for rec in _read_decisions(Path(root)):
            deduped[_dedupe_key(rec)] = rec
    return list(deduped.values())

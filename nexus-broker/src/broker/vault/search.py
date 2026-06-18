"""vault_query — unified search / list / recent / by-tag / by-domain.

Plan §6.6 read contract:
  - Default filters: exclude_maturity=('archived',), min_confidence=3
  - Callers opt in to lower-trust content via exclude_maturity=(), min_confidence=1
  - Privacy fence enforced before any DB read when filters.domain is fenced

Plan §6.5 modes:
  - 'fast'   : dense (sqlite-vec) — the ONLY implemented retrieval path today
  - 'hybrid' : dense + FTS5 BM25 (Phase 6+; not yet implemented — falls back to 'fast')
  - 'quality': dense + FTS5 + reranker (Phase 6+; not yet implemented — falls back to 'fast')

NOTE: _recall_fast executes dense-only regardless of the requested mode.
The returned ``mode`` key always reflects what actually ran ('fast'), never
a mode that was requested but not implemented.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from broker.vault import policy as policy_mod
from broker.vault.db import open_db

if TYPE_CHECKING:
    from broker.vault._server import AppConfig


_DEFAULT_EXCLUDE_MATURITY: tuple[str, ...] = ("archived",)
_DEFAULT_MIN_CONFIDENCE: int = 3


def _normalize_filters(filters: dict[str, Any]) -> dict[str, Any]:
    out = dict(filters or {})
    if "exclude_maturity" not in out:
        out["exclude_maturity"] = _DEFAULT_EXCLUDE_MATURITY
    else:
        em = out["exclude_maturity"]
        out["exclude_maturity"] = tuple(em) if isinstance(em, (list, tuple)) else ()
    if "min_confidence" not in out:
        out["min_confidence"] = _DEFAULT_MIN_CONFIDENCE
    return out


def _fts_has_rows(db_path: Path) -> bool:
    try:
        conn = open_db(db_path, read_only=True)
        try:
            row = conn.execute("SELECT count(*) FROM vec_memory_fts LIMIT 1").fetchone()
            return bool(row and row[0] > 0)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — FTS probe is best-effort; absence => not present
        return False


def _resolve_mode(requested: str, db_path: Path) -> str:
    if requested not in {"fast", "hybrid", "quality"}:
        return "fast"
    if requested in {"hybrid", "quality"} and not _fts_has_rows(db_path):
        return "fast"
    return requested


def _recall_fast(
    *,
    db_path: Path,
    vault_root: Path,
    query: str,
    kind: str | None,
    domain: str | None,
    min_confidence: int,
    exclude_maturity: Iterable[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Wraps research.ingest.recall() — keeps schema in lock-step with the canonical
    read API (plan §6.6).

    vault_root is the research/ directory; its parent is the repo root where
    research/ingest.py lives as the package ``research.ingest``.
    """
    import importlib
    import sys

    repo_root = vault_root.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    mem_path = str(repo_root / ".memory")
    if mem_path not in sys.path:
        sys.path.insert(0, mem_path)
    ingest = importlib.import_module("research.ingest")
    rows = ingest.recall(
        query,
        top_k=limit,
        kind=kind,
        domain=domain,
        min_confidence=min_confidence,
        exclude_maturity=tuple(exclude_maturity),
    )
    return rows


def _list_recent(
    *,
    vault_root: Path,
    domain: str | None,
    kind: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """List-mode fallback when no query string is supplied.

    Walks the vault filesystem for .md files; returns the most-recently-touched
    paths under (10-knowledge/<domain>/, 20-workshop/, 35-ai-techniques/,
    30-projects/, 40-inbox/raw/) — skipping archive.
    """
    candidates: list[tuple[float, Path]] = []
    roots: list[Path] = []
    if domain:
        roots.append(vault_root / "10-knowledge" / domain)
    else:
        roots.append(vault_root / "10-knowledge")
        roots.append(vault_root / "20-workshop")
        roots.append(vault_root / "30-projects")
        roots.append(vault_root / "35-ai-techniques")
        roots.append(vault_root / "40-inbox" / "raw")
    for root in roots:
        if not root.is_dir():
            continue
        for md in root.rglob("*.md"):
            try:
                candidates.append((md.stat().st_mtime, md))
            except OSError:
                continue
    candidates.sort(key=lambda t: t[0], reverse=True)
    hits = []
    for mtime, path in candidates[: max(limit, 0)]:
        rel = path.relative_to(vault_root).as_posix()
        # Derive the actual domain from the path: notes under 10-knowledge/<domain>/
        # carry their domain in the second path component.
        parts = rel.split("/")
        actual_domain: str | None = domain
        if actual_domain is None and len(parts) >= 2 and parts[0] == "10-knowledge":
            actual_domain = parts[1]
        hits.append(
            {
                "path": rel,
                "kind": kind or "research",
                "ref_id": rel,
                "text_blob": None,
                "created_at": datetime.fromtimestamp(mtime, tz=UTC).isoformat(),
                "distance": None,
                "domain": actual_domain,
                "confidence": None,
                "maturity": None,
            }
        )
    return hits


async def vault_query_impl(
    *,
    config: AppConfig,
    filters: dict[str, Any],
    query: str | None,
    order_by: str | None,
    mode: str,
    limit: int,
) -> dict[str, Any]:
    f = _normalize_filters(filters)
    domain = f.get("domain")
    kind = f.get("kind")
    min_confidence = int(f.get("min_confidence", _DEFAULT_MIN_CONFIDENCE))
    exclude_maturity = tuple(f.get("exclude_maturity", _DEFAULT_EXCLUDE_MATURITY))

    # Privacy fence — short-circuit BEFORE we hit the DB.
    fenced = policy_mod.domains_filter_includes_fenced(domain)
    if fenced is not None:
        decision = policy_mod.enforce("vault_query", fenced, config.access_mode)
        if decision == "return_empty":
            return {"hits": [], "mode": "fast", "fenced": True}
        if decision == "deny":
            return {
                "hits": [],
                "mode": "fast",
                "fenced": True,
                "error": "fenced_denied",
            }

    if query:
        # _recall_fast is dense-only regardless of the requested mode; report
        # the mode that actually executed ('fast'), not the requested label.
        hits = _recall_fast(
            db_path=config.db_path,
            vault_root=config.vault_root,
            query=query,
            kind=kind,
            domain=domain,
            min_confidence=min_confidence,
            exclude_maturity=exclude_maturity,
            limit=limit,
        )
        executed_mode = "fast"
    else:
        hits = _list_recent(
            vault_root=config.vault_root, domain=domain, kind=kind, limit=limit
        )
        executed_mode = "fast"

    if order_by == "recent" and hits and "created_at" in hits[0]:
        hits.sort(key=lambda h: h.get("created_at") or "", reverse=True)

    # VAULT-1: when domain=None (unfiltered query), still exclude fenced domains
    # for access modes that cannot read fenced content.
    rules = policy_mod.load_rules(config.vault_root)
    if not rules.can_read_fenced(config.access_mode):
        hits = [
            h for h in hits
            if policy_mod.hit_visible(h.get("domain"), config.access_mode, rules)
        ]

    return {"hits": hits, "mode": executed_mode, "fenced": False, "count": len(hits)}

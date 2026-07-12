"""Citation-coverage probe (R3-T04, node N09) — deterministic.

Every leaf must cite >=1 `context_files` entry, and every cited path must
exist on disk relative to the repo root. Pure filesystem check — no model
call, no network.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from broker.plan_validation.score import Verdict

# probes/citation.py -> probes -> plan_validation -> broker -> src -> nexus-broker -> repo root
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[5]


def check_citation_coverage(doc: dict[str, Any], repo_root: str | Path | None = None) -> Verdict:
    """Fail any leaf that cites zero context_files, or cites a path that does
    not exist on disk (relative to `repo_root`, default the repo root
    inferred from this file's own location)."""
    root = Path(repo_root) if repo_root is not None else DEFAULT_REPO_ROOT
    offending: list[str] = []
    details: list[str] = []
    for raw in doc.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        nid = raw.get("node_id")
        if not isinstance(nid, str) or not nid:
            continue
        cited = raw.get("context_files") or []
        if not cited:
            offending.append(nid)
            details.append(f"'{nid}' cites zero context_files")
            continue
        missing = [c for c in cited if isinstance(c, str) and not (root / c).exists()]
        if missing:
            offending.append(nid)
            details.append(f"'{nid}' cites nonexistent context_files: {missing}")

    return Verdict(passed=not details, offending_node_ids=sorted(set(offending)), details=details)

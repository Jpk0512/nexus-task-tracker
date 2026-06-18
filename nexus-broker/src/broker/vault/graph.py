"""vault_graph_query + vault_health impls.

vault_graph_query: reads `<repo_path>/knowledge-graph.json` (repo-analyzer output).
                   Optional server-side `jq` subprocess for filter expressions.

vault_health: reads research/_meta/vault-health.json if present; else synthesizes
              a dict from file counts per zone + latest backup + validator status.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from broker.vault._server import AppConfig


_KNOWN_ZONES = (
    "00-meta",
    "10-knowledge",
    "15-code-knowledge",
    "20-workshop",
    "30-projects",
    "35-ai-techniques",
    "40-inbox",
    "99-archive",
)


def _resolve_repo_path(vault_root: Path, repo_path: str) -> Path:
    p = Path(repo_path)
    # Reject absolute paths and traversals — must stay within vault_root.
    if p.is_absolute():
        # Return a safe dead-end inside vault_root rather than an outside path.
        return vault_root.resolve() / "_blocked_absolute"
    resolved_root = vault_root.resolve()
    candidate = (vault_root / repo_path).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        # Traversal escape — return a safe dead-end inside vault_root.
        return resolved_root / "_blocked_traversal"
    return candidate


async def vault_graph_query_impl(
    *, config: AppConfig, repo_path: str, jq_expr: str | None
) -> dict[str, Any]:
    repo_dir = _resolve_repo_path(config.vault_root, repo_path)
    graph_file = repo_dir / "knowledge-graph.json"
    if not graph_file.is_file():
        return {"error": "graph_not_found", "repo_path": repo_path, "looked_at": str(graph_file)}
    raw = graph_file.read_text(encoding="utf-8", errors="ignore")
    if not jq_expr:
        try:
            return {"graph": json.loads(raw), "filtered": False}
        except json.JSONDecodeError as exc:
            return {"error": "invalid_json", "detail": str(exc)}
    if shutil.which("jq") is None:
        return {"error": "jq_unavailable", "hint": "install jq to use jq_expr"}
    proc = subprocess.run(
        ["jq", jq_expr],
        input=raw,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        return {"error": "jq_failed", "stderr": proc.stderr.strip()}
    try:
        return {"graph": json.loads(proc.stdout), "filtered": True, "jq_expr": jq_expr}
    except json.JSONDecodeError:
        return {"graph_raw": proc.stdout, "filtered": True, "jq_expr": jq_expr}


def _count_md(zone_dir: Path) -> int:
    if not zone_dir.is_dir():
        return 0
    return sum(1 for _ in zone_dir.rglob("*.md"))


def _b4_eval_state(meta_dir: Path) -> dict[str, Any]:
    """Compute the B4 auto-disable verdict from the latest eval run.

    Phase 7d binding (App C #7d): if `current_top3 / baseline_top3` falls
    below `baseline.threshold_pct`, surface `recall_disabled: true` so
    the SessionStart hook + kind=research domains can auto-flip to
    append-only until recovery.

    Defaults are inert: missing baseline OR missing latest run -> the
    feature is off (`recall_disabled: false`, `state: "no_data"`).
    """
    baseline_path = meta_dir / "eval-baseline.json"
    latest_path = meta_dir / "eval-run-latest.json"
    out: dict[str, Any] = {
        "recall_disabled": False,
        "state": "no_data",
    }
    if not baseline_path.is_file() or not latest_path.is_file():
        return out
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        out["state"] = "parse_error"
        return out

    try:
        baseline_top3 = float(baseline.get("baseline_top3", 0.0))
        threshold_pct = float(baseline.get("threshold_pct", 0.85))
        current_top3 = float(latest.get("aggregate", {}).get("top3_accuracy", 0.0))
        current_n = int(latest.get("aggregate", {}).get("n", 0))
    except (TypeError, ValueError):
        out["state"] = "type_error"
        return out
    if baseline_top3 <= 0:
        out["state"] = "baseline_zero"
        return out
    ratio = current_top3 / baseline_top3
    out.update(
        {
            "state": "ok" if ratio >= threshold_pct else "degraded",
            "recall_disabled": ratio < threshold_pct,
            "baseline_top3": baseline_top3,
            "current_top3": current_top3,
            "current_n": current_n,
            "threshold_pct": threshold_pct,
            "ratio": round(ratio, 4),
        }
    )
    return out


async def vault_health_impl(*, config: AppConfig) -> dict[str, Any]:
    cached = config.vault_root / "_meta" / "vault-health.json"
    if cached.is_file():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            data.setdefault("source", "cached")
            return data
        except json.JSONDecodeError:
            pass
    # Synthesize from filesystem.
    counts = {zone: _count_md(config.vault_root / zone) for zone in _KNOWN_ZONES}
    meta_dir = config.vault_root / "_meta"
    pii_findings = meta_dir / "pii-findings.jsonl"
    pii_count = 0
    if pii_findings.is_file():
        try:
            pii_count = sum(1 for line in pii_findings.open() if line.strip())
        except OSError:
            pii_count = 0
    backup_log = config.vault_root.parent / ".memory" / "backups"
    last_backup = None
    if backup_log.is_dir():
        backups = sorted(backup_log.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if backups:
            last_backup = datetime.fromtimestamp(
                backups[0].stat().st_mtime, tz=UTC
            ).isoformat()
    eval_state = _b4_eval_state(meta_dir)
    return {
        "source": "synthesized",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "file_counts": counts,
        "pii_findings": pii_count,
        "last_backup_at": last_backup,
        "validator": {"privacy_fence": "unchecked"},
        "eval": eval_state,
        "recall_disabled": eval_state["recall_disabled"],
    }

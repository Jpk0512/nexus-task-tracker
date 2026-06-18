"""Prompts + Resources (plan §7.2).

Prompts (2):
  - vault-state-summary       — markdown summary of vault state.
  - vault-graduate-suggestions — seedling notes ready to promote.

Resources (2):
  - note://<path>             — read-only vault note (cap: MOCs + 100 recent).
  - job://<id>                — vault_jobs row.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from broker.vault import policy as policy_mod
from broker.vault.graph import vault_health_impl
from broker.vault.jobs import get_job

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from broker.vault._server import AppConfig


_RESOURCE_RECENT_LIMIT = 100


def _enumerate_resource_uris(vault_root: Path) -> list[str]:
    """MOCs + 100 most-recently-touched .md files (plan §7.2)."""
    mocs: list[Path] = list(vault_root.rglob("_MOC.md"))
    recents: list[tuple[float, Path]] = []
    for md in vault_root.rglob("*.md"):
        if md.name == "_MOC.md":
            continue
        try:
            recents.append((md.stat().st_mtime, md))
        except OSError:
            continue
    recents.sort(key=lambda t: t[0], reverse=True)
    chosen = mocs + [p for _, p in recents[:_RESOURCE_RECENT_LIMIT]]
    uris = []
    seen: set[str] = set()
    for p in chosen:
        try:
            rel = p.relative_to(vault_root).as_posix()
        except ValueError:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        uris.append(f"note://{rel}")
    return uris


def register_prompts_and_resources(mcp: FastMCP, config: AppConfig) -> None:
    @mcp.prompt(name="vault-state-summary")
    def vault_state_summary() -> str:
        """Markdown summary of current vault state — counts per zone, recent additions, health."""
        import asyncio

        health = asyncio.run(vault_health_impl(config=config))
        lines = ["# Vault state summary",
                 f"_generated {datetime.now(tz=UTC).isoformat()}_", ""]
        counts: dict[str, Any] = health.get("file_counts", {})
        if counts:
            lines.append("## File counts per zone")
            for zone, n in counts.items():
                lines.append(f"- `{zone}`: {n}")
            lines.append("")
        pii = health.get("pii_findings", 0)
        last_backup = health.get("last_backup_at")
        lines.append(f"PII findings: **{pii}**")
        lines.append(f"Last backup: **{last_backup or 'none recorded'}**")
        return "\n".join(lines)

    @mcp.prompt(name="vault-graduate-suggestions")
    def vault_graduate_suggestions() -> str:
        """Seedlings ripe for promotion: high backlink count + 30+ days old."""
        suggestions: list[tuple[int, Path, float]] = []
        seedling_root = config.vault_root / "20-workshop"
        if seedling_root.is_dir():
            for md in seedling_root.rglob("*.md"):
                try:
                    text = md.read_text(encoding="utf-8", errors="ignore")
                    mtime = md.stat().st_mtime
                except OSError:
                    continue
                if "maturity: seedling" not in text:
                    continue
                stem = md.stem
                backlinks = 0
                needle = f"[[{stem}"
                for other in config.vault_root.rglob("*.md"):
                    if other == md:
                        continue
                    try:
                        if needle in other.read_text(encoding="utf-8", errors="ignore"):
                            backlinks += 1
                    except OSError:
                        continue
                age_days = (datetime.now().timestamp() - mtime) / 86400.0
                if backlinks >= 2 and age_days >= 30:
                    suggestions.append((backlinks, md, age_days))
        suggestions.sort(key=lambda t: t[0], reverse=True)
        if not suggestions:
            return "# Graduate suggestions\n\n_no seedlings currently meet the heuristic_"
        lines = ["# Graduate suggestions",
                 "Seedlings with ≥2 backlinks and ≥30 days old:", ""]
        for backlinks, path, age in suggestions[:20]:
            rel = path.relative_to(config.vault_root).as_posix()
            lines.append(f"- `{rel}` — {backlinks} backlinks, {age:.0f}d old")
        return "\n".join(lines)

    # ---------- Resources ----------

    @mcp.resource("note://{rel_path}")
    def note_resource(rel_path: str) -> str:
        """Read a vault note as a resource. Privacy-fenced: fenced domains return stub on web_default."""
        # Note: parametrized resources read on-demand; enumeration cap is advisory.
        candidate = (config.vault_root / rel_path).resolve()
        try:
            candidate.relative_to(config.vault_root.resolve())
        except ValueError:
            return json.dumps({"error": "escapes_vault_root", "path": rel_path})
        if not candidate.is_file():
            return json.dumps({"error": "not_found", "path": rel_path})
        raw = candidate.read_text(encoding="utf-8", errors="ignore")
        # VAULT-4: enforce privacy fence — parse domain and check policy.
        import re as _re
        m = _re.search(r"^domain:\s*(\S+)", raw, _re.MULTILINE)
        domain = m.group(1) if m else None
        rules = policy_mod.load_rules(config.vault_root)
        decision = policy_mod.enforce("vault_get_note", domain, config.access_mode, rules)
        if decision in ("return_empty", "deny"):
            return json.dumps({"fenced": True, "path": rel_path})
        return raw

    @mcp.resource("job://{job_id}")
    def job_resource(job_id: str) -> str:
        """Read a vault_jobs row by id."""
        row = get_job(config.db_path, job_id)
        if not row:
            return json.dumps({"error": "not_found", "job_id": job_id})
        return json.dumps(row, indent=2, sort_keys=True)

    # Stash enumeration list on the app for diagnostics / tests; FastMCP itself
    # serves the parametrized resource pattern, so explicit enumeration is
    # advisory (plan §7.2 cap: MOCs + 100 most-recent).
    mcp._nexus_vault_resource_enumeration = _enumerate_resource_uris(config.vault_root)

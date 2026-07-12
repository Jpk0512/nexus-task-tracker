"""Pure, uncached scan of `.claude/agents/*.md` + `.claude/skills/*/SKILL.md`.

Deliberately schema-agnostic and MCP-blind: this module never reads or
serves an MCP tool schema (that surface is superseded by the already-running
toolport/conduit aggregator — plans/13-r4-conductor-lane-plan.md §1). It only
ever returns skill/agent metadata, which is the non-MCP half of the 2.1
registry-query capability plans/08 §2.1 splits between toolport (MCP half)
and this daemon (skills/agents half).

Kept as plain functions (no caching, no daemon dependency) so the exact same
scan logic backs BOTH the daemon's warm in-memory cache (server.py) and the
direct-read fallback a caller uses when the daemon is unreachable
(fallback.py) — one scan implementation, never two that could drift.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def scan_agents(project_path: Path) -> list[dict[str, Any]]:
    """Scan `.claude/agents/*.md` frontmatter. Missing dir -> empty list, not an error."""
    agents_dir = Path(project_path) / ".claude" / "agents"
    out: list[dict[str, Any]] = []
    if not agents_dir.is_dir():
        return out
    for md_file in sorted(agents_dir.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if not fm.get("name"):
            continue
        out.append(
            {
                "kind": "agent",
                "name": fm.get("name"),
                "description": fm.get("description", ""),
                "model": fm.get("model"),
                "skills": fm.get("skills", []),
            }
        )
    return out


def scan_skills(project_path: Path) -> list[dict[str, Any]]:
    """Scan `.claude/skills/*/SKILL.md` frontmatter. Missing dir -> empty list."""
    skills_dir = Path(project_path) / ".claude" / "skills"
    out: list[dict[str, Any]] = []
    if not skills_dir.is_dir():
        return out
    for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if not fm.get("name"):
            continue
        metadata = fm.get("metadata")
        tier = metadata.get("tier") if isinstance(metadata, dict) else None
        out.append(
            {
                "kind": "skill",
                "name": fm.get("name"),
                "description": fm.get("description", ""),
                "tier": tier,
            }
        )
    return out


def scan_registry(project_path: Path) -> list[dict[str, Any]]:
    """The full non-MCP registry: every agent + skill entry, unfiltered."""
    return scan_agents(project_path) + scan_skills(project_path)


def filter_registry(
    entries: list[dict[str, Any]], query_context: str | None
) -> list[dict[str, Any]]:
    """2.1-half context-filtered query: keyword match on name/description.

    None/empty query_context returns everything unfiltered (session-start's
    "give me the roster" case); a non-empty query_context narrows to the
    relevant subset, per plans/07 §2 Option C's `query_registry` worked
    example.
    """
    if not query_context:
        return entries
    needle = query_context.lower()
    return [
        e
        for e in entries
        if needle in (e.get("name") or "").lower() or needle in (e.get("description") or "").lower()
    ]

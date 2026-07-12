"""Deterministic parser for docs/agents/SKILL_MAP.md's `| persona | work_type | skills |` table.

Pure text parsing — no YAML/JSON source of truth exists for this map (it is
authored as a markdown table for human + guard consumption per the file's own
banner). No model calls, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def parse_skill_map(text: str) -> dict[tuple[str, str], list[str]]:
    """Parse every `| persona | work_type | skills |` row into a lookup dict.

    Keys are (persona, work_type) exactly as written (including the literal
    `*` wildcard work_type row). Skills cells of the form `[a, b, c]` or
    `a, b, c` are both accepted; whitespace is trimmed. Header/separator rows
    (`---`) and rows outside the `| persona | work_type | skills |` header's
    section are skipped by requiring a plausible skills cell (a `[`-wrapped
    or comma-joined list of skill-like tokens).
    """
    rows: dict[tuple[str, str], list[str]] = {}
    for line in text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        persona, work_type, skills_cell = (g.strip() for g in m.groups())
        if not persona or not work_type or not skills_cell:
            continue
        if persona.lower() == "persona" and work_type.lower() == "work_type":
            continue  # header row
        if _SEP_CELL_RE.match(persona) and _SEP_CELL_RE.match(work_type):
            continue  # markdown table separator row
        skills = _parse_skills_cell(skills_cell)
        if skills is None:
            continue
        rows[(persona, work_type)] = skills
    return rows


def _parse_skills_cell(cell: str) -> list[str] | None:
    inner = cell.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    elif inner in ("", "—", "-", "none", "None"):
        return []
    parts = [p.strip().strip("`") for p in inner.split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return []
    # Reject prose cells (e.g. a caption row that slipped through the regex) —
    # a genuine skills list is short kebab-case-ish tokens, never containing
    # whitespace inside a token or sentence punctuation.
    if any(" " in p or p.endswith(".") for p in parts):
        return None
    return parts


def load_skill_map(path: str | Path) -> dict[tuple[str, str], list[str]]:
    return parse_skill_map(Path(path).read_text(encoding="utf-8"))


def required_skills_for(
    skill_map: dict[tuple[str, str], list[str]], persona: str, work_type: str
) -> list[str]:
    """Resolve the minimum required skills for (persona, work_type).

    Exact (persona, work_type) match wins. Otherwise falls back to the UNION
    of every row for that persona (SKILL_MAP.md's own documented fallback
    behaviour: "falls back to every row for that persona, so the persona's
    foundational convention skill is always enforced").
    """
    if (persona, work_type) in skill_map:
        return skill_map[(persona, work_type)]
    fallback: list[str] = []
    seen: set[str] = set()
    for (p, _wt), skills in skill_map.items():
        if p != persona:
            continue
        for s in skills:
            if s not in seen:
                seen.add(s)
                fallback.append(s)
    return fallback

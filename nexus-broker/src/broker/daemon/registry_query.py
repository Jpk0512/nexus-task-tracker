"""Full-scope 2.1 context-filtered registry query — the non-MCP surface.

plans/13-r4-conductor-lane-plan.md N15 (plans/08 §2.1 "full"): completes the
context-filtered result semantics `query_registry(project_path, query_context)`
returning only the skill/agent/persona subset relevant to `query_context`,
never the whole parsed registry — the verbatim worked example plans/08 §2.1
and plans/07 §2 Option C both cite. The MCP-schema half of §2.1 stays
superseded by the already-running toolport/conduit aggregator (plans/13 §1);
this module never reads or serves an MCP tool schema.

Phase A's pilot (`registry_scan.filter_registry`) shipped a single-substring
match over name/description only — enough to unblock the reversibility gate,
not "full" per §2.1's own text. This module is the FULL-scope query surface:
multi-term AND matching over name/description/kind/tier *and* an agent's
declared `skills` list (so a query naming a skill an agent uses finds that
agent even when the skill name never appears in its description), plus a
defense-in-depth strip of anything MCP-tool-schema-shaped before it can ever
leave this surface — belt-and-braces on top of `registry_scan.scan_registry`,
which is already MCP-blind by construction.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from broker.daemon.registry_scan import scan_registry

# Keys that only ever appear on an MCP tool-schema-shaped payload. An entry
# carrying any of these is never a skill/agent entry and must never be served
# here — even if a future change to scan_registry's upstream sources widened
# what it returns, this is the last line of defense for the SS1 boundary.
_MCP_SHAPE_KEYS = ("inputSchema", "tools", "mcp")

_ALLOWED_KINDS = ("agent", "skill")


def _entry_haystack(entry: dict[str, Any]) -> str:
    """Everything about an entry a query_context term may legitimately match
    against — name, description, kind, tier, and (for agents) the skills list.
    """
    parts = [
        str(entry.get("name") or ""),
        str(entry.get("description") or ""),
        str(entry.get("kind") or ""),
        str(entry.get("tier") or ""),
    ]
    skills = entry.get("skills")
    if isinstance(skills, list):
        parts.extend(str(s) for s in skills)
    return " ".join(parts).lower()


def _matches(entry: dict[str, Any], terms: list[str]) -> bool:
    haystack = _entry_haystack(entry)
    return all(term in haystack for term in terms)


def _strip_non_registry_shapes(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in entries
        if e.get("kind") in _ALLOWED_KINDS and not any(k in e for k in _MCP_SHAPE_KEYS)
    ]


def query_registry(project_path: Path, query_context: str | None) -> list[dict[str, Any]]:
    """The plans/08 §2.1 worked example, verbatim signature.

    `query_context=None` (or empty/whitespace-only) is the session-start
    "give me the roster" case and returns the full skill/agent/persona
    registry. A non-empty `query_context` is split on whitespace into terms
    that must ALL match (AND, not OR) somewhere in an entry's name,
    description, kind, tier, or declared skills — so a multi-word query
    narrows monotonically instead of widening back out to a broad
    single-token substring hit. Never returns an MCP-tool-schema-shaped
    entry (SS1 boundary; see `_strip_non_registry_shapes`).
    """
    entries = _strip_non_registry_shapes(scan_registry(Path(project_path)))
    if query_context is None:
        return entries
    terms = [t for t in query_context.lower().split() if t]
    if not terms:
        return entries
    return [e for e in entries if _matches(e, terms)]

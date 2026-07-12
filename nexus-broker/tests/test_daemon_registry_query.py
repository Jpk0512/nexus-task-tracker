"""N15 (plans/13-r4-conductor-lane-plan.md) — plans/08 §2.1 FULL-scope
context-filtered registry query: `broker.daemon.registry_query.query_registry`.

Covers exactly N15's acceptance criteria:
  - a context-targeted query returns a strict subset of the unfiltered dump
  - no MCP tool schema ever appears in a query_registry response (SS1 boundary)
plus the "full scope" delta over Phase A's single-substring
`registry_scan.filter_registry`: multi-term AND narrowing and matching via an
agent's declared `skills` list, not name/description alone.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from broker.daemon import registry_query
from broker.daemon.registry_query import query_registry

HERMES_AGENT_MD = """---
name: demo-hermes
description: "Handles Tableau auth integration work."
model: sonnet
skills:
  - hermes-auth-patterns
---

# Demo Hermes
"""

ATLAS_AGENT_MD = """---
name: demo-atlas
description: "DuckDB schema design lead."
model: opus
skills:
  - atlas-schema-patterns
---

# Demo Atlas
"""

QUILL_AGENT_MD = """---
name: demo-quill
description: "Testing and quality lead."
model: sonnet
skills:
  - tdd-core
---

# Demo Quill
"""

TABLEAU_SKILL_MD = """---
name: demo-tableau-skill
description: "Tableau REST endpoints reference."
metadata: {tier: sonnet}
---

# Demo Tableau Skill
"""

DUCKDB_SKILL_MD = """---
name: demo-duckdb-skill
description: "DuckDB read/write patterns."
metadata: {tier: opus}
---

# Demo DuckDB Skill
"""


def _write_agent(project: Path, filename: str, text: str) -> None:
    (project / ".claude" / "agents" / filename).write_text(text)


def _write_skill(project: Path, dirname: str, text: str) -> None:
    skill_dir = project / ".claude" / "skills" / dirname
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(text)


@pytest.fixture()
def project(tmp_path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".claude" / "agents").mkdir(parents=True)
    (proj / ".claude" / "skills").mkdir(parents=True)
    _write_agent(proj, "demo-hermes.md", HERMES_AGENT_MD)
    _write_agent(proj, "demo-atlas.md", ATLAS_AGENT_MD)
    _write_agent(proj, "demo-quill.md", QUILL_AGENT_MD)
    _write_skill(proj, "demo-tableau-skill", TABLEAU_SKILL_MD)
    _write_skill(proj, "demo-duckdb-skill", DUCKDB_SKILL_MD)
    return proj


# ── unfiltered dump (query_context None / empty) ────────────────────────────


def test_none_query_context_returns_full_unfiltered_dump(project) -> None:
    result = query_registry(project, None)
    assert {e["name"] for e in result} == {
        "demo-hermes",
        "demo-atlas",
        "demo-quill",
        "demo-tableau-skill",
        "demo-duckdb-skill",
    }


def test_blank_query_context_also_returns_full_dump(project) -> None:
    full = query_registry(project, None)
    assert query_registry(project, "") == full
    assert query_registry(project, "   ") == full


# ── strict-subset acceptance criterion ──────────────────────────────────────


def test_targeted_query_is_a_strict_subset_of_the_unfiltered_dump(project) -> None:
    full = query_registry(project, None)
    narrowed = query_registry(project, "tableau")

    assert len(narrowed) < len(full)
    assert narrowed != full
    for entry in narrowed:
        assert entry in full  # every narrowed entry really is drawn from the full dump
    assert {e["name"] for e in narrowed} == {"demo-hermes", "demo-tableau-skill"}


def test_no_match_returns_empty_list_not_full_dump(project) -> None:
    assert query_registry(project, "nonexistent-xyz-term") == []


# ── full-scope delta 1: multi-term AND narrows further than a single term ──


def test_multi_term_query_narrows_monotonically(project) -> None:
    single_term = query_registry(project, "tableau")
    multi_term = query_registry(project, "tableau auth")

    assert {e["name"] for e in single_term} == {"demo-hermes", "demo-tableau-skill"}
    # "auth" only appears in demo-hermes's description, not the skill's —
    # adding a second AND'd term must narrow the result further, never widen it.
    assert {e["name"] for e in multi_term} == {"demo-hermes"}
    assert len(multi_term) < len(single_term)
    for entry in multi_term:
        assert entry in single_term


# ── full-scope delta 2: matches via an agent's declared `skills` list ──────


def test_query_matches_via_skills_list_not_just_name_or_description(project) -> None:
    """demo-quill's name/description never mention 'tdd-core' — only its
    frontmatter `skills:` list does. Phase A's registry_scan.filter_registry
    (name/description only) would miss this; the FULL-scope query must not.
    """
    result = query_registry(project, "tdd-core")
    assert [e["name"] for e in result] == ["demo-quill"]


def test_query_is_case_insensitive_across_all_matched_fields(project) -> None:
    assert {e["name"] for e in query_registry(project, "TDD-CORE")} == {"demo-quill"}
    assert {e["name"] for e in query_registry(project, "DuckDB")} == {
        "demo-atlas",
        "demo-duckdb-skill",
    }


# ── SS1 boundary: no MCP tool schema ever appears, defense-in-depth ────────


def test_ss1_boundary_real_scan_never_yields_mcp_shaped_entries(project) -> None:
    for entry in query_registry(project, None):
        assert entry["kind"] in ("agent", "skill")
        assert "inputSchema" not in entry
        assert "tools" not in entry
        assert "mcp" not in entry


def test_ss1_boundary_strips_mcp_shaped_entries_even_if_upstream_widens(
    project, monkeypatch
) -> None:
    """Belt-and-braces: even if a future scan_registry regression started
    returning an MCP-tool-schema-shaped payload, query_registry must still
    never serve it. Simulated by monkeypatching the upstream scan.
    """

    def _poisoned_scan(_project_path):
        return [
            {"kind": "agent", "name": "clean-agent", "description": "fine"},
            {
                "kind": "tool",
                "name": "evil-mcp-tool",
                "description": "should never be served",
                "inputSchema": {"type": "object"},
            },
            {
                "kind": "mcp_server",
                "name": "another-evil-one",
                "description": "also should never be served",
                "tools": ["a", "b"],
            },
        ]

    monkeypatch.setattr(registry_query, "scan_registry", _poisoned_scan)

    result = query_registry(project, None)
    assert [e["name"] for e in result] == ["clean-agent"]

    # And a targeted query over the poisoned entries stays just as clean.
    result_targeted = query_registry(project, "evil")
    assert result_targeted == []


# ── accepts a str path, not just a Path (project_path per plans/08 §2.1) ───


def test_accepts_string_project_path(project) -> None:
    result = query_registry(str(project), "tableau")
    assert {e["name"] for e in result} == {"demo-hermes", "demo-tableau-skill"}

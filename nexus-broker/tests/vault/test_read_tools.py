"""Phase 5a — read tools return expected shapes on a fixture vault."""
from __future__ import annotations

import pytest
from broker.vault.moc import vault_moc_impl
from broker.vault.notes import vault_get_note_impl
from broker.vault.search import vault_query_impl


@pytest.mark.asyncio
async def test_vault_query_list_mode_returns_hits(config_local) -> None:
    """No query → list-recent mode returns fixture notes."""
    result = await vault_query_impl(
        config=config_local, filters={}, query=None, order_by=None, mode="fast", limit=10
    )
    assert result["fenced"] is False
    assert result["mode"] == "fast"
    paths = [h["path"] for h in result["hits"]]
    assert any("golden-note.md" in p for p in paths)


@pytest.mark.asyncio
async def test_vault_query_filters_by_domain(config_local) -> None:
    """domain filter restricts the search to that zone."""
    result = await vault_query_impl(
        config=config_local,
        filters={"domain": "general-knowledge"},
        query=None,
        order_by=None,
        mode="fast",
        limit=10,
    )
    assert result["fenced"] is False
    paths = [h["path"] for h in result["hits"]]
    for p in paths:
        assert "10-knowledge/general-knowledge" in p


@pytest.mark.asyncio
async def test_vault_get_note_returns_frontmatter_and_links(config_local) -> None:
    result = await vault_get_note_impl(
        config=config_local,
        path="10-knowledge/general-knowledge/golden-note.md",
        include_body=True,
    )
    assert result["fenced"] is False
    assert result["frontmatter"]["title"] == "Golden note"
    assert result["frontmatter"]["domain"] == "general-knowledge"
    assert "TL;DR" in result["body"]
    assert "other-note" in result["outbound_links"]


@pytest.mark.asyncio
async def test_vault_get_note_not_found(config_local) -> None:
    result = await vault_get_note_impl(
        config=config_local, path="does/not/exist.md", include_body=True
    )
    assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_vault_moc_splits_curated_and_recent(config_local) -> None:
    result = await vault_moc_impl(config=config_local, zone="00-meta")
    assert "curated" in result and "recent" in result
    assert "Curated notes" in result["curated"]
    assert "recent autogen 1" in result["recent"]
    # Markers themselves are not in either section.
    assert "BEGIN AUTO" not in result["curated"]
    assert "END AUTO" not in result["recent"]


@pytest.mark.asyncio
async def test_vault_moc_missing_zone(config_local) -> None:
    result = await vault_moc_impl(config=config_local, zone="does/not/exist")
    assert result.get("error") == "moc_not_found"

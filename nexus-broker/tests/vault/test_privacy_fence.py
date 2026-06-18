"""Phase 5a — privacy fence policy.

A vault_query over domain='personal' returns:
  - hits (or at least no fence error) from access_mode='local_stdio'
  - empty list with fenced=True from access_mode='web_default'

The list-mode fallback exercises the fence WITHOUT depending on sqlite-vec
embedding, so the test is hermetic.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from broker.state import BrokerState, is_notepad_fresh
from broker.vault import policy as policy_mod
from broker.vault.notes import vault_get_note_impl
from broker.vault.search import vault_query_impl


@pytest.mark.asyncio
async def test_query_personal_local_stdio_allows(config_local) -> None:
    result = await vault_query_impl(
        config=config_local,
        filters={"domain": "personal"},
        query=None,
        order_by=None,
        mode="fast",
        limit=10,
    )
    assert result["fenced"] is False
    paths = [h["path"] for h in result["hits"]]
    assert any("personal-secret.md" in p for p in paths)


@pytest.mark.asyncio
async def test_query_personal_web_default_returns_empty(config_web) -> None:
    result = await vault_query_impl(
        config=config_web,
        filters={"domain": "personal"},
        query=None,
        order_by=None,
        mode="fast",
        limit=10,
    )
    assert result["fenced"] is True
    assert result["hits"] == []


@pytest.mark.asyncio
async def test_query_work_web_default_returns_empty(config_web) -> None:
    result = await vault_query_impl(
        config=config_web,
        filters={"domain": "work"},
        query=None,
        order_by=None,
        mode="fast",
        limit=10,
    )
    assert result["fenced"] is True
    assert result["hits"] == []


@pytest.mark.asyncio
async def test_query_non_fenced_domain_passes_web_default(config_web) -> None:
    """domain='general-knowledge' is NOT fenced — web_default may read it."""
    result = await vault_query_impl(
        config=config_web,
        filters={"domain": "general-knowledge"},
        query=None,
        order_by=None,
        mode="fast",
        limit=10,
    )
    assert result["fenced"] is False
    paths = [h["path"] for h in result["hits"]]
    assert any("golden-note.md" in p for p in paths)


@pytest.mark.asyncio
async def test_get_note_fence_blocks_web_default(config_web) -> None:
    """Reading a personal-domain note from web_default returns empty body + fenced=True."""
    result = await vault_get_note_impl(
        config=config_web,
        path="10-knowledge/personal/personal-secret.md",
        include_body=True,
    )
    assert result["fenced"] is True
    assert result["body"] == ""
    assert result["frontmatter"] == {}


def test_policy_load_finds_rules(config_local) -> None:
    rules = policy_mod.load_rules(config_local.vault_root)
    assert "personal" in rules.fenced_domains
    assert "work" in rules.fenced_domains
    assert rules.can_read_fenced("local_stdio") is True
    assert rules.can_read_fenced("web_default") is False
    assert policy_mod.enforce("vault_query", "personal", "web_default", rules) == "return_empty"
    assert policy_mod.enforce("vault_query", "personal", "local_stdio", rules) == "allow"
    assert policy_mod.enforce("vault_query", "general-knowledge", "web_default", rules) == "allow"


def test_bearer_matches_constant_time() -> None:
    assert policy_mod.bearer_matches("abc", "abc") is True
    assert policy_mod.bearer_matches("abc", "abd") is False
    assert policy_mod.bearer_matches("", "abc") is False
    assert policy_mod.bearer_matches(None, "abc") is False
    assert policy_mod.bearer_matches("abc", None) is False


def test_is_notepad_fresh_naive_timestamp_reported_fresh() -> None:
    """A tz-naive notepad_logged_at that is recent must be reported FRESH.

    Before BRK-01, is_notepad_fresh computed now with the same (naive) tzinfo as
    logged — which worked — but swallowed ALL exceptions including TypeError from
    tz-aware/naive subtraction. The fix normalises naive timestamps to UTC via
    replace(tzinfo=UTC), then always uses datetime.now(tz=UTC), so arithmetic is
    always tz-aware and the try/except is narrowed to fromisoformat only.
    """
    # A naive ISO timestamp that is 10 seconds ago — well within NOTEPAD_STALE_SECONDS (300 s).
    recent_naive = (datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(seconds=10)).isoformat()
    state: BrokerState = {"notepad_logged_at": recent_naive}
    assert is_notepad_fresh(state) is True, (
        f"Recent naive timestamp {recent_naive!r} should be FRESH but was reported stale"
    )


def test_is_notepad_fresh_stale_naive_timestamp_reported_stale() -> None:
    """A tz-naive notepad_logged_at that is old must be reported STALE."""
    old_naive = (datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(seconds=400)).isoformat()
    state: BrokerState = {"notepad_logged_at": old_naive}
    assert is_notepad_fresh(state) is False


def test_is_notepad_fresh_bad_string_returns_false() -> None:
    """A non-ISO string must return False without raising."""
    state: BrokerState = {"notepad_logged_at": "not-a-date"}
    assert is_notepad_fresh(state) is False


def test_hit_visible_fenced_domain_web_default_hidden(config_local) -> None:
    """hit_visible returns False for a fenced domain under web_default."""
    rules = policy_mod.load_rules(config_local.vault_root)
    assert policy_mod.hit_visible("personal", "web_default", rules) is False


def test_hit_visible_fenced_domain_local_stdio_visible(config_local) -> None:
    """hit_visible returns True for a fenced domain under local_stdio."""
    rules = policy_mod.load_rules(config_local.vault_root)
    assert policy_mod.hit_visible("personal", "local_stdio", rules) is True


def test_hit_visible_non_fenced_domain_always_visible(config_local) -> None:
    """hit_visible returns True for a non-fenced domain regardless of access_mode."""
    rules = policy_mod.load_rules(config_local.vault_root)
    assert policy_mod.hit_visible("general-knowledge", "web_default", rules) is True


def test_hit_visible_none_domain_gated_by_access_mode(config_local) -> None:
    """hit_visible with domain=None defers to access_mode's can_read_fenced."""
    rules = policy_mod.load_rules(config_local.vault_root)
    # web_default cannot read fenced → domain=None rows are hidden
    assert policy_mod.hit_visible(None, "web_default", rules) is False
    # local_stdio can read fenced → domain=None rows are visible
    assert policy_mod.hit_visible(None, "local_stdio", rules) is True


def test_enforce_unconfigured_tool_fenced_domain_web_default_returns_empty(config_local) -> None:
    """enforce() for a fenced domain with an unconfigured tool name falls through to
    access_mode gate rather than unconditionally allowing (BRK-01 policy fix)."""
    rules = policy_mod.load_rules(config_local.vault_root)
    # vault_related is not in the enforcement table → should return_empty for web_default
    result = policy_mod.enforce("vault_related", "personal", "web_default", rules)
    assert result == "return_empty"


def test_enforce_unconfigured_tool_fenced_domain_local_stdio_allows(config_local) -> None:
    """enforce() for an unconfigured tool + fenced domain still allows local_stdio."""
    rules = policy_mod.load_rules(config_local.vault_root)
    result = policy_mod.enforce("vault_related", "personal", "local_stdio", rules)
    assert result == "allow"


@pytest.mark.asyncio
async def test_search_short_circuit_returns_fast_not_resolve_mode(config_web) -> None:
    """The privacy-fence short-circuit in vault_query_impl returns mode='fast' directly
    (not via _resolve_mode which probes the DB), so the return is the literal string."""
    result = await vault_query_impl(
        config=config_web,
        filters={"domain": "personal"},
        query=None,
        order_by=None,
        mode="fast",
        limit=10,
    )
    assert result["fenced"] is True
    assert result["mode"] == "fast"

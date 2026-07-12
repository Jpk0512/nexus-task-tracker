"""nexus_discover-shaped ranking over a built capability index — R5-T03 N48.

Not wired to the live `nexus_discover` MCP tool (`broker/server.py`,
`broker/discovery.py`) — that wiring is out of this node's write_scope
(plans/15-r5-dag.yaml N48) and is deferred to its downstream consumer.
This module implements the search/ranking half of the proposal's
`nexus_discover` contract (`PROPOSAL-context-slimming-broker-disclosure.md`
section 2) against a `registry_index.index.build_index` result, so a future
wiring node has a tested ranking function to call rather than reinventing
one at the MCP boundary.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def estimate_tokens(record: Mapping[str, Any]) -> int:
    """The `estimated_tokens` a `nexus_discover` candidate reports for
    `record` — the summary-level token budget, since discovery only ever
    returns summaries (proposal section 2: "summary by default").
    """
    return int(record["token_budget"]["summary"])


def _score(record: Mapping[str, Any], query_terms: Sequence[str]) -> int:
    haystack = " ".join((record["id"], record["category"], record["summary"])).lower()
    return sum(haystack.count(term) for term in query_terms)


def discover(
    index: Mapping[str, Any],
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Rank `index["capabilities"]` against `query`, returning compact
    candidates shaped per the proposal's `nexus_discover` output: `id`,
    `kind`, `summary`, `authority`, `estimated_tokens`, `requires_profile`,
    `next_action`.

    An empty `query` matches every candidate (ranked by `id` only) rather
    than returning nothing — `nexus_discover({query: ""})` is a legal "list
    everything" call, not an error. `kinds`, if given, restricts the
    candidate pool before scoring. Ties break by `id` ascending, so results
    are deterministic for identical inputs. `limit` caps the returned list.
    """
    query_terms = [term for term in query.lower().split() if term]

    candidates = [
        record
        for record in index.get("capabilities", [])
        if kinds is None or record["kind"] in kinds
    ]

    if query_terms:
        scored = [(record, _score(record, query_terms)) for record in candidates]
        scored = [(record, score) for record, score in scored if score > 0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]["id"]))
        ranked = [record for record, _ in scored]
    else:
        ranked = sorted(candidates, key=lambda record: record["id"])

    return [
        {
            "id": record["id"],
            "kind": record["kind"],
            "summary": record["summary"],
            "authority": record["authority"],
            "estimated_tokens": estimate_tokens(record),
            "requires_profile": list(record["requires_profile"]),
            "next_action": "nexus_prepare",
        }
        for record in ranked[:limit]
    ]

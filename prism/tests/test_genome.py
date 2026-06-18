"""Tests for BugGenome (prism/genome.py)."""
from __future__ import annotations

import pytest
from prism.genome import VECTOR_SIZE, BugGenome, deterministic_vector

REQUIRED_COLLECTIONS = {"bug_patterns", "risk_scores"}


def test_genome_creates_all_collections(qdrant_client):
    BugGenome(client=qdrant_client)
    existing = {c.name for c in qdrant_client.get_collections().collections}
    assert existing == REQUIRED_COLLECTIONS


def test_deterministic_vector_is_stable_and_unit_length():
    v1 = deterministic_vector("hello")
    v2 = deterministic_vector("hello")
    assert v1 == v2
    assert len(v1) == VECTOR_SIZE
    magnitude = sum(x * x for x in v1) ** 0.5
    assert abs(magnitude - 1.0) < 1e-9


def test_deterministic_vector_distinct_inputs_differ():
    assert deterministic_vector("a") != deterministic_vector("b")


def test_collection_size_matches_produced_vector(qdrant_client):
    BugGenome(client=qdrant_client)
    for name in REQUIRED_COLLECTIONS:
        info = qdrant_client.get_collection(name)
        assert info.config.params.vectors.size == len(deterministic_vector("x"))


@pytest.mark.asyncio
async def test_genome_record_finding_stores_payload(qdrant_client):
    genome = BugGenome(client=qdrant_client)
    await genome.record_finding(
        technique="test", description="SQL inj", file="db.py", line=42, severity=8
    )
    top = await genome.get_highest_risk(limit=5)
    assert len(top) > 0
    assert top[0]["file"] == "db.py"
    assert top[0]["severity"] == 8


@pytest.mark.asyncio
async def test_genome_record_finding_needs_no_model(qdrant_client):
    """Recording a finding must not touch any embedding model or network."""
    genome = BugGenome(client=qdrant_client)
    await genome.record_finding(
        technique="pipeline", description="d", file="x.py", line=1, severity=3
    )
    assert qdrant_client.get_collection("bug_patterns").points_count == 1


@pytest.mark.asyncio
async def test_genome_get_highest_risk_sorted_by_severity(qdrant_client):
    genome = BugGenome(client=qdrant_client)
    for sev in [3, 9, 1, 7]:
        await genome.record_finding(
            technique="t", description=f"sev {sev}", file="x.py", line=sev, severity=sev
        )
    top = await genome.get_highest_risk(limit=10)
    severities = [item["severity"] for item in top]
    assert severities == sorted(severities, reverse=True)

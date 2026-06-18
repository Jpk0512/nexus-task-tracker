"""Wiring tests for the PRISM MCP server (prism/synthesis/mcp_server.py)."""
from __future__ import annotations

import time

import pytest
from prism.genome import BugGenome
from prism.synthesis import mcp_server

from tests.conftest import FakeLLMClient


@pytest.fixture()
def seeded_genome(qdrant_client, monkeypatch):
    genome = BugGenome(client=qdrant_client)
    monkeypatch.setattr(mcp_server, "_get_genome", lambda: genome)
    return genome


async def _seed(genome: BugGenome) -> None:
    await genome.record_finding(
        technique="pipeline",
        description="SQL injection in query builder",
        file="db.py",
        line=42,
        severity=8,
        extra={"category": "injection"},
    )
    await genome.record_finding(
        technique="pipeline",
        description="unbounded recursion",
        file="parse.py",
        line=10,
        severity=3,
        extra={"category": "logic"},
    )


@pytest.mark.asyncio
async def test_get_risk_map_renders_from_payloads(seeded_genome):
    await _seed(seeded_genome)
    report = await mcp_server.get_risk_map()
    assert "db.py" in report
    assert "42" in report
    assert "8" in report
    # highest severity first
    assert report.index("db.py") < report.index("parse.py")


@pytest.mark.asyncio
async def test_get_recent_findings_renders_from_payloads(seeded_genome):
    await _seed(seeded_genome)
    report = await mcp_server.get_recent_findings(minutes=60)
    assert "2 findings" in report
    assert "SQL injection" in report


@pytest.mark.asyncio
async def test_get_recent_findings_respects_cutoff(seeded_genome):
    await seeded_genome.record_finding(
        technique="pipeline", description="old", file="o.py", line=1, severity=1
    )
    # backdate the only finding well beyond the window
    results, _ = seeded_genome.client.scroll(collection_name="bug_patterns", with_payload=True)
    for r in results:
        r.payload["ts"] = time.time() - 7200
        seeded_genome.client.upsert(
            collection_name="bug_patterns",
            points=[type(results[0])(id=r.id, vector=[0.0] * 256, payload=r.payload)],
        )
    report = await mcp_server.get_recent_findings(minutes=60)
    assert "No findings" in report


@pytest.mark.asyncio
async def test_get_convergence_report_no_convergence_single_technique(seeded_genome):
    await _seed(seeded_genome)
    report = await mcp_server.get_convergence_report()
    assert "No convergence" in report


@pytest.mark.asyncio
async def test_trigger_deep_scan_records_finding_via_injected_client(
    seeded_genome, monkeypatch, tmp_path
):
    target = tmp_path / "vuln.py"
    target.write_text("def f(u):\n    return db.execute(u)\n")
    monkeypatch.chdir(tmp_path)

    client = FakeLLMClient(
        [
            '{"suspicious": true, "category": "injection", "confidence": 0.9}',
            '{"severity": 8, "finding": "SQL injection", "confidence": 0.9}',
            "root cause: untrusted input concatenated into query",
        ]
    )

    from prism.config import Config
    from prism.sensor import pipeline as pipeline_mod

    cfg = Config(
        backend="anthropic",
        anthropic_api_key="k",
        claude_base_url="",
        triage_model="t",
        diagnose_model="d",
        rca_model="r",
        genome_path=":memory:",
        severity_threshold=5,
        lmstudio_base_url="http://localhost:1234/v1",
        claude_cli_path="claude",
        claude_cli_timeout=120.0,
    )

    real_cls = pipeline_mod.AssemblyLine

    def _factory(genome, config=None, client_arg=None):
        return real_cls(genome, cfg, client)

    monkeypatch.setattr(mcp_server, "_get_genome", lambda: seeded_genome)
    monkeypatch.setattr("prism.sensor.pipeline.AssemblyLine", _factory)

    report = await mcp_server.trigger_deep_scan("vuln.py")
    assert "finding" in report.lower()
    top = await seeded_genome.get_highest_risk(limit=5)
    assert any(item["severity"] == 8 for item in top)


@pytest.mark.asyncio
async def test_trigger_deep_scan_rejects_outside_path(seeded_genome):
    report = await mcp_server.trigger_deep_scan("/etc/passwd")
    assert "Access denied" in report

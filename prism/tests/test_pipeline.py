"""Tests for AssemblyLine pipeline (prism/sensor/pipeline.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from prism.sensor.pipeline import AssemblyLine, _parse_json

from tests.conftest import FakeLLMClient


def _make_genome():
    genome = MagicMock()
    genome.record_finding = AsyncMock()
    return genome


def test_parse_json_handles_fenced_and_prose():
    assert _parse_json('```json\n{"a": 1}\n```')["a"] == 1
    assert _parse_json('here you go: {"b": 2} done')["b"] == 2


@pytest.mark.asyncio
async def test_pipeline_returns_not_suspicious_for_benign(test_config):
    client = FakeLLMClient(['{"suspicious": false, "category": "none", "confidence": 0.9}'])
    genome = _make_genome()
    result = await AssemblyLine(genome, test_config, client).process("x = 1", file="safe.py", line=1)
    assert result.suspicious is False
    assert result.escalated is False
    genome.record_finding.assert_not_called()


@pytest.mark.asyncio
async def test_pipeline_calls_diagnose_on_suspicious(test_config):
    client = FakeLLMClient(
        [
            '{"suspicious": true, "category": "injection", "confidence": 0.85}',
            '{"severity": 3, "finding": "issue", "confidence": 0.7}',
        ]
    )
    genome = _make_genome()
    result = await AssemblyLine(genome, test_config, client).process("db.execute(u)", file="db.py", line=5)
    assert len(client.calls) == 2
    assert result.suspicious is True
    assert result.escalated is False


@pytest.mark.asyncio
async def test_pipeline_escalates_and_records_high_severity(test_config):
    client = FakeLLMClient(
        [
            '{"suspicious": true, "category": "injection", "confidence": 0.9}',
            '{"severity": 7, "finding": "SQL inj", "confidence": 0.85}',
            "root cause text",
        ]
    )
    genome = _make_genome()
    result = await AssemblyLine(genome, test_config, client).process("db.execute(u)", file="db.py", line=5)
    assert result.suspicious is True
    assert result.escalated is True
    assert result.root_cause == "root cause text"
    genome.record_finding.assert_called_once()
    assert genome.record_finding.call_args.kwargs["severity"] >= 5


@pytest.mark.asyncio
async def test_pipeline_skips_diagnose_on_low_confidence(test_config):
    client = FakeLLMClient(['{"suspicious": true, "category": "x", "confidence": 0.3}'])
    genome = _make_genome()
    result = await AssemblyLine(genome, test_config, client).process("x", file="x.py", line=1)
    assert result.suspicious is False
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_pipeline_triage_error_is_graceful(test_config):
    client = FakeLLMClient([httpx.ConnectError("boom")])
    genome = _make_genome()
    result = await AssemblyLine(genome, test_config, client).process("code", file="x.py", line=1)
    assert result.error is not None
    assert result.suspicious is False
    assert result.category == "error"

"""Shared fixtures for PRISM test suite."""
from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest


@pytest.fixture()
def qdrant_client() -> Generator[Any, None, None]:
    """In-memory Qdrant client for tests — no network, no persistence."""
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        pytest.skip("qdrant-client not installed")
    client = QdrantClient(":memory:")
    yield client


class FakeLLMClient:
    """Scripted LLM client injected into AssemblyLine — no network.

    Pass a list of return values (str) or a single callable; each call to
    ``complete`` pops the next scripted response. A scripted ``Exception``
    instance is raised instead of returned.
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, model: str, prompt: str, *, max_tokens: int = 512) -> str:
        self.calls.append((model, prompt))
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture()
def test_config() -> Any:
    from prism.config import Config

    return Config(
        backend="anthropic",
        anthropic_api_key="test-key",
        claude_base_url="",
        triage_model="triage-model",
        diagnose_model="diagnose-model",
        rca_model="rca-model",
        genome_path=":memory:",
        severity_threshold=5,
        lmstudio_base_url="http://localhost:1234/v1",
        claude_cli_path="claude",
        claude_cli_timeout=120.0,
    )

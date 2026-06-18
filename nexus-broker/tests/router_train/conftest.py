"""Seeded fixtures for broker.router_train tests.

A clean v2 capture record paired with a dispatch-sidecar row, plus quarantine
variants (router_version=="buggy", dispatched_persona=="general-purpose") so the
labeler's drop rules are exercised against real row shapes. Records carry the
model's guess under the role-based ``pred_persona`` key (the post-rename write
shape); ``legacy_qwen_record`` carries the old ``qwen_persona`` key to exercise
normalize-on-read back-compat.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest


def _hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


CLEAN_PROMPT = "Implement the dispatch sidecar hook and wire it into settings.json"
BUGGY_PROMPT = "Captured while OPT-001 blank-persona bug was live"
GP_PROMPT = "A prompt the orchestrator handled itself with no Nexus persona"


@pytest.fixture
def clean_record() -> dict[str, Any]:
    return {
        "session_id": "sess-clean",
        "prompt": CLEAN_PROMPT,
        "prompt_hash": _hash(CLEAN_PROMPT),
        "decision": "prefill",
        "latency_ms": 1234.5,
        "timestamp": "2026-06-03T10:00:00+00:00",
        "pred_persona": "pipeline-data",
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
        "messages": [
            {"role": "system", "content": "router system prompt"},
            {"role": "user", "content": CLEAN_PROMPT},
        ],
        "system_prompt_sha256": _hash("router system prompt"),
        "router_code_sha": "abc1234",
        "source_project": str(Path.home() / "nexus-installer"),
    }


@pytest.fixture
def legacy_qwen_record() -> dict[str, Any]:
    """A pre-rename v2 capture carrying the OLD qwen_persona key (back-compat)."""
    return {
        "session_id": "sess-legacy",
        "prompt": CLEAN_PROMPT,
        "prompt_hash": _hash(CLEAN_PROMPT),
        "decision": "prefill",
        "latency_ms": 1234.5,
        "timestamp": "2026-06-03T10:00:00+00:00",
        "qwen_persona": "pipeline-data",
        "schema_version": 1,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
    }


@pytest.fixture
def clean_dispatch() -> dict[str, Any]:
    return {
        "session_id": "sess-clean",
        "prompt_hash": _hash(CLEAN_PROMPT),
        "dispatched_persona": "pipeline-data",
        "ts": "2026-06-03T10:00:05+00:00",
    }


@pytest.fixture
def buggy_record() -> dict[str, Any]:
    return {
        "session_id": "sess-buggy",
        "prompt": BUGGY_PROMPT,
        "prompt_hash": _hash(BUGGY_PROMPT),
        "decision": "prefill",
        "timestamp": "2026-06-03T11:00:00+00:00",
        "pred_persona": "scout",
        "schema_version": 2,
        "router_version": "buggy",
        "model_id": "granite-4.1-3b",
    }


@pytest.fixture
def buggy_dispatch() -> dict[str, Any]:
    return {
        "session_id": "sess-buggy",
        "prompt_hash": _hash(BUGGY_PROMPT),
        "dispatched_persona": "scout",
        "ts": "2026-06-03T11:00:05+00:00",
    }


@pytest.fixture
def general_purpose_record() -> dict[str, Any]:
    return {
        "session_id": "sess-gp",
        "prompt": GP_PROMPT,
        "prompt_hash": _hash(GP_PROMPT),
        "decision": "fallthrough",
        "timestamp": "2026-06-03T12:00:00+00:00",
        "schema_version": 2,
        "router_version": "fixed",
        "model_id": "granite-4.1-3b",
    }


@pytest.fixture
def general_purpose_dispatch() -> dict[str, Any]:
    return {
        "session_id": "sess-gp",
        "prompt_hash": _hash(GP_PROMPT),
        "dispatched_persona": "general-purpose",
        "ts": "2026-06-03T12:00:05+00:00",
    }

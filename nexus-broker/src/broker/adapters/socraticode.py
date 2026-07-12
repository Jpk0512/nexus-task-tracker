"""socraticode adapter — R5-T03 N51 (plans/15-r5-dag.yaml).

CLI-wrapper adapter (per the proposal's Phase 5 mitigation: "CLI wrappers
where MCP client support is not worth building yet"). SocratiCode ships as an
external MCP server with no in-repo Python client — building a full stdio-MCP
handshake client is out of this node's scope; the wrapped command resolves via
`NEXUS_SOCRATICODE_CMD` (shlex-split), defaulting to the real `socraticode`
binary name. This env seam is also exactly what a future stdio-MCP subprocess
client would replace, without changing this module's public surface (same
four-function shape either way — see `base.resolve_argv`'s docstring).
"""
from __future__ import annotations

from typing import Any

from broker.adapters.base import (
    CapabilityInfo,
    ErrorPacket,
    OkPacket,
    PolicyDecision,
    evaluate_policy,
    make_error,
    resolve_argv,
    run_cli_capability,
)

ADAPTER_NAME = "socraticode"
DEFAULT_TIMEOUT_S = 10.0

_COMMAND_ENV = "NEXUS_SOCRATICODE_CMD"
_DEFAULT_ARGV: tuple[str, ...] = ("socraticode",)

_SCHEMAS: dict[str, dict[str, Any]] = {
    "code_search": {
        "input": {"query": "str", "project": "str (optional project id/path)"},
        "output": {"hits": "list[dict]"},
    },
    "codebase_symbol": {
        "input": {"name": "str", "project": "str (optional)"},
        "output": {"symbol": "dict | None"},
    },
}

_CAPABILITIES: tuple[CapabilityInfo, ...] = tuple(
    CapabilityInfo(id=capability_id, adapter=ADAPTER_NAME, kind="cli_wrapper", description=desc)
    for capability_id, desc in (
        ("code_search", "Semantic code search over an indexed project"),
        ("codebase_symbol", "Exact symbol lookup by name"),
    )
)


def capabilities() -> tuple[CapabilityInfo, ...]:
    return _CAPABILITIES


def schema(capability_id: str) -> dict[str, Any]:
    if capability_id not in _SCHEMAS:
        raise KeyError(f"socraticode adapter has no capability {capability_id!r}")
    return _SCHEMAS[capability_id]


def policy(
    capability_id: str,
    project_profile: dict[str, Any] | None,
    persona_contract: dict[str, Any] | None,
) -> PolicyDecision:
    return evaluate_policy(
        adapter=ADAPTER_NAME,
        capability_id=capability_id,
        project_profile=project_profile,
        persona_contract=persona_contract,
    )


def invoke(
    capability_id: str,
    args: dict[str, Any] | None = None,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ErrorPacket | OkPacket:
    if capability_id not in _SCHEMAS:
        return make_error(
            ADAPTER_NAME,
            capability_id,
            "unknown_capability",
            f"no such capability: {capability_id!r}",
        )
    argv = [*resolve_argv(_COMMAND_ENV, _DEFAULT_ARGV), capability_id]
    return run_cli_capability(
        adapter=ADAPTER_NAME,
        capability_id=capability_id,
        argv=argv,
        input_payload=args or {},
        timeout_s=timeout_s,
    )

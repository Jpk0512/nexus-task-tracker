"""lsp_py adapter — R5-T03 N51 (plans/15-r5-dag.yaml).

CLI-wrapper adapter, same rationale as `socraticode.py`: a Python language-
server (`lsp-py`) is an external process with no in-repo client today, so this
node wraps it via subprocess rather than building a stdio-MCP/LSP-JSON-RPC
client. The wrapped command resolves via `NEXUS_LSP_PY_CMD` (shlex-split),
defaulting to the real `lsp-py` binary name — see `base.resolve_argv`'s
docstring for why this env seam is also the future stdio-MCP-client swap
point.
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

ADAPTER_NAME = "lsp_py"
DEFAULT_TIMEOUT_S = 10.0

_COMMAND_ENV = "NEXUS_LSP_PY_CMD"
_DEFAULT_ARGV: tuple[str, ...] = ("lsp-py",)

_SCHEMAS: dict[str, dict[str, Any]] = {
    "type_reference_lookup": {
        "input": {"symbol": "str", "project": "str (optional project id/path)"},
        "output": {"references": "list[dict]"},
    },
    "definition_lookup": {
        "input": {"symbol": "str", "project": "str (optional)"},
        "output": {"definition": "dict | None"},
    },
}

_CAPABILITIES: tuple[CapabilityInfo, ...] = tuple(
    CapabilityInfo(id=capability_id, adapter=ADAPTER_NAME, kind="cli_wrapper", description=desc)
    for capability_id, desc in (
        ("type_reference_lookup", "Find every type-exact reference to a symbol"),
        ("definition_lookup", "Jump to a symbol's definition"),
    )
)


def capabilities() -> tuple[CapabilityInfo, ...]:
    return _CAPABILITIES


def schema(capability_id: str) -> dict[str, Any]:
    if capability_id not in _SCHEMAS:
        raise KeyError(f"lsp_py adapter has no capability {capability_id!r}")
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

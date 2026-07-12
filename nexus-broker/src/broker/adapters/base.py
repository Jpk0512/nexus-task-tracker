"""Shared adapter primitives — R5-T03 N51 (plans/15-r5-dag.yaml).

Every broker MCP-adapter (`socraticode`, `vault`, `lsp_py` — this node) exposes
the SAME four-function surface the proposal names (Phase 5 / SS6):
`capabilities()`, `schema(capability_id)`, `invoke(capability_id, args)`,
`policy(capability_id, project_profile, persona_contract)`. This module holds
the pieces that would otherwise be copy-pasted three times over: the typed
result shapes, the fail-closed policy evaluator, and the subprocess/CLI-wrapper
runner every non-direct-import adapter (`socraticode`, `lsp_py`) shares.

Fail-closed, never-hang contract (the node's acceptance bar): `invoke()` on
every adapter NEVER raises and NEVER blocks past its timeout budget — a broken
adapter degrades to a typed error packet (`ok: False`, an `error_type`, a
`message`), mirroring the same "no silent degrade, no retry storm, bounded
wait" posture plans/10-broker-mcp-client-design.md §1 already requires of the
broker's own RPC client.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any, Literal, TypedDict


class CapabilityInfo(TypedDict):
    id: str
    adapter: str
    kind: Literal["direct_import", "cli_wrapper"]
    description: str


class ErrorPacket(TypedDict):
    ok: Literal[False]
    adapter: str
    capability_id: str
    error_type: str
    message: str


class OkPacket(TypedDict):
    ok: Literal[True]
    adapter: str
    capability_id: str
    result: Any


class PolicyDecision(TypedDict):
    allowed: bool
    adapter: str
    capability_id: str
    reason: str


def make_error(adapter: str, capability_id: str, error_type: str, message: str) -> ErrorPacket:
    return ErrorPacket(
        ok=False,
        adapter=adapter,
        capability_id=capability_id,
        error_type=error_type,
        message=message,
    )


def make_ok(adapter: str, capability_id: str, result: Any) -> OkPacket:
    return OkPacket(ok=True, adapter=adapter, capability_id=capability_id, result=result)


def evaluate_policy(
    *,
    adapter: str,
    capability_id: str,
    project_profile: dict[str, Any] | None,
    persona_contract: dict[str, Any] | None,
) -> PolicyDecision:
    """Fail-closed: a `persona_contract` that names no `allowed_capabilities`
    (or omits this capability from that list) is DENIED — there is no
    default-allow path, mirroring `broker.registry`'s ALLOWED_PERSONAS posture
    (a name must be explicitly enumerated to be legal, never legal by omission).
    """
    contract = persona_contract or {}
    profile = project_profile or {}

    allowed_capabilities = contract.get("allowed_capabilities")
    if not allowed_capabilities:
        return PolicyDecision(
            allowed=False,
            adapter=adapter,
            capability_id=capability_id,
            reason=(
                "persona_contract declares no allowed_capabilities — "
                "fail-closed default deny"
            ),
        )
    if capability_id not in allowed_capabilities:
        role_id = contract.get("role_id", "<unknown>")
        return PolicyDecision(
            allowed=False,
            adapter=adapter,
            capability_id=capability_id,
            reason=(
                f"role {role_id!r}'s persona_contract does not list "
                f"{capability_id!r} in allowed_capabilities"
            ),
        )

    disabled_adapters = profile.get("disabled_adapters") or ()
    if adapter in disabled_adapters:
        return PolicyDecision(
            allowed=False,
            adapter=adapter,
            capability_id=capability_id,
            reason=f"adapter {adapter!r} is disabled by project_profile",
        )

    return PolicyDecision(
        allowed=True, adapter=adapter, capability_id=capability_id, reason="allowed"
    )


def resolve_argv(env_var: str, default_argv: tuple[str, ...]) -> list[str]:
    """Resolve the base command argv for a CLI-wrapper adapter.

    Reads `env_var` (shlex-split, so a caller can point at a multi-word
    command like "python3 fixture.py") and falls back to `default_argv` — the
    real external binary name — when unset. This is the seam the proposal's
    mitigation names explicitly ("CLI wrappers where MCP client support is not
    worth building yet"); a future stdio-MCP subprocess client replaces only
    this resolution, not the adapter's public surface.
    """
    override = os.environ.get(env_var)
    if override:
        return shlex.split(override)
    return list(default_argv)


def run_cli_capability(
    *,
    adapter: str,
    capability_id: str,
    argv: list[str],
    input_payload: dict[str, Any] | None,
    timeout_s: float,
) -> ErrorPacket | OkPacket:
    """Invoke a CLI-wrapper capability: JSON payload on stdin, JSON result on
    stdout. Every failure mode degrades to a typed `ErrorPacket` — a missing
    binary, a non-zero exit, a hung process past `timeout_s`, or unparseable
    output — NEVER a raised exception and NEVER a hang past the budget.
    """
    try:
        proc = subprocess.run(  # noqa: S603 -- argv is adapter-resolved, never shell=True
            argv,
            input=json.dumps(input_payload or {}),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return make_error(
            adapter, capability_id, "timeout", f"{argv[0]} exceeded {timeout_s}s budget"
        )
    except FileNotFoundError:
        return make_error(adapter, capability_id, "unavailable", f"{argv[0]!r} not found on PATH")
    except OSError as exc:
        return make_error(adapter, capability_id, "invoke_failed", str(exc))

    if proc.returncode != 0:
        detail = proc.stderr.strip()[:500] or f"rc={proc.returncode}"
        return make_error(adapter, capability_id, "invoke_failed", detail)

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return make_error(adapter, capability_id, "invalid_response", proc.stdout.strip()[:500])

    return make_ok(adapter, capability_id, result)

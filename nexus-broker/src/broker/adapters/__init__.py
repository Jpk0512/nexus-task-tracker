"""broker.adapters — external-MCP adapter layer, R5-T03 N51 (plans/15-r5-dag.yaml).

Three adapters, one shared four-function surface (`capabilities()`,
`schema(capability_id)`, `invoke(capability_id, args)`,
`policy(capability_id, project_profile, persona_contract)` — proposal
Phase 5 / SS6):

  - `vault`       direct import (`broker.vault.search`/`graph`) — in-repo, no subprocess.
  - `socraticode` CLI wrapper (subprocess) — see socraticode.py's docstring.
  - `lsp_py`      CLI wrapper (subprocess) — see lsp_py.py's docstring.

This node builds and tests the adapters ONLY. `all_capabilities()`/`invoke()`/
`policy()` below are the aggregate shape a future `nexus_discover`/`nexus_run`
would route through — wiring that into server.py/discovery.py is N52's
(do_not_touch here, per the node spec's config-surface separation).
"""
from __future__ import annotations

from typing import Any

from broker.adapters import lsp_py, socraticode, vault
from broker.adapters.base import CapabilityInfo, ErrorPacket, OkPacket, PolicyDecision, make_error

_ADAPTERS: dict[str, Any] = {
    "vault": vault,
    "socraticode": socraticode,
    "lsp_py": lsp_py,
}


def all_capabilities() -> tuple[CapabilityInfo, ...]:
    """The aggregate capability list a future `nexus_discover` would surface."""
    caps: list[CapabilityInfo] = []
    for mod in _ADAPTERS.values():
        caps.extend(mod.capabilities())
    return tuple(caps)


def invoke(
    adapter: str,
    capability_id: str,
    args: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ErrorPacket | OkPacket:
    """The aggregate invoke a future `nexus_run` would route
    `adapter`.`capability_id` through."""
    mod = _ADAPTERS.get(adapter)
    if mod is None:
        return make_error(adapter, capability_id, "unknown_adapter", f"no such adapter: {adapter!r}")
    return mod.invoke(capability_id, args, **kwargs)


def policy(
    adapter: str,
    capability_id: str,
    project_profile: dict[str, Any] | None,
    persona_contract: dict[str, Any] | None,
) -> PolicyDecision:
    mod = _ADAPTERS.get(adapter)
    if mod is None:
        return PolicyDecision(
            allowed=False,
            adapter=adapter,
            capability_id=capability_id,
            reason=f"unknown adapter: {adapter!r}",
        )
    return mod.policy(capability_id, project_profile, persona_contract)


__all__ = ["all_capabilities", "invoke", "lsp_py", "policy", "socraticode", "vault"]

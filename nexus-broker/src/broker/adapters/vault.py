"""vault adapter — R5-T03 N51 (plans/15-r5-dag.yaml).

Direct-import adapter (per the proposal's Phase 5 framing: "importing local
implementation directly when in-repo"). No subprocess, no MCP client — this
module calls straight into `broker.vault.search`/`broker.vault.graph`, the SAME
implementations `broker.vault.stdio`'s FastMCP app already registers as tools
(`vault_query`, `vault_health`). Adding this adapter changes nothing about how
those functions behave; it gives the broker a second, adapter-shaped caller of
the same code. Wiring this into the actual `nexus_discover`/`nexus_run` tools
is N52's (do_not_touch: server.py/discovery.py here).

Per-call timeout: `vault_query_impl`/`vault_health_impl` are async; `invoke()`
wraps the call in `asyncio.wait_for` so a wedged read degrades to a typed
error packet exactly like the CLI-wrapper adapters' subprocess timeout, never
a hang past `timeout_s`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from broker.adapters.base import (
    CapabilityInfo,
    ErrorPacket,
    OkPacket,
    PolicyDecision,
    evaluate_policy,
    make_error,
    make_ok,
)
from broker.vault._server import AppConfig, build_config
from broker.vault.graph import vault_health_impl
from broker.vault.search import vault_query_impl

ADAPTER_NAME = "vault"
DEFAULT_TIMEOUT_S = 10.0

_SCHEMAS: dict[str, dict[str, Any]] = {
    "vault_query": {
        "input": {
            "query": "str | None — omit for list-recent mode",
            "filters": "dict (optional): domain, kind, min_confidence, exclude_maturity",
            "order_by": "str (optional): 'recent'",
            "mode": "str (optional, default 'fast')",
            "limit": "int (optional, default 10)",
            "vault_root": "str (optional config override)",
            "db_path": "str (optional config override)",
            "access_mode": "str (optional config override, default 'local_stdio')",
        },
        "output": {"hits": "list[dict]", "mode": "str", "fenced": "bool", "count": "int"},
    },
    "vault_health": {
        "input": {
            "vault_root": "str (optional config override)",
            "db_path": "str (optional config override)",
            "access_mode": "str (optional config override, default 'local_stdio')",
        },
        "output": {"file_counts": "dict", "recall_disabled": "bool", "eval": "dict"},
    },
}

_CAPABILITIES: tuple[CapabilityInfo, ...] = tuple(
    CapabilityInfo(id=capability_id, adapter=ADAPTER_NAME, kind="direct_import", description=desc)
    for capability_id, desc in (
        ("vault_query", "Search/list vault notes (dense sqlite-vec search or list-recent)"),
        ("vault_health", "Vault health/status snapshot (file counts, recall B4 state)"),
    )
)


def capabilities() -> tuple[CapabilityInfo, ...]:
    return _CAPABILITIES


def schema(capability_id: str) -> dict[str, Any]:
    if capability_id not in _SCHEMAS:
        raise KeyError(f"vault adapter has no capability {capability_id!r}")
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


def _build_config(call_args: dict[str, Any]) -> AppConfig:
    """Pop the 3 config-override keys out of `call_args` in place, so the
    remainder forwards cleanly to the wrapped impl as its own kwargs — this is
    how a caller overrides WHICH vault an invocation reads, without widening
    the spec's 2-arg `invoke(capability_id, args)` shape with a 3rd
    `project_profile` parameter. Left unset, the impl's own env-var defaults
    (NEXUS_VAULT_ROOT / NEXUS_VAULT_DB) apply.
    """
    vault_root = call_args.pop("vault_root", None)
    db_path = call_args.pop("db_path", None)
    access_mode = call_args.pop("access_mode", "local_stdio")
    return build_config(
        access_mode=access_mode,
        vault_root=Path(vault_root) if vault_root else None,
        db_path=Path(db_path) if db_path else None,
    )


async def _dispatch(capability_id: str, config: AppConfig, args: dict[str, Any]) -> Any:
    if capability_id == "vault_query":
        return await vault_query_impl(
            config=config,
            filters=args.get("filters") or {},
            query=args.get("query"),
            order_by=args.get("order_by"),
            mode=args.get("mode", "fast"),
            limit=int(args.get("limit", 10)),
        )
    if capability_id == "vault_health":
        return await vault_health_impl(config=config)
    raise KeyError(capability_id)


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

    call_args = dict(args or {})
    config = _build_config(call_args)
    try:
        result = asyncio.run(
            asyncio.wait_for(_dispatch(capability_id, config, call_args), timeout=timeout_s)
        )
    except TimeoutError:
        return make_error(
            ADAPTER_NAME, capability_id, "timeout", f"{capability_id} exceeded {timeout_s}s budget"
        )
    except Exception as exc:  # noqa: BLE001 -- any adapter-internal failure degrades to a typed packet
        return make_error(ADAPTER_NAME, capability_id, "invoke_failed", str(exc))
    return make_ok(ADAPTER_NAME, capability_id, result)

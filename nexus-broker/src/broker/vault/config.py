"""YAML config loaders for broker.vault.

Currently scoped to the distill router (`distill-routing.yaml`). Kept separate
from `policy.py` (privacy rules) so router config has its own load path and
can be reloaded independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TierConfig:
    backend: str
    endpoint: str | None = None
    chat_model_env: str | None = None
    chat_model_default: str | None = None
    skip_model_patterns: tuple[str, ...] = ()
    binary: str | None = None
    model: str | None = None
    timeout_s: int = 60


@dataclass(frozen=True)
class RoutingConfig:
    version: int
    tiers: dict[str, TierConfig]
    default_routes: dict[str, str]
    by_kind: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve_tier(self, task: str, kind: str = "default") -> str:
        """Return the tier name ('local' | 'judgment') for (kind, task).

        Per-kind overrides win, with `all_stages` acting as a kind-wide override.
        Falls back to `default_routes[task]`; raises KeyError if unknown.
        """
        overrides = self.by_kind.get(kind, {})
        if "all_stages" in overrides:
            return overrides["all_stages"]
        if task in overrides:
            return overrides[task]
        if task in self.default_routes:
            return self.default_routes[task]
        raise KeyError(f"no route configured for task={task!r} kind={kind!r}")


def load_routing_config(path: Path) -> RoutingConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    tiers_raw = raw.get("tiers", {})
    tiers: dict[str, TierConfig] = {}
    for name, t in tiers_raw.items():
        tiers[name] = TierConfig(
            backend=t["backend"],
            endpoint=t.get("endpoint"),
            chat_model_env=t.get("chat_model_env"),
            chat_model_default=t.get("chat_model_default"),
            skip_model_patterns=tuple(t.get("skip_model_patterns", []) or ()),
            binary=t.get("binary"),
            model=t.get("model"),
            timeout_s=int(t.get("timeout_s", 60)),
        )
    routing = raw.get("routing", {})
    return RoutingConfig(
        version=int(raw.get("version", 1)),
        tiers=tiers,
        default_routes=dict(routing.get("default", {})),
        by_kind={k: dict(v) for k, v in (routing.get("by_kind", {}) or {}).items()},
    )

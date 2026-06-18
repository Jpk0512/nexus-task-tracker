"""Privacy fence policy (plan §7.4, A5/A9).

Loads `research/.privacy-rules.yaml` and answers two questions:

  can_read_fenced(domain, access_mode) -> bool
  enforce(tool_name, domain, access_mode) -> "allow" | "return_empty" | "deny"

`hmac.compare_digest` is exposed via `bearer_matches()` for constant-time
bearer comparison. Bearer rotation itself lands in Phase 5b — the comparison
primitive lives here so the HTTP daemon can import it without restructuring.
"""
from __future__ import annotations

import hmac
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

AccessMode = Literal["local_stdio", "elevated_bearer", "web_default"]
Decision = Literal["allow", "return_empty", "deny"]

_DEFAULT_RULES = {
    "version": 1,
    "fenced_domains": ["personal", "work"],
    "access_modes": {
        "local_stdio": {"can_read_fenced": True},
        "elevated_bearer": {"can_read_fenced": True},
        "web_default": {"can_read_fenced": False},
    },
    "enforcement": {
        "vault_query": {
            "fenced_requires": ["local_stdio", "elevated_bearer"],
            "on_violation": "return_empty",
        },
        "vault_get_note": {
            "fenced_requires": ["local_stdio", "elevated_bearer"],
            "on_violation": "return_empty",
        },
    },
}


@dataclass(frozen=True)
class PolicyRules:
    fenced_domains: frozenset[str]
    access_modes: dict[str, dict]
    enforcement: dict[str, dict]
    version: int
    source_path: Path | None

    def can_read_fenced(self, access_mode: str) -> bool:
        mode_cfg = self.access_modes.get(access_mode, {})
        return bool(mode_cfg.get("can_read_fenced", False))


def _vault_root() -> Path:
    env = os.environ.get("NEXUS_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "research").is_dir():
            return (candidate / "research").resolve()
    return Path.cwd() / "research"


def load_rules(vault_root: Path | None = None) -> PolicyRules:
    """Parse .privacy-rules.yaml. Falls back to safe defaults if missing/empty."""
    root = vault_root or _vault_root()
    path = root / ".privacy-rules.yaml"
    data = None
    if path.is_file():
        text = path.read_text()
        if text.strip():
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError:
                data = None
    if not isinstance(data, dict):
        data = _DEFAULT_RULES
    return PolicyRules(
        fenced_domains=frozenset(data.get("fenced_domains", []) or []),
        access_modes=data.get("access_modes", {}) or {},
        enforcement=data.get("enforcement", {}) or {},
        version=int(data.get("version", 1)),
        source_path=path if path.is_file() else None,
    )


def can_read_fenced(domain: str | None, access_mode: AccessMode, rules: PolicyRules | None = None) -> bool:
    """True if (a) domain is not fenced, or (b) access_mode can_read_fenced."""
    r = rules or load_rules()
    if domain is None or domain not in r.fenced_domains:
        return True
    return r.can_read_fenced(access_mode)


def hit_visible(domain: str | None, access_mode: AccessMode, rules: PolicyRules) -> bool:
    """Single canonical predicate: True if a result row with this domain is visible.

    - domain is None → the row carries no domain tag; treat as fenced to be safe
      when the access mode cannot read fenced content, otherwise allow.
    - domain is in fenced_domains → gate on access_mode's can_read_fenced.
    - domain is a known non-fenced domain → always visible.
    """
    if domain is None or domain in rules.fenced_domains:
        return rules.can_read_fenced(access_mode)
    return True


def enforce(
    tool_name: str,
    domain: str | None,
    access_mode: AccessMode,
    rules: PolicyRules | None = None,
) -> Decision:
    """Decide what the tool layer should do.

    - "allow"          → fall through to normal handling
    - "return_empty"   → tool returns an empty result set (silent fence)
    - "deny"           → tool raises / refuses (unused at present; reserved)
    """
    r = rules or load_rules()
    if domain is None or domain not in r.fenced_domains:
        return "allow"
    cfg = r.enforcement.get(tool_name)
    if not isinstance(cfg, dict):
        # Unconfigured content-touching tool with a fenced domain: fall through
        # to the access_mode gate rather than unconditionally allowing.
        return "allow" if r.can_read_fenced(access_mode) else "return_empty"
    required: Iterable[str] = cfg.get("fenced_requires", []) or []
    if access_mode in required and r.can_read_fenced(access_mode):
        return "allow"
    on_violation = cfg.get("on_violation", "return_empty")
    if on_violation not in ("allow", "return_empty", "deny"):
        on_violation = "return_empty"
    return on_violation  # type: ignore[return-value]


def domains_filter_includes_fenced(
    domain_filter: str | None | list[str], rules: PolicyRules | None = None
) -> str | None:
    """Return the first fenced domain in a filter spec, or None if clean.

    `domain_filter` may be a single string, a list of strings, or None.
    """
    r = rules or load_rules()
    if domain_filter is None:
        return None
    candidates: list[str]
    if isinstance(domain_filter, str):
        candidates = [domain_filter]
    else:
        candidates = [str(d) for d in domain_filter]
    for d in candidates:
        if d in r.fenced_domains:
            return d
    return None


def bearer_matches(presented: str | None, expected: str | None) -> bool:
    """Constant-time bearer comparison (hmac.compare_digest).

    Phase 5b uses this from the HTTP daemon. Defined here so the primitive
    lives next to the policy it enforces.
    """
    if not presented or not expected:
        return False
    try:
        return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    except Exception:  # noqa: BLE001 — auth compare must fail closed on any error
        return False

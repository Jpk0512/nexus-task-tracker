#!/usr/bin/env python3
"""Shared advisory (tranche-A) ping shim — F2-03 migration
(nexus-foundation/plans/artifacts/event-bus-design.md §2a, event-taxonomy.json).

Every tranche-A hook body shrinks to this one shared shim, invoked as:
    python3 _ping_shim.py <event.name> <consumer-id>

`<consumer-id>` is the migrated hook's own filename stem (e.g.
"memory-health-check") — `event.emit`'s `name` carries the BUS event (shared
by several consumers, e.g. 7 files all fire "session.start"); `consumer`
disambiguates which of those consumers' daemon-resident logic to run, so
each hook file's own advisory output/exit-code stays exactly what it was
before migration — never merged/duplicated across the shared event name.

FAIL OPEN, ALWAYS (event-bus-design.md §3, C-06): any RPC miss (dead daemon,
timeout, malformed reply, or a server-side error — e.g. an empty taxonomy on
a non-meta-repo tenant, see advisory_handlers.py module docstring) resolves
to `_daemon_rpc.call_advisory`'s `{"ok": True, "fail_open": True, ...}`
miss-shape, carrying no `advisory_context` — this shim degrades to "the
advisory silently did not fire" (worst case: a lost banner), never to a
blocked session. On a confirmed daemon accept, the shim relays exactly what
the daemon computed: an optional `stdout` (a hookSpecificOutput dict,
JSON-printed verbatim, or a raw string printed as-is — some pre-migration
hook bodies emitted plain text rather than the nested JSON envelope, and
parity means preserving that, not silently upgrading it), an optional
`stderr` human banner, and whatever `exit_code` the handler chose (0 for
every tranche-A consumer migrated so far).

Env forwarding: the hook bodies this shim replaces read `_HOOK_*`/`NEXUS_*`/
`LM_STUDIO_*` overrides (test isolation, model/endpoint overrides) directly
from their own process environment. The daemon is a separate, long-lived
process whose own environment snapshot predates any per-invocation override
a caller sets — so those variables are forwarded explicitly in the RPC
payload rather than assumed readable server-side. This is an allowlist by
prefix, not the full environment (avoids leaking unrelated secrets over the
local RPC).

3.9 IMPORT-SAFETY — live runtime is >=3.11 via `_py.sh`, but the package
twin (where wired) runs this file un-shimmed under ambient python3 (3.9). No
3.11-only idioms: no `datetime.UTC`, no def-time `X | None`, no
`match`/`case` (`from __future__ import annotations` keeps PEP-604 safe).

Per-consumer timeout (F2-03 REVISE, notepad F2-03 latent-defect gotcha):
`main()` resolves each consumer's RPC timeout via `_timeout_for()` rather
than always using the 50ms `NEXUS_PING_SHIM_TIMEOUT_S` default — a consumer
whose daemon-resident handler performs real I/O past that budget (e.g.
router-health-check's LM Studio probe) gets a wider, still-overridable
window so the client doesn't give up before the daemon's real answer
arrives. See `_CONSUMER_TIMEOUT_DEFAULT_S` below.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent

_DEFAULT_TIMEOUT_S = float(os.environ.get("NEXUS_PING_SHIM_TIMEOUT_S", "0.05"))

# Per-consumer timeout overrides (NEXUS_PING_SHIM_TIMEOUT_S lineage) — the
# global 50ms default assumes the daemon-resident handler is pure in-memory
# compute. A consumer whose handler performs real I/O (e.g.
# handle_router_health_check's `_http_get_json(lm_url, timeout=3)` LM Studio
# probe) needs headroom past that I/O's own timeout, or the shim gives up
# and reports a miss (fail-open: the banner is silently lost) WHILE the
# daemon is still computing the real answer underneath it — the exact
# latent defect notepad gotcha F2-03 flags as already-merged. Each override
# is itself env-tunable, suffixed with the consumer id (hyphens -> '_',
# upper-cased): NEXUS_PING_SHIM_TIMEOUT_S__ROUTER_HEALTH_CHECK. Baked
# default is sized past the daemon handler's own 3s I/O timeout plus RPC
# round-trip overhead. This does not change how long the daemon itself may
# block computing the answer (that is bounded by the handler's own I/O
# timeout, unchanged by this shim) — it only stops the CLIENT from giving
# up before a slow-but-eventually-successful daemon answer arrives.
_CONSUMER_TIMEOUT_DEFAULT_S = {
    "router-health-check": 3.5,
}

_FORWARD_ENV_PREFIXES = ("_HOOK_", "NEXUS_", "LM_STUDIO_")
_FORWARD_ENV_EXACT = ("REPO_ROOT",)


def _timeout_for(consumer: str) -> float:
    baked_default = _CONSUMER_TIMEOUT_DEFAULT_S.get(consumer)
    if baked_default is None:
        return _DEFAULT_TIMEOUT_S
    env_key = "NEXUS_PING_SHIM_TIMEOUT_S__" + consumer.upper().replace("-", "_")
    return float(os.environ.get(env_key, str(baked_default)))


def _repo_root() -> Path:
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _load_daemon_rpc():
    spec = importlib.util.spec_from_file_location("_daemon_rpc", HOOKS_DIR / "_daemon_rpc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_stdin_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _forwarded_env() -> dict:
    out = {}
    for key, val in os.environ.items():
        if key in _FORWARD_ENV_EXACT or key.startswith(_FORWARD_ENV_PREFIXES):
            out[key] = val
    return out


def ping(event_name: str, consumer: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
    payload = _read_stdin_payload()
    root = _repo_root()
    try:
        rpc = _load_daemon_rpc()
        result = rpc.call_advisory(
            root,
            "event.emit",
            {"name": event_name, "consumer": consumer, "payload": payload, "env": _forwarded_env()},
            timeout_s,
        )
    except Exception:
        result = None

    advisory_context = result.get("advisory_context") if isinstance(result, dict) else None
    if not isinstance(advisory_context, dict):
        sys.exit(0)  # miss (fail open) or no output computed — never blocks, never raises

    stderr_text = advisory_context.get("stderr")
    if isinstance(stderr_text, str) and stderr_text:
        print(stderr_text, file=sys.stderr)

    stdout_val = advisory_context.get("stdout")
    if isinstance(stdout_val, dict):
        print(json.dumps(stdout_val))
    elif isinstance(stdout_val, str) and stdout_val:
        print(stdout_val)

    exit_code = advisory_context.get("exit_code", 0)
    sys.exit(exit_code if isinstance(exit_code, int) else 0)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(0)  # malformed invocation — nothing to ping, fail open
    event_name, consumer = sys.argv[1], sys.argv[2]
    ping(event_name, consumer, timeout_s=_timeout_for(consumer))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

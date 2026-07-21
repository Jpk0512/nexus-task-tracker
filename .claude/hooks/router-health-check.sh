#!/usr/bin/env bash
# SessionStart hook — F2-03 migrated to the shared advisory ping shim
# (_ping_shim.py). The LM Studio reachability + model-presence probe this
# file used to run now lives daemon-resident in nexus-broker/src/broker/
# daemon/advisory_handlers.py:handle_router_health_check. This package twin
# carried a genuine install-specific divergence from the meta-repo
# pre-migration body (deriving the /v1/models probe URL from router_core.py's
# actual configured chat-completions endpoint, _HOOK_ROUTER_URL /
# _HOOK_QWEN_URL, rather than a hardcoded localhost default) — PORTED into
# the shared handler (it now applies to both tenants; LM_STUDIO_MODELS_URL
# stays the explicit override with highest precedence) rather than swapped
# away, proven by nexus-foundation/tools/hook_parity.sh --tranche A before
# this body was deleted. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "session.start" "router-health-check"

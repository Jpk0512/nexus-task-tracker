#!/usr/bin/env bash
# SessionStart hook — F2-03 migrated to the shared advisory ping shim
# (_ping_shim.py). The version + health-summary banner this file used to
# compute now lives daemon-resident in nexus-broker/src/broker/daemon/
# advisory_handlers.py:handle_health_banner. This package twin carried a
# genuine install-specific divergence from the meta-repo pre-migration body
# (a NATIVE-58 writability preflight, +39 lines) — PORTED into the shared
# handler (it now runs for both tenants) rather than swapped away, proven by
# nexus-foundation/tools/hook_parity.sh --tranche A before this body was
# deleted. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "session.start" "health-banner"

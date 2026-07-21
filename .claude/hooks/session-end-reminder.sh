#!/usr/bin/env bash
# Stop hook companion — F2-03 migrated to the shared advisory ping shim
# (_ping_shim.py). The open-session / non-trivial-activity reminder this
# file used to compute now lives daemon-resident in nexus-broker/src/broker/
# daemon/advisory_handlers.py:handle_session_end_reminder. This package twin
# carried a genuine install-specific divergence from the meta-repo
# pre-migration body (an un-rendered /Users/john.keeney/nexus-task-tracker install-token detector
# that emits a LOUD systemMessage instead of silently going inert, +37 lines)
# — PORTED into the shared handler (it now applies to both tenants; the
# meta-repo's own _HOOK_DB_PATH is never an install-token literal, so this is
# a no-op there) rather than swapped away, proven by
# nexus-foundation/tools/hook_parity.sh --tranche A before this body was
# deleted. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "session.stop" "session-end-reminder"

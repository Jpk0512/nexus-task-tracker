#!/usr/bin/env bash
# PreToolUse hook (matcher: Task; dispatch.pre.observe) — F2-03 migrated to
# the shared advisory ping shim (_ping_shim.py). The persona/goal
# announcement banner this file used to compute now lives daemon-resident
# in nexus-broker/src/broker/daemon/advisory_handlers.py:
# handle_dispatch_announce. Package twin migrated under DEC-085 (bundled
# taxonomy default) — this hook's own body was byte-for-byte identical to
# the meta-repo's pre-migration copy, so no separate port was needed,
# proven by nexus-foundation/tools/hook_parity.sh --tranche A before this
# body was deleted. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "dispatch.pre.observe" "dispatch-announce"

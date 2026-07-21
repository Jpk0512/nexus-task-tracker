#!/usr/bin/env bash
# PostToolUse hook (NH-1) — F2-03 migrated to the shared advisory ping shim
# (_ping_shim.py). The Read/Task prompt-injection content scan this file
# used to compute now lives daemon-resident in
# nexus-broker/src/broker/daemon/advisory_handlers.py:
# handle_read_injection_scanner — ported string-for-string (including the
# ground-truthed jq RAW_RESPONSE precedence/crash quirk — see that module's
# docstring), proven by nexus-foundation/tools/hook_parity.sh --tranche A
# before this body was deleted. Package twin migrated under DEC-085 (bundled
# taxonomy default) — this hook's body differed from the meta-repo's
# pre-migration copy by exactly one allowlist path segment
# (research/35-ai-techniques/nexus-package-audit/ vs the meta-repo's
# research/30-projects/plexus/nexus-package-audit/), which the shared
# handler now allowlists BOTH of, so one handler serves both tenants
# correctly. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "read.completed" "read-injection-scanner"

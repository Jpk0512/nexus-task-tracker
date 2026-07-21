#!/usr/bin/env bash
# PostToolUse hook (OD-7) — F2-03 migrated to the shared advisory ping shim
# (_ping_shim.py). The consecutive-read-without-action counter (session-
# scoped, $TMPDIR-backed) this file used to compute now lives daemon-resident
# in nexus-broker/src/broker/daemon/advisory_handlers.py:
# handle_analysis_paralysis_guard — ported string-for-string, proven by
# nexus-foundation/tools/hook_parity.sh --tranche A before this body was
# deleted. Package twin migrated under DEC-085 (bundled taxonomy default) —
# this hook's own body was byte-for-byte identical to the meta-repo's
# pre-migration copy, so no separate port was needed. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "read.completed" "analysis-paralysis-guard"

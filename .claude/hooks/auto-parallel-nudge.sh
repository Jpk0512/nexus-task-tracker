#!/usr/bin/env bash
# UserPromptSubmit hook (prompt.submitted) — F2-03 migrated to the shared
# advisory ping shim (_ping_shim.py). The delegation-work classifier this
# file used to compute now lives daemon-resident in nexus-broker/src/broker/
# daemon/advisory_handlers.py:handle_auto_parallel_nudge. This package twin
# carried a genuine install-specific divergence from the meta-repo
# pre-migration body (the nudge banner text omitted the meta-repo's internal
# DEC-017 decision-record citation — an installed tenant has no DEC-017 to
# look up) — PORTED into the shared handler as a real tenant branch (keyed
# off the same meta-repo-tenant signal event_bus.py's taxonomy_path_for
# already uses, DEC-085) rather than merged into one universal text, proven
# by nexus-foundation/tools/hook_parity.sh --tranche A before this body was
# deleted. Advisory / fail OPEN.
exec python3 "$(dirname "$0")/_ping_shim.py" "prompt.submitted" "auto-parallel-nudge"

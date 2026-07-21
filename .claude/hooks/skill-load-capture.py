#!/usr/bin/env python3
"""PostToolUse hook (matcher: Skill) — F2-03 migrated to the shared advisory
ping shim (`_ping_shim.py`). The daemon-resident behavior this file used to
contain (event-sourced `skill_load_events` capture, R2-T15) now lives in
`nexus-broker/src/broker/daemon/advisory_handlers.py:handle_skill_loaded` —
ported string-for-string (same field extraction, same `log.py skill
record-load` call), proven by `nexus-foundation/tools/hook_parity.sh
--tranche A` before this body was deleted. Package twin migrated under
DEC-085 (bundled taxonomy default) — this hook's own body was byte-for-byte
identical to the meta-repo's pre-migration copy (docstring path references
aside), so no separate port was needed.

ADVISORY / FAIL OPEN (event-bus-design.md §3, C-06): a dead/unreachable
daemon means the capture row is simply not written this turn — never a
blocked Skill call. See `_ping_shim.py` for the miss contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ping_shim  # noqa: E402

if __name__ == "__main__":
    _ping_shim.ping("skill.loaded", "skill-load-capture")

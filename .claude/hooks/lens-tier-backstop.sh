#!/usr/bin/env python3
"""lens-tier-backstop.sh — Stop hook — F2-03 migrated to the shared advisory
ping shim (_ping_shim.py). The N-distinct-lens-row session-end backstop this
file used to compute now lives daemon-resident in nexus-broker/src/broker/
daemon/advisory_handlers.py:handle_lens_tier_backstop. This package twin was
diffed against the meta-repo pre-migration body (git show 8164bc0) before
swapping: the differences were docstring trims + 3.9-safe f-string-to-
concatenation rewrites and the daemon-resident handler's own REDESIGN_MODE
carve-out being ABSENT here (a feature gap, not install-specific logic this
package twin uniquely needed) — no genuine divergent logic to port, so the
shared handler (a strict superset) applies unchanged to this tenant, proven
by nexus-foundation/tools/hook_parity.sh --tranche A before this body was
deleted. Advisory / fail OPEN (ADVISORY ONLY, exit 0 always — see the
pre-migration docstring this module's daemon handler preserves).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ping_shim  # noqa: E402

if __name__ == "__main__":
    _ping_shim.ping("session.stop", "lens-tier-backstop")

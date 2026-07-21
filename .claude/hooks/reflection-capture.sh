#!/usr/bin/env python3
"""PostToolUse hook (write.post.observe) — F2-03 migrated to the shared
advisory ping shim (`_ping_shim.py`). The doc-critical-edit snapshot capture
(watched-pattern match, diff summary, `reflection_snapshot.jsonl` append)
this file used to compute now lives daemon-resident in
`nexus-broker/src/broker/daemon/advisory_handlers.py:handle_reflection_capture`
— including this tenant's OWN naive set-based diff algorithm (a real, not
cosmetic, divergence from the meta-repo body's Counter-based multiset fix —
preserved as its own tenant branch, not merged away) — proven by
`nexus-foundation/tools/hook_parity.sh --tranche A` before this body was
deleted.

The unrendered-`/Users/john.keeney/nexus-task-tracker`-token loud banner below is REAL
install-divergent logic that stays local: the daemon path can't hit this
failure mode (`project_path` is always the daemon's real root, never a
literal token), so it is checked here, BEFORE ever reaching the shim.

ADVISORY / FAIL OPEN (event-bus-design.md §3, C-06): once rendered, a
dead/unreachable daemon means the snapshot is simply not recorded this
turn — never a blocked edit. See `_ping_shim.py` for the miss contract.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Install-time substitution renders /Users/john.keeney/nexus-task-tracker. Tests (and a runtime
# sanity check) can override via the _HOOK_INSTALL_ROOT env var. KEEP the
# literal /Users/john.keeney/nexus-task-tracker as the default so render_template still
# substitutes it.
REPO = os.environ.get("_HOOK_INSTALL_ROOT", "/Users/john.keeney/nexus-task-tracker")


def _emit_unrendered_warning() -> None:
    """The install-time /Users/john.keeney/nexus-task-tracker token was never rendered. This hook
    would otherwise silently no-op, so doc-critical edits would never be
    snapshotted. Fail SAFE (do not block the edit) but LOUD: emit a nested
    additionalContext warning naming the unrendered token so the
    orchestrator notices the hook is inert."""
    ctx = (
        "[reflection-capture] WARNING — the install-time /Users/john.keeney/nexus-task-tracker token was "
        "never rendered, so this PostToolUse hook cannot locate .memory/files/ and "
        "is silently NOT recording reflection snapshots of doc-critical edits. Re-run "
        "the Nexus install/render step (or set _HOOK_INSTALL_ROOT) to restore capture."
    )
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": ctx}},
        sys.stdout,
    )
    print(ctx, file=sys.stderr)


if __name__ == "__main__":
    if REPO.startswith("__") and REPO.endswith("__"):
        _emit_unrendered_warning()
        sys.exit(0)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _ping_shim

    _ping_shim.ping("write.post.observe", "reflection-capture")

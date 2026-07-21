#!/usr/bin/env python3
"""SessionStart hook — F2-03 migrated to the shared advisory ping shim
(`_ping_shim.py`). Note the filename is `.sh` but the interpreter is python
(settings.json invokes this file directly by path, relying on the shebang —
unchanged by this migration). The decisions-without-lessons query this file
used to run now lives daemon-resident in `nexus-broker/src/broker/daemon/
advisory_handlers.py:handle_lesson_harvester` — ported string-for-string,
proven by `nexus-foundation/tools/hook_parity.sh --tranche A` before this
body was deleted. Package twin migrated under DEC-085 (bundled taxonomy
default) — this hook's own body was byte-for-byte identical to the
meta-repo's pre-migration copy, so no separate port was needed. Advisory /
fail OPEN.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ping_shim  # noqa: E402

if __name__ == "__main__":
    _ping_shim.ping("session.start", "lesson-harvester")

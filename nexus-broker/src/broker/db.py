"""log.py subprocess wrapper — logs broker validation events to project.db."""
from __future__ import annotations

import contextlib
import subprocess

from broker.state import REPO_ROOT


def log_broker_validation(
    *,
    persona: str,
    intent: str,
    turn_id: str,
    router_pre_fill: str | None,
    approved: bool,
    errors: list[str],
) -> None:
    rationale = "; ".join(errors) if errors else "all checks passed"
    cmd = [
        "python3",
        str(REPO_ROOT / ".memory" / "log.py"),
        "context",
        "snapshot",
        "--action-type",
        "broker_validation",
        "--note",
        (
            f"persona={persona} intent={intent} turn_id={turn_id} "
            f"router_pre_fill={router_pre_fill} approved={approved} "
            f"rationale={rationale!r}"
        ),
    ]
    # Best-effort logging: never let a logging failure block the broker result.
    with contextlib.suppress(Exception):
        subprocess.run(cmd, capture_output=True, timeout=5, cwd=str(REPO_ROOT))

#!/usr/bin/env python3
"""PostToolUse hook (matcher: Write|Edit|MultiEdit) — F2-07 `doc.written`
advisory emit for the daemon-resident docs-watcher
(`nexus-broker/src/broker/daemon/docs_watcher.py`, event-taxonomy.json).

Fires the same shared unix-socket JSON-RPC transport every tranche-A hook
uses (`_daemon_rpc.py`'s `call_advisory`) — the design F2-03's shared
`_ping_shim.py` also builds on. This hook is written standalone rather than
delegating to `_ping_shim.py` because that shared shim does not exist on
this leaf's base commit (it lands via the parallel F2-03 worktree); the
RPC shape below (`event.emit`, `name`/`consumer`/`payload`/`env` params,
fail-OPEN on any miss) is the SAME contract `_ping_shim.py` implements, so
this file collapses into a one-line call to it once the two worktrees
merge — nothing here needs to change at that point, it just becomes
redundant with the shared shim.

FAIL OPEN, ALWAYS (event-bus-design.md §3, C-06): this hook NEVER blocks a
Write/Edit/MultiEdit. Any RPC miss (dead daemon, timeout, malformed reply)
is silently swallowed — worst case is a lost freshness banner, never a
denied write. `doc.written` is advisory-fail-open per event-taxonomy.json.

PATH FILTER — only `docs/**` and a small governance-basename allowlist
trigger the RPC at all; every other write exits 0 before touching the
daemon. This filter is a hand-kept duplicate of
`docs_watcher.is_governed_doc_path` — that module lives in the nexus-broker
venv this hook must not depend on (hooks run under ambient python3 / the
`_py.sh`-resolved >=3.11 interpreter, never the broker's own venv). Keep the
two definitions in sync by hand if either changes — the same dual-derivation
posture `_ping_shim.py`'s own docstring documents for `socket_path`.

3.9 IMPORT-SAFETY — live runtime is >=3.11 via `_py.sh`, but the package
twin (nexus-package/.claude/hooks/doc-write-capture.py) runs this file
un-shimmed under ambient python3 (3.9). No 3.11-only idioms: no
`datetime.UTC`, no def-time `X | None`, no `match`/`case` (`from __future__
import annotations` keeps PEP-604 annotations def-time-safe); keep
`timezone.utc` + `# noqa: UP017`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone  # noqa: UP017
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent

_DEFAULT_TIMEOUT_S = float(os.environ.get("NEXUS_DOC_WRITE_CAPTURE_TIMEOUT_S", "0.05"))

_GOVERNED_BASENAMES = {"CLAUDE.md", "DECISIONS.md", "TASKS.md", "INVARIANTS.md", "CONSTITUTION.md"}

_FORWARD_ENV_PREFIXES = ("_HOOK_", "NEXUS_", "LM_STUDIO_")
_FORWARD_ENV_EXACT = ("REPO_ROOT",)


def _repo_root() -> Path:
    override = os.environ.get("_HOOK_REPO_ROOT")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent


def _is_governed_doc_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lstrip("/")
    if normalized.startswith("docs/") or "/docs/" in normalized:
        return True
    return Path(normalized).name in _GOVERNED_BASENAMES


def _relative_file_path(root: Path, raw_path: str) -> str | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    try:
        if candidate.is_absolute():
            return str(candidate.resolve().relative_to(root.resolve()))
        return raw_path
    except ValueError:
        return None  # outside the repo tree entirely — nothing to watch


def _load_daemon_rpc():
    spec = importlib.util.spec_from_file_location("_daemon_rpc", HOOKS_DIR / "_daemon_rpc.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_stdin_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _forwarded_env() -> dict:
    out = {}
    for key, val in os.environ.items():
        if key in _FORWARD_ENV_EXACT or key.startswith(_FORWARD_ENV_PREFIXES):
            out[key] = val
    return out


def main() -> None:
    data = _read_stdin_payload()
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    raw_path = str(tool_input.get("file_path") or data.get("file_path") or "")

    root = _repo_root()
    rel_path = _relative_file_path(root, raw_path)
    if not rel_path or not _is_governed_doc_path(rel_path):
        sys.exit(0)  # not a governed doc — never touches the daemon

    session_id = str(data.get("session_id") or data.get("sessionId") or "unknown")
    author_persona = os.environ.get("_HOOK_PERSONA", "unknown")
    event_payload = {
        "session_id": session_id,
        "file_path": rel_path,
        "diff_summary": str(data.get("tool_name") or "write"),
        "author_persona": author_persona,
    }

    try:
        rpc = _load_daemon_rpc()
        result = rpc.call_advisory(
            root,
            "event.emit",
            {
                "name": "doc.written",
                "consumer": "docs-watcher",
                "payload": event_payload,
                "env": _forwarded_env(),
            },
            _DEFAULT_TIMEOUT_S,
        )
    except Exception:
        result = None  # fail OPEN — never blocks, never raises

    watcher_report = result.get("watcher_report") if isinstance(result, dict) else None
    if isinstance(watcher_report, dict) and watcher_report.get("flagged"):
        findings = watcher_report.get("findings") or []
        kinds = ", ".join(sorted({f.get("kind", "?") for f in findings if isinstance(f, dict)}))
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017
        print(
            f"[doc-write-capture] {ts} — {rel_path}: {len(findings)} finding(s) ({kinds}); "
            f"auto_fixed={watcher_report.get('auto_fixed')}",
            file=sys.stderr,
        )

    sys.exit(0)  # tranche-A advisory — always exit 0


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)

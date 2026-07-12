#!/usr/bin/env python3
# SubagentStop hook: enforces visual evidence for UI/API-touching NEXUS:DONE.
#
# Constitution Article XII (DEC-037): UI changes require before/after screenshot
# evidence; API changes require a real-boundary invocation result.
#
# Accountable skip: non-empty verification_result.visual_skip_reason => allow (exit 0).
# Profile-aware: reads .memory/nexus-stack.json for UI/API globs; falls back to
# framework-derived globs when stack file absent or visual_review keys not set.
#
# Exit codes:
#   0 = pass / skip (not DONE, no UI/API touched, evidence present, or skip_reason)
#   2 = deny (DONE + UI/API touched + no evidence + no skip_reason)
#
# Persona scope: code-writing implementers only (mirrors lens-gate.sh GATED_AGENTS).
# Orchestrator, scout, lens, lens-fast, palette are exempt.
#
# Fail-OPEN: any parse error, missing return, malformed JSON, missing nexus-stack.json
# => exit 0 (never block on gate internal error).
#
# 3.9-import-safe: no datetime.UTC, no def-time X|None, no match/case.

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path

# Load _gate_deny from the same hooks directory.
_gd_path = Path(__file__).parent / "_gate_deny.py"
_gd_spec = importlib.util.spec_from_file_location("_gate_deny", _gd_path)
_gate_deny_mod = importlib.util.module_from_spec(_gd_spec)  # type: ignore[arg-type]
_gd_spec.loader.exec_module(_gate_deny_mod)  # type: ignore[union-attr]

EVENT = "SubagentStop"

# Repo root: env seam for tests; /Users/john.keeney/nexus-task-tracker is substituted at install time.
REPO_ROOT = os.environ.get(
    "_HOOK_REPO_ROOT",
    "/Users/john.keeney/nexus-task-tracker",
)

# Code-writing implementer personas — same set as lens-gate.sh.
# Orchestrator, scout, lens, lens-fast, palette are exempt.
GATED_AGENTS = frozenset({
    "forge-ui",
    "forge-wire",
    "forge-ui-pro",
    "forge-wire-pro",
    "pipeline-data",
    "pipeline-async",
    "pipeline-data-pro",
    "pipeline-async-pro",
    "atlas",
    "hermes",
    "quill-ts",
    "quill-py",
})

MARKER_RE = re.compile(
    r"##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)", re.IGNORECASE
)

# Fallback UI glob prefixes per frontend.framework in nexus-stack.json.
_FRAMEWORK_UI_GLOBS = {
    "next": ["app/", "components/", "app/(routes)/"],
    "vite": ["src/"],
}

# Default API glob prefixes (always applied for API surface detection).
_DEFAULT_API_GLOBS = [
    "app/api/",
    "app/actions/",
    "routers/",
    "ingestion/src/",
]

# Matches absolute .png or .pdf paths in free text.
_SCREENSHOT_PATH_RE = re.compile(
    r'["\']?(/[^\s"\'<>,\]]+\.(?:png|pdf))["\']?', re.IGNORECASE
)

# Matches real-boundary API invocation evidence in free text.
_API_EVIDENCE_RE = re.compile(
    r"(HTTP/[12]|\"status\"\s*:|\bcurl\b|\baside\s+exec\b|\bdocker\s+exec\b"
    r"|\bHTTP\s+\d{3}\b|\d{3}\s+OK|\b2\d{2}\b.*\bOK\b)",
    re.IGNORECASE,
)


def _load_stack_globs() -> tuple[list[str], list[str]]:
    """Return (ui_globs, api_globs) from nexus-stack.json or framework fallback.

    Returns ([], []) for python-only / no-UI projects.
    Fail-open: any exception returns framework fallback, never raises.
    """
    stack: dict = {}
    stack_path = Path(REPO_ROOT) / ".memory" / "nexus-stack.json"
    try:
        raw = stack_path.read_text()
        stack = json.loads(raw)
    except Exception:
        pass

    vr = (stack.get("visual_review") or {})
    ui_explicit = vr.get("ui_globs")
    api_explicit = vr.get("api_globs")

    if isinstance(ui_explicit, list) and isinstance(api_explicit, list):
        return list(ui_explicit), list(api_explicit)

    # Derive from frontend.framework
    framework = str((stack.get("frontend") or {}).get("framework") or "")
    ui_globs = list(_FRAMEWORK_UI_GLOBS.get(framework, []))
    api_globs = list(_DEFAULT_API_GLOBS)
    return ui_globs, api_globs


def _parse_files_changed(text: str) -> list[str]:
    """Extract files_changed list from the first JSON block in the agent response."""
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        fc = obj.get("files_changed")
        if isinstance(fc, list) and all(isinstance(x, str) for x in fc):
            return fc
    return []


def _parse_verification_result(text: str) -> object:
    """Return verification_result value from the first JSON block, or None."""
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if "verification_result" in obj:
            return obj["verification_result"]
    return None


def _normalise_path(f: str) -> str:
    """Strip a single leading ./ or / without mangling dotfile prefixes."""
    if f.startswith("./"):
        return f[2:]
    if f.startswith("/"):
        return f[1:]
    return f


def _touches_globs(files: list[str], globs: list[str]) -> bool:
    """Return True if any file path starts with any glob prefix."""
    if not globs:
        return False
    for f in files:
        norm = _normalise_path(f)
        for g in globs:
            prefix = g if g.endswith("/") else g + "/"
            if norm == g.rstrip("/") or norm.startswith(prefix):
                return True
    return False


def _has_screenshot_evidence(vr: object) -> bool:
    """Return True if verification_result contains before+after screenshot evidence.

    Accepts EITHER:
    - explicit screenshot_before + screenshot_after keys (non-empty strings), OR
    - at least 2 .png/.pdf absolute-path refs anywhere in the stringified vr.
    """
    if isinstance(vr, dict):
        before = vr.get("screenshot_before", "")
        after = vr.get("screenshot_after", "")
        if before and after and isinstance(before, str) and isinstance(after, str):
            return True
    # Fall back: look for >=2 .png/.pdf absolute refs in serialised form.
    vr_text = json.dumps(vr) if not isinstance(vr, str) else vr
    refs = _SCREENSHOT_PATH_RE.findall(vr_text)
    return len(refs) >= 2


def _has_api_invocation_evidence(vr: object) -> bool:
    """Return True if verification_result contains a real-boundary API invocation result.

    Accepts curl output, aside exec output, docker exec snippet, or any text
    containing HTTP status markers or JSON body indicators.
    """
    if vr is None:
        return False
    vr_text = json.dumps(vr) if not isinstance(vr, str) else vr
    return bool(_API_EVIDENCE_RE.search(vr_text))


def _has_skip_reason(vr: object) -> bool:
    """Return True if verification_result.visual_skip_reason is a non-empty string."""
    if not isinstance(vr, dict):
        return False
    reason = vr.get("visual_skip_reason", "")
    return isinstance(reason, str) and bool(reason.strip())


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    if not assistant_text:
        return 0

    marker_match = MARKER_RE.search(assistant_text)
    if not marker_match:
        return 0
    if marker_match.group(1).upper() != "DONE":
        return 0

    agent_name: str = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("tool_input", {}).get("subagent_type")
        or "unknown"
    ).lower()

    if agent_name not in GATED_AGENTS:
        return 0

    files_changed = _parse_files_changed(assistant_text)
    if not files_changed:
        # No files_changed reported — gate cannot determine surface; fail-open.
        return 0

    try:
        ui_globs, api_globs = _load_stack_globs()
    except Exception:
        return 0

    # API-first classification: a file matching both UI and API globs is
    # treated as API only (avoids false SCREENSHOT deny on app/api/** routes).
    api_files = [f for f in files_changed if _touches_globs([f], api_globs)]
    ui_files = [f for f in files_changed if _touches_globs([f], ui_globs) and f not in api_files]
    ui_touched = bool(ui_files)
    api_touched = bool(api_files)

    if not ui_touched and not api_touched:
        return 0

    # Extract verification_result — fail-open on any parse error.
    try:
        vr = _parse_verification_result(assistant_text)
    except Exception:
        return 0

    # Escape hatch: explicit visual_skip_reason always allows.
    if _has_skip_reason(vr):
        _gate_deny_mod.advise(
            EVENT,
            "VISUAL/SKIP",
            (
                "[visual-evidence-gate] INFO — visual evidence skipped: "
                f"visual_skip_reason present (agent={agent_name})."
            ),
        )
        return 0

    if ui_touched and not _has_screenshot_evidence(vr):
        matched_ui = ui_files[:5]
        return _gate_deny_mod.deny(
            EVENT,
            "VISUAL/NO-SCREENSHOT",
            (
                f"[visual-evidence-gate] BLOCK — {agent_name} NEXUS:DONE touched UI paths "
                "but verification_result lacks before/after screenshot evidence (Art. XII / DEC-037).\n"
                f"  UI files matched: {matched_ui[:5]}\n"
                "  Fix options:\n"
                "    A) Capture evidence via aside repl (load `Skill aside-browser`):\n"
                "         const p = await openTab(url);\n"
                "         const s = await snapshot(p, { interactive: true });\n"
                "         await p.screenshot({ path: '/absolute/path/before.png' });\n"
                "         // apply change / reload\n"
                "         await p.screenshot({ path: '/absolute/path/after.png' });\n"
                "       Then set verification_result.screenshot_before + screenshot_after "
                "to those absolute paths.\n"
                "    B) Set verification_result.visual_skip_reason to a non-empty explanation "
                "(makes the skip explicit and auditable)."
            ),
            stderr=True,
        )

    if api_touched and not _has_api_invocation_evidence(vr):
        matched_api = api_files[:5]
        return _gate_deny_mod.deny(
            EVENT,
            "VISUAL/NO-API-INVOCATION",
            (
                f"[visual-evidence-gate] BLOCK — {agent_name} NEXUS:DONE touched API paths "
                "but verification_result lacks a real-boundary invocation result (Art. XII / DEC-037).\n"
                f"  API files matched: {matched_api[:5]}\n"
                "  Fix options:\n"
                "    A) Run a curl against the endpoint and include the output in verification_result:\n"
                "         curl -s http://localhost:3000/api/... | head -20\n"
                "    B) Use `aside exec` to invoke the API and include the result:\n"
                "         aside exec 'GET https://localhost:3000/api/... and show the response'\n"
                "    C) Set verification_result.visual_skip_reason to a non-empty explanation."
            ),
            stderr=True,
        )

    _gate_deny_mod.advise(
        EVENT,
        "VISUAL/PASS",
        (
            f"[visual-evidence-gate] INFO — visual/API evidence satisfied "
            f"(agent={agent_name}, ui_touched={ui_touched}, api_touched={api_touched})."
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

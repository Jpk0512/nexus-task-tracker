#!/usr/bin/env python3
# SubagentStop hook: enforces Lens-before-done for implementing agents.
#
# Contract Rule 17: Forge / Pipeline / Hermes / Atlas returning NEXUS:DONE
# with files_changed touching source paths must have a Lens validation row
# in validation_log written within the last hour for the same task hash.
#
# S2-14 GROUND-TRUTH CROSS-CHECK: files_changed is the agent's SELF-REPORT and
# can omit (or docs-wash) real source changes — omitting it must not skip the
# Lens mandate. When a gated persona returns NEXUS:DONE and the self-report
# shows no gated paths, the gate ALSO consults git ground truth before
# skipping. Window heuristic — deliberately NARROW to bound false positives
# from orchestrator checkpoint commits that predate the agent return:
#   - uncommitted working-tree changes (git status --porcelain -uall: staged,
#     unstaged AND untracked, files listed individually), PLUS
#   - the single HEAD commit only (git diff --name-only HEAD~1..HEAD) — but
#     ONLY when the self-report is absent or unparseable (i.e. we cannot trust
#     files_changed at all). When the self-report IS present and docs-only, the
#     HEAD window is skipped: the agent plausibly only touched docs and the HEAD
#     commit is likely an unrelated checkpoint, not this task's work. This
#     prevents a false-block where a prior hooks-touching commit at HEAD causes
#     a subsequent docs-only NEXUS:DONE to be blocked (TASK-068).
#     Older history is always out of window.
# If ground truth shows gated-source changes, the Lens PASS row is required
# REGARDLESS of the self-report. Fail-soft: when git is unavailable or errors,
# the self-report remains the only signal (no block on git failure alone).
#
# 3.9 CONSTRAINT — the harness runs hooks under the system python3 (3.9.6 on
# macOS). No `X | None` runtime unions in signatures, no `datetime.UTC`, no
# match/case here.
#
# Returns exit 2 (block) or exit 0 (pass/skip).

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta

DB_PATH = os.environ.get(
    "_HOOK_DB_PATH",
    "/Users/john.keeney/nexus-task-tracker/.memory/project.db",
)

# Repo the S2-14 ground-truth cross-check interrogates. Env seam mirrors
# _HOOK_DB_PATH so tests can point the check at a controlled temp repo. An
# unrendered token simply makes git fail -> fail-soft (self-report only).
GIT_ROOT = os.environ.get(
    "_HOOK_GIT_ROOT",
    "/Users/john.keeney/nexus-task-tracker",
)

# Code-writing personas the orchestrator dispatches: every persona that can emit
# source under a gated prefix must pass through Lens before NEXUS:DONE is
# accepted. Read-only personas (scout=investigate, lens=validate) and the
# design-only persona (palette → docs/design only) are deliberately excluded.
# Names mirror the nexus-broker registry DISPATCHABLE_PERSONAS keys; the --pro
# variants are Opus reworks of the same scope and gate identically. Membership is
# matched on the FULL persona name (not the base before '-') so the -pro and the
# sub-stack variants (forge-ui, pipeline-async, …) each gate on their own right.
GATED_AGENTS = frozenset({
    "forge",
    "forge-ui",
    "forge-wire",
    "forge-ui-pro",
    "forge-wire-pro",
    "pipeline",
    "pipeline-data",
    "pipeline-async",
    "pipeline-data-pro",
    "pipeline-async-pro",
    "atlas",
    "hermes",
    "quill",
    "quill-ts",
    "quill-py",
})

# Source paths that trigger the gate when listed in files_changed.
# Derived from the project's stack profile (socraticode_watched_prefixes — the
# source dirs implementers write to). Rendered by render_template from the
# /app/apps/, /app/packages/ token (same construct as socraticode-gate.sh, CL-21).
# The profile prefixes carry a leading slash (e.g. "/apps/web/src/"); strip it
# so they match _touches_source's normalization (which lstrips "./").
# Fallback (unrendered token / empty): the canonical AI-stack source dirs, so a
# raw, un-rendered hook still gates rather than silently failing open.
_RENDERED_WATCHED = "/app/apps/, /app/packages/"
_FALLBACK_PREFIXES = ("app/", "ingestion/src/", "models/", "design/", "app/components/")
if _RENDERED_WATCHED == "__" "WATCHED_PREFIXES__":
    GATED_PATH_PREFIXES = _FALLBACK_PREFIXES
else:
    GATED_PATH_PREFIXES = tuple(
        p.strip().lstrip("/") for p in _RENDERED_WATCHED.split(",") if p.strip()
    ) or _FALLBACK_PREFIXES

MARKER_RE = re.compile(
    r"##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)", re.IGNORECASE
)

VALIDATION_WINDOW = timedelta(hours=1)

# REVISE-detail floor: a verifier (lens/lens-fast) returning NEXUS:REVISE MUST
# include a "Failing criterion:" line so the implementer has a machine-readable
# anchor to fix.  Non-verifier REVISE passes freely (unaffected).
VERIFIER_PERSONAS = frozenset({"lens", "lens-fast"})

FAILING_CRITERION_RE = re.compile(
    r"^\s*Failing criterion\s*:\s*\S",
    re.IGNORECASE | re.MULTILINE,
)

# Content-probe: presence of any of these tokens in the diff/text forces T2.
SUBPROCESS_PROBE_RE = re.compile(
    r"subprocess|eval|exec|os\.system|socket|requests|urllib|http|curl",
    re.IGNORECASE,
)


def _classify_lens_tier(files_changed: list[str], assistant_text: str) -> str:
    """Return 'T1' (trivial/light) or 'T2' (risky/full-audit).

    T2 iff ANY of:
      (a) files_changed has >1 distinct path (multi-file)
      (b) any path starts with a GATED_PATH_PREFIX (after leading-dot-safe strip)
      (c) content-probe hit (SUBPROCESS_PROBE_RE) in assistant_text
      (d) ambiguity — files_changed empty/unparseable (default-deny)
    T1 iff ALL: exactly one file AND non-gated prefix AND no content-probe hit.
    """
    if len(files_changed) > 1:
        return "T2"
    if len(files_changed) == 1:
        f = files_changed[0]
        norm = f[2:] if f.startswith("./") else (f[1:] if f.startswith("/") else f)
        for prefix in GATED_PATH_PREFIXES:
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return "T2"
    if SUBPROCESS_PROBE_RE.search(assistant_text):
        return "T2"
    if not files_changed:
        return "T2"
    return "T1"


def _init_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS validation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            agent_validated     TEXT NOT NULL,
            target_agent        TEXT NOT NULL,
            task_or_brief_hash  TEXT NOT NULL,
            verdict             TEXT NOT NULL,
            evidence_summary    TEXT,
            validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_validation_target
            ON validation_log(target_agent, validated_at DESC)
    """)
    conn.commit()


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


def _touches_source(files: list[str]) -> bool:
    """Return True if any path in files falls under a gated source directory."""
    for f in files:
        # Normalise: strip leading ./ or /
        norm = f.lstrip("./")
        for prefix in GATED_PATH_PREFIXES:
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return True
    return False


def _git_gated_changes(include_head_commit):
    """S2-14 ground truth: gated paths git says actually changed, or None.

    Window (see header): always includes uncommitted working-tree changes.
    The HEAD~1..HEAD half is included only when `include_head_commit` is True
    (i.e. when the self-report is absent/unparseable and we cannot trust
    files_changed at all). When the self-report is present-and-docs-only, the
    HEAD commit is excluded to avoid false-blocks from unrelated checkpoint
    commits (TASK-068).

    Returns the gated subset (possibly empty = clean), or None when git is
    unavailable/errors (fail-soft — self-report stays the signal).
    """
    paths = set()
    base = ["git", "-C", GIT_ROOT]
    try:
        # -uall: list untracked FILES individually — without it git collapses
        # a new directory to "?? dir/", which would never match a gated prefix
        # and the brand-new-file case would slip through.
        st = subprocess.run(
            base + ["status", "--porcelain", "-uall"],
            capture_output=True, text=True, timeout=10,
        )
        if st.returncode != 0:
            return None
        for line in st.stdout.splitlines():
            p = line[3:].strip()
            if " -> " in p:  # rename entry: "R  old -> new"
                p = p.split(" -> ", 1)[1].strip()
            if p:
                paths.add(p.strip('"'))
    except Exception:
        return None
    if include_head_commit:
        try:
            head = subprocess.run(
                base + ["diff", "--name-only", "HEAD~1..HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if head.returncode == 0:
                paths.update(ln.strip() for ln in head.stdout.splitlines() if ln.strip())
            # rc!=0 (e.g. single-commit repo: no HEAD~1) — working tree alone suffices.
        except Exception:
            pass
    return sorted(p for p in paths if _touches_source([p]))


def _warn_extract_miss(payload: dict) -> None:
    """EXTRACT_OK canary (S1-22): valid SubagentStop JSON yielded NO assistant text.

    Harness schema drift (renamed payload keys) would silently disarm this gate —
    every return would look empty and exit 0 forever. Warn LOUDLY instead of
    staying silent (still exit 0: warn, not block). Once per session via a flag
    file keyed on session_id so repeat returns do not spam the orchestrator.
    """
    if not isinstance(payload, dict) or not payload:
        return
    import contextlib
    import tempfile
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "unknown"))[:64]
    flag = os.path.join(tempfile.gettempdir(), ".nexus-extract-miss-lens-gate-" + sid)
    if os.path.exists(flag):
        return
    with contextlib.suppress(OSError):
        open(flag, "w").close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": (
                "[lens-gate] EXTRACT-MISS: SubagentStop payload had no extractable "
                "assistant text — possible harness schema drift"
            ),
        }
    }))


def _derive_task_hash(payload: dict, assistant_text: str) -> str:
    """Produce a stable hash that Lens can reproduce when it calls `validation add`.

    Priority: explicit task_id > task_description > brief hash from assistant text.
    Nexus embeds task_id in the delegation payload when it exists.
    """
    task_id: str = (
        payload.get("task_id")
        or payload.get("tool_input", {}).get("task_id")
        or ""
    )
    task_desc: str = (
        payload.get("task_description")
        or payload.get("tool_input", {}).get("description")
        or os.environ.get("CLAUDE_TASK_DESCRIPTION", "")
        or ""
    )
    raw = task_id or task_desc or assistant_text[:500]
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _has_lens_validation(
    conn: sqlite3.Connection,
    target_agent: str,
    task_hash: str,
) -> bool:
    """Return True iff Lens's *latest* in-window verdict for this task is PASS.

    The gate exists to enforce "Lens PASSed before NEXUS:DONE". Matching ANY row
    regardless of verdict (the old query) means a recorded FAIL/PARTIAL opens the
    GREEN gate — an implementer claims DONE over a logged failure and the
    strongest verifier is undercut by the one gate meant to enforce it.

    Filtering the WHERE to `verdict='PASS'` alone is NOT enough: a stale PASS
    still inside the window would shadow a newer FAIL logged after re-work
    (the same stale-verdict hole, inverted). So select the MOST RECENT in-window
    row first (any verdict, ORDER BY recency, LIMIT 1) and require *that* row's
    verdict to be PASS. The verdict vocabulary (.memory/log.py `validation add`)
    is exactly PASS | PARTIAL | FAIL, so this blocks PARTIAL/FAIL and keeps
    NEXUS:DONE blocked until a *fresh* PASS lands after the latest re-work.

    Compare entirely inside SQLite's datetime domain. `validated_at` defaults
    to CURRENT_TIMESTAMP ('YYYY-MM-DD HH:MM:SS', UTC, no offset). A Python
    `datetime.isoformat()` cutoff ('YYYY-MM-DDTHH:MM:SS.ffffff+00:00') is NOT
    lexicographically comparable to that — the 'T'/space at index 10 inverts
    the order and silently drops fresh rows, locking valid briefs out. Run
    both operands through SQLite `datetime()` so the window math is correct
    regardless of stored format.
    """
    window_hours = int(VALIDATION_WINDOW.total_seconds() // 3600)
    row = conn.execute(
        f"""
        SELECT verdict FROM validation_log
        WHERE agent_validated = 'lens'
          AND target_agent    = ?
          AND task_or_brief_hash = ?
          AND datetime(validated_at) > datetime('now', '-{window_hours} hours')
        ORDER BY datetime(validated_at) DESC, id DESC
        LIMIT 1
        """,
        (target_agent, task_hash),
    ).fetchone()
    return row is not None and row[0] == "PASS"


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
        _warn_extract_miss(payload)
        return 0

    marker_match = MARKER_RE.search(assistant_text)
    if not marker_match:
        return 0

    marker = marker_match.group(1).upper()

    # Extract agent_name early — needed for both the REVISE floor and the DONE gate.
    agent_name: str = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("tool_input", {}).get("subagent_type")
        or "unknown"
    ).lower()

    # REVISE-detail floor: a verifier returning NEXUS:REVISE without a
    # "Failing criterion: <text>" line gives the implementer nothing to fix.
    # Block so the verifier is forced to add specifics before the implementer
    # can act. Non-verifier REVISE (any other persona) passes freely — exit 0.
    if marker == "REVISE":
        if agent_name in VERIFIER_PERSONAS:
            if not FAILING_CRITERION_RE.search(assistant_text):
                print(
                    f"[lens-gate] BLOCK — {agent_name} NEXUS:REVISE is missing a "
                    "'Failing criterion: <text>' line (ORCHESTRATOR-GATES.md §REVISE-floor). "
                    "Add at least one 'Failing criterion: ...' line so the implementer "
                    "has a machine-readable anchor to fix, then re-emit NEXUS:REVISE.",
                    file=sys.stderr,
                )
                return 2
        return 0

    if marker != "DONE":
        # Only NEXUS:DONE triggers the Lens-validation gate below.
        # BLOCKED/CHECKPOINT/NEEDS-DECISION pass freely.
        return 0

    if agent_name not in GATED_AGENTS:
        return 0

    files_changed = _parse_files_changed(assistant_text)
    self_report_gated = _touches_source(files_changed)
    git_gated = None
    if not self_report_gated:
        # S2-14: the self-report alone says "gate does not apply" — do NOT
        # trust it. An absent/unparseable files_changed, or one listing only
        # docs paths, must not skip the Lens mandate when git ground truth
        # shows real gated-source changes.
        #
        # TASK-068: include the HEAD~1..HEAD window ONLY when the self-report
        # is absent/unparseable (files_changed is empty because parsing failed,
        # not because the agent listed docs). When files_changed is present and
        # docs-only, scope git to uncommitted changes only — the HEAD commit is
        # plausibly an unrelated checkpoint and including it causes false-blocks.
        self_report_present_docs_only = bool(files_changed)  # non-empty list, no gated paths
        git_gated = _git_gated_changes(not self_report_present_docs_only)

    if not self_report_gated and not git_gated:
        # No gated source change — confirmed by self-report AND git ground
        # truth (or git unavailable: fail-soft). Gate does not apply.
        return 0

    gt_note = ""
    if git_gated and not self_report_gated:
        gt_note = (
            "  Ground truth (git) shows gated-source changes the self-report "
            f"omitted: {git_gated[:5]}\n"
        )

    task_hash = _derive_task_hash(payload, assistant_text)

    validated: bool | None = None
    last_err: sqlite3.Error | None = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA busy_timeout=5000")
            _init_table(conn)
            validated = _has_lens_validation(conn, agent_name, task_hash)
            conn.close()
            break
        except sqlite3.Error as exc:
            last_err = exc
            if attempt < 2:
                time.sleep(0.1)

    if validated is None:
        # FAIL-CLOSED: the DB could not be read after 3 attempts. Rule 17 cannot
        # be verified, so we must BLOCK rather than silently allow unvalidated work.
        print(
            "[lens-gate] BLOCK — project memory DB is unavailable; Lens validation "
            "(CONTRACT.md Rule 17) could not be verified.\n"
            f"  DB path: {DB_PATH}\n"
            f"  Error after 3 retries: {last_err}\n"
            f"{gt_note}"
            "  Recover: confirm .memory/project.db exists and is a readable SQLite file "
            "(not a directory or locked by another process), then re-dispatch. "
            "Run `python3 .memory/log.py init` if the DB is missing.",
            file=sys.stderr,
        )
        return 2

    if not validated:
        tier = _classify_lens_tier(files_changed, assistant_text)
        print(
            f"[lens-gate] BLOCK — {agent_name.capitalize()} NEXUS:DONE requires Lens "
            "validation first (CONTRACT.md Rule 17). Dispatch Lens before re-claiming done.\n"
            f"  Agent: {agent_name}\n"
            f"  Task hash: {task_hash}\n"
            f"  Lens tier: {tier} ({'light — single non-gated file' if tier == 'T1' else 'full deep audit — multi-file or gated prefix or content probe'})\n"
            f"  Files changed (source): {[f for f in files_changed if _touches_source([f])][:5]}\n"
            f"{gt_note}"
            "  Lens must run: python3 .memory/log.py validation add "
            f"--agent lens --target {agent_name} --task-hash {task_hash} "
            "--verdict PASS|PARTIAL|FAIL --summary \"...\"",
            file=sys.stderr,
        )
        return 2

    # Validated — emit advisory tier note so orchestrator knows which depth was expected.
    tier = _classify_lens_tier(files_changed, assistant_text)
    print(
        f"[lens-gate] INFO — Lens tier for this change: {tier} "
        f"({'light' if tier == 'T1' else 'full deep audit'}). "
        "agent_validated='lens' row accepted (PASS).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

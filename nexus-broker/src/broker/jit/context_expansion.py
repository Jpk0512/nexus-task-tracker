"""JIT context expansion A-D — R5-T02 N47 (plans/15-r5-dag.yaml, proposal SS8
'move full details behind broker').

The R5/N45 SessionStart capping pass shrank every startup hook to a pointer +
counts and moved the FULL body either to disk (the tasks-reconcile report) or
nowhere at all (lessons/registry detail were simply omitted). This module is
where that full detail becomes retrievable ON DEMAND, as a bounded packet, via
the SAME three broker tools Claude already sees (`nexus_run` — server.py wires
`capability_id`/`params` through to `dispatch()` below; see that tool's
docstring for the zero-new-top-level-MCP-tools framing):

  A. memory.session_start_digest {mode: summary|full}
     -- the daemon's `session_digest` capability (`broker.daemon.session_digest`),
        daemon-first with a direct-read fallback (that module's own contract).
  B. tasks.reconcile {mode: full}
     -- the full report `.claude/hooks/session-task-reconcile.sh` (N45) now
        writes to `.memory/files/session-task-reconcile-latest.md`. Direct file
        read only -- there is no daemon capability for this surface.
  C. lessons.pending {mode: full}
     -- decisions matching `lesson-harvester.sh`'s trigger-keyword rule that
        have no lesson recorded yet, queried directly against project.db (read
        replica of that hook's own `find_decisions_without_lessons` logic).
  D. registry.query_full {query_context}
     -- the daemon's FULL-scope registry query (`broker.daemon.registry_query`,
        multi-term AND over name/description/kind/tier/skills -- never the
        phase-A single-substring pilot `registry_scan.filter_registry`),
        daemon-first with a direct-read fallback. This is also the
        invariants/context detail lookup surface: a query_context naming a doc
        or capability finds the registry entries that describe it.

Fail-open contract (binds A and D, the two daemon-backed surfaces): the daemon
is cache-only and non-authoritative. Every daemon-backed JIT path tries the
daemon first and falls back to a direct (uncached) read on `DaemonUnavailable`
-- never fails closed on content, only ever answers "warm" (daemon) or
"direct" (fallback) slower. B and C never touch the daemon at all -- they are
direct reads by construction, so they trivially satisfy the same "always
answers" bar.

Bounded-output contract (binds all four): every packet this module returns
carries `estimated_tokens`/`token_cap`/`truncated` fields and NEVER exceeds its
capability's `token_cap` (`_hard_cap` below is the enforcement backstop -- see
its docstring). The token estimator (`chars / CHARS_PER_TOKEN`) mirrors
`tools/context_budget.py`'s documented heuristic for consistency across the
codebase's budget-reporting surfaces; it is deliberately NOT re-imported from
that root-level tool (out of this node's write_scope, and not meant to be a
shared library import boundary).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from broker.daemon import registry_query as registry_query_module
from broker.daemon.client import DaemonUnavailable, call
from broker.daemon.session_digest import get_session_digest

CHARS_PER_TOKEN = 3.6

# Per-capability token caps. "summary" tiers stay small (the capped hooks
# already carry counts/pointers; a summary packet is a slightly richer nudge,
# not the full body). "full" tiers are generous but never unbounded -- the
# whole point of this module is that even the FULL detail answer is a bounded
# packet, not a re-run of the uncapped SessionStart flood N45 eliminated.
SESSION_DIGEST_SUMMARY_TOKEN_CAP = 400
SESSION_DIGEST_FULL_TOKEN_CAP = 6000
TASKS_RECONCILE_TOKEN_CAP = 6000
LESSONS_PENDING_TOKEN_CAP = 6000
REGISTRY_QUERY_FULL_TOKEN_CAP = 6000

RECONCILE_REPORT_RELPATH = Path(".memory") / "files" / "session-task-reconcile-latest.md"

# Mirrors `.claude/hooks/lesson-harvester.sh`'s TRIGGER_KEYWORDS verbatim --
# same rule, read side.
_LESSON_TRIGGER_KEYWORDS = ("redelegation", "revise", "blocked", "failure", "root cause")

CAPABILITY_IDS: tuple[str, ...] = (
    "memory.session_start_digest",
    "tasks.reconcile",
    "lessons.pending",
    "registry.query_full",
)


class JitCapabilityError(ValueError):
    """Unknown capability id, or an unsupported mode for a known capability."""


def _estimate_tokens(text: str) -> float:
    """chars/3.6-token heuristic -- deterministic, not a real tokenizer count."""
    return round(len(text) / CHARS_PER_TOKEN, 1)


def _cap_list_by_tokens(items: Sequence[Any], token_cap: float) -> tuple[list[Any], bool]:
    """Keep `items` in order until adding the next one would push the running
    JSON size over `token_cap` estimated tokens. Deterministic: identical
    items+order always cut at the identical point. Never drops the FIRST item
    silently truncated-away without the caller-visible `truncated=True` flag.
    """
    kept: list[Any] = []
    for item in items:
        candidate = [*kept, item]
        if kept and _estimate_tokens(json.dumps(candidate, sort_keys=True, default=str)) > token_cap:
            return kept, True
        kept = candidate
    return kept, False


def _cap_text(text: str, token_cap: float, *, pointer: str | None = None) -> tuple[str, bool]:
    """Hard-truncate `text` to `token_cap` estimated tokens, appending a
    caller-visible note (never a silent drop). Same input -> same cut point.

    Measures the JSON-SERIALIZED size of the candidate (`json.dumps`), not the
    raw string length: a text field riddled with newlines (a markdown report
    is exactly this) roughly doubles in size once JSON-escaped (`\\n` is 2
    chars for 1), so capping against raw chars alone would silently return a
    "capped" string that still renders over `token_cap` once embedded in a
    packet -- forcing the outer `_hard_cap` backstop to nuke the whole nice
    structure into a raw-JSON-prefix envelope. Shrinking by 10% per iteration
    against the JSON-rendered estimate converges in a handful of steps and
    guarantees the returned (text, truncated) pair's OWN serialized size fits.
    """
    est = _estimate_tokens(text)
    if est <= token_cap:
        return text, False
    note = f"\n\n...[TRUNCATED: ~{token_cap:.0f} of ~{est:.0f} estimated tokens shown"
    if pointer:
        note += f"; full content at {pointer}"
    note += "]"
    candidate_chars = max(0, int(token_cap * CHARS_PER_TOKEN) - len(note))
    while candidate_chars > 0:
        candidate = text[:candidate_chars] + note
        if _estimate_tokens(json.dumps(candidate)) <= token_cap:
            return candidate, True
        candidate_chars = int(candidate_chars * 0.9)
    return note, True


def _hard_cap(data: Any, token_cap: float, note: str) -> tuple[Any, bool, float]:
    """Safety-net backstop: if `data` (after any surface-specific semantic
    capping already applied) still renders over `token_cap` estimated tokens,
    hard-truncate the rendered JSON string itself so the returned packet NEVER
    exceeds its declared cap, regardless of what shape the caller handed in.
    Deterministic (same data -> same slice); the resulting envelope always
    itself estimates under-or-at cap because it is built from a chars-bounded
    slice plus a short fixed note.
    """
    rendered = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    est = _estimate_tokens(rendered)
    if est <= token_cap:
        return data, False, est
    max_chars = max(0, int(token_cap * CHARS_PER_TOKEN) - len(note) - 32)
    envelope = {"_truncated_raw_json_prefix": rendered[:max_chars], "_note": note}
    return envelope, True, _estimate_tokens(json.dumps(envelope, sort_keys=True, default=str))


def _finalize_packet(
    capability_id: str,
    mode: str,
    data: Any,
    *,
    source: str,
    token_cap: float,
    note: str,
    pre_truncated: bool = False,
) -> dict[str, Any]:
    final_data, hard_truncated, est = _hard_cap(data, token_cap, note)
    return {
        "capability_id": capability_id,
        "mode": mode,
        "source": source,
        "data": final_data,
        "estimated_tokens": est,
        "token_cap": token_cap,
        "truncated": pre_truncated or hard_truncated,
    }


# ---------------------------------------------------------------------------
# A. memory.session_start_digest
# ---------------------------------------------------------------------------


def session_start_digest(
    project_path: Path, *, mode: str = "summary", allow_spawn: bool = True
) -> dict[str, Any]:
    """Daemon-first (`session_digest` RPC) with a direct project.db fallback
    (`get_session_digest`'s own contract -- see that function's docstring).
    `mode="summary"` returns a handful of scalar fields (id/summary/next_step/
    context_log_count); `mode="full"` returns the full session row + capped
    context_log window.
    """
    if mode not in ("summary", "full"):
        raise JitCapabilityError(
            f"memory.session_start_digest: unknown mode {mode!r} (expected 'summary' or 'full')"
        )

    digest = get_session_digest(project_path, allow_spawn=allow_spawn)
    session = digest.get("session")
    context_log = digest.get("context_log", [])
    source = digest.get("source", "unknown")

    if mode == "summary":
        summary_data = {
            "session_id": session["id"] if session else None,
            "summary": session["summary"] if session else None,
            "next_step": session["next_step"] if session else None,
            "context_log_count": len(context_log),
        }
        return _finalize_packet(
            "memory.session_start_digest",
            "summary",
            summary_data,
            source=source,
            token_cap=SESSION_DIGEST_SUMMARY_TOKEN_CAP,
            note="session digest summary truncated",
        )

    capped_log, log_truncated = _cap_list_by_tokens(context_log, SESSION_DIGEST_FULL_TOKEN_CAP)
    full_session = dict(session) if session else None
    truncated = log_truncated
    if full_session is not None:
        combined_est = _estimate_tokens(
            json.dumps({"session": full_session, "context_log": capped_log}, sort_keys=True, default=str)
        )
        if combined_est > SESSION_DIGEST_FULL_TOKEN_CAP:
            # Last resort: the session row's own free-text fields are the
            # oversized part (context_log already capped above) -- trim each,
            # never the whole row.
            per_field_cap = SESSION_DIGEST_FULL_TOKEN_CAP / 4
            for field in ("summary", "next_step", "last_step"):
                value = full_session.get(field)
                if isinstance(value, str) and value:
                    trimmed, field_truncated = _cap_text(value, per_field_cap)
                    if field_truncated:
                        full_session[field] = trimmed
                        truncated = True

    full_data = {"session": full_session, "context_log": capped_log}
    return _finalize_packet(
        "memory.session_start_digest",
        "full",
        full_data,
        source=source,
        token_cap=SESSION_DIGEST_FULL_TOKEN_CAP,
        note="session digest full packet truncated",
        pre_truncated=truncated,
    )


# ---------------------------------------------------------------------------
# B. tasks.reconcile
# ---------------------------------------------------------------------------


def tasks_reconcile(project_path: Path, *, mode: str = "full") -> dict[str, Any]:
    """Direct read of the full reconcile report `session-task-reconcile.sh`
    (N45) writes to `.memory/files/session-task-reconcile-latest.md` when
    SessionStart capping is active. No daemon capability exists for this
    surface -- it is a direct file read by construction.
    """
    if mode != "full":
        raise JitCapabilityError(
            f"tasks.reconcile: unknown mode {mode!r} (only 'full' is served by this surface)"
        )

    report_path = Path(project_path) / RECONCILE_REPORT_RELPATH
    if not report_path.is_file():
        data: dict[str, Any] = {
            "report_available": False,
            "report_path": str(report_path),
            "content": None,
        }
        return _finalize_packet(
            "tasks.reconcile",
            mode,
            data,
            source="direct-read",
            token_cap=TASKS_RECONCILE_TOKEN_CAP,
            note="no reconcile report on disk yet",
        )

    raw = report_path.read_text(encoding="utf-8", errors="replace")
    # Reserve room for the wrapper fields (report_available/report_path,
    # the latter possibly repeated inside the truncation note's pointer) so
    # the WHOLE packet fits under cap without falling through to `_hard_cap`'s
    # uglier raw-JSON-prefix envelope for the common oversized-report case.
    wrapper_overhead = _estimate_tokens(
        json.dumps({"report_available": True, "report_path": str(report_path), "content": ""})
    )
    content_cap = max(0.0, TASKS_RECONCILE_TOKEN_CAP - wrapper_overhead)
    content, truncated = _cap_text(raw, content_cap, pointer=str(report_path))
    data = {"report_available": True, "report_path": str(report_path), "content": content}
    return _finalize_packet(
        "tasks.reconcile",
        mode,
        data,
        source="direct-read",
        token_cap=TASKS_RECONCILE_TOKEN_CAP,
        note="reconcile report truncated",
        pre_truncated=truncated,
    )


# ---------------------------------------------------------------------------
# C. lessons.pending
# ---------------------------------------------------------------------------


def query_lessons_pending(db_path: Path, *, session_id: str | None = None) -> list[dict[str, Any]]:
    """Decisions matching `lesson-harvester.sh`'s trigger-keyword rule with no
    matching lesson yet -- same read, full detail, not scoped to only the most
    recently-ended session unless `session_id` narrows it. Missing/unreadable
    db_path -> empty list (an absent DB is an empty answer, matching every
    other direct-query surface in this module, not an error).
    """
    db_path = Path(db_path)
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        query = "SELECT id, session_id, title, rationale, context FROM decisions"
        params: tuple[Any, ...] = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            params = (session_id,)
        rows = conn.execute(query, params).fetchall()

        pending: list[dict[str, Any]] = []
        for dec_id, dec_session_id, title, rationale, context in rows:
            combined = " ".join(filter(None, [rationale or "", context or ""])).lower()
            if not any(kw in combined for kw in _LESSON_TRIGGER_KEYWORDS):
                continue
            has_lesson = conn.execute(
                "SELECT 1 FROM lessons WHERE source_decision_id = ? LIMIT 1", (dec_id,)
            ).fetchone()
            if has_lesson:
                continue
            pending.append(
                {
                    "decision_id": dec_id,
                    "session_id": dec_session_id,
                    "title": title,
                    "rationale": rationale or "",
                    "context": context or "",
                }
            )
        return pending
    finally:
        conn.close()


def lessons_pending(project_path: Path, *, mode: str = "full") -> dict[str, Any]:
    """Full pending-lessons backlog: every decision matching the trigger-
    keyword rule across the whole project that has no lesson recorded yet.
    Direct project.db read only -- no daemon capability for this surface.
    """
    if mode != "full":
        raise JitCapabilityError(
            f"lessons.pending: unknown mode {mode!r} (only 'full' is served by this surface)"
        )

    db_path = Path(project_path) / ".memory" / "project.db"
    pending = query_lessons_pending(db_path)
    capped, truncated = _cap_list_by_tokens(pending, LESSONS_PENDING_TOKEN_CAP)
    data = {"pending_count": len(pending), "pending": capped}
    return _finalize_packet(
        "lessons.pending",
        mode,
        data,
        source="direct-read",
        token_cap=LESSONS_PENDING_TOKEN_CAP,
        note="pending-lessons list truncated",
        pre_truncated=truncated,
    )


# ---------------------------------------------------------------------------
# D. registry.query_full -- daemon registry_query-full capability
# ---------------------------------------------------------------------------


def registry_query_full(
    project_path: Path, *, query_context: str | None = None, allow_spawn: bool = True
) -> dict[str, Any]:
    """Daemon-first FULL-scope registry query (`broker.daemon.registry_query`
    -- multi-term AND over name/description/kind/tier/skills, never the
    phase-A single-substring pilot) with a direct-read fallback on
    `DaemonUnavailable`. This is also the invariants/context detail lookup
    surface: a `query_context` naming a doc/capability finds the registry
    entries describing it.
    """
    project_path = Path(project_path)
    try:
        result = call(
            project_path,
            "registry_query_full",
            {"query_context": query_context},
            spawn_if_missing=allow_spawn,
        )
        entries = result["entries"]
        source = "daemon"
    except DaemonUnavailable:
        entries = registry_query_module.query_registry(project_path, query_context)
        source = "direct-fallback"

    capped, truncated = _cap_list_by_tokens(entries, REGISTRY_QUERY_FULL_TOKEN_CAP)
    data = {"entry_count": len(entries), "entries": capped}
    return _finalize_packet(
        "registry.query_full",
        "full",
        data,
        source=source,
        token_cap=REGISTRY_QUERY_FULL_TOKEN_CAP,
        note="registry entries truncated",
        pre_truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Dispatch -- the single entry point `nexus_run`'s capability_id path calls.
# ---------------------------------------------------------------------------


def dispatch(
    capability_id: str,
    params: Mapping[str, Any] | None,
    *,
    project_path: Path,
    allow_spawn: bool = True,
) -> dict[str, Any]:
    """Route a `nexus_run(capability_id=..., params=...)` call to the right
    JIT surface above. Raises `JitCapabilityError` (never a bare KeyError/
    AttributeError) on an unknown capability id or an unsupported mode, so a
    caller always gets a legible, typed failure.
    """
    resolved_params = dict(params or {})

    if capability_id == "memory.session_start_digest":
        return session_start_digest(
            project_path,
            mode=resolved_params.get("mode", "summary"),
            allow_spawn=allow_spawn,
        )
    if capability_id == "tasks.reconcile":
        return tasks_reconcile(project_path, mode=resolved_params.get("mode", "full"))
    if capability_id == "lessons.pending":
        return lessons_pending(project_path, mode=resolved_params.get("mode", "full"))
    if capability_id == "registry.query_full":
        return registry_query_full(
            project_path,
            query_context=resolved_params.get("query_context"),
            allow_spawn=allow_spawn,
        )

    raise JitCapabilityError(
        f"unknown JIT capability id: {capability_id!r} (expected one of {CAPABILITY_IDS})"
    )

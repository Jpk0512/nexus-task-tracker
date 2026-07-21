"""Daemon-resident docs-watcher — F2-07 (DEC-084 / FDEC-6 ruling, C-04
separate-judge, `nexus-foundation/plans/artifacts/event-taxonomy.json`'s
`doc.written` event, whose sole named consumer is this module).

On every `doc.written` advisory event, `on_doc_written` runs a C7-style
corpus check of the just-written doc: dead path references, a stale
canonical-version string, and `DEC-*` references that have been superseded
or deprecated. The event fires AFTER the write already landed on disk
(PostToolUse), so this module re-reads the file from `project_path` rather
than trusting any content carried in the event payload.

FDEC-6 / DEC-084 authority boundary (owner-ratified 2026-07-13/16, DEC-084):
the watcher ALWAYS flags every finding — the freshness report is never
silently swallowed, even for a finding it goes on to auto-fix. It
AUTO-WRITES only the three MECHANICAL categories DEC-084 names verbatim
("dead paths, version strings, superseded pointers") — each fix is a single
unambiguous substitution, never a prose rewrite — and every applied fix is
appended to a logged before/after trail (`.memory/docs-watcher-trail.jsonl`,
the same "daemon writes it at runtime, nobody hand-edits it" posture as
`spans.duckdb`, see `spans.spans_db_path_for`). A finding this module cannot
resolve with a single confident substitution (an unresolvable dead path, or
a `DEC-*` reference whose target is `deprecated` with no successor to swap
in) is a SEMANTIC finding — FLAG-ONLY, never auto-written; the watcher never
guesses a replacement.

C-04 (separate-judge): the watcher's own auto-fix write is not exempt from
being watched. `on_doc_written` re-invokes itself once on its own corrected
output (bounded by `_MAX_RECHECK_DEPTH`, never unbounded recursion) so the
corrected doc is independently VERIFIED clean, not simply trusted because
this module wrote it. An auto-fix is a single deterministic substitution, so
a converged doc's re-check must find nothing left to mechanically fix; if it
somehow does not converge within the bound, that surfaces as an ordinary
finding on the final pass rather than looping forever.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_RECHECK_DEPTH = 2

# Mirrors the hook-side filter in .claude/hooks/doc-write-capture.py — that
# hook cannot import this (daemon-venv-only) module, so the two definitions
# are hand-kept in sync, the same dual-derivation posture
# `_ping_shim.py`'s docstring documents for `socket_path`/`paths.py`.
_GOVERNED_BASENAMES = {"CLAUDE.md", "DECISIONS.md", "TASKS.md", "INVARIANTS.md", "CONSTITUTION.md"}

_PATH_REF_RE = re.compile(r"`([\w./-]+\.(?:py|sh|json|md|ya?ml|toml))`")

# Anchored, not a bare `vX.Y.Z` scan (F2-07 REPAIR, FINDING 1a): a version
# token only counts as a NEXUS-VERSION citation when a context anchor word
# (nexus / install(ed) / canonical version / package version / current
# version) sits on the same line within a short run-up — this is what
# distinguishes "This install is v1.0.0" (this project's own version) from
# an unrelated tool/CLI version cited in prose ("verified first-hand on
# v2.1.199"), which shares the same `vX.Y.Z` shape but is not this project's
# version at all. Real corpus proof: docs/archive/audits/FABLE-CONDUCTOR-OPTIONS.md
# cites CLI v2.1.199 / v0.3.142 with no such anchor nearby — zero matches.
_VERSION_CONTEXT_ANCHOR = r"(?:nexus|install(?:ed)?|canonical\s+version|package\s+version|current\s+version)"
_VERSION_REF_RE = re.compile(
    rf"\b{_VERSION_CONTEXT_ANCHOR}\b[^\n]{{0,24}}?\bv(\d+\.\d+\.\d+)\b",
    re.IGNORECASE,
)
_DECISION_REF_RE = re.compile(r"\bDEC-\d+\b")

_SKIP_DIR_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".nexus"}

TRAIL_RELATIVE_PATH = Path(".memory") / "docs-watcher-trail.jsonl"


def is_governed_doc_path(rel_path: str) -> bool:
    """`docs/**` (anywhere in the path) or a known governance basename."""
    normalized = rel_path.replace("\\", "/").lstrip("/")
    if normalized.startswith("docs/") or "/docs/" in normalized:
        return True
    return Path(normalized).name in _GOVERNED_BASENAMES


def is_auto_write_excluded_path(rel_path: str) -> bool:
    """`docs/archive/**` and any other historical-preservation path (any
    `archive` path segment) — still governed (still flagged, F2-07 REPAIR
    FINDING 1b), but NEVER auto-written. Archived docs intentionally preserve
    stale historical citations (old CLI versions, dead paths at the time,
    superseded decision numbers) that must never be silently rewritten to
    look current."""
    normalized = rel_path.replace("\\", "/").lstrip("/")
    return any(part.lower() == "archive" for part in Path(normalized).parts)


@dataclass
class Finding:
    kind: str
    category: str  # "mechanical" | "semantic" — the DEC-084 authority bucket
    detail: str
    auto_fixed: bool = False
    before: str | None = None
    after: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "category": self.category,
            "detail": self.detail,
            "auto_fixed": self.auto_fixed,
            "before": self.before,
            "after": self.after,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def _canonical_version(project_path: Path) -> str | None:
    for candidate in (project_path / "nexus-package" / "VERSION", project_path / ".memory" / ".nexus-version"):
        if candidate.is_file():
            lines = candidate.read_text().splitlines()
            if lines and lines[0].strip():
                return lines[0].strip()
    return None


def _decision_lookup(project_path: Path, dec_id: str) -> dict[str, Any] | None:
    db_path = project_path / ".memory" / "project.db"
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return None
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
        if "id" not in cols or "status" not in cols:
            return None
        has_superseded_by = "superseded_by" in cols
        select_cols = "id, status" + (", superseded_by" if has_superseded_by else "")
        row = conn.execute(f"SELECT {select_cols} FROM decisions WHERE id = ?", (dec_id,)).fetchone()  # noqa: S608 — column set is PRAGMA-derived, never user input; dec_id is parameterized
        if row is None:
            return None
        return {
            "id": row[0],
            "status": row[1],
            "superseded_by": row[2] if has_superseded_by and len(row) > 2 else None,
        }
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _existing_paths_by_basename(project_path: Path) -> dict[str, list[str]]:
    """Basename -> [repo-relative paths] index, built lazily (only when a
    dead-path reference is actually found) — this is a real filesystem walk,
    not free, so callers must not pay it on every clean doc."""
    index: dict[str, list[str]] = {}
    for path in project_path.rglob("*"):
        if not path.is_file() or any(part in _SKIP_DIR_PARTS for part in path.parts):
            continue
        index.setdefault(path.name, []).append(str(path.relative_to(project_path)))
    return index


def check_doc(project_path: Path, content: str, doc_rel_path: str | None = None) -> list[Finding]:
    """Pure corpus check — no writes. Returns every finding, mechanical and
    semantic alike (ALWAYS-flag, per DEC-084). `doc_rel_path`, when given,
    gates auto-write eligibility: a path under `is_auto_write_excluded_path`
    (archived/historical-preservation) downgrades every mechanical finding to
    flag-only — the finding itself is unchanged, only `auto_fixed` flips to
    False (F2-07 REPAIR FINDING 1b)."""
    findings: list[Finding] = []

    canonical_version = _canonical_version(project_path)
    if canonical_version:
        for match in _VERSION_REF_RE.finditer(content):
            found = match.group(1)
            if found != canonical_version:
                findings.append(
                    Finding(
                        kind="stale_version",
                        category="mechanical",
                        detail=f"doc cites v{found}, canonical is v{canonical_version}",
                        auto_fixed=True,
                        before=f"v{found}",
                        after=f"v{canonical_version}",
                    )
                )

    basename_index: dict[str, list[str]] | None = None
    for match in _PATH_REF_RE.finditer(content):
        ref = match.group(1)
        if (project_path / ref).is_file():
            continue
        if basename_index is None:
            basename_index = _existing_paths_by_basename(project_path)
        candidates = basename_index.get(Path(ref).name, [])
        # F2-07 REPAIR FINDING 3 (hardening): only a path-like ref (contains
        # a directory separator) is eligible for auto-fix — a bare
        # backticked filename in prose (no `/`) is flag-only, never
        # auto-linked to a same-named file elsewhere in the repo.
        unique_other_match = len(candidates) == 1 and candidates[0] != ref and "/" in ref
        findings.append(
            Finding(
                kind="dead_path",
                category="mechanical",
                detail=(
                    f"referenced path `{ref}` does not exist; unique same-name match at "
                    f"`{candidates[0]}`"
                    if unique_other_match
                    else f"referenced path `{ref}` does not exist and no unambiguous replacement was found"
                ),
                auto_fixed=unique_other_match,
                before=ref if unique_other_match else None,
                after=candidates[0] if unique_other_match else None,
            )
        )

    for match in _DECISION_REF_RE.finditer(content):
        dec_id = match.group(0)
        record = _decision_lookup(project_path, dec_id)
        if record is None:
            continue
        superseded_by = record.get("superseded_by")
        if superseded_by:
            findings.append(
                Finding(
                    kind="superseded_pointer",
                    category="mechanical",
                    detail=f"{dec_id} was superseded by {superseded_by}",
                    auto_fixed=True,
                    before=dec_id,
                    after=superseded_by,
                )
            )
        elif record.get("status") == "deprecated":
            findings.append(
                Finding(
                    kind="semantic_contradiction",
                    category="semantic",
                    detail=f"{dec_id} is deprecated with no single successor to substitute — needs owner/Plexus review",
                    auto_fixed=False,
                )
            )

    if doc_rel_path is not None and is_auto_write_excluded_path(doc_rel_path):
        # F2-07 REPAIR FINDING 1b: still flag every mechanical finding
        # (nothing here is swallowed), but never auto-write an archived /
        # historical-preservation doc — no auto-write category gets a pass.
        for finding in findings:
            finding.auto_fixed = False

    return findings


def apply_mechanical_fixes(content: str, findings: list[Finding]) -> str:
    fixed = content
    for finding in findings:
        if finding.auto_fixed and finding.before and finding.after:
            fixed = fixed.replace(finding.before, finding.after)
    return fixed


def _append_trail(project_path: Path, doc_rel_path: str, applied: list[Finding]) -> None:
    if not applied:
        return
    trail_path = project_path / TRAIL_RELATIVE_PATH
    trail_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now_iso(), "doc": doc_rel_path, "fixes": [f.to_dict() for f in applied]}
    with trail_path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _advisory_noop(error: str | None = None) -> dict[str, Any]:
    """The fail-OPEN advisory report shape. `doc.written` is a tranche-A
    advisory event (event-taxonomy.json: `fail_policy=advisory-fail-open`) and
    the write it observes has ALREADY landed on disk (PostToolUse) before this
    runs — so a watcher that finds nothing, sees no file, OR fails outright
    must all return the SAME benign no-op, never a block and never a raise.
    `error` is populated only on the fail-OPEN-on-exception path (see
    `on_doc_written`) so a degraded scan is observable without changing the
    clean-no-op shape every other caller asserts."""
    report: dict[str, Any] = {
        "flagged": False,
        "findings": [],
        "auto_fixed": False,
        "recheck": None,
    }
    if error is not None:
        report["error"] = error
    return report


def _scan_and_fix(
    project_path: Path, payload: dict[str, Any], file_path: str, doc_path: Path, recheck_depth: int
) -> dict[str, Any]:
    content = doc_path.read_text()
    findings = check_doc(project_path, content, doc_rel_path=file_path)

    if not findings:
        return _advisory_noop()

    applied = [f for f in findings if f.auto_fixed]
    recheck_report: dict[str, Any] | None = None
    if applied:
        fixed_content = apply_mechanical_fixes(content, applied)
        if fixed_content != content:
            doc_path.write_text(fixed_content)
            _append_trail(project_path, file_path, applied)
            if recheck_depth < _MAX_RECHECK_DEPTH:
                recheck_report = on_doc_written(project_path, payload, recheck_depth + 1)

    return {
        "flagged": True,
        "findings": [f.to_dict() for f in findings],
        "auto_fixed": bool(applied),
        "recheck": recheck_report,
    }


def on_doc_written(project_path: Path, payload: dict[str, Any], _recheck_depth: int = 0) -> dict[str, Any]:
    """The `doc.written` handler `event_bus.handle_event_emit` calls for this
    event's sole consumer. Reads the file fresh from disk (the event fires
    post-write), never trusts payload content.

    FAIL-OPEN (advisory posture, FDEC-6 / C-06): a doc write is NEVER blocked
    by this watcher. The write already landed before the advisory event fires,
    and any unexpected failure inside the corpus check — an undecodable file,
    a check that raises, a transient I/O error — is swallowed into a benign
    fail-OPEN report carrying an `error` note, never propagated. The daemon
    caller therefore always gets a result, never an exception that could turn
    this advisory (tranche-A) event into a block."""
    file_path = str(payload.get("file_path") or "")
    if not file_path:
        return _advisory_noop()

    doc_path = project_path / file_path
    if not doc_path.is_file():
        return _advisory_noop()

    try:
        return _scan_and_fix(project_path, payload, file_path, doc_path, _recheck_depth)
    except Exception as exc:  # noqa: BLE001 — advisory fail-OPEN: a watcher failure must never block or corrupt the doc write it observes
        return _advisory_noop(error=f"{type(exc).__name__}: {exc}")

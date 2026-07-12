#!/usr/bin/env python3
"""Sync project docs from project.db. Called by stop hook — runs after every turn.

Updates:
  docs/TASKS.md            — full regen from tasks table (with progress counts)
  docs/features/FEAT-*.md  — status badges on implementation plan headings
  docs/PRD.md              — feature status column
  docs/sessions/SESSIONS.md — prepend completed sessions
  docs/DECISIONS.md        — append new decisions
  docs/drift-report.md     — staleness alerts for specs vs DB

3.9-SAFETY: this file is DELIVERED to install targets and runs under the
target's ambient python3 (macOS ships 3.9.6) — no `from datetime import UTC`,
no runtime `X | None`, no match/case. The live Plexus copy uses 3.11+ idioms;
the two copies are HAND-RECONCILED (like .claude/hooks), never byte-synced.
"""
from __future__ import annotations

import contextlib
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / ".memory" / "project.db"
DOCS = ROOT / "docs"


def _feature_names() -> dict[str, str]:
    """Derive FEAT-id → display name from docs/features/FEAT-*.md filenames.

    No baked-in project names: a fresh install has no specs yet (empty map and
    FEAT ids render as themselves); each project's own spec files supply the
    labels after that.
    """
    names: dict[str, str] = {}
    for spec in sorted(DOCS.glob("features/FEAT-*.md")):
        parts = spec.stem.split("-", 2)
        if len(parts) < 2:
            continue
        fid = f"{parts[0]}-{parts[1]}"
        names[fid] = parts[2].replace("-", " ").title() if len(parts) > 2 else fid
    return names


FEATURE_NAMES: dict[str, str] = _feature_names()

_BADGE = {
    "done": " ✓",
    "in_progress": " →",
    "blocked": " ✗",
    "cancelled": " ✗",
    "todo": "",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _query(con: sqlite3.Connection, sql: str) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql)]


# ── 1. TASKS.md ───────────────────────────────────────────────────────────────

def sync_tasks(tasks: list[dict]) -> None:
    """Two-level emitter: one ## section per FEAT, one table row per task.

    Each task row carries its own TASK-NNN ID so `grep TASK-031 docs/TASKS.md`
    resolves directly without manual diving into aggregated counts.
    """
    by_feat: dict[str | None, list[dict]] = {}
    for t in tasks:
        by_feat.setdefault(t["feature_id"], []).append(t)

    lines: list[str] = [
        "# Tasks",
        "",
        "> Auto-synced from `.memory/project.db`. Do not edit by hand.",
        "",
    ]

    feat_order = sorted(k for k in by_feat if k is not None)
    for fid in feat_order:
        feat_tasks = by_feat[fid]
        done = sum(1 for t in feat_tasks if t["status"] == "done")
        total = len(feat_tasks)
        pct = int(done / total * 100) if total else 0
        feat_label = FEATURE_NAMES.get(fid, fid)
        lines += [f"## {fid} — {feat_label} ({done}/{total} done, {pct}%)", ""]
        lines += [
            "| Task | Status | Owner | Updated |",
            "|------|--------|-------|---------|",
        ]
        for t in feat_tasks:
            updated = (t.get("updated_at") or "")[:10]
            lines.append(
                f"| **{t['id']}** — {t['title']} | {t['status']} | {t['assigned_to'] or ''} | {updated} |"
            )
        lines.append("")

    infra = by_feat.get(None, [])
    if infra:
        done = sum(1 for t in infra if t["status"] == "done")
        total = len(infra)
        pct = int(done / total * 100) if total else 0
        lines += [f"## Infrastructure / Housekeeping ({done}/{total} done, {pct}%)", ""]
        lines += [
            "| Task | Status | Owner | Updated |",
            "|------|--------|-------|---------|",
        ]
        for t in infra:
            updated = (t.get("updated_at") or "")[:10]
            lines.append(
                f"| **{t['id']}** — {t['title']} | {t['status']} | {t['assigned_to'] or ''} | {updated} |"
            )
        lines.append("")

    blocked = [t for t in tasks if t["status"] == "blocked"]
    if blocked:
        lines += ["---", "", "## Blocked", ""]
        for t in blocked:
            note = t.get("notes") or "see task for details"
            lines.append(f"- **{t['id']}** — {note}")
        lines.append("")

    (DOCS / "TASKS.md").write_text("\n".join(lines))


# ── 2. FEAT spec badges ───────────────────────────────────────────────────────

def sync_feat_specs(tasks: list[dict]) -> None:
    status_map = {t["id"]: t["status"] for t in tasks}

    # Matches: ### TASK-001 [existing badge] — rest of title
    pattern = re.compile(
        r"^(### TASK-\d+)(?:\s+[✓→✗])?\s*(—.+)$",
        re.MULTILINE,
    )

    for feat_path in sorted(DOCS.glob("features/FEAT-*.md")):
        content = feat_path.read_text()

        def replace(m: re.Match) -> str:
            task_id = m.group(1).split()[-1]  # "TASK-001"
            badge = _BADGE.get(status_map.get(task_id, "todo"), "")
            return f"{m.group(1)}{badge} {m.group(2)}"

        patched = pattern.sub(replace, content)
        if patched != content:
            feat_path.write_text(patched)


# ── 3. PRD.md feature status ──────────────────────────────────────────────────

def sync_prd(tasks: list[dict]) -> None:
    prd_path = DOCS / "PRD.md"
    if not prd_path.exists():
        return
    content = prd_path.read_text()

    feat_computed: dict[str, str] = {}
    for fid in sorted({t["feature_id"] for t in tasks if t["feature_id"]}):
        ft = [t for t in tasks if t["feature_id"] == fid]
        statuses = [t["status"] for t in ft]
        done = sum(1 for s in statuses if s == "done")
        if all(s == "blocked" for s in statuses):
            feat_computed[fid] = "Blocked"
        elif done == len(ft):
            feat_computed[fid] = "Done"
        elif done > 0 or any(s == "in_progress" for s in statuses):
            feat_computed[fid] = "In Development"
        else:
            feat_computed[fid] = "Planned"

    # Patch rows: | FEAT-001 | Title | Status | Priority |
    def patch_row(m: re.Match) -> str:
        fid, pre, _, post = m.group(1), m.group(2), m.group(3), m.group(4)
        new_status = feat_computed.get(fid, m.group(3))
        return f"| {fid} |{pre}| {new_status} |{post}"

    patched = re.sub(
        r"\| (FEAT-\d+) \|([^|]+)\| ([^|]+) \|([^|]+\|)",
        patch_row,
        content,
    )
    if patched != content:
        prd_path.write_text(patched)


# ── 4. SESSIONS.md ────────────────────────────────────────────────────────────

def sync_sessions(sessions: list[dict]) -> None:
    sess_path = DOCS / "sessions" / "SESSIONS.md"
    if not sess_path.exists():
        return
    content = sess_path.read_text()

    for s in sessions:
        if not s.get("ended_at") or not s.get("summary"):
            continue
        if s["id"] in content:
            continue

        date_str = (s["started_at"] or "")[:10]
        block = (
            f"## {s['id']} — {date_str}\n\n"
            f"**Branch:** `{s.get('branch') or 'main'}`  \n"
            f"**Summary:** {s['summary']}  \n"
        )
        if s.get("next_step"):
            block += f"**Next:** {s['next_step']}  \n"
        block += "\n---\n\n"

        # Insert after the first --- divider in the file
        parts = content.split("\n---\n", 1)
        content = parts[0] + "\n---\n\n" + block + (parts[1].lstrip("\n") if len(parts) > 1 else "")

    sess_path.write_text(content)


# ── 5. DECISIONS.md ───────────────────────────────────────────────────────────

def sync_decisions(decisions: list[dict]) -> None:
    dec_path = DOCS / "DECISIONS.md"
    if not dec_path.exists():
        return
    content = dec_path.read_text()

    appended = False
    for d in decisions:
        if f"## {d['id']}" in content:
            continue
        block = (
            f"\n---\n\n"
            f"## {d['id']} — {d['title']}\n\n"
            f"**Status:** {d['status'].capitalize()}  \n"
            f"**Date:** {(d.get('decided_at') or '')[:10]}\n\n"
            f"**Context:** {d.get('context') or ''}\n\n"
            f"**Decision:** {d.get('decision') or ''}\n\n"
            f"**Consequences:** {d.get('consequences') or 'None recorded.'}\n"
        )
        content += block
        appended = True

    if appended:
        dec_path.write_text(content)


# ── 6. drift-report.md ────────────────────────────────────────────────────────

def sync_semantic_drift() -> list[str]:
    """Detect spec drift by tracking identifier presence in the codebase.

    For each docs/features/FEAT-*.md spec:
      1. Extract acceptance criteria block (lines under '## Acceptance' headings)
         and the spec body.
      2. Extract candidate identifiers: CamelCase, snake_case, dotted paths,
         function-like patterns. Filter common stopwords.
      3. Count occurrences of each identifier across the codebase
         (app/, ingestion/, models/, docs/).
      4. Cache scores in .memory/drift_scores.json keyed by FEAT-id +
         identifier. On subsequent runs compare current vs cached.
      5. Emit drift alerts when count drops by ≥50% (CRITICAL) or
         identifier disappears entirely (CRITICAL). Score changes <50%
         but ≥20% emit a WARN.

    Returns a list of alert lines to be appended to the drift report.
    """
    import json

    SPECS_DIR = DOCS / "features"
    CACHE_PATH = ROOT / ".memory" / "drift_scores.json"
    SEARCH_ROOTS = [ROOT / "app", ROOT / "ingestion", ROOT / "models", ROOT / "docs"]

    if not SPECS_DIR.is_dir():
        return []

    STOPWORDS = {
        "the", "and", "for", "with", "that", "this", "from", "into", "given",
        "when", "then", "should", "must", "will", "have", "has", "had",
        "feat", "task", "dec", "scout", "forge", "pipeline", "hermes",
        "atlas", "lens", "quill", "nexus", "yes", "true", "false", "null",
    }

    IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")

    def extract_identifiers(text: str) -> set[str]:
        idents = set()
        for m in IDENT_RE.findall(text):
            base = m.lower()
            if len(base) < 5:
                continue
            if base in STOPWORDS:
                continue
            # Need at least one of: underscore, dot, or mixed-case-like signal
            if "_" not in m and "." not in m and m.lower() == m and m.upper() == m:
                continue
            if any(c.isupper() for c in m[1:]) or "_" in m or "." in m:
                idents.add(m)
        return idents

    cached = {}
    if CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            cached = {}

    # Phase 1: collect identifiers per spec
    spec_identifiers: dict[str, set[str]] = {}
    for spec_path in sorted(SPECS_DIR.glob("FEAT-*.md")):
        feat_id = spec_path.stem.split("-")[0] + "-" + spec_path.stem.split("-")[1]
        body = spec_path.read_text()
        accept_match = re.search(
            r"##\s+Acceptance\s+Criteria.*?(?=^##\s|\Z)",
            body, re.IGNORECASE | re.MULTILINE | re.DOTALL
        )
        section = accept_match.group(0) if accept_match else body
        spec_identifiers[feat_id] = extract_identifiers(section)

    # Union of all identifiers across all specs — we'll count each once per file.
    all_idents = set()
    for s in spec_identifiers.values():
        all_idents |= s
    if not all_idents:
        CACHE_PATH.write_text(json.dumps({}, indent=2))
        return []

    # Phase 2: single pass through the codebase. For each file, count each
    # identifier's occurrences using str.count (fast C-level). Total work is
    # O(files × idents × file_size) instead of O(idents × files × file_size).
    ident_totals: dict[str, int] = dict.fromkeys(all_idents, 0)
    skip_suffixes = {
        ".pyc", ".bin", ".db", ".png", ".jpg", ".jpeg", ".webp", ".lock",
        ".woff", ".woff2", ".ico", ".svg", ".pdf", ".mp4", ".webm", ".gif",
        ".otf", ".ttf", ".eot", ".map",
    }
    skip_path_fragments = ("/node_modules/", "/.next/", "/dist/", "/build/",
                           "/.git/", "/__pycache__/", "/.venv/", "/.pytest_cache/",
                           "/.arize-tmp-traces/", "/.claude/plugins/")

    file_count = 0
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix in skip_suffixes:
                continue
            sp = str(p)
            if any(frag in sp for frag in skip_path_fragments):
                continue
            try:
                # Cap per-file size to keep this bounded.
                if p.stat().st_size > 2_000_000:  # 2 MB
                    continue
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            file_count += 1
            for ident in all_idents:
                c = txt.count(ident)
                if c:
                    ident_totals[ident] += c

    # Phase 3: per-spec scores from the shared totals.
    current_scores: dict[str, dict[str, int]] = {}
    alerts: list[str] = []
    for feat_id, idents in spec_identifiers.items():
        current_scores[feat_id] = {ident: ident_totals[ident] for ident in sorted(idents)}

        prev = cached.get(feat_id, {})
        for ident, cur_cnt in current_scores[feat_id].items():
            prev_cnt = prev.get(ident)
            if prev_cnt is None:
                continue
            if prev_cnt == 0:
                continue
            ratio = cur_cnt / prev_cnt
            if cur_cnt == 0 and prev_cnt > 0:
                alerts.append(
                    f"- **[CRITICAL drift]** `{feat_id}` identifier `{ident}` "
                    f"disappeared from codebase (was {prev_cnt}, now 0)."
                )
            elif ratio <= 0.50:
                alerts.append(
                    f"- **[CRITICAL drift]** `{feat_id}` identifier `{ident}` "
                    f"dropped from {prev_cnt} to {cur_cnt} occurrences (×{ratio:.2f})."
                )
            elif ratio <= 0.80:
                alerts.append(
                    f"- **[WARN drift]** `{feat_id}` identifier `{ident}` "
                    f"dropped from {prev_cnt} to {cur_cnt} (×{ratio:.2f})."
                )

    CACHE_PATH.write_text(json.dumps(current_scores, indent=2))
    return alerts


def sync_claude_md_staleness() -> list[str]:
    """Flag missing or stale CLAUDE.md files and a stale docs/sessions/SESSIONS.md.

    Watches root + per-domain CLAUDE.md files. Missing → WARN. Mtime > 30d → WARN
    with day count. SESSIONS.md mtime > 7d → WARN. Returns alert lines (empty if
    everything is fresh).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).timestamp()  # noqa: UP017
    alerts: list[str] = []

    # Root CLAUDE.md is always required. Per-module CLAUDE.md files are only
    # expected when the module dir actually exists — guard with is_dir() so we
    # never emit phantom MISSING alerts for modules this project does not have.
    module_dirs = ["app", "ingestion", "models", "design"]
    claude_files = [ROOT / "CLAUDE.md"]
    claude_files += [
        ROOT / d / "CLAUDE.md" for d in module_dirs if (ROOT / d).is_dir()
    ]
    for p in claude_files:
        rel = p.relative_to(ROOT)
        if not p.exists():
            alerts.append(f"- **[WARN drift]** `{rel}` is MISSING.")
            continue
        age_days = int((now - p.stat().st_mtime) // 86400)
        if age_days > 30:
            alerts.append(
                f"- **[WARN drift]** `{rel}` not updated in {age_days} days — review for staleness."
            )

    sessions_path = DOCS / "sessions" / "SESSIONS.md"
    if not sessions_path.exists():
        alerts.append("- **[WARN drift]** `docs/sessions/SESSIONS.md` is MISSING.")
    else:
        age_days = int((now - sessions_path.stat().st_mtime) // 86400)
        if age_days > 7:
            alerts.append(
                f"- **[WARN drift]** `docs/sessions/SESSIONS.md` not updated in {age_days} days "
                "— sessions may not be syncing."
            )

    return alerts


# ── 7. Identity-doc drift (advisory) ─────────────────────────────────────────

def check_identity_doc_drift(con: sqlite3.Connection) -> list[str]:
    """Advisory: flag signals that CLAUDE.md / README.md may be stale.

    Checks:
    1. Decision gap — if the newest DEC-XXX in project.db is N steps ahead of
       the highest DEC-XXX mentioned in CLAUDE.md or README, flag it.
    2. VERSION mismatch — if CLAUDE.md/README.md mention a vX.Y.Z that does not
       match nexus-package/VERSION, flag it.
    3. Retired persona names — if CLAUDE.md/README.md reference a known-retired
       base persona name as a dispatch target, flag it.

    Never raises. Never modifies CLAUDE.md or README.md. Returns advisory lines.
    """
    alerts: list[str] = []

    # ── 1. Decision gap ──────────────────────────────────────────────────────
    try:
        rows = [row[0] for row in con.execute(
            "SELECT id FROM decisions ORDER BY id"
        )]
        if rows:
            db_max_dec_num = 0
            for dec_id in rows:
                m = re.match(r"DEC-(\d+)", dec_id)
                if m:
                    db_max_dec_num = max(db_max_dec_num, int(m.group(1)))

            identity_files = [ROOT / "CLAUDE.md", ROOT / "README.md"]
            doc_max_dec_num = 0
            for p in identity_files:
                if not p.exists():
                    continue
                for m in re.finditer(r"DEC-(\d+)", p.read_text()):
                    doc_max_dec_num = max(doc_max_dec_num, int(m.group(1)))

            gap = db_max_dec_num - doc_max_dec_num
            if gap >= 5:
                alerts.append(
                    f"- **[ADVISORY]** Decision gap: newest decision is DEC-{db_max_dec_num:03d}"
                    f" but identity docs reference only up to DEC-{doc_max_dec_num:03d}"
                    f" (gap={gap}). Review CLAUDE.md / README for recent decisions that"
                    " may need documenting."
                )
    except Exception as exc:  # noqa: BLE001
        alerts.append(f"- **[ADVISORY-ERROR]** Decision-gap check failed: {exc}")

    # ── 2. VERSION mismatch ──────────────────────────────────────────────────
    try:
        version_path = ROOT / "nexus-package" / "VERSION"
        if version_path.exists():
            pkg_version = version_path.read_text().strip()
            identity_files = [ROOT / "CLAUDE.md", ROOT / "README.md"]
            for p in identity_files:
                if not p.exists():
                    continue
                text = p.read_text()
                mentioned = re.findall(r"\bv(\d+\.\d+\.\d+)\b", text)
                stale = [v for v in mentioned if v != pkg_version]
                if stale:
                    rel = p.relative_to(ROOT)
                    alerts.append(
                        f"- **[ADVISORY]** `{rel}` references version(s) {stale}"
                        f" but nexus-package/VERSION is `{pkg_version}`."
                        " Review whether the version reference is stale."
                    )
    except Exception as exc:  # noqa: BLE001
        alerts.append(f"- **[ADVISORY-ERROR]** VERSION check failed: {exc}")

    # ── 3. Retired persona names ─────────────────────────────────────────────
    try:
        # These are the retired BASE names (without split suffix).
        # The valid dispatch targets are the split variants (forge-ui, forge-wire, etc.).
        retired_dispatch_targets = {"forge", "pipeline", "quill"}
        # Pattern: used as dispatch target, not just mentioned as a word inside a
        # compound like "forge-ui".  Match the bare name at a word boundary NOT
        # followed by a hyphen (which would make it a compound like forge-ui).
        retired_re = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in retired_dispatch_targets) + r")\b(?!-)"
        )
        identity_files = [ROOT / "CLAUDE.md", ROOT / "README.md"]
        for p in identity_files:
            if not p.exists():
                continue
            text = p.read_text()
            found = set(retired_re.findall(text))
            if found:
                rel = p.relative_to(ROOT)
                alerts.append(
                    f"- **[ADVISORY]** `{rel}` mentions retired base persona name(s)"
                    f" {sorted(found)} as bare words — verify these are not used as"
                    " dispatch targets (split variants like forge-ui / forge-wire are correct)."
                )
    except Exception as exc:  # noqa: BLE001
        alerts.append(f"- **[ADVISORY-ERROR]** Retired-persona check failed: {exc}")

    return alerts


def check_related_doc_cohesion() -> list[str]:
    """Advisory: flag doc-relationship groups where one side changed without the others.

    Reads .memory/doc-relationships.yaml.  For each group, if ANY member was
    modified within the last 30 days, check whether the OTHERS were modified
    within 7 days of the most-recently-changed member.  If not, emit an advisory
    to review the sibling docs.

    Never raises. Never modifies any doc. Returns advisory lines.
    Degrades gracefully (advisory no-op) when doc-relationships.yaml is absent.
    """
    alerts: list[str] = []
    rel_yaml = ROOT / ".memory" / "doc-relationships.yaml"
    if not rel_yaml.exists():
        return alerts

    try:
        import yaml  # type: ignore[import-untyped]
        config = yaml.safe_load(rel_yaml.read_text())
    except Exception as exc:  # noqa: BLE001
        return [f"- **[ADVISORY-ERROR]** doc-relationships.yaml parse failed: {exc}"]

    from datetime import datetime, timezone

    now_ts = datetime.now(timezone.utc).timestamp()  # noqa: UP017
    thirty_days = 30 * 86400
    seven_days = 7 * 86400

    groups = config.get("groups", []) if isinstance(config, dict) else []
    for group in groups:
        name = group.get("name", "unnamed")
        doc_paths_rel = group.get("docs", [])
        reason = group.get("reason", "")

        doc_mtimes: dict[str, float] = {}
        for rel in doc_paths_rel:
            p = ROOT / rel
            if p.exists():
                doc_mtimes[rel] = p.stat().st_mtime

        if len(doc_mtimes) < 2:
            continue

        max_mtime = max(doc_mtimes.values())
        if now_ts - max_mtime > thirty_days:
            # Nothing in this group changed recently — skip
            continue

        # Find which doc changed most recently, and which siblings lag behind
        most_recent_rel = max(doc_mtimes, key=lambda r: doc_mtimes[r])
        stale_siblings = [
            rel for rel, mtime in doc_mtimes.items()
            if rel != most_recent_rel and max_mtime - mtime > seven_days
        ]
        if stale_siblings:
            changed_age_days = int((now_ts - max_mtime) / 86400)
            stale_list = ", ".join(f"`{s}`" for s in sorted(stale_siblings))
            alerts.append(
                f"- **[ADVISORY]** Doc group `{name}`: `{most_recent_rel}` was"
                f" modified {changed_age_days}d ago but related doc(s) {stale_list}"
                f" have not been updated recently. Review related docs: {stale_list}."
                + (f" Reason: {reason.strip()}" if reason else "")
            )

    return alerts


def sync_drift_report(tasks: list[dict], con: sqlite3.Connection | None = None) -> None:
    from datetime import datetime, timezone

    alerts: list[str] = []
    now_utc = datetime.now(timezone.utc)  # noqa: UP017

    # Alert if a task has been in_progress for more than 2 sessions (approx 48h)
    for t in tasks:
        if t["status"] != "in_progress":
            continue
        updated = t.get("updated_at") or ""
        if not updated:
            continue
        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age_hours = (now_utc - dt).total_seconds() / 3600
            if age_hours > 48:
                alerts.append(
                    f"- **{t['id']}** ({t['title']}) has been `in_progress` for {int(age_hours)}h — stale?"
                )
        except (ValueError, TypeError):
            pass

    # Alert if any spec file is missing for a feature that has non-done tasks
    feat_ids_with_open = {
        t["feature_id"] for t in tasks
        if t["feature_id"] and t["status"] not in ("done", "cancelled")
    }
    for fid in feat_ids_with_open:
        spec_files = list((DOCS / "features").glob(f"{fid}-*.md"))
        if not spec_files:
            alerts.append(f"- **{fid}** has open tasks but no spec file at `docs/features/{fid}-*.md`")

    # Check for tasks with no assigned_to
    unassigned = [t for t in tasks if not t.get("assigned_to") and t["status"] == "todo"]
    if unassigned:
        alerts.append(
            f"- {len(unassigned)} todo task(s) have no persona assigned: "
            + ", ".join(t["id"] for t in unassigned)
        )

    now_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Drift Report",
        "",
        f"> Auto-generated by stop hook. Last updated: {now_str}",
        "",
    ]

    # Append semantic-drift alerts from spec → codebase identifier tracking.
    try:
        sem_alerts = sync_semantic_drift()
    except Exception as exc:  # noqa: BLE001
        sem_alerts = [f"- **[ERROR]** Semantic drift detector failed: {exc}"]

    try:
        claude_alerts = sync_claude_md_staleness()
    except Exception as exc:  # noqa: BLE001
        claude_alerts = [f"- **[ERROR]** CLAUDE.md staleness check failed: {exc}"]

    # Advisory: identity-doc drift (never blocks, never overwrites CLAUDE.md / README)
    identity_alerts: list[str] = []
    try:
        if con is not None:
            identity_alerts.extend(check_identity_doc_drift(con))
    except Exception as exc:  # noqa: BLE001
        identity_alerts.append(f"- **[ADVISORY-ERROR]** Identity-doc drift check failed: {exc}")
    try:
        identity_alerts.extend(check_related_doc_cohesion())
    except Exception as exc:  # noqa: BLE001
        identity_alerts.append(f"- **[ADVISORY-ERROR]** Related-doc cohesion check failed: {exc}")

    if alerts or sem_alerts or claude_alerts or identity_alerts:
        lines += ["## Alerts", ""]
        if alerts:
            lines += alerts + [""]
        if claude_alerts:
            lines += ["### CLAUDE.md staleness", ""]
            lines += claude_alerts + [""]
        if sem_alerts:
            lines += ["### Semantic drift (spec ↔ code identifier tracking)", ""]
            lines += sem_alerts + [""]
        if identity_alerts:
            lines += ["### Identity-doc drift (advisory — no action required)", ""]
            lines += identity_alerts + [""]
    else:
        lines += ["## Status", "", "No staleness alerts. All systems nominal.", ""]

    (DOCS / "drift-report.md").write_text("\n".join(lines))


# ── Main ──────────────────────────────────────────────────────────────────────

def sync_state_md(
    tasks: list[dict],
    sessions: list[dict],
    decisions: list[dict],
    con: sqlite3.Connection,
) -> None:
    """Write .memory/STATE.md with YAML frontmatter for statusline + downstream readers (NH-2)."""
    from datetime import datetime, timezone

    import yaml  # type: ignore[import-untyped]

    active_session = next((s for s in sessions if s.get("ended_at") is None), None)
    open_tasks = [t for t in tasks if t.get("status") not in ("done", "cancelled")]
    in_progress = [t for t in open_tasks if t.get("status") == "in_progress"]

    feature_rows = _query(
        con,
        "SELECT id, title, status FROM feature_specs WHERE status NOT IN ('done','cancelled') ORDER BY id",
    )
    active_feature = feature_rows[0]["id"] if feature_rows else None

    active_persona = None
    for t in in_progress:
        if t.get("assigned_to"):
            active_persona = t["assigned_to"]
            break

    active_phase = None
    last_phase_decision = next(
        (d for d in reversed(decisions) if "Phase" in (d.get("title") or "")), None
    )
    if last_phase_decision:
        match = re.search(r"Phase\s+\d+(?:\s+Wave\s+\d+)?", last_phase_decision["title"])
        if match:
            active_phase = match.group(0)

    last_decision = decisions[-1] if decisions else None

    frontmatter = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),  # noqa: UP017
        "active_session": active_session["id"] if active_session else None,
        "active_branch": active_session["branch"] if active_session else None,
        "active_phase": active_phase,
        "active_persona": active_persona,
        "active_feature": active_feature,
        "open_task_count": len(open_tasks),
        "in_progress_task_count": len(in_progress),
        "in_progress_task_ids": [t["id"] for t in in_progress[:10]],
        "last_decision_id": last_decision["id"] if last_decision else None,
        "last_decision_title": last_decision["title"] if last_decision else None,
    }

    out = ["---"]
    out.append(yaml.safe_dump(frontmatter, sort_keys=False, default_flow_style=False).rstrip())
    out.append("---")
    out.append("")
    out.append("# Project STATE")
    out.append("")
    out.append("Auto-generated by `.memory/sync_docs.py` on every Stop hook. Frontmatter is the machine-readable view; this file is also human-readable as a quick status check.")
    out.append("")
    out.append(f"- **Active session:** `{frontmatter['active_session'] or 'none'}` on `{frontmatter['active_branch'] or 'unknown'}`")
    out.append(f"- **Active phase:** {frontmatter['active_phase'] or 'none'}")
    out.append(f"- **Active persona:** {frontmatter['active_persona'] or 'none'}")
    out.append(f"- **Active feature:** {frontmatter['active_feature'] or 'none'}")
    out.append(f"- **Open tasks:** {frontmatter['open_task_count']} (in-progress: {frontmatter['in_progress_task_count']})")
    if frontmatter["in_progress_task_ids"]:
        out.append(f"- **In-progress IDs:** {', '.join(frontmatter['in_progress_task_ids'])}")
    if frontmatter["last_decision_id"]:
        out.append(f"- **Last decision:** {frontmatter['last_decision_id']} — {frontmatter['last_decision_title']}")
    out.append("")

    state_path = ROOT / ".memory" / "STATE.md"
    state_path.write_text("\n".join(out))


def main() -> None:
    if not DB_PATH.exists():
        return

    con = sqlite3.connect(DB_PATH)
    tasks = _query(con, "SELECT id, feature_id, title, status, priority, assigned_to, notes, updated_at FROM tasks ORDER BY id")
    decisions = _query(con, "SELECT id, title, status, context, decision, consequences, decided_at FROM decisions ORDER BY id")
    sessions = _query(con, "SELECT id, started_at, ended_at, summary, next_step, branch FROM sessions ORDER BY started_at DESC LIMIT 10")

    sync_tasks(tasks)
    sync_feat_specs(tasks)
    sync_prd(tasks)
    sync_sessions(sessions)
    sync_decisions(decisions)
    sync_drift_report(tasks, con)
    with contextlib.suppress(ImportError):
        sync_state_md(tasks, sessions, decisions, con)

    con.close()


if __name__ == "__main__":
    main()

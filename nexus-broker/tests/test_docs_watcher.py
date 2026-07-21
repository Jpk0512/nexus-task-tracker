"""F2-07 daemon-resident docs-watcher — `docs_watcher.py`, the sole named
consumer of the `doc.written` event (event-taxonomy.json). Covers this
leaf's acceptance surface (brief + `nexus-foundation/TASKS.md` F2-07):

  - a seeded-stale-ref write MUST flag;
  - a clean write MUST NOT (zero false-positives on the clean corpus fixture);
  - a mechanical auto-fix (dead path / stale version / superseded decision
    pointer, DEC-084's three named categories) is applied AND logged to the
    before/after trail;
  - a semantic contradiction (no single confident replacement) is FLAG-ONLY
    — the doc on disk is never rewritten for it;
  - C-04 (separate-judge): the watcher's own auto-fix write is itself
    re-checked, converging clean within one bounded recheck pass.

Fixtures build a real, minimal `decisions` table matching production's
actual post-OPT-054-migration column set (id/status/superseded_by, the
columns this module reads) — no hand-invented shape, no mocked DB
connection (tdd-core: real in-memory/fixture DB, not a mocked cursor).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from broker.daemon import docs_watcher


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / ".memory").mkdir(parents=True)
    return root


def _seed_decisions_db(db_path: Path, decisions: list[dict]) -> None:
    """Real sqlite table, production's actual post-migration column set
    (`.memory/schema.sql` decisions table + `2026-06-12-opt054-supersession.py`'s
    added columns) — only the columns `docs_watcher._decision_lookup` reads
    (id/status/superseded_by) are populated meaningfully; the rest are
    present for shape fidelity."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE decisions (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'accepted',
            context       TEXT NOT NULL,
            decision      TEXT NOT NULL,
            rationale     TEXT,
            alternatives  TEXT,
            consequences  TEXT,
            decided_at    TEXT NOT NULL,
            session_id    TEXT,
            valid_from    TEXT,
            valid_to      TEXT,
            superseded_by TEXT,
            supersedes    TEXT,
            content_hash  TEXT,
            is_tombstone  INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    for d in decisions:
        conn.execute(
            "INSERT INTO decisions (id, title, status, context, decision, decided_at, superseded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                d["id"],
                d.get("title", d["id"]),
                d.get("status", "accepted"),
                "context",
                "decision text",
                "2026-01-01T00:00:00Z",
                d.get("superseded_by"),
            ),
        )
    conn.commit()
    conn.close()


# ── is_governed_doc_path ────────────────────────────────────────────────


def test_is_governed_doc_path_matches_docs_dir_and_governance_basenames() -> None:
    assert docs_watcher.is_governed_doc_path("docs/CONSTITUTION.md") is True
    assert docs_watcher.is_governed_doc_path("nested/docs/plan.md") is True
    assert docs_watcher.is_governed_doc_path("CLAUDE.md") is True
    assert docs_watcher.is_governed_doc_path("nexus-foundation/DECISIONS.md") is True


def test_is_governed_doc_path_rejects_ungoverned_path() -> None:
    assert docs_watcher.is_governed_doc_path("nexus-broker/src/broker/daemon/event_bus.py") is False
    assert docs_watcher.is_governed_doc_path("README.md") is False


# ── clean corpus: zero false positives ──────────────────────────────────


def test_clean_doc_write_is_not_flagged(project) -> None:
    doc = project / "docs" / "clean.md"
    doc.write_text("# Clean doc\n\nNo stale versions, no dead paths, no decision refs.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/clean.md"})

    assert report == {"flagged": False, "findings": [], "auto_fixed": False, "recheck": None}
    assert doc.read_text() == "# Clean doc\n\nNo stale versions, no dead paths, no decision refs.\n"


def test_on_doc_written_missing_file_path_is_noop(project) -> None:
    report = docs_watcher.on_doc_written(project, {})
    assert report == {"flagged": False, "findings": [], "auto_fixed": False, "recheck": None}


def test_on_doc_written_nonexistent_file_is_noop(project) -> None:
    report = docs_watcher.on_doc_written(project, {"file_path": "docs/does-not-exist.md"})
    assert report == {"flagged": False, "findings": [], "auto_fixed": False, "recheck": None}


# ── seeded-stale-ref write MUST flag ────────────────────────────────────


def test_stale_version_reference_is_flagged(project) -> None:
    (project / "nexus-package").mkdir()
    (project / "nexus-package" / "VERSION").write_text("2.0.0\n")
    doc = project / "docs" / "readme.md"
    doc.write_text("This install is v1.0.0.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/readme.md"})

    assert report["flagged"] is True
    assert len(report["findings"]) == 1
    finding = report["findings"][0]
    assert finding["kind"] == "stale_version"
    assert finding["category"] == "mechanical"
    assert finding["auto_fixed"] is True


# ── mechanical auto-fix, applied + logged with a before/after trail ─────


def test_stale_version_is_mechanically_fixed_and_logged_to_trail(
    project, snapshot: SnapshotAssertion
) -> None:
    (project / "nexus-package").mkdir()
    (project / "nexus-package" / "VERSION").write_text("2.0.0\n")
    doc = project / "docs" / "readme.md"
    doc.write_text("This install is v1.0.0.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/readme.md"})

    assert report["auto_fixed"] is True
    # doc-render: the watcher's rewritten doc content is a reviewed golden
    # snapshot (F3-04) — a wording/format drift in the rewrite now shows as a
    # readable snapshot diff instead of a silent inline-string edit.
    assert doc.read_text() == snapshot(name="rewritten_doc")

    trail_path = project / docs_watcher.TRAIL_RELATIVE_PATH
    assert trail_path.is_file()
    lines = trail_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["doc"] == "docs/readme.md"
    # envelope fixture: the audit-trail fix entry shape, reviewed via snapshot.
    assert entry["fixes"] == snapshot(name="trail_fixes")


def test_superseded_decision_pointer_is_mechanically_fixed(
    project, snapshot: SnapshotAssertion
) -> None:
    _seed_decisions_db(
        project / ".memory" / "project.db",
        [{"id": "DEC-050", "status": "superseded", "superseded_by": "DEC-084"}],
    )
    doc = project / "docs" / "plan.md"
    doc.write_text("See DEC-050 for the original ruling.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/plan.md"})

    assert report["flagged"] is True
    assert report["auto_fixed"] is True
    finding = report["findings"][0]
    assert finding["kind"] == "superseded_pointer"
    assert finding["category"] == "mechanical"
    assert finding["before"] == "DEC-050"
    assert finding["after"] == "DEC-084"
    # doc-render: reviewed golden snapshot of the rewritten doc content.
    assert doc.read_text() == snapshot(name="rewritten_doc")

    # F2-07 REPAIR FINDING 2: trail-entry shape assertion for this category
    # too — not just the doc rewrite (previously only stale_version had this).
    trail_path = project / docs_watcher.TRAIL_RELATIVE_PATH
    entry = json.loads(trail_path.read_text().strip().splitlines()[0])
    assert entry["doc"] == "docs/plan.md"
    # envelope fixture: the audit-trail fix entry shape, reviewed via snapshot.
    assert entry["fixes"] == snapshot(name="trail_fixes")


def test_dead_path_with_unique_basename_match_is_mechanically_fixed(
    project, snapshot: SnapshotAssertion
) -> None:
    real_dir = project / "nexus-broker" / "src" / "broker" / "daemon"
    real_dir.mkdir(parents=True)
    (real_dir / "docs_watcher.py").write_text("# moved here\n")
    doc = project / "docs" / "plan.md"
    doc.write_text("See `old/location/docs_watcher.py` for the watcher.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/plan.md"})

    assert report["auto_fixed"] is True
    finding = report["findings"][0]
    assert finding["kind"] == "dead_path"
    assert finding["category"] == "mechanical"
    assert finding["after"] == "nexus-broker/src/broker/daemon/docs_watcher.py"
    assert "nexus-broker/src/broker/daemon/docs_watcher.py" in doc.read_text()

    # F2-07 REPAIR FINDING 2: trail-entry shape assertion for this category.
    trail_path = project / docs_watcher.TRAIL_RELATIVE_PATH
    entry = json.loads(trail_path.read_text().strip().splitlines()[0])
    assert entry["doc"] == "docs/plan.md"
    # envelope fixture: the audit-trail fix entry shape (incl. the free-text
    # `detail` sentence), reviewed via snapshot.
    assert entry["fixes"] == snapshot(name="trail_fixes")


def test_dead_path_with_no_unambiguous_match_is_flagged_but_not_fixed(project) -> None:
    doc = project / "docs" / "plan.md"
    doc.write_text("See `totally/gone/nowhere.py` for detail.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/plan.md"})

    assert report["flagged"] is True
    assert report["auto_fixed"] is False
    finding = report["findings"][0]
    assert finding["kind"] == "dead_path"
    assert finding["category"] == "mechanical"
    assert finding["auto_fixed"] is False
    assert doc.read_text() == "See `totally/gone/nowhere.py` for detail.\n"


def test_dead_path_bare_filename_with_no_slash_is_flag_only_even_with_unique_match(project) -> None:
    """F2-07 REPAIR FINDING 3 (hardening): a bare backticked filename (no
    directory separator) in prose is never auto-linked, even when exactly
    one same-named file exists elsewhere in the repo — only path-LIKE refs
    (containing `/`) are auto-fix eligible."""
    real_dir = project / "nexus-broker" / "src" / "broker" / "daemon"
    real_dir.mkdir(parents=True)
    (real_dir / "docs_watcher.py").write_text("# lives here\n")
    doc = project / "docs" / "plan.md"
    doc.write_text("See `docs_watcher.py` for the watcher.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/plan.md"})

    assert report["flagged"] is True
    assert report["auto_fixed"] is False
    finding = report["findings"][0]
    assert finding["kind"] == "dead_path"
    assert finding["auto_fixed"] is False
    assert finding["before"] is None and finding["after"] is None
    assert doc.read_text() == "See `docs_watcher.py` for the watcher.\n"
    assert not (project / docs_watcher.TRAIL_RELATIVE_PATH).is_file()


# ── FINDING 1 repair: stale-version detection must not corrupt unrelated ──
# ── historical version citations (real archive corpus, DEC-084 owner fix) ─


def test_unanchored_version_token_is_not_flagged_even_off_archive_path(project) -> None:
    """F2-07 REPAIR FINDING 1a: a bare `vX.Y.Z` token with no nexus-version
    context anchor nearby (nexus / install(ed) / canonical version / package
    version / current version) is not a nexus-version citation at all and
    must not be flagged or fixed — independent of path (this doc lives under
    plain `docs/`, not `docs/archive/**`), proving the content-anchor fix
    alone (not just the path exclusion) closes the false positive."""
    (project / "nexus-package").mkdir()
    (project / "nexus-package" / "VERSION").write_text("1.18.4\n")
    doc = project / "docs" / "notes.md"
    doc.write_text(
        "Every CLI flag cited in this note was verified first-hand on v2.1.199, "
        "and the `unstable_v2` preview was removed in v0.3.142.\n"
    )
    original = doc.read_text()

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/notes.md"})

    assert report == {"flagged": False, "findings": [], "auto_fixed": False, "recheck": None}
    assert doc.read_text() == original
    assert not (project / docs_watcher.TRAIL_RELATIVE_PATH).is_file()


def test_archive_path_real_corpus_shape_yields_zero_rewrite(project) -> None:
    """F2-07 REPAIR FINDING 1b + regression proof: real content shaped like
    the actually-committed `docs/archive/audits/FABLE-CONDUCTOR-OPTIONS.md`
    (unrelated CLI-tool version citations, `vX.Y.Z`-shaped, with no
    nexus-context anchor, plus a dead-looking backtick path reference) under
    a `docs/archive/**` path must produce ZERO rewrite — proven against the
    canonical version differing from every cited token, which is exactly the
    condition that previously triggered a corrupting auto-fix."""
    (project / "nexus-package").mkdir()
    (project / "nexus-package" / "VERSION").write_text("1.18.4\n")
    archive_dir = project / "docs" / "archive" / "audits"
    archive_dir.mkdir(parents=True)
    doc = archive_dir / "FABLE-CONDUCTOR-OPTIONS.md"
    original = (
        "**Evidence classes used below:** `[first-hand]` = verified on this box "
        "this session (CLI v2.1.199 flags, main-branch files, hardware).\n\n"
        "The removed `unstable_v2` preview, v0.3.142 `[docs]`, was a session API "
        "the conductor doesn't strictly need.\n\n"
        "See `some/moved/module.py` for detail.\n"
    )
    doc.write_text(original)

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/archive/audits/FABLE-CONDUCTOR-OPTIONS.md"})

    # still flagged (DEC-084: never silently swallowed) — but zero rewrite.
    assert doc.read_text() == original
    assert report["auto_fixed"] is False
    for finding in report["findings"]:
        assert finding["auto_fixed"] is False
    assert not (project / docs_watcher.TRAIL_RELATIVE_PATH).is_file()


def test_is_auto_write_excluded_path_flags_any_archive_segment() -> None:
    assert docs_watcher.is_auto_write_excluded_path("docs/archive/audits/x.md") is True
    assert docs_watcher.is_auto_write_excluded_path("docs/archive/nexus-redesign/plans/y.md") is True
    assert docs_watcher.is_auto_write_excluded_path("docs/plan.md") is False


def test_stale_version_with_anchor_still_auto_fixed_off_archive_path(project) -> None:
    """Sanity companion to the anchor fix: a genuinely-anchored nexus-version
    citation on a NON-archive path is still mechanically auto-fixed — the
    repair narrows false positives, it does not disable the feature."""
    (project / "nexus-package").mkdir()
    (project / "nexus-package" / "VERSION").write_text("2.0.0\n")
    doc = project / "docs" / "readme.md"
    doc.write_text("The nexus install is v1.0.0 today.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/readme.md"})

    assert report["auto_fixed"] is True
    assert "v2.0.0" in doc.read_text()


# ── semantic contradiction: FLAG-ONLY, never auto-written ───────────────


def test_deprecated_decision_with_no_successor_is_flag_only_semantic(project) -> None:
    _seed_decisions_db(
        project / ".memory" / "project.db",
        [{"id": "DEC-900", "status": "deprecated", "superseded_by": None}],
    )
    doc = project / "docs" / "plan.md"
    original = "Per DEC-900, this still applies.\n"
    doc.write_text(original)

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/plan.md"})

    assert report["flagged"] is True
    assert report["auto_fixed"] is False
    finding = report["findings"][0]
    assert finding["kind"] == "semantic_contradiction"
    assert finding["category"] == "semantic"
    assert finding["auto_fixed"] is False
    assert finding["before"] is None and finding["after"] is None
    # never rewritten — semantic findings are flag-only, no guessed substitution
    assert doc.read_text() == original
    assert not (project / docs_watcher.TRAIL_RELATIVE_PATH).is_file()


# ── fail-OPEN advisory: a failing/dead watcher never blocks a doc write ──


def test_doc_written_is_advisory_fail_open_in_taxonomy(project) -> None:
    """Acceptance 3 (bus contract): `doc.written` is a tranche-A,
    advisory-fail-open event. Only tranche-B `event.verify` events can ever
    deny — a tranche-A event is structurally incapable of blocking a write, so
    even a total watcher failure can only ever degrade to fail-OPEN, never to
    a block. Proven against the real taxonomy the daemon actually loads."""
    from broker.daemon import event_bus

    taxonomy = event_bus.EventTaxonomy(event_bus.taxonomy_path_for(project))
    event = taxonomy.get("doc.written")
    assert event.tranche == "A"
    assert event.fail_policy == "advisory-fail-open"


def test_watcher_internal_failure_is_fail_open_and_never_raises(project, monkeypatch) -> None:
    """Acceptance 3 (module contract): if the corpus check itself blows up,
    `on_doc_written` swallows it into a benign fail-OPEN report — it never
    raises (so the daemon caller `event_bus.handle_event_emit` can never turn
    the advisory into a block) and it never corrupts the already-written doc.
    A dead watcher is a no-op, not a blocker."""
    doc = project / "docs" / "readme.md"
    original = "This install is v1.0.0.\n"
    doc.write_text(original)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("corpus check exploded")

    monkeypatch.setattr(docs_watcher, "check_doc", _boom)

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/readme.md"})

    assert report["flagged"] is False
    assert report["auto_fixed"] is False
    assert report["recheck"] is None
    assert "RuntimeError" in report["error"]
    # the write the watcher was observing is left exactly as it landed —
    # neither blocked nor corrupted.
    assert doc.read_text() == original
    assert not (project / docs_watcher.TRAIL_RELATIVE_PATH).is_file()


def test_undecodable_doc_write_is_fail_open(project) -> None:
    """Acceptance 3 (real, un-monkeypatched failure path): a non-UTF-8 doc
    write makes `read_text()` raise `UnicodeDecodeError` deep inside the scan.
    That must fail OPEN too — the raw bytes on disk are left exactly as
    written and no exception escapes to the daemon caller."""
    doc = project / "docs" / "binary.md"
    raw = b"\xff\xfe not valid utf-8 \x80\x81\n"
    doc.write_bytes(raw)

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/binary.md"})

    assert report["flagged"] is False
    assert "error" in report
    assert doc.read_bytes() == raw


# ── C-04: the watcher's own fix-write is itself re-checked ──────────────


def test_on_doc_written_rechecks_its_own_fix_and_converges_clean(
    project, snapshot: SnapshotAssertion
) -> None:
    (project / "nexus-package").mkdir()
    (project / "nexus-package" / "VERSION").write_text("2.0.0\n")
    doc = project / "docs" / "readme.md"
    doc.write_text("This install is v1.0.0.\n")

    report = docs_watcher.on_doc_written(project, {"file_path": "docs/readme.md"})

    assert report["auto_fixed"] is True
    recheck = report["recheck"]
    assert recheck is not None
    # the corrected doc, independently re-scanned, is clean — no infinite loop,
    # no further findings, no further recursive recheck. envelope fixture,
    # reviewed via snapshot.
    assert recheck == snapshot(name="recheck_envelope")

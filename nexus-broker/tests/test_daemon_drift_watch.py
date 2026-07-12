"""2.6 install-drift detection (plans/08 §2.6, Phase-B R4-T09).

Covers the node's three acceptance criteria against synthetic tmp trees only
(never the real repo's `nexus-package/` — that would path-double into the
snapshot per the deployable-engineering gotcha about install-surface tests):

  1. a seeded live-vs-package divergence is flagged within one refresh
     interval;
  2. the watcher never writes to either tree (a full byte-snapshot of both
     trees is unchanged after every scan/compare/check call);
  3. `default_pairs()`/`watch_repo()` are meta-repo-tenant-only (a bare tmp
     dir with no `nexus-package/` + `tools/build_snapshot.sh` raises).

`tools/build_snapshot.sh --check` staying green after `--sync` is verified
separately at the shell level (deployable-engineering's release-gate step);
it is not a pytest-level assertion.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from broker.daemon.drift_watch import (
    DriftWatcher,
    NotAMetaRepoError,
    WatchedPair,
    compare_pair,
    default_pairs,
    is_meta_repo_tenant,
    watch_repo,
)

PLEXUS_SELF_TESTS_BLOCK = """
PLEXUS_SELF_TESTS=(
  tests/test_batch17.py
  tests/test_batch18.py
  # a comment line, must be ignored
  tests/test_drift_guard.py
)
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    """Every regular file's full byte content, keyed by rel path — the
    positive invariant a read-only-observer test asserts against (per
    deployable-engineering's false-green-discipline rule: assert the
    invariant directly, don't just scan for an absence)."""
    if not root.is_dir():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _bump_mtime(path: Path) -> None:
    """Force a distinct, unambiguous mtime change regardless of filesystem
    clock resolution, so cache-invalidation tests never flake on a
    same-tick write."""
    new_time = time.time() + 5.0
    os.utime(path, (new_time, new_time))


# ---------------------------------------------------------------------------
# AC1 — seeded divergence flagged within one refresh interval
# ---------------------------------------------------------------------------


def test_identical_trees_report_no_drift(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)

    watcher = DriftWatcher([pair], ttl_s=1000.0)
    report = watcher.check()

    assert report.has_drift is False
    assert report.findings == ()


def test_seeded_content_divergence_flagged_on_next_refresh(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)
    watcher = DriftWatcher([pair], ttl_s=1000.0)

    baseline = watcher.check()
    assert baseline.has_drift is False

    # Seed a live-vs-package divergence: mutate ONLY the live copy.
    live_file = live / "a.py"
    live_file.write_text("x = 2\n", encoding="utf-8")
    _bump_mtime(live_file)

    report = watcher.check()  # the very next refresh — must catch it now

    assert report.has_drift is True
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.pair_label == "demo"
    assert finding.rel_path == "a.py"
    assert finding.kind == "content_mismatch"


def test_seeded_live_only_file_flagged(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)
    watcher = DriftWatcher([pair], ttl_s=1000.0)
    assert watcher.check().has_drift is False

    _write(live / "new_module.py", "y = 2\n")

    report = watcher.check()

    assert report.has_drift is True
    kinds = {(f.rel_path, f.kind) for f in report.findings}
    assert ("new_module.py", "live_only") in kinds


def test_seeded_package_only_file_flagged(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)
    watcher = DriftWatcher([pair], ttl_s=1000.0)
    assert watcher.check().has_drift is False

    _write(pkg / "stale_leftover.py", "z = 3\n")

    report = watcher.check()

    assert report.has_drift is True
    kinds = {(f.rel_path, f.kind) for f in report.findings}
    assert ("stale_leftover.py", "package_only") in kinds


def test_force_refresh_bypasses_mtime_cache(tmp_path: Path) -> None:
    """Pin the mtime back to its exact original value after mutating content
    (deterministic on any filesystem, unlike relying on clock resolution) to
    isolate the cache behavior precisely: an unchanged mtime key means the
    plain `check()` legitimately still returns the stale cached report, while
    `force=True` re-scans and catches the real divergence regardless."""
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)
    watcher = DriftWatcher([pair], ttl_s=1000.0)
    assert watcher.check().has_drift is False

    live_file = live / "a.py"
    original_stat = live_file.stat()
    live_file.write_text("x = 999\n", encoding="utf-8")
    os.utime(live_file, (original_stat.st_atime, original_stat.st_mtime))  # pin mtime identical

    cached = watcher.check()
    assert cached.has_drift is False  # mtime key unchanged -> still the stale cached report

    forced = watcher.check(force=True)
    assert forced.has_drift is True  # force bypasses the cache entirely


def test_compare_pair_one_shot_matches_watcher(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 2\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)

    findings = compare_pair(pair)

    assert len(findings) == 1
    assert findings[0].kind == "content_mismatch"


def test_only_files_mode_ignores_files_outside_named_set(tmp_path: Path) -> None:
    """The `.memory`-style pairing: only the three named memory-tooling files
    are compared, so an intentionally-divergent file like `project.db`
    (data, never byte-gated) never produces a false-positive finding."""
    live = tmp_path / "memory_live"
    pkg = tmp_path / "memory_pkg"
    _write(live / "log.py", "VERSION = 1\n")
    _write(pkg / "log.py", "VERSION = 1\n")
    _write(live / "project.db", "totally-different-live-data")
    _write(pkg / "project.db", "totally-different-package-data")
    pair = WatchedPair(
        label=".memory",
        live_root=live,
        package_root=pkg,
        only_files=frozenset({"log.py", "schema.sql", "health.py"}),
    )

    findings = compare_pair(pair)

    assert findings == []


def test_only_files_mode_flags_divergence_in_named_file(tmp_path: Path) -> None:
    live = tmp_path / "memory_live"
    pkg = tmp_path / "memory_pkg"
    _write(live / "log.py", "VERSION = 1\n")
    _write(pkg / "log.py", "VERSION = 2\n")
    pair = WatchedPair(
        label=".memory",
        live_root=live,
        package_root=pkg,
        only_files=frozenset({"log.py", "schema.sql", "health.py"}),
    )

    findings = compare_pair(pair)

    assert len(findings) == 1
    assert findings[0].rel_path == "log.py"
    assert findings[0].kind == "content_mismatch"


def test_excluded_dirs_never_produce_findings(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    # __pycache__ diverges wildly — must never be reported.
    _write(live / "__pycache__" / "a.cpython-312.pyc", "binary-ish-live")
    _write(pkg / "__pycache__" / "a.cpython-312.pyc", "different-binary-ish-pkg")
    _write(live / ".venv" / "lib" / "site.py", "only in live venv")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)

    findings = compare_pair(pair)

    assert findings == []


def test_missing_tree_reports_all_files_as_one_sided(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg_does_not_exist"
    _write(live / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)

    findings = compare_pair(pair)

    assert len(findings) == 1
    assert findings[0].kind == "live_only"


# ---------------------------------------------------------------------------
# AC2 — read-only observer: never writes to either tree
# ---------------------------------------------------------------------------


def test_watcher_never_writes_to_either_tree(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(live / "sub" / "b.py", "y = 2\n")
    _write(pkg / "a.py", "x = 1\n")
    _write(pkg / "sub" / "b.py", "y = 999\n")  # a real seeded divergence too

    live_before = _tree_snapshot(live)
    pkg_before = _tree_snapshot(pkg)

    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)
    watcher = DriftWatcher([pair], ttl_s=0.01)
    # Exercise every read path repeatedly, including the TTL-expiry re-scan
    # branch and the one-shot `compare_pair` entry point.
    for _ in range(3):
        report = watcher.check()
        time.sleep(0.02)
    assert report.has_drift is True
    compare_pair(pair)
    pair.scan_live()
    pair.scan_package()

    live_after = _tree_snapshot(live)
    pkg_after = _tree_snapshot(pkg)

    assert live_after == live_before
    assert pkg_after == pkg_before
    # No stray files anywhere under the tmp root either (no lockfiles, no
    # sidecar cache files written next to the watched trees).
    expected_files = {Path("live") / k for k in live_before} | {Path("pkg") / k for k in pkg_before}
    actual_files = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    assert actual_files == expected_files


def test_scan_functions_are_pure_reads_no_mutation_of_inputs(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    _write(pkg / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)

    mtime_before_live = (live / "a.py").stat().st_mtime
    mtime_before_pkg = (pkg / "a.py").stat().st_mtime

    pair.scan_live()
    pair.scan_package()
    compare_pair(pair)

    assert (live / "a.py").stat().st_mtime == mtime_before_live
    assert (pkg / "a.py").stat().st_mtime == mtime_before_pkg


# ---------------------------------------------------------------------------
# AC3 — meta-repo tenant only
# ---------------------------------------------------------------------------


def test_is_meta_repo_tenant_false_for_bare_dir(tmp_path: Path) -> None:
    assert is_meta_repo_tenant(tmp_path) is False


def test_default_pairs_raises_for_non_meta_repo(tmp_path: Path) -> None:
    with pytest.raises(NotAMetaRepoError):
        default_pairs(tmp_path)


def test_watch_repo_raises_for_non_meta_repo(tmp_path: Path) -> None:
    with pytest.raises(NotAMetaRepoError):
        watch_repo(tmp_path)


def _build_fake_meta_repo(root: Path) -> None:
    """A synthetic meta-repo-shaped tree under tmp_path — never the real
    repo's own `nexus-package/` (that would be an install-surface test per
    the deployable-engineering gotcha; this stays fully self-contained)."""
    _write(root / "tools" / "build_snapshot.sh", PLEXUS_SELF_TESTS_BLOCK)
    (root / "nexus-package").mkdir(parents=True, exist_ok=True)
    _write(root / "nexus-broker" / "src" / "broker" / "mod.py", "VALUE = 1\n")
    _write(root / "nexus-package" / "nexus-broker" / "src" / "broker" / "mod.py", "VALUE = 1\n")
    _write(root / "nexus-broker" / "tests" / "test_batch17.py", "# self-test, live only\n")
    _write(root / "nexus-broker" / "tests" / "test_real.py", "# real test\n")
    _write(root / "nexus-package" / "nexus-broker" / "tests" / "test_real.py", "# real test\n")
    _write(root / ".memory" / "log.py", "VERSION = 1\n")
    _write(root / ".memory" / "schema.sql", "CREATE TABLE t (id INTEGER);\n")
    _write(root / ".memory" / "health.py", "def check(): return True\n")
    _write(root / "nexus-package" / ".memory" / "log.py", "VERSION = 1\n")
    _write(root / "nexus-package" / ".memory" / "schema.sql", "CREATE TABLE t (id INTEGER);\n")
    _write(root / "nexus-package" / ".memory" / "health.py", "def check(): return True\n")


def test_is_meta_repo_tenant_true_for_fake_meta_repo(tmp_path: Path) -> None:
    _build_fake_meta_repo(tmp_path)
    assert is_meta_repo_tenant(tmp_path) is True


def test_default_pairs_clean_fake_meta_repo_has_no_drift(tmp_path: Path) -> None:
    _build_fake_meta_repo(tmp_path)

    watcher = watch_repo(tmp_path, ttl_s=1000.0)
    report = watcher.check()

    assert report.has_drift is False


def test_default_pairs_self_test_exclusion_parsed_from_build_snapshot_sh(tmp_path: Path) -> None:
    """`tests/test_batch17.py` exists live-only by design (a Plexus self-test
    stripped from the snapshot per `PLEXUS_SELF_TESTS`) and must NOT be
    flagged — proves the parser in `_self_test_exclusions` actually wired
    into `default_pairs()`."""
    _build_fake_meta_repo(tmp_path)

    watcher = watch_repo(tmp_path, ttl_s=1000.0)
    report = watcher.check()

    flagged_paths = {f.rel_path for f in report.findings}
    assert "test_batch17.py" not in flagged_paths


def test_default_pairs_flags_real_divergence_in_fake_meta_repo(tmp_path: Path) -> None:
    _build_fake_meta_repo(tmp_path)
    live_mod = tmp_path / "nexus-broker" / "src" / "broker" / "mod.py"
    live_mod.write_text("VALUE = 2\n", encoding="utf-8")
    _bump_mtime(live_mod)

    watcher = watch_repo(tmp_path, ttl_s=1000.0)
    report = watcher.check()

    assert report.has_drift is True
    assert any(
        f.rel_path == "broker/mod.py" and f.pair_label == "nexus-broker/src"
        for f in report.findings
    )


def test_default_pairs_memory_file_divergence_flagged(tmp_path: Path) -> None:
    _build_fake_meta_repo(tmp_path)
    live_log = tmp_path / ".memory" / "log.py"
    live_log.write_text("VERSION = 2\n", encoding="utf-8")
    _bump_mtime(live_log)

    watcher = watch_repo(tmp_path, ttl_s=1000.0)
    report = watcher.check()

    assert report.has_drift is True
    assert any(f.rel_path == "log.py" and f.pair_label == ".memory (memory-tooling)" for f in report.findings)


def test_drift_report_as_dict_shape(tmp_path: Path) -> None:
    live = tmp_path / "live"
    pkg = tmp_path / "pkg"
    _write(live / "a.py", "x = 1\n")
    pair = WatchedPair(label="demo", live_root=live, package_root=pkg)
    watcher = DriftWatcher([pair], ttl_s=1000.0)

    report = watcher.check()
    payload = report.as_dict()

    assert payload["has_drift"] is True
    assert isinstance(payload["checked_at"], float)
    assert payload["findings"] == [{"pair": "demo", "path": "a.py", "kind": "live_only"}]

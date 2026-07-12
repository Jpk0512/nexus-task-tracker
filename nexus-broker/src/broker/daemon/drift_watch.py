"""2.6 install-drift detection — read-only, continuous comparison of the LIVE
`nexus-broker/` + `.memory/` trees against their `nexus-package/` twins
(plans/08-daemon-capability-catalog.md §2.6, Phase-B / R4-T09).

`tools/build_snapshot.sh --check` REMAINS the authoritative drift gate (per
`CLAUDE.md`'s Deployable Engineering layer map) — this module adds DETECTION
LATENCY only: an early-warning signal that can fire continuously between
`build_snapshot` runs, never authority. A green `DriftReport` here is not a
release gate and must never be treated as one.

Refresh posture matches 1.1's `_RegistryCache` (`server.py`): a cheap mtime
key is recomputed on every `check()` call, and the (comparatively expensive)
content-hash comparison only re-runs when that key changed or the TTL
elapsed — file-change-driven, not a fixed poll cadence, and no new watcher
dependency (stdlib `pathlib.rglob` + `hashlib`, same posture as the rest of
this stdlib-only daemon per `plans/07` §2 Option C).

Read-only observer: every function in this module only ever calls `.stat()`,
`.is_dir()`, `.is_file()`, `.rglob()`, and `.read_bytes()` on the trees it
watches. Nothing here opens a file for writing, creates, deletes, or renames
anything in either tree.

Meta-repo tenant only: `nexus-package/` (the build-snapshot twin this module
diffs against) is Plexus's own build artifact — it does not exist on an
installed (product) tenant, so `default_pairs()`/`watch_repo()` refuse to run
against a tree that isn't recognizably the Plexus meta-repo (see
`NotAMetaRepoError`). The comparison primitives (`WatchedPair`, `compare_pair`,
`DriftWatcher`) are otherwise generic and exercised directly against tmp
trees in tests, independent of that guard.

Wired into `broker.daemon.server`'s lifecycle via `start_drift_watch()` (N31,
plans/14 SS6): a periodic background task under the daemon's own event loop,
gated by `is_meta_repo_tenant()` — not a dispatch-table RPC method. No
`drift_status` method exists on `handle_request` yet; a future node may still
add one to expose the running watcher's latest `DriftReport` over the wire
without changing this module's shape.
"""
from __future__ import annotations

import contextlib
import hashlib
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".hypothesis",
        ".claude",
        ".git",
    }
)

# Mirrors `RSYNC_EXCLUDES` file-suffix entries in `tools/build_snapshot.sh`
# (`*.pyc`, `*.db`, `*.db-shm`, `*.db-wal`, `*.egg-info`) — runtime/build
# artifacts the snapshot pipeline itself never treats as synced content.
_DEFAULT_EXCLUDE_SUFFIXES: tuple[str, ...] = (".pyc", ".db", ".db-shm", ".db-wal", ".egg-info")


def _is_excluded_dir_part(part: str, exclude_dir_names: frozenset[str]) -> bool:
    return part in exclude_dir_names or part.endswith(".egg-info")


def _scan_tree(
    root: Path,
    exclude_dir_names: frozenset[str],
    exclude_rel_paths: frozenset[str],
    exclude_suffixes: tuple[str, ...],
) -> dict[str, Path]:
    """rel-posix-path -> abs Path for every regular file under `root` that
    survives exclusion. Missing `root` -> empty dict, not an error (matches
    `registry_scan.scan_agents`' missing-dir posture). Read-only: iteration
    and `.stat()`/`.is_file()` only.
    """
    out: dict[str, Path] = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        rel_str = rel.as_posix()
        if rel_str in exclude_rel_paths:
            continue
        if any(_is_excluded_dir_part(part, exclude_dir_names) for part in rel.parts[:-1]):
            continue
        if any(rel_str.endswith(suf) for suf in exclude_suffixes):
            continue
        out[rel_str] = p
    return out


@dataclass(frozen=True)
class WatchedPair:
    """One live-tree vs package-tree comparison root.

    `only_files`, when set, restricts the comparison universe to exactly
    those root-relative filenames (the `.memory/{log.py,schema.sql,health.py}`
    case — the only three files `build_snapshot.sh`'s `SYNCED_MEMORY_FILES`
    treats as byte-identical-by-contract; the rest of `.memory/` legitimately
    differs, e.g. `project.db` is data and `sync_docs.py` is hand-reconciled
    per `build_snapshot.sh`'s own comment). When `None`, the whole tree is
    walked recursively (the `nexus-broker/src` and `nexus-broker/tests`
    case).
    """

    label: str
    live_root: Path
    package_root: Path
    only_files: frozenset[str] | None = None
    exclude_dir_names: frozenset[str] = _DEFAULT_EXCLUDE_DIR_NAMES
    exclude_rel_paths: frozenset[str] = frozenset()
    exclude_suffixes: tuple[str, ...] = _DEFAULT_EXCLUDE_SUFFIXES

    def _scan(self, root: Path) -> dict[str, Path]:
        if self.only_files is not None:
            out: dict[str, Path] = {}
            for name in self.only_files:
                p = root / name
                if p.is_file():
                    out[name] = p
            return out
        return _scan_tree(root, self.exclude_dir_names, self.exclude_rel_paths, self.exclude_suffixes)

    def scan_live(self) -> dict[str, Path]:
        return self._scan(self.live_root)

    def scan_package(self) -> dict[str, Path]:
        return self._scan(self.package_root)


@dataclass(frozen=True)
class DriftFinding:
    pair_label: str
    rel_path: str
    kind: str  # "content_mismatch" | "live_only" | "package_only"


@dataclass(frozen=True)
class DriftReport:
    checked_at: float
    findings: tuple[DriftFinding, ...] = field(default_factory=tuple)

    @property
    def has_drift(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "has_drift": self.has_drift,
            "findings": [
                {"pair": f.pair_label, "path": f.rel_path, "kind": f.kind} for f in self.findings
            ],
        }


def _hash_bytes(path: Path) -> str | None:
    """Read-only content hash. Never raises — an unreadable/vanished file
    (e.g. a benign race with a concurrent `build_snapshot` run) surfaces as a
    `live_only`/`package_only` finding from the scan step instead of crashing
    the watcher; `None != None` is impossible here since both sides are only
    hashed when both scans reported the file present.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _compare_files(
    pair: WatchedPair, live_files: dict[str, Path], package_files: dict[str, Path]
) -> list[DriftFinding]:
    findings: list[DriftFinding] = []
    for rel in sorted(set(live_files) | set(package_files)):
        live_p = live_files.get(rel)
        pkg_p = package_files.get(rel)
        if live_p is not None and pkg_p is None:
            findings.append(DriftFinding(pair.label, rel, "live_only"))
        elif pkg_p is not None and live_p is None:
            findings.append(DriftFinding(pair.label, rel, "package_only"))
        elif _hash_bytes(live_p) != _hash_bytes(pkg_p):
            findings.append(DriftFinding(pair.label, rel, "content_mismatch"))
    return findings


def compare_pair(pair: WatchedPair) -> list[DriftFinding]:
    """One-shot pure read-only diff of a single pair (no caching). Never
    writes to either tree."""
    return _compare_files(pair, pair.scan_live(), pair.scan_package())


def _mtime_key_for(files: dict[str, Path]) -> tuple[tuple[str, float], ...]:
    stamps: list[tuple[str, float]] = []
    for rel, p in files.items():
        with contextlib.suppress(OSError):
            stamps.append((rel, p.stat().st_mtime))
    return tuple(sorted(stamps))


class DriftWatcher:
    """Continuous read-only install-drift detector over N `WatchedPair`s.

    `check()` recomputes a cheap mtime key (a stat-walk, no content reads)
    every call; the actual content-hash comparison — the expensive part —
    only re-runs when that key changed since the last check, or the TTL
    elapsed, or `force=True`. This mirrors `server.py`'s `_RegistryCache`
    cache-invalidation posture (1.1) applied to drift detection instead of
    registry parsing.
    """

    def __init__(self, pairs: Sequence[WatchedPair], ttl_s: float = 30.0) -> None:
        self.pairs = list(pairs)
        self.ttl_s = ttl_s
        self._loaded_at = 0.0
        self._mtime_key: tuple[Any, ...] | None = None
        self._report: DriftReport | None = None

    def check(self, force: bool = False) -> DriftReport:
        now = time.monotonic()
        scanned = [(pair, pair.scan_live(), pair.scan_package()) for pair in self.pairs]
        key = tuple(
            (pair.label, _mtime_key_for(live), _mtime_key_for(pkg)) for pair, live, pkg in scanned
        )
        stale = (now - self._loaded_at) > self.ttl_s
        if force or self._report is None or key != self._mtime_key or stale:
            findings: list[DriftFinding] = []
            for pair, live, pkg in scanned:
                findings.extend(_compare_files(pair, live, pkg))
            self._report = DriftReport(checked_at=time.time(), findings=tuple(findings))
            self._mtime_key = key
            self._loaded_at = now
        return self._report


class NotAMetaRepoError(RuntimeError):
    """Raised by `default_pairs()`/`watch_repo()` when asked to watch a tree
    that is not the Plexus meta-repo. Install-drift detection (plans/08 §2.6)
    is a meta-repo-tenant-only capability — `nexus-package/`, the build-
    snapshot twin every comparison here is against, does not exist on an
    installed product tenant.
    """


def is_meta_repo_tenant(repo_root: Path) -> bool:
    repo_root = Path(repo_root)
    return (repo_root / "nexus-package").is_dir() and (
        repo_root / "tools" / "build_snapshot.sh"
    ).is_file()


_SELF_TESTS_RE = re.compile(r"PLEXUS_SELF_TESTS=\((.*?)\)", re.DOTALL)


def _self_test_exclusions(build_snapshot_sh: Path) -> frozenset[str]:
    """Best-effort parse of `PLEXUS_SELF_TESTS` straight out of
    `build_snapshot.sh` so this watcher's `nexus-broker/tests` exclusion list
    can never silently drift from the script's own list (two hand-maintained
    copies of the same array is exactly the kind of drift this module exists
    to catch elsewhere). Never raises: a parse miss just yields an empty
    exclusion set, which surfaces as extra (accurate, low-cost) `live_only`
    findings for the self-test files rather than a silent false negative.
    """
    try:
        text = build_snapshot_sh.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    match = _SELF_TESTS_RE.search(text)
    if not match:
        return frozenset()
    entries: set[str] = set()
    for line in match.group(1).splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("tests/"):
            entries.add(stripped)
    return frozenset(entries)


def default_pairs(repo_root: Path) -> list[WatchedPair]:
    """The real Plexus meta-repo watch set: `nexus-broker/src` + `.../tests`
    (mirroring `build_snapshot.sh`'s own rsync targets and self-test
    exclusions) plus the three byte-gated `.memory/` memory-tooling files.
    Raises `NotAMetaRepoError` against any tree lacking `nexus-package/` +
    `tools/build_snapshot.sh`.
    """
    repo_root = Path(repo_root)
    if not is_meta_repo_tenant(repo_root):
        raise NotAMetaRepoError(
            f"{repo_root} has no nexus-package/ + tools/build_snapshot.sh — "
            "install-drift detection (plans/08 §2.6) is a meta-repo-tenant-only capability"
        )
    broker_live = repo_root / "nexus-broker"
    broker_pkg = repo_root / "nexus-package" / "nexus-broker"
    memory_live = repo_root / ".memory"
    memory_pkg = repo_root / "nexus-package" / ".memory"
    # `PLEXUS_SELF_TESTS` entries are `nexus-broker/`-relative (e.g.
    # `tests/test_batch17.py`); the `nexus-broker/tests` pair's own root
    # IS `nexus-broker/tests`, so its `exclude_rel_paths` must be rooted one
    # level deeper — strip the leading `tests/` each entry carries.
    self_tests = _self_test_exclusions(repo_root / "tools" / "build_snapshot.sh")
    tests_exclusions = frozenset(p.removeprefix("tests/") for p in self_tests)
    return [
        WatchedPair(
            label="nexus-broker/src",
            live_root=broker_live / "src",
            package_root=broker_pkg / "src",
        ),
        WatchedPair(
            label="nexus-broker/tests",
            live_root=broker_live / "tests",
            package_root=broker_pkg / "tests",
            exclude_rel_paths=tests_exclusions,
        ),
        WatchedPair(
            label=".memory (memory-tooling)",
            live_root=memory_live,
            package_root=memory_pkg,
            only_files=frozenset({"log.py", "schema.sql", "health.py"}),
        ),
    ]


def watch_repo(repo_root: Path, ttl_s: float = 30.0) -> DriftWatcher:
    """Convenience constructor: `DriftWatcher(default_pairs(repo_root), ttl_s)`."""
    return DriftWatcher(default_pairs(repo_root), ttl_s=ttl_s)

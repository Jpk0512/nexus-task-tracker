"""NATIVE-19 — planning-gate Item 7 test-stub globs are PROFILE-AWARE.

Item 7 of the planning gate ("Feature-tagged test stubs exist") used to HARDCODE
its test-stub globs to ``ingestion/tests/**`` + ``app/**``, so projects whose tests
live elsewhere (insites: ``web/e2e/`` for Playwright + ``api/tests/`` for pytest)
permanently FAILED the gate. This upstreams the insites DEC-010 bridge: the globs are
now DERIVED from the project's ``.memory/nexus-stack.json`` profile, with the original
ingestion/app patterns retained as a fallback for no/partial-profile projects.

These are POSITIVE-invariant tests: we drop real feature-tagged stub files on disk in
a fake project tree and assert the profile-derived globs actually MATCH them (i.e. the
behaviour happens), plus that a no-profile project yields [] so the fallback governs.

IMPORT STRATEGY mirrors test_memory_heuristics.py: ``.memory/log.py`` lives outside the
``broker`` package under a hyphenated dir, so we spec-from-file import it once. The
functions under test are pure path logic — no DB, no network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

_LOG_PY = Path(__file__).resolve().parents[2] / ".memory" / "log.py"


def _load_memlog() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nexus_memlog_pg_globs", _LOG_PY)
    assert spec and spec.loader, f"cannot build import spec for {_LOG_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


memlog = _load_memlog()


_INSITES_PROFILE = {
    "schema_version": "1.0",
    "frontend": {
        "present": True,
        "framework": "vite",
        "src_dir": "web/src",
        "test_dir": "web/src",
        "ts_check_dir": "web",
        "test_runner": "vitest",
    },
    "backend": {
        "present": True,
        "framework": "fastapi",
        "language": "python",
        "src_dir": "api",
        "py_check_dir": "api worker",
    },
}


def _write_profile(root: Path, profile: dict) -> None:
    mem = root / ".memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "nexus-stack.json").write_text(json.dumps(profile), encoding="utf-8")


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("// stub\n", encoding="utf-8")


def _matches(root: Path, globs: list[str]) -> list[Path]:
    found: list[Path] = []
    for g in globs:
        found.extend(root.glob(g))
    return found


# ── positive invariant: web/+api/ profile finds web/e2e + api/tests stubs ─────────


def test_profile_globs_match_web_e2e_playwright_stub(tmp_path: Path) -> None:
    """A vitest/playwright frontend profile yields globs that MATCH web/e2e/*feat1*."""
    _write_profile(tmp_path, _INSITES_PROFILE)
    stub = tmp_path / "web" / "e2e" / "feat1-smoke.spec.ts"
    _touch(stub)

    globs = memlog._profile_aware_test_globs(tmp_path, {"1", "001"}, "smoke-suite")
    assert globs, "profile present -> must derive non-empty globs"
    assert stub in _matches(tmp_path, globs), "web/e2e feat-tagged stub must be found"


def test_profile_globs_match_api_tests_pytest_stub(tmp_path: Path) -> None:
    """A python backend profile (src_dir=api) yields globs matching api/tests/test_*feat1*.py."""
    _write_profile(tmp_path, _INSITES_PROFILE)
    stub = tmp_path / "api" / "tests" / "test_feat1_endpoints.py"
    _touch(stub)

    globs = memlog._profile_aware_test_globs(tmp_path, {"1", "001"}, "smoke-suite")
    assert stub in _matches(tmp_path, globs), "api/tests feat-tagged stub must be found"


def test_profile_globs_match_spec_slug_stub(tmp_path: Path) -> None:
    """The spec-slug variant matches both a web slug stub and an api slug stub."""
    _write_profile(tmp_path, _INSITES_PROFILE)
    web_stub = tmp_path / "web" / "src" / "pages" / "checkout-flow.test.tsx"
    api_stub = tmp_path / "api" / "tests" / "test_checkout_flow.py"
    _touch(web_stub)
    _touch(api_stub)

    globs = memlog._profile_aware_test_globs(tmp_path, {"7", "007"}, "checkout-flow")
    found = _matches(tmp_path, globs)
    assert web_stub in found, "web src spec-slug stub must be found"
    assert api_stub in found, "api/tests spec-slug stub must be found"


def test_profile_globs_use_worker_dir_from_py_check_dir(tmp_path: Path) -> None:
    """py_check_dir='api worker' is space-split, so worker/ test dirs are covered too."""
    _write_profile(tmp_path, _INSITES_PROFILE)
    stub = tmp_path / "worker" / "tests" / "test_feat1_job.py"
    _touch(stub)

    globs = memlog._profile_aware_test_globs(tmp_path, {"1", "001"}, "smoke-suite")
    assert stub in _matches(tmp_path, globs), "worker test dir from py_check_dir must be covered"


# ── fallback invariant: no profile -> [] (caller keeps ingestion/app defaults) ────


def test_no_profile_yields_empty_so_fallback_governs(tmp_path: Path) -> None:
    """A project with NO nexus-stack.json derives no profile globs -> fallback governs."""
    # deliberately no .memory/nexus-stack.json
    globs = memlog._profile_aware_test_globs(tmp_path, {"1", "001"}, "smoke-suite")
    assert globs == [], "missing profile must yield [] (ingestion/app fallback applies)"


def test_empty_profile_yields_empty(tmp_path: Path) -> None:
    """An empty/garbage profile is non-fatal and yields [] (fallback governs)."""
    _write_profile(tmp_path, {})
    assert memlog._profile_aware_test_globs(tmp_path, {"1", "001"}, "x") == []


def test_malformed_profile_json_is_non_fatal(tmp_path: Path) -> None:
    """Unreadable/garbage JSON must not raise — returns {} -> [] so the gate still runs."""
    mem = tmp_path / ".memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "nexus-stack.json").write_text("{ not json", encoding="utf-8")
    assert memlog._stack_profile_for_gate(tmp_path) == {}
    assert memlog._profile_aware_test_globs(tmp_path, {"1"}, "x") == []


# ── ingestion/app default project still passes via the retained hardcoded fallback ─


def test_ingestion_app_profile_still_supported(tmp_path: Path) -> None:
    """A canonical ingestion/app profile derives globs that match its own stub too."""
    profile = {
        "frontend": {"present": True, "src_dir": "app", "test_runner": "vitest"},
        "data": {"has_ingestion": True, "ingestion_dir": "ingestion"},
    }
    _write_profile(tmp_path, profile)
    py_stub = tmp_path / "ingestion" / "tests" / "test_feat1_transform.py"
    ts_stub = tmp_path / "app" / "dashboard" / "feat1-widget.test.ts"
    _touch(py_stub)
    _touch(ts_stub)

    globs = memlog._profile_aware_test_globs(tmp_path, {"1", "001"}, "transform")
    found = _matches(tmp_path, globs)
    assert py_stub in found
    assert ts_stub in found

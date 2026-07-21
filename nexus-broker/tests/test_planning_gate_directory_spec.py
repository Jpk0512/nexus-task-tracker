"""fleet FB-3 (Qa Dashboard #2) — planning-gate accepts DIRECTORY-style specs.

`cmd_planning_gate_check` previously matched ONLY flat `docs/features/{feat}-*.md`
files via a single glob, so a directory-style spec (`docs/features/FEAT-004-
ai-reviewers/design.md`, `data-model.md` — an established convention on fleet
projects) was invisible: items 1/2/4/6 false-FAILed a live incident-fix dispatch.
These tests drive the REAL function against fixtures dropped under a
per-test `tmp_path` (see `spec_dir_root` below) rather than re-implementing
the glob logic.

`cmd_planning_gate_check` derives its `docs/` root from `Path(__file__).resolve()
.parent.parent` (relative to wherever `.memory/log.py` was loaded from) — there is
no env var or arg to redirect it, and `nexus-broker/src` is out of scope for this
leg. `spec_dir_root` achieves per-test isolation by monkeypatching the loaded
memlog module's own `__file__` attribute to a per-test tmp_path stand-in, so the
computed docs-root lands under `tmp_path` instead of a single shared directory —
no fixed shared path, no cross-test rmtree race under parallel/nested runs.

IMPORT STRATEGY mirrors test_planning_gate_profile_globs.py: `.memory/log.py`
lives outside the `broker` package under a hyphenated dir, so it is spec-from-file
imported once.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_LOG_PY = Path(__file__).resolve().parents[2] / ".memory" / "log.py"


def _load_memlog() -> ModuleType:
    spec = importlib.util.spec_from_file_location("nexus_memlog_pg_dirspec", _LOG_PY)
    assert spec and spec.loader, f"cannot build import spec for {_LOG_PY}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


memlog = _load_memlog()


@pytest.fixture
def spec_dir_root(tmp_path, monkeypatch) -> Path:
    """Per-test `docs/features/` root, isolated via a monkeypatched module
    `__file__` rather than a fixed shared path — each test gets its own
    tmp_path subtree, so there is nothing for a sibling test's teardown to
    race against, and pytest cleans tmp_path up on its own (no rmtree here)."""
    fake_memory_dir = tmp_path / ".memory"
    fake_memory_dir.mkdir()
    monkeypatch.setattr(memlog, "__file__", str(fake_memory_dir / "log.py"))
    return tmp_path / "docs" / "features"


def _run_check(feat_id: str, capsys) -> dict:
    try:
        memlog.cmd_planning_gate_check(argparse.Namespace(feat=feat_id))
    except SystemExit:
        pass
    return json.loads(capsys.readouterr().out)


_DESIGN_MD = (
    "# Design\n\nGiven a request, When it is processed, Then a response is returned.\n\n"
    "## Constitution Check\n- [x] Article I — spec-first\n"
)
_DATA_MODEL_MD = "# Data Model\n\nCREATE TABLE widgets (id INTEGER PRIMARY KEY);\n"


def test_directory_style_spec_passes_items_1_2_4_6(capsys, spec_dir_root) -> None:
    spec_dir = spec_dir_root / "FEAT-999-dir-spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "design.md").write_text(_DESIGN_MD, encoding="utf-8")
    (spec_dir / "data-model.md").write_text(_DATA_MODEL_MD, encoding="utf-8")

    result = _run_check("FEAT-999", capsys)
    items = {item["item"]: item for item in result["items"]}

    assert items[1]["passed"] is True, items[1]["detail"]
    assert items[1]["detail"] == str(spec_dir)
    assert items[2]["passed"] is True, "GWT not detected across concatenated *.md"
    assert items[4]["passed"] is True, "Article checklist not detected"
    assert items[6]["passed"] is True, "CREATE TABLE not detected"
    # Item-7 spec_slug derives from the DIRECTORY name, not a filename.
    assert "'dir-spec'" in items[7]["detail"]


def test_flat_file_spec_still_works(capsys, spec_dir_root) -> None:
    spec_dir_root.mkdir(parents=True)
    (spec_dir_root / "FEAT-998-flat-spec.md").write_text(
        _DESIGN_MD + _DATA_MODEL_MD, encoding="utf-8"
    )

    result = _run_check("FEAT-998", capsys)
    items = {item["item"]: item for item in result["items"]}

    assert items[1]["passed"] is True, items[1]["detail"]
    assert items[2]["passed"] is True
    assert items[4]["passed"] is True
    assert items[6]["passed"] is True
    assert "'flat-spec'" in items[7]["detail"]


def test_missing_spec_detail_names_both_accepted_forms(capsys, spec_dir_root) -> None:
    del spec_dir_root  # isolates docs-root from the real worktree; nothing to create
    result = _run_check("FEAT-997", capsys)
    items = {item["item"]: item for item in result["items"]}

    assert items[1]["passed"] is False
    assert "docs/features/FEAT-997-*.md" in items[1]["detail"]
    assert "docs/features/FEAT-997-*/" in items[1]["detail"]

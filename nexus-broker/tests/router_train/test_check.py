"""check() — the loud deterministic integrity gate (00-DESIGN.md 'integrity check gate').

Pins the three contract behaviors the design names verbatim:
  - FAIL + non-zero exit on a 0-labeled fixture (OPT-053 non-empty-collection gate)
  - PASS on a labeled fixture
  - the referenced-artifact gate FAILs on a dangling path
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from broker.router_train import (
    LABEL_STATUS_DROPPED_GENERIC,
    LABEL_STATUS_OK,
    LABEL_STATUS_QUARANTINED_RETIRED,
)
from broker.router_train.check import check, main, referenced_artifacts, render


def _labeled(
    record: dict[str, Any],
    dispatch: dict[str, Any],
    *,
    label_status: str = LABEL_STATUS_OK,
) -> dict[str, Any]:
    """Promote a clean capture record to a fully-labeled training row."""
    row = dict(record)
    row["label_persona"] = dispatch["dispatched_persona"]
    row["label_source"] = "dispatch_sidecar"
    row["label_confidence"] = 1.0
    row["labeled_at"] = dispatch["ts"]
    row["label_status"] = label_status
    return row


def _empty_hooks(tmp_path: Path) -> tuple[Path, ...]:
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    return (hook_dir,)


def test_zero_labeled_fixture_fails(
    clean_record: dict[str, Any], tmp_path: Path
) -> None:
    """An unlabeled corpus (the migrated 604) must FAIL the OPT-053 gate."""
    report = check([clean_record], hook_dirs=_empty_hooks(tmp_path))
    assert not report.ok
    assert any("labeled == 0" in f for f in report.failures)
    assert report.labeled == 0
    assert report.total == 1


def test_labeled_fixture_passes(
    clean_record: dict[str, Any],
    clean_dispatch: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A fully-labeled, schema-valid corpus with all artifacts resolving must PASS."""
    row = _labeled(clean_record, clean_dispatch)
    report = check([row], hook_dirs=_empty_hooks(tmp_path))
    assert report.ok, report.failures
    assert report.labeled == 1
    assert report.coverage == 1.0
    assert report.coverage_by_source == {"dispatch_sidecar": 1}
    assert report.quarantine_count == 0


def test_referenced_artifact_gate_fails_on_dangling_path(
    clean_record: dict[str, Any],
    clean_dispatch: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A hook docstring naming a script that does not exist must FAIL the gate.

    This is the exact phantom-harvester failure mode: a lying docstring referencing
    `python -m broker.router_train.harvest` (never written) reads as 'done'.
    """
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "router.py").write_text(
        '"""Router hook.\n\n'
        "Harvest with `python -m broker.router_train.phantom_harvester`.\n"
        '"""\n'
    )
    row = _labeled(clean_record, clean_dispatch)
    report = check([row], hook_dirs=(hook_dir,))
    assert not report.ok
    assert "broker.router_train.phantom_harvester" in report.dangling_artifacts
    assert any("referenced-artifact gate" in f for f in report.failures)


def test_referenced_artifact_gate_passes_on_real_module(tmp_path: Path) -> None:
    """A docstring naming a module that DOES resolve must not be flagged."""
    hook_dir = tmp_path / "hooks"
    hook_dir.mkdir()
    (hook_dir / "router.py").write_text(
        '"""Check with `python -m broker.router_train.check`."""\n'
    )
    resolved = referenced_artifacts((hook_dir,))
    assert resolved.get("broker.router_train.check") is True


def test_quarantine_counted(
    buggy_record: dict[str, Any], tmp_path: Path
) -> None:
    report = check([buggy_record], hook_dirs=_empty_hooks(tmp_path))
    assert report.quarantine_count == 1


def test_dupes_reported(
    clean_record: dict[str, Any],
    clean_dispatch: dict[str, Any],
    tmp_path: Path,
) -> None:
    row = _labeled(clean_record, clean_dispatch)
    report = check([row, dict(row)], hook_dirs=_empty_hooks(tmp_path))
    assert report.dupe_count == 1
    assert report.dupe_samples == [row["prompt_hash"]]


def test_main_nonzero_exit_on_zero_labeled(
    clean_record: dict[str, Any], tmp_path: Path
) -> None:
    """The CLI entrypoint must exit non-zero on FAIL (loud deterministic gate)."""
    fixture = tmp_path / "decisions.jsonl"
    fixture.write_text(json.dumps(clean_record) + "\n")
    assert main([str(fixture)]) == 1


def test_main_zero_exit_on_labeled(
    clean_record: dict[str, Any],
    clean_dispatch: dict[str, Any],
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A labeled fixture passed on the CLI exits zero — but only when the live hook
    tree's referenced artifacts all resolve. We pin the artifact gate to an empty
    hook dir so this asserts the OPT-053 + schema path, not the live tree state."""
    import broker.router_train.check as check_mod

    empty = tmp_path / "hooks"
    empty.mkdir()
    monkeypatch.setattr(check_mod, "HOOK_DIRS", (empty,))

    row = _labeled(clean_record, clean_dispatch)
    fixture = tmp_path / "decisions.jsonl"
    fixture.write_text(json.dumps(row) + "\n")
    assert main([str(fixture)]) == 0


def test_label_status_distribution_surfaces_pollution(
    clean_record: dict[str, Any],
    clean_dispatch: dict[str, Any],
    general_purpose_record: dict[str, Any],
    general_purpose_dispatch: dict[str, Any],
    tmp_path: Path,
) -> None:
    """check() reports the ok / dropped_generic / quarantined_retired distribution
    and counts class balance over ok rows only."""
    ok_row = _labeled(clean_record, clean_dispatch)
    generic_row = _labeled(
        general_purpose_record,
        general_purpose_dispatch,
        label_status=LABEL_STATUS_DROPPED_GENERIC,
    )
    retired_row = dict(ok_row)
    retired_row["prompt"] = "a retired-base prompt"
    retired_row["prompt_hash"] = "f" * 64
    retired_row["label_persona"] = "forge"
    retired_row["label_status"] = LABEL_STATUS_QUARANTINED_RETIRED

    report = check(
        [ok_row, generic_row, retired_row], hook_dirs=_empty_hooks(tmp_path)
    )
    assert report.label_status_balance == {
        LABEL_STATUS_OK: 1,
        LABEL_STATUS_DROPPED_GENERIC: 1,
        LABEL_STATUS_QUARANTINED_RETIRED: 1,
    }
    assert report.training_grade_count == 1
    # class balance counts ok rows ONLY — neither general-purpose nor forge appears.
    assert report.class_balance == {"pipeline-data": 1}
    assert "general-purpose" not in report.class_balance
    assert "forge" not in report.class_balance
    # a polluted (generic/retired) row must NOT trip the schema-violation gate.
    assert report.schema_violation_count == 0, report.schema_violations_by_field
    rendered = render(report)
    assert "label status" in rendered
    assert LABEL_STATUS_DROPPED_GENERIC in rendered

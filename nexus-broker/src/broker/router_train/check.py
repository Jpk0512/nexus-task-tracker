"""CHECK — the loud deterministic integrity gate (00-DESIGN.md 'integrity check gate').

A single reproducible command so "checking the data" is a command, not an opinion:
every actor that runs `python -m broker.router_train.check` gets the identical verdict.
This kills the "different wrong every time" loop (OPT-053 applied).

It reports, then exits non-zero on FAIL:
  - row count (total + post-dedupe distinct by prompt_hash)
  - label coverage % broken down by label_source
  - label_status distribution (ok / dropped_generic / quarantined_retired /
    quarantined_buggy) — surfaces LABEL POLLUTION explicitly so "checking the data"
    names the non-training-grade rows instead of silently exporting them
  - schema violations by field (router_training_record.schema.json), over ok rows only
  - dupes (+ sample prompt_hash collisions)
  - class balance (label_persona distribution over label_status=="ok" ONLY; flag
    classes < MIN_CLASS_EXAMPLES) — the balance the training set actually sees
  - provenance integrity (rows missing model_id / router_version / schema_version)
  - quarantine count (router_version == "buggy")
  - referenced-artifact-exists gate (every script/module path named in a shipped hook
    docstring resolves to a real file — the exact check that would have caught the
    phantom harvester)
  - OPT-053 non-empty-collection gate (FAIL if total == 0 OR labeled == 0 OR
    coverage < COVERAGE_THRESHOLD)

check() is pure (returns a Report); render()/main() do the I/O so export() and CI can
call check() in-process without capturing stdout.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from broker.router_train.aggregate import aggregate, prompt_hash
from broker.router_train.export import training_grade, validate
from broker.router_train.label import LABEL_STATUS_OK

UNLABELED = "unlabeled"
BUGGY = "buggy"
PROVENANCE_FIELDS = ("model_id", "router_version", "schema_version")
MIN_CLASS_EXAMPLES = 5
COVERAGE_THRESHOLD = 0.0

REPO_ROOT = Path(__file__).resolve().parents[4]
HOOK_DIRS = (
    REPO_ROOT / ".claude" / "hooks",
    REPO_ROOT / "nexus-package" / ".claude" / "hooks",
)

# Module/script paths referenced inside hook docstrings, e.g.
#   python -m broker.router_train.harvest
#   scripts/harvest_router_training_data.py
# A leading `$`/`{` excludes shell-variable paths (`$SCRIPT_DIR/x.sh`), which
# resolve at runtime relative to the hook dir, not the repo root — flagging them
# would be a false positive. The `path/to/...` doc placeholder is excluded too.
_MODULE_REF = re.compile(r"python\s+-m\s+([A-Za-z_][\w.]+)")
_PATH_REF = re.compile(r"(?<![\w$./{-])((?:[\w-]+/)+[\w-]+\.(?:py|sh|json))")
_PLACEHOLDER_PREFIXES = ("path/to/",)


@dataclass
class Report:
    total: int = 0
    distinct: int = 0
    labeled: int = 0
    coverage: float = 0.0
    coverage_by_source: dict[str, int] = field(default_factory=dict)
    label_status_balance: dict[str, int] = field(default_factory=dict)
    training_grade_count: int = 0
    schema_violations_by_field: dict[str, int] = field(default_factory=dict)
    schema_violation_count: int = 0
    dupe_count: int = 0
    dupe_samples: list[str] = field(default_factory=list)
    class_balance: dict[str, int] = field(default_factory=dict)
    underpopulated_classes: list[str] = field(default_factory=list)
    provenance_missing: dict[str, int] = field(default_factory=dict)
    quarantine_count: int = 0
    dangling_artifacts: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def _row_hash(record: dict[str, Any]) -> str:
    ph = record.get("prompt_hash")
    if ph:
        return str(ph)
    prompt = record.get("prompt")
    return prompt_hash(prompt) if isinstance(prompt, str) and prompt else ""


def _label_source(record: dict[str, Any]) -> str:
    source = record.get("label_source")
    return str(source) if source else UNLABELED


def _label_status(record: dict[str, Any]) -> str:
    """The row's label_status, defaulting to ``ok`` when absent (back-compat with
    rows built before classification — only an explicit non-ok status pollutes)."""
    status = record.get("label_status")
    return str(status) if status else LABEL_STATUS_OK


def _is_labeled(record: dict[str, Any]) -> bool:
    return (
        _label_source(record) != UNLABELED
        and bool(str(record.get("label_persona") or "").strip())
    )


def referenced_artifacts(hook_dirs: tuple[Path, ...] = HOOK_DIRS) -> dict[str, bool]:
    """Map every script/module path named in a shipped hook docstring → exists.

    Scans module docstrings (the leading triple-quoted block) of every hook, plus
    inline `python -m <module>` references, and resolves each to a real file. A
    dangling reference is exactly the phantom-harvester failure mode: a docstring
    that names a script which was never written.
    """
    resolved: dict[str, bool] = {}
    for hook_dir in hook_dirs:
        if not hook_dir.exists():
            continue
        for hook in sorted(hook_dir.glob("*.py")) + sorted(hook_dir.glob("*.sh")):
            try:
                text = hook.read_text()
            except OSError:
                continue
            for module in _MODULE_REF.findall(text):
                resolved.setdefault(module, _module_resolves(module))
            for rel in _PATH_REF.findall(text):
                if rel.startswith(_PLACEHOLDER_PREFIXES):
                    continue
                resolved.setdefault(rel, _path_resolves(rel, hook_dir))
    return resolved


def _module_resolves(dotted: str) -> bool:
    rel = Path(*dotted.split("."))
    for base in (REPO_ROOT / "nexus-broker" / "src", REPO_ROOT):
        if (base / rel).with_suffix(".py").exists():
            return True
        if (base / rel / "__init__.py").exists():
            return True
    return False


def _path_resolves(rel: str, hook_dir: Path) -> bool:
    bases = (
        hook_dir,
        REPO_ROOT,
        REPO_ROOT / "nexus-broker",
        REPO_ROOT / "nexus-package",
    )
    return any((base / rel).exists() for base in bases)


def check(
    records: list[dict[str, Any]],
    *,
    hook_dirs: tuple[Path, ...] = HOOK_DIRS,
    coverage_threshold: float = COVERAGE_THRESHOLD,
    min_class_examples: int = MIN_CLASS_EXAMPLES,
) -> Report:
    """Compute the integrity report. Pure — no I/O, no exit. ok == PASS."""
    report = Report()
    report.total = len(records)

    hashes = [_row_hash(r) for r in records]
    hash_counts = Counter(h for h in hashes if h)
    report.distinct = len(hash_counts)
    dupes = {h: c for h, c in hash_counts.items() if c > 1}
    report.dupe_count = sum(c - 1 for c in dupes.values())
    report.dupe_samples = sorted(dupes)[:5]

    source_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    labeled = 0
    class_counts: Counter[str] = Counter()
    for rec in records:
        if _is_labeled(rec):
            labeled += 1
            source_counts[_label_source(rec)] += 1
            status = _label_status(rec)
            status_counts[status] += 1
            # Class balance is the balance the TRAINING set actually sees, so it
            # counts label_persona over label_status=="ok" rows ONLY — pollution
            # (generic / retired / buggy) is reported separately, not mixed in.
            if status == LABEL_STATUS_OK:
                class_counts[str(rec.get("label_persona"))] += 1
        if str(rec.get("router_version") or "") == BUGGY:
            report.quarantine_count += 1
        for fld in PROVENANCE_FIELDS:
            value = rec.get(fld)
            if value is None or (isinstance(value, str) and not value.strip()):
                report.provenance_missing[fld] = report.provenance_missing.get(fld, 0) + 1

    report.labeled = labeled
    report.coverage = (labeled / report.total) if report.total else 0.0
    report.coverage_by_source = dict(source_counts)
    report.label_status_balance = dict(status_counts)
    report.training_grade_count = status_counts.get(LABEL_STATUS_OK, 0)
    report.class_balance = dict(class_counts)
    report.underpopulated_classes = sorted(
        persona for persona, n in class_counts.items() if n < min_class_examples
    )

    # Validate only TRAINING-GRADE rows — non-ok rows (generic / retired) legitimately
    # violate the schema's persona constraints and are excluded from export anyway, so
    # they must not fail the integrity gate.
    labeled_rows = training_grade([r for r in records if _is_labeled(r)])
    violations = validate(labeled_rows)
    report.schema_violation_count = len(violations)
    field_counts: Counter[str] = Counter()
    for viol in violations:
        path = viol.get("path") or []
        field_counts[str(path[0]) if path else "<root>"] += 1
    report.schema_violations_by_field = dict(field_counts)

    report.dangling_artifacts = sorted(
        ref for ref, exists in referenced_artifacts(hook_dirs).items() if not exists
    )

    if report.total == 0:
        report.failures.append("OPT-053: empty collection (total == 0)")
    if labeled == 0:
        report.failures.append("OPT-053: zero labeled rows (labeled == 0)")
    if report.coverage < coverage_threshold:
        report.failures.append(
            f"OPT-053: coverage {report.coverage:.1%} < threshold {coverage_threshold:.1%}"
        )
    if report.schema_violation_count:
        report.failures.append(
            f"{report.schema_violation_count} schema violation(s)"
        )
    if report.dangling_artifacts:
        report.failures.append(
            "referenced-artifact gate: "
            + ", ".join(report.dangling_artifacts)
            + " (named in a hook docstring but not on disk)"
        )
    return report


def render(report: Report) -> str:
    lines = [
        "router-data check",
        "=" * 60,
        f"rows                 : {report.total} total, {report.distinct} distinct (by prompt_hash)",
        f"label coverage       : {report.labeled}/{report.total} ({report.coverage:.1%})",
    ]
    if report.coverage_by_source:
        for source, count in sorted(report.coverage_by_source.items()):
            lines.append(f"  by source          : {source}={count}")
    if report.label_status_balance:
        status_line = ", ".join(
            f"{s}={n}" for s, n in sorted(report.label_status_balance.items())
        )
        lines.append(f"label status         : {status_line}")
        lines.append(f"  training-grade (ok): {report.training_grade_count}")
    lines.append(
        f"schema violations    : {report.schema_violation_count}"
        + (
            " — " + ", ".join(f"{f}={n}" for f, n in sorted(report.schema_violations_by_field.items()))
            if report.schema_violations_by_field
            else ""
        )
    )
    lines.append(
        f"dupes                : {report.dupe_count}"
        + (f" (sample: {', '.join(h[:12] for h in report.dupe_samples)})" if report.dupe_samples else "")
    )
    if report.class_balance:
        balance = ", ".join(f"{p}={n}" for p, n in sorted(report.class_balance.items()))
        lines.append(f"class balance (ok)   : {balance}")
    if report.underpopulated_classes:
        lines.append(
            f"  under {MIN_CLASS_EXAMPLES}            : {', '.join(report.underpopulated_classes)}"
        )
    lines.append(
        "provenance missing   : "
        + (
            ", ".join(f"{f}={n}" for f, n in sorted(report.provenance_missing.items()))
            if report.provenance_missing
            else "none"
        )
    )
    lines.append(f"quarantine (buggy)   : {report.quarantine_count}")
    lines.append(
        "referenced artifacts : "
        + (
            "DANGLING — " + ", ".join(report.dangling_artifacts)
            if report.dangling_artifacts
            else "all resolve"
        )
    )
    lines.append("-" * 60)
    if report.ok:
        lines.append("RESULT: PASS")
    else:
        lines.append("RESULT: FAIL")
        for reason in report.failures:
            lines.append(f"  - {reason}")
    return "\n".join(lines)


def _load_records() -> list[dict[str, Any]]:
    return aggregate()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    records: list[dict[str, Any]]
    if argv:
        records = []
        for arg in argv:
            path = Path(arg)
            if not path.exists():
                print(f"router-data check: input not found: {arg}", file=sys.stderr)
                return 2
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    else:
        records = _load_records()
    report = check(records, hook_dirs=HOOK_DIRS)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

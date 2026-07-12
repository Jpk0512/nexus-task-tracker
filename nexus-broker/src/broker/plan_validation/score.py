"""Plan-validation gate — deterministic core (R3-T04 / node N08).

WRAPS `broker.node_contract` (N03) — does not reimplement its per-field type
checks, acyclicity walk, or CONTRACT-shape validation. Adds the plan-level
checks node_contract does not make on its own:

  * skills_derived  — every node's skills_required is a superset of the
                       SKILL_MAP.md minimum for its (persona, work_type)
  * write_disjoint   — no two nodes that could run in the same wave (i.e.
                       are NOT ordered by a depends_on chain either way)
                       declare overlapping write_scope globs
  * invocability     — every node's dispatch_primitive (if declared) is
                       orchestrator-invocable (Workflow/Agent/Monitor/Cron/...),
                       never a user-only slash command (/goal, /loop, /effort —
                       DEC-020/DEC-024-PENDING, R3-T05/N11); see
                       `broker.plan_validation.checks.invocability`

and re-exposes node_contract's own findings under the plan-gate's verdict
vocabulary:

  * acyclic
  * verification_concrete
  * mece

DETERMINISTIC-ONLY: no model calls, no network I/O anywhere in this module
or its imports — this includes the N09 probes wired in below. `score_plan`
calls `probes.gate.run_probes` unconditionally, but `run_probes` is
self-gating (`probes.gate.gate_requires_probes`): on a T0/T1, non-irreversible
plan it imports zero probe submodules and returns None, preserving N09's own
opt-in contract exactly (see `probes/gate.py` and its
`test_default_invocation_runs_zero_probes`). When probes DO fire (any node
risk_tier=='T2' or irreversible==true), they are themselves static/offline
checks (citation-file existence, stub-command pattern matching) — no live
model or network call, same as everything else in this module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from broker.node_contract import ValidationError, validate_dag
from broker.plan_validation.checks.invocability import check_invocability
from broker.plan_validation.plan_doc import load_plan_as_dag_doc
from broker.plan_validation.probes.gate import run_probes
from broker.plan_validation.skill_map import load_skill_map, required_skills_for
from broker.plan_validation.verdict import Verdict

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SKILL_MAP_PATH = DEFAULT_REPO_ROOT / "docs" / "agents" / "SKILL_MAP.md"

_ACYCLIC_CODES = {"cycle", "dangling-edge"}
_VERIFICATION_CODES = {"bad-verification-method", "prose-verification"}
_MECE_CODES = {"orphan-leaf", "mece-write-collision", "edge-asymmetry"}


def _verdict_from_errors(errors: list[ValidationError], codes: set[str]) -> Verdict:
    matching = [e for e in errors if e.code in codes]
    offending = sorted({e.node_id for e in matching if e.node_id})
    details = [repr(e) for e in matching]
    return Verdict(passed=not matching, offending_node_ids=offending, details=details)


def _nodes_by_id(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in doc.get("nodes") or []:
        if isinstance(raw, dict):
            nid = raw.get("node_id")
            if isinstance(nid, str) and nid:
                result[nid] = raw
    return result


def _reaches(nodes: dict[str, dict[str, Any]], start: str, target: str) -> bool:
    """True if `target` is reachable from `start` via depends_on (start runs after target)."""
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        node = nodes.get(current)
        if not node:
            continue
        stack.extend(node.get("depends_on") or [])
    return False


def _ordered(nodes: dict[str, dict[str, Any]], a: str, b: str) -> bool:
    return _reaches(nodes, a, b) or _reaches(nodes, b, a)


def check_write_disjoint(doc: dict[str, Any]) -> Verdict:
    """No two unordered nodes (could land in the same dispatch wave) declare
    overlapping write_scope globs.

    A node with no write_scope declared makes no claim and is excluded from
    the comparison (node_contract.py itself treats an absent write_scope as
    an empty list, not a wildcard — see its `or []` read). Overlap is
    exact-string match on globs; this is intentionally conservative (it does
    not expand globs against a filesystem — deterministic/offline only) and
    catches the common case this plan actually uses: identical literal paths
    declared by two nodes with no ordering edge between them.
    """
    nodes = _nodes_by_id(doc)
    scope_owners: dict[str, list[str]] = {}
    for nid, node in nodes.items():
        for glob in node.get("write_scope") or []:
            scope_owners.setdefault(glob, []).append(nid)

    offending: set[str] = set()
    details: list[str] = []
    for glob, owners in scope_owners.items():
        if len(owners) < 2:
            continue
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                a, b = owners[i], owners[j]
                if not _ordered(nodes, a, b):
                    offending.update((a, b))
                    details.append(f"'{a}' and '{b}' both declare write_scope '{glob}' with no ordering edge")

    return Verdict(passed=not details, offending_node_ids=sorted(offending), details=details)


def check_skills_derived(doc: dict[str, Any], skill_map_path: str | Path = DEFAULT_SKILL_MAP_PATH) -> Verdict:
    """Every node's skills_required must be a superset of the SKILL_MAP.md
    minimum for its (persona, work_type) — read-only personas (empty
    minimum) always pass trivially."""
    skill_map = load_skill_map(skill_map_path)
    offending: list[str] = []
    details: list[str] = []
    for raw in doc.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        nid = raw.get("node_id")
        persona = raw.get("agent_persona")
        work_type = raw.get("work_type")
        declared = raw.get("skills_required") or []
        if not isinstance(persona, str) or not isinstance(work_type, str):
            continue  # node_contract's own field-type check already flags this
        minimum = required_skills_for(skill_map, persona, work_type)
        missing = [s for s in minimum if s not in declared]
        if missing:
            offending.append(nid)
            details.append(f"'{nid}' ({persona}, {work_type}) missing required skills: {missing}")

    return Verdict(passed=not details, offending_node_ids=sorted(o for o in offending if o), details=details)


def score_plan(
    doc: dict[str, Any],
    skill_map_path: str | Path = DEFAULT_SKILL_MAP_PATH,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Score a parsed plan DAG document. Pure function: no I/O, no model calls
    on the deterministic core. Also runs N09's opt-in probes via
    `probes.gate.run_probes` — self-gated, a no-op (returns None, adds no
    'probes' key) on a T0/T1, non-irreversible plan; see the module docstring."""
    errors = validate_dag(doc)

    verdicts = {
        "acyclic": _verdict_from_errors(errors, _ACYCLIC_CODES),
        "verification_concrete": _verdict_from_errors(errors, _VERIFICATION_CODES),
        "mece": _verdict_from_errors(errors, _MECE_CODES),
        "skills_derived": check_skills_derived(doc, skill_map_path),
        "write_disjoint": check_write_disjoint(doc),
        "invocability": check_invocability(doc),
    }

    other_errors = [e for e in errors if e.code not in (_ACYCLIC_CODES | _VERIFICATION_CODES | _MECE_CODES)]

    result: dict[str, Any] = {k: v.to_dict() for k, v in verdicts.items()}

    probes_result = run_probes(doc, repo_root=DEFAULT_REPO_ROOT if repo_root is None else repo_root)
    if probes_result is not None:
        result["probes"] = probes_result

    result["overall_pass"] = (
        all(v.passed for v in verdicts.values())
        and not other_errors
        and (probes_result is None or probes_result.get("overall_pass") is True)
    )
    if other_errors:
        result["other_errors"] = [e.to_dict() for e in other_errors]
    return result


def score_file(
    path: str | Path,
    skill_map_path: str | Path = DEFAULT_SKILL_MAP_PATH,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load + score a plan markdown file. I/O boundary; score_plan stays pure
    apart from the N09 probe self-gating documented on score_plan."""
    doc = load_plan_as_dag_doc(path)
    return score_plan(doc, skill_map_path, repo_root=repo_root)


def _cli_score(path: str, as_json: bool, skill_map_path: str | Path = DEFAULT_SKILL_MAP_PATH) -> int:
    result = score_file(path, skill_map_path=skill_map_path)
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for key in (
            "acyclic",
            "verification_concrete",
            "mece",
            "skills_derived",
            "write_disjoint",
            "invocability",
        ):
            v = result[key]
            status = "PASS" if v["pass"] else "FAIL"
            print(f"{key}: {status}" + (f" — offending: {v['offending_node_ids']}" if not v["pass"] else ""))
        if "probes" in result:
            probes = result["probes"]
            probes_pass = probes.get("overall_pass") is True
            status = "PASS" if probes_pass else "FAIL"
            failing_probes = sorted(k for k, v in probes.items() if isinstance(v, dict) and v.get("pass") is False)
            print(f"probes: {status}" + (f" — failing: {failing_probes}" if not probes_pass else ""))
        print(f"overall: {'PASS' if result['overall_pass'] else 'FAIL'}")
    return 0 if result["overall_pass"] else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] != "score":
        print(
            "usage: python -m broker.plan_validation score <plan-file> [--json] [--skill-map PATH]",
            file=sys.stderr,
        )
        return 1
    path = argv[1]
    rest = argv[2:]
    as_json = "--json" in rest
    skill_map_path: str | Path = DEFAULT_SKILL_MAP_PATH
    if "--skill-map" in rest:
        skill_map_path = Path(rest[rest.index("--skill-map") + 1])
    return _cli_score(path, as_json, skill_map_path)


if __name__ == "__main__":
    raise SystemExit(main())

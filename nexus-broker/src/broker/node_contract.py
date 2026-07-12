"""Node-contract DAG validator — schema_version 2 (R3-T01, docs/agents/CONTRACT.md).

Pure, deterministic, offline: no model calls, no network I/O. Parses a YAML/JSON
node-contract DAG and checks per-field types, DAG acyclicity, MECE coverage
(every non-terminal leaf's output is consumed downstream; no two leaves write
the same path without an ordering edge), and verification_method concreteness
(type=="command" with a non-empty command string — prose is rejected).

Transport-agnostic by design (C1.a): this module has no CLI/MCP framework
dependency of its own beyond stdlib + pyyaml, so the R4-T06 daemon can import
and call `validate_dag` unchanged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_SCHEMA_VERSION = 2
RISK_TIERS = ("T0", "T1", "T2")

# R4-T05 (plans/11-codex-lane-design.md SS9.1/SS9.3) — cross-vendor executor fields,
# additive to schema_version 2. Absence of 'executor' on a node defaults to 'claude'
# and none of these checks fire (byte-identical to pre-R4-T05 behavior).
EXECUTOR_VALUES = ("claude", "codex")
EXECUTOR_MODEL_SLUGS = ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini")
CODEX_LANE_FLAG_RELATIVE_PATH = ".claude/codex-lane.enabled"
_UNBOUNDED_WRITE_GLOBS = {"**/*", "**", "*", "/**", "/**/*"}

REQUIRED_NODE_FIELDS = (
    "node_id",
    "depends_on",
    "downstream_consumers",
    "agent_persona",
    "goal",
    "context_files",
    "acceptance_criteria",
    "verification_method",
    "risk_tier",
    "skills_required",
    "do_not_touch",
)


class ValidationError:
    """One structured validation failure."""

    __slots__ = ("code", "message", "node_id")

    def __init__(self, code: str, message: str, node_id: str | None = None) -> None:
        self.code = code
        self.message = message
        self.node_id = node_id

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        prefix = f"[{self.node_id}] " if self.node_id else ""
        return f"{prefix}{self.code}: {self.message}"

    def to_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "node_id": self.node_id}


def _err(errors: list[ValidationError], code: str, message: str, node_id: str | None = None) -> None:
    errors.append(ValidationError(code, message, node_id))


def _is_list_of_str(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _default_codex_lane_flag_path() -> Path:
    """Resolve the real repo's codex-lane flag file (plans/11 SS9.1/SS11): walk up
    from this file to find the repo root (.memory/ dir is the marker — same
    convention as broker.state._find_repo_root). NEXUS_CODEX_LANE_FLAG_PATH
    env-overrides the resolved path so tests/CLI never need to touch the real
    .claude/ tree to exercise this check."""
    override = os.environ.get("NEXUS_CODEX_LANE_FLAG_PATH")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate / CODEX_LANE_FLAG_RELATIVE_PATH
    return Path.cwd() / CODEX_LANE_FLAG_RELATIVE_PATH


def _resolve_codex_lane_flag_path(override: str | Path | None) -> Path:
    if override is not None:
        return Path(override)
    return _default_codex_lane_flag_path()


def _codex_sandbox_mode_for_write_scope(write_scope: list[str]) -> str | None:
    """Map a write_scope glob list to a codex --sandbox mode (plans/11 SS9.1):
    empty -> 'read-only' (no writes needed); a bounded glob list -> 'workspace-write'
    (expressible as -s workspace-write -C <scope> [--add-dir ...]); an unbounded glob
    that could match anywhere in the tree is not expressible as a single codex
    sandbox -> None (invalid for executor: codex)."""
    if not write_scope:
        return "read-only"
    for entry in write_scope:
        normalized = entry.strip()
        if normalized in _UNBOUNDED_WRITE_GLOBS or normalized.startswith("/"):
            return None
    return "workspace-write"


def load_dag(path: str | Path) -> dict[str, Any]:
    """Load a node-contract DAG document from a YAML or JSON file. Raises on I/O/parse error."""
    text = Path(path).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise ValueError(f"top-level document must be a mapping, got {type(doc).__name__}")
    return doc


def validate_schema_version(doc: dict[str, Any], errors: list[ValidationError]) -> bool:
    """Check schema_version is present, an int, and == SUPPORTED_SCHEMA_VERSION.

    Returns False (validator should abort) on an unrecognized/missing/wrong-type version —
    an explicit failure, never a best-effort parse (CONTRACT.md field 10).
    """
    if "schema_version" not in doc:
        _err(errors, "missing-field", "top-level document missing required field 'schema_version'")
        return False
    version = doc["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        _err(
            errors,
            "bad-type",
            f"schema_version must be an int, got {type(version).__name__}: {version!r}",
        )
        return False
    if version != SUPPORTED_SCHEMA_VERSION:
        _err(
            errors,
            "unsupported-schema-version",
            f"unsupported schema_version {version!r}; validator supports {SUPPORTED_SCHEMA_VERSION}",
        )
        return False
    return True


def _validate_node_fields(
    node: Any, errors: list[ValidationError], codex_lane_flag_path: str | Path | None
) -> str | None:
    """Per-field type checks for one node. Returns the node_id if resolvable, else None."""
    if not isinstance(node, dict):
        _err(errors, "bad-type", f"node entry must be a mapping, got {type(node).__name__}")
        return None

    node_id = node.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        _err(errors, "missing-field", "node missing non-empty string 'node_id'")
        node_id = None

    for field in REQUIRED_NODE_FIELDS:
        if field not in node:
            _err(errors, "missing-field", f"node missing required field '{field}'", node_id)

    if "depends_on" in node and not _is_list_of_str(node["depends_on"]):
        _err(errors, "bad-type", "'depends_on' must be a list of strings", node_id)
    if "downstream_consumers" in node and not _is_list_of_str(node["downstream_consumers"]):
        _err(errors, "bad-type", "'downstream_consumers' must be a list of strings", node_id)
    if "agent_persona" in node and not isinstance(node["agent_persona"], str):
        _err(errors, "bad-type", "'agent_persona' must be a string", node_id)
    if "goal" in node and (not isinstance(node["goal"], str) or not node["goal"]):
        _err(errors, "bad-type", "'goal' must be a non-empty string", node_id)
    if "context_files" in node and not _is_list_of_str(node["context_files"]):
        _err(errors, "bad-type", "'context_files' must be a list of strings", node_id)
    if "acceptance_criteria" in node and not _is_list_of_str(node["acceptance_criteria"]):
        _err(errors, "bad-type", "'acceptance_criteria' must be a list of strings", node_id)
    if "skills_required" in node and not _is_list_of_str(node["skills_required"]):
        _err(errors, "bad-type", "'skills_required' must be a list of strings", node_id)
    if "do_not_touch" in node and not _is_list_of_str(node["do_not_touch"]):
        _err(errors, "bad-type", "'do_not_touch' must be a list of strings", node_id)

    if "risk_tier" in node and node["risk_tier"] not in RISK_TIERS:
        _err(
            errors,
            "bad-enum",
            f"'risk_tier' must be one of {RISK_TIERS}, got {node['risk_tier']!r}",
            node_id,
        )
    if "lens_type" in node and node["lens_type"] not in RISK_TIERS:
        _err(
            errors,
            "bad-enum",
            f"'lens_type' must be one of {RISK_TIERS}, got {node['lens_type']!r}",
            node_id,
        )
    if "required_lens_types" in node:
        rlt = node["required_lens_types"]
        if not (isinstance(rlt, list) and all(v in RISK_TIERS for v in rlt)):
            _err(
                errors,
                "bad-enum",
                f"'required_lens_types' must be a list drawn from {RISK_TIERS}, got {rlt!r}",
                node_id,
            )
    if "irreversible" in node and not isinstance(node["irreversible"], bool):
        _err(errors, "bad-type", "'irreversible' must be a boolean", node_id)
    if "budget" in node and node["budget"] not in ("S", "M", "L", "XL"):
        _err(
            errors,
            "bad-enum",
            f"'budget' must be one of ('S','M','L','XL'), got {node['budget']!r}",
            node_id,
        )

    _validate_verification_method(node, node_id, errors)
    _validate_executor_fields(node, node_id, errors, codex_lane_flag_path)

    return node_id


def _validate_verification_method(node: dict[str, Any], node_id: str | None, errors: list[ValidationError]) -> None:
    """Concreteness check: type=='command' with a non-empty command string. Prose is rejected."""
    vm = node.get("verification_method")
    if vm is None:
        return  # missing-field already recorded by the required-fields loop
    if not isinstance(vm, dict):
        _err(errors, "bad-verification-method", "'verification_method' must be a mapping", node_id)
        return
    vm_type = vm.get("type")
    if vm_type != "command":
        _err(
            errors,
            "prose-verification",
            f"'verification_method.type' must be 'command', got {vm_type!r} (prose verification is rejected)",
            node_id,
        )
        return
    command = vm.get("command")
    if not isinstance(command, str) or not command.strip():
        _err(
            errors,
            "prose-verification",
            "'verification_method.command' must be a non-empty, concrete command string",
            node_id,
        )


def _validate_executor_fields(
    node: dict[str, Any],
    node_id: str | None,
    errors: list[ValidationError],
    codex_lane_flag_path: str | Path | None,
) -> None:
    """R4-T05 (plans/11-codex-lane-design.md SS9.1/SS9.3): cross-vendor executor
    fields, additive to schema_version 2 (CONTRACT.md 'Cross-vendor executor
    fields'). Absence of 'executor' defaults to claude — none of these checks
    fire, so every pre-R4-T05 node-contract row validates unchanged. The
    lane-flag filesystem check (the only I/O this validator ever performs) is
    resolved lazily here, only for an executor: codex node."""
    if "executor" not in node:
        return
    executor = node["executor"]
    if executor not in EXECUTOR_VALUES:
        _err(errors, "bad-enum", f"'executor' must be one of {EXECUTOR_VALUES}, got {executor!r}", node_id)
        return  # the codex-specific checks below are meaningless off-enum

    if executor != "codex":
        return

    flag_path = _resolve_codex_lane_flag_path(codex_lane_flag_path)
    if not flag_path.exists():
        _err(
            errors,
            "codex-lane-disabled",
            f"'executor: codex' requires the lane-enabled flag file to exist ({flag_path}); "
            "the codex lane is off",
            node_id,
        )

    executor_model = node.get("executor_model")
    if executor_model is not None and executor_model not in EXECUTOR_MODEL_SLUGS:
        _err(
            errors,
            "bad-enum",
            f"'executor_model' must be one of {EXECUTOR_MODEL_SLUGS} for executor: codex, got {executor_model!r}",
            node_id,
        )

    if node.get("irreversible") is True:
        _err(
            errors,
            "codex-irreversible-invalid",
            "'executor: codex' is invalid when 'irreversible' is true (v1 codex lane doctrine restricts "
            "codex legs to read-only/workspace-scoped work)",
            node_id,
        )

    write_scope = node.get("write_scope")
    if write_scope is None:
        write_scope = []  # node_contract treats an absent write_scope as [], not a wildcard
    if _is_list_of_str(write_scope) and _codex_sandbox_mode_for_write_scope(write_scope) is None:
        _err(
            errors,
            "codex-write-scope-unmappable",
            f"'write_scope' {write_scope!r} does not map to an expressible codex sandbox mode "
            "(read-only or a bounded workspace-write scope); unbounded globs are invalid for executor: codex",
            node_id,
        )


def _check_cycles(nodes: dict[str, dict[str, Any]], errors: list[ValidationError]) -> None:
    """Kahn's-algorithm topological sort over depends_on edges; any residual node is in a cycle."""
    in_degree = {nid: 0 for nid in nodes}
    adjacency: dict[str, list[str]] = {nid: [] for nid in nodes}

    for nid, node in nodes.items():
        for dep in node.get("depends_on") or []:
            if dep not in nodes:
                _err(errors, "dangling-edge", f"depends_on references unknown node id '{dep}'", nid)
                continue
            adjacency[dep].append(nid)
            in_degree[nid] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for nxt in adjacency[current]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if visited != len(nodes):
        cyclic = sorted(nid for nid, deg in in_degree.items() if deg > 0)
        _err(errors, "cycle", f"DAG contains a cycle involving node(s): {cyclic}")


def _check_edge_symmetry(nodes: dict[str, dict[str, Any]], errors: list[ValidationError]) -> None:
    """Every A -> B depends_on edge must be mirrored by B in A's downstream_consumers."""
    for nid, node in nodes.items():
        for dep in node.get("depends_on") or []:
            upstream = nodes.get(dep)
            if upstream is None:
                continue  # already reported as dangling-edge
            if nid not in (upstream.get("downstream_consumers") or []):
                _err(
                    errors,
                    "edge-asymmetry",
                    f"'{dep}' depends_on->'{nid}' has no matching downstream_consumers entry on '{dep}'",
                    nid,
                )


def _check_mece(nodes: dict[str, dict[str, Any]], errors: list[ValidationError]) -> None:
    """Every non-terminal leaf's output must be consumed; no two leaves write the same
    path without an ordering edge between them.

    A DAG has exactly one legitimate sink (a node with empty downstream_consumers — the
    terminal node whose output nothing further reads). Any additional sink is an
    orphan-leaf: a node whose output is produced but never consumed and never surfaced
    as the DAG's own terminal.
    """
    if len(nodes) > 1:
        sink_ids = [nid for nid, node in nodes.items() if not (node.get("downstream_consumers") or [])]
        if len(sink_ids) > 1:
            canonical_terminal = sink_ids[0]
            for nid in sink_ids[1:]:
                _err(
                    errors,
                    "orphan-leaf",
                    f"node '{nid}' has no downstream_consumers and is not the DAG's terminal node "
                    f"(terminal is '{canonical_terminal}')",
                    nid,
                )

    write_owners: dict[str, list[str]] = {}
    for nid, node in nodes.items():
        for path in node.get("write_scope") or []:
            write_owners.setdefault(path, []).append(nid)

    def _ordered(a: str, b: str) -> bool:
        return _reaches(nodes, a, b) or _reaches(nodes, b, a)

    for path, owners in write_owners.items():
        if len(owners) < 2:
            continue
        for i in range(len(owners)):
            for j in range(i + 1, len(owners)):
                if not _ordered(owners[i], owners[j]):
                    _err(
                        errors,
                        "mece-write-collision",
                        f"nodes '{owners[i]}' and '{owners[j]}' both write '{path}' with no ordering edge between them",
                        owners[i],
                    )


def _reaches(nodes: dict[str, dict[str, Any]], start: str, target: str) -> bool:
    """True if `target` is reachable from `start` following depends_on edges (start depends
    transitively on target, i.e. target must run before start)."""
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


def validate_dag(
    doc: dict[str, Any], codex_lane_flag_path: str | Path | None = None
) -> list[ValidationError]:
    """Validate a parsed node-contract DAG document. Returns a list of ValidationError
    (empty == valid). Pure/offline function: no model calls, no network I/O — the one
    exception is a lane-flag file-existence check (see `_validate_executor_fields`),
    which only runs at all when a node declares `executor: codex`.

    `codex_lane_flag_path` overrides the resolved `.claude/codex-lane.enabled` path
    (R4-T05 / plans/11 SS9.1/SS9.3) — used by tests/CLI to exercise the codex-lane
    check without touching the real repo's `.claude/` tree; production callers omit
    it and get the real repo-relative flag file."""
    errors: list[ValidationError] = []

    if not validate_schema_version(doc, errors):
        return errors

    raw_nodes = doc.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        _err(errors, "missing-field", "top-level document missing non-empty 'nodes' list")
        return errors

    nodes: dict[str, dict[str, Any]] = {}
    for raw in raw_nodes:
        node_id = _validate_node_fields(raw, errors, codex_lane_flag_path)
        if node_id is not None and isinstance(raw, dict):
            if node_id in nodes:
                _err(errors, "duplicate-node-id", f"node id '{node_id}' appears more than once")
                continue
            nodes[node_id] = raw

    if not nodes:
        return errors

    _check_cycles(nodes, errors)
    _check_edge_symmetry(nodes, errors)
    _check_mece(nodes, errors)

    return errors


def validate_file(
    path: str | Path, codex_lane_flag_path: str | Path | None = None
) -> list[ValidationError]:
    """Load + validate a node-contract DAG file. I/O boundary; validate_dag stays pure/offline."""
    try:
        doc = load_dag(path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        return [ValidationError("parse-error", str(exc))]
    return validate_dag(doc, codex_lane_flag_path=codex_lane_flag_path)


def _cli_validate(path: str) -> int:
    errors = validate_file(path)
    if not errors:
        print(f"OK: {path} is a valid node-contract DAG (schema_version {SUPPORTED_SCHEMA_VERSION})")
        return 0
    print(f"FAIL: {path} — {len(errors)} validation error(s):", file=sys.stderr)
    for e in errors:
        print(f"  {e!r}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2 or argv[0] != "validate":
        print("usage: python -m broker.node_contract validate <file>", file=sys.stderr)
        return 1
    return _cli_validate(argv[1])


if __name__ == "__main__":
    raise SystemExit(main())

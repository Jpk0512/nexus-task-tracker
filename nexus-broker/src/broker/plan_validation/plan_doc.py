"""Extract node-contract YAML blocks from a plan markdown file.

A plan file (nexus-redesign/plans/*.md) is prose + one fenced ```yaml block
per node, each block a self-contained node-contract-shaped mapping (node_id,
depends_on, downstream_consumers, ...). This module assembles those blocks
into the single `{"schema_version": 2, "nodes": [...]}` document shape that
`broker.node_contract.validate_dag` already validates — no duplication of
node_contract's own field/DAG checks.

Deterministic, offline: stdlib + pyyaml only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_YAML_FENCE_RE = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)


def extract_node_blocks(text: str) -> list[dict[str, Any]]:
    """Parse every fenced ```yaml block that looks like a node (has node_id).

    Non-node yaml fences (none expected in current plan files, but the plan
    format is prose-authored) are silently skipped rather than raising, since
    this is a documentation format, not a strict schema — node_contract.py's
    own validation is what enforces required fields on the blocks that DO
    claim to be nodes.
    """
    nodes: list[dict[str, Any]] = []
    for match in _YAML_FENCE_RE.finditer(text):
        block_text = match.group(1)
        try:
            parsed = yaml.safe_load(block_text)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and "node_id" in parsed:
            nodes.append(parsed)
    return nodes


def load_plan_as_dag_doc(path: str | Path) -> dict[str, Any]:
    """Load a plan markdown file and assemble it into a node-contract DAG doc.

    Returns the `{"schema_version": 2, "nodes": [...]}` shape that
    `broker.node_contract.validate_dag` consumes directly.
    """
    text = Path(path).read_text(encoding="utf-8")
    nodes = extract_node_blocks(text)
    return {"schema_version": 2, "nodes": nodes}

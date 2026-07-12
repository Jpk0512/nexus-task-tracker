"""Stub-mutation oracle (R3-T04, node N09) — deterministic falsifiability probe.

Detects leaves whose `verification_method` is a structural pass-through — a
"stub" that satisfies N08's concreteness check (a non-empty command string)
without doing any real verification work: `true`, `:`, `pass`, `exit 0`, a
bare `echo`. Also ships `mutate_to_stub`, a pure function that seeds a known
stub into an otherwise-good plan, used by this module's own test suite (and
reusable by any caller) to prove the detector actually fires — a check that
can never fail is not a gate.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from broker.plan_validation.score import Verdict

_STUB_COMMAND_RE = re.compile(
    r"^\s*(true|:|pass|exit\s+0|echo(\s+(ok|done|success|passed?))?)\s*$",
    re.IGNORECASE,
)
_STUB_TEXT_MARKERS = ("todo", "fixme", "not implemented", "placeholder")


def is_stub_command(command: str) -> bool:
    """True if `command` matches a known trivial/no-op verification pattern."""
    return bool(_STUB_COMMAND_RE.match(command or ""))


def mutate_to_stub(doc: dict[str, Any], node_id: str) -> dict[str, Any]:
    """Return a deep copy of `doc` with `node_id`'s verification_method
    replaced by a trivial stub command (`true`) — a leaf that still satisfies
    N08's structural concreteness check (non-empty command) but does no real
    verification. Pure function: never mutates the input doc."""
    mutated = copy.deepcopy(doc)
    for node in mutated.get("nodes") or []:
        if isinstance(node, dict) and node.get("node_id") == node_id:
            node["verification_method"] = {"type": "command", "command": "true"}
    return mutated


def check_stub_mutation(doc: dict[str, Any]) -> Verdict:
    """Scan every leaf for a stub-pattern verification_method command, or a
    stub marker in its goal/acceptance_criteria text. Fails (offending node
    ids listed) if any are found."""
    offending: list[str] = []
    details: list[str] = []
    for raw in doc.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        nid = raw.get("node_id")
        if not isinstance(nid, str) or not nid:
            continue

        vm = raw.get("verification_method")
        command = vm.get("command") if isinstance(vm, dict) else None
        if isinstance(command, str) and is_stub_command(command):
            offending.append(nid)
            details.append(f"'{nid}' verification_method.command {command!r} is a stub pattern")
            continue

        text_parts = [raw.get("goal")] + list(raw.get("acceptance_criteria") or [])
        text_blob = " ".join(str(x).lower() for x in text_parts if x)
        hit = next((m for m in _STUB_TEXT_MARKERS if m in text_blob), None)
        if hit:
            offending.append(nid)
            details.append(f"'{nid}' goal/acceptance_criteria contains stub marker {hit!r}")

    return Verdict(passed=not details, offending_node_ids=sorted(set(offending)), details=details)

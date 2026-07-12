"""Orchestrator-invocability check (R3-T05, node N11) — closes DEC-020/DEC-024-PENDING.

A plan leaf names a `dispatch_primitive`: the mechanism the orchestrator will use to
execute that node (Workflow, Agent, Monitor, Cron, RemoteTrigger, TeamCreate, inline, ...).
Per Constitution DEC-023/024 (`docs/CONSTITUTION.md`) and `.claude/skills/dispatch/SKILL.md`,
`/goal`, `/loop`, and `/effort` are **USER-ONLY** slash commands — the orchestrator can never
invoke them itself; it must EMULATE their effect with an orchestrator-invocable primitive
(e.g. a loop-until-done Workflow emulates `/loop`). A plan that names a leaf's dispatch
primitive as one of these user-only commands is not executable by the orchestrator and MUST
fail this gate.

This makes NATIVE-19 falsifiable: a plan CAN name a non-invocable primitive, and doing so
MUST fail with the offending node id(s), not silently pass.

DETERMINISTIC-ONLY: a plain string/enum membership check — no model calls, no network I/O.
A node that declares no `dispatch_primitive` makes no claim and is excluded from this check
(same convention `score.check_write_disjoint` uses for an absent `write_scope`) — this field
is optional accept-tier metadata, not yet a `node_contract.REQUIRED_NODE_FIELDS` member.
"""
from __future__ import annotations

from typing import Any

from broker.plan_validation.verdict import Verdict

# Keyword fragments of every orchestrator-invocable primitive named in
# `.claude/skills/dispatch/SKILL.md`'s cheat-sheet ("Workflow, Monitor,
# CronCreate/Delete/List, RemoteTrigger, Agent, Task*, TeamCreate ... AVAILABLE by
# default", plus the deliberately-uncontrolled "inline" no-dispatch case). Substring
# match on the lower-cased declared primitive so phrasings like "loop-until-done
# Workflow" or "Monitor (external oracle)" still resolve to their base primitive.
_INVOCABLE_KEYWORDS = (
    "workflow",
    "agent",
    "monitor",
    "cron",
    "remotetrigger",
    "teamcreate",
    "task",
    "inline",
)

# The user-only slash commands named verbatim in DEC-023/024 — the orchestrator never
# invokes these; it emulates them. Matched after stripping a leading '/' so both
# "/goal" and "goal" spellings are caught.
_USER_ONLY_SLASH_COMMANDS = ("goal", "loop", "effort")


def _classify(primitive: str) -> str | None:
    """Return a failure reason for `primitive`, or None if it is orchestrator-invocable."""
    normalized = primitive.strip().lower()
    if not normalized:
        return "'dispatch_primitive' is an empty string"

    # Any leading '/' is categorically a slash command — user-only by construction
    # (DEC-023/024), regardless of which one. Also catch the bare (unslashed) word
    # for the three named commands so "goal"/"loop"/"effort" don't slip through.
    first_word = normalized.lstrip("/").split()[0] if normalized.lstrip("/") else ""
    if normalized.startswith("/") or first_word in _USER_ONLY_SLASH_COMMANDS:
        return (
            f"{primitive!r} is a USER-ONLY slash command (DEC-023/024) — the orchestrator "
            "never invokes it directly; it must emulate the effect with an "
            "orchestrator-invocable primitive (e.g. a loop-until-done Workflow)"
        )

    if any(keyword in normalized for keyword in _INVOCABLE_KEYWORDS):
        return None

    return (
        f"{primitive!r} is not a recognized orchestrator-invocable primitive "
        f"(expected one of: Workflow / Agent / Monitor / Cron / RemoteTrigger / "
        f"TeamCreate / inline)"
    )


def check_invocability(doc: dict[str, Any]) -> Verdict:
    """Every node's `dispatch_primitive`, if declared, must be orchestrator-invocable.

    A node with no `dispatch_primitive` declared makes no claim and passes trivially.
    Pure function: no I/O, no model calls.
    """
    offending: list[str] = []
    details: list[str] = []

    for raw in doc.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        nid = raw.get("node_id")
        primitive = raw.get("dispatch_primitive")
        if primitive is None:
            continue  # no claim made — nothing to check
        if not isinstance(primitive, str):
            offending.append(nid)
            details.append(f"'{nid}': 'dispatch_primitive' must be a string, got {type(primitive).__name__}")
            continue

        reason = _classify(primitive)
        if reason is not None:
            offending.append(nid)
            details.append(f"'{nid}': {reason}")

    return Verdict(
        passed=not details,
        offending_node_ids=sorted(o for o in offending if o),
        details=details,
    )

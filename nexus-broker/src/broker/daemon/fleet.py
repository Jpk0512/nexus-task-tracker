"""plans/08 items 2.3 (cross-project queries) + 2.4 (gate-block rollup) in
ONE thin client-side aggregation surface (plans/13-r4-conductor-lane-plan.md
N16, Phase B, N13-PASS-gated).

Design constraint, verbatim from plans/08's own closing note and plan 13
§2.B: "whether the fleet ever needs ONE daemon-of-daemons... or whether
fleet-wide queries stay a thin client-side aggregation over N per-project
sockets... is itself a design decision for whichever phase first needs
genuine cross-project coordination — not decided here." This module takes
the thin-aggregation side of that fork: it is a CLIENT, never a server, and
introduces zero new long-lived processes. Every per-project answer is fetched
either over that project's own already-existing daemon socket
(`broker.daemon.client`, unchanged) or by reading that project's own files
directly — never a shared fleet-wide store, never a second daemon that
coordinates the first.

2.3 (`fleet_call` / `query_fleet_registry`) goes over each project's socket,
exactly as plans/08 2.3 frames it. 2.4 (`fleet_gate_block_rollup`) reads each
project's `.memory/files/gate_blocks.jsonl` — the real, already-instrumented
OPT-030/033 producer of gate-deny events (`.claude/hooks/_gate_deny.py`'s
`_record_block`; see also `tools/gate_blocks_report.py`, the single-project
precedent this module generalizes fleet-wide). That sink is NOT one of the
three tables `telemetry_store.TelemetryStore` batches (`dispatch_telemetry`/
`skill_load_events`/`agent_activity` — none carries a "which gate" column),
so 2.4 cannot be built as a daemon RPC without a schema or server.py change,
both out of N16's write_scope. Reading the sink directly is still a
"batched" telemetry read (`_record_block` appends one row per gate-deny
event, exactly the batching plans/08 2.4 describes) and is unambiguously an
in-memory Python aggregation, never a cross-project SQL join — no
`ATTACH DATABASE`, no cross-DB `UNION`, not even a `sqlite3.connect` call.
"""
from __future__ import annotations

import contextlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from broker.daemon import client as daemon_client
from broker.daemon.client import DaemonUnavailable

GATE_BLOCKS_RELATIVE_PATH = Path(".memory") / "files" / "gate_blocks.jsonl"


@dataclass(frozen=True)
class ProjectRef:
    """One fleet member. `label` namespaces every aggregated result — a
    caller must always be able to tell WHICH project an entry or count came
    from. Defaults to the project directory's own name, deliberately never
    the opaque `sha256(project_path)[:16]` digest `broker.daemon.paths`
    hashes into the socket filename — that identifier authenticates a
    socket, it does not name a project for a human reading a fleet report.
    """

    project_path: Path
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_path", Path(self.project_path))
        if not self.label:
            resolved_label = self.project_path.name or str(self.project_path)
            object.__setattr__(self, "label", resolved_label)


@dataclass(frozen=True)
class FleetQueryResult:
    """Per-project + fleet-wide envelope for a client-side aggregation over
    N per-project daemon sockets — namespacing preserved (2.3's acceptance
    criterion). `errors` captures per-project unreachability WITHOUT aborting
    the rest of the fleet call: a partial fleet answer beats none, and the
    caller can always see exactly which project's answer, if any, is
    missing and why.
    """

    by_project: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok_labels(self) -> list[str]:
        return list(self.by_project)


def fleet_call(
    projects: list[ProjectRef],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    spawn_if_missing: bool = True,
    spawn_wait_s: float | None = None,
    connect_timeout: float | None = None,
) -> FleetQueryResult:
    """2.3's general cross-project aggregation primitive: call `method`
    against EACH project's OWN daemon socket in turn (never a shared/central
    process — no daemon-of-daemons) and stitch the answers together keyed by
    project label. Query-agnostic by design: registry queries today, and,
    unchanged, any task/decision RPC a per-project daemon grows later — this
    is exactly plans/08 2.3's "thin aggregation layer... answering
    cross-project registry/task/decision queries" framing, generalized to
    whatever method the daemon on the other end of each socket answers.
    """
    by_project: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for ref in projects:
        try:
            by_project[ref.label] = daemon_client.call(
                ref.project_path,
                method,
                params,
                spawn_if_missing=spawn_if_missing,
                spawn_wait_s=spawn_wait_s,
                connect_timeout=connect_timeout,
            )
        except DaemonUnavailable as exc:
            errors[ref.label] = str(exc)
    return FleetQueryResult(by_project=by_project, errors=errors)


def query_fleet_registry(
    projects: list[ProjectRef],
    query_context: str | None = None,
    **kwargs: Any,
) -> FleetQueryResult:
    """2.3's concrete worked example: the skills/agents/persona registry
    query (`broker.daemon.registry_query` / N15), fanned out over every
    project's daemon and namespaced by project label. Each
    `by_project[label]` value is the unmodified `{"entries": [...]}` shape
    the `query_registry` RPC already returns per-project — this layer
    aggregates, it never reshapes a single project's answer.
    """
    return fleet_call(projects, "query_registry", {"query_context": query_context}, **kwargs)


def _read_gate_blocks(sink_path: Path) -> list[dict[str, Any]]:
    """Best-effort JSONL read, mirroring `tools/gate_blocks_report.py`'s
    single-project reader: a missing sink (a project that has not yet
    tripped any gate) is zero blocks, never an error; a malformed line is
    skipped rather than aborting the whole read.
    """
    rows: list[dict[str, Any]] = []
    if not sink_path.is_file():
        return rows
    with open(sink_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            with contextlib.suppress(json.JSONDecodeError):
                rows.append(json.loads(line))
    return rows


@dataclass(frozen=True)
class GateBlockRollup:
    """Fleet-wide answer to plans/08 2.4's own worked question: "which gate
    is blocking the most work, fleet-wide". `by_hook` and `total_blocks` are
    the FLEET-WIDE rollup; `by_project` retains the per-project breakdown so
    a caller can still see which project is driving a fleet-wide top hook.
    """

    by_hook: dict[str, int] = field(default_factory=dict)
    by_project: dict[str, dict[str, int]] = field(default_factory=dict)
    total_blocks: int = 0

    @property
    def top_hook(self) -> tuple[str, int] | None:
        """"Which gate blocked most" — None when the fleet has zero blocks
        recorded (never a fabricated answer)."""
        if not self.by_hook:
            return None
        return max(self.by_hook.items(), key=lambda kv: (kv[1], kv[0]))


def fleet_gate_block_rollup(projects: list[ProjectRef]) -> GateBlockRollup:
    """2.4 — the fleet-wide gate-block rollup, computed client-side over
    each project's own `.memory/files/gate_blocks.jsonl` sink (the real
    `_gate_deny.py: _record_block` producer — see module docstring for why
    this sink, not a daemon RPC, is this node's honest data source). A plain
    per-project file read plus an in-memory `Counter` merge — never a
    cross-project SQL join.
    """
    fleet_counter: Counter[str] = Counter()
    by_project: dict[str, dict[str, int]] = {}
    for ref in projects:
        sink_path = ref.project_path / GATE_BLOCKS_RELATIVE_PATH
        rows = _read_gate_blocks(sink_path)
        project_counter: Counter[str] = Counter(row.get("hook", "<unknown>") for row in rows)
        by_project[ref.label] = dict(project_counter)
        fleet_counter.update(project_counter)
    return GateBlockRollup(
        by_hook=dict(fleet_counter),
        by_project=by_project,
        total_blocks=sum(fleet_counter.values()),
    )

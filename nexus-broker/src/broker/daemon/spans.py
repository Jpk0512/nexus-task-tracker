"""broker.daemon.spans — F2-05 OTel-style span store: single-writer DuckDB
append behind the daemon (`nexus-foundation/plans/wave-2.md` §(e), FDEC-8
"OTLP-compatible, dual-sink" span model, ADR-001 Tier 2).

Shape: `trace = session`, `span = dispatch | gate | leg` (TASK-094 adds
`session | workflow | phase | tool_call` — the full trace-tree grain
`observability-taxonomy.json` names: session_trace > workflow > phase >
dispatch > leg > gate_fire > tool_call), carrying duration + token
attributes, with `parent_span_id` giving the tree its linkage. F2-02's
`event_bus.handle_span_emit` forward-stub (notepad gotcha #331: "validates
only that span is a dict; content-shape validation deferred to F2-05
storage") is upgraded by THIS module — `validate_span` is the one gate every
span crosses before it becomes a durable row; a malformed span raises
`SpanValidationError` (a typed `ValueError` subclass) and is REJECTED, never
silently dropped and never partially written.

Single-writer discipline (ADR-001 negative-risk #1, event-bus-design.md §4
point 4): exactly one `SpanStore` per daemon process, holding the ONE open
read-write DuckDB connection for the process's lifetime — the same
"documented AND structural" guarantee `tracing.TraceJournal` / `bus.EventBus`
already carry for their own state, verified empirically here too: DuckDB
itself refuses a second process's connection (even `read_only=True`) against
a file another process holds open for write, raising `duckdb.IOException` at
connect time — an accidental second writer fails LOUD, never silently
corrupts the file. A reader (e.g. `span_smoke.py`, the future F2-06
dashboard) must wait for the daemon to release the file (graceful shutdown)
before opening it, exactly like `project.db`'s own single-writer posture.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from broker.daemon import wtcs

VALID_KINDS = frozenset(
    {"dispatch", "gate", "leg", "session", "workflow", "phase", "tool_call"}
)  # session/workflow/phase/tool_call: TASK-094 capture-everything (observability-taxonomy.json levels)
VALID_STATUSES = frozenset({"UNSET", "OK", "ERROR"})
VALID_TOKEN_SOURCES = frozenset({"exact", "approx"})  # DEC-092 — harness-derived split counts reuse 'exact', never a new enum member

# TASK-093 stage 1 — the extended dispatch-span attribute schema, kept in
# 1:1 field-name lockstep with `dispatch_telemetry`'s own columns
# (`.memory/schema.sql`) so a telemetry row maps onto a dispatch span's
# attributes with zero name translation (see `server._emit_dispatch_span_
# from_telemetry`). Every field is OPTIONAL — a producer supplies whatever
# it knows, absence is always valid — but per the notepad #331 pattern
# (`event_bus.handle_span_emit` does only `isinstance(span, dict)`; THIS
# module is the one gate a span crosses before disk) a PRESENT field of the
# wrong type is a `SpanValidationError`, never a silent coercion or drop.
#
# TASK-094 additions: `phase_id`/`error_class` (str), the 4 split harness-
# usage-block counts (numeric, DEC-092 token_source='exact' convention — the
# blended `tokens` top-level column + attribute stays for F2-06 dashboard
# back-compat), and `revise_reasons` (a structured list, not free text).
_DISPATCH_STR_ATTRS: tuple[str, ...] = (
    "task_id", "workflow_id", "phase_id", "persona", "model", "task_tier", "marker", "error_class",
)
_DISPATCH_NUMERIC_ATTRS: tuple[str, ...] = (
    "tokens", "tool_uses", "tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_creation",
)
_DISPATCH_LIST_STR_ATTRS: tuple[str, ...] = ("revise_reasons",)

# TASK-094 gate_fire level (observability-taxonomy.json) — daemon RPC-miss
# observability: was the gate's own daemon RPC unreachable/timed out, and how
# long did it take before that was known. `revise_reasons` mirrors the
# dispatch-level field for a lens REVISE verdict fired at a gate.
_GATE_BOOL_ATTRS: tuple[str, ...] = ("rpc_miss",)
_GATE_NUMERIC_ATTRS: tuple[str, ...] = ("rpc_latency_ms",)
_GATE_LIST_STR_ATTRS: tuple[str, ...] = ("revise_reasons",)

# TASK-094 tool_call level (observability-taxonomy.json) — per-tool-invocation
# granularity; `tool_status` is validated against the existing VALID_STATUSES
# vocabulary (never a second status enum for the same concept).
_TOOL_CALL_STR_ATTRS: tuple[str, ...] = ("tool_name", "error_class")
_TOOL_CALL_NUMERIC_ATTRS: tuple[str, ...] = ("consecutive_read_count",)
_TOOL_CALL_BOOL_ATTRS: tuple[str, ...] = ("rpc_miss",)

# TASK-094 — first-class span keys (observability-taxonomy.json core-move #3):
# additive nullable columns on the `spans` table itself, not just JSON
# attributes, so a query can filter/join on them directly instead of paying a
# `json_extract_string` per row. Applies to EVERY span kind (not dispatch-only)
# — a gate/leg/tool_call span belonging to a workflow/phase/task is just as
# joinable as a dispatch span. Existing readers of `attributes.task_id` /
# `attributes.workflow_id` (e.g. `wtcs.py`'s views) are unaffected — dispatch
# spans keep writing both the column AND the attribute.
_FIRST_CLASS_KEY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("workflow_id", "VARCHAR"),
    ("phase_id", "VARCHAR"),
    ("task_id", "VARCHAR"),
)

SPANS_TABLE = "spans"

# TASK-094 — new installs get the first-class key columns inline (additive to
# the CREATE TABLE literal costs nothing for a fresh file); an EXISTING
# spans.duckdb predating this change gets them via the idempotent ALTER loop
# in `SpanStore.__init__` (`CREATE TABLE IF NOT EXISTS` never touches an
# already-created table's columns) — see `_FIRST_CLASS_KEY_COLUMNS`.
_DDL = f"""
CREATE TABLE IF NOT EXISTS {SPANS_TABLE} (
    trace_id VARCHAR NOT NULL,
    span_id VARCHAR NOT NULL,
    parent_span_id VARCHAR,
    name VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    start_time VARCHAR NOT NULL,
    end_time VARCHAR,
    duration_ms DOUBLE,
    tokens DOUBLE,
    workflow_id VARCHAR,
    phase_id VARCHAR,
    task_id VARCHAR,
    attributes VARCHAR NOT NULL,
    recorded_at VARCHAR NOT NULL,
    PRIMARY KEY (trace_id, span_id)
)
"""

_INSERT = f"""
INSERT INTO {SPANS_TABLE}
    (trace_id, span_id, parent_span_id, name, kind, status,
     start_time, end_time, duration_ms, tokens, workflow_id, phase_id, task_id,
     attributes, recorded_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_QUERY_COLUMNS = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "kind",
    "status",
    "start_time",
    "end_time",
    "duration_ms",
    "tokens",
    "workflow_id",
    "phase_id",
    "task_id",
    "attributes",
    "recorded_at",
)


class SpanValidationError(ValueError):
    """Malformed span payload at the write boundary (F2-05, notepad #331).

    Always raised — never a silent drop — so a caller (the RPC layer, a
    future in-process producer) sees a typed, specific reason instead of a
    span vanishing into the store unrecorded. A `ValueError` subclass so
    every existing bare-`except ValueError`/generic-exception boundary in
    this codebase (server.py's `_client_loop` catch-all in particular) keeps
    working unchanged.
    """


def spans_db_path_for(project_path: Path) -> Path:
    """`.memory/spans.duckdb` — the ADR-001 Tier 2 file, same directory
    `project.db` already lives in, same "daemon writes it at runtime,
    nobody hand-edits it" posture."""
    return project_path / ".memory" / "spans.duckdb"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def _require_str(span: dict[str, Any], field: str) -> str:
    value = span.get(field)
    if not isinstance(value, str) or not value:
        raise SpanValidationError(f"span.{field} must be a non-empty str")
    return value


def _optional_str(span: dict[str, Any], field: str) -> str | None:
    value = span.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SpanValidationError(f"span.{field} must be a non-empty str or None")
    return value


def _optional_number(span: dict[str, Any], field: str) -> float | None:
    value = span.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpanValidationError(f"span.{field} must be numeric or None")
    return float(value)


# TASK-094 — shared per-attribute-dict validators, factored out of the
# original single-purpose `validate_dispatch_attributes` body so the gate_fire
# and tool_call attribute schemas (below) get the same "optional, but a
# present value of the wrong type is a typed SpanValidationError, never a
# silent coercion" guarantee without re-deriving the checks per kind.
def _validate_str_attrs(attributes: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    for field in fields:
        value = attributes.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise SpanValidationError(f"{label} attribute {field!r} must be a non-empty str")


def _validate_numeric_attrs(attributes: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    for field in fields:
        value = attributes.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SpanValidationError(f"{label} attribute {field!r} must be numeric")


def _validate_bool_attrs(attributes: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    for field in fields:
        value = attributes.get(field)
        if value is None:
            continue
        if not isinstance(value, bool):
            raise SpanValidationError(f"{label} attribute {field!r} must be a bool")


def _validate_str_list_attrs(attributes: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    for field in fields:
        value = attributes.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise SpanValidationError(f"{label} attribute {field!r} must be a list of non-empty str")


def validate_dispatch_attributes(attributes: dict[str, Any]) -> None:
    """Shape-validate a `kind="dispatch"` span's `attributes` dict against the
    TASK-093/TASK-094 extended schema (task_id, workflow_id, phase_id,
    persona, model, task_tier, marker, error_class, tokens, tokens_in,
    tokens_out, tokens_cache_read, tokens_cache_creation, token_source,
    tool_uses, revise_reasons) — called from `validate_span` ONLY for
    dispatch-kind spans; a gate/leg span's attributes are never held to this
    schema (FDEC-8 dual-sink model: only dispatch spans carry
    harness-telemetry-shaped attributes).

    Every field is optional. When present: the string fields must be a
    non-empty `str`; the numeric fields must be numeric (never `bool`, which
    is an `int` subclass in Python); `revise_reasons` must be a list of
    non-empty `str` (a structured cause list, never free text); `token_source`
    must be one of `VALID_TOKEN_SOURCES` (`"exact"`/`"approx"` — DEC-092: the
    4 split harness-usage-block counts use `"exact"`, the enum itself is not
    extended). Any violation raises `SpanValidationError` — never a silent
    coercion, never a silent drop of the bad field.
    """
    _validate_str_attrs(attributes, _DISPATCH_STR_ATTRS, "dispatch")
    _validate_numeric_attrs(attributes, _DISPATCH_NUMERIC_ATTRS, "dispatch")
    _validate_str_list_attrs(attributes, _DISPATCH_LIST_STR_ATTRS, "dispatch")
    token_source = attributes.get("token_source")
    if token_source is not None and token_source not in VALID_TOKEN_SOURCES:
        raise SpanValidationError(
            f"dispatch attribute 'token_source' must be one of {sorted(VALID_TOKEN_SOURCES)}, got {token_source!r}"
        )


def validate_gate_attributes(attributes: dict[str, Any]) -> None:
    """Shape-validate a `kind="gate"` span's TASK-094 gate_fire attributes:
    `rpc_miss` (bool — did the gate's daemon RPC miss/timeout), `rpc_latency_ms`
    (numeric), `revise_reasons` (list of non-empty str). Called from
    `validate_span` ONLY for gate-kind spans; every OTHER existing gate
    attribute (`gate_name`, `verdict`, ...) stays unvalidated free-form, same
    as before this leaf.
    """
    _validate_bool_attrs(attributes, _GATE_BOOL_ATTRS, "gate")
    _validate_numeric_attrs(attributes, _GATE_NUMERIC_ATTRS, "gate")
    _validate_str_list_attrs(attributes, _GATE_LIST_STR_ATTRS, "gate")


def validate_tool_call_attributes(attributes: dict[str, Any]) -> None:
    """Shape-validate a `kind="tool_call"` span's TASK-094 attributes:
    `tool_name`/`error_class` (str), `tool_status` (one of `VALID_STATUSES` —
    reuses the existing span-status vocabulary rather than a second enum for
    the same concept), `consecutive_read_count` (numeric), `rpc_miss` (bool,
    for daemon-RPC-backed tool calls). `span_id`/`parent_span_id`/`duration_ms`
    are already generic top-level span fields, not attributes.
    """
    _validate_str_attrs(attributes, _TOOL_CALL_STR_ATTRS, "tool_call")
    _validate_numeric_attrs(attributes, _TOOL_CALL_NUMERIC_ATTRS, "tool_call")
    _validate_bool_attrs(attributes, _TOOL_CALL_BOOL_ATTRS, "tool_call")
    tool_status = attributes.get("tool_status")
    if tool_status is not None and tool_status not in VALID_STATUSES:
        raise SpanValidationError(
            f"tool_call attribute 'tool_status' must be one of {sorted(VALID_STATUSES)}, got {tool_status!r}"
        )


def validate_span(span: Any) -> dict[str, Any]:
    """Shape-validate one span payload at the write boundary. This is the
    ONLY gate a span crosses before it becomes a DuckDB row — `SpanStore.
    record` calls this before touching disk, and `event_bus.handle_span_emit`
    relies on it (transitively) for the daemon RPC surface, so a malformed
    span never reaches storage half-written or under a wrong shape.

    Required: `trace_id` (the session), `span_id`, `name`, `kind` (one of
    `VALID_KINDS` — TASK-094 adds `session`/`workflow`/`phase`/`tool_call` to
    the original `dispatch`/`gate`/`leg`). Everything else is optional with a
    safe default — `start_time` defaults to now, `status` defaults to
    `"UNSET"`, `attributes` defaults to `{}`. Raises `SpanValidationError` —
    never a bare `ValueError`, never a silent no-op — on any shape violation.

    TASK-094: `workflow_id`/`phase_id`/`task_id` are first-class top-level
    keys — read straight off `span`, not `attributes` — and apply to EVERY
    kind, not just `dispatch`. `attributes` schema validation is still
    kind-gated (`validate_dispatch_attributes` / `validate_gate_attributes` /
    `validate_tool_call_attributes`); `session`/`workflow`/`phase`/`leg`
    spans carry free-form, unvalidated attributes, same posture `gate`/`leg`
    already had pre-TASK-094.
    """
    if not isinstance(span, dict):
        raise SpanValidationError("span must be a dict")

    trace_id = _require_str(span, "trace_id")
    span_id = _require_str(span, "span_id")
    name = _require_str(span, "name")
    kind = _require_str(span, "kind")
    if kind not in VALID_KINDS:
        raise SpanValidationError(f"span.kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")

    parent_span_id = _optional_str(span, "parent_span_id")
    if parent_span_id == span_id:
        raise SpanValidationError("span.parent_span_id must not equal span.span_id")

    status = span.get("status", "UNSET")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise SpanValidationError(f"span.status must be one of {sorted(VALID_STATUSES)}, got {status!r}")

    start_time = _optional_str(span, "start_time") or _now_iso()
    end_time = _optional_str(span, "end_time")
    duration_ms = _optional_number(span, "duration_ms")
    tokens = _optional_number(span, "tokens")
    workflow_id = _optional_str(span, "workflow_id")
    phase_id = _optional_str(span, "phase_id")
    task_id = _optional_str(span, "task_id")

    attributes = span.get("attributes", {})
    if not isinstance(attributes, dict):
        raise SpanValidationError("span.attributes must be a dict")
    if kind == "dispatch":
        validate_dispatch_attributes(attributes)
    elif kind == "gate":
        validate_gate_attributes(attributes)
    elif kind == "tool_call":
        validate_tool_call_attributes(attributes)
    try:
        attributes_json = json.dumps(attributes, default=str)
    except TypeError as exc:
        raise SpanValidationError(f"span.attributes must be JSON-serializable: {exc}") from exc

    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "status": status,
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": duration_ms,
        "tokens": tokens,
        "workflow_id": workflow_id,
        "phase_id": phase_id,
        "task_id": task_id,
        "attributes": attributes_json,
        "recorded_at": _now_iso(),
    }


class SpanStore:
    """Daemon-resident, single-writer DuckDB span store (ADR-001 Tier 2).

    One instance per daemon process (see `event_bus.EventBusState.
    span_store`, lazily constructed on the first `span.emit`), holding the
    one open read-write connection for as long as the daemon lives.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(db_path))
        self._conn.execute(_DDL)
        # TASK-094 — additive migration for a spans.duckdb file created before
        # the first-class workflow_id/phase_id/task_id columns existed:
        # `CREATE TABLE IF NOT EXISTS` above never alters an already-created
        # table, so an existing file needs this idempotent ADD COLUMN pass
        # (DuckDB's `ADD COLUMN IF NOT EXISTS` no-ops cleanly on repeat calls).
        for column_name, column_type in _FIRST_CLASS_KEY_COLUMNS:
            self._conn.execute(f"ALTER TABLE {SPANS_TABLE} ADD COLUMN IF NOT EXISTS {column_name} {column_type}")
        # TASK-093 stage 1 — the WTCS analytics view chain lives on this same
        # connection (single-writer, ADR-001); idempotent, so re-running it on
        # every construction is always safe (wtcs.create_wtcs_views docstring).
        wtcs.create_wtcs_views(self._conn)
        self.span_count = 0

    def record(self, span: dict[str, Any]) -> dict[str, Any]:
        """Validate then append ONE span row. Raises `SpanValidationError`
        on any shape violation — the span is REJECTED before the `INSERT`
        ever runs, so a malformed span never lands as a partial row."""
        clean = validate_span(span)
        self._conn.execute(
            _INSERT,
            [
                clean["trace_id"],
                clean["span_id"],
                clean["parent_span_id"],
                clean["name"],
                clean["kind"],
                clean["status"],
                clean["start_time"],
                clean["end_time"],
                clean["duration_ms"],
                clean["tokens"],
                clean["workflow_id"],
                clean["phase_id"],
                clean["task_id"],
                clean["attributes"],
                clean["recorded_at"],
            ],
        )
        self.span_count += 1
        return clean

    def query_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """Every span for one trace, oldest-recorded-first — the exact
        parent-child-linkage query this leaf's acceptance criterion (and
        `span_smoke.py`) needs."""
        rows = self._conn.execute(
            f"SELECT {', '.join(_QUERY_COLUMNS)} FROM {SPANS_TABLE} "
            "WHERE trace_id = ? ORDER BY recorded_at",
            [trace_id],
        ).fetchall()
        return [dict(zip(_QUERY_COLUMNS, row, strict=True)) for row in rows]

    def count(self) -> int:
        result = self._conn.execute(f"SELECT COUNT(*) FROM {SPANS_TABLE}").fetchone()
        return int(result[0]) if result else 0

    def close(self) -> None:
        self._conn.close()

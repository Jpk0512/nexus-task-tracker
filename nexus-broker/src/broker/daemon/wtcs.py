"""broker.daemon.wtcs — Workflow Time-Cost Score (WTCS) analytics, built
directly on top of the F2-05 `spans` DuckDB store (TASK-093 stage 1).

ROOT CAUSE this module's sibling fix (`server._emit_dispatch_span_from_telemetry`)
closes, for context: F2-05 shipped `spans.SpanStore` + the `span.emit` RPC fully
tested (`test_spans.py`, `span_smoke.py` against a real spawned daemon) but wired
to NO live producer — no hook, no daemon-internal caller, ever invoked `span.emit`
outside a test. Every real dispatch-telemetry write in this repo either shells out
to `log.py dispatch record` directly (`completion-capture.py`, `broker.conductor.
dag.record_dispatch_telemetry` — both bypass the daemon entirely) or, when it DOES
reach the daemon via the `record_telemetry` RPC (`fallback.py`'s documented "future
hook integration" path), landed only in `TelemetryStore` -> `project.db`'s
`dispatch_telemetry` table, never in `spans.duckdb`. `server.py` now bridges that
RPC (best-effort, additive, never touching `.claude/hooks/**`) so any caller that
DOES reach `record_telemetry` also durably materializes a `dispatch`-kind span —
this module is what makes that data queryable.

WTCS formula (this leaf's own spec):

    WTCS = (M / M_ref)^0.5 * (K / K_ref)^0.3 * (A / A_ref)^0.2 * 1.25^R * 1.5^B

  - M: wall-minutes, first span recorded for the task to the last span whose
       `marker` attribute is exactly `"DONE"`.
  - K: total tokens across the task's dispatch spans (native `tokens` column,
       falling back to the `attributes.tokens` JSON field when the column is
       NULL — see `dispatch_span_attrs` below).
  - A: distinct `persona` count across the task's dispatch spans.
  - R: count of dispatch spans whose `marker` attribute is exactly `"REVISE"`.
  - B: count of dispatch spans whose `marker` attribute is exactly `"BLOCKED"`.
  - M_ref / K_ref / A_ref: the ROLLING 14-day MEDIAN of M / K / A for every
    OTHER completed task sharing the same `task_tier` classification (a
    trailing window measured from `now()` at query time — these are VIEWS,
    never materialized, so "rolling" is implicit in every SELECT).

Expected behaviors (stage-2/hermes: author tests against these, not against
implementation details):

  1. `create_wtcs_views(conn)` is idempotent — `CREATE OR REPLACE VIEW`, safe
     to call once per `SpanStore` construction (wired into `spans.SpanStore.
     __init__`, right after the `spans` table DDL) and safe to call again on
     an existing connection with no error and no duplicate-object failure.
  2. `dispatch_span_attrs` only ever surfaces `kind = 'dispatch'` rows — a
     `gate`/`leg` span is invisible to every view in this module (FDEC-8's
     dual-sink model: only dispatch spans carry harness-telemetry-shaped
     attributes; TASK-093 stage 1 scope is dispatch-only).
  3. A dispatch span with no `task_id` attribute (present or not JSON-
     parseable) is EXCLUDED from `task_span_rollup` — never grouped under a
     NULL/empty key, never silently coerced into a fake "unknown" bucket.
  4. `task_span_rollup.wall_minutes` / `last_done_at` are NULL for a task with
     no `"DONE"`-marker span yet (an in-flight or abandoned task) — it is
     INCLUDED in the rollup (so `total_tokens`/`agent_count`/`revise_count`/
     `blocked_count` stay visible for an in-flight task) but EXCLUDED from
     `wtcs_classification_medians_14d` and `wtcs_score` (both filter on
     `last_done_at IS NOT NULL`) — an incomplete task must never pollute the
     reference medians or receive a fabricated score.
  5. `wtcs_classification_medians_14d` carries one row per completed subject
     `task_id`; each row's `m_ref`/`k_ref`/`a_ref`/`sample_size` are computed
     by a correlated subquery over every OTHER completed task sharing that
     row's `task_tier` (`peer.task_id != subject.task_id`, matching this
     module's own "every OTHER completed task" formula spec verbatim — the
     subject's own row never contributes to its own reference stats). A
     `task_tier` never supplied by any completed task inside the trailing
     14-day window simply produces no rows with that `classification` — never
     a zero-filled or NULL-classification row. A lone task in a fresh
     `task_tier` still gets a row keyed to itself, with NULL medians
     (0-row peer pool) — see behavior 6.
  6. `wtcs_score.wtcs` is NULL (never an error, never a fabricated 0/1) when
     the task's classification has no medians row yet (cold-start: first task
     of a new `task_tier`, or the classification's medians are literally 0 —
     `NULLIF(ref, 0)` guards the divide) — a consumer must treat NULL as
     "not enough history yet," not as a real score of zero.
  7. `R`/`B` (`revise_count`/`blocked_count`) are always non-negative integers,
     never NULL — `COUNT(*) FILTER (...)` returns 0, not NULL, when no span
     matches, so `1.25^R`/`1.5^B` are always well-defined once M/K/A resolve.
"""
from __future__ import annotations

import duckdb

DISPATCH_SPAN_ATTRS_VIEW = "dispatch_span_attrs"
TASK_SPAN_ROLLUP_VIEW = "task_span_rollup"
CLASSIFICATION_MEDIANS_VIEW = "wtcs_classification_medians_14d"
WTCS_SCORE_VIEW = "wtcs_score"

ROLLING_WINDOW_DAYS = 14

_M_EXPONENT = 0.5
_K_EXPONENT = 0.3
_A_EXPONENT = 0.2
_REVISE_BASE = 1.25
_BLOCKED_BASE = 1.5

_DISPATCH_SPAN_ATTRS_SQL = f"""
CREATE OR REPLACE VIEW {DISPATCH_SPAN_ATTRS_VIEW} AS
SELECT
    trace_id,
    span_id,
    parent_span_id,
    status,
    start_time,
    end_time,
    duration_ms,
    COALESCE(tokens, TRY_CAST(json_extract_string(attributes, '$.tokens') AS DOUBLE)) AS tokens,
    json_extract_string(attributes, '$.task_id') AS task_id,
    json_extract_string(attributes, '$.workflow_id') AS workflow_id,
    json_extract_string(attributes, '$.persona') AS persona,
    json_extract_string(attributes, '$.model') AS model,
    json_extract_string(attributes, '$.task_tier') AS task_tier,
    json_extract_string(attributes, '$.marker') AS marker,
    json_extract_string(attributes, '$.token_source') AS token_source,
    TRY_CAST(json_extract_string(attributes, '$.tool_uses') AS INTEGER) AS tool_uses,
    TRY_CAST(recorded_at AS TIMESTAMP) AS recorded_at_ts,
    recorded_at
FROM spans
WHERE kind = 'dispatch'
"""

_TASK_SPAN_ROLLUP_SQL = f"""
CREATE OR REPLACE VIEW {TASK_SPAN_ROLLUP_VIEW} AS
SELECT
    task_id,
    any_value(task_tier) AS task_tier,
    min(recorded_at_ts) AS first_span_at,
    max(CASE WHEN marker = 'DONE' THEN recorded_at_ts END) AS last_done_at,
    date_diff(
        'minute',
        min(recorded_at_ts),
        max(CASE WHEN marker = 'DONE' THEN recorded_at_ts END)
    ) AS wall_minutes,
    sum(tokens) AS total_tokens,
    count(DISTINCT persona) AS agent_count,
    count(*) FILTER (WHERE marker = 'REVISE') AS revise_count,
    count(*) FILTER (WHERE marker = 'BLOCKED') AS blocked_count
FROM {DISPATCH_SPAN_ATTRS_VIEW}
WHERE task_id IS NOT NULL AND task_id != ''
GROUP BY task_id
"""

_CLASSIFICATION_MEDIANS_SQL = f"""
CREATE OR REPLACE VIEW {CLASSIFICATION_MEDIANS_VIEW} AS
SELECT
    subject.task_id AS task_id,
    subject.task_tier AS classification,
    (
        SELECT median(peer.wall_minutes)
        FROM {TASK_SPAN_ROLLUP_VIEW} peer
        WHERE peer.task_tier = subject.task_tier
          AND peer.task_id != subject.task_id
          AND peer.last_done_at IS NOT NULL
          AND peer.last_done_at >= now()::TIMESTAMP - INTERVAL '{ROLLING_WINDOW_DAYS} days'
    ) AS m_ref,
    (
        SELECT median(peer.total_tokens)
        FROM {TASK_SPAN_ROLLUP_VIEW} peer
        WHERE peer.task_tier = subject.task_tier
          AND peer.task_id != subject.task_id
          AND peer.last_done_at IS NOT NULL
          AND peer.last_done_at >= now()::TIMESTAMP - INTERVAL '{ROLLING_WINDOW_DAYS} days'
    ) AS k_ref,
    (
        SELECT median(peer.agent_count)
        FROM {TASK_SPAN_ROLLUP_VIEW} peer
        WHERE peer.task_tier = subject.task_tier
          AND peer.task_id != subject.task_id
          AND peer.last_done_at IS NOT NULL
          AND peer.last_done_at >= now()::TIMESTAMP - INTERVAL '{ROLLING_WINDOW_DAYS} days'
    ) AS a_ref,
    (
        SELECT count(*)
        FROM {TASK_SPAN_ROLLUP_VIEW} peer
        WHERE peer.task_tier = subject.task_tier
          AND peer.task_id != subject.task_id
          AND peer.last_done_at IS NOT NULL
          AND peer.last_done_at >= now()::TIMESTAMP - INTERVAL '{ROLLING_WINDOW_DAYS} days'
    ) AS sample_size
FROM {TASK_SPAN_ROLLUP_VIEW} subject
WHERE subject.last_done_at IS NOT NULL
  AND subject.task_tier IS NOT NULL
"""

_WTCS_SCORE_SQL = f"""
CREATE OR REPLACE VIEW {WTCS_SCORE_VIEW} AS
SELECT
    r.task_id,
    r.task_tier,
    r.first_span_at,
    r.last_done_at,
    r.wall_minutes,
    r.total_tokens,
    r.agent_count,
    r.revise_count,
    r.blocked_count,
    m.m_ref,
    m.k_ref,
    m.a_ref,
    m.sample_size AS classification_sample_size,
    POWER(r.wall_minutes / NULLIF(m.m_ref, 0), {_M_EXPONENT})
        * POWER(r.total_tokens / NULLIF(m.k_ref, 0), {_K_EXPONENT})
        * POWER(r.agent_count / NULLIF(m.a_ref, 0), {_A_EXPONENT})
        * POWER({_REVISE_BASE}, r.revise_count)
        * POWER({_BLOCKED_BASE}, r.blocked_count) AS wtcs
FROM {TASK_SPAN_ROLLUP_VIEW} r
LEFT JOIN {CLASSIFICATION_MEDIANS_VIEW} m
    ON m.classification = r.task_tier AND m.task_id = r.task_id
WHERE r.last_done_at IS NOT NULL
"""

_ALL_VIEWS_SQL: tuple[str, ...] = (
    _DISPATCH_SPAN_ATTRS_SQL,
    _TASK_SPAN_ROLLUP_SQL,
    _CLASSIFICATION_MEDIANS_SQL,
    _WTCS_SCORE_SQL,
)


def create_wtcs_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Create (or replace) the WTCS analytics view chain on `conn`, which must
    already have the `spans` table (see `spans._DDL`) — every view here reads
    straight off `spans`, never a copy/materialization. Idempotent: safe to
    call on every `SpanStore` construction (see `spans.SpanStore.__init__`).
    """
    for statement in _ALL_VIEWS_SQL:
        conn.execute(statement)

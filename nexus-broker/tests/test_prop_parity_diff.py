"""F3-05 property suite 5/5 — row-level parity-diff INVARIANTS.

The parity-diff is the row-diff F3-03 needs to compare an event-store projection
against the live `project.db` table during dual-write cutover. INVARIANTS:

  * REFLEXIVE / EMPTY-ON-EQUAL:  ∀ x:  diff(x, x) == []   (identical inputs → no
    differences).
  * DETECTS ANY INJECTED MUTATION:  mutating one cell of one row is ALWAYS
    surfaced as a ('changed', key, fields) record naming that row's key and the
    changed field; dropping a row surfaces ('missing', key, …); adding a row
    surfaces ('extra', key, …). No mutation is ever silently swallowed.

BOUNDARY NOTE (F3-03 promotion — COMPLETE): `diff_rows` has been PROMOTED
verbatim into its production home `broker.daemon.event_store` and this suite now
imports it from there (the test-local reference copy is deleted). `store_parity.py`
also consumes the promoted function. These properties remain the executable spec
that pins `diff_rows`'s invariants against the production implementation.

Regression corpus: explicit `@example(...)` pinned inline (empty-vs-empty, and a
single-row single-field mutation) — the shrinker fixed points.
"""
from __future__ import annotations

from typing import Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from broker.daemon.event_store import diff_rows

pytestmark = pytest.mark.property

_int = st.integers(min_value=0, max_value=1000)
_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=0, max_size=12
)


def _by_id(row: dict[str, Any]) -> Any:
    return row["id"]


@st.composite
def _rows(draw: st.DrawFn, min_size: int = 0, max_size: int = 8) -> list[dict[str, Any]]:
    """Rows with a UNIQUE `id` key and three value fields a/b/c."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    return [
        {
            "id": f"r{i}",
            "a": draw(_int),
            "b": draw(_text),
            "c": draw(st.booleans()),
        }
        for i in range(n)
    ]


@given(rows=_rows())
@example(rows=[])
@example(rows=[{"id": "r0", "a": 1, "b": "x", "c": True}])
def test_diff_of_identical_rows_is_empty(rows: list[dict[str, Any]]) -> None:
    """INVARIANT: diff(x, x) == [] — identical projections show no drift."""
    assert diff_rows(rows, list(rows), key=_by_id) == []
    # order independence: same rows in reverse still diff-empty (keyed compare)
    assert diff_rows(rows, list(reversed(rows)), key=_by_id) == []


@given(rows=_rows(min_size=1), field=st.sampled_from(["a", "b", "c"]), which=st.integers(min_value=0))
@example(rows=[{"id": "r0", "a": 1, "b": "x", "c": True}], field="a", which=0)
def test_diff_detects_any_single_cell_mutation(
    rows: list[dict[str, Any]], field: str, which: int
) -> None:
    """INVARIANT: any single-cell mutation is surfaced as a ('changed', key,
    fields) record naming the mutated row and field — never silently absorbed."""
    idx = which % len(rows)
    mutated = [dict(row) for row in rows]
    old = mutated[idx][field]
    if field == "a":
        mutated[idx][field] = old + 1
    elif field == "b":
        mutated[idx][field] = old + "!"
    else:
        mutated[idx][field] = not old
    assert mutated[idx][field] != old, "mutation must actually change the cell"

    diffs = diff_rows(rows, mutated, key=_by_id)
    changed = [r for r in diffs if r[0] == "changed" and r[1] == rows[idx]["id"]]
    assert len(changed) == 1, f"mutation not detected in {diffs}"
    assert field in changed[0][2]


@given(rows=_rows(min_size=1), which=st.integers(min_value=0))
@example(rows=[{"id": "r0", "a": 1, "b": "x", "c": True}], which=0)
def test_diff_detects_a_dropped_row(rows: list[dict[str, Any]], which: int) -> None:
    """INVARIANT: a row present in expected but absent from actual → ('missing',
    key, …)."""
    idx = which % len(rows)
    dropped = [row for i, row in enumerate(rows) if i != idx]
    diffs = diff_rows(rows, dropped, key=_by_id)
    assert ("missing", rows[idx]["id"]) in [(r[0], r[1]) for r in diffs]


@given(rows=_rows())
@example(rows=[])
def test_diff_detects_an_added_row(rows: list[dict[str, Any]]) -> None:
    """INVARIANT: a row present in actual but absent from expected → ('extra',
    key, …)."""
    added = [*rows, {"id": "r-new", "a": 7, "b": "new", "c": False}]
    diffs = diff_rows(rows, added, key=_by_id)
    assert ("extra", "r-new") in [(r[0], r[1]) for r in diffs]

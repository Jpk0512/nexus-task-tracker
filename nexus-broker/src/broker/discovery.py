"""nexus_discover / nexus_prepare / nexus_run — R4-T06 daemon groundwork (R3-T02/N05).

Transport-agnostic core (C1.a): this module imports NOTHING from `fastmcp` or
any transport layer — only stdlib + sibling broker modules (state/registry).
server.py registers thin @mcp.tool() wrappers around the *_impl functions here,
mirroring the worktree_registry.py / nexus_register_worktree split already in
this package. When the R4-T06 Option C daemon stands up its Unix-socket
JSON-RPC transport, it imports these same *_impl functions unchanged — no
stdio-only assumption anywhere below (C1.a).

No resident state in the hook/broker process (C1.d): every call reads/writes
broker_state.json via state.read_state()/write_state() — the SAME file-backed
mechanism nexus_validate_brief already uses. Nothing here holds an in-memory
dict across calls.

No fleet-schema assumption (C1.c): nothing below touches .memory/schema.sql or
assumes a project.db shape; `nexus_discover` reads only the in-package
ALLOWED_PERSONAS/PERSONA_INTENTS registry.

Semantics (groundwork tier — these are read/stage/mark primitives, not a
dispatch engine):
  - nexus_discover(): list dispatchable personas + their legal intents. Pure,
    read-only, no state file touched.
  - nexus_prepare(persona, intent, turn_id): stage a prepared dispatch —
    validates persona/intent legality (reusing registry.py, the same table
    nexus_validate_brief checks) and writes a `prepared_at` marker into
    broker_state.json so nexus_run can confirm preparation happened first.
    Deterministic; no model call, no network I/O, no sleep.
  - nexus_run(turn_id): mark a prepared dispatch as running — requires a
    matching, unexpired `prepared_at` (staleness governed by
    resolve_turn_stale_seconds(), the SECONDARY N05 deliverable; see state.py).
    Returns ok=False (never raises) when preparation is missing/stale/mismatched
    so a caller can retry nexus_prepare rather than crash.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from broker.registry import ALLOWED_PERSONAS, PERSONA_INTENTS
from broker.state import read_state, resolve_turn_stale_seconds, write_state


class DiscoverResult(TypedDict):
    personas: list[str]
    persona_intents: dict[str, list[str]]


def nexus_discover_impl() -> DiscoverResult:
    """List every dispatchable persona and its legal intents. Pure, read-only."""
    return DiscoverResult(
        personas=sorted(ALLOWED_PERSONAS),
        persona_intents={p: list(intents) for p, intents in PERSONA_INTENTS.items()},
    )


class PrepareResult(TypedDict):
    ok: bool
    persona: str
    intent: str
    turn_id: str
    prepared_at: str | None
    errors: list[str]


def nexus_prepare_impl(persona: str, intent: str, turn_id: str) -> PrepareResult:
    """Stage a prepared dispatch: validate persona/intent, mark prepared_at.

    Deterministic legality check only (mirrors nexus_validate_brief steps 1-2,
    without the full brief-normalization pipeline — this is groundwork, not a
    brief-approval replacement). Writes to the SAME broker_state.json file
    nexus_validate_brief writes (C1.d: no separate resident state).
    """
    errors: list[str] = []
    if persona not in ALLOWED_PERSONAS:
        errors.append(f"persona '{persona}' is not in the dispatch registry")
    elif intent not in PERSONA_INTENTS.get(persona, []):
        errors.append(
            f"intent '{intent}' is not legal for persona '{persona}' "
            f"(allowed: {PERSONA_INTENTS.get(persona, [])})"
        )
    if not turn_id or not turn_id.strip():
        errors.append("turn_id must be non-empty")

    ok = not errors
    prepared_at: str | None = None
    if ok:
        prepared_at = datetime.now(tz=UTC).isoformat()
        state = read_state()
        state["prepared_at"] = prepared_at
        state["prepared_persona"] = persona
        state["prepared_intent"] = intent
        state["prepared_turn_id"] = turn_id
        write_state(state)

    return PrepareResult(
        ok=ok,
        persona=persona,
        intent=intent,
        turn_id=turn_id,
        prepared_at=prepared_at,
        errors=errors,
    )


class RunResult(TypedDict):
    ok: bool
    turn_id: str
    started_at: str | None
    errors: list[str]


def _prepared_age_seconds(state: dict[str, Any], now: datetime) -> float | None:
    ts = state.get("prepared_at")
    if not ts:
        return None
    try:
        prepared = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if prepared.tzinfo is None:
        prepared = prepared.replace(tzinfo=UTC)
    return (now - prepared).total_seconds()


def nexus_run_impl(turn_id: str) -> RunResult:
    """Mark a previously-prepared dispatch as running.

    Requires a matching, unexpired prepared_turn_id/prepared_at pair (window =
    resolve_turn_stale_seconds(), the SECONDARY N05 deliverable). No sleep, no
    polling loop, no busy-wait — a single state read + one comparison, so the
    per-call cost is the same order of magnitude as N04's ~13ms
    nexus_validate_brief ledger figure, not a fixed 120s wait.
    """
    state = read_state()
    errors: list[str] = []

    prepared_turn_id = state.get("prepared_turn_id")
    if prepared_turn_id != turn_id:
        errors.append(
            f"no matching prepared dispatch for turn_id '{turn_id}' — call "
            "nexus_prepare first"
        )
    else:
        now = datetime.now(tz=UTC)
        age = _prepared_age_seconds(state, now)
        window = resolve_turn_stale_seconds()
        if age is None:
            errors.append("prepared_at is missing or unparseable — re-run nexus_prepare")
        elif age > window:
            errors.append(
                f"prepared dispatch is stale ({age:.1f}s > {window}s window) — "
                "re-run nexus_prepare"
            )

    ok = not errors
    started_at: str | None = None
    if ok:
        started_at = datetime.now(tz=UTC).isoformat()
        state["started_at"] = started_at
        write_state(state)

    return RunResult(ok=ok, turn_id=turn_id, started_at=started_at, errors=errors)

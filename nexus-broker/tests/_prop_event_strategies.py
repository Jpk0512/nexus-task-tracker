"""F3-05 — shared hypothesis strategies for generated event streams.

NOT a test module (no `test_` prefix → never collected). Produces well-formed
`broker.daemon.event_store` events whose payloads carry EXACTLY the keys the
production folds read (real production schema — no invented fields), so a
generated stream drives the REAL projection code without a KeyError and without
mocking the store. Used by `test_prop_replay_determinism.py` and
`test_prop_projection_idempotency.py`.

Design choices that keep the stream fold-safe:
  * `event_version` is pinned to 1 (pre-invariant) so a generated uncited PASS
    never trips `fold_validation_log`'s POST-invariant hard-refuse
    (CITED_VERDICT_MIN_VERSION == 2) — that guard has its own targeted test.
  * verdicts are drawn only from the closed enum {PASS, PARTIAL, FAIL}.
  * `event_id` is index-unique within a stream, so a stream is a set of distinct
    events (the idempotency property re-delivers the SAME set).
"""
from __future__ import annotations

from hypothesis import strategies as st

_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=1, max_size=16
)
_int = st.integers(min_value=0, max_value=1000)
_ts = st.sampled_from(
    ["2026-01-01T00:00:00Z", "2026-06-15T12:30:00Z", "2026-07-17T09:00:00Z"]
)
_task_id = st.sampled_from(["t1", "t2", "t3"])
_sess_id = st.sampled_from(["s1", "s2", "s3"])
_verdict = st.sampled_from(["PASS", "PARTIAL", "FAIL"])
_persona = st.sampled_from(["hermes", "atlas", "quill-py", "lens", "scout"])
_status = st.sampled_from(["todo", "doing", "done"])
_marker = st.sampled_from(["## NEXUS:DONE", "## NEXUS:REVISE", "## NEXUS:BLOCKED"])


def _spec(event_type: str, payload_strategy: st.SearchStrategy) -> st.SearchStrategy:
    return st.tuples(st.just(event_type), payload_strategy)


_EVENT_SPECS = st.one_of(
    _spec("task.created", st.fixed_dictionaries(
        {"id": _task_id, "title": _text, "status": _status, "priority": _text, "created_at": _ts})),
    _spec("task.updated", st.fixed_dictionaries(
        {"id": _task_id, "changed_fields": st.fixed_dictionaries({"status": _status, "title": _text}),
         "updated_at": _ts})),
    _spec("task.stalled", st.fixed_dictionaries(
        {"id": _task_id, "stall_count": _int, "last_persona": _persona, "updated_at": _ts})),
    _spec("task.archived", st.fixed_dictionaries(
        {"id": _task_id, "notes": _text, "updated_at": _ts})),
    _spec("task.id_repaired", st.fixed_dictionaries(
        {"orphan_id": _task_id, "canonical_id": _task_id})),
    _spec("session.started", st.fixed_dictionaries(
        {"id": _sess_id, "started_at": _ts, "branch": _text})),
    _spec("session.ended", st.fixed_dictionaries(
        {"id": _sess_id, "ended_at": _ts, "summary": _text, "next_step": _text})),
    _spec("session.reset", st.fixed_dictionaries(
        {"closed_session_id": _sess_id, "new_session_id": _sess_id,
         "closed_at": _ts, "new_started_at": _ts, "branch": _text})),
    _spec("session.message_counted", st.fixed_dictionaries(
        {"id": _sess_id, "user_message_count": _int})),
    _spec("lens.verdict.recorded", st.fixed_dictionaries(
        {"verdict": _verdict, "agent_validated": st.just("lens"), "target_agent": _persona,
         "task_or_brief_hash": _text, "evidence_backed": st.booleans(), "validated_at": _ts})),
    _spec("dispatch.completed", st.fixed_dictionaries(
        {"persona": _persona, "session_id": _sess_id, "dispatch_id": _text,
         "marker": _marker, "model": _text})),
    _spec("skill.loaded", st.fixed_dictionaries(
        {"dispatch_id": _text, "skill_id": _text, "ts": _ts, "byte_len": _int})),
    _spec("span.emitted", st.fixed_dictionaries({"note": _text})),
)


@st.composite
def event_streams(draw: st.DrawFn, max_size: int = 25) -> list[dict]:
    """A list of distinct, fold-safe events in append (== `seq`) order."""
    specs = draw(st.lists(_EVENT_SPECS, min_size=0, max_size=max_size))
    events: list[dict] = []
    for index, (event_type, payload) in enumerate(specs):
        events.append(
            {
                "event_id": f"evt-{index}",
                "event_type": event_type,
                "aggregate_id": f"agg-{index}",
                "event_version": 1,
                "session_id": None,
                "occurred_at": "2026-07-17T00:00:00Z",
                "payload": payload,
            }
        )
    return events

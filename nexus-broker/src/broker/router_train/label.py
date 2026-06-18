"""LABEL — left-join capture records to the dispatch sidecar (PRIMARY
ground-truth) with intra-session nearest-FOLLOWING-dispatch alignment
(00-DESIGN.md 'LABEL').

Each routed prompt is labeled with the persona the orchestrator ACTUALLY
dispatched next within the same session — NOT a naive session-level join (which
smears one label across a multi-turn session). Within a session, records and
dispatches are ordered by timestamp; each record takes the nearest dispatch whose
timestamp is >= the record's. Exact (session_id, prompt_hash) sidecar matches win
over time alignment.

Every emitted pair carries a ``label_status`` stamp classifying its dispatched
persona. Only ``label_status == "ok"`` rows are TRAINING-GRADE; the rest are kept
on the result (visible to the check report) but MUST be excluded from any training
export. The four statuses:

  - ``ok``                   — a valid dispatchable Nexus router target
                               (membership in NEXUS_PERSONAS).
  - ``dropped_generic``      — NOT a Nexus route target: a Claude built-in agent
                               type, a plugin-namespaced agent ("codex:…"), or an
                               ad-hoc probe agent. Training on it teaches
                               non-router behavior.
  - ``quarantined_retired``  — a RETIRED Nexus base name (forge/pipeline/quill);
                               ambiguous — each was split into two successors and
                               cannot be mapped to a single label, so it needs a
                               human split decision before training.
  - ``quarantined_buggy``    — router_version == "buggy" (OPT-001 quarantine, the
                               blank-persona-description bug); the captured input is
                               corrupt regardless of the dispatched persona.
"""
from __future__ import annotations

from typing import Any

from broker.registry import DISPATCHABLE_PERSONAS, RETIRED_BASE_PERSONAS
from broker.router_train.aggregate import _normalize_record, prompt_hash

BUGGY = "buggy"
LABEL_SOURCE_SIDECAR = "dispatch_sidecar"
LABEL_CONFIDENCE_SIDECAR = 1.0

LABEL_STATUS_OK = "ok"
LABEL_STATUS_DROPPED_GENERIC = "dropped_generic"
LABEL_STATUS_QUARANTINED_RETIRED = "quarantined_retired"
LABEL_STATUS_QUARANTINED_BUGGY = "quarantined_buggy"

# Generic Claude built-in / external-plugin agent types — NOT Nexus personas.
# These are dispatched by Claude Code itself or by installed plugins (general
# explore/search/setup helpers, the claude-code-guide built-in, plugin-namespaced
# agents like "codex:codex-rescue"), so a row labeled with one teaches the router
# to imitate a non-routable built-in rather than to pick a Nexus persona. Compared
# case-insensitively (Claude emits both "Explore" and "general-purpose" shapes).
# Plugin-namespaced names (those containing ":") are caught structurally in
# classify_label, not enumerated here. Drop all of these from training.
GENERIC_BUILTINS = frozenset(
    {
        "general-purpose",
        "general",
        "explore",
        "claude",
        "claude-code-guide",
        "statusline-setup",
        "output-style-setup",
    }
)

# RETIRED Nexus base names. Each was split into two scope-specific successors
# (forge → forge-ui / forge-wire, pipeline → pipeline-data / pipeline-async,
# quill → quill-ts / quill-py) and persona-alias-resolver.sh DENIES the bare base
# name. A mined base name is AMBIGUOUS — it cannot be mapped to a single split
# successor without the brief's scope — so it is quarantined for a human split
# decision instead of dropped or guessed. DERIVED (OPT-002) from the single
# source broker.registry.RETIRED_BASE_PERSONAS so this set cannot drift either.
RETIRED_BASE_NAMES = RETIRED_BASE_PERSONAS

# The dispatchable Nexus persona roster — the ONLY names that are training-grade
# ("ok"). DERIVED (OPT-002) from the single source of truth,
# broker.registry.DISPATCHABLE_PERSONAS, instead of being re-listed here. The
# mined transcripts contain dispatches to MANY non-router agents — Claude
# built-ins, plugin agents, and ad-hoc probe agents (researcher / nexus-ops /
# tool-prober / …) — none of which the router can route to. Membership in the
# broker dispatch registry is exactly "is this a real Nexus dispatch target", so
# allow-by-DISPATCHABLE_PERSONAS guarantees "ok ⊆ real Nexus personas" AND can
# never drift from the broker again. The retired bases are excluded by
# construction (they are not in the registry) and are handled before this check
# in classify_label via RETIRED_BASE_NAMES.
NEXUS_PERSONAS = DISPATCHABLE_PERSONAS


def classify_label(persona: str, router_version: str) -> str:
    """Classify a dispatched persona into a ``label_status``.

    Precedence:
      1. ``router_version == "buggy"`` (OPT-001 quarantine) — the captured input is
         corrupt regardless of which persona was dispatched.
      2. A RETIRED Nexus base name (forge/pipeline/quill) → ``quarantined_retired``
         (ambiguous; needs a human split before training).
      3. A dispatchable Nexus persona (NEXUS_PERSONAS) → ``ok`` — training-grade.
      4. Everything else → ``dropped_generic``: Claude built-ins, plugin-namespaced
         agents (a ``:`` in the name), and ad-hoc/probe agents — none are router
         targets, so they teach non-router behavior and are dropped.
    """
    if router_version == BUGGY:
        return LABEL_STATUS_QUARANTINED_BUGGY
    folded = persona.strip().casefold()
    if folded in RETIRED_BASE_NAMES:
        return LABEL_STATUS_QUARANTINED_RETIRED
    if folded in NEXUS_PERSONAS:
        return LABEL_STATUS_OK
    # Not a route target. Known generics (GENERIC_BUILTINS) and plugin-namespaced
    # agents (a ":" in the name) are the explicit, documented cases; the catch-all
    # also covers ad-hoc probe agents not on the Nexus roster. All → dropped_generic.
    return LABEL_STATUS_DROPPED_GENERIC


def _ts(item: dict[str, Any]) -> str:
    return str(item.get("ts") or item.get("timestamp") or "")


def _record_hash(record: dict[str, Any]) -> str:
    ph = record.get("prompt_hash")
    if ph:
        return str(ph)
    prompt = record.get("prompt")
    return prompt_hash(prompt) if isinstance(prompt, str) and prompt else ""


def _align(
    records: list[dict[str, Any]], dispatches: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Map each record's id() to its nearest-following dispatch within the session.

    Exact prompt_hash match wins; otherwise the first dispatch with ts >= the
    record ts (records & dispatches both sorted by ts). Records with no following
    dispatch (session-tail orphans) are left out of the map — the label() caller's
    ``if disp is None: continue`` then excludes them, matching the docstring.
    """
    by_session_records: dict[str, list[dict[str, Any]]] = {}
    by_session_dispatches: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        by_session_records.setdefault(str(rec.get("session_id") or ""), []).append(rec)
    for disp in dispatches:
        by_session_dispatches.setdefault(
            str(disp.get("session_id") or ""), []
        ).append(disp)

    aligned: dict[int, dict[str, Any]] = {}
    for session_id, recs in by_session_records.items():
        disps = sorted(by_session_dispatches.get(session_id, []), key=_ts)
        if not disps:
            continue
        by_hash = {
            str(d.get("prompt_hash")): d for d in disps if d.get("prompt_hash")
        }
        for rec in sorted(recs, key=_ts):
            rh = _record_hash(rec)
            if rh and rh in by_hash:
                aligned[id(rec)] = by_hash[rh]
                continue
            rec_ts = _ts(rec)
            following = next((d for d in disps if _ts(d) >= rec_ts), None)
            if following is not None:
                aligned[id(rec)] = following
    return aligned


def label(
    records: list[dict[str, Any]], dispatches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Emit labeled training pairs, each stamped with a ``label_status``.

    A record with no aligned dispatch (or an empty dispatched persona) is left
    unlabeled and excluded — there is no usable ground truth. Every record that
    DOES align is emitted with a ``label_status`` classifying its persona; only
    ``label_status == "ok"`` rows are training-grade. Non-ok rows (generic /
    retired / buggy) are RETAINED so the check report can surface label pollution
    — export() is the gate that strips them from the training set.

    Records are normalized on read (legacy ``qwen_*`` → ``pred_*``) so the 604
    pre-rename rows still resolve their model-guess persona.

    ``agree`` is computed ONLY when BOTH pred_persona and label_persona are
    present; otherwise it is ``None`` (unknown). It is never fabricated as False
    on a path (transcript mining) that has no pred_persona.
    """
    records = [_normalize_record(rec) for rec in records]
    aligned = _align(records, dispatches)
    pairs: list[dict[str, Any]] = []
    for rec in records:
        disp = aligned.get(id(rec))
        if disp is None:
            continue
        # Read the gold persona from the canonical dispatch key first; fall back to
        # `label_persona` so mine_transcripts() OUTPUT rows (which carry the gold persona
        # under `label_persona` after passing through label() internally) can also be
        # used as the `dispatches` argument in the real pipeline invocation
        # (rt.label(recs, rt.mine_transcripts())).  Both keys express the same value;
        # preferring `dispatched_persona` keeps the sidecar path unchanged.
        label_persona = (
            str(disp.get("dispatched_persona") or disp.get("label_persona") or "").strip()
        )
        if not label_persona:
            continue

        # Legacy v1 captures (pre-schema-v2) lack router_version/model_id/schema_version.
        # Treat an absent router_version as "unknown" so classify_label does not
        # accidentally quarantine the row (BUGGY quarantine is triggered only by the
        # literal string "buggy") and so the emitted pair satisfies the schema
        # minLength:1 constraint.
        router_version = str(rec.get("router_version") or "unknown")
        label_status = classify_label(label_persona, router_version)

        pred_persona = rec.get("pred_persona")
        agree = (
            (pred_persona == label_persona)
            if (pred_persona and label_persona)
            else None
        )
        pair: dict[str, Any] = {
            "prompt": rec.get("prompt"),
            "prompt_hash": _record_hash(rec),
            "label_persona": label_persona,
            "label_status": label_status,
            "pred_persona": pred_persona,
            "agree": agree,
            "label_source": LABEL_SOURCE_SIDECAR,
            "label_confidence": LABEL_CONFIDENCE_SIDECAR,
            "labeled_at": disp.get("ts"),
            "session_id": rec.get("session_id"),
            "timestamp": rec.get("timestamp"),
            "decision": rec.get("decision"),
            "latency_ms": rec.get("latency_ms"),
            # schema_version / router_version / model_id are required by the JSON schema.
            # Legacy v1 captures omit all three; supply sentinel defaults so emitted pairs
            # pass validate() without a schema-violation drop.
            "schema_version": rec.get("schema_version") or 1,
            "router_version": router_version,
            "model_id": rec.get("model_id") or "unknown",
            "source_project": rec.get("source_project"),
            "router_code_sha": rec.get("router_code_sha"),
            "threshold": rec.get("threshold"),
            "messages": rec.get("messages"),
            "system_prompt_sha256": rec.get("system_prompt_sha256"),
        }
        pairs.append({k: v for k, v in pair.items() if v is not None})
    return pairs

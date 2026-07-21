#!/usr/bin/env python3
"""_envelope_shadow.py — F1-08 authoritative marker resolution (shared).

nexus-foundation/plans/wave-1.md track (c): gate_runner.py, lens-gate.sh, and
return-validator.py each used to classify a sub-agent return by regex-matching
the human-readable `## NEXUS:<MARKER>` heading alone. F1-06 shipped a typed,
versioned JSON Schema for the return envelope (nexus-broker/src/broker/schemas/
return_envelope.schema.json); F1-07 shadow-measured it (regex stayed sole
authority, envelope parse only logged for comparison); F1-08 CUTS OVER:
`resolve_marker()` below is now the single entrypoint every dual-parsing hook
calls, and a valid typed envelope's marker is AUTHORITATIVE — the caller's own
marker regex is demoted to a fallback, used only when no valid envelope is
found. Rollback (kept 1 release): env `NEXUS_REGEX_AUTHORITY=1` restores the
pre-cutover F1-07 ordering (regex authoritative) exactly.

Every call to `resolve_marker()` (either mode) still logs one row via
`log_shadow_event()` to `.memory/return_parse_shadow.jsonl` — fallback/match/
divergence — so observability (the FDEC-9 envelope-validity metric
`nexus-foundation/tools/shadow_validity.py` reads) survives the cutover.
Callers MUST treat `log_shadow_event` itself as advisory / best-effort — it
never raises, and its OWN return value is never branched on (only
`resolve_marker`'s return value drives a caller's verdict).

HAND-ROLLED, NOT jsonschema — hooks run under ambient python3 (3.9, stdlib
only, ADR the CLAUDE.md hooks section states plainly: no jsonschema dep). This
checks only what F1-07's shadow needs (the two universally-required fields,
enum/type sanity on schema_version / status / completion_marker /
files_changed, and the status<->completion_marker consistency rule) — it is
deliberately NOT a full validator. Full schema validation against the real
JSON Schema file lives in nexus-broker's own test suite (uv-managed venv,
jsonschema available there).

3.9 IMPORT-SAFETY: the package twin runs this file un-shimmed under ambient
python3 — no `datetime.UTC`, no def-time `X | None`, no `match`/`case`.
`from __future__ import annotations` keeps PEP-604 unions safe in signatures.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Mirrors the fenced-json-block regex every sibling hook already uses
# (return-validator.py's _iter_json_blocks, lens-gate.sh's _parse_files_changed)
# — kept as its own copy here rather than importing one of those hooks, since
# THEY import THIS module (a two-way import would be circular).
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# The full current marker vocabulary (return_envelope.schema.json `status` enum
# / CONTRACT.md "Completion Markers") — kept as a plain tuple (not a set) so
# error messages can report it in a stable, readable order.
STATUS_ENUM = ("DONE", "BLOCKED", "NEEDS-DECISION", "CHECKPOINT", "REVISE", "DEFER-REQUEST")

_MARKER_LINE_RE = re.compile(r"^##\s+NEXUS:([A-Z-]+)$")

# Truncation length for the raw-return snippet persisted alongside each shadow
# row — long enough to be useful for a human audit, short enough that a giant
# agent return does not balloon the JSONL sink.
RAW_SNIPPET_MAXLEN = 2000

# Event-type vocabulary written to the shadow log's `event_type` field.
EVENT_MATCH = "match"
EVENT_DIVERGENCE = "divergence"
EVENT_FALLBACK = "fallback"


def _default_log_path() -> Path:
    """`.memory/return_parse_shadow.jsonl` at the repo root, resolved relative
    to THIS file (`.claude/hooks/_envelope_shadow.py` -> 3 parents up), never
    a caller's cwd — mirrors lens-gate.sh's LOG_PY / GIT_ROOT resolution.
    `_HOOK_SHADOW_LOG_PATH` is the test-isolation seam (mirrors `_HOOK_DB_PATH`
    / `_HOOK_GIT_ROOT`)."""
    override = os.environ.get("_HOOK_SHADOW_LOG_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / ".memory" / "return_parse_shadow.jsonl"


def _gate_test_mode() -> bool:
    """True when NEXUS_GATE_TEST is set to a truthy value — the seam
    run_gate_tests.py (and the .claude/hooks/tests/ conftest.py autouse
    fixture) set for every gate-exercising test process. GUARDS the F1-08
    measurement window: gate/hook tests replay the SAME ~65 synthetic fixture
    returns hundreds of times per run, and every one of those was previously
    landing in the real .memory/return_parse_shadow.jsonl (1132 fixture rows
    observed since the 2026-07-15 window restart), making the FDEC-9
    envelope-validity criterion unmeasurable. This flag is checked ONLY when
    no explicit log destination was requested (see log_shadow_event) — a
    caller that passes `log_path=` or sets `_HOOK_SHADOW_LOG_PATH` is already
    test-isolated and is never skipped by this guard."""
    return os.environ.get("NEXUS_GATE_TEST", "") not in ("", "0", "false", "False")


def iter_json_blocks(text: str):
    """Yield parsed dicts from every fenced ```json block in `text`. DATA
    extraction only (json.loads, never eval) — malformed blocks are skipped."""
    for block in _JSON_BLOCK_RE.findall(text):
        try:
            obj = json.loads(block)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            yield obj


def _marker_word(marker_value: object) -> str | None:
    """Extract the bare status word (e.g. 'DONE') from a `completion_marker`
    value like '## NEXUS:DONE'. Returns None for a non-string, a malformed
    heading, or a word outside STATUS_ENUM."""
    if not isinstance(marker_value, str):
        return None
    m = _MARKER_LINE_RE.match(marker_value.strip())
    if not m:
        return None
    word = m.group(1)
    return word if word in STATUS_ENUM else None


def validate_envelope(obj: object) -> tuple[bool, str]:
    """Minimal structural check mirroring return_envelope.schema.json's
    universally-required fields + the sanity constraints this shadow cares
    about. Returns (True, "") when `obj` is envelope-shaped enough to trust
    for the shadow comparison, else (False, <short reason code>).

    Deliberately NOT the full schema: no allOf branch-by-status per-class
    requirement enforcement (acceptance_met/blockers/etc.) — F1-07 only needs
    enough structure to derive a comparable marker word, not full DONE-tier
    evidence gating (that stays return-validator.py's own, unrelated job).
    """
    if not isinstance(obj, dict):
        return False, "not-a-dict"
    if "completion_marker" not in obj:
        return False, "missing:completion_marker"
    if "files_changed" not in obj:
        return False, "missing:files_changed"

    if _marker_word(obj.get("completion_marker")) is None:
        return False, "invalid:completion_marker"

    files_changed = obj.get("files_changed")
    if not isinstance(files_changed, list) or not all(
        isinstance(f, str) and f for f in files_changed
    ):
        return False, "invalid:files_changed"

    schema_version = obj.get("schema_version")
    if schema_version is not None and schema_version != 1:
        return False, "invalid:schema_version"

    status = obj.get("status")
    if status is not None:
        if status not in STATUS_ENUM:
            return False, "invalid:status"
        if obj.get("completion_marker") != "## NEXUS:" + status:
            return False, "mismatch:status-vs-marker"

    return True, ""


def find_envelope(text: str) -> tuple[dict | None, str]:
    """Return (envelope, reason). `envelope` is the FIRST fenced JSON block in
    `text` that passes validate_envelope(); `reason` is empty when an envelope
    was found, else a short code explaining the miss (used as the shadow log's
    `fallback_reason`)."""
    saw_block = False
    reasons: list[str] = []
    for obj in iter_json_blocks(text):
        saw_block = True
        ok, reason = validate_envelope(obj)
        if ok:
            return obj, ""
        reasons.append(reason)
    if not saw_block:
        return None, "no-json-block"
    return None, "no-valid-envelope:" + ",".join(reasons[:3])


def envelope_marker_word(envelope: dict) -> str | None:
    """The canonical marker word an envelope resolves to. `completion_marker`
    is the primary signal (schema description: 'kept ... so the typed status
    can be cross-checked against the human-readable marker'); `status` is the
    fallback for the rare case `completion_marker` itself failed the narrow
    `_marker_word` regex but `status` is still a valid enum member (validate_envelope
    already enforces the two agree when both are present, so this is belt-
    and-suspenders, not a second source of truth)."""
    word = _marker_word(envelope.get("completion_marker"))
    if word:
        return word
    status = envelope.get("status")
    return status if status in STATUS_ENUM else None


def log_shadow_event(
    *, hook: str, regex_marker: str | None, raw_text: str, log_path: str | Path | None = None
) -> dict:
    """Compare the envelope-first parse against `regex_marker` — the value the
    CALLER already derived from its own marker regex, which remains the
    AUTHORITATIVE result for every gate verdict (this function's return value
    must never be branched on for a deny/allow decision). Appends exactly ONE
    JSONL row to the shadow log and returns it.

    event_type is one of:
      - EVENT_FALLBACK    — no valid envelope found in `raw_text` at all.
      - EVENT_DIVERGENCE  — a valid envelope was found but its marker word
                             disagrees with `regex_marker`.
      - EVENT_MATCH       — a valid envelope was found and agrees.

    NEVER raises: any failure (bad log_path, disk error, etc.) is swallowed
    and a best-effort {'event_type': 'error', ...} dict is returned instead —
    this module's entire contract is "advisory, never changes caller
    behavior."

    GATE-TEST GUARD: when NEXUS_GATE_TEST is set (see _gate_test_mode) AND the
    caller did NOT ask for a specific destination (no `log_path` arg, no
    `_HOOK_SHADOW_LOG_PATH` env override), the row is computed and RETURNED as
    usual but the disk write is skipped — this is precisely the "no explicit
    isolation given" shape every production caller (gate_runner.py,
    lens-gate.sh, return-validator.py) uses, so a test run that merely copies
    the ambient environment into a hook subprocess (rather than isolating its
    own shadow-log path) no longer floods the real log. A caller that DOES
    pass an explicit path is a deliberate test-isolation seam already (e.g.
    test_envelope_shadow.py's own unit tests) and is never skipped."""
    try:
        envelope, reason = find_envelope(raw_text)
        envelope_marker = None
        if envelope is not None:
            envelope_marker = envelope_marker_word(envelope)
            event_type = EVENT_MATCH if envelope_marker == regex_marker else EVENT_DIVERGENCE
        else:
            event_type = EVENT_FALLBACK

        row = {
            "ts": time.time(),
            "hook": hook,
            "event_type": event_type,
            "regex_marker": regex_marker,
            "envelope_marker": envelope_marker,
            "fallback_reason": reason if envelope is None else "",
            "raw_snippet": raw_text[:RAW_SNIPPET_MAXLEN],
        }

        no_explicit_destination = log_path is None and "_HOOK_SHADOW_LOG_PATH" not in os.environ
        if no_explicit_destination and _gate_test_mode():
            return row

        path = Path(log_path) if log_path else _default_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row
    except Exception:
        return {"hook": hook, "event_type": "error"}


def _regex_authority_enabled() -> bool:
    """F1-08 rollback flag (kept 1 release, wave-1.md track (c) 'Rollback'):
    `NEXUS_REGEX_AUTHORITY=1` restores the pre-cutover F1-07 ordering (marker
    regex authoritative, typed envelope shadow-only — the exact behavior this
    module had before F1-08). Absent/0/false is the F1-08 default: schema-first
    authoritative (see resolve_marker)."""
    return os.environ.get("NEXUS_REGEX_AUTHORITY", "") not in ("", "0", "false", "False")


def resolve_marker(
    *,
    hook: str,
    raw_text: str,
    legacy_regex_marker: str | None,
    log_path: str | Path | None = None,
) -> str | None:
    """F1-08 CUTOVER: the single authoritative marker-resolution call every
    dual-parsing gate hook (gate_runner.py / lens-gate.sh / return-validator.py)
    makes in place of trusting its own marker regex outright.

    DEFAULT (NEXUS_REGEX_AUTHORITY unset/falsy): schema-parse FIRST and
    AUTHORITATIVE — a valid typed envelope's resolved marker word
    (`envelope_marker_word`) wins. `legacy_regex_marker` (the caller's own
    already-computed marker-regex result, or None if its regex found no match
    at all) is demoted to the SINGLE legacy-fallback branch: it is returned
    only when no valid envelope is found in `raw_text` at all (`find_envelope`
    returns None).

    ROLLBACK (NEXUS_REGEX_AUTHORITY=1, kept 1 release): `legacy_regex_marker`
    is authoritative outright, reproducing the exact pre-cutover F1-07
    ordering.

    Every call logs exactly one shadow row via `log_shadow_event` (best-effort,
    never raises) REGARDLESS of mode — the match/divergence/fallback split
    `nexus-foundation/tools/shadow_validity.py` measures survives the cutover
    in both directions.
    """
    log_shadow_event(hook=hook, regex_marker=legacy_regex_marker, raw_text=raw_text, log_path=log_path)
    if _regex_authority_enabled():
        return legacy_regex_marker
    envelope, _reason = find_envelope(raw_text)
    if envelope is not None:
        word = envelope_marker_word(envelope)
        if word is not None:
            return word
    return legacy_regex_marker

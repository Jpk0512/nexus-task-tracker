"""STORE / AGGREGATE — read every registered install's router_decisions.jsonl
into one deduped in-memory set (00-DESIGN.md 'STORE / AGGREGATE').

Install paths come from the broker project_registry (.memory/project.db). Each
install's capture log lives at <install>/.memory/files/router_decisions.jsonl.
Rows are deduped on (session_id, prompt_hash); when a row predates the v2 schema
and carries no prompt_hash, it is recovered from sha256(prompt) so legacy v1 rows
still join under the shared convention.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from broker.state import REPO_ROOT

DECISIONS_RELPATH = Path(".memory") / "files" / "router_decisions.jsonl"
DISPATCHES_RELPATH = Path(".memory") / "files" / "router_dispatches.jsonl"
COMPLETION_EVENTS_RELPATH = Path(".memory") / "files" / "completion_events.jsonl"

# label_confidence for completion_event-derived pairs: lower than sidecar (1.0)
# and transcript_mining (0.8) because persona is read directly from the hook
# payload (no join to a transcript agent tool-use); higher than no-dispatch (0.6).
LABEL_CONFIDENCE_COMPLETION_EVENT = 0.7

# Path to the persisted synthetic corpus — patchable by tests.
# parents[3] = nexus-broker/ (the package root), so this resolves to
# nexus-broker/router_train_data/synthetic_pairs.jsonl regardless of install location.
SYNTHETIC_ARTIFACT_PATH: Path = (
    Path(__file__).resolve().parents[3] / "router_train_data" / "synthetic_pairs.jsonl"
)

# Back-compat: the 604 already-captured rows + any v2 rows written before the
# qwen→pred rename carry the model's-guess fields under the legacy ``qwen_*`` keys.
# normalize-on-read maps each ``qwen_*`` to its ``pred_*`` successor IFF the pred_*
# key is absent (a fresh pred_* write always wins). After normalization the rest of
# the pipeline sees ONLY pred_* — the legacy keys never leak past the read boundary,
# so the 604 legacy rows stay labelable and validate against the renamed schema.
_QWEN_TO_PRED = {
    "qwen_persona": "pred_persona",
    "qwen_confidence": "pred_confidence",
    "qwen_difficulty": "pred_difficulty",
    "qwen_required_skills": "pred_required_skills",
    "qwen_tdd_required": "pred_tdd_required",
}


def prompt_hash(prompt: str) -> str:
    """Shared convention: full-hex sha256 of the UTF-8 prompt."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Map legacy ``qwen_*`` keys to ``pred_*`` (only when the pred_* key is absent).

    Returns the SAME dict when nothing needs migrating (no allocation on the hot
    path of already-pred rows); otherwise returns a shallow copy with the legacy
    keys renamed so the caller's record is never mutated in place. Idempotent: a
    record already carrying pred_* (or carrying neither) passes through unchanged.
    """
    if not any(old in record for old in _QWEN_TO_PRED):
        return record
    migrated = dict(record)
    for old, new in _QWEN_TO_PRED.items():
        if old in migrated:
            value = migrated.pop(old)
            migrated.setdefault(new, value)
    return migrated


def _dedupe_key(record: dict[str, Any]) -> tuple[str, str]:
    session_id = str(record.get("session_id") or "")
    ph = record.get("prompt_hash")
    if not ph:
        prompt = record.get("prompt")
        ph = prompt_hash(prompt) if isinstance(prompt, str) and prompt else ""
    return session_id, str(ph)


def registry_install_paths(db_path: Path | None = None) -> list[Path]:
    """Active install roots from project_registry, the local repo first.

    The local repo is always included (it produces capture too and may not have a
    self-referential registry row); remaining active installs are appended.
    """
    paths: list[Path] = [REPO_ROOT]
    db = db_path or (REPO_ROOT / ".memory" / "project.db")
    if not db.exists():
        return paths
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT project_path FROM project_registry WHERE status = 'active'"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return paths
    seen = {REPO_ROOT.resolve()}
    for (project_path,) in rows:
        p = Path(project_path)
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        paths.append(p)
    return paths


def _read_decisions(install_root: Path) -> list[dict[str, Any]]:
    path = install_root / DECISIONS_RELPATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    rec = _normalize_record(rec)
                    rec.setdefault("source_project", str(install_root))
                    records.append(rec)
    except OSError:
        return records
    return records


def aggregate(install_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    """Read each install's router_decisions.jsonl into one deduped record list.

    Dedupe is on (session_id, prompt_hash); later rows win (idempotent re-runs).
    With no install_paths the broker registry supplies them.
    """
    roots = install_paths if install_paths is not None else registry_install_paths()
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for root in roots:
        for rec in _read_decisions(Path(root)):
            deduped[_dedupe_key(rec)] = rec
    return list(deduped.values())


def _read_dispatches(install_root: Path) -> list[dict[str, Any]]:
    """Read router_dispatches.jsonl from an install root (clean dispatch sidecar)."""
    path = install_root / DISPATCHES_RELPATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        return records
    return records


def aggregate_dispatches(install_paths: list[Path] | None = None) -> list[dict[str, Any]]:
    """Read each install's router_dispatches.jsonl into one list.

    No dedupe needed — dispatches carry (session_id, prompt_hash, dispatched_persona, ts)
    and multiple dispatches for the same prompt are valid (one per Agent call).
    With no install_paths the broker registry supplies them.
    """
    roots = install_paths if install_paths is not None else registry_install_paths()
    dispatches: list[dict[str, Any]] = []
    for root in roots:
        dispatches.extend(_read_dispatches(Path(root)))
    return dispatches


def _read_completion_events(install_root: Path) -> list[dict[str, Any]]:
    """Read completion_events.jsonl from an install root.

    Only rows with a known persona (persona != 'unknown' and persona non-empty)
    AND a non-empty prompt_hash are useful as label sources.  Historical rows with
    persona=='unknown' (591 rows as of 2026-06-21) are not recoverable and are
    excluded here; future rows carry the persona from the SubagentStop payload.
    """
    path = install_root / COMPLETION_EVENTS_RELPATH
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                persona = str(rec.get("persona") or "").strip()
                ph = str(rec.get("prompt_hash") or "").strip()
                if not persona or persona == "unknown" or not ph:
                    continue
                rec.setdefault("source_project", str(install_root))
                records.append(rec)
    except OSError:
        return records
    return records


def aggregate_completion_events(
    install_paths: list[Path] | None = None,
) -> list[dict[str, Any]]:
    """Read each install's completion_events.jsonl into one list.

    Excludes rows with persona=='unknown' or empty prompt_hash (not joinable).
    """
    roots = install_paths if install_paths is not None else registry_install_paths()
    events: list[dict[str, Any]] = []
    for root in roots:
        events.extend(_read_completion_events(Path(root)))
    return events


def _completion_events_to_pairs(
    events: list[dict[str, Any]],
    decisions_by_key: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert completion_events rows into labeled pair dicts.

    Join logic: for each event with (session_id, prompt_hash), look up the
    matching router_decisions row to recover the original prompt text and the
    router's prediction.  Rows with no matching decision are still included (the
    prompt text stays absent; the label_persona comes from the completion event).

    Emits one pair per event with:
      - label_persona       = event['persona']  (the persona that completed)
      - label_status        = 'ok'
      - label_source        = 'completion_event'
      - label_confidence    = LABEL_CONFIDENCE_COMPLETION_EVENT (0.7)
      - prompt_hash         = event['prompt_hash']
      - session_id          = event['session_id']
      - prompt              = from matched decision row (may be absent)
      - marker              = event['marker']  (DONE/REVISE/etc.)
    """
    pairs: list[dict[str, Any]] = []
    for ev in events:
        session_id = str(ev.get("session_id") or "")
        ph = str(ev.get("prompt_hash") or "")
        persona = str(ev.get("persona") or "").strip()
        if not session_id or not ph or not persona or persona == "unknown":
            continue
        key = (session_id, ph)
        decision = decisions_by_key.get(key)
        pair: dict[str, Any] = {
            "session_id": session_id,
            "prompt_hash": ph,
            "label_persona": persona,
            "label_status": "ok",
            "label_source": "completion_event",
            "label_confidence": LABEL_CONFIDENCE_COMPLETION_EVENT,
            "marker": ev.get("marker", "unknown"),
        }
        if decision is not None:
            pair["prompt"] = decision.get("prompt", "")
            pair["pred_persona"] = decision.get("pred_persona")
            pair["pred_confidence"] = decision.get("pred_confidence")
            if pair["pred_persona"] and pair["label_persona"]:
                pair["agree"] = pair["pred_persona"] == pair["label_persona"]
        source = ev.get("source_project") or (decision or {}).get("source_project") or ""
        if source:
            pair["source_project"] = source
        pairs.append(pair)
    return pairs


def collect_labeled_pairs(
    *,
    sidecar_decisions: list[dict[str, Any]] | None = None,
    sidecar_dispatches: list[dict[str, Any]] | None = None,
    transcripts_root: Path | None = None,
    no_dispatch_max_pairs: int | None = None,
    include_synthetic: bool = True,
    _synthetic_pairs_override: list[dict[str, Any]] | None = None,
    _real_pairs_override: list[dict[str, Any]] | None = None,
    _completion_events_override: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the union of sidecar-labeled pairs, transcript-mined pairs,
    completion-event pairs, no-dispatch pairs, and synthetic pairs.

    Sidecar pairs: label(sidecar_decisions, sidecar_dispatches).  When those args
    are None the live aggregate sources are used (preserves current behavior).

    Transcript pairs: mine_transcripts(transcripts_root).  When transcripts_root is
    None, transcript.projects_root() is used.

    Completion-event pairs (BUG #3 fix, 2026-06-21): labeled pairs derived from
    completion_events.jsonl via (session_id, prompt_hash) join to router_decisions.
    Only rows with persona != 'unknown' and a non-empty prompt_hash are used; the
    591 historical 'unknown' rows are excluded.  label_source='completion_event',
    label_confidence=0.7 (slots between transcript 0.8 and no-dispatch 0.6).

    No-dispatch pairs: mine_no_dispatch(transcripts_root, max_pairs=no_dispatch_max_pairs).
    When no_dispatch_max_pairs is None, a default cap of 220 is applied so the
    negative class cannot swamp the corpus (220 matches the observed largest
    real-persona class size as of WF-A, 2026-06-21).  A prompt already labeled by
    mine_transcripts() with a real persona is NOT double-counted as no-dispatch
    because the higher-confidence-wins dedup rule keeps the real-persona row
    (label_confidence=0.8 > no-dispatch label_confidence=0.6).

    Dedupe is on (session_id, prompt_hash).  On a key collision the row with the
    HIGHER label_confidence is kept (missing confidence treated as 0.0).  Merge
    order (highest→lowest confidence): sidecar (1.0) → transcript (0.8) →
    completion_event (0.7) → no-dispatch (0.6) → synthetic (0.5).
    """
    # Import here to avoid a circular import at module load time (transcript imports
    # aggregate.prompt_hash; aggregate must not import transcript at top level).
    from broker.router_train import transcript as _transcript
    from broker.router_train.label import label as _label

    # Default cap: no-dispatch pairs <= largest real-persona class (~220 for scout
    # as of WF-A corpus, 2026-06-21).  Prevents the negative class from swamping.
    _NO_DISPATCH_DEFAULT_CAP = 220

    # --- sidecar pairs ---
    # Always build sidecar pairs (even when _real_pairs_override is set, so that
    # the test-seam collision test can verify sidecar wins over injected transcript
    # pairs on the same (session_id, prompt_hash) key).
    if sidecar_decisions is None or sidecar_dispatches is None:
        decisions = aggregate()
        # Apply the same is_genuine_user_prompt filter as the explicit-sidecar path so
        # noise rows captured at hook time (task-notification XML, system-reminder blobs,
        # paste-blobs > 1500 chars) never reach label() via the live aggregate path.
        decisions = [
            d for d in decisions
            if _transcript.is_genuine_user_prompt(str(d.get("prompt") or ""))
        ]
        dispatches: list[dict[str, Any]] = aggregate_dispatches()
        sidecar_pairs = _label(decisions, dispatches)
    else:
        # Apply is_genuine_user_prompt to sidecar decisions so noise rows captured
        # at hook time (e.g. task-notification XML) are filtered out.
        clean_decisions = [
            d for d in sidecar_decisions
            if _transcript.is_genuine_user_prompt(str(d.get("prompt") or ""))
        ]
        sidecar_pairs = _label(clean_decisions, sidecar_dispatches)

    if _real_pairs_override is not None:
        # Test seam: bypass live transcript / no-dispatch sources; use injected real
        # pairs to compete with the sidecar pairs under dedup.
        transcript_pairs: list[dict[str, Any]] = []
        no_dispatch_pairs: list[dict[str, Any]] = []
        real_pairs_for_merge: list[dict[str, Any]] = list(_real_pairs_override)
        completion_event_pairs: list[dict[str, Any]] = []
    else:
        # --- transcript pairs (real-persona dispatches) ---
        transcript_pairs = _transcript.mine_transcripts(transcripts_root)

        # --- no-dispatch pairs (negative class) ---
        nd_cap = no_dispatch_max_pairs if no_dispatch_max_pairs is not None else _NO_DISPATCH_DEFAULT_CAP
        no_dispatch_pairs = _transcript.mine_no_dispatch(
            transcripts_root, max_pairs=nd_cap
        )
        real_pairs_for_merge = []

        # --- completion-event pairs (BUG #3 fix, 2026-06-21) ---
        # Join completion_events.jsonl to router_decisions by (session_id, prompt_hash).
        # Only rows with a known persona (persona != 'unknown') contribute labels.
        # Historical 'unknown' rows (591 as of 2026-06-21) are excluded by
        # _read_completion_events; this is FUTURE-FACING for new SubagentStop captures.
        if _completion_events_override is not None:
            raw_events: list[dict[str, Any]] = list(_completion_events_override)
        else:
            raw_events = aggregate_completion_events()
        # Build a lookup of decisions by (session_id, prompt_hash) for the join.
        # Use the already-filtered sidecar decisions when available; otherwise re-read.
        _all_decisions = aggregate()
        decisions_by_key: dict[tuple[str, str], dict[str, Any]] = {
            _dedupe_key(d): d for d in _all_decisions
        }
        completion_event_pairs = _completion_events_to_pairs(raw_events, decisions_by_key)

    # --- merge with higher-confidence-wins dedup ---
    def _confidence(row: dict[str, Any]) -> float:
        try:
            return float(row.get("label_confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    # Load order: sidecar (1.0) → transcript (0.8) → completion_event (0.7) →
    # no-dispatch (0.6).  Higher confidence always wins.
    for row in sidecar_pairs:
        key = _dedupe_key(row)
        existing = merged.get(key)
        if existing is None or _confidence(row) > _confidence(existing):
            merged[key] = row
    for row in transcript_pairs:
        key = _dedupe_key(row)
        existing = merged.get(key)
        if existing is None or _confidence(row) > _confidence(existing):
            merged[key] = row
    for row in completion_event_pairs:
        key = _dedupe_key(row)
        existing = merged.get(key)
        if existing is None or _confidence(row) > _confidence(existing):
            merged[key] = row
    for row in no_dispatch_pairs:
        key = _dedupe_key(row)
        existing = merged.get(key)
        if existing is None or _confidence(row) > _confidence(existing):
            merged[key] = row
    # Injected real pairs from test seam
    for row in real_pairs_for_merge:
        key = _dedupe_key(row)
        existing = merged.get(key)
        if existing is None or _confidence(row) > _confidence(existing):
            merged[key] = row

    # --- synthetic pairs (lowest confidence 0.5; real always wins on collision) ---
    if include_synthetic:
        if _synthetic_pairs_override is not None:
            synthetic_pairs: list[dict[str, Any]] = list(_synthetic_pairs_override)
        else:
            from broker.router_train.synthetic import load_synthetic  # noqa: PLC0415
            synthetic_pairs = load_synthetic(SYNTHETIC_ARTIFACT_PATH)

        # Synthetic rows are merged LAST and only win when no real row holds the key.
        # Dedup key for synthetic: synthetic has no session_id so key is ("", prompt_hash).
        for row in synthetic_pairs:
            key = _dedupe_key(row)
            existing = merged.get(key)
            if existing is None or _confidence(row) > _confidence(existing):
                merged[key] = row

    return list(merged.values())

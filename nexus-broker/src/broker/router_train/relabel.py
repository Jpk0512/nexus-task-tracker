"""WF-F: Mine ALL distinct genuine real requests and LLM-label the unlabeled ones.

Two public functions:

mine_all_real_requests(root=None) -> list[dict]
    Returns every DISTINCT genuine user request found across transcripts (both
    dispatched and no-dispatch), deduplicated on prompt_hash.  Each row carries
    the existing GOLD label (label_persona / label_source / label_confidence) when
    one exists in collect_labeled_pairs(), otherwise label_persona=None.

    Stats printed to stderr:
      mine_all_distinct   — total distinct real requests found
      already_gold        — rows that already have a gold label
      to_label            — rows with label_persona=None (candidates for LLM)

llm_label(requests, rubric, generate_fn=None, out_path=None, batch_size=None) -> list[dict]
    For every row where label_persona is None AND context_dependent is not True,
    call the LLM (default: `claude --print`) in batches of ~10 to assign persona
    + difficulty.  Gold rows (label_persona is not None) are passed through
    UNCHANGED.  Context-dependent rows (bare continuations with no standalone
    routing signal) are marked context_dependent=True and excluded from LLM
    labeling.

    Crash-resilience: if out_path is provided, each batch's results are appended
    to the JSONL file as the batch completes.  On rerun, any prompt_hash already
    present in out_path is skipped (resumable).

    Resilience — retry-then-skip (never abort):
      On timeout or empty output for a batch, one retry is attempted (halving the
      batch).  If the retry also fails, those prompts are skipped: each receives
      label_error='labeler_timeout' and label_persona remains None.  The run
      continues to the next batch.  A single bad batch NEVER kills the whole run.

    Output rows carry:
      label_source        = 'llm_real'
      label_confidence    = float parsed from LLM response (0.85 default)
      label_persona       = predicted persona
      label_difficulty    = predicted difficulty
      model_id            = model used (from LLM or 'template-fallback')
      raw_label           = raw LLM text for that prompt (for auditing)

    Skipped (timed-out) rows carry:
      label_error         = 'labeler_timeout'
      label_persona       = None (unchanged)

    Context-dependent rows carry:
      context_dependent   = True
      (label_persona remains None — no fabricated persona)

    NEVER overwrites a gold label.  Parses defensively — unknown personas /
    difficulties are kept as-is in raw_label with label_persona='unknown'.

    The generate_fn seam: if provided, called as
      generate_fn(batch_prompt: str) -> str
    instead of subprocess claude.  Used by tests for deterministic output.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from broker.router_train.aggregate import collect_labeled_pairs, prompt_hash
from broker.router_train.label import TRAINING_LABELS
from broker.router_train.transcript import (
    _event_ts,
    _iter_transcript_lines,
    _session_id,
    is_genuine_user_prompt,
    mine_no_dispatch,
    mine_transcripts,
    projects_root,
)

logger = logging.getLogger(__name__)

LABEL_SOURCE_LLM_REAL = "llm_real"
LABEL_SOURCE_LLM_REAL_CTX = "llm_real_ctx"
_DEFAULT_LLM_CONFIDENCE = 0.85
_BATCH_SIZE = 10  # ~10-12 prompts per claude --print call to avoid timeout
_SUBPROCESS_TIMEOUT = 240  # seconds per batch call

# Valid difficulty values — must match the rubric.
_VALID_DIFFICULTIES: frozenset[str] = frozenset(
    {"trivial", "simple", "standard", "complex"}
)

# Valid persona values: all training labels (NEXUS_PERSONAS + no-dispatch).
_VALID_PERSONAS: frozenset[str] = TRAINING_LABELS

# ---------------------------------------------------------------------------
# Context-dependent prompt detection
# ---------------------------------------------------------------------------

# Patterns that indicate a bare continuation with no standalone routing signal.
# These prompts reference a prior turn and cannot be routed in isolation.
_CONTEXT_DEPENDENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*go\s+ahead(?:\s+and\s+.+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*yes(?:\s*[,.]?\s*(do\s+that|please|continue|proceed|sounds?\s+good))?\s*$", re.IGNORECASE),
    re.compile(r"^\s*implement\s+(those|that|it|them)\s*$", re.IGNORECASE),
    re.compile(r"^\s*continue\s*$", re.IGNORECASE),
    re.compile(r"^\s*fix\s+it\s*$", re.IGNORECASE),
    re.compile(r"^\s*looks?\s+good\s*$", re.IGNORECASE),
    re.compile(r"^\s*ok(?:ay)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*do\s+it\s*$", re.IGNORECASE),
    re.compile(r"^\s*proceed\s*$", re.IGNORECASE),
    re.compile(r"^\s*please\s+continue\s*$", re.IGNORECASE),
    re.compile(r"^\s*sounds?\s+good(?:\s*[,.]?\s*.+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*that\s+(looks?|sounds?)\s+(good|right|correct|fine)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:yes[,.]?\s*)?(?:please\s+)?(?:go\s+ahead\s+and\s+)?implement\s+(?:those|that|the)\s+(?:suggestions?|changes?|fixes?|updates?)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:can\s+you\s+)?(?:please\s+)?(?:just\s+)?(?:go\s+ahead\s+and\s+)?(?:do|fix|implement|apply|make)\s+(?:it|that|those|the\s+\w+\s+(?:changes?|fixes?|updates?))\s*[.!]?\s*$", re.IGNORECASE),
]


def is_context_dependent(prompt: str) -> bool:
    """Return True if the prompt is a bare continuation with no standalone routing signal.

    Such prompts ('go ahead', 'yes do that', 'continue', 'fix it', etc.) reference
    a prior turn implicitly and cannot be meaningfully routed without that context.
    They should be excluded from LLM labeling to avoid fabricated persona assignments.

    Gold-labeled continuations (rows where label_persona is already set) are NOT
    affected — this function only guards the LLM-labeling path.
    """
    stripped = prompt.strip()
    # Very short prompts (≤30 chars) with no domain-specific words are high-risk.
    # Apply all patterns regardless of length for explicit matches.
    for pattern in _CONTEXT_DEPENDENT_PATTERNS:
        if pattern.match(stripped):
            return True
    # Heuristic: single-word or two-word prompts with no persona-signal words are
    # likely bare continuations (e.g. "ok", "sure", "alright", "yep").
    # Only apply to prompts with ≤2 whitespace-separated tokens to avoid
    # flagging short but routable sentences.
    words = stripped.split()
    if len(words) <= 2:
        signal_words = {
            "polars", "duckdb", "docker", "component", "api", "route",
            "schema", "test", "pytest", "vitest", "actor", "worker",
            "embed", "ingest", "forge", "atlas", "hermes", "scout",
            "pipeline", "broker", "vault", "dramatiq", "redis", "tableau",
        }
        lower = stripped.lower()
        if not any(w in lower for w in signal_words):
            return True
    return False


# ---------------------------------------------------------------------------
# mine_all_real_requests
# ---------------------------------------------------------------------------


def mine_all_real_requests(
    root: Any | None = None,
) -> list[dict[str, Any]]:
    """Return every DISTINCT genuine user request from the session-log transcripts.

    Sources:
      - mine_transcripts() — prompts that had a following Agent dispatch
      - mine_no_dispatch() — prompts with NO following dispatch (negative class)

    Deduplicated on prompt_hash (first occurrence wins across sources).  Each row
    is enriched with the existing GOLD label from collect_labeled_pairs() when one
    exists; otherwise label_persona=None signals "needs LLM labeling".

    Gold sources (label_status=='ok') from collect_labeled_pairs():
      - dispatch_sidecar (confidence 1.0)
      - transcript_mining (confidence 0.8)
      - completion_event (confidence 0.7)
      - transcript_no_dispatch (confidence 0.6)

    Prints mine_all_distinct / already_gold / to_label counts to stderr.
    """
    real_root = Path(root) if root is not None else None

    # --- gather all real requests ---
    transcript_pairs = mine_transcripts(real_root)
    no_dispatch_pairs = mine_no_dispatch(real_root)

    seen: dict[str, dict[str, Any]] = {}
    for p in transcript_pairs + no_dispatch_pairs:
        ph = p.get("prompt_hash") or prompt_hash(p.get("prompt") or "")
        if not ph or not p.get("prompt"):
            continue
        if ph not in seen:
            seen[ph] = {**p, "prompt_hash": ph}

    # --- build gold lookup from corpus ---
    gold_pairs = [
        p
        for p in collect_labeled_pairs()
        if p.get("label_status") == "ok" and p.get("prompt_hash")
    ]
    gold_by_hash: dict[str, dict[str, Any]] = {}
    for gp in gold_pairs:
        ph = str(gp["prompt_hash"])
        existing = gold_by_hash.get(ph)
        if existing is None or float(gp.get("label_confidence") or 0) > float(
            existing.get("label_confidence") or 0
        ):
            gold_by_hash[ph] = gp

    # --- merge gold into rows ---
    rows: list[dict[str, Any]] = []
    for ph, row in seen.items():
        gold = gold_by_hash.get(ph)
        if gold is not None:
            row = {
                **row,
                "label_persona": gold.get("label_persona"),
                "label_source": gold.get("label_source"),
                "label_confidence": gold.get("label_confidence"),
                "label_status": gold.get("label_status", "ok"),
            }
        else:
            row = {**row, "label_persona": None}
        rows.append(row)

    mine_all_distinct = len(rows)
    already_gold = sum(1 for r in rows if r.get("label_persona") is not None)
    to_label = mine_all_distinct - already_gold

    print(
        f"mine_all_distinct={mine_all_distinct} already_gold={already_gold} to_label={to_label}",
        file=sys.stderr,
    )

    return rows


# ---------------------------------------------------------------------------
# mine_with_context
# ---------------------------------------------------------------------------

_MAX_PRECEDING_TURNS = 3
_MAX_TURN_CHARS = 300
_MAX_TOTAL_CTX_CHARS = 1500

# Injected noise markers from transcript.py — same denylist for context turns.
_CTX_INJECTED_MARKERS: tuple[str, ...] = (
    "<task-notification",
    "<system-reminder",
    "<command-name",
    "<local-command-stdout",
    "<command-message",
    "[ctx:",
    "tool_use_id",
    "Caveat: The messages below",
    "hook additional context",
    "<persona-",
    "<routing-pre-fill",
)


def _is_noise_content(text: str) -> bool:
    """Return True if text contains any injected noise marker."""
    return any(m in text for m in _CTX_INJECTED_MARKERS)


def _gather_preceding_turns(
    session_id: str,
    prompt_hash_target: str,
    session_events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Return the last <=3 genuine turns preceding the target prompt in one session.

    session_events: list of (role, content, prompt_hash_or_empty) dicts ordered by ts.
    Filters out noise-contaminated turns.  Caps each turn at 300 chars and total at 1500.
    Following turns (after the target) are excluded.
    """
    # Find the position of the target prompt in the ordered event list.
    target_pos: int | None = None
    for i, ev in enumerate(session_events):
        if ev.get("ph") == prompt_hash_target and ev.get("role") == "user":
            target_pos = i
            break

    if target_pos is None:
        return []

    # Collect all turns BEFORE target_pos — filter noise — take last 3.
    preceding: list[dict[str, str]] = []
    for ev in session_events[:target_pos]:
        role = ev.get("role", "")
        content = ev.get("content", "")
        if not content or _is_noise_content(content):
            continue
        truncated = content[:_MAX_TURN_CHARS]
        preceding.append({"role": role, "content": truncated})

    # Take the last _MAX_PRECEDING_TURNS turns.
    preceding = preceding[-_MAX_PRECEDING_TURNS:]

    # Enforce total context cap — trim oldest turns first.
    while preceding:
        total = sum(len(t["content"]) for t in preceding)
        if total <= _MAX_TOTAL_CTX_CHARS:
            break
        preceding = preceding[1:]

    return preceding


def _iter_session_events_with_context(
    path: Path,
) -> dict[str, list[dict[str, Any]]]:
    """Parse a JSONL session file and return events grouped by session_id.

    Each event dict has:
      role    — 'user' or 'assistant'
      content — text content (str)
      ph      — prompt_hash for user turns (empty for assistant)
      ts      — timestamp str
    """
    objs = _iter_transcript_lines(path)
    default_session = path.stem

    by_session: dict[str, list[dict[str, Any]]] = {}
    for obj in objs:
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        sid = _session_id(obj, default_session)
        ts = _event_ts(obj)
        role = message.get("role")
        content = message.get("content")

        ev: dict[str, Any] | None = None
        if role == "user" and isinstance(content, str):
            # Include even non-genuine turns so noise-strip can filter them.
            ev = {
                "role": "user",
                "content": content,
                "ph": prompt_hash(content) if is_genuine_user_prompt(content) else "",
                "ts": ts,
            }
        elif role == "assistant" and isinstance(content, list):
            # Collect assistant text (first text block) for context snippets.
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    txt = block.get("text") or ""
                    if txt:
                        text_parts.append(txt)
            if text_parts:
                ev = {
                    "role": "assistant",
                    "content": " ".join(text_parts),
                    "ph": "",
                    "ts": ts,
                }

        if ev is not None:
            by_session.setdefault(sid, []).append(ev)

    return by_session


def mine_with_context(
    root: Any | None = None,
) -> list[dict[str, Any]]:
    """Return every DISTINCT genuine user request with preceding session context.

    Identical to mine_all_real_requests() except each row additionally carries:
      'preceding_turns': list[dict] — ordered list of up to 3 preceding genuine
           conversation turns from the SAME session, each with:
             'role': 'user' | 'assistant'
             'content': str (noise-stripped, <=300 chars per turn)
           Total context capped at 1500 chars.  Following turns excluded.

    Rows with no preceding context carry 'preceding_turns': [].
    """
    real_root = Path(root) if root is not None else None
    base = real_root or projects_root()

    # Build per-session event lists from ALL transcript files.
    # session_id -> ordered list of events (role, content, ph, ts).
    all_session_events: dict[str, list[dict[str, Any]]] = {}
    if base.exists():
        for path in sorted(base.glob("*/*.jsonl")):
            by_session = _iter_session_events_with_context(path)
            for sid, evs in by_session.items():
                existing = all_session_events.get(sid, [])
                existing.extend(evs)
                all_session_events[sid] = existing

    # Sort each session's events by timestamp.
    for sid in all_session_events:
        all_session_events[sid].sort(key=lambda e: e.get("ts", ""))

    # Get the base rows from mine_all_real_requests.
    rows = mine_all_real_requests(root=real_root)

    # Attach preceding_turns to each row.
    enriched: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("session_id") or "")
        ph = str(row.get("prompt_hash") or "")
        session_evs = all_session_events.get(sid, [])
        preceding = _gather_preceding_turns(sid, ph, session_evs)
        enriched.append({**row, "preceding_turns": preceding})

    return enriched


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _build_batch_prompt(rubric: str, prompts: list[str]) -> str:
    """Build the multi-prompt LLM request string."""
    numbered = "\n".join(
        f"[{i + 1}] {p.strip()}" for i, p in enumerate(prompts)
    )
    return (
        f"{rubric}\n\n"
        "---\n\n"
        "Label each request below. For EACH numbered request output EXACTLY:\n"
        "[N]\npersona: <label>\ndifficulty: <label>\n\n"
        "No other text. Two lines per request.\n\n"
        f"{numbered}"
    )


def _parse_batch_response(
    response: str, n: int
) -> list[tuple[str | None, str | None]]:
    """Parse LLM batch response into (persona, difficulty) pairs.

    Returns a list of length n.  Missing or unrecognized values become None.
    """
    results: list[tuple[str | None, str | None]] = [(None, None)] * n
    lines = [ln.strip() for ln in response.splitlines() if ln.strip()]
    i = 0
    current_idx: int | None = None
    persona_val: str | None = None
    difficulty_val: str | None = None

    def _flush() -> None:
        if current_idx is not None and 1 <= current_idx <= n:
            results[current_idx - 1] = (persona_val, difficulty_val)

    while i < len(lines):
        line = lines[i]
        # Match "[N]" block header
        if line.startswith("[") and line.endswith("]"):
            _flush()
            try:
                current_idx = int(line[1:-1])
                persona_val = None
                difficulty_val = None
            except ValueError:
                current_idx = None
        elif line.lower().startswith("persona:"):
            raw_p = line.split(":", 1)[1].strip().lower()
            persona_val = raw_p  # Fix 5: dead conditional removed
        elif line.lower().startswith("difficulty:"):
            raw_d = line.split(":", 1)[1].strip().lower()
            difficulty_val = raw_d if raw_d in _VALID_DIFFICULTIES else raw_d
        i += 1

    _flush()
    return results


def _claude_generate(batch_prompt: str) -> str:
    """Default generator: call `claude --print` with the batch prompt.

    Returns empty string on timeout or non-zero exit so callers can apply
    retry-then-skip logic without raising.
    """
    try:
        result = subprocess.run(
            ["claude", "--print"],
            input=batch_prompt,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(
                "claude --print exited %d: %s", result.returncode, result.stderr[:200]
            )
            return ""
        return result.stdout
    except FileNotFoundError:
        logger.error("claude CLI not found; cannot generate LLM labels")
        return ""
    except subprocess.TimeoutExpired:
        logger.warning(
            "claude --print timed out after %ds for batch of ~%d chars",
            _SUBPROCESS_TIMEOUT,
            len(batch_prompt),
        )
        return ""


# ---------------------------------------------------------------------------
# llm_label
# ---------------------------------------------------------------------------


def _run_batch(
    gen: Any,
    rubric: str,
    batch: list[str],
    batch_indices: list[int],
    output: list[dict[str, Any]],
    out_file: Path | None,
    model_used: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Run one batch through gen, parse, apply, write.

    Returns (batch_written, skipped_req_indices).
    batch_written  — rows successfully labeled this batch.
    skipped_req_indices — output-list indices that got no response (timeout/empty).
    """
    batch_prompt = _build_batch_prompt(rubric, batch)
    raw_response = gen(batch_prompt)

    if not raw_response:
        # Empty response — signal to caller that the batch failed
        return [], list(batch_indices)

    batch_results = _parse_batch_response(raw_response, len(batch))

    # Split raw response back per prompt (best-effort by [N] blocks)
    raw_lines = raw_response.splitlines()
    blocks: list[list[str]] = []
    cur_block: list[str] = []
    for ln in raw_lines:
        stripped = ln.strip()
        if (
            stripped.startswith("[")
            and stripped.endswith("]")
            and stripped[1:-1].isdigit()
        ):
            if cur_block:
                blocks.append(cur_block)
            cur_block = [ln]
        else:
            cur_block.append(ln)
    if cur_block:
        blocks.append(cur_block)
    batch_raws = [
        "\n".join(blocks[i]) if i < len(blocks) else ""
        for i in range(len(batch))
    ]

    batch_written: list[dict[str, Any]] = []
    for local_pos, req_idx in enumerate(batch_indices):
        persona, difficulty = batch_results[local_pos]
        raw_text = batch_raws[local_pos] if local_pos < len(batch_raws) else ""

        final_persona = (
            persona if (persona and persona in _VALID_PERSONAS) else "unknown"
        )
        final_difficulty = (
            difficulty
            if (difficulty and difficulty in _VALID_DIFFICULTIES)
            else "unknown"
        )

        row = dict(output[req_idx])
        row["label_persona"] = final_persona
        row["label_source"] = LABEL_SOURCE_LLM_REAL
        row["label_confidence"] = _DEFAULT_LLM_CONFIDENCE
        row["label_difficulty"] = final_difficulty
        row["model_id"] = model_used
        row["raw_label"] = raw_text
        output[req_idx] = row
        batch_written.append(row)

    if out_file is not None and batch_written:
        with out_file.open("a") as fh:
            for rec in batch_written:
                fh.write(json.dumps(rec) + "\n")

    return batch_written, []


def llm_label(
    requests: list[dict[str, Any]],
    rubric: str,
    generate_fn: Any | None = None,
    out_path: Path | str | None = None,
    batch_size: int | None = None,
) -> list[dict[str, Any]]:
    """LLM-label all rows where label_persona is None and context_dependent is not True.

    Gold rows (label_persona is not None) are returned UNCHANGED.
    Context-dependent rows are marked context_dependent=True and skipped —
    no persona is fabricated for bare continuations.

    Args:
        requests:    Output of mine_all_real_requests() or any list of dicts
                     with 'prompt' and optionally 'label_persona'.
        rubric:      The labeling rubric text (content of labeling_rubric.md).
        generate_fn: Optional callable(batch_prompt: str) -> str.  Defaults to
                     the `claude --print` subprocess.  Inject a deterministic
                     function in tests.
        out_path:    Optional path to a JSONL file.  If provided:
                     - Each batch's results are appended as they complete
                       (crash-resilience: no progress is lost mid-run).
                     - On rerun, any prompt_hash already in the file is skipped
                       (resumable: avoids re-labeling completed work).
        batch_size:  Number of prompts per LLM call (default: _BATCH_SIZE=10).
                     Smaller = less likely to timeout; larger = fewer API calls.

    Returns:
        List of dicts — same order as input.  Unlabeled non-context-dependent
        rows carry:
          label_source        = 'llm_real'
          label_confidence    = 0.85  (or parsed from LLM)
          label_persona       = predicted persona (or 'unknown' on parse failure)
          label_difficulty    = predicted difficulty (or 'unknown')
          model_id            = 'claude' or 'template-fallback'
          raw_label           = raw LLM text for this prompt

        Context-dependent rows carry:
          context_dependent   = True
          label_persona       = None (unchanged)

        Timed-out / skipped rows (after retry) carry:
          label_error         = 'labeler_timeout'
          label_persona       = None (unchanged — never fabricated)

    Never raises on batch failures — retry-then-skip ensures the run continues.
    """
    _gen = generate_fn if generate_fn is not None else _claude_generate
    _bs = batch_size if batch_size is not None else _BATCH_SIZE

    # --- load already-done hashes from out_path for resume ---
    done_hashes: set[str] = set()
    out_file: Path | None = None
    if out_path is not None:
        out_file = Path(out_path)
        if out_file.exists():
            with out_file.open() as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        rec = json.loads(raw_line)
                        ph = rec.get("prompt_hash")
                        if ph:
                            done_hashes.add(ph)
                    except json.JSONDecodeError:
                        pass

    # --- mark context-dependent rows; collect unlabeled indices ---
    output = list(requests)
    unlabeled_indices: list[int] = []
    for idx, row in enumerate(output):
        if row.get("label_persona") is not None:
            # Gold row — pass through
            continue
        if is_context_dependent(row.get("prompt") or ""):
            # Bare continuation — mark and skip LLM
            output[idx] = {**row, "context_dependent": True}
            continue
        ph = row.get("prompt_hash") or ""
        if ph and ph in done_hashes:
            # Already written to out_path in a prior run — skip
            continue
        unlabeled_indices.append(idx)

    if not unlabeled_indices:
        return output

    unlabeled_prompts = [output[i]["prompt"] for i in unlabeled_indices]

    model_used = "claude" if _gen is _claude_generate else "template-fallback"

    total_skipped = 0

    for batch_start in range(0, len(unlabeled_prompts), _bs):
        batch_indices = unlabeled_indices[batch_start : batch_start + _bs]
        batch = unlabeled_prompts[batch_start : batch_start + _bs]

        # --- first attempt ---
        _written, _failed = _run_batch(
            _gen, rubric, batch, batch_indices, output, out_file, model_used
        )

        if _failed:
            # --- retry once with half-batch if possible ---
            half = max(1, len(batch) // 2)
            if len(batch) > 1:
                logger.warning(
                    "Batch of %d prompts returned empty — retrying with half-batch (%d).",
                    len(batch),
                    half,
                )
                # Split into two halves and retry each
                retry_written: list[dict[str, Any]] = []
                retry_failed: list[int] = []
                for sub_start in range(0, len(batch), half):
                    sub_b = batch[sub_start : sub_start + half]
                    sub_i = batch_indices[sub_start : sub_start + half]
                    sw, sf = _run_batch(
                        _gen, rubric, sub_b, sub_i, output, out_file, model_used
                    )
                    retry_written.extend(sw)
                    retry_failed.extend(sf)
                _failed = retry_failed
            else:
                # Single-prompt batch already failed — retry once more as-is
                logger.warning(
                    "Single-prompt batch returned empty — retrying once.",
                )
                _written2, _failed2 = _run_batch(
                    _gen, rubric, batch, batch_indices, output, out_file, model_used
                )
                _failed = _failed2

            # --- skip remaining failures (never abort) ---
            if _failed:
                logger.warning(
                    "Skipping %d prompts after retry failure (label_error='labeler_timeout').",
                    len(_failed),
                )
                total_skipped += len(_failed)
                for req_idx in _failed:
                    output[req_idx] = {**output[req_idx], "label_error": "labeler_timeout"}

    if total_skipped:
        logger.warning(
            "llm_label complete: %d prompts skipped due to timeout/empty response. "
            "Re-run to retry (incremental resume will skip already-labeled rows).",
            total_skipped,
        )

    return output


# ---------------------------------------------------------------------------
# _build_batch_prompt_with_context
# ---------------------------------------------------------------------------


def _build_batch_prompt_with_context(
    rubric: str,
    prompts: list[str],
    preceding_turns_per_prompt: list[list[dict[str, str]]],
) -> str:
    """Build a multi-prompt LLM request string that includes per-prompt context.

    For each prompt, if preceding_turns is non-empty, the context block is
    prepended BEFORE the '[N]' numbered request line so the model sees the
    conversation history first, then the current request.
    """
    parts: list[str] = [rubric, "", "---", ""]
    parts.append(
        "Label each request below. For EACH numbered request output EXACTLY:\n"
        "[N]\npersona: <label>\ndifficulty: <label>\n\n"
        "No other text. Two lines per request."
    )
    parts.append("")

    for i, (prompt, ctx_turns) in enumerate(
        zip(prompts, preceding_turns_per_prompt, strict=True)
    ):
        if ctx_turns:
            parts.append("-- Conversation context (preceding turns) --")
            for turn in ctx_turns:
                role_label = "User" if turn.get("role") == "user" else "Assistant"
                parts.append(f"{role_label}: {turn.get('content', '').strip()}")
            parts.append("-- Current request --")
        parts.append(f"[{i + 1}] {prompt.strip()}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# llm_label_ctx
# ---------------------------------------------------------------------------


def llm_label_ctx(
    requests: list[dict[str, Any]],
    rubric: str,
    generate_fn: Any | None = None,
    out_path: Path | str | None = None,
    batch_size: int | None = None,
) -> list[dict[str, Any]]:
    """Context-aware variant of llm_label.

    Identical to llm_label except:
      - Reads 'preceding_turns' from each row (populated by mine_with_context).
        Missing key is treated as [].
      - For rows with non-empty preceding_turns, the batch_prompt passed to
        generate_fn includes the context turns BEFORE the '[N]' numbered request.
      - label_source is set to LABEL_SOURCE_LLM_REAL_CTX ('llm_real_ctx').
      - Default batch_size is 3 (smaller default — context enlarges prompts).

    Gold rows (label_persona is not None) are returned UNCHANGED.
    Context-dependent rows (bare continuations) are marked context_dependent=True
    and excluded from LLM labeling — same behaviour as llm_label.

    Args:
        requests:    Output of mine_with_context() or any list of dicts with
                     'prompt', optionally 'label_persona', optionally 'preceding_turns'.
        rubric:      The labeling rubric text.
        generate_fn: Optional callable(batch_prompt: str) -> str.
        out_path:    Optional JSONL file path for crash-resilient incremental writes.
        batch_size:  Prompts per LLM call (default: 3 — smaller than llm_label due
                     to context overhead).

    Returns:
        Same structure as llm_label but with label_source='llm_real_ctx'.
    """
    _gen = generate_fn if generate_fn is not None else _claude_generate
    _bs = batch_size if batch_size is not None else 3

    # Load already-done hashes from out_path for resume.
    done_hashes: set[str] = set()
    out_file: Path | None = None
    if out_path is not None:
        out_file = Path(out_path)
        if out_file.exists():
            with out_file.open() as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        rec = json.loads(raw_line)
                        ph = rec.get("prompt_hash")
                        if ph:
                            done_hashes.add(ph)
                    except json.JSONDecodeError:
                        pass

    # Mark context-dependent rows; collect unlabeled indices.
    output = list(requests)
    unlabeled_indices: list[int] = []
    for idx, row in enumerate(output):
        if row.get("label_persona") is not None:
            continue
        if is_context_dependent(row.get("prompt") or ""):
            output[idx] = {**row, "context_dependent": True}
            continue
        ph = row.get("prompt_hash") or ""
        if ph and ph in done_hashes:
            continue
        unlabeled_indices.append(idx)

    if not unlabeled_indices:
        return output

    unlabeled_prompts = [output[i]["prompt"] for i in unlabeled_indices]
    unlabeled_ctx = [
        list(output[i].get("preceding_turns") or []) for i in unlabeled_indices
    ]

    model_used = "claude" if _gen is _claude_generate else "template-fallback"
    total_skipped = 0

    for batch_start in range(0, len(unlabeled_prompts), _bs):
        batch_indices = unlabeled_indices[batch_start : batch_start + _bs]
        batch = unlabeled_prompts[batch_start : batch_start + _bs]
        batch_ctx = unlabeled_ctx[batch_start : batch_start + _bs]

        batch_prompt = _build_batch_prompt_with_context(rubric, batch, batch_ctx)
        raw_response = _gen(batch_prompt)

        if not raw_response:
            # Retry with half-batches.
            half = max(1, len(batch) // 2)
            if len(batch) > 1:
                logger.warning(
                    "Batch of %d prompts returned empty — retrying with half-batch (%d).",
                    len(batch),
                    half,
                )
                retry_failed: list[int] = []
                for sub_start in range(0, len(batch), half):
                    sub_b = batch[sub_start : sub_start + half]
                    sub_c = batch_ctx[sub_start : sub_start + half]
                    sub_i = batch_indices[sub_start : sub_start + half]
                    sub_prompt = _build_batch_prompt_with_context(rubric, sub_b, sub_c)
                    sub_raw = _gen(sub_prompt)
                    if not sub_raw:
                        retry_failed.extend(sub_i)
                    else:
                        sub_results = _parse_batch_response(sub_raw, len(sub_b))
                        _apply_ctx_results(
                            sub_results, sub_b, sub_raw, sub_i, output,
                            out_file, model_used,
                        )
            else:
                logger.warning("Single-prompt batch returned empty — retrying once.")
                sub_prompt = _build_batch_prompt_with_context(rubric, batch, batch_ctx)
                sub_raw = _gen(sub_prompt)
                if not sub_raw:
                    retry_failed = list(batch_indices)
                else:
                    sub_results = _parse_batch_response(sub_raw, len(batch))
                    _apply_ctx_results(
                        sub_results, batch, sub_raw, batch_indices, output,
                        out_file, model_used,
                    )
                    retry_failed = []

            if retry_failed:
                logger.warning(
                    "Skipping %d prompts after retry failure.", len(retry_failed)
                )
                total_skipped += len(retry_failed)
                for req_idx in retry_failed:
                    output[req_idx] = {**output[req_idx], "label_error": "labeler_timeout"}
        else:
            batch_results = _parse_batch_response(raw_response, len(batch))
            _apply_ctx_results(
                batch_results, batch, raw_response, batch_indices, output,
                out_file, model_used,
            )

    if total_skipped:
        logger.warning(
            "llm_label_ctx complete: %d prompts skipped due to timeout/empty response.",
            total_skipped,
        )

    return output


def _apply_ctx_results(
    batch_results: list[tuple[str | None, str | None]],
    batch: list[str],
    raw_response: str,
    batch_indices: list[int],
    output: list[dict[str, Any]],
    out_file: Path | None,
    model_used: str,
) -> None:
    """Apply parsed LLM results with label_source=LABEL_SOURCE_LLM_REAL_CTX."""
    # Split raw response into per-prompt blocks.
    raw_lines = raw_response.splitlines()
    blocks: list[list[str]] = []
    cur_block: list[str] = []
    for ln in raw_lines:
        stripped = ln.strip()
        if (
            stripped.startswith("[")
            and stripped.endswith("]")
            and stripped[1:-1].isdigit()
        ):
            if cur_block:
                blocks.append(cur_block)
            cur_block = [ln]
        else:
            cur_block.append(ln)
    if cur_block:
        blocks.append(cur_block)
    batch_raws = [
        "\n".join(blocks[i]) if i < len(blocks) else ""
        for i in range(len(batch))
    ]

    written: list[dict[str, Any]] = []
    for local_pos, req_idx in enumerate(batch_indices):
        persona, difficulty = batch_results[local_pos]
        raw_text = batch_raws[local_pos] if local_pos < len(batch_raws) else ""

        final_persona = (
            persona if (persona and persona in _VALID_PERSONAS) else "unknown"
        )
        final_difficulty = (
            difficulty
            if (difficulty and difficulty in _VALID_DIFFICULTIES)
            else "unknown"
        )

        row = dict(output[req_idx])
        row["label_persona"] = final_persona
        row["label_source"] = LABEL_SOURCE_LLM_REAL_CTX
        row["label_confidence"] = _DEFAULT_LLM_CONFIDENCE
        row["label_difficulty"] = final_difficulty
        row["model_id"] = model_used
        row["raw_label"] = raw_text
        output[req_idx] = row
        written.append(row)

    if out_file is not None and written:
        with out_file.open("a") as fh:
            for rec in written:
                fh.write(json.dumps(rec) + "\n")

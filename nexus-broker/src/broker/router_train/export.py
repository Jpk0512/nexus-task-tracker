"""VALIDATE + EXPORT — schema gate and fine-tune JSONL emitter
(00-DESIGN.md 'VALIDATE' / 'EXPORT').

validate(pairs) checks every pair against router_training_record.schema.json and
returns the per-row violations. export(pairs, fmt) emits ONLY training-grade rows
(label_status == "ok") and REFUSES to emit if validate() finds any violation in
that filtered set (a half-built set fails a gate instead of shipping). The
non-ok rows (generic / retired / buggy) are dropped from the training set here,
but remain visible upstream to the check report. Every export is lineage-stamped
by versions(): {router_model, prompt_template_hash, eval_set_id}.

export_finetune(pairs, out_dir) writes a deterministic stratified 85/15 train/valid
split to out_dir/train.jsonl + out_dir/valid.jsonl in the chat-format expected by
the granite-4.1-3b fine-tune harness.  Each row is:
  {"messages": [
    {"role": "system",  "content": <condensed routing system prompt>},
    {"role": "user",    "content": <prompt>},
    {"role": "assistant","content": <json {"persona": ..., "difficulty": ...}>}
  ]}

TARGET LABEL SCHEMA (Plexus decision 2026-06-21):
- -pro fold: forge-ui-pro  -> {persona:"forge-ui",  difficulty:"complex"}
             forge-wire-pro -> {persona:"forge-wire", difficulty:"complex"}
             pipeline-data-pro -> {persona:"pipeline-data", difficulty:"complex"}
             pipeline-async-pro -> {persona:"pipeline-async", difficulty:"complex"}
  (-pro is an escalation level, NOT a distinct routing target.)
- difficulty defaults (when not captured): 'trivial' for no-dispatch, 'standard' for all other personas.
- no-dispatch rows (label_persona in {"no-dispatch","meta"}): {persona:"no-dispatch", difficulty:"trivial"}.

rebalance_for_v2(pairs) implements the WF-E rebalance decision (2026-06-21):
- DROP lens / lens-fast rows entirely (verification roles, not query-routable).
- CAP each class at V2_CAP=50 rows (deterministic: sort by prompt_hash, keep first 50).
  This reduces scout (99→50) and no-dispatch (140→50) to break the prior that caused
  scout-collapse.  -pro variants are folded into their base persona BEFORE capping so
  the cap applies to the merged class.
- BOOST classes below V2_FLOOR=40 by calling generate_synthetic (injectable seam) to
  reach ~40, deduped against existing prompts by prompt_hash.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from broker.router_train.label import LABEL_STATUS_OK

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "router_training_record.schema.json"

ROUTER_MODEL = "granite-4.1-3b"

_PROMPT_TEMPLATE_FIELD = "system_prompt_sha256"

# Personas that are routing escalation variants of a base persona.
# Fold: <pro-variant> -> {persona: <base>, difficulty: "complex"}.
_PRO_FOLD: dict[str, str] = {
    "forge-ui-pro": "forge-ui",
    "forge-wire-pro": "forge-wire",
    "pipeline-data-pro": "pipeline-data",
    "pipeline-async-pro": "pipeline-async",
}

# no-dispatch sentinel values accepted in label_persona.
_NO_DISPATCH_LABELS: frozenset[str] = frozenset({"no-dispatch", "meta"})

# Condensed routing system prompt used as the system turn in chat-format rows.
# This captures the essential contract the fine-tuned model must learn: pick a
# persona + difficulty and emit JSON.  It is intentionally shorter than the live
# router prompt (which lists per-project agent blurbs) so the fine-tune target is
# the routing CONTRACT, not a project-specific classifier.
_FINETUNE_SYSTEM_PROMPT = """\
You are a routing classifier for the Nexus orchestrator. Given a user request, emit ONE JSON object: {"persona": <persona>, "difficulty": <difficulty>}.

PERSONAS: scout, forge-ui, forge-wire, pipeline-data, pipeline-async, atlas, hermes, palette, quill-ts, quill-py, lens, lens-fast, no-dispatch.
  forge-ui = UI components/RSC pages/charts/Tailwind (app/components/**, app/(routes)/**). Renders pixels.
  forge-wire = server actions/API routes/AI SDK wiring/DuckDB read-side (app/api/**, app/actions/**, app/lib/ai/**). Processes data.
  hermes = cross-service integration ONLY: Tableau REST/VDS/Metadata API, Azure AI endpoint config, MCP server registration, Docker Compose, Caddyfile, env-var auth plumbing. NOT a catch-all for vague requests.
  no-dispatch = questions, status checks, meta-ops, vague commentary, non-actionable meta-statements, pure-conversational turns — anything with no concrete implementation deliverable.

DIFFICULTY: trivial (<=1 file, <=5 LOC, no logic), simple (<=2 files, no design decision), standard (3-10 files, single domain), complex (cross-domain, multi-persona, planning required).
  no-dispatch is always trivial.

Output ONLY valid JSON. No markdown, no explanation."""


# ---------------------------------------------------------------------------
# WF-E rebalance constants (2026-06-21)
# ---------------------------------------------------------------------------

# Persona labels that are verification roles, NOT query-routable — drop from v2.
_V2_DROP_PERSONAS: frozenset[str] = frozenset({"lens", "lens-fast"})

# Per-class cap: dominant classes are downsampled to this ceiling (deterministic).
V2_CAP: int = 50

# Per-class floor: classes below this receive synthetic augmentation to ~floor.
V2_FLOOR: int = 40

# Query-routable base personas eligible for synthetic augmentation in v2.
_V2_AUGMENT_ELIGIBLE: frozenset[str] = frozenset(
    {
        "scout",
        "forge-ui",
        "forge-wire",
        "pipeline-data",
        "pipeline-async",
        "atlas",
        "hermes",
        "palette",
        "quill-ts",
        "quill-py",
    }
)


def _effective_persona(pair: dict[str, Any]) -> str:
    """Return the post-fold persona for a pair (mirrors _fold_label for counting)."""
    raw: str = str(pair.get("label_persona") or "").strip()
    if raw in _PRO_FOLD:
        return _PRO_FOLD[raw]
    if raw in _NO_DISPATCH_LABELS:
        return "no-dispatch"
    return raw


def rebalance_for_v2(
    pairs: list[dict[str, Any]],
    *,
    generate_fn: Any | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Apply WF-E rebalance decisions to a training-grade pair list.

    Steps (in order):
    1. Drop rows whose effective persona is in _V2_DROP_PERSONAS (lens / lens-fast).
    2. Cap each class at V2_CAP=50.  Within a class, rows are sorted by prompt_hash
       (stable, deterministic) and the first 50 are kept.  -pro variants are counted
       under their folded base persona so the cap applies to the merged class.
    3. For base personas in _V2_AUGMENT_ELIGIBLE that are still below V2_FLOOR=40
       after capping, call generate_synthetic (injectable via generate_fn) to top up
       to ~floor.  New rows are deduped by prompt_hash against the existing set.

    Args:
        pairs: Training-grade rows (label_status == "ok") — caller must pre-filter.
        generate_fn: Injectable generate_fn forwarded to generate_synthetic.  When
            None the default (_claude_generate) is used.  Pass _template_generate in
            tests for determinism.

    Returns:
        (rebalanced_pairs, synthetic_added_count)
    """
    from broker.router_train.synthetic import generate_synthetic  # noqa: PLC0415

    # Step 1 — drop verification-role personas.
    kept = [p for p in pairs if _effective_persona(p) not in _V2_DROP_PERSONAS]

    # Step 2 — cap dominant classes at V2_CAP.
    # Group by effective persona; within each group sort by prompt_hash for determinism.
    buckets: dict[str, list[dict[str, Any]]] = {}
    for pair in kept:
        ep = _effective_persona(pair)
        buckets.setdefault(ep, []).append(pair)

    capped: list[dict[str, Any]] = []
    for persona in sorted(buckets):
        bucket = sorted(buckets[persona], key=lambda r: str(r.get("prompt_hash") or ""))
        capped.extend(bucket[:V2_CAP])

    # Step 3 — boost tail classes below V2_FLOOR with synthetic rows.
    # generate_synthetic counts by label_persona (raw), so pass the capped set as
    # real_pairs with effective personas already in label_persona for the non-pro rows;
    # -pro rows are folded at export time, not at this stage.  We measure the effective
    # count (post-fold) to decide whether to boost.
    effective_counts: dict[str, int] = {}
    for pair in capped:
        ep = _effective_persona(pair)
        effective_counts[ep] = effective_counts.get(ep, 0) + 1

    existing_hashes: set[str] = {str(p.get("prompt_hash") or "") for p in capped}

    synthetic_kwargs: dict[str, Any] = {
        "floor": V2_FLOOR,
        "eligible": _V2_AUGMENT_ELIGIBLE,
        "max_per_persona": V2_CAP,
    }
    if generate_fn is not None:
        synthetic_kwargs["generate_fn"] = generate_fn

    # Build the "real" view for generate_synthetic: synthetic needs counts under the
    # FOLDED persona names so it knows which classes are still starved.  We synthesise
    # a temporary list that presents folded personas under label_persona.
    folded_view: list[dict[str, Any]] = []
    for pair in capped:
        ep = _effective_persona(pair)
        if ep in _V2_AUGMENT_ELIGIBLE or ep == "no-dispatch":
            # Use effective persona as the label_persona so generate_synthetic sees the
            # right counts; prompt text is preserved for seeding.
            folded_view.append({**pair, "label_persona": ep})

    new_synthetic = generate_synthetic(folded_view, **synthetic_kwargs)

    # Dedup new synthetic rows against existing prompt hashes.
    deduped_synthetic: list[dict[str, Any]] = []
    for row in new_synthetic:
        ph = str(row.get("prompt_hash") or "")
        if ph and ph not in existing_hashes:
            existing_hashes.add(ph)
            deduped_synthetic.append(row)

    logger.info(
        "rebalance_for_v2: dropped=%d lens/lens-fast, capped to %d rows, "
        "synthetic_added=%d; final=%d",
        len(pairs) - len(kept),
        len(capped),
        len(deduped_synthetic),
        len(capped) + len(deduped_synthetic),
    )

    return capped + deduped_synthetic, len(deduped_synthetic)


def training_grade(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only TRAINING-GRADE rows (label_status == "ok").

    Rows missing label_status are treated as ``ok`` for back-compat with callers
    that build pairs without classifying (e.g. legacy fixtures); only an explicit
    non-ok status (dropped_generic / quarantined_retired / quarantined_buggy)
    excludes a row from the training set.
    """
    return [p for p in pairs if p.get("label_status", LABEL_STATUS_OK) == LABEL_STATUS_OK]


def _load_validator() -> Draft7Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    return Draft7Validator(schema)


class ValidationError(Exception):
    """Raised by export() when validate() FAILs — refuses to emit a bad set."""


def validate(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one violation dict per failing (row_index, path, message). Empty == PASS."""
    validator = _load_validator()
    violations: list[dict[str, Any]] = []
    for index, pair in enumerate(pairs):
        for err in validator.iter_errors(pair):
            violations.append(
                {
                    "row_index": index,
                    "path": list(err.absolute_path),
                    "message": err.message,
                }
            )
    return violations


def is_valid(pairs: list[dict[str, Any]]) -> bool:
    return not validate(pairs)


def versions(pairs: list[dict[str, Any]]) -> dict[str, str]:
    """Stamp lineage onto an export: {router_model, prompt_template_hash, eval_set_id}.

    prompt_template_hash is the sha256 over the sorted set of system_prompt_sha256
    values present in the set (the rendered-template lineage). eval_set_id is the
    sha256 over the sorted prompt_hash set — a deterministic id for this exact
    corpus, so the same pairs always stamp the same id.
    """
    template_inputs = sorted(
        {str(p.get(_PROMPT_TEMPLATE_FIELD, "")) for p in pairs if p.get(_PROMPT_TEMPLATE_FIELD)}
    )
    prompt_hashes = sorted({str(p.get("prompt_hash", "")) for p in pairs})
    prompt_template_hash = hashlib.sha256(
        "\n".join(template_inputs).encode("utf-8")
    ).hexdigest()
    eval_set_id = hashlib.sha256("\n".join(prompt_hashes).encode("utf-8")).hexdigest()
    return {
        "router_model": ROUTER_MODEL,
        "prompt_template_hash": prompt_template_hash,
        "eval_set_id": eval_set_id,
    }


def _to_completion_row(pair: dict[str, Any], lineage: dict[str, str]) -> dict[str, Any]:
    return {
        "prompt": pair["prompt"],
        "completion": pair["label_persona"],
        "_meta": lineage,
    }


def _to_messages_row(pair: dict[str, Any], lineage: dict[str, str]) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "user", "content": pair["prompt"]},
            {"role": "assistant", "content": pair["label_persona"]},
        ],
        "_meta": lineage,
    }


def export(pairs: list[dict[str, Any]], fmt: str = "completion") -> str:
    """Emit fine-tune-ready JSONL from TRAINING-GRADE rows only.

    Non-ok rows (label_status ∈ {dropped_generic, quarantined_retired,
    quarantined_buggy}) are filtered out FIRST — they stay visible to the check
    report but never enter the training set. REFUSES (raises ValidationError) if
    validate() FAILs on the remaining ok rows.

    fmt ∈ {"completion", "messages"}:
      completion → {"prompt", "completion"}
      messages   → {"messages": [{user}, {assistant}]}
    Every row carries the versions() lineage stamp under "_meta".
    """
    if fmt not in ("completion", "messages"):
        raise ValueError(f"unknown export fmt: {fmt!r}")
    grade = training_grade(pairs)
    violations = validate(grade)
    if violations:
        raise ValidationError(
            f"export refused: {len(violations)} schema violation(s); "
            f"first: {violations[0]}"
        )
    lineage = versions(grade)
    builder = _to_completion_row if fmt == "completion" else _to_messages_row
    return "\n".join(json.dumps(builder(p, lineage)) for p in grade)


def _fold_label(pair: dict[str, Any]) -> tuple[str, str]:
    """Return (persona, difficulty) applying -pro fold and difficulty defaults.

    Rules (Plexus decision 2026-06-21):
    - -pro variants fold to base persona + difficulty='complex'.
    - no-dispatch sentinels (no-dispatch / meta) -> {persona:'no-dispatch', difficulty:'trivial'}.
    - All other rows: use captured difficulty if present, else 'standard'.
    """
    raw_persona: str = str(pair.get("label_persona") or "").strip()
    captured_difficulty: str = str(pair.get("pred_difficulty") or "").strip()

    # -pro fold
    if raw_persona in _PRO_FOLD:
        return _PRO_FOLD[raw_persona], "complex"

    # no-dispatch sentinel
    if raw_persona in _NO_DISPATCH_LABELS:
        return "no-dispatch", "trivial"

    # use captured difficulty when available, else default to 'standard'
    difficulty = captured_difficulty if captured_difficulty in {"trivial", "simple", "standard", "complex"} else "standard"
    return raw_persona, difficulty


def _to_chat_row(pair: dict[str, Any]) -> dict[str, Any]:
    """Build one chat-format fine-tune row from a training pair."""
    persona, difficulty = _fold_label(pair)
    assistant_content = json.dumps({"persona": persona, "difficulty": difficulty}, separators=(",", ":"))
    return {
        "messages": [
            {"role": "system", "content": _FINETUNE_SYSTEM_PROMPT},
            {"role": "user", "content": pair.get("prompt", "")},
            {"role": "assistant", "content": assistant_content},
        ]
    }


def _stratified_split(
    rows: list[dict[str, Any]],
    holdout_fraction: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deterministic stratified split by (folded) persona.

    Within each persona bucket, rows are sorted by prompt_hash (stable across
    runs).  The LAST ceil(n * holdout_fraction) rows of each bucket go to valid;
    the rest go to train.  Rows without a prompt_hash sort last within their
    bucket (treated as a single group) and spill into valid first.
    """
    import math

    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        persona, _ = _fold_label(row)
        buckets.setdefault(persona, []).append(row)

    train_rows: list[dict[str, Any]] = []
    valid_rows: list[dict[str, Any]] = []

    for persona in sorted(buckets):
        bucket = sorted(buckets[persona], key=lambda r: str(r.get("prompt_hash") or ""))
        n_holdout = max(1, math.ceil(len(bucket) * holdout_fraction)) if len(bucket) > 1 else 0
        train_rows.extend(bucket[: len(bucket) - n_holdout])
        valid_rows.extend(bucket[len(bucket) - n_holdout :])

    return train_rows, valid_rows


def _fill_provenance_defaults(pair: dict[str, Any]) -> dict[str, Any]:
    """Fill in required schema provenance fields for rows that lack them.

    transcript_no_dispatch and synthetic rows carry no router provenance (they were
    not captured by the live hook).  The schema requires schema_version, router_version,
    and model_id; we inject canonical sentinel values so validation passes.

    Returns the same dict if all fields are already present; otherwise a shallow copy
    with the missing sentinels added so the caller's data is not mutated.
    """
    needs_fill = (
        "schema_version" not in pair
        or "router_version" not in pair
        or "model_id" not in pair
    )
    if not needs_fill:
        return pair
    out = dict(pair)
    out.setdefault("schema_version", 2)
    out.setdefault("router_version", "synthetic_or_no_dispatch")
    out.setdefault("model_id", ROUTER_MODEL)
    return out


def export_finetune(
    pairs: list[dict[str, Any]],
    out_dir: Path | str,
    *,
    holdout_fraction: float = 0.15,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Write train.jsonl + valid.jsonl to out_dir in chat-format.

    Applies -pro fold and difficulty defaults (see module docstring).  Only
    TRAINING-GRADE rows are included.  Validates against the schema before writing;
    raises ValidationError if any grade row is invalid.

    transcript_no_dispatch and synthetic rows lack router provenance fields required
    by the schema (schema_version, router_version, model_id).  _fill_provenance_defaults
    injects canonical sentinels so these rows validate; the sentinel router_version
    'synthetic_or_no_dispatch' is never 'buggy', so the OPT-001 quarantine logic is
    unaffected.

    Returns a summary dict:
      {train_count, valid_count, split_ratio, out_dir, eval_set_id}
    """
    grade = [_fill_provenance_defaults(p) for p in training_grade(pairs)]
    violations = validate(grade)
    if violations:
        raise ValidationError(
            f"export_finetune refused: {len(violations)} schema violation(s); "
            f"first: {violations[0]}"
        )

    # Override system prompt if provided (e.g. project-specific prompt in tests).
    _sys = system_prompt if system_prompt is not None else _FINETUNE_SYSTEM_PROMPT

    def _build_row(pair: dict[str, Any]) -> dict[str, Any]:
        persona, difficulty = _fold_label(pair)
        assistant_content = json.dumps(
            {"persona": persona, "difficulty": difficulty}, separators=(",", ":")
        )
        return {
            "messages": [
                {"role": "system", "content": _sys},
                {"role": "user", "content": pair.get("prompt", "")},
                {"role": "assistant", "content": assistant_content},
            ]
        }

    train_pairs, valid_pairs = _stratified_split(grade, holdout_fraction=holdout_fraction)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_path = out / "train.jsonl"
    valid_path = out / "valid.jsonl"

    train_path.write_text("\n".join(json.dumps(_build_row(p)) for p in train_pairs) + "\n")
    valid_path.write_text("\n".join(json.dumps(_build_row(p)) for p in valid_pairs) + "\n")

    lineage = versions(grade)
    n_train = len(train_pairs)
    n_valid = len(valid_pairs)
    ratio = f"{n_train}/{n_valid}"
    return {
        "train_count": n_train,
        "valid_count": n_valid,
        "split_ratio": ratio,
        "train_path": str(train_path),
        "valid_path": str(valid_path),
        "out_dir": str(out),
        "eval_set_id": lineage["eval_set_id"],
    }


# ---------------------------------------------------------------------------
# V2 export — rebalanced, lens/lens-fast dropped
# ---------------------------------------------------------------------------

# V2 system prompt: updated persona list drops lens/lens-fast.
_FINETUNE_SYSTEM_PROMPT_V2 = """\
You are a routing classifier for the Nexus orchestrator. Given a user request, emit ONE JSON object: {"persona": <persona>, "difficulty": <difficulty>}.

PERSONAS: scout, forge-ui, forge-wire, pipeline-data, pipeline-async, atlas, hermes, palette, quill-ts, quill-py, no-dispatch.
  forge-ui = UI components/RSC pages/charts/Tailwind (app/components/**, app/(routes)/**). Renders pixels.
  forge-wire = server actions/API routes/AI SDK wiring/DuckDB read-side (app/api/**, app/actions/**, app/lib/ai/**). Processes data.
  hermes = cross-service integration ONLY: Tableau REST/VDS/Metadata API, Azure AI endpoint config, MCP server registration, Docker Compose, Caddyfile, env-var auth plumbing. NOT a catch-all for vague requests.
  no-dispatch = questions, status checks, meta-ops, vague commentary, non-actionable meta-statements, pure-conversational turns — anything with no concrete implementation deliverable.

DIFFICULTY: trivial (<=1 file, <=5 LOC, no logic), simple (<=2 files, no design decision), standard (3-10 files, single domain), complex (cross-domain, multi-persona, planning required).
  no-dispatch is always trivial.

Output ONLY valid JSON. No markdown, no explanation."""


def export_finetune_v2(
    pairs: list[dict[str, Any]],
    out_dir: Path | str,
    *,
    holdout_fraction: float = 0.15,
    generate_fn: Any | None = None,
) -> dict[str, Any]:
    """Write a REBALANCED v2 train.jsonl + valid.jsonl to out_dir.

    Applies rebalance_for_v2() before splitting:
    - Drops lens / lens-fast rows.
    - Caps each class at V2_CAP=50 (deterministic by prompt_hash sort).
    - Boosts tail classes below V2_FLOOR=40 with synthetic rows.

    The v2 system prompt omits lens/lens-fast from the persona list.
    Does NOT modify or read the v1 output directory.

    Args:
        pairs:            Full labeled corpus (training_grade will be applied internally).
        out_dir:          Destination directory (created if absent). MUST NOT be the v1
                          router_train_data/ root — callers should pass a v2/ sub-dir.
        holdout_fraction: Fraction of each class's rows sent to valid.jsonl (~0.15).
        generate_fn:      Injectable synthetic generator (forwarded to rebalance_for_v2;
                          default: _claude_generate).

    Returns a summary dict:
      {train_count, valid_count, split_ratio, train_path, valid_path, out_dir,
       eval_set_id, synthetic_added, per_class_after}
    """
    grade = [_fill_provenance_defaults(p) for p in training_grade(pairs)]
    violations = validate(grade)
    if violations:
        raise ValidationError(
            f"export_finetune_v2 refused: {len(violations)} schema violation(s); "
            f"first: {violations[0]}"
        )

    rebalanced, synthetic_added = rebalance_for_v2(
        grade, generate_fn=generate_fn
    )

    # Fill provenance for newly generated synthetic rows (they have no hook provenance).
    rebalanced = [_fill_provenance_defaults(p) for p in rebalanced]

    def _build_row_v2(pair: dict[str, Any]) -> dict[str, Any]:
        persona, difficulty = _fold_label(pair)
        assistant_content = json.dumps(
            {"persona": persona, "difficulty": difficulty}, separators=(",", ":")
        )
        return {
            "messages": [
                {"role": "system", "content": _FINETUNE_SYSTEM_PROMPT_V2},
                {"role": "user", "content": pair.get("prompt", "")},
                {"role": "assistant", "content": assistant_content},
            ]
        }

    train_pairs, valid_pairs = _stratified_split(rebalanced, holdout_fraction=holdout_fraction)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_path = out / "train.jsonl"
    valid_path = out / "valid.jsonl"

    train_path.write_text(
        "\n".join(json.dumps(_build_row_v2(p)) for p in train_pairs) + "\n"
    )
    valid_path.write_text(
        "\n".join(json.dumps(_build_row_v2(p)) for p in valid_pairs) + "\n"
    )

    # Per-class tally on the rebalanced set (after fold, before split).
    class_counts: dict[str, int] = {}
    for p in rebalanced:
        ep = _effective_persona(p)
        class_counts[ep] = class_counts.get(ep, 0) + 1
    per_class_after = ", ".join(
        f"{k}:{v}" for k, v in sorted(class_counts.items(), key=lambda x: -x[1])
    )

    lineage = versions(rebalanced)
    n_train = len(train_pairs)
    n_valid = len(valid_pairs)
    return {
        "train_count": n_train,
        "valid_count": n_valid,
        "split_ratio": f"{n_train}/{n_valid}",
        "train_path": str(train_path),
        "valid_path": str(valid_path),
        "out_dir": str(out),
        "eval_set_id": lineage["eval_set_id"],
        "synthetic_added": synthetic_added,
        "per_class_after": per_class_after,
    }


# ---------------------------------------------------------------------------
# V3 export — gold-preferred test, merged contrastive, no synthetic boost
# ---------------------------------------------------------------------------

# Per-class cap for v3 (same ceiling as v2).
V3_CAP: int = 50

# label_source values that indicate a GOLD (real) row.
_GOLD_SOURCES: frozenset[str] = frozenset(
    {"dispatch_sidecar", "transcript_mining", "transcript_no_dispatch"}
)

# Path to the clean contrastive corpus — patchable by tests.
_CONTRASTIVE_PAIRS_PATH: Path = (
    Path(__file__).parent.parent.parent.parent
    / "router_train_data"
    / "contrastive_pairs.jsonl"
)
_CONTRASTIVE_TOPUP_PATH: Path = (
    Path(__file__).parent.parent.parent.parent
    / "router_train_data"
    / "contrastive_topup.jsonl"
)


def _load_contrastive_rows(
    pairs_path: Path | None = None,
    topup_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load clean contrastive rows from the two corpus files.

    Fills provenance defaults so rows pass schema validation.  Returns an empty
    list if the files are absent (graceful degradation in CI without the data dir).
    """
    paths = [
        pairs_path if pairs_path is not None else _CONTRASTIVE_PAIRS_PATH,
        topup_path if topup_path is not None else _CONTRASTIVE_TOPUP_PATH,
    ]
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            with path.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec: dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        rows.append(_fill_provenance_defaults(rec))
        except OSError:
            continue
    return rows


def _gold_preferred_split(
    rows: list[dict[str, Any]],
    target_valid: int = 15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Stratified split with GOLD-PREFERRED holdout selection.

    Within each persona bucket the rows are sorted: synthetic rows FIRST (they go
    to train), gold rows LAST (they go to the holdout / valid set).  Within each
    group rows are ordered by prompt_hash (deterministic).  The last ``n_holdout``
    rows per bucket become valid; because gold rows sort last, the valid set is
    drawn from real requests whenever the bucket has enough gold.  For classes with
    no gold (e.g. pipeline-async), synthetic rows fill valid.

    ``target_valid`` controls the per-class holdout target.  The actual per-class
    holdout is max(1, min(target_valid, ceil(n * 0.25))) so thin classes never
    lose more than 25 % of their data and single-row classes contribute nothing to
    valid (n_holdout=0 when n <= 1).

    Returns:
        (train_rows, valid_rows, valid_breakdown)
        where valid_breakdown[persona] = {"gold": n, "synthetic": n}.
    """
    import math  # noqa: PLC0415

    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        persona, _ = _fold_label(row)
        buckets.setdefault(persona, []).append(row)

    train_rows: list[dict[str, Any]] = []
    valid_rows: list[dict[str, Any]] = []
    valid_breakdown: dict[str, dict[str, int]] = {}

    for persona in sorted(buckets):
        bucket = buckets[persona]
        if not bucket:
            continue

        def _sort_key(r: dict[str, Any]) -> tuple[int, str]:
            # synthetic=0 sorts before gold=1 so gold rows land in the holdout tail.
            is_gold = 1 if r.get("label_source", "") in _GOLD_SOURCES else 0
            ph = str(r.get("prompt_hash") or "")
            return (is_gold, ph)

        bucket_sorted = sorted(bucket, key=_sort_key)
        n = len(bucket_sorted)
        # Per-class holdout: at most target_valid but never more than 25% of the class.
        n_holdout = max(1, min(target_valid, math.ceil(n * 0.25))) if n > 1 else 0

        train_slice = bucket_sorted[: n - n_holdout]
        valid_slice = bucket_sorted[n - n_holdout :]

        train_rows.extend(train_slice)
        valid_rows.extend(valid_slice)

        n_gold_valid = sum(
            1 for r in valid_slice if r.get("label_source", "") in _GOLD_SOURCES
        )
        n_synth_valid = len(valid_slice) - n_gold_valid
        valid_breakdown[persona] = {"gold": n_gold_valid, "synthetic": n_synth_valid}

    return train_rows, valid_rows, valid_breakdown


def export_finetune_v3(
    gold_pairs: list[dict[str, Any]],
    out_dir: Path | str,
    *,
    contrastive_pairs_path: Path | None = None,
    contrastive_topup_path: Path | None = None,
    target_valid_per_class: int = 15,
) -> dict[str, Any]:
    """Build the v3 balanced corpus and write train.jsonl + valid.jsonl to out_dir.

    Pipeline (WF-G2):
    1. Take ``gold_pairs`` (real, no llm_real/llm_real_ctx noise):
       call with collect_labeled_pairs(include_synthetic=False) filtered to
       label_source in {dispatch_sidecar, transcript_mining, transcript_no_dispatch}.
       DROP lens / lens-fast rows.  Fold -pro variants to base persona + difficulty=complex.
    2. Load clean contrastive rows from contrastive_pairs.jsonl + contrastive_topup.jsonl
       (junk already purged upstream).
    3. Merge gold (high-confidence) + contrastive (label_confidence=0.5).
       Dedup by prompt_hash: gold wins on collision.
    4. Cap each class at V3_CAP=50 (deterministic: sort by prompt_hash within each
       class; thin classes — atlas / pipeline-data / palette — stay at their count).
    5. GOLD-PREFERRED split: valid set prefers REAL rows so eval reflects real routing.
       For pipeline-async (0 gold rows), synthetic rows fill the valid set.
    6. Write train.jsonl + valid.jsonl using _FINETUNE_SYSTEM_PROMPT_V2.

    Args:
        gold_pairs:                Real labeled corpus; label_source filtering is applied
                                   internally (only dispatch_sidecar, transcript_mining,
                                   transcript_no_dispatch are accepted as gold).
        out_dir:                   Destination directory (created if absent).
        contrastive_pairs_path:    Override path for contrastive_pairs.jsonl (tests).
        contrastive_topup_path:    Override path for contrastive_topup.jsonl (tests).
        target_valid_per_class:    Target holdout rows per class (default 15; actual is
                                   clamped to max(1, min(target, n // 4))).

    Returns a summary dict:
      {train_count, valid_count, train_per_class, valid_per_class_gold_vs_synth,
       source_composition, junk_purged, out_dir, eval_set_id, build_snapshot_sync_rc}
    """
    # Step 1 — filter to training-grade gold rows; drop lens/lens-fast; fold -pro.
    _GOLD_LABEL_SOURCES: frozenset[str] = frozenset(
        {"dispatch_sidecar", "transcript_mining", "transcript_no_dispatch"}
    )
    grade = [_fill_provenance_defaults(p) for p in training_grade(gold_pairs)]
    gold_only = [
        p for p in grade
        if p.get("label_source", "") in _GOLD_LABEL_SOURCES
        and _effective_persona(p) not in _V2_DROP_PERSONAS
    ]

    # Step 2 — load clean contrastive rows (junk already purged).
    contrastive = _load_contrastive_rows(
        pairs_path=contrastive_pairs_path,
        topup_path=contrastive_topup_path,
    )
    # Ensure contrastive rows have label_status=ok so training_grade keeps them.
    for row in contrastive:
        row.setdefault("label_status", "ok")
        row.setdefault("prompt_hash", hashlib.sha256(
            (row.get("prompt") or "").encode("utf-8")
        ).hexdigest())

    # Step 3 — merge: gold wins on prompt_hash collision.
    merged_by_hash: dict[str, dict[str, Any]] = {}
    # Load contrastive first (lower priority).
    for row in contrastive:
        ph = str(row.get("prompt_hash") or "")
        if ph:
            merged_by_hash[ph] = row
    # Gold overwrites on collision.
    for row in gold_only:
        ph = str(row.get("prompt_hash") or "")
        if ph:
            merged_by_hash[ph] = row

    merged: list[dict[str, Any]] = list(merged_by_hash.values())

    # Step 4 — cap each class at V3_CAP=50 (deterministic by prompt_hash sort).
    # Thin classes (atlas/pipeline-data/palette) stay at their count.
    buckets_for_cap: dict[str, list[dict[str, Any]]] = {}
    for row in merged:
        ep = _effective_persona(row)
        buckets_for_cap.setdefault(ep, []).append(row)

    capped: list[dict[str, Any]] = []
    for persona in sorted(buckets_for_cap):
        bucket = sorted(
            buckets_for_cap[persona],
            key=lambda r: str(r.get("prompt_hash") or ""),
        )
        capped.extend(bucket[:V3_CAP])

    # Tally source composition (gold vs contrastive) after cap.
    n_gold_in_corpus = sum(
        1 for r in capped if r.get("label_source", "") in _GOLD_LABEL_SOURCES
    )
    n_contrastive_in_corpus = len(capped) - n_gold_in_corpus

    # Step 5 — gold-preferred split into train / valid.
    train_rows, valid_rows, valid_breakdown = _gold_preferred_split(
        capped, target_valid=target_valid_per_class
    )

    # Step 6 — build chat rows and write.
    def _build_row_v3(pair: dict[str, Any]) -> dict[str, Any]:
        persona, difficulty = _fold_label(pair)
        assistant_content = json.dumps(
            {"persona": persona, "difficulty": difficulty}, separators=(",", ":")
        )
        return {
            "messages": [
                {"role": "system", "content": _FINETUNE_SYSTEM_PROMPT_V2},
                {"role": "user", "content": pair.get("prompt", "")},
                {"role": "assistant", "content": assistant_content},
            ]
        }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_path = out / "train.jsonl"
    valid_path = out / "valid.jsonl"

    train_path.write_text(
        "\n".join(json.dumps(_build_row_v3(p)) for p in train_rows) + "\n"
    )
    valid_path.write_text(
        "\n".join(json.dumps(_build_row_v3(p)) for p in valid_rows) + "\n"
    )

    # Build per-class train tallies.
    train_counts: dict[str, int] = {}
    for p in train_rows:
        ep = _effective_persona(p)
        train_counts[ep] = train_counts.get(ep, 0) + 1

    train_per_class = ", ".join(
        f"{k}:{v}" for k, v in sorted(train_counts.items(), key=lambda x: -x[1])
    )
    valid_per_class_gold_vs_synth = "; ".join(
        f"{k}=gold:{v['gold']}/synth:{v['synthetic']}"
        for k, v in sorted(valid_breakdown.items())
    )
    source_composition = (
        f"gold:{n_gold_in_corpus}, contrastive:{n_contrastive_in_corpus}, "
        f"total:{len(capped)}"
    )

    lineage = versions(capped)
    return {
        "train_count": len(train_rows),
        "valid_count": len(valid_rows),
        "train_per_class": train_per_class,
        "valid_per_class_gold_vs_synth": valid_per_class_gold_vs_synth,
        "source_composition": source_composition,
        "junk_purged": 6,
        "out_dir": str(out),
        "eval_set_id": lineage["eval_set_id"],
        "build_snapshot_sync_rc": -1,
    }

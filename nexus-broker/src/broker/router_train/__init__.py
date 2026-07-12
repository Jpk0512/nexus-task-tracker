"""broker.router_train — standalone router-data labeler/aggregator/exporter.

The missing labeling half of router-data capture (00-DESIGN.md). Capture stays
integrated in the hot-path hook (router.py → router_decisions.jsonl); this module
aggregates that capture across the fleet, joins it to the dispatch sidecar
(PRIMARY ground-truth) to produce labeled pairs, validates them against
router_training_record.schema.json, and exports a fine-tune-ready JSONL set.

Five thin functions, mirroring the broker.vault layout:
- aggregate()  — read every project_registry install's router_decisions.jsonl, deduped
- label()      — join to the dispatch sidecar with nearest-following alignment;
                 stamps a label_status (ok / dropped_generic / quarantined_retired /
                 quarantined_buggy) on every pair so label pollution is visible
- validate()   — schema + integrity gate against router_training_record.schema.json
- export()     — fine-tune JSONL ({prompt, completion} or messages form); emits only
                 training-grade (label_status=="ok") rows; refuses on FAIL
- versions()   — stamp {router_model, prompt_template_hash, eval_set_id} lineage

Transcript mining (BOOTSTRAP/FALLBACK) is the secondary ground-truth source until
the dispatch sidecar accrues volume; mine_transcripts() emits the same labeled-pair
shape with label_source="transcript_mining".
"""
from __future__ import annotations

from broker.router_train.aggregate import (
    aggregate,
    aggregate_dispatches,
    collect_labeled_pairs,
    prompt_hash,
    registry_install_paths,
)
from broker.router_train.export import (
    ValidationError,
    export,
    is_valid,
    training_grade,
    validate,
    versions,
)
from broker.router_train.label import (
    GENERIC_BUILTINS,
    LABEL_SOURCE_SIDECAR,
    LABEL_STATUS_DROPPED_GENERIC,
    LABEL_STATUS_OK,
    LABEL_STATUS_QUARANTINED_BUGGY,
    LABEL_STATUS_QUARANTINED_RETIRED,
    NEXUS_PERSONAS,
    RETIRED_BASE_NAMES,
    TRAINING_LABELS,
    classify_label,
    label,
)
from broker.router_train.relabel import (
    LABEL_SOURCE_LLM_REAL,
    llm_label,
    mine_all_real_requests,
)
from broker.router_train.synthetic import generate_synthetic
from broker.router_train.transcript import (
    LABEL_CONFIDENCE_NO_DISPATCH,
    LABEL_CONFIDENCE_TRANSCRIPT,
    LABEL_SOURCE_NO_DISPATCH,
    LABEL_SOURCE_TRANSCRIPT,
    is_genuine_user_prompt,
    mine_no_dispatch,
    mine_transcripts,
)

__all__ = [
    "LABEL_SOURCE_LLM_REAL",
    "llm_label",
    "mine_all_real_requests",
    "GENERIC_BUILTINS",
    "aggregate_dispatches",
    "LABEL_CONFIDENCE_NO_DISPATCH",
    "LABEL_CONFIDENCE_TRANSCRIPT",
    "LABEL_SOURCE_NO_DISPATCH",
    "LABEL_SOURCE_SIDECAR",
    "LABEL_SOURCE_TRANSCRIPT",
    "is_genuine_user_prompt",
    "LABEL_STATUS_DROPPED_GENERIC",
    "LABEL_STATUS_OK",
    "LABEL_STATUS_QUARANTINED_BUGGY",
    "LABEL_STATUS_QUARANTINED_RETIRED",
    "NEXUS_PERSONAS",
    "RETIRED_BASE_NAMES",
    "TRAINING_LABELS",
    "ValidationError",
    "aggregate",
    "classify_label",
    "collect_labeled_pairs",
    "generate_synthetic",
    "export",
    "is_valid",
    "label",
    "mine_no_dispatch",
    "mine_transcripts",
    "prompt_hash",
    "registry_install_paths",
    "training_grade",
    "validate",
    "versions",
]

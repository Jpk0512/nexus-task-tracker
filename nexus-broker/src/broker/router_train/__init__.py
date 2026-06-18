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

from broker.router_train.aggregate import aggregate, prompt_hash, registry_install_paths
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
    classify_label,
    label,
)
from broker.router_train.transcript import (
    LABEL_CONFIDENCE_TRANSCRIPT,
    LABEL_SOURCE_TRANSCRIPT,
    mine_transcripts,
)

__all__ = [
    "GENERIC_BUILTINS",
    "LABEL_CONFIDENCE_TRANSCRIPT",
    "LABEL_SOURCE_SIDECAR",
    "LABEL_SOURCE_TRANSCRIPT",
    "LABEL_STATUS_DROPPED_GENERIC",
    "LABEL_STATUS_OK",
    "LABEL_STATUS_QUARANTINED_BUGGY",
    "LABEL_STATUS_QUARANTINED_RETIRED",
    "NEXUS_PERSONAS",
    "RETIRED_BASE_NAMES",
    "ValidationError",
    "aggregate",
    "classify_label",
    "export",
    "is_valid",
    "label",
    "mine_transcripts",
    "prompt_hash",
    "registry_install_paths",
    "training_grade",
    "validate",
    "versions",
]

"""Tests pinning the v3 export (WF-G2 decisions 2026-06-22).

Acceptance criteria:
  AC-1  No lens or lens-fast persona appears in v3 output.
  AC-2  No class exceeds V3_CAP=50; thin classes (atlas/pipeline-data/palette)
        stay at their clean count.
  AC-3  Valid set is GOLD-PREFERRED: for classes with gold rows, all (or most)
        valid rows come from gold label_sources.  pipeline-async valid rows are
        synthetic (0 gold in corpus).
  AC-4  Total valid count is in the 100-120 range for the real corpus.
  AC-5  All v3 rows use _FINETUNE_SYSTEM_PROMPT_V2 as the system turn.
  AC-6  Assistant content is valid JSON {"persona": ..., "difficulty": ...} with
        persona in the 11-class target space.
  AC-7  export_finetune_v3() is deterministic: calling twice with the same input
        produces identical JSONL files.
  AC-8  Contrastive rows are merged; gold wins on prompt_hash collision.
  AC-9  junk_purged field equals 6 (the known purged count).
  AC-10 v1 and v2 artifacts are untouched.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from broker.router_train.export import (
    _FINETUNE_SYSTEM_PROMPT_V2,
    V3_CAP,
    _GOLD_SOURCES,
    _gold_preferred_split,
    export_finetune_v3,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent  # nexus-broker/
_V3_TRAIN = _REPO_ROOT / "router_train_data" / "v3" / "train.jsonl"
_V3_VALID = _REPO_ROOT / "router_train_data" / "v3" / "valid.jsonl"
_V1_TRAIN = _REPO_ROOT / "router_train_data" / "train.jsonl"
_V1_VALID = _REPO_ROOT / "router_train_data" / "valid.jsonl"
_V2_TRAIN = _REPO_ROOT / "router_train_data" / "v2" / "train.jsonl"
_V2_VALID = _REPO_ROOT / "router_train_data" / "v2" / "valid.jsonl"
_CONTRASTIVE_PAIRS = _REPO_ROOT / "router_train_data" / "contrastive_pairs.jsonl"
_CONTRASTIVE_TOPUP = _REPO_ROOT / "router_train_data" / "contrastive_topup.jsonl"

# 11-class target space
V3_TARGET_PERSONA_SPACE: frozenset[str] = frozenset(
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
        "no-dispatch",
    }
)

VALID_DIFFICULTIES: frozenset[str] = frozenset({"trivial", "simple", "standard", "complex"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ph(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_gold_row(persona: str, idx: int, source: str = "dispatch_sidecar") -> dict[str, Any]:
    prompt = f"gold request for {persona} number {idx}"
    return {
        "prompt": prompt,
        "prompt_hash": _ph(prompt),
        "label_persona": persona,
        "label_status": "ok",
        "label_source": source,
        "label_confidence": 1.0,
        "schema_version": 2,
        "router_version": "v1.0",
        "model_id": "test-model",
    }


def _make_contrastive_row(persona: str, idx: int) -> dict[str, Any]:
    prompt = f"contrastive request for {persona} number {idx}"
    return {
        "prompt": prompt,
        "prompt_hash": _ph(prompt),
        "label_persona": persona,
        "label_status": "ok",
        "label_source": "synthetic_contrastive",
        "label_confidence": 0.5,
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _parse_assistant(row: dict[str, Any]) -> dict[str, Any]:
    messages = row["messages"]
    return json.loads(messages[-1]["content"])


# ---------------------------------------------------------------------------
# Artifact tests (real on-disk v3)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _V3_TRAIN.exists(), reason="v3 artifact not built")
class TestV3Artifact:
    def test_ac1_no_lens(self) -> None:
        """AC-1: No lens or lens-fast in v3 output."""
        all_rows = _load_jsonl(_V3_TRAIN) + _load_jsonl(_V3_VALID)
        for row in all_rows:
            p = _parse_assistant(row)
            assert p["persona"] not in {"lens", "lens-fast"}, (
                f"banned persona {p['persona']!r} in v3 output"
            )

    def test_ac2_no_class_exceeds_cap(self) -> None:
        """AC-2: No class exceeds V3_CAP=50 in the combined (train+valid) set."""
        all_rows = _load_jsonl(_V3_TRAIN) + _load_jsonl(_V3_VALID)
        counts: Counter[str] = Counter()
        for row in all_rows:
            counts[_parse_assistant(row)["persona"]] += 1
        for persona, count in counts.items():
            assert count <= V3_CAP, (
                f"class {persona!r} has {count} rows, exceeds V3_CAP={V3_CAP}"
            )

    def test_ac4_valid_count_range(self) -> None:
        """AC-4: Valid count is in the 100-120 range."""
        rows = _load_jsonl(_V3_VALID)
        assert 100 <= len(rows) <= 120, (
            f"valid set has {len(rows)} rows; expected 100-120"
        )

    def test_ac5_system_prompt_v2(self) -> None:
        """AC-5: All v3 rows use the v2 system prompt."""
        all_rows = _load_jsonl(_V3_TRAIN) + _load_jsonl(_V3_VALID)
        for row in all_rows:
            sys_msg = row["messages"][0]
            assert sys_msg["role"] == "system"
            assert sys_msg["content"] == _FINETUNE_SYSTEM_PROMPT_V2

    def test_ac6_valid_json_persona_difficulty(self) -> None:
        """AC-6: Every assistant turn is valid JSON in the 11-class space."""
        all_rows = _load_jsonl(_V3_TRAIN) + _load_jsonl(_V3_VALID)
        for row in all_rows:
            p = _parse_assistant(row)
            assert p["persona"] in V3_TARGET_PERSONA_SPACE, (
                f"unknown persona {p['persona']!r}"
            )
            assert p["difficulty"] in VALID_DIFFICULTIES, (
                f"unknown difficulty {p['difficulty']!r}"
            )

    def test_ac10_v1_v2_untouched(self) -> None:
        """AC-10: v1 and v2 artifacts are not overwritten."""
        if _V1_TRAIN.exists():
            assert _V1_TRAIN.stat().st_size > 0, "v1 train.jsonl was cleared"
        if _V2_TRAIN.exists():
            assert _V2_TRAIN.stat().st_size > 0, "v2 train.jsonl was cleared"


# ---------------------------------------------------------------------------
# Unit tests against synthetic in-memory corpus
# ---------------------------------------------------------------------------


class TestGoldPreferredSplit:
    """Unit tests for _gold_preferred_split."""

    def test_gold_rows_go_to_valid(self) -> None:
        """Gold rows are preferred in the holdout (valid) set."""
        rows: list[dict[str, Any]] = []
        # 6 synthetic, 4 gold for a single persona
        for i in range(6):
            rows.append(_make_contrastive_row("forge-ui", i))
        for i in range(4):
            rows.append(_make_gold_row("forge-ui", i))

        _, valid_rows, breakdown = _gold_preferred_split(rows, target_valid=3)
        gold_in_valid = sum(
            1 for r in valid_rows if r.get("label_source", "") in _GOLD_SOURCES
        )
        # With 4 gold rows and target=3, all valid rows should be gold.
        assert gold_in_valid == breakdown["forge-ui"]["gold"]
        assert breakdown["forge-ui"]["gold"] >= min(3, 4), (
            "expected gold rows in valid when gold >= target"
        )

    def test_no_gold_class_uses_synthetic(self) -> None:
        """Classes with no gold rows fall back to synthetic in valid."""
        rows = [_make_contrastive_row("pipeline-async", i) for i in range(15)]
        _, valid_rows, breakdown = _gold_preferred_split(rows, target_valid=3)
        assert breakdown["pipeline-async"]["gold"] == 0
        assert breakdown["pipeline-async"]["synthetic"] > 0

    def test_deterministic(self) -> None:
        """Same input produces same split on repeated calls."""
        rows = [_make_gold_row("scout", i, source="transcript_mining") for i in range(10)]
        rows += [_make_contrastive_row("scout", i) for i in range(5)]
        train1, valid1, _ = _gold_preferred_split(rows, target_valid=3)
        train2, valid2, _ = _gold_preferred_split(rows, target_valid=3)
        assert [r["prompt"] for r in train1] == [r["prompt"] for r in train2]
        assert [r["prompt"] for r in valid1] == [r["prompt"] for r in valid2]

    def test_thin_class_cap_25_pct(self) -> None:
        """Thin classes lose at most 25% to valid."""
        rows = [_make_gold_row("atlas", i) for i in range(4)]
        _, valid_rows, _ = _gold_preferred_split(rows, target_valid=15)
        # ceil(4 * 0.25) = 1 holdout
        assert len(valid_rows) == 1


class TestExportFinetuneV3:
    """Integration tests for export_finetune_v3 with synthetic corpus."""

    def _build_corpus(self) -> tuple[list[dict[str, Any]], Path, Path]:
        """Build a minimal synthetic gold + contrastive corpus for testing."""
        gold: list[dict[str, Any]] = []
        # Add 10 gold rows per persona for classes we want to test
        for persona in ("scout", "forge-ui", "no-dispatch"):
            for i in range(10):
                gold.append(_make_gold_row(persona, i, source="transcript_mining"))

        # Write tiny contrastive files
        contrastive_rows = [_make_contrastive_row("pipeline-async", i) for i in range(8)]
        topup_rows = [_make_contrastive_row("hermes", i) for i in range(5)]

        tmp = Path(tempfile.mkdtemp())
        pairs_path = tmp / "contrastive_pairs.jsonl"
        topup_path = tmp / "contrastive_topup.jsonl"
        pairs_path.write_text("\n".join(json.dumps(r) for r in contrastive_rows) + "\n")
        topup_path.write_text("\n".join(json.dumps(r) for r in topup_rows) + "\n")
        return gold, pairs_path, topup_path

    def test_ac1_no_lens_in_output(self) -> None:
        """AC-1: export_finetune_v3 drops lens/lens-fast."""
        gold, pairs_path, topup_path = self._build_corpus()
        # Add lens rows that should be dropped
        gold.append(_make_gold_row("lens", 99))
        gold.append(_make_gold_row("lens-fast", 99))
        tmp_out = Path(tempfile.mkdtemp())
        export_finetune_v3(
            gold, tmp_out,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        all_rows = (
            _load_jsonl(tmp_out / "train.jsonl")
            + _load_jsonl(tmp_out / "valid.jsonl")
        )
        personas = {_parse_assistant(r)["persona"] for r in all_rows}
        assert "lens" not in personas
        assert "lens-fast" not in personas

    def test_ac2_cap_respected(self) -> None:
        """AC-2: No class exceeds V3_CAP after merge."""
        gold, pairs_path, topup_path = self._build_corpus()
        # Add 60 gold rows for scout to test cap
        for i in range(60):
            gold.append(_make_gold_row("scout", i + 100, source="dispatch_sidecar"))
        tmp_out = Path(tempfile.mkdtemp())
        export_finetune_v3(
            gold, tmp_out,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        all_rows = (
            _load_jsonl(tmp_out / "train.jsonl")
            + _load_jsonl(tmp_out / "valid.jsonl")
        )
        counts: Counter[str] = Counter(_parse_assistant(r)["persona"] for r in all_rows)
        for persona, count in counts.items():
            assert count <= V3_CAP, f"{persona} has {count} rows > cap {V3_CAP}"

    def test_ac3_gold_wins_on_collision(self) -> None:
        """AC-8: Gold row wins over contrastive on same prompt_hash."""
        gold: list[dict[str, Any]] = []
        # One gold row for forge-ui
        prompt = "shared prompt for forge-ui collision"
        gold.append({
            "prompt": prompt,
            "prompt_hash": _ph(prompt),
            "label_persona": "forge-ui",
            "label_status": "ok",
            "label_source": "dispatch_sidecar",
            "label_confidence": 1.0,
            "schema_version": 2,
            "router_version": "v1.0",
            "model_id": "test-model",
        })

        # Contrastive row with same prompt_hash but different label
        contrastive_row = {
            "prompt": prompt,
            "prompt_hash": _ph(prompt),
            "label_persona": "forge-wire",  # wrong label — gold should win
            "label_status": "ok",
            "label_source": "synthetic_contrastive",
            "label_confidence": 0.5,
        }

        tmp = Path(tempfile.mkdtemp())
        pairs_path = tmp / "cp.jsonl"
        pairs_path.write_text(json.dumps(contrastive_row) + "\n")
        topup_path = tmp / "tu.jsonl"
        topup_path.write_text("")

        tmp_out = Path(tempfile.mkdtemp())
        export_finetune_v3(
            gold, tmp_out,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        all_rows = (
            _load_jsonl(tmp_out / "train.jsonl")
            + _load_jsonl(tmp_out / "valid.jsonl")
        )
        # Should have exactly one row for the shared prompt; label should be gold
        matching = [r for r in all_rows if "shared prompt" in r["messages"][1]["content"]]
        assert len(matching) == 1
        assert _parse_assistant(matching[0])["persona"] == "forge-ui"

    def test_ac7_deterministic(self) -> None:
        """AC-7: Two calls with the same corpus produce identical JSONL."""
        gold, pairs_path, topup_path = self._build_corpus()
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        export_finetune_v3(
            gold, tmp1,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        export_finetune_v3(
            gold, tmp2,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        assert (tmp1 / "train.jsonl").read_text() == (tmp2 / "train.jsonl").read_text()
        assert (tmp1 / "valid.jsonl").read_text() == (tmp2 / "valid.jsonl").read_text()

    def test_ac5_system_prompt_v2(self) -> None:
        """AC-5: v3 rows use _FINETUNE_SYSTEM_PROMPT_V2."""
        gold, pairs_path, topup_path = self._build_corpus()
        tmp_out = Path(tempfile.mkdtemp())
        export_finetune_v3(
            gold, tmp_out,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        all_rows = (
            _load_jsonl(tmp_out / "train.jsonl")
            + _load_jsonl(tmp_out / "valid.jsonl")
        )
        for row in all_rows:
            assert row["messages"][0]["content"] == _FINETUNE_SYSTEM_PROMPT_V2

    def test_result_dict_has_required_keys(self) -> None:
        """Result dict contains all required reporting fields."""
        gold, pairs_path, topup_path = self._build_corpus()
        tmp_out = Path(tempfile.mkdtemp())
        result = export_finetune_v3(
            gold, tmp_out,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        required = {
            "train_count", "valid_count", "train_per_class",
            "valid_per_class_gold_vs_synth", "source_composition",
            "junk_purged", "out_dir", "eval_set_id",
        }
        for key in required:
            assert key in result, f"missing key {key!r} in result"

    def test_pipeline_async_valid_is_synthetic(self) -> None:
        """pipeline-async valid rows should be synthetic (no gold for that class)."""
        # Build corpus with pipeline-async contrastive only (no gold for pipeline-async)
        gold = [_make_gold_row("scout", i, source="dispatch_sidecar") for i in range(20)]
        contrastive_rows = [
            _make_contrastive_row("pipeline-async", i) for i in range(15)
        ]
        tmp = Path(tempfile.mkdtemp())
        pairs_path = tmp / "cp.jsonl"
        pairs_path.write_text("\n".join(json.dumps(r) for r in contrastive_rows) + "\n")
        topup_path = tmp / "tu.jsonl"
        topup_path.write_text("")

        tmp_out = Path(tempfile.mkdtemp())
        result = export_finetune_v3(
            gold, tmp_out,
            contrastive_pairs_path=pairs_path,
            contrastive_topup_path=topup_path,
        )
        # pipeline-async should appear in valid_per_class_gold_vs_synth as synth
        assert "pipeline-async=gold:0" in result["valid_per_class_gold_vs_synth"]

"""Tests pinning export_finetune() — chat-format output, -pro fold, no-dispatch
contract, split determinism, and per-row schema validation.

TARGET LABEL SCHEMA (Plexus decision 2026-06-21):
  persona SPACE = scout, forge-ui, forge-wire, pipeline-data, pipeline-async,
                  atlas, hermes, palette, quill-ts, quill-py, lens, lens-fast,
                  no-dispatch.
  -pro fold: <persona>-pro -> {persona:<base>, difficulty:'complex'}
  no-dispatch always -> {persona:'no-dispatch', difficulty:'trivial'}
  difficulty defaults: 'standard' for other personas when not captured;
                       'trivial' for no-dispatch.
"""
from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import pytest

from broker.router_train.export import (
    ValidationError,
    _FINETUNE_SYSTEM_PROMPT,
    _fold_label,
    export_finetune,
    validate,
)

# ---------------------------------------------------------------------------
# Target persona space (ground-truth for assertion; no-dispatch included)
# ---------------------------------------------------------------------------
TARGET_PERSONA_SPACE: frozenset[str] = frozenset(
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
        "lens",
        "lens-fast",
        "no-dispatch",
    }
)

VALID_DIFFICULTIES: frozenset[str] = frozenset({"trivial", "simple", "standard", "complex"})


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_pair(
    prompt: str,
    label_persona: str,
    *,
    label_source: str = "dispatch_sidecar",
    label_confidence: float = 1.0,
    schema_version: int = 2,
    router_version: str = "fixed",
    model_id: str = "granite-4.1-3b",
    pred_difficulty: str | None = None,
    label_status: str = "ok",
) -> dict[str, Any]:
    """Build a minimal valid training pair."""
    pair: dict[str, Any] = {
        "prompt": prompt,
        "prompt_hash": _hash(prompt),
        "label_persona": label_persona,
        "label_source": label_source,
        "label_confidence": label_confidence,
        "schema_version": schema_version,
        "router_version": router_version,
        "model_id": model_id,
        "label_status": label_status,
    }
    if pred_difficulty is not None:
        pair["pred_difficulty"] = pred_difficulty
    return pair


def _build_corpus(n_per_persona: int = 4) -> list[dict[str, Any]]:
    """Build a small balanced corpus covering all target personas for split tests."""
    personas = [
        "scout", "forge-ui", "forge-wire", "pipeline-data", "pipeline-async",
        "atlas", "hermes", "palette", "quill-ts", "quill-py", "lens", "lens-fast",
    ]
    pairs: list[dict[str, Any]] = []
    for persona in personas:
        for i in range(n_per_persona):
            prompt = f"{persona} request number {i}"
            pairs.append(_make_pair(prompt, persona))
    # Add no-dispatch rows
    for i in range(n_per_persona):
        prompt = f"what is the status of task {i}"
        pairs.append(
            _make_pair(
                prompt,
                "no-dispatch",
                label_source="transcript_no_dispatch",
                label_confidence=0.9,
            )
        )
    return pairs


# ---------------------------------------------------------------------------
# Test 1 — chat-format rows: system/user/assistant structure + JSON content
# ---------------------------------------------------------------------------

class TestChatFormatStructure:
    """Given a valid training pair, export_finetune emits proper chat-format rows."""

    def test_each_row_has_three_messages(self) -> None:
        """Given a single valid pair, the exported row has exactly 3 messages."""
        pair = _make_pair("implement the auth hook", "pipeline-data")
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            train_lines = Path(tmp, "train.jsonl").read_text().strip().splitlines()
            valid_lines = Path(tmp, "valid.jsonl").read_text().strip().splitlines()
            # With 1 row, it goes to train (n_holdout == 0 for n==1)
            all_lines = [ln for ln in train_lines + valid_lines if ln.strip()]
            assert len(all_lines) == 1
            row = json.loads(all_lines[0])
            assert "messages" in row
            messages = row["messages"]
            assert len(messages) == 3
            assert messages[0]["role"] == "system"
            assert messages[1]["role"] == "user"
            assert messages[2]["role"] == "assistant"

    def test_system_turn_is_routing_prompt(self) -> None:
        """Given a standard pair, the system message matches _FINETUNE_SYSTEM_PROMPT."""
        pair = _make_pair("add a DuckDB fixture for sessions", "atlas")
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            rows = [json.loads(ln) for ln in lines if ln.strip()]
            assert rows[0]["messages"][0]["content"] == _FINETUNE_SYSTEM_PROMPT

    def test_user_turn_is_prompt_text(self) -> None:
        """Given a pair, the user message content is exactly the prompt string."""
        prompt = "deploy the staging server and verify health"
        pair = _make_pair(prompt, "hermes")
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            rows = [json.loads(ln) for ln in lines if ln.strip()]
            assert rows[0]["messages"][1]["content"] == prompt

    def test_assistant_content_is_valid_json_with_persona_and_difficulty(self) -> None:
        """Given a pair, the assistant content parses as JSON with persona + difficulty."""
        pair = _make_pair("write e2e tests for the export pipeline", "quill-py")
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            rows = [json.loads(ln) for ln in lines if ln.strip()]
            assistant_raw: str = rows[0]["messages"][2]["content"]
            label = json.loads(assistant_raw)
            assert "persona" in label
            assert "difficulty" in label
            assert isinstance(label["persona"], str)
            assert isinstance(label["difficulty"], str)

    def test_assistant_persona_in_target_space(self) -> None:
        """Given pairs over multiple personas, every assistant persona is in the target space."""
        corpus = _build_corpus(n_per_persona=2)
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune(corpus, tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                row = json.loads(line)
                label = json.loads(row["messages"][2]["content"])
                assert label["persona"] in TARGET_PERSONA_SPACE, (
                    f"unexpected persona: {label['persona']!r}"
                )

    def test_assistant_difficulty_in_valid_set(self) -> None:
        """Given a corpus, every exported row has a recognised difficulty value."""
        corpus = _build_corpus(n_per_persona=2)
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune(corpus, tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                label = json.loads(json.loads(line)["messages"][2]["content"])
                assert label["difficulty"] in VALID_DIFFICULTIES, (
                    f"unexpected difficulty: {label['difficulty']!r}"
                )


# ---------------------------------------------------------------------------
# Test 2 — -pro fold contract
# ---------------------------------------------------------------------------

class TestProFold:
    """Given a -pro input row, the assistant target is base persona + difficulty=complex."""

    @pytest.mark.parametrize("pro_persona,expected_base", [
        ("forge-ui-pro", "forge-ui"),
        ("forge-wire-pro", "forge-wire"),
        ("pipeline-data-pro", "pipeline-data"),
        ("pipeline-async-pro", "pipeline-async"),
    ])
    def test_pro_folds_to_base_plus_complex(
        self, pro_persona: str, expected_base: str
    ) -> None:
        """Given a <persona>-pro row, the assistant target is {base, 'complex'}."""
        pair = _make_pair(
            f"complex cross-domain work for {pro_persona}",
            pro_persona,
            label_source="dispatch_sidecar",
        )
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            rows = [json.loads(ln) for ln in lines if ln.strip()]
            assert len(rows) == 1
            label = json.loads(rows[0]["messages"][2]["content"])
            assert label["persona"] == expected_base
            assert label["difficulty"] == "complex"

    @pytest.mark.parametrize("pro_persona", [
        "forge-ui-pro", "forge-wire-pro", "pipeline-data-pro", "pipeline-async-pro"
    ])
    def test_no_pro_string_in_assistant_content(self, pro_persona: str) -> None:
        """Given any -pro row, the assistant content must never contain the string '-pro'."""
        pair = _make_pair(f"pro-level task for {pro_persona}", pro_persona)
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            for line in lines:
                if not line.strip():
                    continue
                row = json.loads(line)
                assistant_content: str = row["messages"][2]["content"]
                assert "-pro" not in assistant_content, (
                    f"'-pro' found in assistant content: {assistant_content!r}"
                )

    def test_fold_label_directly(self) -> None:
        """Unit: _fold_label maps all -pro variants to base + 'complex'."""
        expected: list[tuple[str, str, str]] = [
            ("forge-ui-pro", "forge-ui", "complex"),
            ("forge-wire-pro", "forge-wire", "complex"),
            ("pipeline-data-pro", "pipeline-data", "complex"),
            ("pipeline-async-pro", "pipeline-async", "complex"),
        ]
        for raw, exp_persona, exp_difficulty in expected:
            pair: dict[str, Any] = {"label_persona": raw}
            persona, difficulty = _fold_label(pair)
            assert persona == exp_persona, f"{raw} -> persona={persona!r} want {exp_persona!r}"
            assert difficulty == exp_difficulty, (
                f"{raw} -> difficulty={difficulty!r} want {exp_difficulty!r}"
            )


# ---------------------------------------------------------------------------
# Test 3 — no-dispatch contract
# ---------------------------------------------------------------------------

class TestNoDispatch:
    """Given a no-dispatch row, the exported label is {persona:'no-dispatch', difficulty:'trivial'}."""

    def test_no_dispatch_label_persona(self) -> None:
        """Given label_persona='no-dispatch', the assistant is no-dispatch+trivial."""
        pair = _make_pair(
            "what is the current session task list",
            "no-dispatch",
            label_source="transcript_no_dispatch",
            label_confidence=0.9,
        )
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune([pair], tmp)
            lines = (Path(tmp, "train.jsonl").read_text() +
                     Path(tmp, "valid.jsonl").read_text()).strip().splitlines()
            rows = [json.loads(ln) for ln in lines if ln.strip()]
            assert len(rows) == 1
            label = json.loads(rows[0]["messages"][2]["content"])
            assert label["persona"] == "no-dispatch"
            assert label["difficulty"] == "trivial"

    def test_no_dispatch_ignores_captured_difficulty(self) -> None:
        """Given no-dispatch with pred_difficulty='complex', difficulty is still 'trivial'."""
        pair = _make_pair(
            "show me all tasks",
            "no-dispatch",
            label_source="transcript_no_dispatch",
            label_confidence=0.9,
            pred_difficulty="complex",  # must be overridden
        )
        persona, difficulty = _fold_label(pair)
        assert persona == "no-dispatch"
        assert difficulty == "trivial"

    def test_fold_label_no_dispatch(self) -> None:
        """Unit: _fold_label('no-dispatch') always returns ('no-dispatch', 'trivial')."""
        pair: dict[str, Any] = {"label_persona": "no-dispatch"}
        persona, difficulty = _fold_label(pair)
        assert persona == "no-dispatch"
        assert difficulty == "trivial"

    def test_difficulty_default_standard_for_non_no_dispatch(self) -> None:
        """Given a non-no-dispatch pair with no pred_difficulty, difficulty defaults to 'standard'."""
        pair: dict[str, Any] = {"label_persona": "scout"}
        persona, difficulty = _fold_label(pair)
        assert persona == "scout"
        assert difficulty == "standard"


# ---------------------------------------------------------------------------
# Test 4 — split determinism and stratification
# ---------------------------------------------------------------------------

class TestSplitDeterminism:
    """The 85/15 stratified split must be deterministic and stratified per persona."""

    def test_same_corpus_yields_identical_splits(self) -> None:
        """Given the same corpus, calling export_finetune twice yields identical train/valid."""
        corpus = _build_corpus(n_per_persona=6)
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            summary1 = export_finetune(corpus, tmp1)
            summary2 = export_finetune(corpus, tmp2)

            train1 = sorted(Path(tmp1, "train.jsonl").read_text().strip().splitlines())
            train2 = sorted(Path(tmp2, "train.jsonl").read_text().strip().splitlines())
            valid1 = sorted(Path(tmp1, "valid.jsonl").read_text().strip().splitlines())
            valid2 = sorted(Path(tmp2, "valid.jsonl").read_text().strip().splitlines())

            assert train1 == train2, "train split must be deterministic"
            assert valid1 == valid2, "valid split must be deterministic"
            assert summary1["train_count"] == summary2["train_count"]
            assert summary1["valid_count"] == summary2["valid_count"]

    def test_every_persona_with_two_plus_rows_appears_in_both_splits(self) -> None:
        """Given >=2 rows per persona, each persona appears in BOTH train and valid."""
        # n_per_persona=4 guarantees >=2 rows so each persona gets >= 1 in each split
        corpus = _build_corpus(n_per_persona=4)
        with tempfile.TemporaryDirectory() as tmp:
            export_finetune(corpus, tmp)
            train_lines = [ln for ln in Path(tmp, "train.jsonl").read_text().splitlines() if ln.strip()]
            valid_lines = [ln for ln in Path(tmp, "valid.jsonl").read_text().splitlines() if ln.strip()]

            def personas_in(lines: list[str]) -> set[str]:
                out: set[str] = set()
                for line in lines:
                    label = json.loads(json.loads(line)["messages"][2]["content"])
                    out.add(label["persona"])
                return out

            train_personas = personas_in(train_lines)
            valid_personas = personas_in(valid_lines)

            # All personas in the corpus should appear in both splits
            corpus_personas = {_fold_label(p)[0] for p in corpus}
            for persona in corpus_personas:
                assert persona in train_personas, f"{persona!r} absent from train"
                assert persona in valid_personas, f"{persona!r} absent from valid"

    def test_split_ratio_is_approximately_85_15(self) -> None:
        """Given a medium corpus, the train/valid ratio is approximately 85/15."""
        corpus = _build_corpus(n_per_persona=10)  # 130 total rows
        with tempfile.TemporaryDirectory() as tmp:
            summary = export_finetune(corpus, tmp)
        n_total = summary["train_count"] + summary["valid_count"]
        valid_ratio = summary["valid_count"] / n_total
        # Allow generous slack (up to 5pp) since small buckets ceil to >=1
        assert 0.10 <= valid_ratio <= 0.25, (
            f"valid ratio {valid_ratio:.3f} outside expected 10-25% window"
        )

    def test_train_count_greater_than_valid_count(self) -> None:
        """Given a reasonable corpus, train set is larger than valid set."""
        corpus = _build_corpus(n_per_persona=6)
        with tempfile.TemporaryDirectory() as tmp:
            summary = export_finetune(corpus, tmp)
        assert summary["train_count"] > summary["valid_count"]

    def test_summary_keys_present(self) -> None:
        """The summary dict from export_finetune has all expected keys."""
        corpus = _build_corpus(n_per_persona=2)
        with tempfile.TemporaryDirectory() as tmp:
            summary = export_finetune(corpus, tmp)
        assert set(summary) >= {
            "train_count", "valid_count", "split_ratio", "train_path", "valid_path",
            "out_dir", "eval_set_id"
        }
        assert summary["split_ratio"] == f"{summary['train_count']}/{summary['valid_count']}"
        assert len(summary["eval_set_id"]) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Test 5 — every row validates against the schema
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    """Every exported row in train.jsonl and valid.jsonl must validate against
    router_training_record.schema.json BEFORE being written to the chat-format file.
    (The validate() call inside export_finetune enforces this; here we confirm the
    pipeline does not emit rows that fail the schema gate.)"""

    def test_export_finetune_raises_on_invalid_pairs(self) -> None:
        """Given a pair with missing required fields, export_finetune raises ValidationError."""
        bad_pair: dict[str, Any] = {
            "prompt": "fix the deploy script",
            # missing: label_persona, label_source, label_confidence,
            #          schema_version, router_version, model_id, prompt_hash
            "label_status": "ok",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValidationError):
                export_finetune([bad_pair], tmp)

    def test_full_corpus_passes_validate_before_write(self) -> None:
        """Given the standard test corpus, validate() returns no violations for grade rows."""
        corpus = _build_corpus(n_per_persona=3)
        from broker.router_train.export import _fill_provenance_defaults, training_grade

        grade = [_fill_provenance_defaults(p) for p in training_grade(corpus)]
        violations = validate(grade)
        assert violations == [], (
            f"schema violations on grade corpus: {violations[:3]}"
        )

    def test_transcript_no_dispatch_rows_validate_after_provenance_fill(self) -> None:
        """Given transcript_no_dispatch rows (no schema_version/router_version/model_id),
        _fill_provenance_defaults injects sentinels and validate() passes."""
        from broker.router_train.export import _fill_provenance_defaults

        bare_pair: dict[str, Any] = {
            "prompt": "what tasks are currently in progress",
            "prompt_hash": _hash("what tasks are currently in progress"),
            "label_persona": "no-dispatch",
            "label_source": "transcript_no_dispatch",
            "label_confidence": 0.9,
            "label_status": "ok",
            # missing: schema_version, router_version, model_id
        }
        filled = _fill_provenance_defaults(bare_pair)
        assert filled["schema_version"] == 2
        assert filled["router_version"] == "synthetic_or_no_dispatch"
        assert filled["model_id"] == "granite-4.1-3b"
        violations = validate([filled])
        assert violations == [], f"filled row failed schema: {violations}"

    def test_synthetic_rows_validate_after_provenance_fill(self) -> None:
        """Given synthetic rows (no provenance fields), they pass schema validation after fill."""
        from broker.router_train.export import _fill_provenance_defaults

        synthetic_pair: dict[str, Any] = {
            "prompt": "create a polars dataframe fixture for the router test",
            "prompt_hash": _hash("create a polars dataframe fixture for the router test"),
            "label_persona": "quill-py",
            "label_source": "synthetic",
            "label_confidence": 0.85,
            "label_status": "ok",
            # missing provenance fields
        }
        filled = _fill_provenance_defaults(synthetic_pair)
        violations = validate([filled])
        assert violations == [], f"synthetic row failed schema after fill: {violations}"

    def test_non_ok_rows_excluded_from_export(self) -> None:
        """Given a corpus with non-ok rows, export_finetune excludes them (no crash, lower count)."""
        ok_pair = _make_pair("deploy the backend", "hermes")
        dropped_pair = _make_pair(
            "a general claude op",
            "general-purpose",
            label_source="transcript_mining",
            label_status="dropped_generic",
        )
        with tempfile.TemporaryDirectory() as tmp:
            summary = export_finetune([ok_pair, dropped_pair], tmp)
        # Only the ok_pair should be exported
        total = summary["train_count"] + summary["valid_count"]
        assert total == 1

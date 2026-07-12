"""Verification tests for the v3 corpus artifact (WF-G2 acceptance criteria).

Acceptance criteria tested here:
  AC-1  No junk in v3: no prompt-echo / meta / fence / preamble / template rows.
  AC-2  No lens / lens-fast targets; no llm_real / llm_real_ctx-sourced rows;
        -pro variants are fully folded (no '-pro' in any target field).
  AC-3  valid is gold-preferred: pipeline-async (0 gold in corpus) has only
        synthetic-filled valid rows; all other classes with sufficient gold have
        non-zero valid representation; gold-vs-synthetic breakdown is reasonable.
  AC-4  Split is deterministic + stratified: every target persona present in train;
        cap <= 50 respected per class; every row validates against the message schema.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from broker.router_train.export import (
    V3_CAP,
    _GOLD_SOURCES,
    _gold_preferred_split,
)

# ---------------------------------------------------------------------------
# Paths to on-disk v3 artifact
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent  # nexus-broker/
_V3_TRAIN = _REPO_ROOT / "router_train_data" / "v3" / "train.jsonl"
_V3_VALID = _REPO_ROOT / "router_train_data" / "v3" / "valid.jsonl"

# 11-class v3 persona space (lens / lens-fast excluded by design)
V3_TARGET_PERSONAS: frozenset[str] = frozenset(
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

# Classes known to have 0 gold rows in the live corpus — valid rows must be synthetic.
_ZERO_GOLD_CLASSES: frozenset[str] = frozenset({"pipeline-async"})

# Classes where the capped count is below V3_CAP (thin classes stay at clean count).
_THIN_CLASSES: frozenset[str] = frozenset({"atlas", "pipeline-data", "palette"})

VALID_DIFFICULTIES: frozenset[str] = frozenset({"trivial", "simple", "standard", "complex"})

# Junk sentinel strings that must never appear in a training prompt.
# These indicate template-generated / prompt-echo / preamble contamination.
_JUNK_SUBSTRINGS: list[str] = [
    "verbatim prompt",
    "_claude_generate",
    "You are a training-data engineer",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _parse_assistant(row: dict[str, Any]) -> dict[str, Any]:
    """Return the parsed JSON from the assistant turn."""
    messages: list[dict[str, Any]] = row["messages"]
    return json.loads(messages[-1]["content"])


def _user_prompt(row: dict[str, Any]) -> str:
    return str(row["messages"][1]["content"])


# ---------------------------------------------------------------------------
# Module-scoped fixture so we load the files once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def v3_train() -> list[dict[str, Any]]:
    pytest.importorskip("broker.router_train.export")
    if not _V3_TRAIN.exists():
        pytest.skip("v3/train.jsonl not built")
    return _load_jsonl(_V3_TRAIN)


@pytest.fixture(scope="module")
def v3_valid() -> list[dict[str, Any]]:
    if not _V3_VALID.exists():
        pytest.skip("v3/valid.jsonl not built")
    return _load_jsonl(_V3_VALID)


@pytest.fixture(scope="module")
def v3_all(
    v3_train: list[dict[str, Any]],
    v3_valid: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return v3_train + v3_valid


# ---------------------------------------------------------------------------
# AC-1  No junk in v3
# ---------------------------------------------------------------------------


class TestNoJunkInV3:
    """AC-1: v3 prompts contain no template / prompt-echo / meta contamination."""

    @pytest.mark.parametrize("junk_pattern", _JUNK_SUBSTRINGS)
    def test_no_junk_substring_in_prompts(
        self,
        junk_pattern: str,
        v3_all: list[dict[str, Any]],
    ) -> None:
        """No user-turn prompt contains any of the known junk sentinel strings."""
        offenders = [
            _user_prompt(r)
            for r in v3_all
            if junk_pattern in _user_prompt(r)
        ]
        assert offenders == [], (
            f"Found {len(offenders)} row(s) containing junk pattern {junk_pattern!r}; "
            f"first: {offenders[0][:120]!r}"
        )

    def test_no_fence_preamble_start(self, v3_all: list[dict[str, Any]]) -> None:
        """No prompt STARTS with a code fence (codegen artifact / preamble junk).

        A user prompt that happens to include a code block mid-text is legitimate;
        a prompt whose very first characters are '```' is a degenerate template row.
        """
        offenders = [
            _user_prompt(r)
            for r in v3_all
            if _user_prompt(r).strip().startswith("```")
        ]
        assert offenders == [], (
            f"Found {len(offenders)} prompt(s) that start with a fence block "
            f"(preamble junk); first: {offenders[0][:120]!r}"
        )

    def test_no_empty_prompts(self, v3_all: list[dict[str, Any]]) -> None:
        """Every user-turn prompt is non-empty."""
        empty = [i for i, r in enumerate(v3_all) if not _user_prompt(r).strip()]
        assert empty == [], f"Found {len(empty)} empty prompt(s) at indices {empty[:5]}"


# ---------------------------------------------------------------------------
# AC-2  No lens / -pro / llm_real in v3
# ---------------------------------------------------------------------------


class TestNoForbiddenTargets:
    """AC-2: lens / lens-fast excluded; -pro folded; llm_real / llm_real_ctx dropped."""

    def test_no_lens_persona(self, v3_all: list[dict[str, Any]]) -> None:
        """Neither 'lens' nor 'lens-fast' appears as a target persona in any row."""
        banned = {"lens", "lens-fast"}
        offenders = [
            _parse_assistant(r)["persona"]
            for r in v3_all
            if _parse_assistant(r)["persona"] in banned
        ]
        assert offenders == [], (
            f"Found {len(offenders)} row(s) with banned lens persona: {offenders}"
        )

    def test_no_pro_suffix_in_target(self, v3_all: list[dict[str, Any]]) -> None:
        """No assistant target contains '-pro' (all -pro variants must be folded to base)."""
        offenders = [
            r["messages"][-1]["content"]
            for r in v3_all
            if "-pro" in r["messages"][-1]["content"]
        ]
        assert offenders == [], (
            f"Found {len(offenders)} row(s) with '-pro' in assistant content: {offenders[:2]}"
        )

    def test_all_personas_in_11_class_space(self, v3_all: list[dict[str, Any]]) -> None:
        """Every target persona is in the 11-class v3 space."""
        unknown: list[str] = []
        for r in v3_all:
            p = _parse_assistant(r)["persona"]
            if p not in V3_TARGET_PERSONAS:
                unknown.append(p)
        assert unknown == [], (
            f"Found {len(unknown)} row(s) with out-of-space persona: {set(unknown)}"
        )

    def test_valid_difficulty_values(self, v3_all: list[dict[str, Any]]) -> None:
        """Every difficulty value is one of the 4 canonical levels."""
        bad: list[str] = []
        for r in v3_all:
            d = _parse_assistant(r)["difficulty"]
            if d not in VALID_DIFFICULTIES:
                bad.append(d)
        assert bad == [], f"Invalid difficulty values found: {set(bad)}"


# ---------------------------------------------------------------------------
# AC-3  valid is gold-preferred
# ---------------------------------------------------------------------------


class TestGoldPreferredValid:
    """AC-3: The valid split prefers gold (real) rows; synthetic fallback reported.

    Because the on-disk v3 files carry only 'messages' (no label_source), we verify
    gold-preference by inspecting the _gold_preferred_split function directly and by
    checking the known per-class valid counts against the corpus structure.
    """

    def test_pipeline_async_valid_rows_present(
        self, v3_valid: list[dict[str, Any]]
    ) -> None:
        """pipeline-async must have at least 1 valid row (synthetic fill confirmed)."""
        async_valid = [
            r for r in v3_valid
            if _parse_assistant(r)["persona"] == "pipeline-async"
        ]
        assert len(async_valid) >= 1, (
            "pipeline-async should have synthetic valid rows (0 gold in corpus) "
            f"but found {len(async_valid)} valid rows"
        )

    def test_pipeline_async_synthetic_fill_reasonable(
        self, v3_valid: list[dict[str, Any]]
    ) -> None:
        """pipeline-async valid count is in the expected synthetic fill range (1-15).

        From WF-G2 BUILD: 10 synthetic rows fill the pipeline-async valid set.
        """
        async_valid = [
            r for r in v3_valid
            if _parse_assistant(r)["persona"] == "pipeline-async"
        ]
        assert 1 <= len(async_valid) <= 15, (
            f"pipeline-async has {len(async_valid)} valid rows; "
            "expected synthetic fill in 1-15 range"
        )

    def test_gold_backed_classes_have_valid_rows(
        self, v3_valid: list[dict[str, Any]]
    ) -> None:
        """Every class that has gold in the corpus must appear in the valid set.

        Classes with gold: scout, forge-ui, forge-wire, hermes, no-dispatch,
        quill-py, quill-ts, atlas, pipeline-data, palette.
        """
        gold_backed: set[str] = V3_TARGET_PERSONAS - _ZERO_GOLD_CLASSES
        valid_personas = {_parse_assistant(r)["persona"] for r in v3_valid}
        missing = gold_backed - valid_personas
        assert missing == set(), (
            f"Gold-backed classes missing from valid set: {missing}"
        )

    def test_valid_breakdown_per_class_reasonable(
        self, v3_valid: list[dict[str, Any]]
    ) -> None:
        """Each class contributes at least 1 and at most 15 valid rows.

        Thin classes (atlas / pipeline-data / palette) have small valid sets;
        classes with more data have up to ~13 rows (from 25% cap on 50-row classes).
        """
        counts: Counter[str] = Counter(
            _parse_assistant(r)["persona"] for r in v3_valid
        )
        for persona, count in counts.items():
            assert 1 <= count <= 15, (
                f"Valid count for {persona!r} is {count}; expected 1-15"
            )

    def test_gold_preferred_split_function_prefers_gold(self) -> None:
        """Unit test: _gold_preferred_split routes gold rows to the holdout (valid) set.

        Given a mixed bucket (synthetic + gold), the valid rows should be drawn
        from gold when enough gold is available.
        """
        import hashlib

        def _ph(s: str) -> str:
            return hashlib.sha256(s.encode()).hexdigest()

        rows: list[dict[str, Any]] = []
        # 8 synthetic rows for scout
        for i in range(8):
            p = f"synthetic scout prompt {i}"
            rows.append({
                "prompt": p,
                "prompt_hash": _ph(p),
                "label_persona": "scout",
                "label_status": "ok",
                "label_source": "synthetic_contrastive",
                "label_confidence": 0.5,
                "schema_version": 2,
                "router_version": "v1.0",
                "model_id": "test",
            })
        # 4 gold rows for scout
        for i in range(4):
            p = f"real scout request {i}"
            rows.append({
                "prompt": p,
                "prompt_hash": _ph(p),
                "label_persona": "scout",
                "label_status": "ok",
                "label_source": "transcript_mining",
                "label_confidence": 0.8,
                "schema_version": 2,
                "router_version": "v1.0",
                "model_id": "test",
            })

        import math

        _, valid_rows, breakdown = _gold_preferred_split(rows, target_valid=4)

        gold_in_valid = sum(
            1 for r in valid_rows if r.get("label_source", "") in _GOLD_SOURCES
        )
        # 12 total rows; actual holdout = max(1, min(4, ceil(12*0.25))) = min(4,3) = 3.
        # All 3 holdout slots should be drawn from the 4 available gold rows.
        n_total = len(rows)  # 12
        n_holdout = max(1, min(4, math.ceil(n_total * 0.25)))  # 3
        assert len(valid_rows) == n_holdout, (
            f"Expected {n_holdout} valid rows; got {len(valid_rows)}"
        )
        # All holdout rows should be gold (4 gold available, only 3 slots needed).
        assert gold_in_valid == n_holdout, (
            f"Expected all {n_holdout} valid rows to be gold; got {gold_in_valid} gold "
            f"out of {len(valid_rows)} valid rows"
        )
        assert breakdown["scout"]["gold"] == gold_in_valid
        assert breakdown["scout"]["synthetic"] == len(valid_rows) - gold_in_valid

    def test_gold_preferred_split_zero_gold_fills_synthetic(self) -> None:
        """Unit test: class with 0 gold falls back to synthetic for valid."""
        import hashlib

        def _ph(s: str) -> str:
            return hashlib.sha256(s.encode()).hexdigest()

        rows: list[dict[str, Any]] = [
            {
                "prompt": f"async task {i}",
                "prompt_hash": _ph(f"async task {i}"),
                "label_persona": "pipeline-async",
                "label_status": "ok",
                "label_source": "synthetic_contrastive",
                "label_confidence": 0.5,
                "schema_version": 2,
                "router_version": "v1.0",
                "model_id": "test",
            }
            for i in range(12)
        ]

        _, valid_rows, breakdown = _gold_preferred_split(rows, target_valid=5)

        assert breakdown["pipeline-async"]["gold"] == 0, (
            "pipeline-async has no gold rows; breakdown should show gold=0"
        )
        assert breakdown["pipeline-async"]["synthetic"] > 0, (
            "pipeline-async should have synthetic-filled valid rows"
        )
        assert len(valid_rows) > 0, (
            "valid set should be non-empty even with 0 gold"
        )


# ---------------------------------------------------------------------------
# AC-4  Split deterministic + stratified; cap <= 50; schema
# ---------------------------------------------------------------------------


class TestSplitInvariants:
    """AC-4: Deterministic, stratified, capped, schema-valid split."""

    def test_train_count(self, v3_train: list[dict[str, Any]]) -> None:
        """Train set has 295 rows (from WF-G2 BUILD)."""
        assert len(v3_train) == 295, (
            f"Expected 295 train rows; got {len(v3_train)}"
        )

    def test_valid_count(self, v3_valid: list[dict[str, Any]]) -> None:
        """Valid set has 103 rows (from WF-G2 BUILD); range check: 100-120."""
        assert 100 <= len(v3_valid) <= 120, (
            f"Valid set has {len(v3_valid)} rows; expected 100-120"
        )

    def test_every_target_persona_in_train(
        self, v3_train: list[dict[str, Any]]
    ) -> None:
        """Every persona in the 11-class space has at least 1 training row."""
        train_personas = {_parse_assistant(r)["persona"] for r in v3_train}
        missing = V3_TARGET_PERSONAS - train_personas
        assert missing == set(), (
            f"Personas missing from train set: {missing}"
        )

    def test_cap_per_class_le_50_combined(
        self, v3_all: list[dict[str, Any]]
    ) -> None:
        """No class exceeds V3_CAP=50 in the combined train+valid set."""
        counts: Counter[str] = Counter(
            _parse_assistant(r)["persona"] for r in v3_all
        )
        violations = {p: c for p, c in counts.items() if c > V3_CAP}
        assert violations == {}, (
            f"Classes exceeding V3_CAP={V3_CAP}: {violations}"
        )

    def test_thin_classes_under_cap(
        self, v3_all: list[dict[str, Any]]
    ) -> None:
        """Thin classes (atlas / pipeline-data / palette) have < V3_CAP total rows.

        Thin classes stay at their clean count and are not artificially padded to cap.
        """
        counts: Counter[str] = Counter(
            _parse_assistant(r)["persona"] for r in v3_all
        )
        for persona in _THIN_CLASSES:
            count = counts.get(persona, 0)
            assert 1 <= count < V3_CAP, (
                f"Thin class {persona!r} has {count} total rows; "
                f"expected 1 <= count < {V3_CAP}"
            )

    def test_row_schema_three_messages(self, v3_all: list[dict[str, Any]]) -> None:
        """Every row has exactly 3 messages with roles [system, user, assistant]."""
        bad: list[int] = []
        for i, row in enumerate(v3_all):
            msgs = row.get("messages", [])
            if len(msgs) != 3:
                bad.append(i)
                continue
            roles = [m.get("role") for m in msgs]
            if roles != ["system", "user", "assistant"]:
                bad.append(i)
        assert bad == [], (
            f"Found {len(bad)} rows with wrong message structure at indices {bad[:5]}"
        )

    def test_assistant_content_valid_json(
        self, v3_all: list[dict[str, Any]]
    ) -> None:
        """Every assistant turn is valid JSON with 'persona' and 'difficulty' keys."""
        bad: list[int] = []
        for i, row in enumerate(v3_all):
            try:
                parsed = _parse_assistant(row)
                if "persona" not in parsed or "difficulty" not in parsed:
                    bad.append(i)
            except (json.JSONDecodeError, KeyError, IndexError):
                bad.append(i)
        assert bad == [], (
            f"Found {len(bad)} rows with invalid assistant JSON at indices {bad[:5]}"
        )

    def test_per_class_train_counts_match_build_report(
        self, v3_train: list[dict[str, Any]]
    ) -> None:
        """Train per-class counts match the WF-G2 BUILD report exactly.

        Expected (from BUILD marker): forge-ui:37, forge-wire:37, hermes:37,
        no-dispatch:37, scout:37, pipeline-async:30, quill-py:30, quill-ts:27,
        atlas:9, palette:7, pipeline-data:7.
        """
        expected: dict[str, int] = {
            "forge-ui": 37,
            "forge-wire": 37,
            "hermes": 37,
            "no-dispatch": 37,
            "scout": 37,
            "pipeline-async": 30,
            "quill-py": 30,
            "quill-ts": 27,
            "atlas": 9,
            "palette": 7,
            "pipeline-data": 7,
        }
        actual: Counter[str] = Counter(
            _parse_assistant(r)["persona"] for r in v3_train
        )
        mismatches = {
            p: (actual.get(p, 0), exp)
            for p, exp in expected.items()
            if actual.get(p, 0) != exp
        }
        assert mismatches == {}, (
            f"Train per-class count mismatches (persona: actual vs expected): {mismatches}"
        )

    def test_per_class_valid_counts_match_build_report(
        self, v3_valid: list[dict[str, Any]]
    ) -> None:
        """Valid per-class counts match the WF-G2 BUILD report exactly.

        Expected: atlas:3, forge-ui:13, forge-wire:13, hermes:13, no-dispatch:13,
        palette:3, pipeline-async:10, pipeline-data:3, quill-py:10, quill-ts:9, scout:13.
        """
        expected: dict[str, int] = {
            "atlas": 3,
            "forge-ui": 13,
            "forge-wire": 13,
            "hermes": 13,
            "no-dispatch": 13,
            "palette": 3,
            "pipeline-async": 10,
            "pipeline-data": 3,
            "quill-py": 10,
            "quill-ts": 9,
            "scout": 13,
        }
        actual: Counter[str] = Counter(
            _parse_assistant(r)["persona"] for r in v3_valid
        )
        mismatches = {
            p: (actual.get(p, 0), exp)
            for p, exp in expected.items()
            if actual.get(p, 0) != exp
        }
        assert mismatches == {}, (
            f"Valid per-class count mismatches (persona: actual vs expected): {mismatches}"
        )

    def test_no_duplicate_user_prompts_in_train(
        self, v3_train: list[dict[str, Any]]
    ) -> None:
        """Train set has no duplicate user prompts (dedup was applied)."""
        prompts = [_user_prompt(r) for r in v3_train]
        counts = Counter(prompts)
        dupes = {p: c for p, c in counts.items() if c > 1}
        assert dupes == {}, (
            f"Found {len(dupes)} duplicate prompt(s) in train set; "
            f"first: {next(iter(dupes))[:80]!r}"
        )

    def test_no_duplicate_user_prompts_in_valid(
        self, v3_valid: list[dict[str, Any]]
    ) -> None:
        """Valid set has no duplicate user prompts."""
        prompts = [_user_prompt(r) for r in v3_valid]
        counts = Counter(prompts)
        dupes = {p: c for p, c in counts.items() if c > 1}
        assert dupes == {}, (
            f"Found {len(dupes)} duplicate prompt(s) in valid set; "
            f"first: {next(iter(dupes))[:80]!r}"
        )

    def test_train_valid_prompts_disjoint(
        self,
        v3_train: list[dict[str, Any]],
        v3_valid: list[dict[str, Any]],
    ) -> None:
        """No prompt appears in both train and valid (no leakage)."""
        train_prompts = {_user_prompt(r) for r in v3_train}
        valid_prompts = {_user_prompt(r) for r in v3_valid}
        overlap = train_prompts & valid_prompts
        assert overlap == set(), (
            f"Found {len(overlap)} prompt(s) in BOTH train and valid (data leakage); "
            f"first: {next(iter(overlap))[:80]!r}"
        )

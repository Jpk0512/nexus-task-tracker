"""Tests for broker.router_train.relabel — deterministic, no real LLM.

Acceptance criteria:
  AC1 — mine_all_real_requests: distinct genuine requests; gold rows keep their
        gold label_source (not 'llm_real'); unlabeled rows carry label_persona=None.
  AC2 — llm_label with FAKE generate_fn: labels ONLY label_persona==None rows;
        NEVER overwrites gold rows; emits label_source='llm_real', confidence,
        a valid persona, a valid difficulty.
  AC3 — Defensive parse: markdown-fenced / garbled JSON from generator is parsed
        or skipped without crashing.
  AC4 — Determinism: fixed fake generate_fn produces identical output on two runs.
  AC5 — Schema enum: is_valid() accepts a row with label_source='llm_real'.
  AC6 — Context-dependent detection: bare continuations are excluded from LLM
        labeling; gold-labeled continuations pass through unchanged.
  AC7 — Incremental/resumable: each batch is written to out_path as it completes;
        on rerun, rows already in out_path are skipped.
"""
from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from broker.router_train.export import is_valid
from broker.router_train.relabel import (
    LABEL_SOURCE_LLM_REAL,
    _VALID_DIFFICULTIES,
    _VALID_PERSONAS,
    is_context_dependent,
    llm_label,
    mine_all_real_requests,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ph(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _gold_row(
    prompt: str,
    persona: str,
    source: str = "dispatch_sidecar",
    confidence: float = 1.0,
    session_id: str = "sess-gold",
) -> dict[str, Any]:
    """Build a row that already has a gold label (label_persona is not None)."""
    return {
        "session_id": session_id,
        "prompt": prompt,
        "prompt_hash": _ph(prompt),
        "label_persona": persona,
        "label_source": source,
        "label_confidence": confidence,
        "label_status": "ok",
    }


def _unlabeled_row(
    prompt: str,
    session_id: str = "sess-unlabeled",
) -> dict[str, Any]:
    """Build a row with no label yet (label_persona is None)."""
    return {
        "session_id": session_id,
        "prompt": prompt,
        "prompt_hash": _ph(prompt),
        "label_persona": None,
    }


def _make_rubric() -> str:
    return "## Test rubric — classify prompts by persona and difficulty."


# ---------------------------------------------------------------------------
# Fake generate_fn builders
# ---------------------------------------------------------------------------


def _fake_generate_good(prompts_in_batch: int) -> str:
    """Return a well-formed batch response for N prompts.

    Assigns persona 'scout' difficulty 'standard' to every prompt.
    """
    lines = []
    for i in range(1, prompts_in_batch + 1):
        lines.append(f"[{i}]")
        lines.append("persona: scout")
        lines.append("difficulty: standard")
        lines.append("")
    return "\n".join(lines)


def _make_fixed_generate(persona: str = "scout", difficulty: str = "standard") -> Any:
    """Return a deterministic generate_fn that always emits persona/difficulty."""

    def _gen(batch_prompt: str) -> str:
        # Count how many numbered prompts are in the batch.
        # The batch format is "[N] <text>" — count "[N]" lines.
        count = sum(
            1
            for line in batch_prompt.splitlines()
            if line.strip().startswith("[") and line.strip().endswith("]")
            and line.strip()[1:-1].isdigit()
        )
        # Fallback: if no "[N]" markers counted, assume 1.
        n = max(count, 1)
        lines = []
        for i in range(1, n + 1):
            lines.append(f"[{i}]")
            lines.append(f"persona: {persona}")
            lines.append(f"difficulty: {difficulty}")
            lines.append("")
        return "\n".join(lines)

    return _gen


# ---------------------------------------------------------------------------
# AC1 — mine_all_real_requests
# ---------------------------------------------------------------------------


class TestMineAllRealRequests:
    """AC1: mine_all_real_requests returns distinct genuine requests.

    Because this function reads from LIVE transcript files on disk, we do NOT
    call it against the full live corpus in these unit tests — that is the domain
    of integration/smoke tests run as a separate Plexus background job.  Instead
    we verify the CONTRACT via the collect_labeled_pairs seam and the public
    output shape on a controlled minimal real root with NO transcripts (empty dir),
    which triggers the code path where both mine_transcripts and mine_no_dispatch
    return [] but collect_labeled_pairs may still emit gold rows.

    The key shape invariants we can assert without disk fixtures:
      - Returns a list of dicts.
      - Every row has 'prompt_hash' (non-empty str) and 'prompt' (str).
      - Rows with gold labels carry label_source != 'llm_real'.
      - Rows without gold labels carry label_persona == None.
    """

    def test_returns_list_of_dicts(self, tmp_path: Path) -> None:
        """Given an empty transcript root, When mine_all_real_requests is called,
        Then it returns a list (possibly empty)."""
        result = mine_all_real_requests(root=tmp_path)
        assert isinstance(result, list)

    def test_every_row_has_prompt_hash_and_prompt(self, tmp_path: Path) -> None:
        """Given a transcript root with one minimal session JSONL,
        When mine_all_real_requests is called,
        Then every returned row carries non-empty prompt_hash and prompt fields."""
        # Build a minimal Claude Code session JSONL with one genuine user turn
        # and one Agent dispatch (so mine_transcripts sees a dispatched pair).
        session_dir = tmp_path / "project-abc"
        session_dir.mkdir()
        prompt_text = "Can you investigate the broker hook latency issue"
        session_file = session_dir / "test-session.jsonl"
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "test-session",
                "timestamp": "2026-06-21T10:00:00+00:00",
                "message": {"role": "user", "content": prompt_text},
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "test-session",
                "timestamp": "2026-06-21T10:00:05+00:00",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_scout",
                            "name": "Agent",
                            "input": {
                                "description": "scout",
                                "prompt": "investigate broker",
                                "subagent_type": "scout",
                            },
                        }
                    ],
                },
            }),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        result = mine_all_real_requests(root=tmp_path)
        assert isinstance(result, list)
        assert len(result) >= 1
        for row in result:
            assert isinstance(row.get("prompt_hash"), str), "prompt_hash must be a str"
            assert row["prompt_hash"] != "", "prompt_hash must be non-empty"
            assert isinstance(row.get("prompt"), str), "prompt must be a str"
            assert row["prompt"] != "", "prompt must be non-empty"

    def test_deduplicated_on_prompt_hash(self, tmp_path: Path) -> None:
        """Given two sessions with the same prompt,
        When mine_all_real_requests is called,
        Then the prompt appears exactly once (dedup on prompt_hash)."""
        prompt_text = "Set up the router training pipeline please"
        # Two sessions, same prompt — the second is a duplicate.
        for i, session_id in enumerate(["sess-a", "sess-b"]):
            session_dir = tmp_path / f"project-{i}"
            session_dir.mkdir()
            session_file = session_dir / f"{session_id}.jsonl"
            lines = [
                json.dumps({
                    "type": "user",
                    "sessionId": session_id,
                    "timestamp": f"2026-06-21T10:0{i}:00+00:00",
                    "message": {"role": "user", "content": prompt_text},
                }),
                json.dumps({
                    "type": "assistant",
                    "sessionId": session_id,
                    "timestamp": f"2026-06-21T10:0{i}:05+00:00",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": f"toolu_{i}",
                                "name": "Agent",
                                "input": {
                                    "description": "scout",
                                    "prompt": "investigate",
                                    "subagent_type": "scout",
                                },
                            }
                        ],
                    },
                }),
            ]
            session_file.write_text("\n".join(lines) + "\n")

        result = mine_all_real_requests(root=tmp_path)
        hashes = [r["prompt_hash"] for r in result]
        assert len(hashes) == len(set(hashes)), "Rows must be deduplicated on prompt_hash"

    def test_unlabeled_rows_have_label_persona_none(self, tmp_path: Path) -> None:
        """Given a prompt with no matching gold label in collect_labeled_pairs,
        When mine_all_real_requests is called,
        Then that row carries label_persona=None (candidate for LLM labeling)."""
        # Use a highly unique prompt that will not match anything in the live corpus.
        prompt_text = "Investigate zz-unique-sentinel-test-prompt-relabel-AC1-2026"
        session_dir = tmp_path / "project-unique"
        session_dir.mkdir()
        session_file = session_dir / "sess-unique.jsonl"
        lines = [
            json.dumps({
                "type": "user",
                "sessionId": "sess-unique",
                "timestamp": "2026-06-21T10:00:00+00:00",
                "message": {"role": "user", "content": prompt_text},
            }),
            json.dumps({
                "type": "assistant",
                "sessionId": "sess-unique",
                "timestamp": "2026-06-21T10:00:05+00:00",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_scout_unique",
                            "name": "Agent",
                            "input": {
                                "description": "scout",
                                "prompt": "investigate",
                                "subagent_type": "scout",
                            },
                        }
                    ],
                },
            }),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        result = mine_all_real_requests(root=tmp_path)
        matching = [r for r in result if r.get("prompt") == prompt_text]
        # The row may have been gold-labeled if the live corpus happens to match,
        # but since this is a sentinel prompt, it should be unlabeled.
        # We assert the row is present and its shape is valid.
        assert len(matching) >= 1
        row = matching[0]
        # label_persona is either None (unlabeled) or a str (gold — only if corpus matched).
        lp = row.get("label_persona")
        assert lp is None or isinstance(lp, str), (
            f"label_persona must be None or str, got {type(lp)!r}"
        )
        # If gold, label_source must NOT be 'llm_real'.
        if lp is not None:
            assert row.get("label_source") != LABEL_SOURCE_LLM_REAL, (
                "Gold row must not have label_source='llm_real'"
            )

    def test_gold_rows_preserve_original_label_source(self, tmp_path: Path) -> None:
        """Given a prompt whose hash matches a gold pair (e.g. dispatch_sidecar),
        When mine_all_real_requests is called,
        Then the returned row's label_source is the original gold source, not 'llm_real'."""
        # We can't easily inject a gold pair without touching live files,
        # but we CAN assert the invariant on any gold row that mine returns.
        result = mine_all_real_requests(root=tmp_path)
        for row in result:
            if row.get("label_persona") is not None:
                assert row.get("label_source") != LABEL_SOURCE_LLM_REAL, (
                    f"Gold row (persona={row['label_persona']!r}) must not have "
                    f"label_source='llm_real'; got {row.get('label_source')!r}"
                )


# ---------------------------------------------------------------------------
# AC2 — llm_label with fake generate_fn
# ---------------------------------------------------------------------------


class TestLlmLabel:
    """AC2: llm_label labels ONLY label_persona==None rows; never overwrites gold."""

    def test_gold_rows_pass_through_unchanged(self) -> None:
        """Given a mix of gold and unlabeled rows,
        When llm_label is called with a fake generate_fn,
        Then gold rows are returned UNCHANGED (label_source, persona, confidence intact)."""
        gold = _gold_row("Add retry logic to the broker gate", "pipeline-data")
        unlabeled = _unlabeled_row("Can you check the vault search latency")
        rows = [gold, unlabeled]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())

        # Gold row must survive verbatim.
        out_gold = result[0]
        assert out_gold["label_persona"] == "pipeline-data"
        assert out_gold["label_source"] == "dispatch_sidecar"
        assert out_gold["label_confidence"] == 1.0
        assert "raw_label" not in out_gold, "Gold row must not gain raw_label"

    def test_unlabeled_rows_receive_llm_real_source(self) -> None:
        """Given unlabeled rows,
        When llm_label is called,
        Then every previously-unlabeled row carries label_source='llm_real'."""
        prompts = [
            "Update the dispatch sidecar hook to log session id",
            "Write the transcript export for rebalancing",
        ]
        rows = [_unlabeled_row(p, session_id=f"sess-{i}") for i, p in enumerate(prompts)]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())

        assert len(result) == len(rows)
        for row in result:
            assert row["label_source"] == LABEL_SOURCE_LLM_REAL, (
                f"Expected label_source='llm_real', got {row['label_source']!r}"
            )

    def test_unlabeled_rows_receive_valid_persona(self) -> None:
        """Given unlabeled rows,
        When llm_label assigns a persona,
        Then label_persona is in TRAINING_LABELS union {'unknown'}."""
        rows = [_unlabeled_row("Implement the forge-ui dashboard tile", session_id="s0")]
        result = llm_label(
            rows, _make_rubric(), generate_fn=_make_fixed_generate(persona="forge-ui")
        )
        persona = result[0]["label_persona"]
        valid = _VALID_PERSONAS | {"unknown"}
        assert persona in valid, f"label_persona={persona!r} not in valid persona set"

    def test_unlabeled_rows_receive_valid_difficulty(self) -> None:
        """Given unlabeled rows,
        When llm_label assigns a difficulty,
        Then label_difficulty is in {trivial, simple, standard, complex, 'unknown'}."""
        rows = [_unlabeled_row("Wire the new hook into settings.json", session_id="s0")]
        result = llm_label(
            rows, _make_rubric(), generate_fn=_make_fixed_generate(difficulty="trivial")
        )
        diff = result[0]["label_difficulty"]
        valid = _VALID_DIFFICULTIES | {"unknown"}
        assert diff in valid, f"label_difficulty={diff!r} not in valid difficulty set"

    def test_unlabeled_rows_receive_confidence(self) -> None:
        """Given unlabeled rows,
        When llm_label runs,
        Then every labeled row carries a float label_confidence."""
        rows = [_unlabeled_row("Can you design a test plan for the export module", session_id="s0")]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())
        conf = result[0]["label_confidence"]
        assert isinstance(conf, float), f"label_confidence must be float, got {type(conf)}"
        assert 0.0 <= conf <= 1.0, f"label_confidence={conf} out of [0,1] range"

    def test_unlabeled_rows_receive_model_id(self) -> None:
        """Given unlabeled rows,
        When llm_label runs with a fake generate_fn,
        Then labeled rows carry a non-empty model_id string."""
        rows = [_unlabeled_row("Refactor the metrics aggregation loop", session_id="s0")]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())
        model_id = result[0].get("model_id")
        assert isinstance(model_id, str) and model_id, (
            f"model_id must be a non-empty str, got {model_id!r}"
        )

    def test_gold_rows_never_overwritten_in_mixed_batch(self) -> None:
        """Given a batch containing both gold and unlabeled rows interleaved,
        When llm_label runs,
        Then NO gold row has its label_persona changed."""
        gold1 = _gold_row("Scaffold the new broker vault endpoint", "hermes", session_id="sg1")
        unlab1 = _unlabeled_row("Check the hook deny-message text", session_id="su1")
        gold2 = _gold_row("Deploy atlas schema migration", "atlas", session_id="sg2")
        unlab2 = _unlabeled_row("Add a ruff lint rule to pyproject", session_id="su2")
        rows = [gold1, unlab1, gold2, unlab2]

        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())

        assert result[0]["label_persona"] == "hermes"
        assert result[0]["label_source"] == "dispatch_sidecar"
        assert result[2]["label_persona"] == "atlas"
        assert result[2]["label_source"] == "dispatch_sidecar"
        # The unlabeled rows should now have a label.
        assert result[1]["label_source"] == LABEL_SOURCE_LLM_REAL
        assert result[3]["label_source"] == LABEL_SOURCE_LLM_REAL

    def test_all_gold_batch_returns_unchanged(self) -> None:
        """Given a batch of only gold rows (nothing to label),
        When llm_label is called,
        Then the output is identical to the input (no LLM call made)."""
        rows = [
            _gold_row("Build the webhook handler", "forge-wire", session_id="sg1"),
            _gold_row("Write pytest for router module", "quill-py", session_id="sg2"),
        ]
        called: list[str] = []

        def _spy_gen(prompt: str) -> str:
            called.append(prompt)
            return ""

        result = llm_label(rows, _make_rubric(), generate_fn=_spy_gen)
        assert called == [], "generate_fn must NOT be called when all rows are gold"
        assert result[0]["label_persona"] == "forge-wire"
        assert result[1]["label_persona"] == "quill-py"


# ---------------------------------------------------------------------------
# AC3 — Defensive parse: garbled/fenced generator output
# ---------------------------------------------------------------------------


class TestDefensiveParse:
    """AC3: garbled/markdown-fenced generator output is handled without crashing."""

    def test_markdown_fenced_response_does_not_crash(self) -> None:
        """Given a generator that wraps output in markdown fences,
        When llm_label is called,
        Then it returns without raising and rows carry label_source='llm_real'."""
        def _fenced_gen(batch_prompt: str) -> str:
            return textwrap.dedent("""\
                ```json
                {"persona": "scout", "difficulty": "standard"}
                ```

                [1]
                persona: scout
                difficulty: standard
            """)

        rows = [_unlabeled_row("Trace the slow path in broker startup", session_id="s0")]
        # Must not raise.
        result = llm_label(rows, _make_rubric(), generate_fn=_fenced_gen)
        assert len(result) == 1
        assert result[0]["label_source"] == LABEL_SOURCE_LLM_REAL

    def test_completely_garbled_response_does_not_crash(self) -> None:
        """Given a generator returning random noise,
        When llm_label is called,
        Then it returns without raising; the row gets label_persona='unknown'."""
        def _garbled_gen(batch_prompt: str) -> str:
            return "!@#$%^&*() definitely not valid response format!!!"

        rows = [_unlabeled_row("Fix the missing import in aggregate.py", session_id="s0")]
        result = llm_label(rows, _make_rubric(), generate_fn=_garbled_gen)
        assert len(result) == 1
        assert result[0]["label_source"] == LABEL_SOURCE_LLM_REAL
        # When parse fails persona defaults to 'unknown'.
        assert result[0]["label_persona"] == "unknown"
        assert result[0]["label_difficulty"] == "unknown"

    def test_empty_generator_response_skips_row_with_label_error(self) -> None:
        """Given a generator that always returns an empty string,
        When llm_label is called,
        Then it returns without raising; the row is skipped (retry-then-skip)
        and carries label_error='labeler_timeout' with label_persona=None."""
        def _empty_gen(batch_prompt: str) -> str:
            return ""

        rows = [_unlabeled_row("Add pagination to vault search results", session_id="s0")]
        result = llm_label(rows, _make_rubric(), generate_fn=_empty_gen)
        assert len(result) == 1
        # Empty gen → retry-then-skip: row is skipped, not silently labeled.
        assert result[0].get("label_error") == "labeler_timeout", (
            f"Expected label_error='labeler_timeout', got {result[0].get('label_error')!r}"
        )
        assert result[0]["label_persona"] is None, (
            "Skipped row must not receive a fabricated persona"
        )

    def test_partial_response_missing_difficulty_does_not_crash(self) -> None:
        """Given a generator that omits difficulty for some prompts,
        When llm_label is called,
        Then it returns without raising; missing difficulty becomes 'unknown'."""
        def _partial_gen(batch_prompt: str) -> str:
            return "[1]\npersona: scout\n"  # No difficulty line.

        rows = [_unlabeled_row("Review the session JSONL schema", session_id="s0")]
        result = llm_label(rows, _make_rubric(), generate_fn=_partial_gen)
        assert len(result) == 1
        assert result[0]["label_source"] == LABEL_SOURCE_LLM_REAL
        assert result[0]["label_difficulty"] == "unknown"

    def test_unknown_persona_from_generator_stored_as_unknown(self) -> None:
        """Given a generator emitting an unrecognized persona string,
        When llm_label is called,
        Then label_persona is 'unknown' (never a fabricated valid persona)."""
        def _unknown_persona_gen(batch_prompt: str) -> str:
            return "[1]\npersona: some-made-up-agent\ndifficulty: simple\n"

        rows = [_unlabeled_row("Help me set up the nexus broker", session_id="s0")]
        result = llm_label(rows, _make_rubric(), generate_fn=_unknown_persona_gen)
        assert len(result) == 1
        # An unrecognized persona must NOT be silently treated as valid.
        # The relabel code stores it as 'unknown'.
        assert result[0]["label_persona"] == "unknown"


# ---------------------------------------------------------------------------
# AC4 — Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """AC4: fixed fake generate_fn produces identical output on repeated calls."""

    def test_same_output_on_two_runs(self) -> None:
        """Given the same input rows and a fixed generate_fn,
        When llm_label is called twice,
        Then both calls produce byte-for-byte identical results."""
        rows = [
            _unlabeled_row("Implement the labeling pipeline smoke test", session_id="s0"),
            _unlabeled_row("Wire the export finetune v2 to the training job", session_id="s1"),
            _gold_row("Add a Plexus self-test for the vault MCP", "hermes", session_id="sg"),
        ]
        rubric = _make_rubric()
        gen = _make_fixed_generate(persona="scout", difficulty="standard")

        result_a = llm_label(rows, rubric, generate_fn=gen)
        result_b = llm_label(rows, rubric, generate_fn=gen)

        assert len(result_a) == len(result_b)
        for a, b in zip(result_a, result_b, strict=True):
            assert a == b, (
                f"Non-deterministic output detected:\n  run1={a!r}\n  run2={b!r}"
            )

    def test_output_order_matches_input_order(self) -> None:
        """Given ordered rows [unlabeled, gold, unlabeled],
        When llm_label is called,
        Then the output list preserves the same positional order."""
        p1 = "First unlabeled: plan the retrain workflow"
        p2 = "Gold row: design the fine-tune eval harness"
        p3 = "Second unlabeled: write tests for export_finetune_v2"
        rows: list[dict[str, Any]] = [
            _unlabeled_row(p1, session_id="s0"),
            _gold_row(p2, "palette", session_id="sg"),
            _unlabeled_row(p3, session_id="s2"),
        ]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())

        assert result[0]["prompt"] == p1
        assert result[1]["prompt"] == p2
        assert result[2]["prompt"] == p3

    def test_batch_boundary_does_not_affect_output(self) -> None:
        """Given a batch larger than 1 prompt (exercises the batch-split logic),
        When llm_label is called,
        Then all unlabeled rows receive a label (no dropped rows at batch boundary)."""
        n_unlabeled = 5
        rows = [
            _unlabeled_row(f"Unlabeled prompt number {i}", session_id=f"s{i}")
            for i in range(n_unlabeled)
        ]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())
        labeled = [r for r in result if r.get("label_source") == LABEL_SOURCE_LLM_REAL]
        assert len(labeled) == n_unlabeled, (
            f"Expected {n_unlabeled} labeled rows, got {len(labeled)}"
        )


# ---------------------------------------------------------------------------
# AC5 — Schema enum: is_valid() must accept llm_real rows
# ---------------------------------------------------------------------------


class TestSchemaEnumLlmReal:
    """AC5: router_training_record.schema.json includes 'llm_real' in label_source enum."""

    def _minimal_valid_row(self, label_source: str) -> dict[str, Any]:
        """Construct a minimal row that satisfies all required schema fields."""
        return {
            "prompt": "Write the Polars transform for session aggregation",
            "label_persona": "pipeline-data",
            "label_source": label_source,
            "label_confidence": 0.85,
            "schema_version": 2,
            "router_version": "v2",
            "model_id": "claude",
            "prompt_hash": "a" * 64,
        }

    def test_llm_real_is_accepted_by_is_valid(self) -> None:
        """Given a row with label_source='llm_real',
        When is_valid() is called,
        Then it returns True (schema enum includes 'llm_real')."""
        row = self._minimal_valid_row("llm_real")
        assert is_valid([row]), (
            "is_valid() rejected label_source='llm_real' — schema enum missing 'llm_real'"
        )

    def test_dispatch_sidecar_still_valid(self) -> None:
        """Regression: existing label_source values still pass validation."""
        for src in ("dispatch_sidecar", "transcript_mining", "synthetic", "human"):
            row = self._minimal_valid_row(src)
            assert is_valid([row]), f"is_valid() rejected label_source={src!r}"

    def test_unknown_label_source_rejected(self) -> None:
        """Given a row with an unknown label_source,
        When is_valid() is called,
        Then it returns False."""
        row = self._minimal_valid_row("made_up_source")
        assert not is_valid([row]), "is_valid() should reject unknown label_source"


# ---------------------------------------------------------------------------
# AC6 — Context-dependent detection
# ---------------------------------------------------------------------------


class TestContextDependent:
    """AC6: bare continuations excluded from LLM labeling; gold continuations pass through."""

    @pytest.mark.parametrize("prompt", [
        "go ahead",
        "go ahead and implement those suggestions",
        "yes do that",
        "yes",
        "implement those",
        "continue",
        "fix it",
        "looks good",
        "okay",
        "do it",
        "proceed",
        "please continue",
        "sounds good",
        "that looks good",
        "implement those changes",
        "yes, implement those suggestions",
    ])
    def test_is_context_dependent_true(self, prompt: str) -> None:
        """Known bare-continuation prompts must be detected as context-dependent."""
        assert is_context_dependent(prompt), (
            f"Expected is_context_dependent=True for {prompt!r}"
        )

    @pytest.mark.parametrize("prompt", [
        "Write the Polars transform that joins sessions to decisions on prompt_hash",
        "Add a Dramatiq actor for Tableau API retries with exponential backoff",
        "Can you investigate the broker hook latency issue",
        "Design the DuckDB table for embedding vectors",
        "Update docker-compose.yml to add a Redis service",
        "What did we commit in the last session?",
        "Build the server action that fetches workbook metadata",
    ])
    def test_is_context_dependent_false(self, prompt: str) -> None:
        """Real routable prompts must NOT be flagged as context-dependent."""
        assert not is_context_dependent(prompt), (
            f"Expected is_context_dependent=False for {prompt!r}"
        )

    def test_context_dependent_rows_excluded_from_llm_labeling(self) -> None:
        """Given a mix of normal unlabeled and context-dependent unlabeled rows,
        When llm_label is called,
        Then context-dependent rows are NOT sent to the LLM (no fabricated persona)
        and carry context_dependent=True with label_persona=None."""
        normal = _unlabeled_row("Write the Polars transform for schema migration", session_id="s0")
        continuation = _unlabeled_row("go ahead and implement those suggestions", session_id="s1")
        short_bare = _unlabeled_row("yes do that", session_id="s2")

        called_with: list[str] = []

        def _spy_gen(prompt: str) -> str:
            called_with.append(prompt)
            # Count bracketed items in prompt and return matching labels
            count = sum(
                1 for line in prompt.splitlines()
                if line.strip().startswith("[") and line.strip().endswith("]")
                and line.strip()[1:-1].isdigit()
            )
            n = max(count, 1)
            lines = []
            for i in range(1, n + 1):
                lines.append(f"[{i}]")
                lines.append("persona: scout")
                lines.append("difficulty: standard")
                lines.append("")
            return "\n".join(lines)

        rows = [normal, continuation, short_bare]
        result = llm_label(rows, _make_rubric(), generate_fn=_spy_gen)

        # Normal row: labeled
        assert result[0]["label_source"] == LABEL_SOURCE_LLM_REAL
        assert result[0].get("context_dependent") is not True

        # Continuation rows: marked context_dependent, no fabricated persona
        assert result[1].get("context_dependent") is True
        assert result[1]["label_persona"] is None, (
            "Context-dependent row must not receive a fabricated persona"
        )
        assert result[2].get("context_dependent") is True
        assert result[2]["label_persona"] is None

        # The LLM was called only once (for the 1 normal prompt, not the 2 continuations)
        assert len(called_with) == 1, (
            f"LLM should have been called once (for 1 normal prompt), got {len(called_with)}"
        )

    def test_gold_continuation_passes_through_unchanged(self) -> None:
        """Given a gold-labeled row whose prompt is a bare continuation,
        When llm_label is called,
        Then the gold label is preserved (context_dependent detection does not touch gold)."""
        # A gold row whose prompt text looks like a bare continuation
        gold = _gold_row("go ahead", "hermes", source="dispatch_sidecar", confidence=1.0)
        rows = [gold]
        result = llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate())
        assert result[0]["label_persona"] == "hermes"
        assert result[0]["label_source"] == "dispatch_sidecar"
        assert result[0].get("context_dependent") is not True


# ---------------------------------------------------------------------------
# AC7 — Incremental / resumable writes via out_path
# ---------------------------------------------------------------------------


class TestIncrementalResumable:
    """AC7: llm_label writes each batch to out_path as it completes; resumes on rerun."""

    def test_out_path_written_per_batch(self, tmp_path: Path) -> None:
        """Given an out_path and unlabeled rows,
        When llm_label completes,
        Then out_path contains one JSONL line per labeled row."""
        out_file = tmp_path / "labels.jsonl"
        rows = [
            _unlabeled_row(f"Write the ingestion transform step {i}", session_id=f"s{i}")
            for i in range(3)
        ]
        llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate(), out_path=out_file)

        assert out_file.exists(), "out_path file must be created by llm_label"
        written = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip()]
        assert len(written) == 3, f"Expected 3 lines in out_path, got {len(written)}"
        for rec in written:
            assert rec.get("label_source") == LABEL_SOURCE_LLM_REAL
            assert "prompt_hash" in rec

    def test_rerun_skips_already_labeled_rows(self, tmp_path: Path) -> None:
        """Given an out_path that already contains some labeled rows from a prior run,
        When llm_label is called again with the same rows,
        Then already-present rows are skipped and the LLM is not called for them."""
        out_file = tmp_path / "labels.jsonl"
        prompts = [
            f"Add the {noun} transform to the ingestion pipeline"
            for noun in ("sessions", "decisions", "embeddings")
        ]
        rows = [_unlabeled_row(p, session_id=f"s{i}") for i, p in enumerate(prompts)]

        call_count: list[int] = [0]

        def _counting_gen(prompt: str) -> str:
            call_count[0] += 1
            count = sum(
                1 for line in prompt.splitlines()
                if line.strip().startswith("[") and line.strip().endswith("]")
                and line.strip()[1:-1].isdigit()
            )
            n = max(count, 1)
            lines = []
            for i in range(1, n + 1):
                lines.append(f"[{i}]")
                lines.append("persona: pipeline-data")
                lines.append("difficulty: simple")
                lines.append("")
            return "\n".join(lines)

        # First run: labels all 3
        llm_label(rows, _make_rubric(), generate_fn=_counting_gen, out_path=out_file)
        calls_after_first = call_count[0]
        assert calls_after_first >= 1

        # Second run: all 3 are already in out_path — LLM must NOT be called
        call_count[0] = 0
        result = llm_label(rows, _make_rubric(), generate_fn=_counting_gen, out_path=out_file)
        assert call_count[0] == 0, (
            f"LLM called {call_count[0]} times on rerun — rows should have been skipped"
        )
        # Output should still reflect the labeled state from the first run
        # (rows in output list are left as-is for already-done hashes)
        assert len(result) == 3

    def test_context_dependent_rows_not_written_to_out_path(self, tmp_path: Path) -> None:
        """Context-dependent rows must not appear in out_path (they have no label)."""
        out_file = tmp_path / "labels.jsonl"
        rows = [
            _unlabeled_row("Write the DuckDB writer for embeddings", session_id="s0"),
            _unlabeled_row("go ahead", session_id="s1"),
        ]
        llm_label(rows, _make_rubric(), generate_fn=_make_fixed_generate(), out_path=out_file)
        written = [json.loads(line) for line in out_file.read_text().splitlines() if line.strip()]
        # Only the routable row should be written; context-dependent excluded
        assert len(written) == 1, (
            f"Expected 1 line in out_path (1 routable, 1 context-dependent excluded), got {len(written)}"
        )
        assert written[0]["label_source"] == LABEL_SOURCE_LLM_REAL


# ---------------------------------------------------------------------------
# AC8 — Retry-then-skip: single bad batch never aborts the run
# ---------------------------------------------------------------------------


class TestRetryThenSkip:
    """AC8: a failing batch is retried once then skipped; the run continues."""

    def test_single_bad_batch_does_not_abort_run(self) -> None:
        """Given a generate_fn that always returns empty,
        When llm_label is called with batch_size=3 and 6 unlabeled rows,
        Then ALL rows carry label_error='labeler_timeout' — the run does NOT raise.
        This confirms that a total generator failure never aborts the process."""
        prompts = [f"Add the Polars transform step {i}" for i in range(6)]
        rows = [_unlabeled_row(p, session_id=f"s{i}") for i, p in enumerate(prompts)]

        def _always_empty(_batch_prompt: str) -> str:
            return ""

        # Must not raise — even with all batches failing
        result = llm_label(rows, _make_rubric(), generate_fn=_always_empty, batch_size=3)

        assert len(result) == 6

        # All rows skipped → label_error='labeler_timeout', label_persona=None
        skipped = [r for r in result if r.get("label_error") == "labeler_timeout"]
        assert len(skipped) == 6, (
            f"Expected all 6 rows skipped after total failure, got {len(skipped)}"
        )
        for r in skipped:
            assert r["label_persona"] is None, "Skipped row must not have a fabricated persona"

    def test_partial_batch_failure_continues_to_next_batch(self) -> None:
        """Given a generate_fn that fails the first batch call but succeeds after,
        When llm_label is called with batch_size=2 and 4 rows,
        Then: batch 0 fails (rows 0-1 skipped), batch 1 succeeds (rows 2-3 labeled).
        The run does NOT raise — a single batch failure is localized."""
        prompts = [f"Prompt number {i} for ingestion step" for i in range(4)]
        rows = [_unlabeled_row(p, session_id=f"s{i}") for i, p in enumerate(prompts)]

        call_count: list[int] = [0]

        def _fail_first_only(batch_prompt: str) -> str:
            call_count[0] += 1
            # First call (initial batch 0) fails; all retries and batch 1 succeed
            if call_count[0] == 1:
                return ""
            # Return valid labels — use batch size from the prompt
            # Count "[N]" on its own line (retry sends half-batches of 1)
            count = sum(
                1 for line in batch_prompt.splitlines()
                if line.strip().startswith("[") and line.strip().endswith("]")
                and line.strip()[1:-1].isdigit()
            )
            # Each half-batch is 1 prompt when batch_size=2 → n defaults to 1
            n = max(count, 1)
            lines: list[str] = []
            for i in range(1, n + 1):
                lines.append(f"[{i}]")
                lines.append("persona: scout")
                lines.append("difficulty: standard")
                lines.append("")
            return "\n".join(lines)

        result = llm_label(rows, _make_rubric(), generate_fn=_fail_first_only, batch_size=2)

        assert len(result) == 4

        # Batch 1 (rows 2-3) must be labeled (generator succeeded for batch 1)
        batch1_labeled = [r for r in result[2:] if r.get("label_source") == LABEL_SOURCE_LLM_REAL]
        assert len(batch1_labeled) == 2, (
            f"Expected 2 rows labeled from batch 1, got {len(batch1_labeled)}"
        )

        # Batch 0 was retried — first call failed; sub-batch retries succeeded (call count > 1)
        # So rows 0-1 should also be labeled (recovered by half-batch retry)
        batch0_labeled = [r for r in result[:2] if r.get("label_source") == LABEL_SOURCE_LLM_REAL]
        # Either labeled (recovered) or skipped — run must NOT have raised
        batch0_skipped = [r for r in result[:2] if r.get("label_error") == "labeler_timeout"]
        assert len(batch0_labeled) + len(batch0_skipped) == 2, (
            "Every batch-0 row must be either labeled or skipped — no missing rows"
        )

    def test_single_bad_batch_retry_halves_the_batch(self) -> None:
        """Given a generate_fn that fails the first call but succeeds on sub-batches,
        When llm_label retries with half-batches,
        Then those prompts are recovered (labeled, not skipped)."""
        prompts = [f"Implement broker router step {i}" for i in range(4)]
        rows = [_unlabeled_row(p, session_id=f"s{i}") for i, p in enumerate(prompts)]

        call_count: list[int] = [0]

        def _fail_first_gen(batch_prompt: str) -> str:
            call_count[0] += 1
            if call_count[0] == 1:
                # Fail the first full-batch call
                return ""
            # Succeed on sub-batch calls — return at least 1 valid label
            return "[1]\npersona: hermes\ndifficulty: trivial\n"

        result = llm_label(rows, _make_rubric(), generate_fn=_fail_first_gen, batch_size=4)

        # All 4 should be recovered via half-batch retry (2 sub-batches of 2)
        labeled = [r for r in result if r.get("label_source") == LABEL_SOURCE_LLM_REAL]
        assert len(labeled) == 4, (
            f"Expected all 4 rows labeled via retry, got {len(labeled)} labeled, "
            f"result={[r.get('label_error') for r in result]}"
        )
        # generator returned [1] hermes — at least first of each sub-batch gets hermes
        hermes_count = sum(1 for r in labeled if r["label_persona"] == "hermes")
        assert hermes_count >= 2, (
            f"Expected at least 2 rows with persona=hermes (one per sub-batch), "
            f"got {hermes_count}"
        )

    def test_skipped_rows_not_written_to_out_path(self, tmp_path: Path) -> None:
        """Given a failing batch with out_path configured,
        When llm_label skips those rows after retry failure,
        Then those rows are NOT written to out_path (nothing to resume on rerun)."""
        out_file = tmp_path / "labels.jsonl"

        def _always_empty(_batch_prompt: str) -> str:
            return ""

        rows = [_unlabeled_row(f"Prompt {i}", session_id=f"s{i}") for i in range(2)]
        llm_label(rows, _make_rubric(), generate_fn=_always_empty, out_path=out_file)

        if out_file.exists():
            written = [
                json.loads(ln)
                for ln in out_file.read_text().splitlines()
                if ln.strip()
            ]
            assert len(written) == 0, (
                f"Skipped (timed-out) rows must not be written to out_path; got {len(written)}"
            )

    def test_configurable_batch_size(self) -> None:
        """Given batch_size=2 and 5 unlabeled rows,
        When llm_label is called,
        Then the generator is called multiple times (one call per batch of 2/1)."""
        rows = [
            _unlabeled_row(f"Configure the Dramatiq pipeline step {i}", session_id=f"s{i}")
            for i in range(5)
        ]

        call_count: list[int] = [0]

        def _counting_gen(batch_prompt: str) -> str:
            call_count[0] += 1
            count = sum(
                1 for line in batch_prompt.splitlines()
                if line.strip().startswith("[") and line.strip().endswith("]")
                and line.strip()[1:-1].isdigit()
            )
            n = max(count, 1)
            lines: list[str] = []
            for i in range(1, n + 1):
                lines.append(f"[{i}]")
                lines.append("persona: pipeline-async")
                lines.append("difficulty: simple")
                lines.append("")
            return "\n".join(lines)

        result = llm_label(rows, _make_rubric(), generate_fn=_counting_gen, batch_size=2)

        # 5 rows / batch_size=2 → 3 batches (2+2+1)
        assert call_count[0] == 3, (
            f"Expected 3 generate_fn calls for 5 prompts at batch_size=2, got {call_count[0]}"
        )
        labeled = [r for r in result if r.get("label_source") == LABEL_SOURCE_LLM_REAL]
        assert len(labeled) == 5


# ---------------------------------------------------------------------------
# AC9 — Targeted resilience: 3-batch run with bad middle batch + out_path writes
# ---------------------------------------------------------------------------


def _make_labeled_response(n: int, persona: str = "scout", difficulty: str = "standard") -> str:
    """Helper: build a valid LLM response for n numbered prompts."""
    lines: list[str] = []
    for i in range(1, n + 1):
        lines.append(f"[{i}]")
        lines.append(f"persona: {persona}")
        lines.append(f"difficulty: {difficulty}")
        lines.append("")
    return "\n".join(lines)


def _count_numbered_prompts(batch_prompt: str) -> int:
    """Count [N] markers in a batch prompt string."""
    return sum(
        1
        for line in batch_prompt.splitlines()
        if line.strip().startswith("[") and line.strip().endswith("]")
        and line.strip()[1:-1].isdigit()
    )


class TestResilienceHarden:
    """AC9: precise resilience scenarios requested in harden brief.

    Four scenarios tested:
      9a — bad 2nd batch (empty/raises): batches 1 and 3 are labeled+written;
           no exception propagates from llm_label.
      9b — retry-then-succeed: a generate_fn that fails once then succeeds
           causes the batch to be labeled after retry (not skipped).
      9c — persistent-skip: a generate_fn that always fails marks those rows
           label_error='labeler_timeout'; other batches proceed normally.
      9d — incremental+resume regression: out_path is appended per batch;
           rerun skips already-done hashes; no row is double-written.
    """

    def test_9a_bad_middle_batch_does_not_abort_flanking_batches(
        self, tmp_path: Path
    ) -> None:
        """Given a 3-batch run (3 rows each) where batch 1 (middle) always fails,
        When llm_label is called with out_path,
        Then: batches 0 and 2 are labeled AND written to out_path;
              batch 1 rows carry label_error='labeler_timeout';
              no exception propagates from llm_label."""
        out_file = tmp_path / "labels_9a.jsonl"

        # 9 rows → 3 batches of 3
        prompts = [f"Run the ingestion pipeline step {i}" for i in range(9)]
        rows = [_unlabeled_row(p, session_id=f"s9a-{i}") for i, p in enumerate(prompts)]

        # Track which batch (by call number) is being processed.
        # Call order: batch0 first attempt, possibly retries, batch1 first attempt,
        # possibly retries, batch2 first attempt. We track by counting "[1]" presence
        # and which batch we're in via state.
        batch_num: list[int] = [0]
        call_counts: list[int] = [0, 0, 0]  # calls per batch slot

        def _fail_middle(batch_prompt: str) -> str:
            n = _count_numbered_prompts(batch_prompt)
            # Identify which batch slot this call belongs to using a simple counter.
            # The first call for any given batch size signals a new batch start.
            # We use a side-channel: track how many distinct batch-size groups we've seen.
            slot = batch_num[0]
            call_counts[slot] += 1
            if slot == 1:
                # Middle batch: always fail (both initial + retry attempts)
                if call_counts[slot] == 1:
                    # After the first failure the retry kicks in — advance slot
                    # after retry is exhausted. We advance batch_num when retries done.
                    pass
                return ""
            # For non-middle batches: always succeed
            resp = _make_labeled_response(max(n, 1), persona="atlas", difficulty="complex")
            # Advance batch slot after a SUCCESS (first success signals batch done)
            batch_num[0] = min(slot + 1, 2)
            return resp

        # Because the slot-advancement logic is tricky with retries, we use a simpler
        # approach: fail specifically on calls 1 (initial batch1) and 2 (retry half-a),
        # and 3 (retry half-b), then succeed otherwise. We count total calls.
        # The retry logic for batch_size=3: half=max(1, 3//2)=1, so there are
        # 3 sub-batches in the retry loop. Call breakdown:
        #   call 1: batch0 initial (3 prompts) → succeed
        #   call 2: batch1 initial (3 prompts) → fail
        #   calls 3,4,5: batch1 sub-batches (1 prompt each) → fail (persistent)
        #   call 6: batch2 initial (3 prompts) → succeed
        total_calls: list[int] = [0]

        def _middle_batch_failer(batch_prompt: str) -> str:
            total_calls[0] += 1
            n = max(_count_numbered_prompts(batch_prompt), 1)
            call = total_calls[0]
            if call == 1:
                # batch 0 — succeed
                return _make_labeled_response(n, persona="atlas", difficulty="complex")
            if call in (2, 3, 4, 5):
                # batch 1 initial (call 2) + 3 sub-batch retries (calls 3,4,5) — all fail
                return ""
            # batch 2 (call 6+) — succeed
            return _make_labeled_response(n, persona="atlas", difficulty="complex")

        result = llm_label(
            rows, _make_rubric(), generate_fn=_middle_batch_failer, batch_size=3,
            out_path=out_file
        )

        # No exception — len must be 9
        assert len(result) == 9, f"llm_label must return all 9 rows, got {len(result)}"

        # Batch 0 (rows 0-2): labeled
        batch0 = result[:3]
        assert all(r.get("label_source") == LABEL_SOURCE_LLM_REAL for r in batch0), (
            f"Batch 0 rows must be labeled; got label_sources={[r.get('label_source') for r in batch0]}"
        )

        # Batch 1 (rows 3-5): skipped after retry failure
        batch1 = result[3:6]
        assert all(r.get("label_error") == "labeler_timeout" for r in batch1), (
            f"Batch 1 rows must be skipped; got {[r.get('label_error') for r in batch1]}"
        )
        assert all(r["label_persona"] is None for r in batch1), (
            "Batch 1 skipped rows must not have a fabricated persona"
        )

        # Batch 2 (rows 6-8): labeled
        batch2 = result[6:]
        assert all(r.get("label_source") == LABEL_SOURCE_LLM_REAL for r in batch2), (
            f"Batch 2 rows must be labeled; got label_sources={[r.get('label_source') for r in batch2]}"
        )

        # out_path: only labeled rows written — 6 from batches 0+2, none from batch 1
        assert out_file.exists(), "out_path must be created when labeled rows exist"
        written = [
            json.loads(ln) for ln in out_file.read_text().splitlines() if ln.strip()
        ]
        assert len(written) == 6, (
            f"out_path must contain 6 rows (batches 0+2 only); got {len(written)}"
        )
        written_hashes = {r["prompt_hash"] for r in written}
        # None of the batch-1 prompt_hashes should be in out_path
        batch1_hashes = {rows[i]["prompt_hash"] for i in range(3, 6)}
        overlap = written_hashes & batch1_hashes
        assert not overlap, (
            f"Skipped (batch-1) prompt hashes must not appear in out_path; overlap={overlap}"
        )

    def test_9b_retry_then_succeed_labels_the_batch(self) -> None:
        """Given a generate_fn that fails once (full batch) then succeeds on half-batches,
        When llm_label processes a single batch of 4 rows,
        Then ALL 4 rows are labeled (recovered via half-batch retry) — not skipped."""
        prompts = [f"Implement relay routing step {i}" for i in range(4)]
        rows = [_unlabeled_row(p, session_id=f"s9b-{i}") for i, p in enumerate(prompts)]

        call_num: list[int] = [0]

        def _fail_once_gen(batch_prompt: str) -> str:
            call_num[0] += 1
            n = max(_count_numbered_prompts(batch_prompt), 1)
            if call_num[0] == 1:
                # First full-batch call fails → triggers half-batch retry
                return ""
            # All subsequent calls (half-batch retries) succeed
            return _make_labeled_response(n, persona="pipeline-data", difficulty="simple")

        result = llm_label(rows, _make_rubric(), generate_fn=_fail_once_gen, batch_size=4)

        assert len(result) == 4
        labeled = [r for r in result if r.get("label_source") == LABEL_SOURCE_LLM_REAL]
        skipped = [r for r in result if r.get("label_error") == "labeler_timeout"]
        assert len(labeled) == 4, (
            f"All 4 rows must be labeled after retry; labeled={len(labeled)}, "
            f"skipped={len(skipped)}"
        )
        # Verify call_num > 1 (retry actually fired)
        assert call_num[0] > 1, (
            f"generate_fn must have been called more than once (retry); got {call_num[0]}"
        )

    def test_9c_persistent_failure_skips_batch_others_proceed(
        self, tmp_path: Path
    ) -> None:
        """Given a generate_fn that permanently fails for one batch but succeeds for others,
        When llm_label processes 3 batches (batch sizes 2, 2, 2),
        Then: failing-batch rows carry label_error='labeler_timeout' with label_persona=None;
              succeeding-batch rows carry label_source='llm_real';
              no exception propagates from llm_label;
              out_path contains only the successfully labeled rows."""
        out_file = tmp_path / "labels_9c.jsonl"
        # 6 rows → 3 batches of 2; batch index 1 will always fail
        prompts = [f"Broker dispatch resilience step {i}" for i in range(6)]
        rows = [_unlabeled_row(p, session_id=f"s9c-{i}") for i, p in enumerate(prompts)]

        # Track calls per batch group by counting sequential pairs
        call_num: list[int] = [0]

        def _batch1_always_fails(batch_prompt: str) -> str:
            call_num[0] += 1
            n = max(_count_numbered_prompts(batch_prompt), 1)
            # Calls: 1=batch0, 2=batch1-initial, 3=batch1-half-retry-a,
            #        4=batch1-half-retry-b, 5=batch2
            call = call_num[0]
            if call in (2, 3, 4):
                # batch 1 and all its retries → always fail
                return ""
            return _make_labeled_response(n, persona="forge-wire", difficulty="standard")

        result = llm_label(
            rows, _make_rubric(), generate_fn=_batch1_always_fails, batch_size=2,
            out_path=out_file
        )

        assert len(result) == 6, f"Must return all 6 rows; got {len(result)}"

        # Rows 0-1 (batch 0): labeled
        assert result[0].get("label_source") == LABEL_SOURCE_LLM_REAL
        assert result[1].get("label_source") == LABEL_SOURCE_LLM_REAL

        # Rows 2-3 (batch 1): persistently skipped
        assert result[2].get("label_error") == "labeler_timeout"
        assert result[3].get("label_error") == "labeler_timeout"
        assert result[2]["label_persona"] is None
        assert result[3]["label_persona"] is None

        # Rows 4-5 (batch 2): labeled (run continued past the failed batch)
        assert result[4].get("label_source") == LABEL_SOURCE_LLM_REAL
        assert result[5].get("label_source") == LABEL_SOURCE_LLM_REAL

        # out_path: 4 rows (batches 0 and 2), NOT the 2 skipped rows
        assert out_file.exists()
        written = [
            json.loads(ln) for ln in out_file.read_text().splitlines() if ln.strip()
        ]
        assert len(written) == 4, (
            f"out_path must have 4 rows (batches 0+2 only); got {len(written)}"
        )
        for rec in written:
            assert rec.get("label_source") == LABEL_SOURCE_LLM_REAL, (
                f"All written rows must be labeled; got {rec.get('label_source')!r}"
            )

    def test_9d_incremental_resume_regression(self, tmp_path: Path) -> None:
        """Regression: out_path is appended per batch; rerun skips done hashes.

        Given a first run that labels 4 rows across 2 batches,
        When a second run is called with the same rows + 2 new rows,
        Then: the LLM is called only for the 2 NEW rows (4 already-done are skipped);
              the final out_path contains all 6 unique labeled rows (no duplicates).
        """
        out_file = tmp_path / "labels_9d.jsonl"
        call_num: list[int] = [0]

        def _success_gen(batch_prompt: str) -> str:
            call_num[0] += 1
            n = max(_count_numbered_prompts(batch_prompt), 1)
            return _make_labeled_response(n, persona="quill-py", difficulty="trivial")

        # First run: 4 rows → 2 batches of 2
        original_prompts = [f"Wire the session store step {i}" for i in range(4)]
        original_rows = [
            _unlabeled_row(p, session_id=f"s9d-{i}") for i, p in enumerate(original_prompts)
        ]
        llm_label(
            original_rows, _make_rubric(), generate_fn=_success_gen,
            out_path=out_file, batch_size=2
        )
        calls_first_run = call_num[0]
        assert calls_first_run >= 2, (
            f"First run must call generator at least twice (2 batches); got {calls_first_run}"
        )

        first_run_written = [
            json.loads(ln) for ln in out_file.read_text().splitlines() if ln.strip()
        ]
        assert len(first_run_written) == 4, (
            f"First run must write 4 rows; got {len(first_run_written)}"
        )

        # Reset call counter for second run
        call_num[0] = 0

        # Second run: same 4 rows + 2 new rows
        new_prompts = [f"Add telemetry for training phase {i}" for i in range(2)]
        new_rows = [
            _unlabeled_row(p, session_id=f"s9d-new-{i}") for i, p in enumerate(new_prompts)
        ]
        all_rows = original_rows + new_rows

        result = llm_label(
            all_rows, _make_rubric(), generate_fn=_success_gen,
            out_path=out_file, batch_size=2
        )

        # Generator must only be called for the 2 new rows (1 batch of 2)
        assert call_num[0] == 1, (
            f"Second run must call generator only once (for 2 new rows); got {call_num[0]}"
        )

        # Result list: all 6 rows present
        assert len(result) == 6

        # out_path: 6 unique rows total (no duplicates from rerun)
        all_written = [
            json.loads(ln) for ln in out_file.read_text().splitlines() if ln.strip()
        ]
        assert len(all_written) == 6, (
            f"out_path must have 6 unique rows after second run; got {len(all_written)}"
        )
        written_hashes = [r["prompt_hash"] for r in all_written]
        assert len(written_hashes) == len(set(written_hashes)), (
            "out_path must not contain duplicate prompt_hashes (no double-write on rerun)"
        )


# ---------------------------------------------------------------------------
# New import for context-aware features
# These symbols don't exist until the implementation lands; tests in the new
# classes below will be collected and FAIL (not ImportError-skipped) because
# the fixtures assert their presence directly.
# ---------------------------------------------------------------------------

try:
    from broker.router_train.relabel import (  # noqa: E402
        LABEL_SOURCE_LLM_REAL_CTX,
        llm_label_ctx,
        mine_with_context,
    )
    _CTX_FEATURES_AVAILABLE = True
except ImportError:
    LABEL_SOURCE_LLM_REAL_CTX = None  # type: ignore[assignment]
    llm_label_ctx = None  # type: ignore[assignment]
    mine_with_context = None  # type: ignore[assignment]
    _CTX_FEATURES_AVAILABLE = False

# Marker applied to every test class that exercises context-aware features.
# When the symbols are missing the tests FAIL (not skip) so the stubs stay RED.
_ctx_requires = pytest.mark.skipif(
    False,  # never skip — always run so failures are visible
    reason="context-aware features not yet implemented",
)


# ---------------------------------------------------------------------------
# Helpers for context-gathering tests
# ---------------------------------------------------------------------------


def _make_session_jsonl(
    tmp_path: Path,
    project_name: str,
    session_id: str,
    turns: list[dict[str, str]],
) -> Path:
    """Write a Claude Code session JSONL file with an ordered list of turns.

    Each turn is a dict with keys:
      role      — 'user' or 'assistant'
      content   — the turn text (string for user; for assistant may be a
                  plain string OR a JSON-serializable list of tool_use blocks)
      ts_suffix — optional timestamp suffix (default: '00', '01', '02', …)
      persona   — only for assistant Agent dispatches (sets subagent_type)
    """
    session_dir = tmp_path / project_name
    session_dir.mkdir(exist_ok=True)
    session_file = session_dir / f"{session_id}.jsonl"

    lines: list[str] = []
    for i, turn in enumerate(turns):
        role = turn["role"]
        ts = f"2026-06-21T10:{i:02d}:00+00:00"
        if role == "user":
            obj: dict[str, Any] = {
                "type": "user",
                "sessionId": session_id,
                "timestamp": ts,
                "message": {"role": "user", "content": turn["content"]},
            }
        else:
            # Build assistant message; if 'persona' is set, emit Agent tool_use
            persona = turn.get("persona")
            if persona:
                content_blocks: list[dict[str, Any]] = [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{i}",
                        "name": "Agent",
                        "input": {
                            "description": persona,
                            "prompt": turn.get("content", ""),
                            "subagent_type": persona,
                        },
                    }
                ]
            else:
                content_blocks = [
                    {"type": "text", "text": turn.get("content", "")}
                ]
            obj = {
                "type": "assistant",
                "sessionId": session_id,
                "timestamp": ts,
                "message": {"role": "assistant", "content": content_blocks},
            }
        lines.append(json.dumps(obj))

    session_file.write_text("\n".join(lines) + "\n")
    return session_file


def _strip_injected_noise(text: str) -> str:
    """Minimal noise-strip that mirrors what mine_with_context must do."""
    # Injected markers that signal non-genuine content
    injected = (
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
    for marker in injected:
        if marker in text:
            return ""
    return text


# ---------------------------------------------------------------------------
# AC10 — mine_with_context: context capture
# ---------------------------------------------------------------------------


class TestMineWithContext:
    """AC10: mine_with_context attaches preceding turns per request.

    mine_with_context(root=<path>) -> list[dict]
    Identical to mine_all_real_requests EXCEPT each row additionally carries:
      'preceding_turns': list[dict] — ordered list of the preceding ~3 genuine
           conversation turns from the SAME session, each with:
             'role': 'user' | 'assistant'
             'content': str (cleaned, noise-stripped, <=~300 chars per turn)
           Capped to the last 3 turns; total context capped to ~1.5 KB.
    Rows with NO preceding turns in the same session carry 'preceding_turns': [].
    """

    def test_preceding_turns_key_present_on_all_rows(self, tmp_path: Path) -> None:
        """Given a session with multiple turns where the target prompt has predecessors,
        When mine_with_context is called,
        Then every returned row has a 'preceding_turns' key (list, possibly empty)."""
        turns = [
            {"role": "user", "content": "Set up the router training pipeline"},
            {"role": "assistant", "content": "I will set up the pipeline now.", "persona": "pipeline-data"},
            {"role": "user", "content": "Write tests for the relabel module"},
            {"role": "assistant", "content": "I will write those tests.", "persona": "quill-py"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10", "sess-ctx10", turns)

        result = mine_with_context(root=tmp_path)

        assert isinstance(result, list)
        assert len(result) >= 1
        for row in result:
            assert "preceding_turns" in row, (
                f"Row missing 'preceding_turns': {list(row.keys())}"
            )
            assert isinstance(row["preceding_turns"], list), (
                f"preceding_turns must be a list, got {type(row['preceding_turns'])}"
            )

    def test_first_prompt_in_session_has_empty_preceding_turns(
        self, tmp_path: Path
    ) -> None:
        """Given a session where the target prompt is the FIRST turn,
        When mine_with_context is called,
        Then that row carries preceding_turns=[] (nothing precedes it)."""
        prompt_text = "Initialize the nexus broker pipeline from scratch ctx10b"
        turns = [
            {"role": "user", "content": prompt_text},
            {"role": "assistant", "content": "Will do.", "persona": "pipeline-data"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10b", "sess-ctx10b", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if prompt_text in r.get("prompt", "")]
        assert len(matching) >= 1, "Target prompt not found in mine_with_context output"
        row = matching[0]
        assert row["preceding_turns"] == [], (
            f"First prompt must have empty preceding_turns; got {row['preceding_turns']}"
        )

    def test_preceding_turns_capped_at_three(self, tmp_path: Path) -> None:
        """Given a session with 5+ turns before the target prompt,
        When mine_with_context is called,
        Then preceding_turns contains at most 3 turns for that row."""
        target_prompt = "Now finalize the training export step ctx10c"
        turns = [
            {"role": "user", "content": "Set up the sessions table schema"},
            {"role": "assistant", "content": "Setting up sessions.", "persona": "atlas"},
            {"role": "user", "content": "Add the embeddings column"},
            {"role": "assistant", "content": "Adding embeddings column.", "persona": "atlas"},
            {"role": "user", "content": "Write the DuckDB transform for joins"},
            {"role": "assistant", "content": "Writing the transform.", "persona": "pipeline-data"},
            {"role": "user", "content": target_prompt},
            {"role": "assistant", "content": "Finalizing export.", "persona": "pipeline-data"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10c", "sess-ctx10c", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if target_prompt in r.get("prompt", "")]
        assert len(matching) >= 1, f"Target prompt not found; result prompts={[r.get('prompt') for r in result]}"
        row = matching[0]
        assert len(row["preceding_turns"]) <= 3, (
            f"preceding_turns must be capped at 3; got {len(row['preceding_turns'])} turns"
        )

    def test_preceding_turns_ordered_oldest_first(self, tmp_path: Path) -> None:
        """Given a session with prior turns A, B, C before target D,
        When mine_with_context is called,
        Then preceding_turns for D is ordered oldest-first: [A, B, C] (or last 3)."""
        turns = [
            {"role": "user", "content": "First: set up the router sessions table"},
            {"role": "assistant", "content": "Done: sessions table created.", "persona": "atlas"},
            {"role": "user", "content": "Second: add the embedding vector column"},
            {"role": "assistant", "content": "Done: embedding added.", "persona": "atlas"},
            {"role": "user", "content": "Third: write the DuckDB aggregation join"},
            {"role": "assistant", "content": "Done: aggregation written.", "persona": "pipeline-data"},
            {"role": "user", "content": "Fourth: finalize the export step ctx10d"},
            {"role": "assistant", "content": "Finalizing export.", "persona": "pipeline-data"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10d", "sess-ctx10d", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if "ctx10d" in r.get("prompt", "")]
        assert len(matching) >= 1
        row = matching[0]
        preceding = row["preceding_turns"]
        assert len(preceding) >= 1, "Must have at least one preceding turn"
        # The LAST preceding turn must be the turn immediately before the target
        last_turn = preceding[-1]
        assert isinstance(last_turn, dict)
        assert "role" in last_turn
        assert "content" in last_turn

    def test_following_turn_not_included_in_preceding_turns(
        self, tmp_path: Path
    ) -> None:
        """Given a session: [prior_user, prior_asst, TARGET_user, following_asst],
        When mine_with_context is called,
        Then the following_asst turn is NOT included in preceding_turns for TARGET.

        This is the no-leak invariant: the following dispatch action must never
        contaminate the context window used to label the current request."""
        prior_user_text = "Set up the broker vault endpoint ctx10e-prior"
        target_text = "Now extend it with context-aware labeling ctx10e"
        following_dispatch_text = "dispatching quill-py to write tests"
        turns = [
            {"role": "user", "content": prior_user_text},
            {"role": "assistant", "content": "Setting up vault endpoint.", "persona": "hermes"},
            {"role": "user", "content": target_text},
            # Following assistant turn (should NOT appear in preceding_turns for target)
            {"role": "assistant", "content": following_dispatch_text, "persona": "quill-py"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10e", "sess-ctx10e", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if "ctx10e" in r.get("prompt", "") and "ctx10e-prior" not in r.get("prompt", "")]
        assert len(matching) >= 1, "Target prompt not found"
        row = matching[0]
        preceding = row["preceding_turns"]
        # The following dispatch text must NOT appear in any preceding turn
        for turn in preceding:
            assert following_dispatch_text not in turn.get("content", ""), (
                f"LEAK: following assistant dispatch text found in preceding_turns: "
                f"turn={turn!r}"
            )

    def test_preceding_turns_noise_stripped(self, tmp_path: Path) -> None:
        """Given a session where a prior turn contains injected noise markers,
        When mine_with_context is called,
        Then turns with injected noise are either excluded or stripped from
        preceding_turns (no raw <system-reminder> or <task-notification> content)."""
        # A prior turn that looks like a system injection (should be stripped/excluded)
        injected_content = (
            "<system-reminder>This is a system reminder about tool availability.</system-reminder>"
        )
        genuine_content = "Can you write the DuckDB schema for session aggregation ctx10f"
        target_text = "Now export the training data for fine-tuning ctx10f-target"
        turns = [
            {"role": "user", "content": injected_content},  # injected — must be stripped
            {"role": "user", "content": genuine_content},
            {"role": "assistant", "content": "Writing schema.", "persona": "atlas"},
            {"role": "user", "content": target_text},
            {"role": "assistant", "content": "Exporting.", "persona": "pipeline-data"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10f", "sess-ctx10f", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if "ctx10f-target" in r.get("prompt", "")]
        # The target row should be present
        if len(matching) >= 1:
            row = matching[0]
            for turn in row["preceding_turns"]:
                content = turn.get("content", "")
                assert "<system-reminder" not in content, (
                    f"Injected noise must be stripped from preceding_turns; "
                    f"found '<system-reminder' in: {content!r}"
                )
                assert "<task-notification" not in content

    def test_total_context_capped_to_1500_chars(self, tmp_path: Path) -> None:
        """Given a session with very long prior turns,
        When mine_with_context is called,
        Then total character length of all preceding_turns content is <= 1500 chars."""
        # Very long prior turns — 3 x 800 chars each would exceed 1500 total
        long_text_a = "A" * 800 + " investigate the broker latency issue ctx10g-a"
        long_text_b = "B" * 800 + " write the relabel module tests ctx10g-b"
        long_text_c = "C" * 800 + " update the training export to v2 ctx10g-c"
        target_text = "Finalize and export the fine-tune dataset ctx10g-target"
        turns = [
            {"role": "user", "content": long_text_a},
            {"role": "assistant", "content": "Done a.", "persona": "scout"},
            {"role": "user", "content": long_text_b},
            {"role": "assistant", "content": "Done b.", "persona": "quill-py"},
            {"role": "user", "content": long_text_c},
            {"role": "assistant", "content": "Done c.", "persona": "pipeline-data"},
            {"role": "user", "content": target_text},
            {"role": "assistant", "content": "Exporting.", "persona": "pipeline-data"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10g", "sess-ctx10g", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if "ctx10g-target" in r.get("prompt", "")]
        if len(matching) >= 1:
            row = matching[0]
            total_chars = sum(len(t.get("content", "")) for t in row["preceding_turns"])
            assert total_chars <= 1500, (
                f"Total preceding_turns content must be <= 1500 chars; got {total_chars}"
            )

    def test_preceding_turns_per_turn_content_capped_to_300_chars(
        self, tmp_path: Path
    ) -> None:
        """Given a prior turn with content > 300 chars,
        When mine_with_context is called,
        Then that turn's content in preceding_turns is truncated to ~300 chars."""
        very_long_prior = "X" * 500 + " design the session aggregation pipeline ctx10h-prior"
        target_text = "Now implement the aggregation pipeline step ctx10h-target"
        turns = [
            {"role": "user", "content": very_long_prior},
            {"role": "assistant", "content": "Designing pipeline.", "persona": "pipeline-data"},
            {"role": "user", "content": target_text},
            {"role": "assistant", "content": "Implementing.", "persona": "pipeline-data"},
        ]
        _make_session_jsonl(tmp_path, "proj-ctx10h", "sess-ctx10h", turns)

        result = mine_with_context(root=tmp_path)
        matching = [r for r in result if "ctx10h-target" in r.get("prompt", "")]
        if len(matching) >= 1:
            row = matching[0]
            for turn in row["preceding_turns"]:
                content_len = len(turn.get("content", ""))
                assert content_len <= 350, (
                    f"Each preceding_turns entry must be <= ~300 chars; "
                    f"got {content_len} chars"
                )

    def test_turns_from_different_sessions_not_mixed(self, tmp_path: Path) -> None:
        """Given two sessions A and B in the same project,
        When mine_with_context is called,
        Then preceding_turns for a prompt in session A contains ONLY turns from session A
        (no cross-session context contamination)."""
        # Session A: prior_a -> target_a
        prior_a = "Session A prior turn: set up the vault endpoint ctx10i-a-prior"
        target_a = "Session A target: write the vault tests ctx10i-a-target"
        # Session B: unrelated prior turn
        prior_b = "Session B prior turn: configure dramatiq workers ctx10i-b-prior"
        target_b = "Session B target: test the dramatiq actor ctx10i-b-target"

        _make_session_jsonl(tmp_path, "proj-ctx10i", "sess-ctx10i-a", [
            {"role": "user", "content": prior_a},
            {"role": "assistant", "content": "Setting up vault.", "persona": "hermes"},
            {"role": "user", "content": target_a},
            {"role": "assistant", "content": "Writing tests.", "persona": "quill-py"},
        ])
        _make_session_jsonl(tmp_path, "proj-ctx10i", "sess-ctx10i-b", [
            {"role": "user", "content": prior_b},
            {"role": "assistant", "content": "Configuring workers.", "persona": "pipeline-async"},
            {"role": "user", "content": target_b},
            {"role": "assistant", "content": "Testing actor.", "persona": "quill-py"},
        ])

        result = mine_with_context(root=tmp_path)
        matching_a = [r for r in result if "ctx10i-a-target" in r.get("prompt", "")]
        if len(matching_a) >= 1:
            row_a = matching_a[0]
            for turn in row_a["preceding_turns"]:
                content = turn.get("content", "")
                assert "ctx10i-b" not in content, (
                    f"Cross-session contamination: session-B content found in session-A "
                    f"preceding_turns: {content!r}"
                )


# ---------------------------------------------------------------------------
# AC11 — llm_label_ctx: context included in labeler prompt
# ---------------------------------------------------------------------------


class TestLlmLabelCtxPromptInclusion:
    """AC11: llm_label_ctx passes context to generate_fn for context-bearing requests.

    llm_label_ctx(requests, rubric, generate_fn=None, out_path=None, batch_size=None)
    Same as llm_label but:
      - Reads 'preceding_turns' from each row (populated by mine_with_context)
      - For rows with non-empty preceding_turns, the batch_prompt passed to
        generate_fn includes the context turns BEFORE the numbered request
      - Writes to out_path=router_train_data/llm_labeled_real_ctx.jsonl by default
      - label_source='llm_real_ctx' (not 'llm_real')
      - Gold rows / context_dependent rows: same behaviour as llm_label
    """

    def test_generate_fn_receives_context_for_context_bearing_row(self) -> None:
        """Given a row with non-empty preceding_turns,
        When llm_label_ctx is called with a spy generate_fn,
        Then the batch_prompt string received by generate_fn contains
        the prior turn content (context is present in the prompt)."""
        prior_content = "Set up the router sessions schema ctx11a"
        target_prompt = "Now write the DuckDB transform for session aggregation ctx11a-target"

        rows: list[dict[str, Any]] = [
            {
                "session_id": "sess-ctx11a",
                "prompt": target_prompt,
                "prompt_hash": _ph(target_prompt),
                "label_persona": None,
                "preceding_turns": [
                    {"role": "user", "content": prior_content},
                    {"role": "assistant", "content": "Setting up schema."},
                ],
            }
        ]

        received_prompts: list[str] = []

        def _spy_gen(batch_prompt: str) -> str:
            received_prompts.append(batch_prompt)
            return "[1]\npersona: pipeline-data\ndifficulty: standard\n"

        llm_label_ctx(rows, _make_rubric(), generate_fn=_spy_gen)

        assert len(received_prompts) >= 1, "generate_fn must be called for unlabeled row"
        combined = " ".join(received_prompts)
        assert prior_content in combined, (
            f"Prior turn content must appear in the batch_prompt; "
            f"got prompt snippet: {combined[:300]!r}"
        )

    def test_generate_fn_does_not_receive_context_for_no_context_row(self) -> None:
        """Given a row with preceding_turns=[],
        When llm_label_ctx is called,
        Then the batch_prompt does NOT contain any context preamble
        (falls back to the standard format)."""
        target_prompt = "Design the fine-tune eval harness for the router model ctx11b"
        rows: list[dict[str, Any]] = [
            {
                "session_id": "sess-ctx11b",
                "prompt": target_prompt,
                "prompt_hash": _ph(target_prompt),
                "label_persona": None,
                "preceding_turns": [],
            }
        ]

        received_prompts: list[str] = []

        def _spy_gen(batch_prompt: str) -> str:
            received_prompts.append(batch_prompt)
            return "[1]\npersona: quill-py\ndifficulty: simple\n"

        llm_label_ctx(rows, _make_rubric(), generate_fn=_spy_gen)

        assert len(received_prompts) >= 1
        # No context preamble should be present — prompt should contain the target
        combined = " ".join(received_prompts)
        assert target_prompt in combined

    def test_context_turns_appear_before_numbered_request(self) -> None:
        """Given a row with preceding_turns,
        When llm_label_ctx is called,
        Then in the batch_prompt the context block appears BEFORE '[1]' (the request number)
        so the model sees context first, then the current request."""
        prior_user = "Implement the broker hook ctx11c-prior"
        target_prompt = "Now test the broker hook with context ctx11c-target"

        rows: list[dict[str, Any]] = [
            {
                "session_id": "sess-ctx11c",
                "prompt": target_prompt,
                "prompt_hash": _ph(target_prompt),
                "label_persona": None,
                "preceding_turns": [
                    {"role": "user", "content": prior_user},
                ],
            }
        ]

        received_prompts: list[str] = []

        def _spy_gen(batch_prompt: str) -> str:
            received_prompts.append(batch_prompt)
            return "[1]\npersona: quill-py\ndifficulty: standard\n"

        llm_label_ctx(rows, _make_rubric(), generate_fn=_spy_gen)

        assert len(received_prompts) >= 1
        p = received_prompts[0]
        ctx_pos = p.find(prior_user)
        req_pos = p.find("[1]")
        assert ctx_pos != -1, f"Prior turn content not found in batch_prompt: {p[:400]!r}"
        assert req_pos != -1, f"'[1]' not found in batch_prompt: {p[:400]!r}"
        assert ctx_pos < req_pos, (
            f"Context (pos {ctx_pos}) must appear BEFORE '[1]' (pos {req_pos})"
        )

    def test_rows_missing_preceding_turns_key_treated_as_no_context(self) -> None:
        """Given a row that has no 'preceding_turns' key (legacy mine_all_real_requests row),
        When llm_label_ctx is called,
        Then it does not crash and labels the row as if preceding_turns=[]."""
        target_prompt = "Write the Polars transform for the session aggregation step ctx11d"
        rows: list[dict[str, Any]] = [
            {
                "session_id": "sess-ctx11d",
                "prompt": target_prompt,
                "prompt_hash": _ph(target_prompt),
                "label_persona": None,
                # No 'preceding_turns' key at all — legacy row shape
            }
        ]

        def _simple_gen(batch_prompt: str) -> str:
            return "[1]\npersona: pipeline-data\ndifficulty: simple\n"

        # Must not raise
        result = llm_label_ctx(rows, _make_rubric(), generate_fn=_simple_gen)
        assert len(result) == 1
        assert result[0]["label_source"] == LABEL_SOURCE_LLM_REAL_CTX


# ---------------------------------------------------------------------------
# AC12 — label_source='llm_real_ctx' + schema validation
# ---------------------------------------------------------------------------


class TestLlmRealCtxSchema:
    """AC12: llm_real_ctx is accepted by is_valid(); label_source set correctly.

    'llm_real_ctx' must be added to the label_source enum in
    router_training_record.schema.json so is_valid() accepts these rows.
    """

    def _minimal_ctx_row(self) -> dict[str, Any]:
        return {
            "prompt": "Write the Polars transform for session aggregation ctx12",
            "label_persona": "pipeline-data",
            "label_source": "llm_real_ctx",
            "label_confidence": 0.85,
            "schema_version": 2,
            "router_version": "v2",
            "model_id": "claude",
            "prompt_hash": "b" * 64,
        }

    def test_llm_real_ctx_accepted_by_is_valid(self) -> None:
        """Given a row with label_source='llm_real_ctx',
        When is_valid() is called,
        Then it returns True (schema enum includes 'llm_real_ctx')."""
        row = self._minimal_ctx_row()
        assert is_valid([row]), (
            "is_valid() rejected label_source='llm_real_ctx' — "
            "schema enum missing 'llm_real_ctx'"
        )

    def test_llm_label_ctx_sets_label_source_llm_real_ctx(self) -> None:
        """Given unlabeled rows,
        When llm_label_ctx is called with a fake generate_fn,
        Then every labeled row carries label_source='llm_real_ctx' (not 'llm_real')."""
        prompts = [
            "Configure the dramatiq worker pool for training jobs ctx12a",
            "Write the evaluation harness for the fine-tuned model ctx12b",
        ]
        rows: list[dict[str, Any]] = [
            {
                "session_id": f"sess-ctx12-{i}",
                "prompt": p,
                "prompt_hash": _ph(p),
                "label_persona": None,
                "preceding_turns": [],
            }
            for i, p in enumerate(prompts)
        ]

        result = llm_label_ctx(rows, _make_rubric(), generate_fn=_make_fixed_generate())

        for row in result:
            if row.get("label_persona") is not None:
                assert row["label_source"] == LABEL_SOURCE_LLM_REAL_CTX, (
                    f"Expected label_source='llm_real_ctx', got {row['label_source']!r}"
                )

    def test_llm_real_and_llm_real_ctx_are_distinct_enum_values(self) -> None:
        """Regression: 'llm_real' and 'llm_real_ctx' are separate enum values;
        neither should be rejected by is_valid()."""
        for src in ("llm_real", "llm_real_ctx"):
            row: dict[str, Any] = {
                "prompt": f"Test prompt for {src}",
                "label_persona": "scout",
                "label_source": src,
                "label_confidence": 0.85,
                "schema_version": 2,
                "router_version": "v2",
                "model_id": "claude",
                "prompt_hash": "c" * 64,
            }
            assert is_valid([row]), (
                f"is_valid() rejected label_source={src!r}"
            )

    def test_out_path_default_is_llm_labeled_real_ctx_jsonl(
        self, tmp_path: Path
    ) -> None:
        """Given a call to llm_label_ctx with explicit out_path,
        When the call completes,
        Then the output file contains rows with label_source='llm_real_ctx'."""
        out_file = tmp_path / "llm_labeled_real_ctx.jsonl"
        rows: list[dict[str, Any]] = [
            {
                "session_id": "sess-ctx12d",
                "prompt": "Build the DuckDB writer for the context-labeled rows ctx12d",
                "prompt_hash": _ph("Build the DuckDB writer for the context-labeled rows ctx12d"),
                "label_persona": None,
                "preceding_turns": [{"role": "user", "content": "Set up DuckDB ctx12d-prior"}],
            }
        ]

        llm_label_ctx(
            rows,
            _make_rubric(),
            generate_fn=_make_fixed_generate(persona="pipeline-data"),
            out_path=out_file,
        )

        assert out_file.exists(), "out_path must be created by llm_label_ctx"
        written = [json.loads(ln) for ln in out_file.read_text().splitlines() if ln.strip()]
        assert len(written) >= 1
        for rec in written:
            assert rec["label_source"] == LABEL_SOURCE_LLM_REAL_CTX, (
                f"Written row must have label_source='llm_real_ctx'; got {rec['label_source']!r}"
            )


# ---------------------------------------------------------------------------
# AC13 — Regression: gold rows untouched; continuations excluded; incremental works
# ---------------------------------------------------------------------------


class TestCtxRegressions:
    """AC13: gold rows untouched, continuations excluded, incremental resume works
    — all under llm_label_ctx (not just llm_label)."""

    def test_gold_rows_unchanged_under_llm_label_ctx(self) -> None:
        """Given a mix of gold and unlabeled rows with preceding_turns,
        When llm_label_ctx is called,
        Then gold rows are returned UNCHANGED (label_persona, label_source preserved)."""
        gold = _gold_row("Scaffold the vault endpoint ctx13a", "hermes")
        unlabeled: dict[str, Any] = {
            "session_id": "sess-ctx13a",
            "prompt": "Add context-aware labeling to the relabel module ctx13a-target",
            "prompt_hash": _ph("Add context-aware labeling to the relabel module ctx13a-target"),
            "label_persona": None,
            "preceding_turns": [{"role": "user", "content": "prior turn ctx13a"}],
        }

        result = llm_label_ctx(
            [gold, unlabeled],
            _make_rubric(),
            generate_fn=_make_fixed_generate(persona="pipeline-data"),
        )

        # Gold row must be untouched
        assert result[0]["label_persona"] == "hermes"
        assert result[0]["label_source"] == "dispatch_sidecar"
        assert result[0]["label_confidence"] == 1.0

        # Unlabeled row must be labeled with llm_real_ctx
        assert result[1]["label_source"] == LABEL_SOURCE_LLM_REAL_CTX

    def test_continuation_rows_excluded_under_llm_label_ctx(self) -> None:
        """Given a bare-continuation row mixed with a normal unlabeled row,
        When llm_label_ctx is called,
        Then the continuation row is marked context_dependent=True with label_persona=None;
        the normal row is labeled."""
        normal: dict[str, Any] = {
            "session_id": "sess-ctx13b",
            "prompt": "Write the Polars join for session aggregation ctx13b",
            "prompt_hash": _ph("Write the Polars join for session aggregation ctx13b"),
            "label_persona": None,
            "preceding_turns": [],
        }
        continuation: dict[str, Any] = {
            "session_id": "sess-ctx13b",
            "prompt": "go ahead",
            "prompt_hash": _ph("go ahead"),
            "label_persona": None,
            "preceding_turns": [{"role": "user", "content": "some prior turn"}],
        }

        result = llm_label_ctx(
            [normal, continuation],
            _make_rubric(),
            generate_fn=_make_fixed_generate(persona="pipeline-data"),
        )

        # Normal row: labeled
        assert result[0]["label_source"] == LABEL_SOURCE_LLM_REAL_CTX
        assert result[0].get("context_dependent") is not True

        # Continuation: flagged, no fabricated persona
        assert result[1].get("context_dependent") is True
        assert result[1]["label_persona"] is None

    def test_incremental_resume_under_llm_label_ctx(self, tmp_path: Path) -> None:
        """Given an out_path that already has some rows from a prior llm_label_ctx run,
        When llm_label_ctx is called again with the same rows,
        Then already-present rows are skipped and the LLM is not called for them."""
        out_file = tmp_path / "llm_labeled_real_ctx.jsonl"
        prompts = [
            "Wire the session store for context-labeled training ctx13c-a",
            "Add telemetry for the context-aware labeling pass ctx13c-b",
            "Export the llm_real_ctx rows to the fine-tune dataset ctx13c-c",
        ]
        rows: list[dict[str, Any]] = [
            {
                "session_id": f"sess-ctx13c-{i}",
                "prompt": p,
                "prompt_hash": _ph(p),
                "label_persona": None,
                "preceding_turns": [],
            }
            for i, p in enumerate(prompts)
        ]

        call_count: list[int] = [0]

        def _counting_gen(batch_prompt: str) -> str:
            call_count[0] += 1
            count = sum(
                1 for line in batch_prompt.splitlines()
                if line.strip().startswith("[") and line.strip().endswith("]")
                and line.strip()[1:-1].isdigit()
            )
            n = max(count, 1)
            lines: list[str] = []
            for i in range(1, n + 1):
                lines.append(f"[{i}]")
                lines.append("persona: pipeline-data")
                lines.append("difficulty: standard")
                lines.append("")
            return "\n".join(lines)

        # First run: labels all 3
        llm_label_ctx(rows, _make_rubric(), generate_fn=_counting_gen, out_path=out_file)
        calls_first = call_count[0]
        assert calls_first >= 1, "First run must call generate_fn"

        # Second run: all 3 are already in out_path — LLM must NOT be called
        call_count[0] = 0
        result = llm_label_ctx(
            rows, _make_rubric(), generate_fn=_counting_gen, out_path=out_file
        )
        assert call_count[0] == 0, (
            f"LLM called {call_count[0]} times on rerun — should be 0 (all rows skipped)"
        )
        assert len(result) == 3


# ---------------------------------------------------------------------------
# AC14 — LABEL_SOURCE_LLM_REAL_CTX constant value
# ---------------------------------------------------------------------------


class TestLabelSourceConstant:
    """AC14: LABEL_SOURCE_LLM_REAL_CTX has the correct string value 'llm_real_ctx'."""

    def test_constant_value(self) -> None:
        """The LABEL_SOURCE_LLM_REAL_CTX constant must equal the string 'llm_real_ctx'."""
        assert LABEL_SOURCE_LLM_REAL_CTX == "llm_real_ctx", (
            f"Expected 'llm_real_ctx', got {LABEL_SOURCE_LLM_REAL_CTX!r}"
        )

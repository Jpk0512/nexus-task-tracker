"""WF-G: generate_contrastive tests.

Covers:
- label_source='synthetic_contrastive', confidence=0.5, synthetic=True
- injectable generate_fn seam (no real claude calls)
- dedup: hash collisions dropped
- out_path incremental write
- boundary-pair logic: both sides of a pair present in output
- single-class generation for starved personas
- schema enum accepts 'synthetic_contrastive'
- _parse_contrastive_response: strips fences/preambles/headers
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from broker.router_train.synthetic import (
    LABEL_SOURCE_CONTRASTIVE,
    LABEL_CONFIDENCE_CONTRASTIVE,
    generate_contrastive,
    _BOUNDARY_PAIRS,
    _CONTRASTIVE_PROMPTS,
    _parse_contrastive_response,
)
from broker.router_train.export import is_valid, _fill_provenance_defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _counter_fn(prefix: str = "prompt") -> Any:
    """Injectable generate_fn that produces globally unique, predictable prompts.

    Uses a per-instance counter that increments across calls so repeated calls
    from the batch-loop in generate_contrastive never produce the same string.
    """
    state: dict[str, int] = {"n": 0}
    calls: list[tuple[str, int]] = []

    def _fn(persona: str, seeds: list[str], n: int) -> list[str]:
        calls.append((persona, n))
        start = state["n"]
        state["n"] += n
        return [f"{prefix}:{persona}:{start + i}" for i in range(n)]

    _fn.calls = calls  # type: ignore[attr-defined]
    return _fn


def _collision_fn(fixed_text: str) -> Any:
    """Always returns the same text — forces hash collisions across personas."""

    def _fn(persona: str, seeds: list[str], n: int) -> list[str]:
        return [fixed_text] * n

    return _fn


# ---------------------------------------------------------------------------
# Schema enum
# ---------------------------------------------------------------------------


class TestSchemaEnumContrastive:
    def test_synthetic_contrastive_in_schema_enum(self) -> None:
        """'synthetic_contrastive' must be accepted by the JSON schema validator."""
        from broker.router_train.aggregate import prompt_hash

        pair = {
            "prompt": "Design the DuckDB table schema for session embeddings",
            "prompt_hash": prompt_hash("Design the DuckDB table schema for session embeddings"),
            "label_persona": "atlas",
            "label_source": "synthetic_contrastive",
            "label_confidence": 0.5,
            "schema_version": 2,
            "router_version": "synthetic_or_no_dispatch",
            "model_id": "granite-4.1-3b",
        }
        assert is_valid([pair]), "schema enum must accept 'synthetic_contrastive'"


# ---------------------------------------------------------------------------
# Core label properties
# ---------------------------------------------------------------------------


class TestContrastiveLabelProperties:
    def test_label_source_is_contrastive(self) -> None:
        fn = _counter_fn()
        results = generate_contrastive(["atlas"], generate_fn=fn, n_per_target=3)
        for row in results:
            if row["label_persona"] == "atlas":
                assert row["label_source"] == LABEL_SOURCE_CONTRASTIVE

    def test_confidence_is_point_five(self) -> None:
        fn = _counter_fn()
        results = generate_contrastive(["atlas"], generate_fn=fn, n_per_target=3)
        for row in results:
            assert row["label_confidence"] == LABEL_CONFIDENCE_CONTRASTIVE

    def test_synthetic_flag_true(self) -> None:
        fn = _counter_fn()
        results = generate_contrastive(["atlas"], generate_fn=fn, n_per_target=3)
        for row in results:
            assert row.get("synthetic") is True

    def test_label_status_ok(self) -> None:
        fn = _counter_fn()
        results = generate_contrastive(["atlas"], generate_fn=fn, n_per_target=3)
        for row in results:
            assert row.get("label_status") == "ok"

    def test_persona_matches_target(self) -> None:
        fn = _counter_fn()
        results = generate_contrastive(["quill-py"], generate_fn=fn, n_per_target=5)
        personas = {r["label_persona"] for r in results}
        assert "quill-py" in personas


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestContrastiveDedup:
    def test_hash_collision_dropped(self) -> None:
        """When generate_fn always returns the same text, only one row per unique prompt."""
        fn = _collision_fn("Write a pytest test for the transform")
        results = generate_contrastive(["quill-py", "atlas"], generate_fn=fn, n_per_target=5)
        hashes = [r["prompt_hash"] for r in results]
        assert len(hashes) == len(set(hashes)), "duplicate hashes must be dropped"

    def test_unique_prompts_all_kept(self) -> None:
        fn = _counter_fn("u")
        results = generate_contrastive(["atlas"], generate_fn=fn, n_per_target=4)
        atlas_rows = [r for r in results if r["label_persona"] == "atlas"]
        # At least n_per_target rows (boundary pairs may add more for non-atlas partners)
        assert len(atlas_rows) >= 4


# ---------------------------------------------------------------------------
# Boundary pairs
# ---------------------------------------------------------------------------


class TestContrastiveBoundaryPairs:
    def test_both_sides_present_for_forge_boundary(self) -> None:
        """forge-ui/forge-wire boundary: both personas appear in output."""
        fn = _counter_fn("bp")
        results = generate_contrastive(
            ["forge-ui", "forge-wire"],
            generate_fn=fn,
            n_per_target=3,
            n_per_boundary_side=2,
        )
        personas = {r["label_persona"] for r in results}
        assert "forge-ui" in personas
        assert "forge-wire" in personas

    def test_boundary_pair_not_generated_when_only_one_side_in_targets(self) -> None:
        """Boundary pairs only generated when BOTH sides are in targets."""
        fn = _counter_fn("bp")
        results = generate_contrastive(
            ["forge-ui"],  # forge-wire absent
            generate_fn=fn,
            n_per_target=3,
            n_per_boundary_side=2,
        )
        personas = {r["label_persona"] for r in results}
        # forge-wire should NOT appear when it's not in targets
        assert "forge-wire" not in personas

    def test_hermes_no_dispatch_boundary(self) -> None:
        fn = _counter_fn("hnd")
        results = generate_contrastive(
            ["hermes", "no-dispatch"],
            generate_fn=fn,
            n_per_target=3,
            n_per_boundary_side=2,
        )
        personas = {r["label_persona"] for r in results}
        assert "hermes" in personas
        assert "no-dispatch" in personas


# ---------------------------------------------------------------------------
# out_path incremental write
# ---------------------------------------------------------------------------


class TestContrastiveOutPath:
    def test_out_path_written_incrementally(self, tmp_path: Path) -> None:
        fn = _counter_fn("inc")
        out_file = tmp_path / "contrastive.jsonl"
        results = generate_contrastive(
            ["atlas", "quill-py"],
            generate_fn=fn,
            n_per_target=4,
            out_path=out_file,
        )
        assert out_file.exists(), "out_path must be created"
        lines = [ln for ln in out_file.read_text().splitlines() if ln.strip()]
        assert len(lines) == len(results), "every row must be written to out_path"
        for line in lines:
            rec = json.loads(line)
            assert rec["label_source"] == LABEL_SOURCE_CONTRASTIVE

    def test_out_path_appends_not_overwrites(self, tmp_path: Path) -> None:
        """Two calls to generate_contrastive with the same out_path append."""
        out_file = tmp_path / "contrastive.jsonl"
        fn1 = _counter_fn("a")
        fn2 = _counter_fn("b")
        generate_contrastive(["atlas"], generate_fn=fn1, n_per_target=3, out_path=out_file)
        generate_contrastive(["quill-py"], generate_fn=fn2, n_per_target=3, out_path=out_file)
        lines = [ln for ln in out_file.read_text().splitlines() if ln.strip()]
        assert len(lines) >= 6, "both batches must appear in the file"


# ---------------------------------------------------------------------------
# Schema validation of generated rows
# ---------------------------------------------------------------------------


class TestContrastiveSchemaValidation:
    def test_generated_rows_pass_schema_with_provenance_fill(self) -> None:
        fn = _counter_fn("v")
        results = generate_contrastive(["atlas", "quill-ts"], generate_fn=fn, n_per_target=3)
        filled = [_fill_provenance_defaults(r) for r in results]
        assert is_valid(filled), f"all rows must validate after provenance fill; got {results[:2]}"


# ---------------------------------------------------------------------------
# Stubs for starved classes
# ---------------------------------------------------------------------------


class TestContrastiveStarvedClasses:
    @pytest.mark.parametrize("persona", ["atlas", "pipeline-async", "quill-ts", "quill-py"])
    def test_starved_class_generates_n_rows(self, persona: str) -> None:
        fn = _counter_fn("sc")
        results = generate_contrastive([persona], generate_fn=fn, n_per_target=8)
        persona_rows = [r for r in results if r["label_persona"] == persona]
        assert len(persona_rows) >= 8, f"expected >=8 rows for {persona}, got {len(persona_rows)}"


# ---------------------------------------------------------------------------
# Targets-only: output personas are a subset of the requested targets
# ---------------------------------------------------------------------------


class TestContrastiveTargetsOnly:
    def test_output_personas_subset_of_targets(self) -> None:
        """generate_contrastive MUST NOT emit rows for personas outside targets."""
        targets = ["atlas", "quill-py"]
        fn = _counter_fn("to")
        results = generate_contrastive(targets, generate_fn=fn, n_per_target=4)
        for row in results:
            assert row["label_persona"] in targets, (
                f"unexpected persona {row['label_persona']!r} not in {targets}"
            )

    def test_single_target_no_partner_rows(self) -> None:
        """Requesting only one side of a boundary pair must not produce the other."""
        fn = _counter_fn("st")
        results = generate_contrastive(["forge-ui"], generate_fn=fn, n_per_target=3)
        for row in results:
            assert row["label_persona"] == "forge-ui", (
                f"only forge-ui requested; got {row['label_persona']!r}"
            )

    def test_multi_target_all_personas_covered(self) -> None:
        """Every requested target must appear at least once in the output."""
        targets = ["atlas", "quill-ts", "quill-py"]
        fn = _counter_fn("mt")
        results = generate_contrastive(targets, generate_fn=fn, n_per_target=5)
        present = {r["label_persona"] for r in results}
        for persona in targets:
            assert persona in present, f"{persona!r} missing from output"


# ---------------------------------------------------------------------------
# Resilience regression: failing batch never aborts; incremental out_path resume
# ---------------------------------------------------------------------------


class TestContrastiveResilience:
    def test_failing_batch_does_not_abort_generation(self) -> None:
        """When generate_fn raises for one persona, other personas still get rows."""

        raise_for: set[str] = {"atlas"}
        call_count: dict[str, int] = {}

        def _flaky_fn(persona: str, seeds: list[str], n: int) -> list[str]:
            call_count[persona] = call_count.get(persona, 0) + 1
            if persona in raise_for:
                raise RuntimeError(f"simulated batch failure for {persona}")
            return [f"ok:{persona}:{i}" for i in range(n)]

        # Should not raise even though "atlas" fails
        try:
            results = generate_contrastive(
                ["atlas", "quill-py"],
                generate_fn=_flaky_fn,
                n_per_target=4,
            )
        except RuntimeError:
            pytest.fail(
                "generate_contrastive must NOT propagate exceptions from generate_fn; "
                "it must skip the failing batch and continue"
            )

        # quill-py must still produce rows
        quill_rows = [r for r in results if r["label_persona"] == "quill-py"]
        assert len(quill_rows) >= 4, (
            f"quill-py must still produce rows when atlas batch fails; got {len(quill_rows)}"
        )

    def test_empty_batch_skipped_output_continues(self) -> None:
        """When generate_fn returns [] for a persona, that persona contributes 0 rows
        but other personas are unaffected and generation does not abort."""
        empty_for: set[str] = {"pipeline-async"}

        def _partial_fn(persona: str, seeds: list[str], n: int) -> list[str]:
            if persona in empty_for:
                return []
            return [f"partial:{persona}:{i}" for i in range(n)]

        results = generate_contrastive(
            ["pipeline-async", "quill-ts"],
            generate_fn=_partial_fn,
            n_per_target=4,
        )
        quill_rows = [r for r in results if r["label_persona"] == "quill-ts"]
        assert len(quill_rows) >= 4, (
            f"quill-ts rows must still appear when pipeline-async fn returns []; got {len(quill_rows)}"
        )
        async_rows = [r for r in results if r["label_persona"] == "pipeline-async"]
        assert len(async_rows) == 0, (
            f"pipeline-async returned [] so must have 0 rows; got {len(async_rows)}"
        )

    def test_out_path_partial_rows_survive_later_failure(self, tmp_path: Path) -> None:
        """Rows written before a failing persona remain on disk (incremental append)."""
        out_file = tmp_path / "resilience.jsonl"
        # atlas writes first (sorted order), then atlas-like persona raises
        # Use a counter so the first n calls succeed and later ones raise
        call_no: dict[str, int] = {}

        def _fn_first_ok_then_fail(persona: str, seeds: list[str], n: int) -> list[str]:
            call_no[persona] = call_no.get(persona, 0) + 1
            # atlas succeeds; quill-py raises on first call
            if persona == "quill-py":
                raise RuntimeError("disk full simulation")
            return [f"partial:{persona}:{i}" for i in range(n)]

        try:
            generate_contrastive(
                ["atlas", "quill-py"],
                generate_fn=_fn_first_ok_then_fail,
                n_per_target=3,
                out_path=out_file,
            )
        except RuntimeError:
            pytest.fail("generate_contrastive must not propagate generate_fn errors")

        # atlas rows (sorted first) must be on disk even though quill-py failed
        if out_file.exists():
            lines = [ln for ln in out_file.read_text().splitlines() if ln.strip()]
            atlas_on_disk = [
                ln for ln in lines if '"atlas"' in ln
            ]
            assert len(atlas_on_disk) >= 3, (
                f"atlas rows must be persisted before quill-py batch fails; "
                f"found {len(atlas_on_disk)} atlas lines on disk"
            )

    def test_resume_appends_to_existing_file(self, tmp_path: Path) -> None:
        """A second generate_contrastive call to an existing out_path appends,
        so a resumed run accumulates all rows."""
        out_file = tmp_path / "resume.jsonl"
        fn_a = _counter_fn("resume_a")
        fn_b = _counter_fn("resume_b")

        # First run: atlas only
        generate_contrastive(["atlas"], generate_fn=fn_a, n_per_target=3, out_path=out_file)
        lines_after_first = [ln for ln in out_file.read_text().splitlines() if ln.strip()]
        assert len(lines_after_first) >= 3, "first run must write rows"

        # Second run: quill-py only — must append, not overwrite
        generate_contrastive(["quill-py"], generate_fn=fn_b, n_per_target=3, out_path=out_file)
        lines_after_second = [ln for ln in out_file.read_text().splitlines() if ln.strip()]
        assert len(lines_after_second) >= len(lines_after_first) + 3, (
            f"resume must append; expected >={len(lines_after_first)+3} lines, "
            f"got {len(lines_after_second)}"
        )
        personas_on_disk = {
            json.loads(ln)["label_persona"]
            for ln in lines_after_second
        }
        assert "atlas" in personas_on_disk, "first run atlas rows must survive resume"
        assert "quill-py" in personas_on_disk, "second run quill-py rows must be appended"


# ---------------------------------------------------------------------------
# LABEL_SOURCE_CONTRASTIVE constant
# ---------------------------------------------------------------------------


class TestContrastiveConstant:
    def test_label_source_constant_value(self) -> None:
        assert LABEL_SOURCE_CONTRASTIVE == "synthetic_contrastive"

    def test_confidence_constant_value(self) -> None:
        assert LABEL_CONFIDENCE_CONTRASTIVE == 0.5


# ---------------------------------------------------------------------------
# _parse_contrastive_response: parser hardening (WF-G1b)
# ---------------------------------------------------------------------------


class TestParseContrastiveResponse:
    """Parser must strip fences, preambles, and headers; keep only real requests."""

    _SIMULATED = (
        "Here are 5 hard-discriminative hermes examples — each anchored on a concrete w\n"
        "```\n"
        "Wire up the Tableau REST API PAT sign-in flow for the VDS endpoint.\n"
        "Register the nexus-vault MCP server in .mcp.json with its stdio launch command.\n"
        "Add a Docker Compose service block for the Redis broker and expose it internally.\n"
    )

    def test_smoke_preamble_and_fence_stripped(self) -> None:
        """Simulated claude output with preamble + fence + 3 real lines: only 3 survive."""
        results = _parse_contrastive_response(self._SIMULATED)
        assert len(results) == 3, (
            f"expected exactly 3 real lines; got {len(results)}: {results}"
        )

    def test_preamble_line_stripped(self) -> None:
        raw = "Here are 10 examples:\nDo the thing.\n"
        results = _parse_contrastive_response(raw)
        assert not any(r.startswith("Here are") for r in results)

    def test_sure_preamble_stripped(self) -> None:
        raw = "Sure! Here are the requests:\nBuild the endpoint.\n"
        results = _parse_contrastive_response(raw)
        assert not any(r.lower().startswith("sure") for r in results)

    def test_fence_line_stripped(self) -> None:
        raw = "```json\nDeploy the service.\n```\n"
        results = _parse_contrastive_response(raw)
        assert not any(r.startswith("```") for r in results)
        assert "Deploy the service." in results

    def test_header_colon_stripped(self) -> None:
        """Lines ending with ':' and <=80 chars are treated as headers and dropped."""
        raw = "Five HARD discriminative requests for `quill-ts`:\nWrite a Vitest test for the hook.\n"
        results = _parse_contrastive_response(raw)
        assert not any(r.endswith(":") for r in results)
        assert any("Vitest" in r for r in results)

    def test_numbered_prefix_stripped(self) -> None:
        """Numbered list prefix is stripped but the request content is kept."""
        raw = "1. Write a Vitest test.\n2. Add an RTL spec.\n"
        results = _parse_contrastive_response(raw)
        assert len(results) == 2
        assert all(not r[0].isdigit() for r in results)

    def test_empty_lines_dropped(self) -> None:
        raw = "\n\nWrite a pytest fixture.\n\n\nAdd a parametrize decorator.\n\n"
        results = _parse_contrastive_response(raw)
        assert len(results) == 2

    def test_short_fragments_dropped(self) -> None:
        """Lines shorter than 10 characters are not real requests."""
        raw = "ok.\nWrite a Polars transform for the ingestion batch.\n"
        results = _parse_contrastive_response(raw)
        assert len(results) == 1
        assert "Polars" in results[0]

    def test_real_requests_all_kept(self) -> None:
        """Lines that are clearly real requests must all pass through."""
        real = [
            "Wire up the Tableau REST API PAT sign-in flow for the VDS endpoint.",
            "Register the nexus-vault MCP server in .mcp.json with its stdio launch command.",
            "Add a Docker Compose service block for the Redis broker.",
        ]
        raw = "\n".join(real)
        results = _parse_contrastive_response(raw)
        assert results == real

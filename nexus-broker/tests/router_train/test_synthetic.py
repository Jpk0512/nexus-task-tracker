"""WF-C: Synthetic augmentation tests (RED phase).

Tests for broker.router_train.synthetic.generate_synthetic() and the
include_synthetic=True extension on collect_labeled_pairs().

Interface contract:
- QUERY_ROUTABLE eligible set = {scout, forge-ui, forge-wire, pipeline-data,
  pipeline-async, atlas, hermes, palette, quill-ts, quill-py}.
- FLOOR = 50. For each eligible persona with < 50 real labels generate
  (50 - current) synthetic pairs, capped at max_per_persona=60.
- generate_fn(persona, seeds, n) -> list[str] is injectable; tests use a
  deterministic fake.
- Synthetic pairs: label_source='synthetic', label_confidence=0.5,
  label_status='ok', synthetic=True, seed_prompt_hash=<real seed hash>.
- DEDUP: collisions with real prompt_hash are dropped.
- collect_labeled_pairs(include_synthetic=True) unions the persisted artifact;
  real rows always beat synthetic rows on a hash collision.
"""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Import under test — will ImportError in RED phase (module doesn't exist yet)
# ---------------------------------------------------------------------------
from broker.router_train.synthetic import generate_synthetic  # noqa: E402
from broker.router_train.aggregate import collect_labeled_pairs, prompt_hash


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ELIGIBLE_PERSONAS: frozenset[str] = frozenset(
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

EXCLUDED_LABELS: frozenset[str] = frozenset(
    {
        "forge-ui-pro",
        "forge-wire-pro",
        "pipeline-data-pro",
        "pipeline-async-pro",
        "quill-ts-pro",
        "quill-py-pro",
        "atlas-pro",
        "lens",
        "lens-fast",
        "no-dispatch",
    }
)

FLOOR = 50
# Resolve relative to this test file: tests/router_train/ -> nexus-broker/ -> router_train_data/
SYNTHETIC_ARTIFACT = (
    Path(__file__).resolve().parent.parent.parent
    / "router_train_data"
    / "synthetic_pairs.jsonl"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_real_pair(persona: str, prompt: str, session_id: str = "sess-real") -> dict[str, Any]:
    """Build a minimal real labeled pair as produced by collect_labeled_pairs()."""
    return {
        "session_id": session_id,
        "prompt": prompt,
        "prompt_hash": prompt_hash(prompt),
        "label_persona": persona,
        "label_status": "ok",
        "label_source": "transcript_mining",
        "label_confidence": 0.8,
    }


def _deterministic_generate_fn(
    persona: str,
    seeds: list[str],
    n: int,
    *,
    counter_start: int = 0,
) -> list[str]:
    """A fake generate_fn that emits unique, deterministic prompt strings.

    Returned strings are of the form:
      "SYNTH:<persona>:<counter>"
    where counter runs from counter_start.  They are guaranteed unique across
    calls as long as counter_start is managed by the caller.
    """
    return [f"SYNTH:{persona}:{counter_start + i}" for i in range(n)]


# Stateless version: ignores counter_start — unique across personas because
# persona name is embedded, but within a persona you need the stateful form.
def _simple_fake_generate(persona: str, seeds: list[str], n: int) -> list[str]:
    """Simplest deterministic fake: unique strings tagged by persona index."""
    return [f"SYNTH:{persona}:{i}" for i in range(n)]


# ---------------------------------------------------------------------------
# Fixture: real pairs representing the WF-A+B corpus snapshot (cut down)
# ---------------------------------------------------------------------------


@pytest.fixture
def real_pairs_corpus() -> list[dict[str, Any]]:
    """Minimal real-pair corpus reflecting the per-class counts from WF-A+B.

    Full counts: scout 220, forge-ui 140, no-dispatch 133, forge-wire 61,
    quill-py 54, lens 52, hermes 41, pipeline-data 35, lens-fast 31,
    forge-ui-pro 21, palette 21, quill-ts 20, atlas 13, forge-wire-pro 11,
    pipeline-data-pro 9, pipeline-async 2.

    Starved eligible: hermes(41), pipeline-data(35), palette(21), quill-ts(20),
    atlas(13), pipeline-async(2).  Already-at/above-floor eligible: scout(220),
    forge-ui(140), forge-wire(61), quill-py(54).
    """
    pairs: list[dict[str, Any]] = []

    per_class = {
        "scout": 5,  # represents 220 (truncated for test speed)
        "forge-ui": 5,  # represents 140
        "forge-wire": 5,  # represents 61
        "quill-py": 5,  # represents 54 (≥50 so no synthesis)
        "hermes": 41,
        "pipeline-data": 35,
        "palette": 21,
        "quill-ts": 20,
        "atlas": 13,
        "pipeline-async": 2,
        # Excluded classes — must NOT be augmented
        "no-dispatch": 5,
        "lens": 5,
        "lens-fast": 5,
        "forge-ui-pro": 5,
        "forge-wire-pro": 5,
        "pipeline-data-pro": 5,
    }

    idx = 0
    for persona, count in per_class.items():
        for j in range(count):
            pairs.append(
                _make_real_pair(
                    persona,
                    f"Real prompt for {persona} number {j}",
                    session_id=f"sess-{persona}-{j}",
                )
            )
            idx += 1

    return pairs


# ---------------------------------------------------------------------------
# Criterion 1: starved persona reaches FLOOR (or max cap)
# ---------------------------------------------------------------------------


class TestGenerateSyntheticFloor:
    """Given a starved eligible persona, generate_synthetic reaches >= FLOOR."""

    def test_pipeline_async_starved_two_real_reaches_floor(self) -> None:
        """GWT: Given pipeline-async has 2 real labels
        When generate_synthetic is called with FLOOR=50 and a fake generate_fn
        Then the result contains >= 48 synthetic pairs for pipeline-async
        and the combined real+synthetic count >= 50.
        """
        real_pairs = [
            _make_real_pair("pipeline-async", f"Real pipeline-async prompt {i}")
            for i in range(2)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )

        pa_synthetic = [r for r in result if r.get("label_persona") == "pipeline-async"]
        # 50 - 2 real = 48 synthetic; capped at max_per_persona=60
        assert len(pa_synthetic) >= 48, (
            f"Expected >= 48 synthetic pipeline-async pairs, got {len(pa_synthetic)}"
        )
        # Positive invariant: combined count hits floor
        combined = 2 + len(pa_synthetic)
        assert combined >= FLOOR, f"Combined real+synthetic {combined} < floor {FLOOR}"

    def test_synthetic_pairs_have_required_fields(self) -> None:
        """GWT: Given a starved persona
        When generate_synthetic produces synthetic pairs
        Then each pair has label_source='synthetic', label_confidence=0.5,
        label_status='ok', synthetic=True, and seed_prompt_hash set.
        """
        real_pairs = [
            _make_real_pair("atlas", f"Atlas real prompt {i}") for i in range(5)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )

        atlas_synthetic = [r for r in result if r.get("label_persona") == "atlas"]
        assert len(atlas_synthetic) > 0, "Expected at least one synthetic atlas pair"
        for pair in atlas_synthetic:
            assert pair.get("label_source") == "synthetic", (
                f"label_source must be 'synthetic', got {pair.get('label_source')!r}"
            )
            assert pair.get("label_confidence") == 0.5, (
                f"label_confidence must be 0.5, got {pair.get('label_confidence')!r}"
            )
            assert pair.get("label_status") == "ok", (
                f"label_status must be 'ok', got {pair.get('label_status')!r}"
            )
            assert pair.get("synthetic") is True, (
                f"synthetic must be True, got {pair.get('synthetic')!r}"
            )
            assert pair.get("seed_prompt_hash") is not None, (
                "seed_prompt_hash (provenance) must be set on synthetic pairs"
            )
            assert pair.get("prompt_hash") is not None, "prompt_hash must be set"
            assert pair.get("prompt") is not None, "prompt must be set"
            assert pair.get("label_persona") == "atlas", (
                f"label_persona must be 'atlas', got {pair.get('label_persona')!r}"
            )

    def test_already_at_floor_persona_not_augmented(self) -> None:
        """GWT: Given forge-wire has 61 real labels (>= FLOOR=50)
        When generate_synthetic is called
        Then no synthetic pairs are generated for forge-wire.
        """
        real_pairs = [
            _make_real_pair("forge-wire", f"Forge-wire real prompt {i}") for i in range(61)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        fw_synthetic = [r for r in result if r.get("label_persona") == "forge-wire"]
        assert fw_synthetic == [], (
            f"Expected zero synthetic forge-wire pairs (already >= floor), got {len(fw_synthetic)}"
        )

    def test_max_per_persona_cap_honored(self) -> None:
        """GWT: Given a persona with 0 real labels and floor=50, max_per_persona=30
        When generate_synthetic is called
        Then no more than 30 synthetic pairs are generated for that persona.
        """
        real_pairs = [
            _make_real_pair("hermes", f"Hermes real prompt {i}") for i in range(1)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=30,
            generate_fn=_simple_fake_generate,
        )
        hermes_synthetic = [r for r in result if r.get("label_persona") == "hermes"]
        assert len(hermes_synthetic) <= 30, (
            f"Expected <= 30 synthetic hermes pairs (max_per_persona=30), "
            f"got {len(hermes_synthetic)}"
        )

    def test_seed_prompt_hash_points_to_a_real_seed(self) -> None:
        """GWT: Given real seeds for pipeline-data
        When generate_synthetic produces synthetic pairs for pipeline-data
        Then seed_prompt_hash on each synthetic pair equals a real seed's prompt_hash.
        """
        real_pairs = [
            _make_real_pair("pipeline-data", f"Pipeline-data real prompt {i}")
            for i in range(10)
        ]
        real_hashes: set[str] = {p["prompt_hash"] for p in real_pairs}

        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        pd_synthetic = [r for r in result if r.get("label_persona") == "pipeline-data"]
        assert len(pd_synthetic) > 0, "Expected synthetic pipeline-data pairs"
        for pair in pd_synthetic:
            seed_hash = pair.get("seed_prompt_hash")
            assert seed_hash in real_hashes, (
                f"seed_prompt_hash {seed_hash!r} is not a hash of any real seed prompt"
            )


# ---------------------------------------------------------------------------
# Criterion 2: exclusion — excluded labels never get synthetic pairs
# ---------------------------------------------------------------------------


class TestGenerateSyntheticExclusion:
    """generate_synthetic NEVER produces pairs for excluded labels."""

    @pytest.mark.parametrize(
        "excluded_persona",
        sorted(EXCLUDED_LABELS),
    )
    def test_excluded_persona_not_synthesized(self, excluded_persona: str) -> None:
        """GWT: Given an excluded label (pro-variant, lens, lens-fast, no-dispatch)
        When generate_synthetic is called even with only 0 real pairs for it
        Then no synthetic pairs are emitted for that excluded label.
        """
        # Put the excluded persona as the ONLY class in real_pairs so it would
        # be starved — if generate_synthetic respects exclusion, output is empty.
        real_pairs = [
            _make_real_pair(excluded_persona, f"Prompt for {excluded_persona} {i}")
            for i in range(2)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        bad = [r for r in result if r.get("label_persona") == excluded_persona]
        assert bad == [], (
            f"Expected zero synthetic pairs for excluded label {excluded_persona!r}, "
            f"got {len(bad)}"
        )

    def test_no_dispatch_not_synthesized_regardless_of_count(self) -> None:
        """GWT: Given no-dispatch has only 2 pairs (< floor)
        When generate_synthetic is called with no-dispatch in real_pairs
        Then no synthetic no-dispatch pairs are emitted (exclusion wins).
        """
        real_pairs = [
            _make_real_pair("no-dispatch", f"No-dispatch prompt {i}") for i in range(2)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        nd = [r for r in result if r.get("label_persona") == "no-dispatch"]
        assert nd == [], f"no-dispatch is excluded from synthesis, got {len(nd)} pairs"

    def test_eligible_plus_excluded_only_eligible_augmented(
        self, real_pairs_corpus: list[dict[str, Any]]
    ) -> None:
        """GWT: Given a mixed corpus with both eligible and excluded classes
        When generate_synthetic is called
        Then ONLY eligible-class pairs appear in the synthetic output.
        """
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs_corpus,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        for pair in result:
            persona = pair.get("label_persona")
            assert persona in ELIGIBLE_PERSONAS, (
                f"Synthetic pair has persona {persona!r} which is not in ELIGIBLE_PERSONAS"
            )


# ---------------------------------------------------------------------------
# Criterion 3: dedup — collision with a real prompt is dropped
# ---------------------------------------------------------------------------


class TestGenerateSyntheticDedup:
    """Synthetic pairs whose prompt_hash collides with a real pair are dropped."""

    def test_collision_with_real_prompt_is_dropped(self) -> None:
        """GWT: Given a fake generate_fn that returns a prompt identical to a real seed
        When generate_synthetic is called
        Then the colliding synthetic pair is NOT emitted (real wins; no dupe).
        """
        real_prompt = "Exactly this real pipeline-async prompt"
        real_pairs = [
            _make_real_pair("pipeline-async", real_prompt),
        ]
        real_hash = prompt_hash(real_prompt)

        # Fake generate_fn: first returns the REAL prompt (collision),
        # subsequent returns unique strings.
        call_count: list[int] = [0]

        def collision_then_unique(persona: str, seeds: list[str], n: int) -> list[str]:
            results: list[str] = []
            for _i in range(n):
                if call_count[0] == 0:
                    results.append(real_prompt)  # collision
                else:
                    results.append(f"UNIQUE:{persona}:{call_count[0]}")
                call_count[0] += 1
            return results

        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=collision_then_unique,
        )
        pa_synthetic = [r for r in result if r.get("label_persona") == "pipeline-async"]
        synthetic_hashes = [r["prompt_hash"] for r in pa_synthetic]

        # Positive invariant: real hash does NOT appear in any synthetic pair's prompt_hash
        assert real_hash not in synthetic_hashes, (
            "A synthetic pair whose prompt_hash collides with a real prompt must be dropped"
        )

    def test_synthetic_intra_batch_dedup(self) -> None:
        """GWT: Given a fake generate_fn that returns duplicate synthetic prompts
        When generate_synthetic is called
        Then each unique synthetic prompt_hash appears at most once in output.
        """
        # generate_fn always returns the same string — every call is a dup
        def all_same(persona: str, seeds: list[str], n: int) -> list[str]:
            return [f"SAME_PROMPT_FOR_{persona}"] * n

        real_pairs = [
            _make_real_pair("atlas", f"Atlas real {i}") for i in range(3)
        ]
        result: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=all_same,
        )
        atlas_synthetic = [r for r in result if r.get("label_persona") == "atlas"]
        hashes = [r["prompt_hash"] for r in atlas_synthetic]
        # Positive invariant: no duplicate prompt_hash in synthetic output
        assert len(hashes) == len(set(hashes)), (
            "Synthetic output contains duplicate prompt_hashes within the same persona batch"
        )


# ---------------------------------------------------------------------------
# Criterion 4: collect_labeled_pairs(include_synthetic=True) — real wins on collision
# ---------------------------------------------------------------------------


class TestCollectLabeledPairsIncludeSynthetic:
    """collect_labeled_pairs(include_synthetic=True) unions the persisted artifact.

    Real-persona rows always beat synthetic rows on a hash collision.
    This test uses a pre-written synthetic_pairs.jsonl artifact (if present);
    when the artifact does not exist (pre-impl) the test asserts the signature
    of collect_labeled_pairs accepts include_synthetic=True without TypeError.
    """

    def test_include_synthetic_kwarg_accepted(self) -> None:
        """GWT: Given collect_labeled_pairs from aggregate.py
        When called with include_synthetic=True
        Then it does NOT raise TypeError (the kwarg exists in the signature).

        This test intentionally calls with minimal args (no real data files
        on disk during CI) and allows empty result; we only assert the function
        accepts the kwarg.
        """
        import inspect

        sig = inspect.signature(collect_labeled_pairs)
        assert "include_synthetic" in sig.parameters, (
            "collect_labeled_pairs must accept include_synthetic= keyword argument"
        )

    def test_real_beats_synthetic_on_hash_collision(self, tmp_path: Path) -> None:
        """GWT: Given a persisted synthetic_pairs.jsonl containing a synthetic pair
        whose prompt_hash matches a real pair
        When collect_labeled_pairs(include_synthetic=True) is called
        Then the returned row for that hash has the REAL label_source (not 'synthetic').

        We patch the SYNTHETIC_ARTIFACT constant in aggregate.py to point to a
        tmp_path fixture file to avoid touching the real artifact during testing.
        """
        import json
        import unittest.mock as mock

        # Build a real pair and a synthetic pair sharing the SAME prompt_hash
        shared_prompt = "A shared prompt that appears in both real and synthetic"
        shared_hash = prompt_hash(shared_prompt)

        synthetic_pair: dict[str, Any] = {
            "prompt": shared_prompt,
            "prompt_hash": shared_hash,
            "label_persona": "atlas",
            "label_status": "ok",
            "label_source": "synthetic",
            "label_confidence": 0.5,
            "synthetic": True,
            "seed_prompt_hash": prompt_hash("some seed"),
        }

        # Write synthetic artifact to tmp_path
        artifact = tmp_path / "synthetic_pairs.jsonl"
        artifact.write_text(json.dumps(synthetic_pair) + "\n", encoding="utf-8")

        # Build real pairs list that includes the same prompt
        real_pair: dict[str, Any] = {
            "session_id": "sess-real-collision",
            "prompt": shared_prompt,
            "prompt_hash": shared_hash,
            "label_persona": "atlas",
            "label_status": "ok",
            "label_source": "transcript_mining",
            "label_confidence": 0.8,
        }

        # Patch aggregate's synthetic artifact path + short-circuit the live sources
        with mock.patch(
            "broker.router_train.aggregate.SYNTHETIC_ARTIFACT_PATH",
            artifact,
        ):
            # Call with pre-cooked sidecar/transcript data containing the real pair
            result: list[dict[str, Any]] = collect_labeled_pairs(
                sidecar_decisions=[],
                sidecar_dispatches=[],
                transcripts_root=tmp_path / "nonexistent_transcripts",
                include_synthetic=True,
                _synthetic_pairs_override=[synthetic_pair],
                _real_pairs_override=[real_pair],
            )

        # Find the row matching shared_hash
        matching = [r for r in result if r.get("prompt_hash") == shared_hash]
        assert len(matching) >= 1, "Expected at least one row for shared_hash"
        # The real row (label_confidence=0.8) must win over synthetic (0.5)
        winner = max(
            matching, key=lambda r: float(r.get("label_confidence") or 0.0)
        )
        assert winner.get("label_source") != "synthetic", (
            f"Real row must beat synthetic on hash collision; "
            f"got label_source={winner.get('label_source')!r}"
        )


# ---------------------------------------------------------------------------
# Criterion 5: determinism — same inputs, same FAKE generate_fn -> identical output
# ---------------------------------------------------------------------------


class TestGenerateSyntheticDeterminism:
    """Same inputs + same FAKE generate_fn -> identical output ordering."""

    def test_same_inputs_produce_identical_results(self) -> None:
        """GWT: Given a deterministic fake generate_fn
        When generate_synthetic is called twice with the same real_pairs
        Then both call results are identical (same order, same values).
        """
        real_pairs: list[dict[str, Any]] = [
            _make_real_pair("pipeline-async", f"PA real prompt {i}") for i in range(2)
        ] + [
            _make_real_pair("atlas", f"Atlas real prompt {i}") for i in range(3)
        ]

        result_a: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        result_b: list[dict[str, Any]] = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )

        # Positive invariant: both runs produce identical lists
        assert len(result_a) == len(result_b), (
            f"Determinism violated: first call returned {len(result_a)} pairs, "
            f"second returned {len(result_b)}"
        )
        prompts_a = sorted(r["prompt"] for r in result_a)
        prompts_b = sorted(r["prompt"] for r in result_b)
        assert prompts_a == prompts_b, (
            "Determinism violated: same inputs produced different prompt sets"
        )

    def test_total_synthetic_output_ordering_is_stable(self) -> None:
        """GWT: Given a mixed corpus
        When generate_synthetic is called twice
        Then the full output list is element-wise identical (not just same set).
        """
        real_pairs = [
            _make_real_pair("quill-ts", f"Quill-ts prompt {i}") for i in range(10)
        ]
        r1 = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        r2 = generate_synthetic(
            real_pairs,
            floor=FLOOR,
            eligible=ELIGIBLE_PERSONAS,
            max_per_persona=60,
            generate_fn=_simple_fake_generate,
        )
        # Element-wise comparison (not just sorted)
        assert r1 == r2, (
            "generate_synthetic output ordering must be deterministic across identical calls"
        )


# ---------------------------------------------------------------------------
# Criterion 6 (bonus): generate_synthetic is re-exported from __init__
# ---------------------------------------------------------------------------


class TestReexportFromInit:
    """generate_synthetic must be accessible via broker.router_train."""

    def test_generate_synthetic_importable_from_package(self) -> None:
        """GWT: Given broker.router_train.__init__
        When `from broker.router_train import generate_synthetic` is executed
        Then it resolves without ImportError.
        """
        import broker.router_train as rt

        assert hasattr(rt, "generate_synthetic"), (
            "generate_synthetic must be re-exported from broker.router_train.__all__"
        )
        assert callable(rt.generate_synthetic), (
            "broker.router_train.generate_synthetic must be callable"
        )

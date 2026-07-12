"""R1-T09: seeded-defect quality anchor + runaway-guard drill.

Two independent quality anchors, per the R1-T09 brief:

1. SEEDED-DEFECT DRILL — plant a known defect that is SPECIFIC to lens-gate
   v2 (R1-T08): a stale/wrong-tier Lens PASS row (lens_type='T1') satisfying
   a dispatch that `_classify_lens_tier` resolves to T2 (a gated single-file
   change under nexus-broker/src/). This is deliberately NOT a plain
   verdict='FAIL'-or-absent scenario — that variant is already covered by
   the pre-existing v1 `_has_lens_validation` floor (see
   test_lens_gate_verdict.py's parametrize(['FAIL', 'PARTIAL']) test and this
   file's own no-validation-at-all test below) — proving nothing about v2
   specifically.

   Originally this replayed the identical fixture against TWO hook versions
   (git HEAD, asserted to predate R1-T08's v2 logic, vs. the working tree) and
   treated the ALLOW-vs-BLOCK divergence as the proof. That premise no longer
   holds — R1-T08 is now on HEAD (`_has_lens_validation_v2` / TIER-MISMATCH
   are committed, not working-tree-only), so there is no pre-v2 HEAD left to
   diff against. The proof that matters — v2 catches a wrong-tier PASS row —
   is a single direct assertion against the current hook instead. See
   test_seeded_broker_validator_bypass_is_caught_by_lens_gate_v2.

   nexus-package/.claude/hooks/lens-gate.sh is the deployable TEMPLATE shipped
   to other projects: its GATED_PATH_PREFIXES is rendered at install time from
   the target project's stack profile (__WATCHED_PREFIXES__ token), falling
   back to a generic installed-target shape (app/, ingestion/src/, models/,
   design/) when run unrendered — which does NOT include nexus-broker/, this
   repo's own gated source. `_HOOK_WATCHED_PREFIXES` overrides that fallback
   for this drill (mirroring the override seam socraticode-gate.sh already
   exposes) so the seeded scenario against nexus-broker/src/broker/server.py
   resolves as gated here too.

2. RUNAWAY-GUARD DRILL — DEC-024 names five independent ceilings (max-iter,
   no-progress, token/$ budget, circuit-breaker, separate-judge). Recon
   (.memory/scout-reports/R1-T09-seeded-defect-drill/findings.md) found that
   guards 1-4 exist ONLY as prose patterns authored inline in Workflow JS
   scripts (nexus-dispatch-catalog skill, the R2-T06 canonical home for the
   runaway-guard checklist — moved there from loop-until-done-patterns, which
   now keeps only a pointer) — there is no importable Python
   runtime module in nexus-broker/src/broker/ implementing max_iterations,
   no_progress detection, a token budget ceiling, or a circuit breaker. A
   test that reimplements that loop logic inline (as recon's own draft did)
   would be testing the test, not the code — a self-testing tautology
   forbidden by tdd-patterns. So guards 1-4 are drilled by asserting the
   documented CONTRACT exists (the skill's checklist literally enumerates
   each ceiling with its trigger condition) and are otherwise reported as a
   NAMED GAP: no production module exists to pin a real assertion against.
   Guard 5 (separate-judge) DOES have a real, drillable production
   implementation — lens-gate.sh itself IS the separate-judge enforcement —
   so it gets a full behavioral proof, not just a documentation check.

Run from nexus-broker/:
    uv run pytest tests/test_seeded_defect_and_runaway_guard_drill.py -v
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".claude" / "hooks" / "lens-gate.sh"
# R2-T06 skill-corpus de-dupe moved the runaway-guard checklist OUT of
# loop-until-done-patterns and INTO nexus-dispatch-catalog as its canonical
# home (nexus-redesign/plans/03-r2e2-design-APPROVED.md §4 Cluster 1);
# loop-until-done-patterns now keeps only a pointer, no checklist content.
LOOP_SKILL = REPO_ROOT / ".claude" / "skills" / "nexus-dispatch-catalog" / "SKILL.md"


def _seed_validation_with_tier(
    db: Path, verdict: str, *, lens_type: str | None, risk_tier: str | None,
    task_hash: str, age_minutes: int = 0, target_agent: str = "forge-wire",
) -> None:
    """Insert one Lens validation row carrying lens_type/risk_tier (R1-T08 v2
    schema). Mirrors test_lens_gate_verdict.py's helper of the same name —
    duplicated (not imported) since nexus-broker/tests/ has no `tests` package
    __init__.py and no existing precedent for cross-test-file imports.
    """
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            agent_validated     TEXT NOT NULL,
            target_agent        TEXT NOT NULL,
            task_or_brief_hash  TEXT NOT NULL,
            verdict             TEXT NOT NULL,
            evidence_summary    TEXT,
            validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            lens_type           TEXT,
            risk_tier           TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO validation_log
            (agent_validated, target_agent, task_or_brief_hash, verdict,
             validated_at, lens_type, risk_tier)
        VALUES ('lens', ?, ?, ?, datetime('now', ?), ?, ?)
        """,
        (target_agent, task_hash, verdict, f"-{age_minutes} minutes", lens_type, risk_tier),
    )
    conn.commit()
    conn.close()


def _init_empty_validation_table(db: Path) -> None:
    """Create validation_log with zero rows — a realistic initialized-but-
    never-validated project.db (ADR-001 Phase 0: lens-gate.sh no longer
    auto-creates this table itself via its own DDL on first touch; that
    schema-init now lives ONLY in log.py's single-writer `init`/`validation
    add`, never in the read-only `check-gate` path this hook shells out to —
    a still-nonexistent DB file is reported as a DB-ERROR, not silently
    healed). Tests that assert "no validation row exists YET" (as opposed to
    "the DB was never initialized at all") must pre-create this table to
    exercise that scenario, not the DB-ERROR one.
    """
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS validation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            agent_validated     TEXT NOT NULL,
            target_agent        TEXT NOT NULL,
            task_or_brief_hash  TEXT NOT NULL,
            verdict             TEXT NOT NULL,
            evidence_summary    TEXT,
            validated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            lens_type           TEXT,
            risk_tier           TEXT
        )
        """
    )
    conn.commit()
    conn.close()


_HOOK_ENV_EXTRA = {"_HOOK_WATCHED_PREFIXES": "nexus-broker/"}


def _run_gate_with_payload(db: Path, payload: dict) -> subprocess.CompletedProcess[str]:
    """Invoke the live hook with an arbitrary payload against the given DB."""
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"_HOOK_DB_PATH": str(db), "PATH": "/usr/bin:/bin", **_HOOK_ENV_EXTRA},
    )


# ---------------------------------------------------------------------------
# 1. SEEDED-DEFECT DRILL — real defect, real gate, real subprocess boundary.
# ---------------------------------------------------------------------------


SEEDED_DEFECT_TASK_ID = "R1-T09-SEEDED-DEFECT-broker-validator-bypass"
SEEDED_DEFECT_TASK_HASH = hashlib.sha256(SEEDED_DEFECT_TASK_ID.encode()).hexdigest()[:16]

SEEDED_DEFECT_DONE_RESPONSE = (
    "Relaxed the broker validator to skip the skills_required cross-check "
    "for work_type='meta' briefs.\n\n"
    "```json\n"
    '{"files_changed": ["nexus-broker/src/broker/server.py"]}\n'
    "```\n\n"
    "## NEXUS:DONE\n"
)

SEEDED_DEFECT_PAYLOAD = {
    "last_assistant_message": SEEDED_DEFECT_DONE_RESPONSE,
    "agent_persona": "forge-wire",
    "task_id": SEEDED_DEFECT_TASK_ID,
}


def test_seeded_broker_validator_bypass_is_caught_by_lens_gate_v2(tmp_path: Path) -> None:
    """THE non-tautological proof. GIVEN a gated persona (forge-wire) claims
    NEXUS:DONE on a single-file gated-prefix change
    (nexus-broker/src/broker/server.py) that `_classify_lens_tier` resolves
    to T2, and the ONLY Lens row on file is a PASS logged at lens_type='T1'
    (a stale/wrong-tier row — e.g. Lens ran a light pass before the change
    grew into gated-source territory, or a prior T1 dispatch's row is being
    reused), WHEN lens-gate.sh evaluates it, THEN it blocks (exit 2,
    LENS/TIER-MISMATCH) — proving a stale/wrong-tier PASS row does not
    satisfy a T2-required dispatch.

    STALE-FIXTURE NOTE: this test originally diffed git HEAD's lens-gate.sh
    (asserted to predate R1-T08's `_has_lens_validation_v2`) against the
    working tree, treating the ALLOW-vs-BLOCK divergence as the proof. That
    premise no longer holds: R1-T08 (commit fb36998) is now on HEAD in BOTH
    `.claude/hooks/lens-gate.sh` and `nexus-package/.claude/hooks/lens-gate.sh`
    — `git show HEAD:<either path>` already contains `_has_lens_validation_v2`
    / the TIER-MISMATCH deny path, so the "before" half's sanity check
    (expecting HEAD to ALLOW) fails; there is no pre-v2 HEAD left to diff
    against. The behavioral proof that matters — v2 catches a wrong-tier PASS
    — is fully expressible as a single assertion against the current hook, so
    this drops the stale before/after diff in favor of that direct check
    (matching the sibling test's style below).
    """
    db = tmp_path / "project.db"
    # Seed: Lens's only PASS row for this task is at T1 — stale/wrong tier
    # for a dispatch _classify_lens_tier resolves to T2 (gated single-file
    # prefix under nexus-broker/src/).
    _seed_validation_with_tier(
        db, "PASS", lens_type="T1", risk_tier="T1", task_hash=SEEDED_DEFECT_TASK_HASH,
    )

    proc_v2 = _run_gate_with_payload(db, SEEDED_DEFECT_PAYLOAD)
    assert proc_v2.returncode == 2, (
        "THE DEFECT: a stale T1 PASS row must NOT satisfy a T2-required "
        f"dispatch under lens-gate v2. Expected BLOCK (exit 2), got "
        f"{proc_v2.returncode}\nstderr: {proc_v2.stderr}"
    )
    assert "LENS/TIER-MISMATCH" in proc_v2.stderr, (
        f"expected LENS/TIER-MISMATCH deny code, got stderr: {proc_v2.stderr}"
    )


def test_seeded_defect_would_have_shipped_without_the_gate(tmp_path: Path) -> None:
    """GIVEN the same seeded defect scenario, WHEN no Lens validation row
    exists at all for the task hash (the counterfactual: Lens was never
    dispatched, so the bypass would ship unreviewed),
    THEN the gate still blocks (LENS/NO-VALIDATION) — proving the gate's
    default posture is deny-until-reviewed, not merely deny-on-wrong-tier.
    This is the v1-floor half of the drill (both v1 and v2 already agree
    here); the v2-SPECIFIC proof is the tier-mismatch divergence above.
    """
    db = tmp_path / "project.db"
    _init_empty_validation_table(db)
    proc = _run_gate_with_payload(db, SEEDED_DEFECT_PAYLOAD)
    assert proc.returncode == 2, (
        f"No Lens row at all must still BLOCK (exit 2), got {proc.returncode}\n"
        f"stderr: {proc.stderr}"
    )
    assert "LENS/NO-VALIDATION" in proc.stderr


def test_seeded_defect_fix_with_genuine_pass_verdict_unblocks(tmp_path: Path) -> None:
    """GIVEN the tier-mismatch was subsequently fixed — Lens re-reviewed at
    the required T2 depth and logged a fresh PASS with lens_type='T2' —
    WHEN the same NEXUS:DONE payload (same task hash) is replayed,
    THEN the gate allows it (exit 0) — proving v2 is not permanently stuck
    once a genuinely-tiered fix lands, i.e. it discriminates the seeded
    wrong-tier case from the correctly-tiered case rather than blocking
    unconditionally.
    """
    db = tmp_path / "project.db"
    _seed_validation_with_tier(
        db, "PASS", lens_type="T2", risk_tier="T2", task_hash=SEEDED_DEFECT_TASK_HASH,
    )
    proc = _run_gate_with_payload(db, SEEDED_DEFECT_PAYLOAD)
    assert proc.returncode == 0, (
        f"A genuine T2 PASS verdict after the fix should ALLOW (exit 0), "
        f"got {proc.returncode}\nstderr: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. RUNAWAY-GUARD DRILL — pass/fail (or named gap) per guard.
# ---------------------------------------------------------------------------


def _loop_skill_text() -> str:
    """Read the runaway-guard checklist's canonical home. R2-T06 relocated
    the checklist from loop-until-done-patterns to nexus-dispatch-catalog
    (nexus-redesign/plans/03-r2e2-design-APPROVED.md §4 Cluster 1) — this
    always reads the current canonical location, not the old pointer file.
    """
    assert LOOP_SKILL.exists(), (
        f"nexus-dispatch-catalog SKILL.md not found at {LOOP_SKILL} — the "
        "runaway-guard contract this drill checks has moved or been deleted."
    )
    return LOOP_SKILL.read_text(encoding="utf-8")


class TestGuard1MaxIterationCap:
    """Guard 1 (DEC-024): max-iteration cap — loop exits after N regardless
    of oracle. NAMED GAP: no production Python module in nexus-broker/src/
    implements this ceiling; it is authored per-Workflow as `budget({
    maxIterations: N })` inline in JS loop scripts (Workflow runtime, not the
    broker). There is no stable import path to pin a real assertion against.
    """

    def test_guard_is_documented_as_a_mandatory_checklist_item(self) -> None:
        text = _loop_skill_text()
        assert re.search(r"Max-iteration cap", text), (
            "Runaway-guard checklist must document the max-iteration cap"
        )
        assert "maxIterations" in text or "MAX_ITER" in text, (
            "Checklist must name the concrete mechanism (budget({maxIterations}) "
            "or an explicit loop counter)"
        )

    @pytest.mark.xfail(
        reason=(
            "GAP (not a stub): no importable nexus-broker/src/broker module "
            "implements a max-iteration ceiling as runtime code — the pattern is "
            "prose-only, authored inline per-Workflow in JS. Tracked as a named "
            "gap in the R1-T09 drill report, not scheduled for this delivery."
        ),
        strict=False,
    )
    def test_guard_has_a_production_runtime_implementation(self) -> None:
        import broker.state as state_mod

        assert hasattr(state_mod, "enforce_max_iterations"), (
            "No max-iteration enforcement function exists in the broker runtime"
        )


class TestGuard2NoProgressDetection:
    """Guard 2 (DEC-024): no-progress detection — halt on >=3 consecutive
    identical findings/errors. Same NAMED GAP as Guard 1: prose-only pattern,
    no broker-side runtime module.
    """

    def test_guard_is_documented_with_a_concrete_threshold(self) -> None:
        text = _loop_skill_text()
        assert re.search(r"No-progress detection", text), (
            "Runaway-guard checklist must document no-progress detection"
        )
        assert re.search(r">=\s*3|3 consecutive", text), (
            "Checklist must name the concrete consecutive-repeat threshold (>=3)"
        )

    @pytest.mark.xfail(
        reason=(
            "GAP (not a stub): no importable nexus-broker/src/broker module "
            "implements no-progress detection as runtime code — the pattern is "
            "prose-only, authored inline per-Workflow (prev_findings comparison "
            "in JS). Tracked as a named gap in the R1-T09 drill report."
        ),
        strict=False,
    )
    def test_guard_has_a_production_runtime_implementation(self) -> None:
        import broker.state as state_mod

        assert hasattr(state_mod, "detect_no_progress"), (
            "No no-progress-detection function exists in the broker runtime"
        )


class TestGuard3TokenBudget:
    """Guard 3 (DEC-024): token/$ budget ceiling. Same NAMED GAP."""

    def test_guard_is_documented(self) -> None:
        text = _loop_skill_text()
        assert re.search(r"Token/\$ budget", text, re.IGNORECASE), (
            "Runaway-guard checklist must document the token/$ budget ceiling"
        )
        # nexus-dispatch-catalog (the R2-T06 canonical home) names the
        # concrete mechanism as budget({maxIterations, ...}) covering both
        # the max-iteration and token/$ ceilings, not a distinct maxTokens
        # symbol — assert the mechanism it actually documents.
        assert "budget(" in text and "maxIterations" in text, (
            "Checklist must name the concrete mechanism (budget({maxIterations, ...}))"
        )

    @pytest.mark.xfail(
        reason=(
            "GAP (not a stub): no importable nexus-broker/src/broker module "
            "tracks or enforces a token/$ budget ceiling as runtime code — the "
            "pattern is prose-only, authored inline per-Workflow via budget(). "
            "Tracked as a named gap in the R1-T09 drill report."
        ),
        strict=False,
    )
    def test_guard_has_a_production_runtime_implementation(self) -> None:
        import broker.state as state_mod

        assert hasattr(state_mod, "enforce_token_budget"), (
            "No token-budget enforcement function exists in the broker runtime"
        )


class TestGuard4CircuitBreaker:
    """Guard 4 (DEC-024): circuit-breaker — failures-per-window rate limit,
    TaskStop + escalate on Nth recurrence. Same NAMED GAP.
    """

    def test_guard_is_documented(self) -> None:
        text = _loop_skill_text()
        assert re.search(r"Circuit-breaker", text), (
            "Runaway-guard checklist must document the circuit-breaker"
        )
        assert "TaskStop" in text, (
            "Checklist must name the concrete escalation mechanism (TaskStop)"
        )

    @pytest.mark.xfail(
        reason=(
            "GAP (not a stub): no importable nexus-broker/src/broker module "
            "implements a failures-per-window circuit breaker as runtime code — "
            "the pattern is prose-only, authored inline per-Workflow. Tracked as "
            "a named gap in the R1-T09 drill report."
        ),
        strict=False,
    )
    def test_guard_has_a_production_runtime_implementation(self) -> None:
        import broker.state as state_mod

        assert hasattr(state_mod, "circuit_breaker_fire"), (
            "No circuit-breaker function exists in the broker runtime"
        )


class TestGuard5SeparateJudge:
    """Guard 5 (DEC-024): separate-judge — the producer never self-certifies
    completion; a distinct Lens verdict row is required. UNLIKE guards 1-4,
    this DOES have a real, drillable production implementation: lens-gate.sh
    itself IS the separate-judge enforcement mechanism (it is invoked
    regardless of what the producing agent's own NEXUS:DONE text claims).
    This gets a full PASS/FAIL behavioral proof, not just a documentation check.
    """

    def test_guard_is_documented(self) -> None:
        text = _loop_skill_text()
        assert re.search(r"[Ss]eparate.judge", text), (
            "Runaway-guard checklist must document the separate-judge principle"
        )

    def test_producer_self_report_of_done_is_insufficient_without_lens_row(
        self, tmp_path: Path,
    ) -> None:
        """GIVEN a gated persona's own NEXUS:DONE text asserts success with no
        corroborating Lens row, WHEN lens-gate.sh evaluates it,
        THEN it blocks — proving the producer's self-certification alone
        never satisfies the separate-judge requirement. PASS for this guard.
        """
        db = tmp_path / "project.db"
        payload = {
            "last_assistant_message": (
                "All acceptance criteria met, verified locally.\n\n"
                "```json\n"
                '{"files_changed": ["nexus-broker/src/broker/server.py"]}\n'
                "```\n\n"
                "## NEXUS:DONE\n"
            ),
            "agent_persona": "pipeline-data",
            "task_id": "R1-T09-guard5-self-report-only",
        }
        proc = _run_gate_with_payload(db, payload)
        assert proc.returncode == 2, (
            "A producer's self-report of DONE with no separate Lens row must "
            f"be blocked (exit 2), got {proc.returncode}\nstderr: {proc.stderr}"
        )

    def test_separate_lens_pass_row_satisfies_the_guard(self, tmp_path: Path) -> None:
        """GIVEN a genuinely separate Lens PASS row exists at the required
        tier, WHEN the same producer claims NEXUS:DONE,
        THEN the gate allows it — the separate-judge requirement is satisfied
        by an actual distinct verdict, not by the producer's own claim.
        """
        db = tmp_path / "project.db"
        task_id = "R1-T09-guard5-separate-pass"
        task_hash = hashlib.sha256(task_id.encode()).hexdigest()[:16]
        _seed_validation_with_tier(
            db, "PASS", lens_type="T2", risk_tier="T2", task_hash=task_hash,
            target_agent="pipeline-data",
        )
        payload = {
            "last_assistant_message": (
                "Implemented and Lens-reviewed.\n\n"
                '{"files_changed": ["nexus-broker/src/broker/server.py"]}\n\n'
                "## NEXUS:DONE\n"
            ),
            "agent_persona": "pipeline-data",
            "task_id": task_id,
        }
        proc = _run_gate_with_payload(db, payload)
        assert proc.returncode == 0, (
            f"A genuine separate Lens PASS row should ALLOW (exit 0), "
            f"got {proc.returncode}\nstderr: {proc.stderr}"
        )


# ---------------------------------------------------------------------------
# Drill summary — a single collectible test asserting the report shape itself
# (so the drill's pass/gap accounting cannot silently drift out of sync with
# the acceptance criterion: "each of the four runaway guards has a documented
# drill result — pass or a named gap").
# ---------------------------------------------------------------------------


DRILL_RESULTS: dict[str, str] = {
    "max_iteration_cap": "GAP — no production runtime module (prose-only Workflow pattern)",
    "no_progress_detection": "GAP — no production runtime module (prose-only Workflow pattern)",
    "token_budget": "GAP — no production runtime module (prose-only Workflow pattern)",
    "circuit_breaker": "GAP — no production runtime module (prose-only Workflow pattern)",
    "separate_judge": "PASS — enforced live by .claude/hooks/lens-gate.sh (behavioral proof above)",
}


def test_drill_report_covers_all_five_guards_with_a_verdict() -> None:
    """Every guard DEC-024 names must have an explicit verdict — PASS or a
    named GAP. Silence for a guard (neither) is not an acceptable drill
    outcome, so this asserts the report dict is exhaustive.
    """
    expected_guards = {
        "max_iteration_cap",
        "no_progress_detection",
        "token_budget",
        "circuit_breaker",
        "separate_judge",
    }
    assert set(DRILL_RESULTS.keys()) == expected_guards
    for guard, verdict in DRILL_RESULTS.items():
        assert verdict.startswith("PASS") or verdict.startswith("GAP"), (
            f"Guard {guard!r} verdict must be PASS or a named GAP, got: {verdict!r}"
        )

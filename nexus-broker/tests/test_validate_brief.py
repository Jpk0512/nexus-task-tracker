"""OPT-053 — truth-table tests for the broker brief validator.

`broker.server.nexus_validate_brief` is the gate EVERY Nexus dispatch routes
through: it decides approved/rejected for a (persona, intent, brief) before any
Task fan-out. Before OPT-053 it had ZERO direct tests. These pin its decision
table by driving the REAL async validator — not a re-implementation.

Hermeticity: the validator has three side effects we neutralize per-test so the
suite never touches the live broker_state.json or project.db —
  * read_state()           → injected (controls notepad freshness)
  * write_state()          → captured (lets us assert the approval write)
  * log_broker_validation()→ swallowed (the fire-and-forget DB subprocess)
All three are module-level names on broker.server, so monkeypatch.setattr on the
server module rebinds exactly what the function calls.

CONTRACT NOTE (validator-vs-brief field surface): `persona` and `intent` are
FUNCTION ARGUMENTS, not brief fields. The brief-level required fields are
broker.server.REQUIRED_BRIEF_FIELDS = goal, context_files, acceptance_criteria,
verification_required, do_not_touch. Persona legality is therefore asserted via
the persona argument (illegal-persona + legal-persona/illegal-intent cases),
and each required brief field is asserted via field removal — together these
cover the task's "each missing required field is rejected with a clear reason".
"""
from __future__ import annotations

import datetime
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

import broker.server as srv
from broker.registry import ALLOWED_PERSONAS, RETIRED_BASE_PERSONAS

# NATIVE-25 (v): load the LIVE skills-required-guard hook so we can assert its
# free-text 'skills_required: a, b' detection directly. The hook is a Python body
# under a .sh extension (routed through _py.sh in settings.json); importlib loads
# it like test_hooks_py39_import.py does. Resolve from the repo root (4 parents up
# from this test file: tests → nexus-broker → repo-root) and skip gracefully if
# the live hook is not present in this checkout.
_GUARD_PATH = (
    Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "skills-required-guard.sh"
)


def _load_skills_guard():
    # The hook is a Python body under a .sh suffix; importlib cannot infer a
    # source loader from the extension, so name a SourceFileLoader explicitly
    # (same technique test_hooks_py39_import.py uses for the .sh gates).
    loader = importlib.machinery.SourceFileLoader(
        "skills_required_guard", str(_GUARD_PATH)
    )
    spec = importlib.util.spec_from_loader("skills_required_guard", loader)
    if spec is None:
        return None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _fresh_ts() -> str:
    return datetime.datetime.now(tz=datetime.UTC).isoformat()


def _well_formed_brief(**overrides: Any) -> dict[str, Any]:
    """A CONTRACT-complete brief that the validator ACCEPTS (with a fresh notepad).

    Every required field present and non-empty; standard tier with a
    notepad_topic so the notepad ritual is satisfied.
    """
    brief: dict[str, Any] = {
        "goal": "Investigate the failing broker gate and report root cause",
        "context_files": ["src/broker/server.py"],
        "acceptance_criteria": ["root cause identified", "fix proposed"],
        "verification_required": ["uv run pytest -q"],
        "do_not_touch": ["pyproject.toml"],
        "notepad_topic": "scout-broker-gate",
        "task_tier": "standard",
    }
    brief.update(overrides)
    return brief


@pytest.fixture
def captured_state(monkeypatch) -> dict[str, Any]:
    """Neutralize side effects; capture what (if anything) write_state receives.

    Returns a dict the test can inspect: captured_state["written"] is the
    BrokerState passed to write_state, or None if the validator never wrote
    (i.e. did not approve). read_state returns a FRESH notepad by default so the
    notepad ritual is satisfied; a test can override read_state via monkeypatch
    to simulate a stale/absent notepad.
    """
    box: dict[str, Any] = {"written": None}

    monkeypatch.setattr(srv, "read_state", lambda: {"notepad_logged_at": _fresh_ts()})

    def _capture_write(state: Any) -> None:
        box["written"] = state

    monkeypatch.setattr(srv, "write_state", _capture_write)
    monkeypatch.setattr(srv, "log_broker_validation", lambda **kwargs: None)
    return box


async def _validate(brief: dict[str, Any], *, persona: str = "scout",
                    intent: str = "investigate", turn_id: str = "turn-1",
                    **kwargs: Any) -> srv.BrokerResult:
    return await srv.nexus_validate_brief(
        persona=persona, intent=intent,
        brief_json=json.dumps(brief), turn_id=turn_id, **kwargs,
    )


# ── ACCEPT: the well-formed CONTRACT brief ────────────────────────────────────

async def test_well_formed_contract_brief_is_accepted(captured_state) -> None:
    result = await _validate(_well_formed_brief())

    assert result["approved"] is True, result["errors"]
    assert result["errors"] == []
    # approved_brief echoes the parsed brief only on approval.
    assert result["approved_brief"] is not None
    assert result["approved_brief"]["goal"].startswith("Investigate")


async def test_approval_writes_broker_state_side_effect(captured_state) -> None:
    """On approve the validator MUST persist a BrokerState marking the approval.

    This is the load-bearing side effect broker-gate.py reads to permit the
    Task; an approval that does not write state would silently block dispatch.
    """
    result = await _validate(
        # quill-py is a code-writing persona — the NATIVE-25 HARD CHECK now
        # requires skills_required, so supply it; the assertion under test is the
        # broker-state WRITE side effect, not the skills gap.
        _well_formed_brief(skills_required=["tdd-patterns"]),
        persona="quill-py", intent="test", turn_id="turn-XYZ",
    )
    assert result["approved"] is True, result["errors"]

    written = captured_state["written"]
    assert written is not None, "approved validation did not write broker state"
    assert written["turn_id"] == "turn-XYZ"
    assert written["approved"] is True
    assert written["persona"] == "quill-py"
    assert written["called_at"], "approval state must stamp called_at"


async def test_approval_persists_gate_fields_into_approved_brief(captured_state) -> None:
    """TASK-083: the validated brief's gate fields land in state.approved_brief.

    nexus_validate_brief already parsed the full brief, so it single-sources the
    dispatch-gate fields (task_tier, work_type, intent, skills_required) into
    broker_state.json under `approved_brief`. broker-gate.py reads these instead
    of re-parsing a JSON block out of the Agent prompt — so the orchestrator no
    longer has to embed a full brief in every dispatch prompt.
    """
    brief = _well_formed_brief(
        task_tier="simple",
        work_type="implement_ui",
        skills_required=["forge-ui-conventions", "rsc-boundary-rules"],
    )
    result = await _validate(
        brief, persona="forge-ui", intent="implement_ui", turn_id="turn-083",
    )
    assert result["approved"] is True, result["errors"]

    written = captured_state["written"]
    assert written is not None
    persisted = written.get("approved_brief")
    assert isinstance(persisted, dict), f"approved_brief must be persisted: {written!r}"
    assert persisted["task_tier"] == "simple"
    assert persisted["work_type"] == "implement_ui"
    # intent is the function ARGUMENT, not a brief field — it must still be carried.
    assert persisted["intent"] == "implement_ui"
    assert persisted["skills_required"] == ["forge-ui-conventions", "rsc-boundary-rules"]


async def test_approved_brief_defaults_when_brief_omits_gate_fields(captured_state) -> None:
    """A brief that omits work_type/skills_required persists safe defaults.

    task_tier normalizes to the validator's 'standard' default; work_type empty
    string and skills_required empty list — never KeyErrors or None — so the
    downstream gate read (per-field fallback) stays well-typed.
    """
    brief = _well_formed_brief()  # no work_type / skills_required
    del brief["task_tier"]  # rely on the validator's default
    result = await _validate(
        brief, persona="scout", intent="investigate", turn_id="turn-083b",
    )
    assert result["approved"] is True, result["errors"]
    persisted = captured_state["written"]["approved_brief"]
    assert persisted["task_tier"] == "standard"
    assert persisted["work_type"] == ""
    assert persisted["intent"] == "investigate"
    assert persisted["skills_required"] == []


async def test_rejection_does_not_write_state(captured_state) -> None:
    """A rejected validation must NOT write an approval — no forged green state.

    A missing required FIELD is now normalized (shape gap), so to exercise the
    rejection-no-write path we drive a genuine HARD error: an invalid persona,
    which is NEVER normalized.
    """
    result = await _validate(_well_formed_brief(), persona="totally-made-up")

    assert result["approved"] is False
    assert result["approved_brief"] is None
    assert captured_state["written"] is None, "rejected validation wrote state"


# ── NORMALIZE: each missing required brief field defaults + warns (was reject) ─
# Dispatch-speed program: a missing required field is a mechanical SHAPE gap, not
# a quality gap — every required field has a safe default, so the brief is
# NORMALIZED (default injected) + WARNED + APPROVED on the first call instead of
# bounced. REQUIRED_BRIEF_FIELDS stays the single source of truth for the set.

@pytest.mark.parametrize("field", list(srv.REQUIRED_BRIEF_FIELDS))
async def test_missing_required_field_is_normalized_and_warned(
    captured_state, field: str
) -> None:
    brief = _well_formed_brief()
    del brief[field]
    result = await _validate(brief)

    assert result["approved"] is True, (
        f"missing {field!r} should normalize, not reject: {result['errors']}"
    )
    assert result["errors"] == []
    # A normalization warning naming the field is emitted.
    assert any(
        "normalized" in w and field in w for w in result["warnings"]
    ), f"no normalize-{field} warning in {result['warnings']}"
    # The persisted/returned brief carries a well-typed value for the field.
    assert result["approved_brief"] is not None
    assert field in result["approved_brief"]


def test_required_field_set_is_the_contract_surface() -> None:
    """Guard: the fields this test exercises ARE the validator's required set.

    If server.py adds/removes a required brief field, this asserts the test's
    parametrize source (REQUIRED_BRIEF_FIELDS) stays the single source of truth.
    """
    assert set(srv.REQUIRED_BRIEF_FIELDS) == {
        "goal", "context_files", "acceptance_criteria",
        "verification_required", "do_not_touch",
    }


# ── NORMALIZE: empty / wrong-typed required collections (was reject) ──────────

async def test_empty_goal_normalized_and_warned(captured_state) -> None:
    """Empty goal → placeholder + warning + APPROVED (was a hard reject)."""
    result = await _validate(_well_formed_brief(goal="   "))
    assert result["approved"] is True, result["errors"]
    assert result["errors"] == []
    assert any("normalized" in w and "goal" in w for w in result["warnings"])
    assert result["approved_brief"]["goal"].strip(), "goal must be non-empty after normalize"


async def test_empty_context_files_list_normalized_and_warned(captured_state) -> None:
    """Empty context_files → defaults to ['.'] + warning + APPROVED."""
    result = await _validate(_well_formed_brief(context_files=[]))
    assert result["approved"] is True, result["errors"]
    assert any("normalized" in w and "context_files" in w for w in result["warnings"])
    assert result["approved_brief"]["context_files"] == ["."]


async def test_non_list_context_files_normalized_and_warned(captured_state) -> None:
    """A bare-string context_files → coerced to a single-element list + APPROVED."""
    result = await _validate(_well_formed_brief(context_files="server.py"))
    assert result["approved"] is True, result["errors"]
    assert any("normalized" in w and "context_files" in w for w in result["warnings"])
    assert result["approved_brief"]["context_files"] == ["server.py"]


async def test_empty_acceptance_criteria_normalized_and_warned(captured_state) -> None:
    """Empty acceptance_criteria → placeholder list + warning + APPROVED."""
    result = await _validate(_well_formed_brief(acceptance_criteria=[]))
    assert result["approved"] is True, result["errors"]
    assert any("normalized" in w and "acceptance_criteria" in w for w in result["warnings"])
    assert result["approved_brief"]["acceptance_criteria"], "must be non-empty"


async def test_non_list_acceptance_criteria_normalized_and_warned(captured_state) -> None:
    """A bare-string acceptance_criteria → single-element list + APPROVED."""
    result = await _validate(_well_formed_brief(acceptance_criteria="root cause found"))
    assert result["approved"] is True, result["errors"]
    assert any("normalized" in w and "acceptance_criteria" in w for w in result["warnings"])
    assert result["approved_brief"]["acceptance_criteria"] == ["root cause found"]


async def test_empty_verification_required_normalized_and_warned(captured_state) -> None:
    """Empty verification_required → ['manual review'] + warning + APPROVED."""
    result = await _validate(_well_formed_brief(verification_required=[]))
    assert result["approved"] is True, result["errors"]
    assert any("normalized" in w and "verification_required" in w for w in result["warnings"])
    assert result["approved_brief"]["verification_required"] == ["manual review"]


# ── context_files cardinality — pins ACTUAL contract behaviour ────────────────

async def test_oversized_context_files_is_currently_accepted(captured_state) -> None:
    """CONTRACT TRUTH: the validator does NOT cap context_files at 5.

    The task brief mentions "oversized context_files (>5)"; the LIVE validator
    only rejects a non-list or an EMPTY list — there is no >5 cap. This test
    pins the real behaviour (a 6-element list with everything else well-formed
    is ACCEPTED) so a future cap is a deliberate, test-visible change rather than
    a silent one. See NOTES in the OPT-053 report: adding a >5 cap is a
    follow-up that would require a src edit (out of scope here).
    """
    six = [f"src/broker/file_{i}.py" for i in range(6)]
    result = await _validate(_well_formed_brief(context_files=six))

    assert result["approved"] is True, result["errors"]
    assert not any("context_files" in e for e in result["errors"])


# ── REJECT: invalid persona / illegal intent ─────────────────────────────────

async def test_unknown_persona_is_rejected(captured_state) -> None:
    result = await _validate(_well_formed_brief(), persona="totally-made-up")
    assert result["approved"] is False
    assert any("not in the dispatch registry" in e for e in result["errors"])


@pytest.mark.parametrize("retired", sorted(RETIRED_BASE_PERSONAS))
async def test_retired_base_persona_is_rejected(captured_state, retired: str) -> None:
    """The retired base names (forge/pipeline/quill) are NOT dispatch targets."""
    assert retired not in ALLOWED_PERSONAS
    result = await _validate(_well_formed_brief(), persona=retired, intent="implement_ui")
    assert result["approved"] is False
    assert any("not in the dispatch registry" in e for e in result["errors"])


async def test_legal_persona_illegal_intent_is_snapped_not_rejected(captured_state) -> None:
    """A VALID persona with an off-row intent is SNAPPED to its legal intent + warned.

    (was a hard reject) — an illegal intent for a valid persona is a mechanical
    SHAPE problem (freeform / mis-typed token), not a routing failure. scout's
    only legal intent is 'investigate', so any illegal intent snaps there and the
    dispatch APPROVES with a snap warning. The persisted intent is the legal one.
    """
    result = await _validate(_well_formed_brief(), persona="scout", intent="implement_ui")
    assert result["approved"] is True, result["errors"]
    assert any(
        "normalized" in w and "snapped" in w and "investigate" in w
        for w in result["warnings"]
    ), result["warnings"]
    # The approved-state intent reflects the snap, not the illegal request.
    assert captured_state["written"]["approved_brief"]["intent"] == "investigate"


# ── REJECT: unparseable brief JSON ────────────────────────────────────────────

async def test_invalid_brief_json_is_rejected(captured_state) -> None:
    result = await srv.nexus_validate_brief(
        persona="scout", intent="investigate",
        brief_json="{ this is not valid json", turn_id="t-json",
    )
    assert result["approved"] is False
    assert any("not valid JSON" in e for e in result["errors"])
    # A parse failure must short-circuit the field checks (no spurious
    # "missing required field" noise on top of the parse error).
    assert not any("missing required field" in e for e in result["errors"])
    assert captured_state["written"] is None


# ── notepad ritual: tier-dependent stale-notepad handling ─────────────────────

async def test_complex_tier_stale_notepad_is_rejected(captured_state, monkeypatch) -> None:
    """Complex + stale/absent notepad → ERROR (rejected)."""
    monkeypatch.setattr(srv, "read_state", lambda: {})  # no notepad_logged_at
    result = await _validate(_well_formed_brief(task_tier="complex"))
    assert result["approved"] is False
    assert any(
        "notepad ritual required for Complex tasks" in e for e in result["errors"]
    )


async def test_standard_tier_stale_notepad_is_only_a_warning(captured_state, monkeypatch) -> None:
    """Standard + stale/absent notepad → WARNING only; still APPROVED."""
    monkeypatch.setattr(srv, "read_state", lambda: {})  # no notepad_logged_at
    result = await _validate(_well_formed_brief(task_tier="standard"))
    assert result["approved"] is True, result["errors"]
    assert any("absent or stale" in w for w in result["warnings"])
    assert result["errors"] == []


async def test_standard_tier_missing_notepad_topic_is_derived_and_warned(captured_state) -> None:
    """Standard/complex missing notepad_topic → DERIVED + warned + APPROVED.

    (was a hard reject) — a missing topic is a SHAPE gap; derive one from
    work_type → intent → goal and warn instead of bouncing the dispatch.
    """
    brief = _well_formed_brief(work_type="refactor-broker")
    del brief["notepad_topic"]
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["errors"] == []
    assert any(
        "normalized" in w and "notepad_topic" in w for w in result["warnings"]
    ), result["warnings"]
    # A non-empty topic was derived and persisted into the brief.
    assert result["approved_brief"]["notepad_topic"]


# ── router pre-fill mismatch is a warning, not a rejection ────────────────────

async def test_router_prefill_mismatch_is_a_warning_not_rejection(captured_state) -> None:
    result = await _validate(
        _well_formed_brief(), persona="scout", router_pre_fill="lens",
    )
    assert result["approved"] is True, result["errors"]
    assert any("router pre-fill was 'lens'" in w for w in result["warnings"])


# ── NATIVE-25: consolidated dispatch PRE-FLIGHT ───────────────────────────────
# validate now surfaces, in ONE call, the downstream-gate requirements that
# previously only fired at Task-dispatch time (the '5 dispatch attempts' friction):
#   (i)  skills_required HARD CHECK — code-writing persona + missing skills → reject
#   (ii) free-text 'skills_required: a, b' is accepted for a code-writing persona
#   (iii) Standard/Complex code-writing dispatch carries a planning-gate ADVISORY
#   (iv) Plexus-meta / non-feature / non-code-writing / Simple → NO advisory, NOT blocked


async def test_code_writing_persona_missing_skills_required_is_rejected(captured_state) -> None:
    """(i) A code-writing persona with NO skills_required → approved=false, error names it.

    This MOVES skills-required-guard.sh Gate-1 earlier (same condition: code-writing
    persona + empty/missing skills_required). The well-formed brief omits
    skills_required entirely, so the HARD CHECK must reject and the message must
    name 'skills_required'.
    """
    brief = _well_formed_brief(work_type="implement_ingestion")
    # no skills_required key at all
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-skills-1",
    )
    assert result["approved"] is False, "code-writing persona w/o skills must reject"
    assert any("skills_required" in e for e in result["errors"]), result["errors"]
    assert any("pipeline-data" in e for e in result["errors"])
    assert captured_state["written"] is None, "rejected validation must not write state"


async def test_code_writing_persona_empty_skills_list_is_rejected(captured_state) -> None:
    """(i') An explicitly EMPTY skills_required list is the same denial as missing."""
    brief = _well_formed_brief(work_type="implement_ui", skills_required=[])
    result = await _validate(
        brief, persona="forge-ui", intent="implement_ui", turn_id="t-skills-2",
    )
    assert result["approved"] is False
    assert any("skills_required" in e for e in result["errors"])


async def test_code_writing_persona_with_skills_list_is_accepted(captured_state) -> None:
    """(ii-list) A code-writing persona WITH a skills_required list passes the HARD CHECK."""
    brief = _well_formed_brief(
        work_type="implement_ingestion",
        skills_required=["pipeline-data-conventions", "polars-duckdb-mapping"],
    )
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-skills-3",
    )
    assert result["approved"] is True, result["errors"]
    assert not any("skills_required" in e for e in result["errors"])


async def test_code_writing_persona_freetext_skills_required_is_accepted(captured_state) -> None:
    """(ii) Free-text 'skills_required: x, y' (string, not list) is accepted.

    The validator normalizes a comma-separated string the same way the guard's
    free-text path does, so a prose skills_required value satisfies the HARD CHECK.
    """
    brief = _well_formed_brief(
        work_type="implement_ingestion",
        skills_required="pipeline-data-conventions, polars-duckdb-mapping",
    )
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-skills-4",
    )
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"] is not None


async def test_non_code_writing_persona_missing_skills_is_not_blocked(captured_state) -> None:
    """(iv-skills) A non-code-writing persona (scout) is UNAFFECTED by the HARD CHECK."""
    result = await _validate(
        _well_formed_brief(), persona="scout", intent="investigate", turn_id="t-skills-5",
    )
    assert result["approved"] is True, result["errors"]
    assert not any("skills_required" in e for e in result["errors"])


async def test_standard_code_writing_dispatch_carries_planning_gate_advisory(captured_state) -> None:
    """(iii) Standard-tier code-writing dispatch → planning-gate ADVISORY warning.

    The advisory is condition-derived (tier + code-writing persona/intent), does
    NOT read project.db, and does NOT flip approved — the dispatch is still
    APPROVED, only WARNED.
    """
    brief = _well_formed_brief(
        task_tier="standard",
        work_type="implement_ingestion",
        skills_required=["pipeline-data-conventions"],
    )
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-pg-1",
    )
    assert result["approved"] is True, result["errors"]
    assert any("PLANNING-GATE ADVISORY" in w for w in result["warnings"]), result["warnings"]


async def test_complex_code_writing_dispatch_carries_planning_gate_advisory(captured_state) -> None:
    """(iii') Complex tier behaves identically — advisory present, still approved."""
    brief = _well_formed_brief(
        task_tier="complex",
        work_type="implement_schema",
        skills_required=["atlas-schema-patterns"],
    )
    result = await _validate(
        brief, persona="atlas", intent="implement_schema", turn_id="t-pg-2",
    )
    assert result["approved"] is True, result["errors"]
    assert any("PLANNING-GATE ADVISORY" in w for w in result["warnings"])


async def test_simple_tier_code_writing_has_no_planning_gate_advisory(captured_state) -> None:
    """(iv-tier) Simple tier is OUT OF SCOPE for the planning gate — NO advisory."""
    brief = _well_formed_brief(
        task_tier="simple",
        work_type="implement_ingestion",
        skills_required=["pipeline-data-conventions"],
    )
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-pg-3",
    )
    assert result["approved"] is True, result["errors"]
    assert not any("PLANNING-GATE ADVISORY" in w for w in result["warnings"])


async def test_non_code_writing_standard_dispatch_has_no_planning_gate_advisory(captured_state) -> None:
    """(iv) Plexus-meta / non-code-writing Standard dispatch → NO advisory, NOT blocked.

    A scout investigation at standard tier is the canonical Plexus-meta /
    non-feature path: the planning-gate exemption MUST hold (no advisory) and the
    skills HARD CHECK MUST NOT block it.
    """
    result = await _validate(
        _well_formed_brief(task_tier="standard", work_type="investigate"),
        persona="scout", intent="investigate", turn_id="t-pg-4",
    )
    assert result["approved"] is True, result["errors"]
    assert not any("PLANNING-GATE ADVISORY" in w for w in result["warnings"])
    assert not any("skills_required" in e for e in result["errors"])


async def test_malformed_brief_emits_no_preflight_noise(captured_state) -> None:
    """(scope) A JSON-parse failure short-circuits the PRE-FLIGHT entirely.

    Neither the skills HARD CHECK nor the planning-gate advisory may fire on an
    unparseable brief — the only error is the parse error.
    """
    result = await srv.nexus_validate_brief(
        persona="pipeline-data", intent="implement_ingestion",
        brief_json="{ not valid json", turn_id="t-preflight-malformed",
    )
    assert result["approved"] is False
    assert any("not valid JSON" in e for e in result["errors"])
    assert not any("skills_required is absent" in e for e in result["errors"])
    assert not any("PLANNING-GATE ADVISORY" in w for w in result["warnings"])


# ════════════════════════════════════════════════════════════════════════════
# DISPATCH-SPEED PROGRAM — NORMALIZE-instead-of-REJECT
# A mechanical field-SHAPE problem (wrong type, empty collection, missing
# default-able field, freeform intent, difficulty-instead-of-tier) is COERCED +
# WARNED + APPROVED on the FIRST call, killing the 2-3 round-trip rejections.
# Real guardrails (invalid persona, JSON parse, Complex stale-notepad, skills
# HARD CHECK, genuine-feature planning gate) are PRESERVED. Asserts POSITIVE
# invariants throughout.
# ════════════════════════════════════════════════════════════════════════════


# ── (a) freeform / illegal intent snaps to legal + approved + warning ─────────

async def test_freeform_intent_snaps_to_legal_and_approves(captured_state) -> None:
    """A close-but-illegal intent token snaps to the persona's matching legal intent."""
    # forge-ui legal intents: implement_ui, implement_api. 'implement_the_ui'
    # token-overlaps 'implement_ui' → snaps there.
    result = await _validate(
        _well_formed_brief(
            task_tier="simple",
            work_type="implement_ui",
            skills_required=["forge-ui-conventions"],
        ),
        persona="forge-ui", intent="implement_the_ui", turn_id="t-snap-1",
    )
    assert result["approved"] is True, result["errors"]
    assert any(
        "normalized" in w and "snapped" in w and "implement_ui" in w
        for w in result["warnings"]
    ), result["warnings"]
    assert captured_state["written"]["approved_brief"]["intent"] == "implement_ui"


async def test_unmatched_intent_falls_back_to_primary_intent(captured_state) -> None:
    """A garbage intent with no overlap snaps to the persona's PRIMARY (first) intent."""
    result = await _validate(
        _well_formed_brief(),
        persona="scout", intent="zzz-nonsense", turn_id="t-snap-2",
    )
    assert result["approved"] is True, result["errors"]
    # scout's only / primary intent is 'investigate'.
    assert captured_state["written"]["approved_brief"]["intent"] == "investigate"
    assert any("snapped" in w and "investigate" in w for w in result["warnings"])


# ── (b) empty context_files defaults + approved + warning ─────────────────────

async def test_missing_context_files_defaults_and_approves(captured_state) -> None:
    """A brief with NO context_files key at all defaults to ['.'] + warns + approves."""
    brief = _well_formed_brief()
    del brief["context_files"]
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["context_files"] == ["."]
    assert any("normalized" in w and "context_files" in w for w in result["warnings"])


# ── (c) difficulty coerces to task_tier ───────────────────────────────────────

@pytest.mark.parametrize(
    ("difficulty", "expected_tier"),
    [
        ("trivial", "simple"),
        ("easy", "simple"),
        ("medium", "standard"),
        ("hard", "complex"),
        ("complex", "complex"),
    ],
)
async def test_difficulty_coerces_to_task_tier(
    captured_state, difficulty: str, expected_tier: str
) -> None:
    """A router pre-fill `difficulty` with NO task_tier coerces to the mapped tier."""
    brief = _well_formed_brief(difficulty=difficulty)
    del brief["task_tier"]
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["task_tier"] == expected_tier
    assert captured_state["written"]["approved_brief"]["task_tier"] == expected_tier
    assert any(
        "normalized" in w and "difficulty" in w and expected_tier in w
        for w in result["warnings"]
    ), result["warnings"]


async def test_explicit_task_tier_wins_over_difficulty(captured_state) -> None:
    """When BOTH are present the explicit task_tier is authoritative (no coercion)."""
    brief = _well_formed_brief(task_tier="simple", difficulty="hard")
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["task_tier"] == "simple"


async def test_invalid_task_tier_snaps_to_standard(captured_state) -> None:
    """An unrecognized task_tier value snaps to 'standard' + warns (not rejected)."""
    result = await _validate(_well_formed_brief(task_tier="ULTRA-MEGA"))
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["task_tier"] == "standard"
    assert any(
        "normalized" in w and "task_tier" in w and "standard" in w
        for w in result["warnings"]
    )


# ── (d) files_touched_estimate as an array is accepted (coerced to length) ────

async def test_files_touched_estimate_array_coerced_to_length(captured_state) -> None:
    """An ARRAY files_touched_estimate is accepted and coerced to its length (int)."""
    brief = _well_formed_brief(
        files_touched_estimate=["a.py", "b.py", "c.py"],
    )
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["files_touched_estimate"] == 3
    assert any(
        "normalized" in w and "files_touched_estimate" in w for w in result["warnings"]
    )


async def test_files_touched_estimate_int_accepted_as_is(captured_state) -> None:
    """An int files_touched_estimate is accepted unchanged, with no warning."""
    result = await _validate(_well_formed_brief(files_touched_estimate=4))
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["files_touched_estimate"] == 4
    assert not any("files_touched_estimate" in w for w in result["warnings"])


async def test_files_touched_estimate_string_normalized_to_int_and_warned(captured_state) -> None:
    """A numeric-string files_touched_estimate coerces to int + warns."""
    result = await _validate(_well_formed_brief(files_touched_estimate="7"))
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["files_touched_estimate"] == 7
    assert any(
        "normalized" in w and "files_touched_estimate" in w for w in result["warnings"]
    )


# ── (e) missing tier inferred ─────────────────────────────────────────────────

async def test_missing_task_tier_and_difficulty_defaults_to_standard(captured_state) -> None:
    """Missing task_tier with NO difficulty hint defaults to 'standard' + warns."""
    brief = _well_formed_brief()
    del brief["task_tier"]
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["approved_brief"]["task_tier"] == "standard"
    assert any(
        "normalized" in w and "task_tier" in w for w in result["warnings"]
    )


# ── (f) multiple gaps returned together in ONE call (collect-all) ─────────────

async def test_multiple_mechanical_gaps_all_normalized_in_one_call(captured_state) -> None:
    """COLLECT-ALL: several shape gaps in one brief are ALL normalized → approved.

    Empty goal + bare-string context_files + empty acceptance_criteria + missing
    verification_required + difficulty-instead-of-tier — every one is a mechanical
    SHAPE gap, so the brief approves on the FIRST call with one warning per gap
    and ZERO errors (never bounced one-at-a-time).
    """
    brief = {
        "goal": "   ",
        "context_files": "server.py",
        "acceptance_criteria": [],
        "do_not_touch": [],
        "difficulty": "easy",  # no task_tier
        # verification_required + notepad_topic + task_tier all omitted
    }
    result = await _validate(brief)
    assert result["approved"] is True, result["errors"]
    assert result["errors"] == [], "no mechanical gap should land in errors[]"
    # Each gap produced a normalization warning — collected together.
    normalized = [w for w in result["warnings"] if "normalized" in w]
    assert len(normalized) >= 4, normalized
    ab = result["approved_brief"]
    assert ab["goal"].strip()
    assert ab["context_files"] == ["server.py"]
    assert ab["acceptance_criteria"]
    assert ab["verification_required"]
    assert ab["task_tier"] == "simple"  # coerced from difficulty 'easy'


async def test_approved_brief_contains_normalized_values(captured_state) -> None:
    """The PERSISTED state (write_state arg) carries the COERCED types, not the raw input.

    Drives a code-writing dispatch so the approved_brief gate fields are written,
    and asserts the normalized intent + tier land in the persisted state.
    """
    brief = _well_formed_brief(
        context_files="src/x.py",        # bare string → ['src/x.py']
        difficulty="hard",                # → tier complex
        skills_required=["forge-ui-conventions"],
        work_type="implement_ui",
    )
    del brief["task_tier"]
    result = await _validate(
        brief, persona="forge-ui", intent="build_the_ui", turn_id="t-norm-persist",
    )
    assert result["approved"] is True, result["errors"]
    written = captured_state["written"]
    assert written is not None, "approved validation must write state"
    persisted = written["approved_brief"]
    # intent snapped (build_the_ui → implement_ui via token overlap on 'ui'/'implement')
    assert persisted["intent"] in {"implement_ui", "implement_api"}
    assert persisted["task_tier"] == "complex"  # coerced from difficulty 'hard'
    # The returned brief echoes the coerced context_files type.
    assert result["approved_brief"]["context_files"] == ["src/x.py"]


# ── (g) GUARDRAILS — normalization NEVER relaxes a real guardrail ─────────────

async def test_guardrail_invalid_persona_still_errors(captured_state) -> None:
    """PRESERVED: an INVALID persona is a HARD error — never snapped/normalized."""
    result = await _validate(_well_formed_brief(), persona="not-a-real-persona")
    assert result["approved"] is False
    assert any("not in the dispatch registry" in e for e in result["errors"])
    assert captured_state["written"] is None


async def test_guardrail_complex_stale_notepad_still_errors(captured_state, monkeypatch) -> None:
    """PRESERVED: Complex tier + stale/absent notepad is STILL a hard error."""
    monkeypatch.setattr(srv, "read_state", lambda: {})  # no notepad_logged_at
    result = await _validate(_well_formed_brief(task_tier="complex"))
    assert result["approved"] is False
    assert any("notepad ritual required for Complex tasks" in e for e in result["errors"])


async def test_guardrail_standard_stale_notepad_still_only_warns(captured_state, monkeypatch) -> None:
    """PRESERVED: Standard tier + stale/absent notepad is STILL only a warning."""
    monkeypatch.setattr(srv, "read_state", lambda: {})
    result = await _validate(_well_formed_brief(task_tier="standard"))
    assert result["approved"] is True, result["errors"]
    assert any("absent or stale" in w for w in result["warnings"])


async def test_guardrail_code_writing_persona_missing_skills_still_errors(captured_state) -> None:
    """PRESERVED: the skills_required HARD CHECK is NOT normalized away."""
    brief = _well_formed_brief(work_type="implement_ingestion")  # no skills_required
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-grd-skills",
    )
    assert result["approved"] is False
    assert any("skills_required" in e for e in result["errors"])


# ── (g) planning-gate SCOPE: genuine feature requires it; bugfix does NOT ──────

async def test_genuine_standard_feature_still_carries_planning_gate_advisory(captured_state) -> None:
    """SCOPE: a real standard FEATURE (feature-like work_type) STILL gets the advisory."""
    brief = _well_formed_brief(
        task_tier="standard",
        work_type="implement_ui",  # genuine feature work
        skills_required=["forge-ui-conventions"],
    )
    result = await _validate(
        brief, persona="forge-ui", intent="implement_ui", turn_id="t-pg-feat",
    )
    assert result["approved"] is True, result["errors"]
    assert any("PLANNING-GATE ADVISORY" in w for w in result["warnings"]), result["warnings"]


async def test_standard_bugfix_does_not_require_planning_gate(captured_state) -> None:
    """SCOPE: a standard-tier BUGFIX (non-feature work_type) gets NO planning-gate advisory.

    Even though the persona is code-writing and the tier is standard, a bugfix is
    NOT a feature, so the spec-first planning gate does not apply — the advisory
    must NOT fire.
    """
    brief = _well_formed_brief(
        task_tier="standard",
        work_type="bugfix",
        skills_required=["pipeline-data-conventions"],
    )
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-pg-bug",
    )
    assert result["approved"] is True, result["errors"]
    assert not any("PLANNING-GATE ADVISORY" in w for w in result["warnings"]), result["warnings"]


async def test_standard_chore_does_not_require_planning_gate(captured_state) -> None:
    """SCOPE: a chore/meta work_type at standard tier gets NO planning-gate advisory."""
    brief = _well_formed_brief(
        task_tier="standard",
        work_type="chore",
        skills_required=["pipeline-data-conventions"],
    )
    result = await _validate(
        brief, persona="pipeline-data", intent="implement_ingestion", turn_id="t-pg-chore",
    )
    assert result["approved"] is True, result["errors"]
    assert not any("PLANNING-GATE ADVISORY" in w for w in result["warnings"])


# ── NATIVE-25 (v): skills-required-guard free-text detection ──────────────────


@pytest.mark.skipif(
    not _GUARD_PATH.exists(), reason="live skills-required-guard.sh not in this checkout"
)
def test_guard_extract_brief_detects_freetext_skills_required() -> None:
    """The guard now sees a PROSE 'skills_required: x' line, not only a ```json block.

    Before the fix _extract_brief parsed ONLY a fenced JSON block or whole-field
    JSON, so a brief written as prose silently yielded {} and a code-writing
    persona slipped past Gate 1. Feed a description carrying a prose
    skills_required line and assert it is extracted.
    """
    guard = _load_skills_guard()
    assert guard is not None
    tool_input = {
        "subagent_type": "forge-ui",
        "description": (
            "Implement the dashboard card.\n"
            "skills_required: forge-ui-conventions, rsc-boundary-rules\n"
            "work_type: implement_ui\n"
        ),
    }
    brief = guard._extract_brief(tool_input)
    assert "forge-ui-conventions" in brief.get("skills_required", [])
    assert "rsc-boundary-rules" in brief.get("skills_required", [])


@pytest.mark.skipif(
    not _GUARD_PATH.exists(), reason="live skills-required-guard.sh not in this checkout"
)
def test_guard_freetext_backfills_json_brief_missing_skills() -> None:
    """A JSON brief WITHOUT skills_required is backfilled from a prose line.

    The free-text path is additive: it never overrides a JSON skills_required,
    but it DOES backfill when the JSON omitted it.
    """
    guard = _load_skills_guard()
    assert guard is not None
    tool_input = {
        "description": (
            '```json\n{"goal": "x", "work_type": "implement_ui"}\n```\n'
            "skills_required: forge-ui-conventions\n"
        ),
    }
    brief = guard._extract_brief(tool_input)
    assert brief.get("skills_required") == ["forge-ui-conventions"]


@pytest.mark.skipif(
    not _GUARD_PATH.exists(), reason="live skills-required-guard.sh not in this checkout"
)
def test_guard_freetext_does_not_override_json_skills() -> None:
    """A JSON brief that already carries skills_required is NOT overridden by prose."""
    guard = _load_skills_guard()
    assert guard is not None
    tool_input = {
        "description": (
            '```json\n{"skills_required": ["from-json"]}\n```\n'
            "skills_required: from-prose\n"
        ),
    }
    brief = guard._extract_brief(tool_input)
    assert brief.get("skills_required") == ["from-json"]

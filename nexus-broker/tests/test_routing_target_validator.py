"""test_routing_target_validator.py — drift guard for the new PreToolUse hook
`routing-target-validator.py` (research/decisions/2026-07-16-routing-target-
validation-gate.md, accepted).

Locks the hook's enum sources to their canonical origins and fails if they
diverge:

  1. The hook's derived LIVE persona set (from deliverables.json) equals the
     broker registry's own `ALLOWED_PERSONAS` — the same ground truth
     `test_base_name_retirement.py` / `test_pro_variant_retirement.py`
     already anchor to. This is the load-bearing cross-check the ADR's
     Open Question #2 resolves (deliverables.json canonical; the registry
     pins agreement).
  2. The hook's derived RETIRED set is a superset of the registry's own
     `RETIRED_BASE_PERSONAS | RETIRED_PRO_PERSONAS`.
  3. The hook's skill-enum resolution points at THIS tree's real installed
     `.claude/skills/*/` dir listing (never a hardcoded/foreign one).

Also exercises the hook end-to-end (subprocess, both live and package hook
trees) for the exact retired/hallucinated targets named in the brief's
acceptance criteria, and confirms live personas pass through with zero
added friction.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from broker.registry import ALLOWED_PERSONAS, RETIRED_BASE_PERSONAS, RETIRED_PRO_PERSONAS

# tests/ -> nexus-broker/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_HOOK = REPO_ROOT / ".claude" / "hooks" / "routing-target-validator.py"
PKG_HOOK = REPO_ROOT / "nexus-package" / ".claude" / "hooks" / "routing-target-validator.py"
HOOK_TREES = [p for p in (LIVE_HOOK, PKG_HOOK) if p.exists()]


def _load_hook_module(hook_path: Path):
    spec = importlib.util.spec_from_file_location(
        f"routing_target_validator_under_test_{hook_path.parent.parent.parent.name}",
        hook_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _run_hook(
    hook_path: Path, subagent_type: str, skills_required: list | None = None
) -> tuple:
    tool_input = {"subagent_type": subagent_type, "description": "", "prompt": ""}
    if skills_required is not None:
        tool_input["description"] = json.dumps({"skills_required": skills_required})
    payload = json.dumps({"tool_input": tool_input})
    proc = subprocess.run(
        [sys.executable, str(hook_path)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout.strip()
    parsed: dict = {}
    if out:
        parsed = json.loads(out.splitlines()[-1])
    return proc.returncode, parsed


# ---------------------------------------------------------------------------
# 1. Persona-enum agreement: hook classification vs deliverables.json vs the
#    broker registry.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_hook_manifest_loads(hook_path: Path) -> None:
    module = _load_hook_module(hook_path)
    manifest = module._load_manifest()
    assert manifest is not None, f"deliverables.json failed to load for {hook_path}"
    assert isinstance(manifest, dict) and manifest


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_hook_live_and_retired_sets_partition_the_manifest(hook_path: Path) -> None:
    """Every non-underscore manifest key is classified exactly once, live XOR retired."""
    module = _load_hook_module(hook_path)
    manifest = module._load_manifest()
    live, retired = module._classify_personas(manifest)
    manifest_keys = {k for k in manifest if not k.startswith("_") and isinstance(manifest[k], dict)}
    assert live | retired == manifest_keys
    assert live & retired == set()


def test_live_hook_persona_set_equals_broker_registry_allowed_personas() -> None:
    """This tree's hook-derived live-persona set matches THIS tree's own
    broker.registry.ALLOWED_PERSONAS (deliverables.json canonical, agreement
    pinned against the registry per the ADR's Open Question #2 resolution).

    codex-worker/codex-reviewer are the one deliberate exception: they ship
    an always-present, non-tombstoned deliverables.json row in EVERY tree
    (test_deliverables_persona_drift.py's coverage requirement for a
    registry-conditionally-dispatchable persona), but broker.registry only
    ADDS them to ALLOWED_PERSONAS when THIS tree's own
    .claude/agents/codex-worker.md + codex-reviewer.md actually exist on
    disk (registry._codex_lane_agent_files_present — a this-machine contrib
    lane, absent by design from a standard package install). The hook has no
    on-disk-file gate of its own (deliverables.json note-text is its only
    source), so mirror the registry's own gate here rather than assume the
    hook's roster is already lane-aware."""
    module = _load_hook_module(LIVE_HOOK)
    manifest = module._load_manifest()
    live, _retired = module._classify_personas(manifest)
    codex_agents_dir = LIVE_HOOK.parent.parent / "agents"
    codex_lane_present = (
        (codex_agents_dir / "codex-worker.md").is_file()
        and (codex_agents_dir / "codex-reviewer.md").is_file()
    )
    expected_live = live if codex_lane_present else (live - {"codex-worker", "codex-reviewer"})
    assert expected_live == ALLOWED_PERSONAS, (
        "routing-target-validator LIVE set (codex-lane-adjusted) diverges from "
        f"broker.registry.ALLOWED_PERSONAS: only-in-hook={expected_live - ALLOWED_PERSONAS}, "
        f"only-in-registry={ALLOWED_PERSONAS - expected_live}"
    )


def test_live_hook_retired_set_is_superset_of_registry_retired_names() -> None:
    """deliverables.json's tombstone markers must cover at least every name
    the broker registry itself retired (base names + -pro variants)."""
    module = _load_hook_module(LIVE_HOOK)
    manifest = module._load_manifest()
    _live, retired = module._classify_personas(manifest)
    registry_retired = RETIRED_BASE_PERSONAS | RETIRED_PRO_PERSONAS
    missing = registry_retired - retired
    assert not missing, (
        f"deliverables.json does not mark these registry-retired names as "
        f"tombstoned: {missing}"
    )


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_base_names_are_tombstoned_but_carved_out_for_defer(hook_path: Path) -> None:
    module = _load_hook_module(hook_path)
    manifest = module._load_manifest()
    _live, retired = module._classify_personas(manifest)
    for base in sorted(module.BASE_NAMES):
        assert base in retired, f"'{base}' expected tombstoned in deliverables.json"
    assert module.BASE_NAMES == frozenset({"forge", "pipeline", "quill"})


# ---------------------------------------------------------------------------
# 2. End-to-end subprocess behavior — the 7 ADR-named retired/hallucinated
#    targets, plus a representative live sample (acceptance criteria 1 + 2).
# ---------------------------------------------------------------------------

_RETIRED_PRO_NAMES = sorted(RETIRED_PRO_PERSONAS)
_BASE_NAMES_PARAM = sorted(RETIRED_BASE_PERSONAS)


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
@pytest.mark.parametrize("pro_name", _RETIRED_PRO_NAMES)
def test_retired_pro_variant_denied_with_typed_cause(hook_path: Path, pro_name: str) -> None:
    """forge-ui-pro / forge-wire-pro / pipeline-data-pro / pipeline-async-pro
    are hard-denied by THIS hook — persona-alias-resolver.sh only advises for
    these (never denies), so this hook closes that gap."""
    code, parsed = _run_hook(hook_path, pro_name)
    assert code == 2, f"expected exit 2 for retired '{pro_name}', got {code}: {parsed}"
    out = parsed.get("hookSpecificOutput", {})
    assert out.get("permissionDecision") == "deny"
    assert "ROUTING/RETIRED-PERSONA" in out.get("permissionDecisionReason", "")


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
@pytest.mark.parametrize("base_name", _BASE_NAMES_PARAM)
def test_base_name_deferred_to_alias_resolver_not_double_denied(
    hook_path: Path, base_name: str
) -> None:
    """forge/pipeline/quill: this hook takes NO action (exit 0, no JSON of its
    own) — persona-alias-resolver.sh owns the redirect/deny decision for
    these, and this hook must never double-deny or shadow it."""
    code, parsed = _run_hook(hook_path, base_name)
    assert code == 0, f"'{base_name}' must defer (exit 0) to persona-alias-resolver, got {code}"
    assert parsed == {}, f"'{base_name}' must emit no hook JSON of its own: {parsed}"


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_hallucinated_persona_denied_with_typed_cause_and_nearest_hint(hook_path: Path) -> None:
    code, parsed = _run_hook(hook_path, "forge-backend")
    assert code == 2, f"expected exit 2 for hallucinated persona, got {code}: {parsed}"
    out = parsed.get("hookSpecificOutput", {})
    assert out.get("permissionDecision") == "deny"
    reason = out.get("permissionDecisionReason", "")
    assert "ROUTING/UNKNOWN-PERSONA" in reason
    assert "forge-backend" in reason


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_typo_persona_denied(hook_path: Path) -> None:
    code, parsed = _run_hook(hook_path, "atals")
    assert code == 2
    reason = parsed.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
    assert "ROUTING/UNKNOWN-PERSONA" in reason


def test_live_personas_pass_through_with_zero_friction() -> None:
    """A representative sample of LIVE personas: exit 0, no deny/advise JSON
    at all (acceptance criterion 2 — zero added friction)."""
    module = _load_hook_module(LIVE_HOOK)
    manifest = module._load_manifest()
    live, _retired = module._classify_personas(manifest)
    sample = sorted(live)[:6]
    for persona in sample:
        code, parsed = _run_hook(LIVE_HOOK, persona)
        assert code == 0, f"live persona '{persona}' unexpectedly blocked: {parsed}"
        assert parsed == {}, f"live persona '{persona}' got unexpected hook output: {parsed}"


def test_bookkeeping_payload_with_no_persona_passes_silently() -> None:
    """A plain TaskCreate/TaskUpdate payload with no subagent_type/agent_type
    is not a dispatch this hook can classify — silent pass."""
    payload = json.dumps({"tool_input": {"description": "update a task"}})
    proc = subprocess.run(
        [sys.executable, str(LIVE_HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ---------------------------------------------------------------------------
# 3. Skill enum: real installed skills dir, fail-open advisory on an unknown
#    skill name.
# ---------------------------------------------------------------------------

def test_unknown_skill_name_is_loud_advisory_not_a_deny() -> None:
    code, parsed = _run_hook(
        LIVE_HOOK, "hermes", skills_required=["agent-protocol", "totally-fake-skill-xyz"]
    )
    assert code == 0, "an unresolvable skill must never block the dispatch (fail-open)"
    out = parsed.get("hookSpecificOutput", {})
    assert "permissionDecision" not in out
    ctx = out.get("additionalContext", "")
    assert "ROUTING/UNKNOWN-SKILL" in ctx
    assert "totally-fake-skill-xyz" in ctx


def test_known_skill_names_produce_no_advisory() -> None:
    """Both names must resolve in EVERY tree this test runs in (live and
    package): "deployable-engineering" is Plexus-meta-only (OD-3) and never
    ships under nexus-package/.claude/skills/, so it would false-positive an
    advisory when this file is snapshotted into the package test tree.
    "verification" ships in both trees' .claude/skills/ dir."""
    code, parsed = _run_hook(
        LIVE_HOOK, "hermes", skills_required=["agent-protocol", "verification"]
    )
    assert code == 0
    assert parsed == {}


def test_namespaced_skill_name_is_skipped_never_flagged() -> None:
    """A plugin/namespaced skill name (containing ':') is not resolvable
    against a local dir listing and must never be flagged as unknown."""
    code, parsed = _run_hook(LIVE_HOOK, "hermes", skills_required=["socraticode:codebase-exploration"])
    assert code == 0
    assert parsed == {}


@pytest.mark.parametrize("hook_path", HOOK_TREES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_skill_enum_matches_real_installed_skills_dir(hook_path: Path) -> None:
    """The hook's skills-dir resolution must point at THIS tree's own
    installed .claude/skills/*/ listing, not a hardcoded/foreign one."""
    module = _load_hook_module(hook_path)
    expected_root = hook_path.parent.parent.parent  # .claude/hooks -> .claude -> tree root
    expected_skills_dir = expected_root / ".claude" / "skills"
    resolved = module._skills_dir()
    assert resolved == expected_skills_dir
    assert resolved.is_dir()


# ---------------------------------------------------------------------------
# 4. Twin consistency (acceptance criterion 4): live and package hook bodies
#    are byte-identical — the "hand-reconciled, no build_snapshot sync" layer
#    map row for .claude/hooks/**.
# ---------------------------------------------------------------------------

def test_live_and_package_hook_bodies_are_byte_identical() -> None:
    if not PKG_HOOK.exists():
        pytest.skip("package tree not present in this checkout")
    assert LIVE_HOOK.read_text() == PKG_HOOK.read_text()


# ---------------------------------------------------------------------------
# 5. Enum-load failure fails OPEN, not closed (never lock out all dispatch).
# ---------------------------------------------------------------------------

def test_missing_manifest_fails_open(tmp_path: Path) -> None:
    """A copy of the hook with no deliverables.json beside it must allow the
    dispatch (exit 0) with a LOUD stderr WARN, never lock out dispatch."""
    isolated_hook = tmp_path / "routing-target-validator.py"
    isolated_hook.write_text(LIVE_HOOK.read_text())
    gate_deny_src = REPO_ROOT / ".claude" / "hooks" / "_gate_deny.py"
    (tmp_path / "_gate_deny.py").write_text(gate_deny_src.read_text())
    payload = json.dumps({"tool_input": {"subagent_type": "hermes"}})
    proc = subprocess.run(
        [sys.executable, str(isolated_hook)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "deliverables.json failed to load" in proc.stderr

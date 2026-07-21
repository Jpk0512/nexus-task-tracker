"""F2-04 tranche-B daemon-resident handlers — `nexus-foundation/plans/
artifacts/event-bus-design.md` §1a/§2a/§3, `deny_handlers.py`.

Covers the 14 tranche-B consumers' real (never-stub) verdict compute, the
`compute_verdict` dispatch/shadow-fail-open contract, and one end-to-end
`event_bus.handle_event_verify` glue check proving the `consumer` param
actually reaches the ported handler (mirrors test_advisory_handlers.py's
F2-03 shape for the deny-capable side).

Every handler is invoked directly against a throwaway `tmp_path` project
tree — no live daemon, no real broker/DB required, per tdd-core's
real-data-SHAPE rule (the JSON shapes below mirror the real on-disk state
`deny_handlers.py`'s own docstring names: broker_state.json,
worktree_registry.json, deliverables.json, project.db's validation_log).
SHADOW ONLY (C-06): these tests assert the ported logic's OWN verdict; they
never assert anything about whether a hook body was actually overridden —
this module's callers never treat a "deny" here as blocking anything.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone  # noqa: UP017
from pathlib import Path

import pytest

from broker.daemon import deny_handlers, event_bus


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def project(tmp_path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".memory" / "files").mkdir(parents=True)
    return proj


def _write_broker_state(project_path: Path, state: dict) -> None:
    path = project_path / ".memory" / "files" / "broker_state.json"
    path.write_text(json.dumps(state))


def _write_worktree_registry(project_path: Path, registry: dict) -> None:
    path = project_path / ".memory" / "files" / "worktree_registry.json"
    path.write_text(json.dumps(registry))


def _write_deliverables(project_path: Path, manifest: dict) -> None:
    (project_path / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    path = project_path / ".claude" / "hooks" / "deliverables.json"
    path.write_text(json.dumps(manifest))


def _valid_token(
    persona: str = "hermes",
    ttl_s: int = 3600,
    allowed_personas: list[str] | None = None,
) -> dict:
    token = {
        "persona": persona,
        "expires_at": (datetime.now(tz=timezone.utc) + timedelta(seconds=ttl_s)).isoformat(),  # noqa: UP017
    }
    if allowed_personas is not None:
        token["allowed_personas"] = allowed_personas
    return token


# ── broker-gate ──────────────────────────────────────────────────────────


def test_broker_gate_no_persona_is_bookkeeping_allow(project):
    # top-level agent_type is a realistic field on every real PreToolUse
    # payload (the CALLING agent's own identity) — it must NOT be misread
    # as the dispatch target once tool_input is present (even empty).
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {}, "agent_type": "forge-ui"}, {}
    )
    assert result["decision"] == "allow"


def test_broker_gate_missing_state_denies(project):
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "BROKER/DISPATCH-BLOCKED"


def test_broker_gate_no_token_denies(project):
    _write_broker_state(project, {})
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "deny"


def test_broker_gate_persona_mismatch_denies(project):
    # DEC-096: a pre-DEC-096 token (no allowed_personas) degrades to the
    # one-element set {persona}, so a different target is not a member → deny.
    _write_broker_state(project, {"capability_token": _valid_token(persona="atlas")})
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "deny"
    assert "not a member" in result["reason"]


# ── DEC-096: closed allowed_personas set (membership, not equality) ────────


def test_broker_gate_membership_allows_roster_member(project):
    _write_broker_state(project, {"capability_token": _valid_token(
        persona="forge-wire",
        allowed_personas=["forge-wire", "pipeline-async", "quill-py"],
    )})
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "pipeline-async"}}, {}
    )
    assert result["decision"] == "allow"


def test_broker_gate_membership_denies_persona_outside_set(project):
    _write_broker_state(project, {"capability_token": _valid_token(
        persona="forge-wire",
        allowed_personas=["forge-wire", "pipeline-async", "quill-py"],
    )})
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "atlas"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "BROKER/DISPATCH-BLOCKED"
    assert "not a member" in result["reason"]


def test_broker_gate_degenerate_single_element_set_is_exact_match(project):
    # A one-element allowed_personas set behaves exactly like the old
    # equality check — no special-case branch for single-persona dispatch.
    _write_broker_state(project, {"capability_token": _valid_token(
        persona="hermes", allowed_personas=["hermes"],
    )})
    allow = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert allow["decision"] == "allow"
    deny = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "forge-ui"}}, {}
    )
    assert deny["decision"] == "deny"


def test_broker_gate_reads_real_on_disk_broker_state(project):
    # Family1: prove the handler honors the REAL broker_state.json on disk
    # (at <project>/.memory/files/broker_state.json) rather than a stub — a
    # full realistic state with a wave-roster token is read and honored...
    _write_broker_state(project, {
        "approved": True,
        "persona": "forge-wire",
        "team_name": "wave-1",
        "approved_brief": {"task_id": "TASK-096", "task_tier": "standard"},
        "capability_token": _valid_token(
            persona="forge-wire",
            allowed_personas=["forge-wire", "pipeline-async", "quill-py"],
        ),
    })
    honored = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "quill-py"}}, {}
    )
    assert honored["decision"] == "allow"
    # ...and when that real file is absent, the ONLY path to a missing/malformed
    # deny is a genuinely-missing file — never a stub the handler substitutes.
    (project / ".memory" / "files" / "broker_state.json").unlink()
    missing = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "quill-py"}}, {}
    )
    assert missing["decision"] == "deny"
    assert "missing/malformed" in missing["reason"]


def test_broker_gate_expired_token_denies(project):
    _write_broker_state(project, {"capability_token": _valid_token(persona="hermes", ttl_s=-10)})
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "deny"
    assert "expired" in result["reason"]


def test_broker_gate_valid_token_allows(project):
    _write_broker_state(project, {"capability_token": _valid_token(persona="hermes")})
    result = deny_handlers.handle_broker_gate(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "allow"


# ── dispatch-shape-guard ─────────────────────────────────────────────────


def test_dispatch_shape_guard_non_dispatch_tool_allows(project):
    result = deny_handlers.handle_dispatch_shape_guard(
        project, {"tool_name": "Read", "tool_input": {}}, {}
    )
    assert result["decision"] == "allow"


def test_dispatch_shape_guard_no_persona_or_team_denies(project):
    # Same realistic top-level agent_type as the broker-gate fixture above —
    # the nested (even empty) tool_input must win; the caller's own identity
    # must never leak through as a parseable dispatch target.
    result = deny_handlers.handle_dispatch_shape_guard(
        project, {"tool_name": "Task", "tool_input": {}, "agent_type": "forge-ui"}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "DISPATCH-SHAPE/UNRECOGNIZED"


def test_dispatch_shape_guard_unregistered_persona_denies(project):
    result = deny_handlers.handle_dispatch_shape_guard(
        project, {"tool_name": "Task", "tool_input": {"subagent_type": "not-a-real-persona"}}, {}
    )
    assert result["decision"] == "deny"


def test_dispatch_shape_guard_registered_persona_allows(project):
    result = deny_handlers.handle_dispatch_shape_guard(
        project, {"tool_name": "Task", "tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "allow"


# ── nested-vs-flat persona derivation (Lens REVISE 1, major finding) ─────
#
# Reproduces the exact divergence Lens flagged: payload {tool_name: Task,
# tool_input: {}, agent_type: forge-ui} — top-level agent_type here is the
# CALLING agent's own identity (present on every real PreToolUse event), not
# a dispatch target, because tool_input (even empty) is present and nested.
# broker-gate.py._dispatch_facts / dispatch-shape-guard.sh's inline logic
# both refuse to read it as a target; the shared unconditional-fallback
# `_dispatch_persona` used to misread it, flipping both verdicts.


def test_broker_gate_nested_empty_tool_input_ignores_caller_agent_type(project):
    """persona derives to "" (bookkeeping allow), NOT 'forge-ui' from the
    top-level agent_type — matches live broker-gate.py exactly."""
    result = deny_handlers.handle_broker_gate(
        project, {"tool_name": "Task", "tool_input": {}, "agent_type": "forge-ui"}, {}
    )
    assert result["decision"] == "allow"
    assert "no persona" in result["reason"]


def test_dispatch_shape_guard_nested_empty_tool_input_denies_backstop(project):
    """persona AND team_name both derive empty (top-level agent_type must
    NOT leak in as the target) -> the default-deny backstop fires, matching
    live dispatch-shape-guard.sh exactly."""
    result = deny_handlers.handle_dispatch_shape_guard(
        project, {"tool_name": "Task", "tool_input": {}, "agent_type": "forge-ui"}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "DISPATCH-SHAPE/UNRECOGNIZED"


def test_dispatch_persona_strict_flat_payload_falls_back_to_agent_type(project):
    """Flat/legacy/test payload (no nested tool_input/input dict at all)
    still falls back to top-level agent_type — the nested-vs-flat rule only
    withholds the fallback when a nested dict was actually found."""
    persona = deny_handlers._dispatch_persona_strict({"agent_type": "hermes"})
    assert persona == "hermes"


# ── skills-required-guard ────────────────────────────────────────────────


def test_skills_required_guard_non_code_persona_allows(project):
    result = deny_handlers.handle_skills_required_guard(
        project, {"tool_input": {"subagent_type": "lens"}}, {}
    )
    assert result["decision"] == "allow"


def test_skills_required_guard_missing_skills_denies(project):
    result = deny_handlers.handle_skills_required_guard(
        project, {"tool_input": {"subagent_type": "hermes", "description": "{}"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "SKILLS/MISSING"


def test_skills_required_guard_present_skills_allows(project):
    brief = json.dumps({"skills_required": ["agent-protocol"]})
    result = deny_handlers.handle_skills_required_guard(
        project,
        {"tool_input": {"subagent_type": "hermes", "description": f"```json\n{brief}\n```"}},
        {},
    )
    assert result["decision"] == "allow"


# ── Bug 4: code-writing roster derived DYNAMICALLY from deliverables.json ──


def test_skills_required_guard_pro_variant_derived_from_deliverables_denies(project):
    # Wave-1 repro: forge-ui-pro is absent from the OLD hardcoded 8-name set,
    # but the hook derives the roster from deliverables.json — a *-pro variant
    # whose _note says "Retired" (NOT the case-sensitive "Tombstone") and which
    # has no must_not_modify ['**/*'] IS a code-writer. forge-ui-pro dispatched
    # with no skills_required must DENY (hook denies, old daemon allowed).
    _write_deliverables(project, {
        "_comment": "fixture manifest",
        "scout": {"must_not_modify": ["**/*"]},
        "forge-ui-pro": {"_note": "Retired dispatch NAME — inert historical record."},
        "forge": {"_note": "Tombstone fallback — pre-split persona key."},
    })
    result = deny_handlers.handle_skills_required_guard(
        project,
        {"tool_input": {"subagent_type": "forge-ui-pro", "description": "{}"}},
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "SKILLS/MISSING"


def test_skills_required_guard_tombstone_and_readonly_not_gated(project):
    # Derivation parity: a Tombstone key ('forge') and a read-only persona
    # ('scout', must_not_modify ['**/*']) are EXCLUDED from the code-writing
    # roster — a no-skills dispatch to either is allowed, exactly as the hook.
    _write_deliverables(project, {
        "scout": {"must_not_modify": ["**/*"]},
        "forge": {"_note": "Tombstone fallback — pre-split persona key."},
        "forge-ui-pro": {"_note": "Retired dispatch NAME."},
    })
    for name in ("forge", "scout"):
        result = deny_handlers.handle_skills_required_guard(
            project,
            {"tool_input": {"subagent_type": name, "description": "{}"}},
            {},
        )
        assert result["decision"] == "allow", name


def test_skills_required_guard_missing_manifest_falls_closed_to_base_set(project):
    # No deliverables.json in the fixture → _load_code_writing_personas
    # degrades to _CODE_WRITING_FALLBACK (the hook's same posture). hermes is in
    # that base set, so a no-skills hermes dispatch still DENIES (gate never
    # silently disabled); a *-pro variant, absent from the fallback, is NOT
    # gated when the manifest is unreadable.
    denied = deny_handlers.handle_skills_required_guard(
        project, {"tool_input": {"subagent_type": "hermes", "description": "{}"}}, {}
    )
    assert denied["decision"] == "deny"
    allowed = deny_handlers.handle_skills_required_guard(
        project, {"tool_input": {"subagent_type": "forge-ui-pro", "description": "{}"}}, {}
    )
    assert allowed["decision"] == "allow"


# ── persona-alias-resolver ───────────────────────────────────────────────


def test_persona_alias_resolver_non_stale_name_allows(project):
    result = deny_handlers.handle_persona_alias_resolver(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "allow"


def test_persona_alias_resolver_stale_unresolvable_denies(project):
    result = deny_handlers.handle_persona_alias_resolver(
        project, {"tool_input": {"subagent_type": "forge", "description": "no scope hints here"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "PERSONA/STALE-FORGE"


def test_persona_alias_resolver_stale_resolvable_allows(project):
    result = deny_handlers.handle_persona_alias_resolver(
        project,
        {"tool_input": {"subagent_type": "forge", "description": "build the app/api server action"}},
        {},
    )
    assert result["decision"] == "allow"


# ── routing-target-validator ─────────────────────────────────────────────


def test_routing_target_validator_no_persona_allows(project):
    result = deny_handlers.handle_routing_target_validator(project, {"tool_input": {}}, {})
    assert result["decision"] == "allow"


def test_routing_target_validator_retired_base_name_allows(project):
    result = deny_handlers.handle_routing_target_validator(
        project, {"tool_input": {"subagent_type": "pipeline"}}, {}
    )
    assert result["decision"] == "allow"


def test_routing_target_validator_missing_manifest_fails_open(project):
    result = deny_handlers.handle_routing_target_validator(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "allow"


def test_routing_target_validator_live_persona_allows(project):
    _write_deliverables(project, {"hermes": {"_note": "live"}})
    result = deny_handlers.handle_routing_target_validator(
        project, {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "allow"


def test_routing_target_validator_retired_persona_denies(project):
    _write_deliverables(project, {"scout": {"_note": "tombstone — retired 2026"}})
    result = deny_handlers.handle_routing_target_validator(
        project, {"tool_input": {"subagent_type": "scout"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "ROUTING/RETIRED-PERSONA"


def test_routing_target_validator_unknown_persona_denies(project):
    _write_deliverables(project, {"hermes": {"_note": "live"}})
    result = deny_handlers.handle_routing_target_validator(
        project, {"tool_input": {"subagent_type": "totally-hallucinated-persona"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "ROUTING/UNKNOWN-PERSONA"


# ── secret-path-guard ────────────────────────────────────────────────────


def test_secret_path_guard_env_write_denies(project):
    result = deny_handlers.handle_secret_path_guard(
        project, {"tool_input": {"file_path": ".env"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "SECRET-PATH/WRITE-DENIED"


def test_secret_path_guard_normal_file_allows(project):
    result = deny_handlers.handle_secret_path_guard(
        project, {"tool_input": {"file_path": "README.md"}}, {}
    )
    assert result["decision"] == "allow"


# ── edit-boundary-impact-gate ────────────────────────────────────────────


def test_edit_boundary_no_active_scope_allows(project):
    result = deny_handlers.handle_edit_boundary_impact_gate(
        project, {"tool_input": {"file_path": "app/foo.ts"}}, {}
    )
    assert result["decision"] == "allow"


def test_edit_boundary_in_scope_allows(project):
    _write_broker_state(project, {"approved_brief": {"write_scope": ["app/"]}})
    result = deny_handlers.handle_edit_boundary_impact_gate(
        project, {"tool_input": {"file_path": str(project / "app" / "foo.ts")}}, {}
    )
    assert result["decision"] == "allow"


def test_edit_boundary_out_of_scope_denies(project):
    _write_broker_state(project, {"approved_brief": {"write_scope": ["app/"]}})
    result = deny_handlers.handle_edit_boundary_impact_gate(
        project, {"tool_input": {"file_path": str(project / "ingestion" / "foo.py")}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "EDIT-BOUNDARY/OUT-OF-SCOPE"


def test_edit_boundary_out_of_scope_with_valid_override_allows(project):
    _write_broker_state(project, {"approved_brief": {"write_scope": ["app/"]}})
    result = deny_handlers.handle_edit_boundary_impact_gate(
        project,
        {
            "tool_input": {
                "file_path": str(project / "ingestion" / "foo.py"),
                "override": {
                    "gate": "EDIT-BOUNDARY",
                    "code": "OUT-OF-SCOPE",
                    "reason": "user approved",
                    "authorized_by": "user",
                },
            }
        },
        {},
    )
    assert result["decision"] == "allow"


# ── oracle-immutability-guard ────────────────────────────────────────────


def test_oracle_immutability_no_active_boundary_allows(project):
    result = deny_handlers.handle_oracle_immutability_guard(
        project, {"tool_input": {"file_path": "app/foo.ts"}}, {}
    )
    assert result["decision"] == "allow"


def test_oracle_immutability_matching_glob_denies(project):
    _write_broker_state(project, {"approved_brief": {"do_not_touch": ["nexus-broker/src/**"]}})
    result = deny_handlers.handle_oracle_immutability_guard(
        project,
        {"tool_input": {"file_path": str(project / "nexus-broker" / "src" / "broker" / "x.py")}},
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "ORACLE-IMMUTABILITY/WRITE-DENIED"


def test_oracle_immutability_non_matching_allows(project):
    _write_broker_state(project, {"approved_brief": {"do_not_touch": ["nexus-broker/src/**"]}})
    result = deny_handlers.handle_oracle_immutability_guard(
        project, {"tool_input": {"file_path": str(project / "app" / "foo.ts")}}, {}
    )
    assert result["decision"] == "allow"


# ── plexus-write-boundary ────────────────────────────────────────────────


def test_plexus_write_boundary_persona_actor_allows(project):
    result = deny_handlers.handle_plexus_write_boundary(
        project,
        {"tool_input": {"file_path": "app/foo.ts"}},
        {"CLAUDE_AGENT_TYPE": "hermes"},
    )
    assert result["decision"] == "allow"


def test_plexus_write_boundary_orchestrator_code_path_denies(project):
    result = deny_handlers.handle_plexus_write_boundary(
        project, {"tool_input": {"file_path": "app/foo.ts"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "PLEXUS-WRITE-BOUNDARY/DELEGATE-REQUIRED"


def test_plexus_write_boundary_orchestrator_non_code_path_allows(project):
    result = deny_handlers.handle_plexus_write_boundary(
        project, {"tool_input": {"file_path": "docs/README.md"}}, {}
    )
    assert result["decision"] == "allow"


# ── worktree-guard ───────────────────────────────────────────────────────


def test_worktree_guard_no_command_allows(project):
    result = deny_handlers.handle_worktree_guard(project, {"tool_input": {}}, {})
    assert result["decision"] == "allow"


def test_worktree_guard_registered_add_allows(project):
    # Real registry schema: created_at + ttl_seconds (NOT the phantom
    # expires_at the old daemon read). A fresh record is live → allow.
    created = datetime.now(tz=timezone.utc).isoformat()  # noqa: UP017
    _write_worktree_registry(
        project,
        {"/tmp/wt-1": {"owner_id": "pipeline-async", "created_at": created, "ttl_seconds": 14400}},
    )
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git worktree add /tmp/wt-1"}}, {}
    )
    assert result["decision"] == "allow"


def test_worktree_guard_unregistered_add_denies(project):
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git worktree add /tmp/wt-unknown"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "WORKTREE-GUARD/UNREGISTERED"


def test_worktree_guard_branch_create_without_bypass_denies(project):
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git checkout -b feature/x"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "WORKTREE-GUARD/NO-FEATURE-BRANCHES"


def test_worktree_guard_branch_create_with_bypass_allows(project):
    result = deny_handlers.handle_worktree_guard(
        project,
        {"tool_input": {"command": "git checkout -b feature/x # BYPASS:USER-APPROVED-BRANCH"}},
        {},
    )
    assert result["decision"] == "allow"


def test_worktree_guard_other_command_allows(project):
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git status"}}, {}
    )
    assert result["decision"] == "allow"


# ── wave-1 parity regressions (HOOK IS GROUND TRUTH, S20260717 soak log) ──


def test_worktree_guard_expired_record_is_denied(project):
    # Bug 1 repro: a 10h-old record under a 4h ttl is EXPIRED. The hook's
    # test_expired_record_is_denied denies it; the old daemon read a phantom
    # `expires_at` (never set by the real created_at+ttl_seconds schema), so
    # the TTL was a no-op that always allowed the stale entry.
    created = (datetime.now(tz=timezone.utc) - timedelta(hours=10)).isoformat()  # noqa: UP017
    _write_worktree_registry(
        project,
        {"/tmp/wt-stale": {"owner_id": "pipeline-async", "created_at": created, "ttl_seconds": 14400}},
    )
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git worktree add /tmp/wt-stale"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "WORKTREE-GUARD/UNREGISTERED"
    assert "expired" in result["reason"]


def test_worktree_guard_expires_at_only_record_is_denied(project):
    # The real registry never sets `expires_at`. A record carrying ONLY that
    # phantom field (no created_at) must DENY — proving the daemon no longer
    # honors the field that used to fail-open.
    _write_worktree_registry(project, {"/tmp/wt-1": {"expires_at": None}})
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git worktree add /tmp/wt-1"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "WORKTREE-GUARD/UNREGISTERED"


def test_worktree_guard_git_switch_dash_c_denies(project):
    # Bug 2 repro: `git switch -c <new>` creates a branch. The hook denies it;
    # the old daemon regex (checkout -b | branch) missed `switch` and allowed.
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git switch -c feature/x"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "WORKTREE-GUARD/NO-FEATURE-BRANCHES"


def test_worktree_guard_git_switch_dash_capital_c_denies(project):
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": "git switch -C feature/x"}}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "WORKTREE-GUARD/NO-FEATURE-BRANCHES"


def test_worktree_guard_git_switch_dash_c_with_bypass_allows(project):
    result = deny_handlers.handle_worktree_guard(
        project,
        {"tool_input": {"command": "git switch -c feature/x # BYPASS:USER-APPROVED-BRANCH"}},
        {},
    )
    assert result["decision"] == "allow"


def test_worktree_guard_quoted_branch_in_commit_message_allows(project):
    # Bug 3 repro (7x in the soak log): a commit message that merely CONTAINS
    # the substring "git branch" must NOT trip NO-FEATURE-BRANCHES. shlex-style
    # segment parsing classifies this as a plain commit → allow; the old
    # non-quote-aware regex false-DENIED it.
    cmd = 'git commit -m "add feature: create git branch helper"'
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": cmd}}, {}
    )
    assert result["decision"] == "allow"


def test_worktree_guard_quoted_worktree_add_in_commit_message_allows(project):
    # Same class of false-deny for the worktree-add substring inside a message.
    cmd = 'git commit -m "docs: explain git worktree add usage"'
    result = deny_handlers.handle_worktree_guard(
        project, {"tool_input": {"command": cmd}}, {}
    )
    assert result["decision"] == "allow"


# ── no-direct-push-to-main ───────────────────────────────────────────────


def test_no_direct_push_no_command_allows(project):
    result = deny_handlers.handle_no_direct_push_to_main(project, {"tool_input": {}}, {})
    assert result["decision"] == "allow"


def test_no_direct_push_non_push_command_allows(project):
    result = deny_handlers.handle_no_direct_push_to_main(
        project, {"tool_input": {"command": "git status"}}, {}
    )
    assert result["decision"] == "allow"


def test_no_direct_push_with_bypass_allows(project):
    result = deny_handlers.handle_no_direct_push_to_main(
        project,
        {"tool_input": {"command": "git push origin main # BYPASS:USER-APPROVED-PUSH-TO-MAIN"}},
        {},
    )
    assert result["decision"] == "allow"


def test_no_direct_push_subagent_denies(project):
    result = deny_handlers.handle_no_direct_push_to_main(
        project,
        {"tool_input": {"command": "git push origin main"}},
        {"CLAUDE_AGENT_TYPE": "hermes"},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "PUSH-GUARD/SUBAGENT-PUSH"


def test_no_direct_push_orchestrator_allows(project):
    result = deny_handlers.handle_no_direct_push_to_main(
        project, {"tool_input": {"command": "git push origin main"}}, {}
    )
    assert result["decision"] == "allow"


# ── no-deferral-gate ─────────────────────────────────────────────────────


def test_no_deferral_gate_empty_text_allows(project):
    result = deny_handlers.handle_no_deferral_gate(project, {}, {})
    assert result["decision"] == "allow"


def test_no_deferral_gate_needs_decision_marker_allows(project):
    result = deny_handlers.handle_no_deferral_gate(
        project,
        {"last_assistant_message": "I will fix this later. ## NEXUS:NEEDS-DECISION naming it"},
        {},
    )
    assert result["decision"] == "allow"


def test_no_deferral_gate_defer_pattern_denies(project):
    result = deny_handlers.handle_no_deferral_gate(
        project, {"last_assistant_message": "will fix this later"}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "DEFER/FIX-DEFERRED"


def test_no_deferral_gate_clean_text_allows(project):
    result = deny_handlers.handle_no_deferral_gate(
        project, {"last_assistant_message": "all good, shipping now"}, {}
    )
    assert result["decision"] == "allow"


# ── lens-gate ────────────────────────────────────────────────────────────


def _make_validation_db(project_path: Path, *, task_hash: str, verdict: str, age_minutes: float) -> None:
    db_path = project_path / ".memory" / "project.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE validation_log (task_hash TEXT, verdict TEXT, validated_at TEXT)"
    )
    validated_at = (datetime.now(tz=timezone.utc) - timedelta(minutes=age_minutes)).isoformat()  # noqa: UP017
    conn.execute(
        "INSERT INTO validation_log (task_hash, verdict, validated_at) VALUES (?, ?, ?)",
        (task_hash, verdict, validated_at),
    )
    conn.commit()
    conn.close()


def test_lens_gate_non_gated_persona_allows(project):
    result = deny_handlers.handle_lens_gate(
        project, {"persona": "lens", "marker": "## NEXUS:DONE"}, {}
    )
    assert result["decision"] == "allow"


def test_lens_gate_not_a_done_return_allows(project):
    result = deny_handlers.handle_lens_gate(
        project, {"persona": "hermes", "marker": "## NEXUS:REVISE"}, {}
    )
    assert result["decision"] == "allow"


def test_lens_gate_done_no_files_changed_allows(project):
    result = deny_handlers.handle_lens_gate(
        project,
        {"persona": "hermes", "marker": "## NEXUS:DONE", "return_envelope": {"files_changed": []}},
        {},
    )
    assert result["decision"] == "allow"


def test_lens_gate_done_no_db_fails_soft_allow(project):
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "persona": "hermes",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": [".claude/hooks/foo.py"]},
        },
        {},
    )
    assert result["decision"] == "allow"


def test_lens_gate_done_with_recent_pass_row_allows(project):
    _make_validation_db(project, task_hash="abc123", verdict="pass", age_minutes=5)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "persona": "hermes",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": [".claude/hooks/foo.py"]},
        },
        {},
    )
    assert result["decision"] == "allow"


def test_lens_gate_done_without_pass_row_denies(project):
    _make_validation_db(project, task_hash="other-task", verdict="pass", age_minutes=5)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "persona": "hermes",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": [".claude/hooks/foo.py"]},
        },
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "LENS-GATE/NO-VALIDATION"


def test_lens_gate_done_stale_pass_row_denies(project):
    _make_validation_db(project, task_hash="abc123", verdict="pass", age_minutes=120)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "persona": "hermes",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": [".claude/hooks/foo.py"]},
        },
        {},
    )
    assert result["decision"] == "deny"


# ── Family2: persona-extraction aligned to the hook (ground truth) ─────────


def test_lens_gate_persona_from_subagent_type_is_gated(project):
    # No top-level "persona" key — the persona rides subagent_type, exactly the
    # shape that previously shadowed as "not a gated persona" (Family2). The
    # aligned extraction gates it and reaches the real no-validation deny.
    _make_validation_db(project, task_hash="other-task", verdict="pass", age_minutes=5)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "subagent_type": "pipeline-async",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": ["ingestion/src/workers/x.py"]},
        },
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "LENS-GATE/NO-VALIDATION"


def test_lens_gate_persona_from_agent_type_is_gated(project):
    # An Agent-tool dispatch carries the persona under top-level agent_type
    # (NATIVE-4) — the hook reads it, so the daemon must too.
    _make_validation_db(project, task_hash="other-task", verdict="pass", age_minutes=5)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "agent_type": "forge-ui",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": ["app/foo.tsx"]},
        },
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "LENS-GATE/NO-VALIDATION"


def test_lens_gate_quill_py_is_now_gated(project):
    # The daemon gated set previously omitted quill-py/quill-ts — align to the
    # hook's GATED_AGENTS so a quill DONE is gated, not silently exempted.
    _make_validation_db(project, task_hash="other-task", verdict="pass", age_minutes=5)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "subagent_type": "quill-py",
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": ["nexus-broker/tests/test_x.py"]},
        },
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "LENS-GATE/NO-VALIDATION"


def test_lens_gate_tool_input_nested_persona_is_gated(project):
    # Deepest fallback in the hook's chain: tool_input.subagent_type.
    _make_validation_db(project, task_hash="other-task", verdict="pass", age_minutes=5)
    result = deny_handlers.handle_lens_gate(
        project,
        {
            "tool_input": {"subagent_type": "atlas"},
            "marker": "## NEXUS:DONE",
            "task_hash": "abc123",
            "return_envelope": {"files_changed": ["models/schema.sql"]},
        },
        {},
    )
    assert result["decision"] == "deny"
    assert result["code"] == "LENS-GATE/NO-VALIDATION"


# ── plan-validation-gate ─────────────────────────────────────────────────


def test_plan_validation_gate_no_plan_path_allows(project):
    result = deny_handlers.handle_plan_validation_gate(project, {}, {})
    assert result["decision"] == "allow"


def test_plan_validation_gate_missing_file_denies(project):
    result = deny_handlers.handle_plan_validation_gate(
        project, {"plan_path": "docs/plans/does-not-exist.md"}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "PLAN-VALIDATION/FAIL"


def test_plan_validation_gate_scorer_invoke_failure_denies(project, tmp_path):
    plan_file = project / "docs" / "plans" / "x.md"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text("# plan\n")
    # project/nexus-broker does not exist -> subprocess.run's cwd is invalid -> OSError -> deny.
    result = deny_handlers.handle_plan_validation_gate(
        project, {"plan_path": "docs/plans/x.md"}, {}
    )
    assert result["decision"] == "deny"
    assert result["code"] == "PLAN-VALIDATION/FAIL"


# ── compute_verdict dispatch ─────────────────────────────────────────────


def test_compute_verdict_unknown_consumer_allows(project):
    result = deny_handlers.compute_verdict(project, "totally-unknown-consumer", {}, {})
    assert result["decision"] == "allow"
    assert "no daemon-resident handler ported" in result["reason"]


def test_compute_verdict_known_consumer_routes_to_handler(project):
    _write_broker_state(project, {"capability_token": _valid_token(persona="hermes")})
    result = deny_handlers.compute_verdict(
        project, "broker-gate", {"tool_input": {"subagent_type": "hermes"}}, {}
    )
    assert result["decision"] == "allow"


def test_compute_verdict_handler_exception_shadow_fails_open(project, monkeypatch):
    def _boom(project_path, payload, env):
        raise RuntimeError("synthetic handler bug")

    monkeypatch.setitem(deny_handlers.DENY_HANDLERS, "broker-gate", _boom)
    result = deny_handlers.compute_verdict(project, "broker-gate", {}, {})
    assert result["decision"] == "allow"
    assert "raised RuntimeError" in result["reason"]


def test_compute_verdict_all_14_tranche_b_consumers_registered():
    expected = {
        "broker-gate", "dispatch-shape-guard", "skills-required-guard",
        "persona-alias-resolver", "routing-target-validator", "secret-path-guard",
        "edit-boundary-impact-gate", "oracle-immutability-guard", "plexus-write-boundary",
        "worktree-guard", "no-direct-push-to-main", "no-deferral-gate", "lens-gate",
        "plan-validation-gate",
    }
    assert set(deny_handlers.DENY_HANDLERS.keys()) == expected


# ── event_bus.handle_event_verify glue (F2-04 replaces the F2-02 stub) ───


def test_handle_event_verify_no_consumer_is_neutral_allow(project):
    state = event_bus.EventBusState(project)
    result = event_bus.handle_event_verify(state, {"name": "write.pre.verify"})
    assert result["decision"] == "allow"
    assert result["consumer"] is None


def test_handle_event_verify_with_consumer_routes_to_deny_handlers(project):
    _write_broker_state(project, {"capability_token": _valid_token(persona="hermes")})
    state = event_bus.EventBusState(project)
    result = event_bus.handle_event_verify(
        state,
        {
            "name": "dispatch.pre.verify",
            "consumer": "broker-gate",
            "payload": {"tool_input": {"subagent_type": "hermes"}},
        },
    )
    assert result["decision"] == "allow"
    assert result["consumer"] == "broker-gate"


def test_handle_event_verify_wrong_tranche_raises(project):
    state = event_bus.EventBusState(project)
    with pytest.raises(ValueError):
        event_bus.handle_event_verify(state, {"name": "session.start"})

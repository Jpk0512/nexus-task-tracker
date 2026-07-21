"""Daemon-resident tranche-B (deny-capable) handlers — F2-04 shadow migration
(`nexus-foundation/plans/artifacts/event-bus-design.md` §1a/§2a/§3,
wave-2.md §(d)).

Replaces `event_bus.handle_event_verify`'s F2-02 stub-allow ("stub verdict —
tranche-B consumer gate logic lands in F2-04 shadow migration") with REAL
per-consumer verdict compute — see notepad F2-04 #330: "stub-allow in
handle_event_verify MUST be replaced with real gate logic this leg."

SHADOW-ONLY, NEVER AUTHORITATIVE (C-06): every verdict this module computes
is, for the whole of F2-04, a SHADOW verdict only — the retained hook body
at `.claude/hooks/<file>` stays the sole authoritative decision-maker until
cutover (>=2 shadow sessions, zero unexplained divergence, C-06). This
module's callers (currently: direct unit-test/RPC callers only — hook-body
shadow-wiring that CALLS `event.verify` per live dispatch is out of THIS
leg's write-boundary, see the F2-04 notepad) must never treat a `"deny"`
return here as blocking anything by itself.

Each function here is a handler with the contract:

    handler(project_path: Path, payload: dict, env: dict) -> dict

returning `{"decision": "allow" | "deny", "reason": str, "code": str}` —
`code` follows the existing `<GATE>/<SUBCODE>` convention every hook-side
`_gate_deny.deny()` call already uses (e.g. `"SECRET-PATH/WRITE-DENIED"`),
so a future shadow-log comparator can line a daemon verdict's `code` up
against the hook body's own `_gate_deny` call site directly.

PORTING DISCIPLINE (distinct from `advisory_handlers.py`'s "faithful,
string-for-string port" bar): tranche-B hook bodies are, in aggregate, an
order of magnitude larger and more state-dependent than tranche-A's
(capability-token HMAC verification, git-worktree registry lookups,
multi-stage broker state machines) — see the F2-04 notepad for the explicit,
named scope reduction taken here: every handler below computes a REAL
verdict from REAL on-disk state (never a stub, never a hard-coded "allow"),
but a handful of the most state-heavy predicates (broker-gate's HMAC/
denylist signature check chief among them) are intentionally narrowed to
their PRIMARY decision boundary rather than byte-exact parity with every
edge branch — full parity is a hook_parity.sh --tranche B (nexus-foundation/
tools/, hermes-owned) exercise this leg's write-boundary does not reach.
Every narrowing is called out in the handler's own docstring.

3.9 IMPORT-SAFETY — live runtime is >=3.11, this module is imported only by
`event_bus.py` (itself >=3.11-only, no un-shimmed package twin) — ordinary
3.11+ idioms are fine here, unlike the `.claude/hooks/*.py` bodies this
module ports from.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# ── shared helpers ──────────────────────────────────────────────────────


def _allow(reason: str) -> dict[str, Any]:
    return {"decision": "allow", "reason": reason, "code": ""}


def _deny(code: str, reason: str) -> dict[str, Any]:
    return {"decision": "deny", "reason": reason, "code": code}


def _broker_state_path(project_path: Path) -> Path:
    return project_path / ".memory" / "files" / "broker_state.json"


def _db_path(project_path: Path) -> Path:
    return project_path / ".memory" / "project.db"


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _tool_input(payload: dict) -> dict:
    for key in ("tool_input", "input"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    return payload


def _write_target_paths(tool_input: dict) -> list[str]:
    paths: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        val = tool_input.get(key)
        if val:
            paths.append(str(val))
    for edit in tool_input.get("edits", []) or []:
        if isinstance(edit, dict) and "file_path" in edit:
            paths.append(str(edit["file_path"]))
    return paths


def _relativize(raw_path: str, project_path: Path) -> str:
    p = Path(raw_path)
    if not p.is_absolute():
        return raw_path
    try:
        return str(p.resolve().relative_to(project_path.resolve()))
    except ValueError:
        return raw_path


def _matches_glob(path: str, glob: str) -> bool:
    """Ported verbatim (semantics, not bytes) from do-not-touch-guard.sh's
    `_matches` — trailing-slash directory-prefix, fnmatch, bare-name subtree."""
    normglob = glob.strip()
    if normglob.endswith("/"):
        prefix = normglob.rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if fnmatch.fnmatch(path, normglob):
        return True
    if "*" not in normglob and "?" not in normglob and "[" not in normglob:
        return path == normglob or path.startswith(normglob + "/")
    return False


def _classify_actor(env: dict, payload: dict) -> str:
    """Mirrors plexus-write-boundary.sh's classify_actor: "orchestrator" /
    "persona" / "unknown" from CLAUDE_AGENT_TYPE (env) or payload.agent_type."""
    for raw in (env.get("CLAUDE_AGENT_TYPE"), payload.get("agent_type")):
        if not raw:
            continue
        lower = str(raw).lower()
        if any(tok in lower for tok in ("orchestrator", "plexus", "nexus")):
            return "orchestrator"
        return "persona"
    return "unknown"


# ── dispatch.pre.verify ─────────────────────────────────────────────────


def _extract_brief(tool_input: dict) -> dict:
    block_re = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        for blockmatch in block_re.findall(raw):
            try:
                return json.loads(blockmatch)
            except json.JSONDecodeError:
                continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _dispatch_persona(payload: dict, tool_input: dict) -> str:
    """Unconditional-fallback persona read — correct ONLY for the 3 consumers
    whose own source hooks (skills-required-guard.sh, persona-alias-resolver.sh,
    routing-target-validator.py) use this exact unconditional fallback chain,
    including a top-level `payload.agent_type` fallback regardless of whether
    tool_input/input was nested. Do NOT reuse this for broker-gate or
    dispatch-shape-guard — see `_dispatch_persona_strict` below, which those
    two source hooks require instead."""
    persona = (
        tool_input.get("subagent_type", "")
        or tool_input.get("agent_type", "")
        or payload.get("subagent_type", "")
        or payload.get("agent_type", "")
    )
    return str(persona).lower().strip()


def _dispatch_persona_strict(payload: dict) -> str:
    """Nested-vs-flat-aware persona derivation — mirrors broker-gate.py's
    `_dispatch_facts` and dispatch-shape-guard.sh's inline logic EXACTLY:
    top-level `agent_type` is ALWAYS the CALLING agent's own identity
    (present on every real PreToolUse event) and must NEVER be read as the
    dispatch target when a nested tool_input/input dict was found — only
    that nested dict's subagent_type/agent_type, falling back to top-level
    subagent_type (never top-level agent_type). Only a flat/legacy/test
    payload (no nested dict at all) falls back to top-level agent_type too.

    Distinct from `_dispatch_persona` above (whose unconditional top-level
    agent_type fallback is what those 2 source hooks explicitly avoid, to
    stop the calling agent's own identity from being misread as its
    dispatch target — the exact incident both hooks' docstrings name)."""
    nested: dict | None = None
    for key in ("tool_input", "input"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            nested = candidate
            break
    tool_input = nested if nested is not None else payload

    if nested is not None:
        persona = (
            tool_input.get("subagent_type", "")
            or tool_input.get("agent_type", "")
            or payload.get("subagent_type", "")
        )
    else:
        persona = (
            tool_input.get("subagent_type", "")
            or tool_input.get("agent_type", "")
            or payload.get("subagent_type", "")
            or payload.get("agent_type", "")
        )
    return str(persona).lower().strip()


def _token_allowed_personas(token: dict) -> set:
    """DEC-096: the CLOSED set of personas a capability token authorizes.

    Reads the signed `allowed_personas` claim (normalized, lower/stripped). An
    absent/empty/malformed claim DEGRADES to the one-element set `{persona}` —
    a pre-DEC-096 token stays exact-match-equivalent with NO special-case
    branch. `allowed_personas` is a signed claim, so it cannot be widened (or
    stripped to force the degenerate fallback) without breaking the signature
    the live hook re-verifies."""
    raw = token.get("allowed_personas")
    if isinstance(raw, list):
        members = {
            str(p).lower().strip()
            for p in raw
            if isinstance(p, str) and str(p).strip()
        }
        if members:
            return members
    persona_claim = str(token.get("persona", "") or "").lower().strip()
    return {persona_claim} if persona_claim else set()


def handle_broker_gate(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Faithful-primary-boundary port of broker-gate.py (969 lines).

    STATE READ: reads the REAL on-disk broker_state.json at
    `<project_path>/.memory/files/broker_state.json` (the daemon is launched
    with `--project-path <repo-root>`, so this resolves to the live state the
    orchestrator's mint wrote) — there is NO payload-embedded or narrowed
    state substitute; a genuinely missing/malformed file is the only path to
    the "broker unavailable" deny.

    DEC-096 PERSONA AUTHORITY: the token carries a CLOSED `allowed_personas`
    set (`_token_allowed_personas`). The dispatch target must be a MEMBER of
    that set — a persona OUTSIDE it is DENIED (fail-closed). A single-persona
    token is the degenerate one-element set, so this membership check is
    exact-match-equivalent for it; a Workflow-wave token authorizes every
    persona in its declared roster. No `is_workflow_leg` branch exists
    (Option C permanently rejected).

    SCOPE NARROWING (see module docstring): the HMAC signature re-check +
    jti-denylist lookup (both keyed off `_HOOK_TOKEN_KEY_PATH` /
    `_HOOK_TOKEN_DENYLIST_PATH`, hook-body-local file resolution) is NOT
    reproduced here — a forged-but-well-shaped token would pass this shadow
    check where the live hook would catch it via signature mismatch. Never
    authoritative during shadow (module docstring); this gap is named in the
    F2-04 notepad for cutover-gate tracking, not silently absorbed.

    Uses `_dispatch_persona_strict` (NOT the shared `_dispatch_persona`) —
    broker-gate.py's own `_dispatch_facts` never reads a top-level
    `agent_type` as the dispatch target once a nested tool_input/input dict
    is present, since that field is always the CALLING agent's own identity.
    """
    persona = _dispatch_persona_strict(payload)
    if not persona:
        return _allow("no persona in payload — task/bookkeeping, not a real dispatch")

    state = _read_json(_broker_state_path(project_path))
    if state is None:
        return _deny("BROKER/DISPATCH-BLOCKED", "broker_state.json missing/malformed — broker unavailable")

    token = state.get("capability_token")
    if not isinstance(token, dict):
        return _deny("BROKER/DISPATCH-BLOCKED", "no capability_token on broker_state.json — no valid dispatch authority")

    allowed = _token_allowed_personas(token)
    if persona and allowed and persona not in allowed:
        return _deny(
            "BROKER/DISPATCH-BLOCKED",
            f"dispatch targets '{persona}' but it is not a member of the capability "
            f"token's allowed_personas set {sorted(allowed)} "
            f"(persona-mismatch — fail-closed, DEC-096 closed-set membership)",
        )

    try:
        expires_at = datetime.fromisoformat(str(token.get("expires_at")))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(tz=UTC) > expires_at:
            return _deny("BROKER/DISPATCH-BLOCKED", "capability token expired")
    except (TypeError, ValueError):
        return _deny("BROKER/DISPATCH-BLOCKED", "capability token missing/malformed expires_at")

    return _allow("valid unexpired capability token present for this persona")


_PERSONA_ROSTER = frozenset({
    "scout", "forge-wire", "forge-wire-pro", "forge-ui", "forge-ui-pro",
    "pipeline-data", "pipeline-data-pro", "pipeline-async", "pipeline-async-pro",
    "atlas", "hermes", "lens", "lens-fast", "quill-ts", "quill-py",
    "palette", "fable-planner", "planner",
})
_FABLE_HELPER_TYPES = frozenset({"explore", "general-purpose", "scout"})


def handle_dispatch_shape_guard(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Port of dispatch-shape-guard.sh: default-deny backstop when a
    Task|TeamCreate|Agent payload yields no parseable persona/team, or names
    a persona outside the fixed dispatchable roster (mirrored from
    nexus-broker/src/broker/registry.py DISPATCHABLE_PERSONAS, same
    copy-not-import precedent the hook itself uses).

    Uses `_dispatch_persona_strict` (NOT the shared `_dispatch_persona`) —
    the live hook's default-deny backstop exists precisely because a
    top-level `agent_type` (the CALLER's own identity) must never be
    misread as a dispatch target once a nested tool_input/input dict is
    present; the unconditional-fallback helper would silently defeat that
    backstop."""
    tool_name = str(payload.get("tool_name", "") or "")
    if tool_name not in ("Task", "TeamCreate", "Agent"):
        return _allow("not a dispatch tool_name")

    tool_input = _tool_input(payload)
    caller_persona = str(payload.get("agent_type", "") or "").lower().strip() if tool_input is not payload else ""
    persona = _dispatch_persona_strict(payload)
    team_name = str(tool_input.get("team_name", "") or payload.get("team_name", "") or "").strip()

    if not persona and not team_name:
        return _deny("DISPATCH-SHAPE/UNRECOGNIZED", "unrecognized/renamed dispatch shape — cannot parse a target persona")

    is_fable_helper = caller_persona == "fable-planner" and persona in _FABLE_HELPER_TYPES
    if persona and persona not in _PERSONA_ROSTER and not is_fable_helper:
        return _deny(
            "DISPATCH-SHAPE/UNRECOGNIZED",
            f"unregistered persona '{persona}' is not in the broker dispatchable-persona roster",
        )
    return _allow("dispatch shape recognized")


# Fail-CLOSED fallback for `_load_code_writing_personas` — mirrors
# skills-required-guard.sh's own `_FALLBACK` EXACTLY (its docstring: "Falls back
# to a minimal hardcoded set if the file is absent or malformed so the gate is
# never silently disabled"). An unreadable/malformed deliverables.json therefore
# still GATES these eight core code-writers (never a silent allow-all), matching
# the hook's fail-closed posture — the *derived* extended roster (palette,
# *-pro variants, codex-worker, fable-planner, planner) is simply unavailable
# until the manifest reads cleanly again.
_CODE_WRITING_FALLBACK = frozenset({
    "forge-ui", "forge-wire", "pipeline-data", "pipeline-async", "atlas", "hermes", "quill-ts", "quill-py",
})


def _load_code_writing_personas(project_path: Path) -> frozenset:
    """Derive the code-writing persona roster DYNAMICALLY from
    `<project_path>/.claude/hooks/deliverables.json`, mirroring
    skills-required-guard.sh's `_load_code_writing_personas` byte-for-byte in
    semantics (deliverables.json is the single source of truth):

    - skip `_`-prefixed keys and non-dict configs;
    - skip a Tombstone entry — the hook's check is CASE-SENSITIVE `"Tombstone"
      in note`, so a `_note` that says "Retired" (the *-pro variants) is NOT a
      tombstone and IS included;
    - skip a read-only persona (`must_not_modify` contains `"**/*"`);
    - everything else is a code-writer.

    Any I/O/parse failure (or an empty derived set) DEGRADES to
    `_CODE_WRITING_FALLBACK` — the same fail-closed choice the hook makes, so
    the gate is never silently disabled. On today's manifest this yields the 16
    non-readonly/non-tombstone personas (the 8 base code-writers + palette, the
    four *-pro variants, codex-worker, fable-planner, planner)."""
    try:
        manifest = _read_json(project_path / ".claude" / "hooks" / "deliverables.json")
        if not isinstance(manifest, dict):
            return _CODE_WRITING_FALLBACK
        result: set[str] = set()
        for persona, cfg in manifest.items():
            if persona.startswith("_") or not isinstance(cfg, dict):
                continue
            note = cfg.get("_note", "")
            if isinstance(note, str) and "Tombstone" in note:
                continue
            if "**/*" in cfg.get("must_not_modify", []):
                continue
            result.add(persona)
        return frozenset(result) if result else _CODE_WRITING_FALLBACK
    except Exception:  # noqa: BLE001 — any manifest read/parse failure fails closed to _FALLBACK
        return _CODE_WRITING_FALLBACK


def handle_skills_required_guard(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Port of skills-required-guard.sh Gate 1 (Rule 19): a code-writing
    persona dispatched with an absent/empty `skills_required` in its brief
    is denied. The Gate-2 SKILL_MAP.md mandatory-skill check is advisory
    (WARN) on the live hook, never a deny — not reproduced as a verdict
    branch here (mirrors that: this handler never denies for it).

    CODE-WRITING ROSTER (parity fix): the gated roster is derived DYNAMICALLY
    from deliverables.json via `_load_code_writing_personas` — the hook's single
    source of truth — NOT a hardcoded 8-name set. The old hardcoded set omitted
    palette, the four *-pro variants, codex-worker, fable-planner and planner, so
    a (e.g.) forge-ui-pro dispatch with no skills_required was denied by the hook
    but allowed by the daemon; deriving from the manifest closes that divergence.
    An unreadable manifest falls closed to `_CODE_WRITING_FALLBACK` (the hook's
    same posture)."""
    tool_input = _tool_input(payload)
    persona = _dispatch_persona(payload, tool_input)
    if persona not in _load_code_writing_personas(project_path):
        return _allow("not a code-writing persona dispatch")

    brief = _extract_brief(tool_input)
    skills_required = brief.get("skills_required")
    if skills_required is None:
        skills_required = tool_input.get("skills_required")
    has_skills = isinstance(skills_required, list) and any(
        isinstance(s, str) and s.strip() for s in skills_required
    )
    if not has_skills:
        return _deny(
            "SKILLS/MISSING",
            f"code-writing persona '{persona}' dispatched with no skills_required in brief",
        )
    return _allow("skills_required present")


def handle_persona_alias_resolver(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Port of persona-alias-resolver.sh: a retired BASE NAME (forge/
    pipeline/quill) that cannot be resolved to its split persona from brief
    scope hints is denied; a resolvable one, or any non-stale name, allows
    (the live hook's successful-resolve path is an ALLOW + rewrite-advice
    additionalContext, never a deny — matched here as allow)."""
    tool_input = _tool_input(payload)
    persona = _dispatch_persona(payload, tool_input)
    if persona not in {"forge", "pipeline", "quill"}:
        return _allow("not a stale base name")

    brief_text = " ".join(
        str(tool_input.get(k, "") or "") for k in ("description", "prompt")
    ).lower()
    resolvable = {
        "forge": r"app/components|app/\(routes\)|tremor|tailwind|rsc page|ui component|app/api|app/actions|server action|ai sdk|duckdb read",
        "pipeline": r"transforms|writers|embeddings|polars|duckdb write|workers|dramatiq|tableau|redis|async|clients",
        "quill": r"\.ts|\.tsx|vitest|react testing|typescript|\.py|pytest|polars fixture|python",
    }[persona]
    if re.search(resolvable, brief_text):
        return _allow(f"stale base name '{persona}' resolvable from brief scope")
    return _deny(
        f"PERSONA/STALE-{persona.upper()}",
        f"stale persona name '{persona}' cannot be resolved from brief — add explicit scope or dispatch the split persona directly",
    )


def handle_routing_target_validator(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Port of routing-target-validator.py PERSONA HALF (fail-closed); the
    SKILL HALF (unknown skills_required entries) is advisory-only on the
    live hook and not reproduced as a deny branch here."""
    tool_input = _tool_input(payload)
    persona = _dispatch_persona(payload, tool_input)
    if not persona:
        return _allow("no persona in payload — bookkeeping")
    if persona in {"forge", "pipeline", "quill"}:
        return _allow("retired base name — deferred to persona-alias-resolver")

    manifest = _read_json(project_path / ".claude" / "hooks" / "deliverables.json")
    if manifest is None:
        return _allow("deliverables.json unavailable — fail open")

    live: set[str] = set()
    retired: set[str] = set()
    for name, cfg in manifest.items():
        if name.startswith("_") or not isinstance(cfg, dict):
            continue
        note = cfg.get("_note", "")
        is_retired = isinstance(note, str) and ("tombstone" in note.lower() or "retired" in note.lower())
        (retired if is_retired else live).add(name)

    if persona in live:
        return _allow("persona present and live in deliverables.json")
    if persona in retired:
        return _deny("ROUTING/RETIRED-PERSONA", f"'{persona}' is a RETIRED dispatch target")
    return _deny("ROUTING/UNKNOWN-PERSONA", f"'{persona}' is not a known dispatch target (hallucinated or misspelled)")


# ── write.pre.verify ────────────────────────────────────────────────────

_SECRET_DENY_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_rsa.*", "id_ed25519",
    "id_ed25519.*", "id_ecdsa", "id_ecdsa.*", "*.p12", "*.pfx", "secrets.*",
    ".netrc", ".npmrc", "*.jks", "*.keystore",
)


def handle_secret_path_guard(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Faithful port of secret-path-guard.sh."""
    tool_input = _tool_input(payload)
    for raw_path in _write_target_paths(tool_input):
        name = Path(raw_path.rstrip("/")).name
        for pat in _SECRET_DENY_PATTERNS:
            if fnmatch.fnmatch(name, pat):
                return _deny(
                    "SECRET-PATH/WRITE-DENIED",
                    f"write to secret/credential file '{raw_path}' is blocked",
                )
    return _allow("no secret-path match")


def handle_edit_boundary_impact_gate(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Faithful port of edit-boundary-impact-gate.sh: write_scope ALLOW-list
    from broker_state.json approved_brief. The N12 typed-override honor path
    is preserved (returns allow, matching the live hook's exit-0 override)."""
    tool_input = _tool_input(payload)
    state = _read_json(_broker_state_path(project_path)) or {}
    brief = state.get("approved_brief") if isinstance(state.get("approved_brief"), dict) else {}
    scope_globs = [g for g in (brief.get("write_scope") or []) if isinstance(g, str) and g.strip()]
    if not scope_globs:
        return _allow("no active write_scope declared")

    paths = _write_target_paths(tool_input)
    if not paths:
        return _allow("no write-target path in payload")

    override = tool_input.get("override")
    override_ok = (
        isinstance(override, dict)
        and override.get("gate") == "EDIT-BOUNDARY"
        and override.get("code") == "OUT-OF-SCOPE"
        and isinstance(override.get("reason"), str)
        and override.get("reason").strip()
        and override.get("authorized_by") == "user"
    )

    out_of_scope = [
        raw for raw in paths
        if not any(_matches_glob(_relativize(raw, project_path), g) for g in scope_globs)
    ]
    if not out_of_scope:
        return _allow("all write targets within write_scope")
    if override_ok:
        return _allow(f"typed override honored for '{out_of_scope[0]}'")
    return _deny("EDIT-BOUNDARY/OUT-OF-SCOPE", f"write to '{out_of_scope[0]}' falls outside declared write_scope")


def handle_oracle_immutability_guard(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Faithful port of oracle-immutability-guard.sh: do_not_touch DENY-list
    from broker_state.json approved_brief."""
    tool_input = _tool_input(payload)
    state = _read_json(_broker_state_path(project_path)) or {}
    brief = state.get("approved_brief") if isinstance(state.get("approved_brief"), dict) else {}
    globs = [g for g in (brief.get("do_not_touch") or []) if isinstance(g, str) and g.strip()]
    if not globs:
        return _allow("no active do_not_touch boundary")

    for raw_path in _write_target_paths(tool_input):
        rel_path = _relativize(raw_path, project_path)
        for glob in globs:
            if _matches_glob(rel_path, glob):
                return _deny(
                    "ORACLE-IMMUTABILITY/WRITE-DENIED",
                    f"write to '{raw_path}' matches protected do_not_touch glob '{glob}'",
                )
    return _allow("no do_not_touch match")


_CODE_EXT_PATTERNS = ("*.py", "*.ts", "*.tsx", "*.sh")
_CODE_PATH_SUBSTRINGS = (".claude/hooks/", "nexus-broker/", "app/", "ingestion/")


def _is_code_path(raw_path: str) -> bool:
    name = Path(raw_path.rstrip("/")).name
    if any(fnmatch.fnmatch(name, pat) for pat in _CODE_EXT_PATTERNS):
        return True
    normalized = raw_path.replace("\\", "/")
    if any(sub in normalized for sub in _CODE_PATH_SUBSTRINGS):
        return True
    if fnmatch.fnmatch(name, "*.py") and "/.memory/" in ("/" + normalized):
        return True
    parts = [p for p in normalized.split("/") if p]
    return bool(len(parts) > 1 and "tests" in parts[:-1])


def handle_plexus_write_boundary(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Faithful port of plexus-write-boundary.sh: denies only when the actor
    classifies as "orchestrator" (neither CLAUDE_AGENT_TYPE nor
    payload.agent_type names a persona) AND the write target is a code path.
    Any persona sub-agent writing anything is allowed."""
    if _classify_actor(env, payload) == "persona":
        return _allow("actor is a persona sub-agent — delegated code writes are allowed")

    tool_input = _tool_input(payload)
    for raw_path in _write_target_paths(tool_input):
        if _is_code_path(raw_path):
            return _deny(
                "PLEXUS-WRITE-BOUNDARY/DELEGATE-REQUIRED",
                f"orchestrator attempted to write executable code directly at '{raw_path}'",
            )
    return _allow("orchestrator write target is not executable code")


# ── bash.pre.verify ─────────────────────────────────────────────────────

_BYPASS_PUSH_TOKEN = "# BYPASS:USER-APPROVED-PUSH-TO-MAIN"
_PUSH_MAIN_RE = re.compile(
    r"git(?:\s+-[-\w=./]+)*\s+push.*(\bmain\b|--force.*main|\borigin\s+main\b|HEAD\b|\s@(\s|$)|refs/heads/)",
)

# ── worktree-guard command classifier (ported semantics-for-semantics from
# worktree-guard.sh's inline python `strip_heredocs`/`split_segments`/
# `tokens_of`/`segment_has_bypass` block — HOOK IS GROUND TRUTH). The previous
# module-level `_WORKTREE_ADD_RE`/`_BRANCH_CREATE_RE` regexes had two
# divergences the wave-1 soak log reproduced: (a) `git switch -c/-C` created a
# branch but matched neither regex (daemon allowed, hook denied); (b) neither
# regex was quote/segment-aware, so a commit MESSAGE containing the substring
# "git branch ..." false-tripped NO-FEATURE-BRANCHES (daemon denied, hook
# allowed). This classifier parses shlex-style, quote- and segment-aware,
# exactly as the hook does. ──────────────────────────────────────────────
_GIT_WRAPPERS = frozenset({
    "rtk", "sudo", "env", "time", "nice", "ionice", "exec",
    "command", "builtin", "xargs",
})
_BRANCH_MGMT_FLAGS = frozenset({
    "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy",
    "-l", "--list", "-a", "--all", "-r", "--remotes", "--show-current",
    "--edit-description", "--set-upstream-to", "-u", "--unset-upstream",
    "--contains", "--merged", "--no-merged",
})
_BRANCH_CREATE_SWITCHES = frozenset({"-b", "-B", "-c", "-C", "--create", "--orphan"})
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_heredocs(s: str) -> str:
    """Drop heredoc bodies so their contents aren't parsed as commands."""
    out, lines, i = [], s.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*([\"']?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if m:
            delim, strip_tabs, j = m.group(2), "<<-" in line, i + 1
            while j < len(lines):
                test = lines[j].lstrip("\t") if strip_tabs else lines[j]
                if test == delim:
                    out.append(lines[j])
                    break
                j += 1
            i = j + 1
            continue
        i += 1
    return "\n".join(out)


def _split_segments(s: str) -> list[str]:
    """Split on top-level ; | & && || newline, respecting quotes and parens."""
    segs: list[str] = []
    cur: list[str] = []
    i, n = 0, len(s)
    in_single = in_double = False
    paren = 0
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            cur.append(ch)
            cur.append(s[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            if ch == "(":
                paren += 1
                cur.append(ch)
                i += 1
                continue
            if ch == ")":
                paren = max(0, paren - 1)
                cur.append(ch)
                i += 1
                continue
            if paren == 0:
                two = s[i:i + 2]
                if two in ("&&", "||", ";;"):
                    segs.append("".join(cur))
                    cur = []
                    i += 2
                    continue
                if ch in (";", "|", "&", "\n", "(", ")"):
                    segs.append("".join(cur))
                    cur = []
                    i += 1
                    continue
        cur.append(ch)
        i += 1
    if cur:
        segs.append("".join(cur))
    return segs


def _tokens_of(segment: str) -> list[str]:
    seg = segment.strip().lstrip("(").strip()
    if not seg:
        return []
    try:
        toks = shlex.split(seg, comments=True, posix=True)
    except ValueError:
        # Unbalanced quotes — fall back to a permissive whitespace split so a
        # `git worktree add` buried in a malformed line still trips the guard.
        toks = seg.split()
    idx = 0
    while idx < len(toks) and _ASSIGN_RE.match(toks[idx]):
        idx += 1
    toks = toks[idx:]
    while toks and toks[0].rsplit("/", 1)[-1] in _GIT_WRAPPERS:
        toks = toks[1:]
        j = 0
        while j < len(toks) and _ASSIGN_RE.match(toks[j]):
            j += 1
        toks = toks[j:]
    return toks


def _segment_has_bypass(raw_seg: str) -> bool:
    """True only when '# BYPASS:USER-APPROVED-BRANCH' appears as a trailing
    shell comment on THIS segment (outside quotes) — mirrors the hook."""
    token = "BYPASS:USER-APPROVED-BRANCH"
    n = len(raw_seg)
    in_single = in_double = False
    i = 0
    while i < n:
        ch = raw_seg[i]
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_double and ch == "#":
            return token in raw_seg[i + 1:].strip()
        i += 1
    return False


def _is_new_branch_name(arg: str) -> bool:
    return bool(arg) and not arg.startswith("-")


def _classify_git_command(cmd: str) -> tuple[str, str, bool]:
    """Return (kind, worktree_path, new_branch_has_bypass) where kind is one of
    WORKTREE_ADD / NEW_BRANCH / COMMIT / NONE — the SAME classification the hook
    emits. Priority WORKTREE_ADD > NEW_BRANCH > COMMIT > NONE."""
    worktree_add = False
    worktree_path = ""
    new_branch = False
    new_branch_has_bypass = False
    commit_detected = False

    for seg in _split_segments(_strip_heredocs(cmd)):
        toks = _tokens_of(seg)
        if len(toks) < 2 or toks[0].rsplit("/", 1)[-1] != "git":
            continue
        sub = toks[1]
        rest = toks[2:]

        if sub == "worktree" and rest and rest[0] == "add":
            worktree_add = True
            value_flags = {"-b", "-B", "--reason"}
            args = rest[1:]
            k = 0
            while k < len(args):
                a = args[k]
                if a in value_flags:
                    k += 2
                    continue
                if a.startswith("-"):
                    k += 1
                    continue
                worktree_path = a
                break
            continue

        if sub in ("checkout", "switch"):
            for k, a in enumerate(rest):
                if a in _BRANCH_CREATE_SWITCHES:
                    if k + 1 < len(rest) and _is_new_branch_name(rest[k + 1]):
                        new_branch = True
                        if _segment_has_bypass(seg):
                            new_branch_has_bypass = True
                    break
            continue

        if sub == "branch":
            positionals = [a for a in rest if _is_new_branch_name(a)]
            destructive_or_list = any(
                a in _BRANCH_MGMT_FLAGS or a.startswith("--set-upstream-to=")
                for a in rest
            )
            if positionals and not destructive_or_list:
                new_branch = True
                if _segment_has_bypass(seg):
                    new_branch_has_bypass = True
            continue

        if sub == "commit":
            commit_detected = True
            continue

    if worktree_add:
        return ("WORKTREE_ADD", worktree_path, new_branch_has_bypass)
    if new_branch:
        return ("NEW_BRANCH", "", new_branch_has_bypass)
    if commit_detected:
        return ("COMMIT", "", False)
    return ("NONE", "", False)


def _resolve_worktree_target(raw: str, project_path: Path) -> str:
    """Resolve the parsed worktree-add path to an absolute path the same way
    the hook does: an already-absolute path passes through; a relative path is
    normpath-joined against the repo root (the daemon's --project-path)."""
    if not raw:
        return ""
    if raw.startswith("/"):
        return raw
    return os.path.normpath(os.path.join(str(project_path), raw))


def _worktree_add_verdict(project_path: Path, target: str) -> dict[str, Any]:
    """Registry-ownership + TTL check mirroring worktree-guard.sh's REG_VERDICT
    python EXACTLY: the record must exist AND be non-expired by
    `created_at + ttl_seconds` (default ttl 14400s). The previous daemon read a
    phantom `entry['expires_at']` the real registry schema NEVER sets, so the
    TTL was a no-op that always allowed a stale (e.g. 10h-old, 4h-ttl) entry."""
    if not target:
        return _deny("WORKTREE-GUARD/UNREGISTERED", "no worktree path could be parsed from the command")
    registry = _read_json(project_path / ".memory" / "files" / "worktree_registry.json")
    if not isinstance(registry, dict):
        return _deny("WORKTREE-GUARD/UNREGISTERED", f"registry file missing/corrupt — cannot verify ownership of {target!r}")
    entry = registry.get(target)
    if not isinstance(entry, dict):
        return _deny("WORKTREE-GUARD/UNREGISTERED", f"no registry record for {target!r}")

    created_at = entry.get("created_at")
    ttl_seconds = entry.get("ttl_seconds", 14400)
    try:
        ttl_seconds = float(ttl_seconds)
    except (TypeError, ValueError):
        return _deny("WORKTREE-GUARD/UNREGISTERED", f"registry record for {target!r} has an invalid ttl_seconds")
    if not created_at:
        return _deny("WORKTREE-GUARD/UNREGISTERED", f"registry record for {target!r} is missing created_at")
    try:
        created = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        return _deny("WORKTREE-GUARD/UNREGISTERED", f"registry record for {target!r} has an unparseable created_at")
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    age_seconds = (datetime.now(tz=UTC) - created).total_seconds()
    if age_seconds >= ttl_seconds:
        return _deny(
            "WORKTREE-GUARD/UNREGISTERED",
            f"registry record for {target!r} expired ({int(age_seconds)}s old, ttl {int(ttl_seconds)}s)",
        )
    owner = entry.get("owner_id", "<unknown>")
    return _allow(f"registered live worktree entry for {target!r} (owner {owner})")


def handle_worktree_guard(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Primary-boundary port of worktree-guard.sh (HOOK IS GROUND TRUTH):
    `git worktree add` needs a LIVE (non-expired-by-created_at+ttl_seconds)
    worktree_registry.json record for the resolved target path; `git checkout
    -b|-B` / `git switch -c|-C|--create|--orphan` / `git branch <new>` is
    denied unless the trailing `# BYPASS:USER-APPROVED-BRANCH` comment is
    present on that segment. Detection is via `_classify_git_command`, a
    faithful shlex/segment-aware port of the hook's own classifier.

    Three wave-1 divergences fixed here: (1) TTL was a no-op reading a phantom
    `expires_at` field (now `created_at + ttl_seconds`, `_worktree_add_verdict`);
    (2) `git switch -c/-C` was missed by the old regex; (3) the old regexes were
    not quote/segment-aware, so a commit message containing "git branch"/"git
    worktree add" false-tripped the branch/worktree deny.

    The N71 Decision-A `git commit` flag-mix corner case (deploy-governance.
    enabled + mixed flag/hook-body diff) is NOT ported — narrow, config-gated
    corner case, named here rather than silently absorbed; a classified COMMIT
    therefore always allows on this shadow handler."""
    tool_input = _tool_input(payload)
    command = str(tool_input.get("command", "") or "")
    if not command:
        return _allow("no command in payload")

    kind, worktree_path, has_bypass = _classify_git_command(command)
    if kind == "WORKTREE_ADD":
        return _worktree_add_verdict(project_path, _resolve_worktree_target(worktree_path, project_path))
    if kind == "NEW_BRANCH":
        if has_bypass:
            return _allow("bypass token present for branch creation")
        return _deny("WORKTREE-GUARD/NO-FEATURE-BRANCHES", "DEC-002 main-only — feature branch creation is denied")

    return _allow("not a worktree/branch-creation command")


def handle_no_direct_push_to_main(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Faithful-primary-boundary port of no-direct-push-to-main.sh."""
    tool_input = _tool_input(payload)
    command = str(tool_input.get("command", "") or "")
    if not command:
        return _allow("no command in payload")
    if not _PUSH_MAIN_RE.search(command):
        return _allow("not a push-to-main command")
    if _BYPASS_PUSH_TOKEN in command:
        return _allow("bypass token present")
    agent_type = str(env.get("CLAUDE_AGENT_TYPE", "") or "").lower()
    if not agent_type or "orchestrator" in agent_type:
        return _allow("orchestrator/user session")
    return _deny("PUSH-GUARD/SUBAGENT-PUSH", "a sub-agent may not push to main directly")


# ── subagent.stop.verify ────────────────────────────────────────────────

_NEEDS_DECISION_RE = re.compile(r"NEXUS:NEEDS-DECISION", re.IGNORECASE)
_DEFER_PATTERN_RE = re.compile(
    r"\b(defer(red|ring)? (this|the) fix|will fix (this )?(later|in a follow[- ]?up)|"
    r"follow[- ]?up task (for|to fix)|out of scope,? not fix(ed|ing))\b",
    re.IGNORECASE,
)


def handle_no_deferral_gate(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Port of no-deferral-gate.sh's DECISION MODEL. The live hook ships
    SHADOW-FIRST (NEXUS_NO_DEFERRAL_ENFORCE default unset ⇒ would-deny only,
    never a real block) — this handler reproduces the underlying pattern
    match as a real verdict (matching the hook's decision model, not its
    enforce/shadow env toggle, since that toggle is an *enforcement* policy
    the daemon is not the authority for during F2-04 shadow)."""
    text = str(payload.get("last_assistant_message", "") or "")
    if not text:
        return _allow("no assistant text to inspect")
    if _NEEDS_DECISION_RE.search(text):
        return _allow("NEXUS:NEEDS-DECISION marker present — sanctioned defer")
    if _DEFER_PATTERN_RE.search(text):
        return _deny("DEFER/FIX-DEFERRED", "defer-of-a-discovered-fix pattern present without a NEEDS-DECISION marker")
    return _allow("no defer-of-fix pattern detected")


# Mirror of lens-gate.sh's GATED_AGENTS EXACTLY (hook is ground truth) — the
# eight code-writing personas whose NEXUS:DONE needs a Lens PASS row. The daemon
# set previously omitted quill-ts/quill-py, so a quill DONE shadowed as "not a
# gated persona" while the hook gated it (Family2 divergence).
_GATED_PERSONAS = frozenset({
    "forge-ui", "forge-wire", "pipeline-data", "pipeline-async",
    "atlas", "hermes", "quill-ts", "quill-py",
})
_DONE_MARKER_RE = re.compile(r"##\s*NEXUS:DONE", re.IGNORECASE)
_LENS_WINDOW = timedelta(hours=1)


def _lens_gate_persona(payload: dict) -> str:
    """Persona derivation for the SubagentStop lens-gate — ALIGNED to
    lens-gate.sh's `main()` agent_name fallback chain (HOOK IS GROUND TRUTH,
    Family2): `agent_persona` -> `subagent_type` -> `agent_type` ->
    `tool_input.subagent_type` -> `tool_input.agent_type`.

    Unlike a PreToolUse dispatch gate, top-level `agent_type` IS the sub-agent's
    own persona in a SubagentStop payload (NATIVE-4: an Agent-tool dispatch
    carries the persona under `agent_type`, not `subagent_type`) — the hook
    reads it deliberately, so this handler must too, or an Agent-shaped
    dispatch falls through to "" and is wrongly exempted. `payload.persona` is
    kept only as a trailing daemon-native fallback (the hook has no such key;
    real SubagentStop payloads never carry it, so it never re-introduces a
    divergence — it only serves direct daemon/test callers)."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    persona = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("agent_type")
        or tool_input.get("subagent_type")
        or tool_input.get("agent_type")
        or payload.get("persona")
        or ""
    )
    return str(persona).lower().strip()


def handle_lens_gate(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Primary-boundary port of lens-gate.sh: a gated persona's NEXUS:DONE
    return requires a Lens PASS row in validation_log within the last hour
    for the same task hash. The S2-14 git-ground-truth cross-check (when the
    self-report is absent/unparseable) is NOT reproduced here — self-report
    only; named as a scope reduction, not silently absorbed.

    PERSONA-EXTRACTION ALIGNMENT (Family2): the target persona is derived via
    `_lens_gate_persona`, which mirrors the hook's own agent_name fallback
    chain rather than reading a single `payload.persona` key — the previous
    single-key read shadowed a real gated DONE as "not a gated persona"
    whenever the payload carried the persona under subagent_type/agent_type.
    The gated set also mirrors the hook's GATED_AGENTS exactly (`_GATED_PERSONAS`).
    """
    persona = _lens_gate_persona(payload)
    if persona not in _GATED_PERSONAS:
        return _allow("not a gated persona")

    marker = str(payload.get("marker", "") or "")
    last_message = str(payload.get("last_assistant_message", "") or "")
    if not (_DONE_MARKER_RE.search(marker) or _DONE_MARKER_RE.search(last_message)):
        return _allow("not a NEXUS:DONE return")

    return_envelope = payload.get("return_envelope")
    files_changed: list[str] = []
    if isinstance(return_envelope, dict):
        raw_files = return_envelope.get("files_changed")
        if isinstance(raw_files, list):
            files_changed = [f for f in raw_files if isinstance(f, str)]
    if not files_changed:
        return _allow("no gated source files_changed self-reported")

    task_hash = str(payload.get("task_hash", "") or payload.get("session_id", "") or "")
    db = _db_path(project_path)
    if not db.is_file():
        return _allow("project.db unavailable — cannot verify, fail-soft")

    try:
        conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        try:
            cutoff = (datetime.now(tz=UTC) - _LENS_WINDOW).isoformat()
            row = conn.execute(
                """
                SELECT 1 FROM validation_log
                WHERE task_hash = ? AND verdict = 'pass' AND validated_at > ?
                LIMIT 1
                """,
                (task_hash, cutoff),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return _allow("validation_log query failed — fail-soft, cannot verify")

    if row:
        return _allow("recent Lens PASS row found for this task hash")
    return _deny("LENS-GATE/NO-VALIDATION", f"NEXUS:DONE from '{persona}' with no recent Lens PASS row for task_hash={task_hash!r}")


def handle_plan_validation_gate(project_path: Path, payload: dict, env: dict) -> dict[str, Any]:
    """Primary-boundary port of plan-validation-gate.py: FAIL-CLOSED shim
    over `python -m broker.plan_validation score <file> --json`. Shells out
    to the real scorer CLI (same contract the hook itself uses) rather than
    reimplementing scoring — any invocation failure (missing plan file,
    scorer error, non-JSON output) is a deny, matching the hook's
    fail-closed posture."""
    plan_path = payload.get("plan_path") or payload.get("plan_file")
    if not plan_path:
        return _allow("no plan file named in this SubagentStop — not a plan-authoring turn")

    abs_path = project_path / str(plan_path)
    if not abs_path.is_file():
        return _deny("PLAN-VALIDATION/FAIL", f"plan file not found: {plan_path}")

    try:
        proc = subprocess.run(
            ["python3", "-m", "broker.plan_validation", "score", str(abs_path), "--json"],
            cwd=str(project_path / "nexus-broker"),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _deny("PLAN-VALIDATION/FAIL", f"could not invoke the scorer: {exc}")

    if proc.returncode not in (0, 1):
        return _deny("PLAN-VALIDATION/FAIL", f"scorer exited {proc.returncode} unexpectedly")

    try:
        verdict = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return _deny("PLAN-VALIDATION/FAIL", "scorer produced non-JSON output")

    if not isinstance(verdict, dict) or "overall_pass" not in verdict:
        return _deny("PLAN-VALIDATION/FAIL", "scorer verdict missing 'overall_pass'")

    if verdict.get("overall_pass"):
        return _allow("plan-validation scorer PASS")
    return _deny("PLAN-VALIDATION/FAIL", "plan-validation scorer FAIL (overall_pass=false)")


# ── dispatch table ───────────────────────────────────────────────────────

DENY_HANDLERS: dict[str, Any] = {
    "broker-gate": handle_broker_gate,
    "dispatch-shape-guard": handle_dispatch_shape_guard,
    "skills-required-guard": handle_skills_required_guard,
    "persona-alias-resolver": handle_persona_alias_resolver,
    "routing-target-validator": handle_routing_target_validator,
    "secret-path-guard": handle_secret_path_guard,
    "edit-boundary-impact-gate": handle_edit_boundary_impact_gate,
    "oracle-immutability-guard": handle_oracle_immutability_guard,
    "plexus-write-boundary": handle_plexus_write_boundary,
    "worktree-guard": handle_worktree_guard,
    "no-direct-push-to-main": handle_no_direct_push_to_main,
    "no-deferral-gate": handle_no_deferral_gate,
    "lens-gate": handle_lens_gate,
    "plan-validation-gate": handle_plan_validation_gate,
}


def compute_verdict(project_path: Path, consumer: str, payload: dict, env: dict) -> dict[str, Any]:
    """Real per-consumer verdict compute — the F2-04 replacement for
    `handle_event_verify`'s F2-02 stub-allow. Unknown/unmapped consumer ids
    (a taxonomy entry with no handler yet) return an explicit "allow" with a
    `code` naming the gap — never a silent stub, but also never a false
    deny for a consumer this leg has not ported."""
    handler = DENY_HANDLERS.get(consumer)
    if handler is None:
        return _allow(f"no daemon-resident handler ported for consumer {consumer!r} yet")
    try:
        return handler(project_path, payload, env)
    except Exception as exc:  # noqa: BLE001 — a handler bug must never crash the daemon's RPC loop
        return _allow(f"handler for {consumer!r} raised {type(exc).__name__}: {exc} — shadow-fail-open, never authoritative")

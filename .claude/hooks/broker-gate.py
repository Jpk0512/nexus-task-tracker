#!/usr/bin/env python3
"""broker-gate.py — PreToolUse hook for the Task tool.

Fires before every Task invocation. Enforces the broker chokepoint:

  1. broker_state.json must exist, parse, and carry the DENY-AUTHORITY evidence
     for THIS dispatch. As of F1-04, that evidence is a VALID unexpired
     capability token minted for THIS persona by nexus_validate_brief
     (`state["capability_token"]`, verified via `_token_shadow.verify_token` —
     see TOKEN AUTHORITY below). Set NEXUS_RITUAL_AUTHORITY=1 to roll back to
     the pre-F1-04 validate/notepad ritual (approved=True + called_at within
     TURN_STALE_SECONDS) unchanged.
  2. [RITUAL MODE ONLY, NEXUS_RITUAL_AUTHORITY=1] notepad_logged_at must be
     present AND within NOTEPAD_STALE_SECONDS (900s) — the notepad ritual is
     load-bearing, not decorative (P2-07 / GAP-06).
  3. For Standard/Complex CODE-writing dispatches, a recent ACCEPTED planning-gate
     row must exist in project.db (P2-09 / GAP-10). Docs/hooks/prose meta-work
     (non-code personas) is deliberately NOT gated here. UNCHANGED in both
     token and ritual mode.
  4. Persona binding: in TOKEN mode (default) the token's own `persona` claim
     IS the binding — the token was minted for exactly one persona and a
     dispatch targeting a different one is denied (persona-mismatch), no
     separate state.persona comparison needed. [RITUAL MODE ONLY] a non-team
     dispatch must target the SAME persona `state.persona` the broker approved
     (S1-04/S1-15); team approvals carry a finite TTL (TEAM_APPROVAL_TTL_SECONDS)
     instead of an unconditional freshness skip.

TOKEN AUTHORITY (F1-04 cutover — nexus-foundation/plans/wave-1.md track (a)):
F1-02 mints a signed HMAC capability token per approved dispatch when the
plan-validation gate PASSes (`nexus_validate_brief`), persisted onto
broker_state.json. F1-03 ran that token dual-parse in SHADOW only (ritual
stayed sole authority, divergences logged to `.memory/token_shadow.jsonl` to
measure drift pre-cutover). F1-04 flips the default: a valid token is now
itself the pass evidence — no turn/notepad freshness required — while
NEXUS_RITUAL_AUTHORITY=1 is the single rollback knob back to the exact
pre-F1-04 ritual semantics. The token-shadow SHADOW TAIL (best-effort
post-exit dual-check + JSONL logging) is RETIRED as of this cutover — its
measurement window served its purpose; `_token_shadow.py` itself stays in
place as the verify library the token-mode deny path calls directly (see the
module-load comment below).

Fail-CLOSED design (P2-10): if broker_state.json is missing, malformed, or
unreadable the Task is DENIED (exit 2) — a down broker must be LOUD, not silently
bypassed. Set NEXUS_BROKER_ALLOW_DEGRADED=1 to opt out: the Task is then allowed
but a LOUD additionalContext warning is emitted every turn so the outage stays
visible. UNCHANGED by the token/ritual flip — this check runs before either mode.

Output contract (mirrors no-direct-push-to-session-branch.sh / skills-required-guard.sh):
a real object
  {"hookSpecificOutput":{"hookEventName":"PreToolUse",
                         "permissionDecision":"deny",
                         "permissionDecisionReason":<reason>}}
on stdout + the reason on stderr + sys.exit(2). The stringified-decision shape
NEVER blocked the harness.

Exit codes:
  0 = allow Task
  2 = block Task

Env overrides (test isolation):
  NEXUS_BROKER_STATE_PATH      — path to broker_state.json
  NEXUS_BROKER_ALLOW_DEGRADED  — '1' allows dispatch when the broker is down
  NEXUS_RITUAL_AUTHORITY       — '1' rolls back to the pre-F1-04 validate/
                                  notepad ritual as deny authority (unset/any
                                  other value = TOKEN mode, the default)
  _HOOK_DB_PATH                — path to project.db (planning-gate lookup)
  _HOOK_REPO_ROOT              — repo root (resolves both paths)
  _HOOK_TOKEN_KEY_PATH          — path to broker_token_key.json (token mode)
  _HOOK_TOKEN_DENYLIST_PATH     — path to token_denylist.jsonl (token mode)
"""
# NOTE: live runtime is >=3.11 via the _py.sh resolver shim, but 3.9
# IMPORT-safety is retained because the package twin runs this file un-shimmed
# under ambient python3 (3.9) and test_hooks_py39_import.py enforces it — do
# NOT introduce 3.11-only idioms (datetime.UTC, def-time X | None, match/case).
# timezone.utc call sites keep their explicit noqa: UP017.
from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Load _gate_deny from the same hooks directory (path-based, no sys.path edit)
# ---------------------------------------------------------------------------
_gd_path = Path(__file__).parent / "_gate_deny.py"
_gd_spec = importlib.util.spec_from_file_location("_gate_deny", _gd_path)
_gate_deny_mod = importlib.util.module_from_spec(_gd_spec)  # type: ignore[arg-type]
_gd_spec.loader.exec_module(_gate_deny_mod)  # type: ignore[union-attr]

# Load _heartbeat from the same hooks directory. Best-effort only — see
# _heartbeat.py; this MUST NEVER change exit code/behavior of this gate.
# emit_heartbeat() itself never raises; the try/except here additionally
# guards against the module failing to LOAD at all (missing file, etc.).
try:
    _hb_path = Path(__file__).parent / "_heartbeat.py"
    _hb_spec = importlib.util.spec_from_file_location("_heartbeat", _hb_path)
    _heartbeat_mod = importlib.util.module_from_spec(_hb_spec)  # type: ignore[arg-type]
    _hb_spec.loader.exec_module(_heartbeat_mod)  # type: ignore[union-attr]
except Exception:
    _heartbeat_mod = None


def _emit_heartbeat(event: str, decision: str, latency_ms: int) -> None:
    if _heartbeat_mod is None:
        return
    _heartbeat_mod.emit_heartbeat("broker-gate", event, decision, latency_ms)


# Load _token_shadow from the same hooks directory. F1-03 loaded this
# best-effort for a post-exit SHADOW tail only; F1-04 promotes it to the
# TOKEN-MODE VERIFY LIBRARY the deny-authority path calls directly (see
# _stage_1c_token_checks below) — a load failure must therefore fail CLOSED
# in token mode (handled inside _stage_1c_token_checks), not silently no-op.
# The try/except here only isolates a broken/missing FILE from crashing the
# whole hook with a raw traceback; a None module still fails closed downstream.
try:
    _ts_path = Path(__file__).parent / "_token_shadow.py"
    _ts_spec = importlib.util.spec_from_file_location("_token_shadow", _ts_path)
    _token_shadow_mod = importlib.util.module_from_spec(_ts_spec)  # type: ignore[arg-type]
    _ts_spec.loader.exec_module(_token_shadow_mod)  # type: ignore[union-attr]
except Exception:
    _token_shadow_mod = None


_START_TIME = time.time()


def _elapsed_ms() -> int:
    try:
        return int((time.time() - _START_TIME) * 1000)
    except Exception:
        return 0

# F1-04: the single rollback knob. '1' = pre-F1-04 validate/notepad ritual is
# deny authority (RITUAL MODE, unchanged semantics); unset/anything else =
# a valid capability token is deny authority (TOKEN MODE, the default).
RITUAL_AUTHORITY_FLAG = "NEXUS_RITUAL_AUTHORITY"

TURN_STALE_SECONDS = 300  # DEC-068 (was 120) — velocity overhaul freshness widening
NOTEPAD_STALE_SECONDS = 900  # DEC-068 (was 300)
# S1-04/S1-15: a standing per-(team,persona) approval is NOT eternal. A team-
# scoped teammate spawn is accepted without per-turn re-validation only while
# the approval is younger than this TTL (replaces the unconditional freshness
# skip — a week-old team approval must not still authorize spawns).
TEAM_APPROVAL_TTL_SECONDS = 4 * 3600
# A planning-gate submission older than this no longer counts as "recent".
# Generous vs lens-gate's 1h because a single plan covers a multi-dispatch turn.
PLANNING_GATE_WINDOW = timedelta(hours=4)

# Personas whose dispatch writes source code. Agreement with
# skills-required-guard.CODE_WRITING_PERSONAS, lens-gate.GATED_AGENTS, and
# no-deferral-gate.FIXING_AGENTS is enforced by nexus-broker/tests/test_drift_guard.py.
# A dispatch to any of these at Standard/Complex tier requires a recent ACCEPTED
# planning-gate row.
CODE_WRITING_PERSONAS = frozenset({
    "forge-ui",
    "forge-wire",
    "pipeline-data",
    "pipeline-async",
    "atlas",
    "hermes",
    "quill-ts", "quill-py",
})

# Intents that imply writing code, regardless of persona spelling.
CODE_WRITING_INTENTS = frozenset({
    "implement_ui",
    "implement_api",
    "implement_ingestion",
    "implement_schema",
    "implement_wiring",
    "test",
})


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    env = os.environ.get("_HOOK_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    return here.parent.parent.parent


def _default_state_path() -> Path:
    return _repo_root() / ".memory" / "files" / "broker_state.json"


def _ritual_authority_enabled() -> bool:
    """True iff the F1-04 rollback flag is SET — pre-F1-04 ritual is deny
    authority. Mirrors the `== "1"` convention of NEXUS_BROKER_ALLOW_DEGRADED."""
    return os.environ.get(RITUAL_AUTHORITY_FLAG) == "1"


def _db_path() -> Path:
    env = os.environ.get("_HOOK_DB_PATH")
    if env:
        return Path(env)
    return _repo_root() / ".memory" / "project.db"


# ---------------------------------------------------------------------------
# Decision emitters
# ---------------------------------------------------------------------------

def warn(msg: str) -> None:
    sys.stderr.write(f"[broker-gate] WARN: {msg}\n")


def note(msg: str) -> None:
    """A non-error, informational stderr line.

    Used when a gate deliberately does NOT apply to this dispatch (e.g. Plexus
    meta-work is out of the planning-gate's scope). Distinct prefix from WARN so
    an intentional skip is never mistaken for a degraded/failed check.
    """
    sys.stderr.write(f"[broker-gate] SKIP: {msg}\n")


# TASK-094 LEG B — gate-deny spans w/ deny reason. `main()`'s first action is
# `payload = _read_payload()`; stashing it here lets `block()`/
# `allow_with_warning()` resolve a trace_id (== session_id) for
# `_gate_deny_mod.emit_gate_span` without threading `payload` through every
# call site in this file. Read-only; never itself a source of the gate's own
# decision.
_LAST_PAYLOAD: dict = {}


def _gate_span_attrs(payload: dict | None = None) -> dict:
    """Resolve the `trace_id`/`task_id` a gate-span emission needs from a
    PreToolUse payload — falls back to `_LAST_PAYLOAD` (see above) when no
    payload is passed explicitly. Returns {} (never None) when nothing is
    resolvable — `emit_gate_span` already no-ops on a missing trace_id, so an
    empty dict here is exactly as inert as `span_attrs=None`.
    """
    p = payload if isinstance(payload, dict) else _LAST_PAYLOAD
    attrs: dict = {}
    session_id = p.get("session_id") or p.get("sessionId")
    if session_id:
        attrs["trace_id"] = session_id
    tool_input = p.get("tool_input")
    task_id = p.get("task_id") or (tool_input.get("task_id") if isinstance(tool_input, dict) else None)
    if task_id:
        attrs["task_id"] = task_id
    return attrs


def allow_with_warning(context: str) -> None:
    """Allow the Task but surface a LOUD additionalContext warning + stderr."""
    _gate_deny_mod.advise("PreToolUse", "BROKER/OUTAGE", context, stderr=True, span_attrs=_gate_span_attrs())
    sys.exit(0)


def block(reason: str) -> None:
    """Emit a real-object PreToolUse deny + stderr reason, then exit 2."""
    sys.exit(
        _gate_deny_mod.deny(
            "PreToolUse",
            "BROKER/DISPATCH-BLOCKED",
            f"Task dispatch blocked: {reason}",
            span_attrs=_gate_span_attrs(),
        )
    )


# ---------------------------------------------------------------------------
# Brief extraction (mirrors skills-required-guard._extract_brief)
# ---------------------------------------------------------------------------

def _read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _extract_brief(tool_input: dict) -> dict:
    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        for blockmatch in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
            try:
                return json.loads(blockmatch)
            except json.JSONDecodeError:
                continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return {}


def _dispatch_facts(payload: dict) -> tuple[str, str, str, str, str]:
    """Return (persona, intent, work_type, task_tier, team_name) for this dispatch.

    Reads the dispatch persona from BOTH the Task shape (`subagent_type`) AND the
    Agent/Team shape (`agent_type`), and extracts `team_name` when the harness
    routes a team-scoped teammate spawn (P6-01 / DW-02..05). The probe established
    that subagent dispatch surfaces as tool_name=Task carrying `subagent_type`;
    the TeamCreate path and any agent-team teammate carry `agent_type`/`team_name`.
    Reading both keeps a single gate correct regardless of which spawn surface the
    harness presents.

    Best-effort: any field the brief omits comes back as "" / "standard".
    """
    # The real harness envelope nests the tool input under "tool_input"; older
    # shapes use "input"; flat payloads (tests) carry fields at top level.
    # IMPORTANT: top-level "agent_type" is the CALLER's identity, not the target
    # — never read persona/team from top level when a nested dict was found.
    _nested: dict | None = None
    for _key in ("tool_input", "input"):
        _candidate = payload.get(_key)
        if isinstance(_candidate, dict):
            _nested = _candidate
            break
    tool_input: dict = _nested if _nested is not None else payload

    if _nested is not None:
        # Input was nested: agent_type at top level is the CALLER's identity —
        # never read it as the dispatch target. subagent_type at top level is
        # the dispatch target and safe to use as a fallback when tool_input is
        # empty (e.g. test shapes that put the persona top-level alongside a
        # present-but-empty tool_input key).
        persona = (
            tool_input.get("subagent_type", "")
            or tool_input.get("agent_type", "")
            or payload.get("subagent_type", "")
        )
        team_name = str(tool_input.get("team_name", "")).strip()
    else:
        # Flat payload fallback (legacy / test shapes).
        persona = (
            tool_input.get("subagent_type", "")
            or tool_input.get("agent_type", "")
            or payload.get("subagent_type", "")
            or payload.get("agent_type", "")
        )
        team_name = str(
            tool_input.get("team_name", "")
            or payload.get("team_name", "")
        ).strip()
    persona = str(persona).lower().strip()

    brief = _extract_brief(tool_input)
    intent = str(brief.get("intent", "") or tool_input.get("intent", "")).lower().strip()
    work_type = str(brief.get("work_type", "")).lower().strip()
    task_tier = str(brief.get("task_tier", "standard") or "standard").lower().strip()
    return persona, intent, work_type, task_tier, team_name


def _resolve_gate_fields(
    state: dict,
    prompt_intent: str,
    prompt_work_type: str,
    prompt_task_tier: str,
) -> tuple[str, str, str]:
    """Return (intent, work_type, task_tier), preferring broker_state.approved_brief.

    TASK-083 single-source: nexus_validate_brief persists the validated brief's
    gate fields into broker_state.json under `approved_brief`. We read those
    FIRST so the orchestrator no longer needs a full JSON brief embedded in every
    Agent prompt. We fall back to the prompt-JSON values (`prompt_*`, from
    _dispatch_facts) per-field ONLY when state lacks that field — so existing
    prompt-embedded briefs keep working unchanged (back-compat). A present-but-
    empty state field is treated as "absent" so it never overrides a real
    prompt-supplied value.
    """
    approved_brief = state.get("approved_brief")
    if not isinstance(approved_brief, dict):
        approved_brief = {}

    state_intent = str(approved_brief.get("intent", "") or "").lower().strip()
    state_work_type = str(approved_brief.get("work_type", "") or "").lower().strip()
    state_task_tier = str(approved_brief.get("task_tier", "") or "").lower().strip()

    intent = state_intent or prompt_intent
    work_type = state_work_type or prompt_work_type
    # task_tier carries a default ("standard") out of _dispatch_facts, so the
    # prompt value is always truthy — state wins only when it actually has a tier.
    task_tier = state_task_tier or prompt_task_tier
    return intent, work_type, task_tier


def _is_code_writing(persona: str, intent: str) -> bool:
    return persona in CODE_WRITING_PERSONAS or intent in CODE_WRITING_INTENTS


# ---------------------------------------------------------------------------
# Planning-gate lookup (P2-09)
# ---------------------------------------------------------------------------

def _has_recent_planning_gate(brief_feat: str) -> bool | None:
    """True/False if a recent ACCEPTED planning-gate row exists; None on DB error.

    A planning-gate submission is a context_log row with
    action_type='planning-gate-submit' whose `summary` is the accepted plan JSON
    (see log.py:cmd_planning_gate_submit). cmd_planning_gate_submit only INSERTs
    that row AFTER the gate resolves ACCEPTED — every REJECTED path exits before
    the INSERT — so the presence of such a row IS the ACCEPTED verdict; there is
    no separate verdict column to filter on. If brief_feat is given we prefer a
    row whose plan.feat matches; otherwise any recent submission counts (the
    orchestrator planned something this turn-window).

    Auto-refresh for active features (GUARDRAIL: spec-first is NOT relaxed):
    When brief_feat is given AND the feature's status in feature_specs is
    'in_progress', any ACCEPTED planning-gate row for that feature satisfies
    the gate regardless of age — the plan was accepted once and the feature is
    still actively being worked. The 4h PLANNING_GATE_WINDOW is kept as the
    fallback for features that are completed/absent/unknown, so a stale plan
    on a finished or nonexistent feature never gets a free pass.

    Connection: project.db runs in WAL journal mode (the live DB does). A
    `mode=ro` URI handle CANNOT open a WAL database — it needs write access to
    the -wal/-shm sidecars to roll the log forward, so it raises
    OperationalError('unable to open database file') unconditionally and the old
    code silently fell through to None (WARN+allow) on EVERY call, making this
    gate permanently inert. `immutable=1` opens a true read-only point-in-time
    handle that ignores the WAL sidecars and works on a live WAL DB (verified to
    return rows against .memory/project.db). This is the read-only analogue of
    lens-gate.sh's plain sqlite3.connect(DB_PATH), without taking a writable
    handle on a DB another process owns.
    """
    db = _db_path()
    if not db.exists():
        # Genuinely cannot check (no DB file). Distinct from a query failure.
        return None
    cutoff = (datetime.now(tz=timezone.utc) - PLANNING_GATE_WINDOW).isoformat()  # noqa: UP017
    try:
        conn = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        try:
            # Primary check: ACCEPTED row within the 4h window (any feat or matching feat).
            rows = conn.execute(
                """
                SELECT summary FROM context_log
                WHERE action_type = 'planning-gate-submit'
                  AND logged_at  > ?
                ORDER BY logged_at DESC
                LIMIT 25
                """,
                (cutoff,),
            ).fetchall()

            if rows:
                return True

            # No recent row. Auto-refresh: if brief_feat is given and the
            # feature is still 'in_progress', any older ACCEPTED row for that
            # feat satisfies the gate. An absent or completed feature gets no
            # free pass — the 4h window stands as the only path for them.
            if brief_feat:
                feat_status_row = conn.execute(
                    "SELECT status FROM feature_specs WHERE id = ? LIMIT 1",
                    (brief_feat,),
                ).fetchone()
                if feat_status_row and feat_status_row[0] == "in_progress":
                    # Feature is actively in-progress: accept any older ACCEPTED row
                    # whose plan.feat matches this feature id (case-insensitive).
                    older_rows = conn.execute(
                        """
                        SELECT summary FROM context_log
                        WHERE action_type = 'planning-gate-submit'
                        ORDER BY logged_at DESC
                        LIMIT 50
                        """,
                    ).fetchall()
                    for (summary,) in older_rows:
                        if not summary:
                            continue
                        try:
                            plan = json.loads(summary)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        row_feat = str(plan.get("feat", "") or plan.get("feat_id", "")).strip()
                        if row_feat.lower() == brief_feat.lower():
                            return True
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # A real query/connection failure (corrupt DB, missing table, locked
        # under immutable contention). NEVER swallow it silently — emit a LOUD
        # diagnostic so a broken planning-gate read is visible, then signal
        # "could not check" to the caller (None) rather than a false allow/deny.
        warn(
            f"planning-gate read FAILED on {db}: {type(exc).__name__}: {exc}. "
            "The planning-gate enforcement could not run for this dispatch."
        )
        return None

    return False


# ---------------------------------------------------------------------------
# Stage decomposition (R3-T03 / N06): four independently short-circuiting
# deterministic checks, CHEAPEST FIRST. A stage that denies exits immediately
# via block()/sys.exit() — no later stage's work (in particular, no sqlite
# query) ever runs on a deny path. No stage makes a model call; every check is
# a dict lookup, a file read, or (stage 5 only) a single sqlite SELECT.
#
#   1a. Bookkeeping early-out              — pure in-memory dict inspection.
#       Cheapest possible check: no filesystem, no parsing beyond the already-
#       decoded stdin payload. Handles TaskCreate/TaskUpdate noise.
#   1b. broker_state.json read             — ONE stat+read of a small local
#       JSON file. Cheap, but strictly more expensive than 1a (real I/O).
#       Fail-CLOSED (P2-10) on missing/malformed/unreadable state.
#   1c. In-memory state checks             — approval, turn-freshness/TTL,
#       persona-binding, notepad freshness. All operate on the dict already
#       loaded in 1b; no additional I/O. Ordered internally cheapest-first
#       (plain dict.get comparisons before the datetime-arithmetic freshness
#       checks).
#   5.  Planning-gate DB lookup            — the ONLY stage that opens a
#       sqlite connection and runs a query. Strictly the most expensive stage
#       (process-local I/O to project.db), so it runs LAST and only when
#       stages 1a-1c have not already denied or exited, AND only when the
#       dispatch is in-scope for the planning gate at all (a scope check that
#       itself costs nothing and is done before the DB is touched).
#
# (There is no separate "stage 1d"/"stage 2-4" — the historical P2-07/P2-09/
# P2-10 numbering from the module docstring maps onto 1b (P2-10), 1c (P2-07 +
# turn/persona checks), and 5 (P2-09) respectively.)
# ---------------------------------------------------------------------------


def _stage_1a_bookkeeping_and_carveout(persona: str, team_name: str) -> None:
    """Cheapest stage: in-memory-only early-out. Exits the process if hit.

    LOCKOUT SAFETY (defense-in-depth): a real agent-spawning dispatch — whether
    it surfaces as the Task tool (subagent_type), an Agent-tool spawn
    (agent_type), or a TeamCreate teammate (agent_type/team_name) — ALWAYS
    carries a persona/team. A payload with NONE of these is not a dispatch: it
    is native task bookkeeping (TaskCreate/TaskUpdate status/owner edits) or
    noise. The matcher already scopes this hook to Task|TeamCreate, but if that
    matcher is ever widened, the broker chokepoint must NOT block the
    orchestrator's own task list. Mirror the early-out the other three dispatch
    gates make (skills-required-guard / persona-alias-resolver /
    dispatch-announce) and silent-pass. Fail toward NOT blocking bookkeeping.
    """
    if not persona and not team_name:
        sys.exit(0)


def _stage_1b_read_broker_state() -> dict:
    """Second-cheapest stage: ONE read of broker_state.json. Fail-CLOSED (P2-10).

    Returns the parsed state dict on success. Exits the process (deny, or allow
    + LOUD warning under NEXUS_BROKER_ALLOW_DEGRADED=1) on any read/parse error.
    """
    allow_degraded = os.environ.get("NEXUS_BROKER_ALLOW_DEGRADED") == "1"

    state_path_env = os.environ.get("NEXUS_BROKER_STATE_PATH")
    state_path = Path(state_path_env) if state_path_env else _default_state_path()

    degraded_reason: str | None = None
    try:
        state = json.loads(state_path.read_text())
    except FileNotFoundError:
        degraded_reason = "broker_state.json not found"
    except json.JSONDecodeError as exc:
        degraded_reason = f"broker_state.json malformed ({exc})"
    except OSError as exc:
        degraded_reason = f"broker_state.json unreadable ({exc})"

    if degraded_reason is not None:
        if allow_degraded:
            allow_with_warning(
                f"BROKER DEGRADED: {degraded_reason}. "
                "NEXUS_BROKER_ALLOW_DEGRADED=1 is set — Task allowed WITHOUT broker "
                "validation. Dispatches are UNGUARDED until the broker is restored. "
                "Start nexus-broker and unset NEXUS_BROKER_ALLOW_DEGRADED to re-arm."
            )
        block(
            f"{degraded_reason} — broker unavailable. "
            "Start nexus-broker or set NEXUS_BROKER_ALLOW_DEGRADED=1 to bypass."
        )
    return state


def _token_allowed_personas(token):
    """DEC-096: the CLOSED set of personas a capability token authorizes.

    Reads the signed `allowed_personas` claim (normalized, lower/stripped). An
    absent/empty/malformed claim DEGRADES to the one-element set `{persona}` so
    a pre-DEC-096 token stays exact-match-equivalent — there is NO
    is_workflow_leg special-case branch (Option C permanently rejected). The
    claim is signed, so it cannot be widened (or stripped to force the
    degenerate fallback) without failing the `verify_token` check above."""
    raw = token.get("allowed_personas") if isinstance(token, dict) else None
    if isinstance(raw, list):
        members = {
            str(p).lower().strip()
            for p in raw
            if isinstance(p, str) and str(p).strip()
        }
        if members:
            return members
    persona_claim = ""
    if isinstance(token, dict):
        persona_claim = str(token.get("persona", "") or "").lower().strip()
    return {persona_claim} if persona_claim else set()


def _stage_1c_token_checks(
    state: dict,
    persona: str,
    intent: str,
    work_type: str,
    task_tier: str,
) -> tuple[str, str, str]:
    """F1-04 TOKEN MODE (default, NEXUS_RITUAL_AUTHORITY unset/!=1): a VALID
    unexpired capability token minted for THIS persona is the sole pass
    evidence — no turn/notepad freshness required (acceptance 1). Persona
    binding IS the token's own `persona` claim (acceptance: 'in token mode the
    token's persona scope IS the binding') — no separate state.persona/
    team_name comparison, unlike RITUAL mode's _stage_1c_state_checks.

    Fail-CLOSED (acceptance 2): token absent/tampered/expired/persona-mismatch
    all DENY with an actionable message naming nexus_validate_brief as the
    mint site and NEXUS_RITUAL_AUTHORITY=1 as the rollback. A failed
    _token_shadow module load (see the load comment near the top of this
    file) also denies here — it is no longer a best-effort tail, it is the
    deny-authority verify library.
    """
    intent, work_type, task_tier = _resolve_gate_fields(state, intent, work_type, task_tier)

    _rollback_hint = (
        "Call nexus_validate_brief to mint a fresh capability token for this "
        f"persona/plan, or set {RITUAL_AUTHORITY_FLAG}=1 to roll back to the "
        "pre-F1-04 validate/notepad ritual."
    )

    if _token_shadow_mod is None:
        block(
            "capability-token verify library (_token_shadow.py) failed to load — "
            "cannot verify the dispatch token. " + _rollback_hint
        )

    token = _token_shadow_mod.extract_token(state)
    token_ok, token_reason = _token_shadow_mod.verify_token(token)
    if not token_ok:
        block(
            f"no valid capability token for this dispatch (reason={token_reason}). "
            + _rollback_hint
        )

    allowed = _token_allowed_personas(token)
    if persona and allowed and persona not in allowed:
        block(
            f"dispatch targets persona '{persona}' but it is not a member of this "
            f"capability token's allowed_personas set {sorted(allowed)} "
            f"(persona-mismatch — DEC-096 closed-set membership). " + _rollback_hint
        )

    return intent, work_type, task_tier


def _stage_1c_state_checks(
    state: dict,
    persona: str,
    intent: str,
    work_type: str,
    task_tier: str,
    team_name: str,
) -> tuple[str, str, str]:
    """Third stage: in-memory-only checks against the already-loaded state dict.

    No additional I/O — every check here is a dict lookup, a datetime parse, or
    string comparison. Ordered cheapest-first internally: plain dict.get
    truthiness checks (approval) before datetime-arithmetic freshness checks
    (turn staleness, notepad staleness). Exits the process (block()) on any
    failure. Returns the resolved (intent, work_type, task_tier) tuple — the
    single-source TASK-083 resolution against state.approved_brief — for use by
    stage 5's scope decision.
    """
    # TASK-083: single-source the gate fields. Now that broker_state.json has
    # been read, prefer its persisted approved_brief (written by
    # nexus_validate_brief) for intent/work_type/task_tier, falling back
    # per-field to the prompt-JSON values from _dispatch_facts only when state
    # lacks them (back-compat). This lets the orchestrator stop re-embedding a
    # full JSON brief in every Agent prompt: the broker already validated and
    # persisted these fields.
    intent, work_type, task_tier = _resolve_gate_fields(state, intent, work_type, task_tier)

    # --- approval (cheapest: one dict.get) ---
    if not state.get("approved", False):
        blocked_persona = state.get("persona", "unknown")
        block(
            f"broker rejected dispatch to '{blocked_persona}' — not allowed. "
            "Call nexus_validate_brief with a valid brief first."
        )

    # --- turn freshness (per-turn for a top-level Task; per-(team,persona) for
    #     a team-scoped teammate spawn) ---
    # A dynamic Workflow creates a Team once, then spawns teammates across MANY
    # turns from a single broker approval (DW-02..05). Holding those spawns to the
    # 120s per-turn window would spuriously block legitimate teammate dispatches.
    # So when THIS dispatch carries a team_name AND the broker approved THIS
    # persona for THAT team, we accept the standing per-(team,persona) approval
    # and skip the turn-staleness check. The fresh-turn 120s contract still
    # governs every ordinary top-level Task dispatch (no team_name) unchanged.
    #
    # NOTE: server.py nexus_validate_brief writes `team_name` into state when the
    # caller supplies it, so a team-scoped spawn whose state carries team_name
    # enables this relaxation. If team_name is absent (old-style approval or a
    # plain top-level Task), the check falls through to the standard 120s
    # freshness window unchanged. The hook fails CLOSED rather than open.
    state_team = str(state.get("team_name", "") or "").strip()
    state_persona = str(state.get("persona", "") or "").lower().strip()
    team_approval_ok = bool(
        team_name
        and state_team
        and state_team == team_name
        and (not persona or not state_persona or state_persona == persona)
    )

    # --- persona binding (S1-04/S1-15): non-team path ---
    # The broker approved ONE persona. A non-team dispatch (no team_name) that
    # targets a DIFFERENT persona is riding another brief's approval — block it.
    # The comparison stays lenient exactly like team_approval_ok above: both
    # sides lowercased+stripped, and an empty side (old-style state, intent-only
    # dispatch) skips the check rather than blocking.
    if not team_name and persona and state_persona and persona != state_persona:
        block(
            f"broker approved persona '{state_persona}' but dispatch targets "
            f"'{persona}' — re-validate the brief for '{persona}' "
            "(call nexus_validate_brief with the persona you are dispatching)."
        )

    called_at_str = state.get("called_at")
    if not called_at_str:
        block(
            "broker_state.json has no called_at timestamp — "
            "nexus_validate_brief was not called this turn."
        )

    try:
        called_at = datetime.fromisoformat(called_at_str)
        if called_at.tzinfo is None:
            called_at = called_at.replace(tzinfo=timezone.utc)  # noqa: UP017
        now = datetime.now(tz=timezone.utc)  # noqa: UP017
        age_seconds = (now - called_at).total_seconds()
    except Exception as exc:
        block(f"broker_state.json called_at is malformed ({exc}) — cannot verify turn.")

    if age_seconds > TURN_STALE_SECONDS:
        if not team_approval_ok:
            block(
                f"broker_state.json is stale ({age_seconds:.0f}s old, max {TURN_STALE_SECONDS}s) — "
                "call nexus_validate_brief again for this turn."
            )
        # S1-04/S1-15: the team relaxation is bounded by a finite TTL — a
        # matched per-(team,persona) approval covers multi-turn teammate spawns
        # but NOT indefinitely.
        if age_seconds > TEAM_APPROVAL_TTL_SECONDS:
            block(
                f"team approval for '{state_team}' has expired ({age_seconds:.0f}s old, "
                f"max {TEAM_APPROVAL_TTL_SECONDS}s TTL) — call nexus_validate_brief "
                "again for this team-scoped dispatch."
            )

    # --- notepad load-bearing (P2-07) ---
    # The broker only writes approved=True when the brief carried notepad_topic;
    # the gate additionally requires the notepad to have been READ this turn
    # (notepad_logged_at present AND within the turn window).
    if task_tier in {"standard", "complex"}:
        notepad_ts = state.get("notepad_logged_at")
        if not notepad_ts:
            block(
                "notepad_logged_at is absent — run "
                "'python3 .memory/log.py notepad list --topic <scope>' and call "
                "nexus_notepad_ping before dispatching."
            )
        try:
            np_at = datetime.fromisoformat(notepad_ts)
            if np_at.tzinfo is None:
                np_at = np_at.replace(tzinfo=timezone.utc)  # noqa: UP017
            np_age = (datetime.now(tz=timezone.utc) - np_at).total_seconds()  # noqa: UP017
        except Exception as exc:
            block(f"notepad_logged_at is malformed ({exc}) — re-run the notepad ritual.")
        if np_age > NOTEPAD_STALE_SECONDS:
            block(
                f"notepad_logged_at is stale ({np_age:.0f}s old, max {NOTEPAD_STALE_SECONDS}s) — "
                "re-run the notepad ritual and nexus_notepad_ping for this turn."
            )

    return intent, work_type, task_tier


def _stage_5_planning_gate(
    payload: dict,
    persona: str,
    intent: str,
    work_type: str,
    task_tier: str,
) -> None:
    """Most expensive stage: the ONLY stage that opens a sqlite connection.

    Runs LAST, and only reaches the DB query at all when the dispatch is
    in-scope (a free scope check gates the expensive query). (P2-09 / GAP-10).
    """
    # --- planning-gate: EXPLICITLY SCOPED to code-writing FEATURE dispatches ---
    # (P2-09 / GAP-10). The spec-first planning gate applies ONLY to a
    # Standard/Complex tier dispatch that actually writes feature code (a
    # code-writing persona OR a feature-implementation intent). Plexus meta-work
    # — hook/skill/doc/broker edits routed to non-code personas like general, or
    # any simple-tier dispatch — is OUT OF SCOPE and is EXPLICITLY skipped with a
    # clear stderr note. There is deliberately NO silent fall-through branch: the
    # dispatch is either gate-checked, or audibly recorded as out-of-scope.
    is_feature_code = (
        task_tier in {"standard", "complex"}
        and _is_code_writing(persona, intent)
        and work_type != "meta"
    )
    if not is_feature_code:
        if task_tier not in {"standard", "complex"}:
            scope_reason = f"task_tier='{task_tier or 'unset'}' (not standard/complex)"
        elif work_type == "meta":
            scope_reason = (
                f"work_type='meta' (Plexus meta-work: hooks/docs/skills/broker edits) "
                f"— persona='{persona or '?'}' / intent='{intent or '?'}'"
            )
        else:
            scope_reason = (
                f"persona='{persona or '?'}' / intent='{intent or '?'}' is not a "
                "code-writing feature dispatch (Plexus meta-work: hooks/docs/skills/"
                "broker edits)"
            )
        note(
            f"planning-gate not applicable — {scope_reason}. Spec-first (Constitution "
            "Art. I) gates code-writing FEATURE dispatches only; this dispatch is "
            "out of scope and allowed without a planning-gate row."
        )
        sys.exit(0)

    # Reuse the same nested-dict resolution so brief extraction is consistent.
    _ti_nested: dict | None = None
    for _ti_key in ("tool_input", "input"):
        _ti_cand = payload.get(_ti_key)
        if isinstance(_ti_cand, dict):
            _ti_nested = _ti_cand
            break
    _ti: dict = _ti_nested if _ti_nested is not None else payload
    brief = _extract_brief(_ti)
    brief_feat = str(
        brief.get("feat") or brief.get("feat_id") or brief.get("task_id") or ""
    ).strip()
    gate = _has_recent_planning_gate(brief_feat)
    if gate is None:
        # Could not check (no project.db, or a read failure already logged LOUD
        # inside _has_recent_planning_gate). DOCUMENTED FAIL-OPEN (S1-15c): do
        # NOT lock out a legitimate dispatch on infrastructure absence — but the
        # fail-open must be LOUD (additionalContext + stderr) AND recorded to the
        # gate_blocks telemetry sink so a silently-inert planning gate stays
        # visible. Honesty over silent policy.
        msg = (
            "PLANNING-GATE FAIL-OPEN: planning-gate check could not run — "
            "project.db absent or unreadable. Cannot confirm a plan was ACCEPTED "
            "for this code-writing dispatch; ALLOWING UNCHECKED (documented "
            "fail-open — see docs/ORCHESTRATOR-GATES.md, broker-gate planning-gate "
            "row). Restore .memory/project.db to re-arm enforcement."
        )
        _gate_deny_mod._record_block("PreToolUse", "BROKER/PLANNING-GATE-FAIL-OPEN", msg)
        _gate_deny_mod.advise(
            "PreToolUse", "BROKER/PLANNING-GATE-FAIL-OPEN", msg, stderr=True,
            span_attrs=_gate_span_attrs(payload),
        )
        sys.exit(0)
    if gate is False:
        block(
            f"no ACCEPTED planning-gate row in the last "
            f"{int(PLANNING_GATE_WINDOW.total_seconds() // 3600)}h for this "
            f"{task_tier} code-writing dispatch to '{persona or intent}'. "
            "Run 'python3 .memory/log.py planning-gate submit --feat <id> --json ...' "
            "first (Constitution Art. I, spec-first)."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    payload = _read_payload()
    global _LAST_PAYLOAD
    _LAST_PAYLOAD = payload if isinstance(payload, dict) else {}
    persona, intent, work_type, task_tier, team_name = _dispatch_facts(payload)

    # Stage 1a — cheapest: in-memory-only early-out (no I/O).
    _stage_1a_bookkeeping_and_carveout(persona, team_name)

    # Stage 1b — one broker_state.json read (fail-closed, P2-10). UNCHANGED in
    # both token and ritual mode.
    state = _stage_1b_read_broker_state()

    # Stage 1c — F1-04 authority flip. TOKEN MODE (default): a valid
    # capability token is the sole pass evidence, no ritual freshness. RITUAL
    # MODE (NEXUS_RITUAL_AUTHORITY=1, rollback): unchanged pre-F1-04 semantics
    # (approval, turn freshness/TTL, persona binding, notepad freshness).
    if _ritual_authority_enabled():
        intent, work_type, task_tier = _stage_1c_state_checks(
            state, persona, intent, work_type, task_tier, team_name
        )
    else:
        intent, work_type, task_tier = _stage_1c_token_checks(
            state, persona, intent, work_type, task_tier
        )

    # Stage 5 — most expensive: the sole sqlite query (planning-gate lookup),
    # gated behind a free in-scope check so the DB is touched only when needed.
    # UNCHANGED in both token and ritual mode.
    _stage_5_planning_gate(payload, persona, intent, work_type, task_tier)

    sys.exit(0)


if __name__ == "__main__":
    # Heartbeat wraps the WHOLE dispatch: main() always exits via sys.exit()
    # (either directly, or via block()/allow_with_warning()) at every one of
    # its many early-return points, so catching SystemExit here at the single
    # outermost call site captures every exit path — deny AND silent-pass —
    # without touching any of main()'s internal control flow. Purely
    # additive: re-raises the exact same SystemExit unchanged.
    #
    # F1-04: the token-shadow post-exit tail (_emit_token_shadow) is RETIRED
    # here — its measurement window (F1-03) served its purpose now that the
    # token check runs INSIDE main() as the deny authority itself.
    # _token_shadow.py stays in place as the verify library _stage_1c_token_
    # checks calls directly; only this best-effort post-hoc shadow call site
    # is gone.
    _exit_code = 0
    try:
        main()
    except SystemExit as _exc:
        _exit_code = _exc.code if isinstance(_exc.code, int) else (0 if _exc.code is None else 1)
        raise
    finally:
        _decision = "block" if _exit_code == 2 else "allow"
        _emit_heartbeat("PreToolUse", _decision, _elapsed_ms())

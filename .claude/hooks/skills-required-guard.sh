#!/usr/bin/env python3
# PreToolUse hook (matcher: Task) — enforces CONTRACT R19: brief-driven skill loading.
#
# Logic:
#   1. Parse brief JSON from the Task tool input (description or prompt field).
#   2. Extract subagent_type and skills_required from the brief.
#   3. If persona is a code-writing persona AND skills_required is absent/empty: DENY (exit 2).
#   4. If skills_required is non-empty but missing mandatory skills per SKILL_MAP.md: WARN (advisory, exit 0).
#   5. Fail open (exit 0, stderr WARN) on any parse error or missing SKILL_MAP.md.
#
# Exit codes: 0 = pass/warn, 2 = deny (block dispatch).
#
# Output contract (mirrors no-direct-push-to-main.sh): a real object
#   {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":<reason>}}
# on stdout + the reason on stderr + sys.exit(2). The earlier stringified
# {"hookSpecificOutput": json.dumps({...})} + exit 0 NEVER blocked the harness.
#
# SHADOW-MODE COMPARISON (R2-T15, added; spec §7 /
# nexus-redesign/plans/03-r2e2-design-APPROVED.md): this script is ALSO wired
# to SubagentStop (see .claude/settings.json). On that event it branches into
# _shadow_mode_compare(), which compares the dispatch's DECLARED
# skills_required (the same brief this script already parses for the
# PreToolUse gate) against the ACTUAL skill_load_events rows recorded by the
# skill-load-capture.py PostToolUse:Skill hook for that session. A mismatch
# (declared but never actually loaded) is LOGGED ONLY — advisory, never a
# deny — per spec: "flipping to deny before a single row of real data exists
# risks blocking legitimate dispatches on an uncalibrated gate." Promotion of
# this comparison to a hard deny is explicitly out of scope here — R3-T07/T08
# own that hardening once shadow data shows an acceptable false-positive rate.
# main() dispatches on payload.hook_event_name (falls back to .event), same
# field verify-after-edit.sh already reads for the same kind of branching.

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Load _gate_deny from the same hooks directory.
_gd_path = Path(__file__).parent / "_gate_deny.py"
_gd_spec = importlib.util.spec_from_file_location("_gate_deny", _gd_path)
_gate_deny_mod = importlib.util.module_from_spec(_gd_spec)  # type: ignore[arg-type]
_gd_spec.loader.exec_module(_gate_deny_mod)  # type: ignore[union-attr]

# Load _heartbeat from the same hooks directory. Best-effort only — see
# _heartbeat.py; this MUST NEVER change exit code/behavior of this gate.
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
    _heartbeat_mod.emit_heartbeat("skills-required-guard", event, decision, latency_ms)


_START_TIME = time.time()


def _elapsed_ms() -> int:
    try:
        return int((time.time() - _START_TIME) * 1000)
    except Exception:
        return 0

def _load_code_writing_personas() -> frozenset:
    """Derive the code-writing persona roster from deliverables.json.

    Non-readonly, non-tombstone entries in the same hooks directory are the
    single source of truth. Falls back to a minimal hardcoded set if the file
    is absent or malformed so the gate is never silently disabled.
    """
    _FALLBACK = frozenset({
        "forge-ui", "forge-wire",
        "pipeline-data", "pipeline-async",
        "atlas", "hermes", "quill-ts", "quill-py",
    })
    try:
        deliverables_path = Path(__file__).parent / "deliverables.json"
        manifest = json.loads(deliverables_path.read_text())
        result = set()
        for persona, cfg in manifest.items():
            if persona.startswith("_") or not isinstance(cfg, dict):
                continue
            note = cfg.get("_note", "")
            if isinstance(note, str) and "Tombstone" in note:
                continue
            if "**/*" in cfg.get("must_not_modify", []):
                continue
            result.add(persona)
        return frozenset(result) if result else _FALLBACK
    except Exception:
        return _FALLBACK


# Personas that MUST have non-empty skills_required in their brief.
# Derived from deliverables.json (single source of truth); read-only personas
# (must_not_modify: ["**/*"]) and tombstones are excluded automatically.
CODE_WRITING_PERSONAS = _load_code_writing_personas()


def _repo_root() -> Path:
    """Resolve repo root from the script location (walk parents for .memory).

    Mirrors broker-gate.py:_default_state_path so the deployable and Plexus
    share one resolution strategy. Env override (_HOOK_REPO_ROOT) wins for
    test isolation; otherwise we never hardcode a foreign path.
    """
    env = os.environ.get("_HOOK_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    # Last-resort fallback: two levels up from .claude/hooks/.
    return here.parent.parent.parent


REPO_ROOT = _repo_root()
SKILL_MAP_PATH = Path(
    os.environ.get("_HOOK_SKILL_MAP_PATH")
    or (REPO_ROOT / "docs" / "agents" / "SKILL_MAP.md")
)
BROKER_STATE_PATH = Path(
    os.environ.get("NEXUS_BROKER_STATE_PATH")
    or (REPO_ROOT / ".memory" / "files" / "broker_state.json")
)

# DB path for the shadow-mode read side. Mirrors lens-gate.sh's own
# _HOOK_DB_PATH convention exactly (same env var name) so both gates resolve
# the same real project.db in production and the same isolated temp DB under
# test — no new env-var surface introduced.
DB_PATH = Path(
    os.environ.get("_HOOK_DB_PATH")
    or (REPO_ROOT / ".memory" / "project.db")
)


def _read_approved_brief_skills() -> list:
    """Return skills_required from broker_state.approved_brief, or [].

    Mirrors broker-gate.py:_resolve_gate_fields — reads approved_brief from the
    last validated brief persisted by nexus_validate_brief. A prompt that carries
    no fenced skills_required block may still have had one in the validated brief,
    so we check here BEFORE Gate 1 denies.

    Fails OPEN (returns []) on any I/O or parse error — Gate 1 still fires if
    the prompt has no skills and the state file is missing/unreadable.
    """
    try:
        raw = BROKER_STATE_PATH.read_text()
        state = json.loads(raw)
    except Exception:
        return []
    approved_brief = state.get("approved_brief")
    if not isinstance(approved_brief, dict):
        return []
    skills = approved_brief.get("skills_required")
    if isinstance(skills, list):
        return [s for s in skills if isinstance(s, str) and s.strip()]
    if isinstance(skills, str):
        return [s.strip() for s in skills.split(",") if s.strip()]
    return []


def _deny(reason: str) -> int:
    """Emit canonical PreToolUse deny + stderr reason. Returns 2."""
    return _gate_deny_mod.deny("PreToolUse", "SKILLS/MISSING", reason)


def _advise(context: str) -> int:
    """Emit canonical PreToolUse advisory (additionalContext). Returns 0."""
    return _gate_deny_mod.advise("PreToolUse", "SKILLS/HINT", context)


def _load_skill_map() -> dict[tuple[str, str], list[str]]:
    """Parse SKILL_MAP.md table into {(persona, work_type): [skills]}.

    Fails OPEN (returns {}) with a stderr WARN if the map is genuinely
    absent — Gate 2 then finds no mandatory skills and never blocks. Gate 1
    (empty-skills deny) is unaffected; it does not depend on the map.
    """
    if not SKILL_MAP_PATH.exists():
        sys.stderr.write(
            f"[skills-required-guard] WARN: SKILL_MAP.md not found at "
            f"{SKILL_MAP_PATH} — Gate 2 (mandatory-skill check) disabled, "
            "failing open.\n"
        )
        return {}
    result: dict[tuple[str, str], list[str]] = {}
    in_table = False
    for line in SKILL_MAP_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith(("| persona", "|---")):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 3:
            continue
        persona, work_type, skills_raw = parts[0], parts[1], parts[2]
        if not persona or persona.startswith("-"):
            continue
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
        result[(persona, work_type)] = skills
    return result


# Free-text 'skills_required: a, b, c' prose line. Matches a line whose key is
# skills_required (optionally quoted, with ':' or '=' separator) and whose value
# is a comma/space-separated list of skill tokens. This is the additive fallback
# for briefs written as PROSE rather than a fenced ```json block (the prior
# extractor saw ONLY JSON, so a prose skills_required line was silently ignored
# and a code-writing persona slipped past Gate 1).
_SKILLS_LINE_RE = re.compile(
    r"""['"]?skills_required['"]?\s*[:=]\s*(.+)""",
    re.IGNORECASE,
)


def _extract_skills_freetext(raw):
    """Scan raw prose for a 'skills_required: a, b' line; return list[str] or [].

    Only the value up to the end of the line is consumed. Tokens are split on
    commas (and whitespace as a secondary separator) and stripped of surrounding
    brackets/quotes so 'skills_required: forge-ui-conventions, rsc-boundary-rules'
    and a bare 'skills_required: forge-ui-conventions' both resolve.
    """
    if not isinstance(raw, str):
        return []
    for line in raw.splitlines():
        m = _SKILLS_LINE_RE.search(line.strip())
        if not m:
            continue
        value = m.group(1).strip().strip("[]")
        # Comma is the primary separator; fall back to whitespace if no commas.
        parts = value.split(",") if "," in value else value.split()
        skills = [p.strip().strip("'\"[]") for p in parts]
        skills = [s for s in skills if s]
        if skills:
            return skills
    return []


def _extract_brief(tool_input: dict) -> dict:
    """Try to parse the brief JSON from the task description or prompt field.

    Order: fenced ```json block, then whole-field JSON, then — additively — a
    free-text 'skills_required: a, b' prose line. The free-text path NEVER
    overrides a JSON brief that already carried skills_required; it only supplies
    a brief when no JSON parsed, OR backfills skills_required when JSON parsed but
    omitted it.
    """
    freetext_skills: list = []
    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        # The brief may be embedded in a markdown JSON block
        for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
            try:
                parsed = json.loads(block)
            except json.JSONDecodeError:
                continue
            if "skills_required" not in parsed:
                backfill = _extract_skills_freetext(raw)
                if backfill:
                    parsed["skills_required"] = backfill
            return parsed
        # Or the whole field is JSON
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            if "skills_required" not in parsed:
                backfill = _extract_skills_freetext(raw)
                if backfill:
                    parsed["skills_required"] = backfill
            return parsed
        # No JSON in this field — remember any prose skills_required line so a
        # purely free-text brief still surfaces its skills.
        if not freetext_skills:
            freetext_skills = _extract_skills_freetext(raw)
    if freetext_skills:
        return {"skills_required": freetext_skills}
    return {}


def _query_loaded_skill_ids(dispatch_id: str) -> list:
    """Return skill_id values actually observed for this dispatch_id.

    log.py has NO read-side CLI for skill_load_events (only the write-side
    `skill record-load` — verified against the live argparse tree; there is
    no `skill list` / `skill-load list` subcommand at all). Rather than shell
    out to a command that does not exist, this reads skill_load_events
    directly via sqlite3 against DB_PATH — the same direct-read pattern
    lens-gate.sh already uses for its own ground-truth cross-check (read-only
    SELECT, no schema/log.py write-path touched).

    Fails open (returns []) on ANY error — missing DB file, missing table,
    query error, malformed rows — because this is advisory shadow-mode only;
    a query failure must never be mistaken for "nothing was loaded" in a way
    that would ever gate a dispatch (it doesn't gate anything today
    regardless).
    """
    if not dispatch_id or not DB_PATH.is_file():
        return []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            rows = conn.execute(
                "SELECT DISTINCT skill_id FROM skill_load_events WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchall()
        finally:
            conn.close()
        return [str(r[0]).strip().lower() for r in rows if r and r[0]]
    except Exception:
        return []


def _record_shadow_mismatch(root, dispatch_id: str, persona: str, missing: list) -> None:
    """Best-effort advisory log of a declared-but-never-loaded skill mismatch.

    Two sinks, both required:
    1. stderr — a visible-output channel an operator (or a test) can observe
       directly, without needing to query the DB. Shadow-mode data that is
       only ever written to a DB row nobody reads defeats the purpose of
       running it in shadow mode before R3 promotion.
    2. `log.py feedback add` (same sink feedback-capture.py writes through)
       for durable/queryable history — not a new table; DO_NOT_TOUCH for this
       dispatch excludes .memory/schema.sql and .memory/log.py from being
       touched by this persona regardless.

    NEVER raises; NEVER affects exit code (shadow mode only).
    """
    sys.stderr.write(
        "[skills-required-guard] SHADOW-MODE MISMATCH: declared "
        f"skills_required for '{persona}' (dispatch_id={dispatch_id}) not "
        f"observed as actual Skill-tool loads: {missing}\n"
    )

    log_py = root / ".memory" / "log.py"
    if not log_py.is_file():
        return
    context = {
        "dispatch_id": dispatch_id,
        "persona": persona,
        "missing_skill_loads": missing,
        "captured_by": "skills-required-guard-shadow",
    }
    cmd = [
        sys.executable,
        str(log_py),
        "feedback",
        "add",
        "--source",
        "hook",
        "--severity",
        "low",
        "--category",
        "skills_required_shadow_mismatch",
        "--message",
        f"Declared skills_required not observed as actual Skill loads for "
        f"'{persona}': {missing}",
        "--context-json",
        json.dumps(context, default=str),
    ]
    try:
        subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=30)
    except Exception:
        return


def _shadow_mode_compare(payload: dict) -> int:
    """SubagentStop-time shadow comparison. ALWAYS returns 0 (advisory only).

    Reads the dispatch's declared skills_required the same way the PreToolUse
    path does (brief JSON on description/prompt, or approved_brief backfill),
    compares against skill_load_events rows for this dispatch_id, and logs
    (never denies) any declared skill never actually observed as a Skill-tool
    load. required subset-of actual, per spec §7.
    """
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}
    # Correlation key: prefer an explicit dispatch_id (what skill-load-capture.py
    # also prefers when writing rows) — fall back to session_id only when no
    # dispatch_id is present anywhere on the payload.
    dispatch_id = str(
        tool_input.get("dispatch_id")
        or payload.get("dispatch_id")
        or payload.get("session_id")
        or payload.get("sessionId")
        or "unknown"
    )
    persona = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or tool_input.get("subagent_type")
        or "unknown"
    )
    persona = str(persona).strip().lower()

    # Declared skills: try the brief on this payload first (rarely present at
    # SubagentStop), then fall back to broker_state.approved_brief — the same
    # backfill source the PreToolUse gate uses.
    #
    # BUG FIXED HERE: _extract_brief() reads description/prompt/input off of
    # WHATEVER dict it is given — the PreToolUse path (main(), below) already
    # passes it tool_input correctly, but this SubagentStop branch was
    # previously passing the full top-level `payload` instead of `tool_input`.
    # Since description/prompt/input live under tool_input, not at the
    # payload's top level, that made _extract_brief() always return {} here —
    # declared skills silently fell through to the approved_brief backfill
    # (or "nothing declared" when that was also empty/isolated in tests),
    # and the comparison against skill_load_events never ran on the real
    # per-dispatch declared list. Passing tool_input fixes the read.
    brief = _extract_brief(tool_input)
    declared = brief.get("skills_required")
    if not declared:
        declared = _read_approved_brief_skills()
    if isinstance(declared, str):
        declared = [s.strip() for s in declared.split(",") if s.strip()]
    if not isinstance(declared, list) or not declared:
        return 0  # nothing declared — nothing to compare

    declared_set = {s.lower() for s in declared}
    actual_set = set(_query_loaded_skill_ids(dispatch_id))

    missing = sorted(declared_set - actual_set)
    if missing:
        _record_shadow_mismatch(_repo_root(), dispatch_id, persona, missing)

    return 0


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0  # fail open

    # SubagentStop branch: shadow-mode comparison only, never a gate decision.
    # hook_event_name is the field verify-after-edit.sh already reads for the
    # same kind of multi-event branching; .event is a defensive fallback.
    hook_event_name = str(payload.get("hook_event_name") or payload.get("event") or "")
    if hook_event_name == "SubagentStop":
        return _shadow_mode_compare(payload)

    # Normalise where the tool payload lives. Claude's PreToolUse:Task nests the
    # arguments under '.tool_input'; some surfaces use '.input'; a few pass them at
    # top level. Resolve in that order — '.tool_input' FIRST — matching the sibling
    # gate persona-alias-resolver.sh exactly. (Reading '.input'/top-level only made
    # this gate silently fail open on real Claude Task dispatches, where
    # subagent_type lives under '.tool_input'.)
    tool_input: dict = payload.get(
        "tool_input", payload.get("input", payload)
    )
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Get the persona. Read BOTH the Task shape (subagent_type) AND the
    # Agent/Team shape (agent_type) — whichever spawn surface the harness
    # presents (P6-01 / DW-02..05). A team-scoped teammate brief carries
    # agent_type rather than subagent_type; without this the guard would silently
    # pass an empty-skills code-writing teammate spawned via a Team.
    subagent_type: str = (
        tool_input.get("subagent_type", "")
        or tool_input.get("agent_type", "")
        or payload.get("subagent_type", "")
        or payload.get("agent_type", "")
    ).lower().strip()

    if not subagent_type:
        return 0  # not a subagent dispatch we can inspect — plain TaskCreate/
        # TaskUpdate bookkeeping carries no persona and must pass untouched.

    # Parse the brief JSON from description/prompt
    brief = _extract_brief(tool_input)

    # skills_required from brief (may be absent, None, or a list)
    skills_required = brief.get("skills_required")
    if skills_required is None:
        # Also check if it's a top-level field in tool_input
        skills_required = tool_input.get("skills_required")

    # Normalise to list
    if isinstance(skills_required, str):
        skills_required = [s.strip() for s in skills_required.split(",") if s.strip()]
    elif not isinstance(skills_required, list):
        skills_required = []

    skills_required_set = {s.lower() for s in skills_required}

    # --- Backfill: if prompt has no skills, try broker_state.approved_brief ---
    # broker-gate.py persists the validated brief under approved_brief via
    # nexus_validate_brief (TASK-083). A dispatch prompt that omits the fenced
    # skills_required block is NOT automatically missing skills — the broker may
    # already have them recorded. Read them here BEFORE Gate 1 denies, so the
    # gate only fires when NEITHER the prompt NOR the approved_brief has skills.
    if not skills_required:
        approved_skills = _read_approved_brief_skills()
        if approved_skills:
            skills_required = approved_skills
            skills_required_set = {s.lower() for s in skills_required}
            sys.stderr.write(
                "[skills-required-guard] backfilled skills_required from "
                f"approved_brief: {skills_required}\n"
            )

    # --- Gate 1: DENY if code-writing persona has empty skills_required ---
    if subagent_type in CODE_WRITING_PERSONAS and not skills_required:
        return _deny(
            f"skills_required is absent or empty for code-writing persona "
            f"'{subagent_type}'. Per CONTRACT R19, every brief for a "
            "code-writing persona MUST list explicit skills. See "
            "docs/agents/SKILL_MAP.md for the minimum required skills per "
            "(persona, work_type)."
        )

    # --- Gate 2: Warn if mandatory skills are missing ---
    if skills_required:
        work_type: str = brief.get("work_type", "").lower().strip()
        skill_map = _load_skill_map()

        # Find matching row(s) — exact match first.
        mandatory: list[str] = []
        if work_type:
            mandatory = skill_map.get((subagent_type, work_type), [])
            if not mandatory:
                # work_type given but matches no row: fall back to every row
                # for this persona (foundational convention skill enforced).
                for (p, _wt), skills in skill_map.items():
                    if p == subagent_type:
                        mandatory.extend(skills)
        else:
            # Doc-only/empty work_type: the persona's '*' minimum row ONLY —
            # never accumulate rows from multiple work_types. Accumulating
            # here previously surfaced integration-specific rows (tableau,
            # claude-api, ...) and duplicate entries on a generic dispatch
            # that has no work_type to disambiguate against.
            mandatory = skill_map.get((subagent_type, "*"), [])

        # Dedup, preserving first-seen order — accumulation above can repeat
        # a skill across multiple rows (e.g. a foundational skill mandated by
        # both a '*' row and a specific-work_type row).
        seen: set = set()
        deduped: list[str] = []
        for s in mandatory:
            key = s.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(s)
        mandatory = deduped

        # Existence-filter: never advise a skill absent from the actual
        # installed roster — this hook runs installed (target project or this
        # meta-repo), and a demanded skill with no matching dir is never
        # loadable regardless of how the map got it.
        skills_dir = REPO_ROOT / ".claude" / "skills"
        if skills_dir.is_dir():
            mandatory = [s for s in mandatory if (skills_dir / s).is_dir()]

        missing = [s for s in mandatory if s.lower() not in skills_required_set]
        if missing:
            return _advise(
                f"skills-required-guard WARN: skills_required for "
                f"'{subagent_type}' (work_type='{work_type}') is missing "
                f"mandatory skills: {missing}. Per SKILL_MAP.md these are "
                "required. Add them to the brief unless this is intentionally "
                "a partial dispatch."
            )

    return 0


if __name__ == "__main__":
    # main() returns an int (0/2), never raising SystemExit itself — capture
    # it here so heartbeat covers every one of main()'s early-return exit
    # paths (deny AND silent-pass) without touching its internal control flow.
    # Heartbeat label stays "PreToolUse" unconditionally (pre-existing
    # behavior, unchanged by the SubagentStop shadow-mode addition above) —
    # the SubagentStop branch is advisory-only and always returns 0 anyway,
    # so mislabeling it here has no decision-relevant effect; not fixed as
    # part of this dispatch to avoid inventing an unverified env var.
    _rc = main()
    _emit_heartbeat("PreToolUse", "block" if _rc == 2 else "allow", _elapsed_ms())
    sys.exit(_rc)

#!/usr/bin/env python3
"""routing-target-validator.py — PreToolUse hook for Task|TeamCreate.

Implements research/decisions/2026-07-16-routing-target-validation-gate.md
(accepted): validates every orchestrator-proposed dispatch persona AND each
`skills_required` entry against the closed enums already maintained in this
tree — `deliverables.json` (persona roster, live vs tombstone) and the
`.claude/skills/*/` dir listing (skill roster) — so a hallucinated or
RETIRED target fails EARLY with a typed cause instead of reaching the
harness as an opaque late spawn error, or silently starving a leaf of a
skill it never loaded.

PERSONA HALF — fail-CLOSED (deny, exit 2):
  - target present in deliverables.json, not tombstoned          -> allow.
  - target is a retired BASE NAME (forge/pipeline/quill)         -> defer
    entirely to persona-alias-resolver.sh (do NOT double-deny; that hook
    already redirects-or-denies these from brief scope hints).
  - target present but tombstoned (e.g. the `-pro` merge-retired
    variants)                                                     -> deny,
    typed cause ROUTING/RETIRED-PERSONA.
  - target absent from the manifest entirely (hallucinated/typo)  -> deny,
    typed cause ROUTING/UNKNOWN-PERSONA, naming the nearest live personas.

SKILL HALF — fail-OPEN + LOUD advise (exit 0), mirroring
skills-required-guard.sh Gate 2: any `skills_required` entry that resolves
to no installed `.claude/skills/<name>/` dir gets a typed advisory
(ROUTING/UNKNOWN-SKILL) but never blocks the dispatch. Namespaced/plugin
skill names (containing ":") are skipped — they are not resolvable against
a local dir listing.

Deliberately NOT an extension of broker-gate.py (keeps its capability-token
authority single-responsibility) and NOT an extension of
persona-alias-resolver.sh (bash, persona-only, deliberately fail-open
default). A separate hook is the only place both target kinds get one
legible home.

A payload carrying no persona at all (native TaskCreate/TaskUpdate
bookkeeping, or a TeamCreate call with no agent_type yet) is not a
dispatch this hook can classify — silent-pass, exactly like the sibling
persona/skills gates.

Enum-load failure (deliverables.json missing/malformed) fails OPEN + LOUD
(stderr WARN) — a missing manifest must never lock out all dispatch.

Output contract (mirrors broker-gate.py / skills-required-guard.sh): a real
object {"hookSpecificOutput":{"hookEventName":"PreToolUse",
"permissionDecision":"deny","permissionDecisionReason":<reason>}} on stdout
+ the reason on stderr + sys.exit(2) for a deny; an additionalContext object
+ sys.exit(0) for an advisory.

Exit codes: 0 = allow/advise, 2 = deny.

Env overrides (test isolation): _HOOK_REPO_ROOT — repo root (resolves the
.claude/skills dir the same way broker-gate.py resolves .memory).
"""
# NOTE: live runtime is >=3.11 via the _py.sh resolver shim, but 3.9
# IMPORT-safety is retained because the package twin runs this file
# un-shimmed under ambient python3 (3.9) and test_hooks_py39_import.py
# enforces it — do NOT introduce 3.11-only idioms (datetime.UTC, def-time
# X | None, match/case).
from __future__ import annotations

import difflib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load _gate_deny from the same hooks directory (path-based, no sys.path edit)
# ---------------------------------------------------------------------------
_gd_path = Path(__file__).parent / "_gate_deny.py"
_gd_spec = importlib.util.spec_from_file_location("_gate_deny", _gd_path)
_gate_deny_mod = importlib.util.module_from_spec(_gd_spec)  # type: ignore[arg-type]
_gd_spec.loader.exec_module(_gate_deny_mod)  # type: ignore[union-attr]

# F2-04 SHADOW wiring (tranche-B) — best-effort, never authoritative. See
# _verify_shadow.py's module docstring. Guarded: an isolated test-fixture
# copy of just this hook file (no _verify_shadow.py sibling) must never
# crash the gate — any load/install failure here is swallowed.
try:
    _vs_path = Path(__file__).parent / "_verify_shadow.py"
    _vs_spec = importlib.util.spec_from_file_location("_verify_shadow", _vs_path)
    _verify_shadow_mod = importlib.util.module_from_spec(_vs_spec)  # type: ignore[arg-type]
    _vs_spec.loader.exec_module(_verify_shadow_mod)  # type: ignore[union-attr]
    _verify_shadow_mod.install_shadow_wiring(_gate_deny_mod, "dispatch.pre.verify", "routing-target-validator")
except Exception:
    pass


# Retired base names — the redirectable-or-deny-by-scope-hint names that
# persona-alias-resolver.sh already owns end-to-end. Deferring here (no
# action, exit 0) avoids a double-deny; these ARE also present as tombstone
# entries in deliverables.json, so without this carve-out they would fall
# into the RETIRED branch below and this hook would deny them itself,
# stepping on the alias-resolver's redirect-from-brief-scope behavior.
BASE_NAMES = frozenset({"forge", "pipeline", "quill"})


def _repo_root() -> Path:
    """Resolve repo root from this file's location (walk parents for .memory).

    Mirrors broker-gate.py:_repo_root so both hooks resolve the same root in
    both the live tree and the (self-contained) nexus-package tree.
    """
    env = os.environ.get("_HOOK_REPO_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".memory").is_dir():
            return candidate
    return here.parent.parent.parent


def _skills_dir() -> Path:
    return _repo_root() / ".claude" / "skills"


# ---------------------------------------------------------------------------
# Persona-enum loading (deliverables.json — the existing SoT; also read by
# skills-required-guard.sh's _load_code_writing_personas()).
# ---------------------------------------------------------------------------

def _load_manifest():
    """Load deliverables.json from this hook's own directory. None on failure."""
    path = Path(__file__).parent / "deliverables.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _classify_personas(manifest: dict):
    """Return (live, retired) frozensets of persona names from the manifest.

    An entry is RETIRED iff its `_note` field contains "tombstone" or
    "retired" (case-insensitive) — the two markers already used by every
    tombstone/`-pro` entry in deliverables.json (e.g. the `forge` and
    `forge-ui-pro` entries). Every other non-underscore-prefixed dict entry
    is LIVE. Underscore-prefixed keys (`_comment`) are metadata, never a
    persona name.
    """
    live: set = set()
    retired: set = set()
    for name, cfg in manifest.items():
        if name.startswith("_") or not isinstance(cfg, dict):
            continue
        note = cfg.get("_note", "")
        is_retired = isinstance(note, str) and (
            "tombstone" in note.lower() or "retired" in note.lower()
        )
        (retired if is_retired else live).add(name)
    return frozenset(live), frozenset(retired)


def _nearest_live_personas(bad: str, live: frozenset, limit: int = 3) -> list:
    matches = difflib.get_close_matches(bad, sorted(live), n=limit, cutoff=0.3)
    return matches if matches else sorted(live)[:limit]


# ---------------------------------------------------------------------------
# Dispatch payload extraction (mirrors broker-gate.py's _dispatch_facts /
# _extract_brief shape).
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


def _tool_input(payload: dict) -> dict:
    for key in ("tool_input", "input"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    return payload


def _extract_persona(payload: dict, tool_input: dict) -> str:
    persona = (
        tool_input.get("subagent_type", "")
        or tool_input.get("agent_type", "")
        or payload.get("subagent_type", "")
        or payload.get("agent_type", "")
    )
    return str(persona).lower().strip()


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_brief(tool_input: dict) -> dict:
    for field in ("description", "prompt", "input"):
        raw = tool_input.get(field, "")
        if not isinstance(raw, str) or not raw.strip():
            continue
        for block in _JSON_BLOCK_RE.findall(raw):
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _extract_skills_required(brief: dict, tool_input: dict) -> list:
    skills = brief.get("skills_required")
    if skills is None:
        skills = tool_input.get("skills_required")
    if isinstance(skills, str):
        return [s.strip() for s in skills.split(",") if s.strip()]
    if isinstance(skills, list):
        return [s for s in skills if isinstance(s, str) and s.strip()]
    return []


def _unknown_skills(skills_required: list, skills_dir: Path) -> list:
    """Skills_required entries with no installed .claude/skills/<name>/ dir.

    Skips namespaced/plugin skill names (containing ":", e.g.
    "socraticode:codebase-exploration") — not resolvable against a local
    dir listing. Fails open (returns []) if the skills dir itself is absent.
    """
    if not skills_dir.is_dir():
        return []
    missing = []
    for skill in skills_required:
        if ":" in skill:
            continue
        if not (skills_dir / skill).is_dir():
            missing.append(skill)
    return missing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    payload = _read_payload()
    tool_input = _tool_input(payload)
    persona = _extract_persona(payload, tool_input)

    if not persona:
        sys.exit(0)  # native bookkeeping (TaskCreate/TaskUpdate) — not a dispatch

    manifest = _load_manifest()
    if manifest is None:
        sys.stderr.write(
            "[routing-target-validator] WARN: deliverables.json failed to load "
            "— persona-enum check DISABLED, failing open. Dispatch allowed "
            "unchecked.\n"
        )
        sys.exit(0)

    live, retired = _classify_personas(manifest)

    if persona in BASE_NAMES:
        # Retired-but-redirectable base name: persona-alias-resolver.sh owns
        # the redirect-from-brief-scope / deny decision for these.
        sys.exit(0)

    if persona not in live:
        if persona in retired:
            sys.exit(
                _gate_deny_mod.deny(
                    "PreToolUse",
                    "ROUTING/RETIRED-PERSONA",
                    f"'{persona}' is a RETIRED dispatch target (tombstoned in "
                    "deliverables.json) — it is no longer a legal Task "
                    f"subagent_type. Live personas: {', '.join(sorted(live))}.",
                )
            )
        nearest = _nearest_live_personas(persona, live)
        sys.exit(
            _gate_deny_mod.deny(
                "PreToolUse",
                "ROUTING/UNKNOWN-PERSONA",
                f"'{persona}' is not a known dispatch target (absent from "
                "deliverables.json — hallucinated or misspelled). Nearest "
                f"live personas: {', '.join(nearest)}.",
            )
        )

    # Persona is live — advisory-only skill-enum check (fail-open + LOUD).
    brief = _extract_brief(tool_input)
    skills_required = _extract_skills_required(brief, tool_input)
    if skills_required:
        missing = _unknown_skills(skills_required, _skills_dir())
        if missing:
            _gate_deny_mod.advise(
                "PreToolUse",
                "ROUTING/UNKNOWN-SKILL",
                f"skills_required for '{persona}' names {missing} — no "
                "installed .claude/skills/<name>/ dir found. The leaf will "
                "silently fail to load this skill. Fix the name or drop it "
                "from skills_required.",
                stderr=True,
            )

    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""gate_runner.py — consolidated per-hook-event gate runner (R3-T10 / N15).

THE orchestrator-speed leaf (docs/archive/nexus-redesign/plans/09-r3-plan-dag.md N15 /
plans/11-gate-enforcement-audit.md). Executes N12's verdict table: instead of
the harness spawning ONE process per settings.json hook-array entry (6-8
entries on the busiest matchers), it spawns exactly ONE process per hook
EVENT — this script — which then runs the union of that event's checks
itself, in N12's cheapest-first order (plans/11 section 1's `latency (avg ms)`
column), short-circuiting the instant a deny-capable check denies.

Two dispatch modes per check, chosen per-check below (never uniformly, see
the design note in each EVENT_CHAINS entry):

  * TRUE in-process (`kind="inprocess"`): the check module is imported via
    importlib and its `main()` is called directly in THIS process — zero
    fork/exec. Only used for checks already proven, by direct source
    inspection, to have a clean `main() -> int` with no behavior that
    differs under import vs. `python3 script.py` (their internal
    `sys.exit(0)` early-returns are caught by `_run_inprocess` below and
    translated to a plain return code — behaviorally identical to a
    subprocess exit). Reserved for ADVISORY-ONLY checks (never deny) so a
    runner-side bug in the wrapper can never newly turn an allow into a
    block — `_run_inprocess` fails OPEN (rc=0) on any unexpected exception.

  * subprocess, unchanged file (`kind="bash"` / `kind="python"`): the
    original hook FILE is invoked exactly as before (bash interpreter, or
    `sys.executable` — NOT `_py.sh`, see below), with the SAME stdin
    payload. This is BYTE-FOR-BYTE IDENTICAL behavior by construction (the
    exact same code path runs) and is the ONLY mode used for every
    deny-capable / byte-for-byte-critical gate: `lens-gate.sh` (the Lens
    structural backstop, Constitution — never removed), `dispatch-shape-guard.sh`
    (R1-T10), `oracle-immutability-guard.sh` (R1-T11), `broker-gate.py`,
    `skills-required-guard.sh`, `persona-alias-resolver.sh`,
    `no-deferral-gate.sh`, `worktree-guard.sh`, `no-direct-push-to-main.sh`,
    `secret-path-guard.sh`, `edit-boundary-impact-gate.sh` (N14). Their own
    test suites invoke these same files directly and are therefore
    completely unaffected by this consolidation.

SPEED win even on the subprocess path: python checks are invoked via
`sys.executable` (the interpreter THIS runner is already running under)
instead of routing back through `_py.sh` — each such nested `_py.sh` call
previously re-ran full candidate discovery (measured ~90ms, see
`_py.sh`'s new resolution cache, R3-T10 sibling fix) OR, post-fix, the
cheap-but-nonzero cache-hit path; going direct to `sys.executable` is ~0ms
of resolution cost per nested check instead. Combined with true
consolidation (1 harness-level process spawn instead of N) and real
short-circuit (a cheap deny skips every later, pricier check), this is
where the >=50% p50 reduction acceptance criterion is met.

Scope note (measured, not assumed): `pretooluse-bash` and `pretooluse-write`
are handled by the SIBLING `gate_runner.sh` (pure bash), NOT this file.
Direct wall-clock benchmarking showed routing THOSE two chains through a
python process is a net REGRESSION — neither chain had any `_py.sh`/python
check to begin with (both were already pure bash-to-bash), so adding a
python-interpreter startup on top of them costs more than the
one-process-instead-of-N consolidation saves. This file is reserved for
events whose chain contains `_py.sh`-wrapped python checks, where bypassing
`_py.sh` is the dominant, measured win (see this leaf's notepad for the
before/after numbers on both siblings).

Advisory-message aggregation: when every check in a chain allows, at most
ONE combined JSON `hookSpecificOutput.additionalContext` is printed (each
check's own message, newline-joined) — never multiple stacked JSON blobs
from one process invocation.

.claude/hooks/*.py execute under the SYSTEM python3 (this file's own
`_py.sh`-resolved interpreter is >=3.11 — see settings.json wiring) but
stays 3.9-import-safe like every other hook module in this tree (no
`datetime.UTC`, no def-time `X | None`, no `match`/`case`) since the
package twin is unshimmed under ambient python3.

F1-08 CUTOVER (nexus-foundation/plans/wave-1.md track (c)): the review-panel
SubagentStop check's completion-marker resolution is schema-first now —
`_envelope_shadow.resolve_marker()` treats a valid typed return envelope
(return_envelope.schema.json) as AUTHORITATIVE, falling back to the single
legacy marker-regex branch (`_LEGACY_DONE_PATTERN`) only when no valid
envelope is present. Rollback flag (kept 1 release): env
`NEXUS_REGEX_AUTHORITY=1` restores the pre-cutover F1-07 ordering (regex
authoritative) exactly. Every resolution — either mode — logs one row to
`.memory/return_parse_shadow.jsonl` via `log_shadow_event`, so observability
survives the cutover.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent

HOOK_EVENT_NAMES = {
    "pretooluse-dispatch": "PreToolUse",
    "subagentstop": "SubagentStop",
}

# R6-T06 / N35 (plans/14 SS4): body gate for the LANE_ROUTER wiring below.
# Flag absent -> run_event() never appends the lane-router entry, so
# EVENT_CHAINS drives "pretooluse-dispatch" completely unmodified (byte-
# identical pre-N35 behavior). Rollback: rm .claude/lane-router.enabled, or
# git revert of the flag-flip commit.
LANE_ROUTER_FLAG = HOOKS_DIR.parent / "lane-router.enabled"


def _lane_router_enabled() -> bool:
    return LANE_ROUTER_FLAG.exists()


# R6-T07 / N38 (plans/14 SS8): body gate for the REVIEW_PANEL_TRIGGER wiring
# below. Flag absent -> run_event() never appends the review-panel entry, so
# EVENT_CHAINS drives "subagentstop" completely unmodified (byte-identical
# pre-N38 behavior). Rollback: rm .claude/review-panel.enabled, or git revert
# of the flag-flip commit. Implemented INLINE (no new hook-tree module) --
# N38's write_scope names only gate_runner.py + its package twin + the test
# file + the flag file, unlike N35's sibling router_core.py module.
REVIEW_PANEL_FLAG = HOOKS_DIR.parent / "review-panel.enabled"


def _review_panel_enabled() -> bool:
    return REVIEW_PANEL_FLAG.exists()


# F1-08 CUTOVER: schema-parse via _envelope_shadow.resolve_marker() is now
# AUTHORITATIVE for the review-panel "qualifying DONE" decision below (mirrors
# return-validator.py / feedback-capture.py's marker vocabulary) — this
# compiled pattern is demoted to the SINGLE legacy marker-regex (MARKER_RE)
# fallback branch gate_runner.py retains, used only when no valid typed
# envelope is found in the return. Rollback (kept 1 release): env
# NEXUS_REGEX_AUTHORITY=1 restores regex-first ordering exactly.
_LEGACY_DONE_PATTERN = re.compile(r"^\s*##\s+NEXUS:DONE\b", re.IGNORECASE | re.MULTILINE)

REVIEW_PANEL_DECISIONS_LOG = HOOKS_DIR.parent.parent / ".memory" / "files" / "review_panel_decisions.jsonl"


def _review_panel_state_path() -> Path:
    """Mirrors do-not-touch-guard.sh's _state_path(): the same
    broker_state.json that guard already reads `do_not_touch` from at this
    same SubagentStop boundary — this reads the sibling node-contract
    fields (risk_tier / irreversible / recall_precision_prone /
    cascade_critical) off the same `approved_brief` object."""
    env = os.environ.get("NEXUS_BROKER_STATE_PATH")
    if env:
        return Path(env)
    return HOOKS_DIR.parent.parent / ".memory" / "files" / "broker_state.json"


def _review_panel_read_approved_brief(state_path: Path) -> dict:
    """Return the approved brief dict, or {} on any missing/malformed state
    (advisory: a read failure must never break the SubagentStop path)."""
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    brief = state.get("approved_brief")
    return brief if isinstance(brief, dict) else {}


def classify_panel_requirement(brief: dict) -> dict:
    """Pure function: given an approved_brief-shaped dict, return the panel
    requirement classification per `Skill review-panel` /
    docs/agents/templates/review-workflow.md. Two INDEPENDENT triggers
    (never merged into one flag — a leg can qualify via either, both, or
    neither):

      end_only := risk_tier == 'T2' OR irreversible is True
        (review-workflow.md "Tier routing" table — the only switch that
        turns on the full multi-shard panel.)
      per_leg  := recall_precision_prone is True OR cascade_critical is True
        (review-workflow.md "Tier-1 PER-LEG targeted review" section --
        locked-OFF default per CONTRACT.md's R3-T14 schema; NEVER a
        blanket per-leg pass.)

    panel_required := end_only OR per_leg. reasons[] names every trigger
    that fired (possibly more than one).
    """
    risk_tier = str(brief.get("risk_tier") or "").strip().upper()
    irreversible = bool(brief.get("irreversible") is True)
    recall_precision_prone = bool(brief.get("recall_precision_prone") is True)
    cascade_critical = bool(brief.get("cascade_critical") is True)

    reasons: list = []
    if risk_tier == "T2":
        reasons.append("risk_tier=T2")
    if irreversible:
        reasons.append("irreversible=true")
    end_only = risk_tier == "T2" or irreversible

    if recall_precision_prone:
        reasons.append("recall_precision_prone=true")
    if cascade_critical:
        reasons.append("cascade_critical=true")
    per_leg = recall_precision_prone or cascade_critical

    return {
        "panel_required": end_only or per_leg,
        "end_only": end_only,
        "per_leg": per_leg,
        "reasons": reasons,
    }


def _write_review_panel_journal(decision: dict, persona: str, journal_path: str = None) -> None:
    """Append ONE JSONL line — ONLY for a qualifying (panel_required=True)
    completion, mirroring do-not-touch-guard.sh's silent-unless-a-hit
    discipline (the review panel is a much rarer, higher-signal event than
    a lane pick — unlike router_core.py's journal-every-decision choice).
    Best-effort: a journal-write failure must never break the SubagentStop
    path (same discipline as router_core.py's _write_routing_journal)."""
    path = Path(journal_path) if journal_path else REVIEW_PANEL_DECISIONS_LOG
    line = {
        "ts": time.time(),
        "persona": persona,
        "panel_required": decision.get("panel_required"),
        "end_only": decision.get("end_only"),
        "per_leg": decision.get("per_leg"),
        "reasons": decision.get("reasons"),
    }
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")


def route_panel_decision(payload: dict, *, state_path: str = None, journal_path: str = None) -> dict:
    """gate_runner.py's subagentstop entry point (R6-T07 / N38): read the
    approved brief, require a genuine DONE completion (this is an "accept"
    gate — BLOCKED/REVISE/NEEDS-DECISION returns are not panel-qualifying),
    classify the panel requirement, journal a line ONLY when it qualifies,
    and return the decision.

    ADVISORY ONLY: this never raises and gate_runner.py never denies on its
    result. Callers gate the WHOLE check behind
    .claude/review-panel.enabled — this function itself performs no flag
    check, so it stays independently unit-testable (mirrors
    router_core.route_dispatch()'s exact discipline).
    """
    try:
        text = str(
            payload.get("last_assistant_message")
            or payload.get("response", {}).get("text")
            or payload.get("tool_response", {}).get("text")
            or ""
        )
        # F1-08: schema-parse first (AUTHORITATIVE); _LEGACY_DONE_PATTERN is
        # the single legacy fallback, used only when no valid envelope is
        # found (see _resolve_marker / _envelope_shadow.resolve_marker).
        legacy_marker = "DONE" if _LEGACY_DONE_PATTERN.search(text) else None
        marker = _resolve_marker("gate_runner.review_panel", text, legacy_marker)
        if marker != "DONE":
            return {
                "panel_required": False, "end_only": False, "per_leg": False,
                "reasons": [], "qualifying_completion": False,
            }

        resolved_state_path = Path(state_path) if state_path else _review_panel_state_path()
        brief = _review_panel_read_approved_brief(resolved_state_path)

        decision = classify_panel_requirement(brief)
        decision["qualifying_completion"] = True

        persona = str(
            payload.get("agent_persona")
            or payload.get("subagent_type")
            or (payload.get("tool_input") or {}).get("subagent_type")
            or "unknown"
        ).strip().lower()

        if decision["panel_required"]:
            _write_review_panel_journal(decision, persona, journal_path=journal_path)

        return decision
    except Exception:
        # Fail-open, silently -- this is advisory; a crash here must never
        # surface as a gate error (mirrors route_dispatch's own discipline).
        return {
            "panel_required": False, "end_only": False, "per_leg": False,
            "reasons": [], "qualifying_completion": False,
        }

# Each entry: (check_name, target, kind, deny_capable)
#   kind "bash"        -> subprocess, /bin/bash <target>              (unchanged file)
#   kind "python"      -> subprocess, sys.executable <target>         (unchanged file, no _py.sh)
#   kind "inprocess"   -> import <target> as a module, call .main()   (advisory-only checks)
#   kind "triage"      -> special adapter for lm-studio-triage-gate.py (N18)
#
# Order is CHEAPEST-FIRST among deny-capable checks per N12's telemetry
# table (plans/11-gate-enforcement-audit.md section 1 `latency (avg ms)`
# column), so a short-circuiting deny skips the MOST expensive remaining
# checks first. Advisory/telemetry-only checks (deny_capable=False) always
# run after every deny-capable check in the chain has allowed.
EVENT_CHAINS: dict[str, list[tuple[str, str, str, bool]]] = {
    "pretooluse-dispatch": [
        # 7.8ms avg (N12 table row 2) — cheapest deny-capable check, first.
        ("broker-gate", "broker-gate.py", "python", True),
        # 68.8ms avg (row 12).
        ("dispatch-shape-guard", "dispatch-shape-guard.sh", "bash", True),
        # 70.4ms avg (row 3).
        ("skills-required-guard", "skills-required-guard.sh", "python", True),
        # 110.4ms avg (row 4) — most expensive deny-capable check, last.
        ("persona-alias-resolver", "persona-alias-resolver.sh", "bash", True),
        # Advisory/telemetry tail — only reached once every deny-capable
        # check above has allowed.
        ("dispatch-announce", "dispatch-announce.sh", "bash", False),
        ("dispatch-capture", "dispatch-capture.py", "inprocess", False),
        ("lm-studio-triage", "lm-studio-triage-gate.py", "triage", False),
    ],
    "subagentstop": [
        # Finding #6 (2026-07-12, drift-analysis): MOVED to the FRONT of the
        # chain, ahead of every deny-capable check. run_event()'s short-
        # circuit (`if rc == 2 and deny_capable: return 2`) means anything
        # placed AFTER no-deferral-gate/lens-gate/plan-validation-gate never
        # runs at all when one of those denies (forcing a REVISE) — which was
        # the confirmed root cause of dispatch_telemetry's near-total harness-
        # side gap (0/69 non-conductor rows): completion-capture.py sat in
        # the advisory tail, so any denied SubagentStop skipped it entirely,
        # and it never wrote dispatch_telemetry at all even when reached.
        # completion-capture.py has NO ordering dependency on any deny-
        # capable check (confirmed by grep: none of them read
        # completion_events.jsonl/activity_open.jsonl/anything it writes),
        # so moving it first is safe and also closes the identical pre-
        # existing gap for completion_events.jsonl's own completeness
        # (a denied dispatch previously left NO ledger row at all).
        ("completion-capture", "completion-capture.py", "inprocess", False),
        # 4.4ms avg (row 5) — cheapest deny-capable check, first among the
        # deny-capable set.
        ("no-deferral-gate", "no-deferral-gate.sh", "python", True),
        # 20.7ms avg (row 6) — the Lens structural backstop. Byte-for-byte
        # subprocess of the UNCHANGED file only — never reimplemented.
        ("lens-gate", "lens-gate.sh", "python", True),
        # R3-T04 / N10: ~1-5ms typical (non-planner persona: reads
        # broker_state.json's `persona` field, exits) — only pays the
        # ~150-300ms `uv run` scorer cost on an actual planner return. Deny-
        # capable, fail-closed: any scorer-invocation error is treated as a
        # FAIL, never a silent pass. Placed after the two cheaper deny-capable
        # checks above, before the advisory tail, per this file's own
        # cheapest-first-among-deny-capable ordering rule.
        ("plan-validation-gate", "plan-validation-gate.py", "python", True),
        # N12 verdict: MERGE (plans/11 section 2) — folded in as an
        # in-process SubagentStop sub-check instead of its own settings.json
        # entry/process. Reachability re-confirmed directly (see notepad):
        # invoking it manually against a NEXUS:DONE-shaped payload runs its
        # full predicate cleanly (rc=0, reads stdin, no crash) — it is live,
        # reachable code; the zero historical telemetry was a missing
        # heartbeat call (return-validator.py never had one), not dead code.
        ("return-validator", "return-validator.py", "inprocess", False),
        ("return-summarizer", "return-summarizer.sh", "inprocess", False),
        ("feedback-capture", "feedback-capture.py", "inprocess", False),
        ("do-not-touch-guard", "do-not-touch-guard.sh", "inprocess", False),
        ("skills-required-guard", "skills-required-guard.sh", "python", False),
    ],
}


def _load_module(unique_name: str, filename: str):
    """Load `filename` (which may end in `.sh` despite being python source —
    several hooks in this tree keep the `.sh` extension for historical
    matcher-naming reasons) as a module. `spec_from_file_location` cannot
    auto-detect a loader for a non-`.py` extension, so the SourceFileLoader
    is passed explicitly rather than relying on extension sniffing."""
    path = str(HOOKS_DIR / filename)
    loader = importlib.machinery.SourceFileLoader(unique_name, path)
    spec = importlib.util.spec_from_file_location(unique_name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _heartbeat_module():
    """Import the shared heartbeat helper (same sink/schema every gate uses)."""
    return _load_module("_gate_runner_heartbeat", "_heartbeat.py")


def _envelope_shadow_module():
    """Import the F1-07 dual-parse SHADOW helper (same module every gate
    uses — see _envelope_shadow.py's own docstring)."""
    return _load_module("_gate_runner_envelope_shadow", "_envelope_shadow.py")


def _resolve_marker(hook: str, text: str, legacy_marker: str | None) -> str | None:
    """F1-08 AUTHORITATIVE marker resolution — see _envelope_shadow.py's
    resolve_marker() docstring (schema-first, legacy_marker demoted to the
    single legacy-fallback branch; NEXUS_REGEX_AUTHORITY=1 rolls back to
    regex-first for 1 release). Fails open to `legacy_marker` on any shadow-
    module import/call error, mirroring the prior _shadow_compare's fail-open
    discipline — this call must never prevent gate_runner.py from reaching a
    decision, whichever source ultimately wins."""
    try:
        return _envelope_shadow_module().resolve_marker(
            hook=hook, raw_text=text, legacy_regex_marker=legacy_marker
        )
    except Exception:
        return legacy_marker


def _emit_heartbeat(hook: str, event: str, decision: str, latency_ms: int) -> None:
    with contextlib.suppress(Exception):
        _heartbeat_module().emit_heartbeat(hook, event, decision, latency_ms)


# Checks whose OWN file already calls emit_heartbeat() internally (confirmed
# by direct source grep, R3-T10 N15 revise-cycle-1 / TASK-010 -- the runner
# invokes each as an UNCHANGED subprocess of its own file, and that file's
# own heartbeat call fires regardless of who invoked it). run_event() below
# must NOT ALSO emit a wrapper-level heartbeat for these, or every
# invocation doubles the row in hook_heartbeat.jsonl. Checks NOT in this set
# (the in-process advisory checks this same leaf folded in, plus the N18
# triage adapter) never had their own heartbeat call -- for THEM the
# wrapper's emission is their only telemetry source and must be kept.
_SELF_EMITTING_CHECKS = frozenset({
    "broker-gate",
    "dispatch-shape-guard",
    "skills-required-guard",
    "persona-alias-resolver",
    "no-deferral-gate",
    "lens-gate",
})


def _run_subprocess(name: str, target: str, kind: str, raw_payload: str) -> tuple[int, str, str, int]:
    if kind == "bash":
        cmd = ["/bin/bash", str(HOOKS_DIR / target)]
    else:
        # kind == "python": go straight to the interpreter THIS runner is
        # already running under — never back through _py.sh (see module
        # docstring's SPEED note).
        cmd = [sys.executable, str(HOOKS_DIR / target)]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, input=raw_payload, capture_output=True, text=True,
            env=dict(os.environ), timeout=30,
        )
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 0, "", f"[gate-runner] {name} timed out (fail-open)"
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return rc, out, err, elapsed_ms


def _run_inprocess(name: str, target: str, raw_payload: str) -> tuple[int, str, str, int]:
    """Import `target` and call its `main()` with stdin/stdout/stderr swapped
    to isolated buffers so its own I/O never touches the runner's real
    streams until we decide it should. ADVISORY-ONLY checks alone use this
    path (see EVENT_CHAINS) — any unexpected exception fails OPEN (rc=0),
    mirroring the `|| true` every one of these carried as a standalone
    settings.json entry."""
    start = time.monotonic()
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    sys.stdin = io.StringIO(raw_payload)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = out_buf, err_buf
    rc = 0
    try:
        mod = _load_module(f"_gate_runner_chk_{name.replace('-', '_')}", target)
        result = mod.main()
        rc = int(result) if isinstance(result, int) else 0
    except SystemExit as exc:
        code = exc.code
        rc = code if isinstance(code, int) else (1 if code else 0)
    except Exception as exc:  # noqa: BLE001 -- fail-open by design, see docstring
        print(f"[gate-runner] {name} crashed in-process: {exc!r}", file=old_stderr)
        rc = 0
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return rc, out_buf.getvalue(), err_buf.getvalue(), elapsed_ms


def _run_triage(name: str, target: str, raw_payload: str) -> tuple[int, str, str, int]:
    """N18 adapter: lm-studio-triage-gate.py's own `main()` expects a
    `{"summary": "..."}` stdin shape, not the raw PreToolUse tool-call
    payload — and it should only fire for a dispatch TARGETING a reviewer
    persona (lens / lens-fast), per its own docstring ("ahead of any
    API-model reviewer dispatch"). This adapter derives the summary from
    the dispatch brief and calls `triage_route()` directly (in-process,
    fail-open by the module's own construction — see its TRIAGE_TIMEOUT_S
    cap)."""
    start = time.monotonic()
    try:
        payload = json.loads(raw_payload) if raw_payload.strip() else {}
    except Exception:
        payload = {}
    tool_input = payload.get("tool_input", {}) if isinstance(payload, dict) else {}
    persona = str(
        tool_input.get("subagent_type") or tool_input.get("agent_type") or ""
    ).strip().lower()
    elapsed_ms = int((time.monotonic() - start) * 1000)
    if persona not in ("lens", "lens-fast"):
        return 0, "", "", elapsed_ms

    summary = str(
        tool_input.get("description") or tool_input.get("prompt") or tool_input.get("goal") or ""
    )[:500]
    try:
        mod = _load_module("_gate_runner_chk_lm_studio_triage", target)
        decision = mod.triage_route(summary)
        out = json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": f"[triage] route={decision['route']} verdict={decision['verdict']}",
            }
        })
        rc = 0
        err = ""
    except Exception as exc:  # noqa: BLE001 -- fail-open, this gate never blocks
        out, err, rc = "", f"[gate-runner] lm-studio-triage crashed (fail-open): {exc!r}", 0
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return rc, out, err, elapsed_ms


def _run_lane_router(name: str, target: str, raw_payload: str) -> tuple[int, str, str, int]:
    """R6-T06 / N35 adapter (plans/14 SS4): derive+classify+journal a
    lane-routing decision via router_core.route_dispatch(), which internally
    calls router_core.classify_lane() -- ADVISORY ONLY, this check never
    denies (the actual conductor/harness dispatch mechanism is untouched by
    a routing pick). Body-gated by the CALLER (run_event only appends this
    check to the chain when _lane_router_enabled() is true) -- this adapter
    performs no flag check itself, so it stays independently testable. Fails
    open on any error, mirroring _run_triage above."""
    start = time.monotonic()
    try:
        payload = json.loads(raw_payload) if raw_payload.strip() else {}
    except Exception:
        payload = {}
    try:
        mod = _load_module("_gate_runner_chk_lane_router", target)
        decision = mod.route_dispatch(payload)
        out = json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    f"[lane-router] lane={decision.get('lane')} "
                    f"shape_class={decision.get('shape_class', 'other')} "
                    f"confidence={decision.get('confidence', 0):.2f} "
                    f"fallback={decision.get('fallback')}"
                ),
            }
        })
        rc, err = 0, ""
    except Exception as exc:  # noqa: BLE001 -- fail-open, this check never blocks
        out, err, rc = "", f"[gate-runner] lane-router crashed (fail-open): {exc!r}", 0
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return rc, out, err, elapsed_ms


def _run_review_panel(name: str, target: str, raw_payload: str) -> tuple[int, str, str, int]:
    """R6-T07 / N38 adapter (plans/14 SS8): classify+journal a review-panel
    trigger decision via THIS module's own route_panel_decision() (INLINE,
    no importlib module load — unlike _run_lane_router's router_core.py
    import, N38's write_scope has no sibling module file) --
    ADVISORY ONLY, this check never denies (the actual panel dispatch
    mechanism, `Skill review-panel`'s roster + aggregation, is untouched by
    this call — DEC-036: the panel fires on named risk signals only). Body-
    gated by the CALLER (run_event only appends this check to the chain
    when _review_panel_enabled() is true) -- this adapter performs no flag
    check itself, so it stays independently testable. Fails open on any
    error, mirroring _run_lane_router above."""
    start = time.monotonic()
    try:
        payload = json.loads(raw_payload) if raw_payload.strip() else {}
    except Exception:
        payload = {}
    try:
        decision = route_panel_decision(payload)
        out = ""
        if decision.get("panel_required"):
            out = json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SubagentStop",
                    "additionalContext": (
                        f"[review-panel] panel_required=true "
                        f"end_only={decision.get('end_only')} "
                        f"per_leg={decision.get('per_leg')} "
                        f"reasons={','.join(decision.get('reasons', []))}"
                    ),
                }
            })
        rc, err = 0, ""
    except Exception as exc:  # noqa: BLE001 -- fail-open, this check never blocks
        out, err, rc = "", f"[gate-runner] review-panel crashed (fail-open): {exc!r}", 0
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return rc, out, err, elapsed_ms


def _run_check(name: str, target: str, kind: str, raw_payload: str) -> tuple[int, str, str, int]:
    if kind == "inprocess":
        return _run_inprocess(name, target, raw_payload)
    if kind == "triage":
        return _run_triage(name, target, raw_payload)
    if kind == "lane-router":
        return _run_lane_router(name, target, raw_payload)
    if kind == "review-panel":
        return _run_review_panel(name, target, raw_payload)
    return _run_subprocess(name, target, kind, raw_payload)


def _extract_advisory_context(out: str) -> list[str]:
    parts = []
    for line in out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ctx = obj.get("hookSpecificOutput", {}).get("additionalContext")
        if ctx:
            parts.append(ctx)
    return parts


def run_event(event_key: str, raw_payload: str) -> int:
    hook_event_name = HOOK_EVENT_NAMES.get(event_key, "PreToolUse")
    chain = list(EVENT_CHAINS.get(event_key, []))
    if event_key == "pretooluse-dispatch" and _lane_router_enabled():
        # R6-T06 / N35 (plans/14 SS4): APPENDED, never inserted mid-chain, so
        # every deny-capable check above still short-circuits first — this is
        # an advisory-only tail entry, same tier as dispatch-announce /
        # dispatch-capture / lm-studio-triage above it. Internally calls
        # router_core.classify_lane() via route_dispatch() — see
        # _run_lane_router's docstring.
        chain.append(("lane-router", "router_core.py", "lane-router", False))
    if event_key == "subagentstop" and _review_panel_enabled():
        # R6-T07 / N38 (plans/14 SS8): APPENDED, never inserted mid-chain, so
        # every deny-capable check above (no-deferral-gate, lens-gate,
        # plan-validation-gate) still short-circuits first — this is an
        # advisory-only tail entry, same tier as return-validator /
        # do-not-touch-guard above it. Calls THIS module's own
        # route_panel_decision() (inline) — see _run_review_panel's
        # docstring. `target` is unused by the "review-panel" kind (no
        # importlib load) but kept for tuple-schema consistency.
        chain.append(("review-panel", "gate_runner.py", "review-panel", False))
    advisory_parts: list[str] = []

    for name, target, kind, deny_capable in chain:
        rc, out, err, elapsed_ms = _run_check(name, target, kind, raw_payload)
        if name not in _SELF_EMITTING_CHECKS:
            _emit_heartbeat(name, hook_event_name, "block" if rc == 2 else "allow", elapsed_ms)

        if rc == 2 and deny_capable:
            # Short-circuit: this check's own captured output IS the final
            # decision, re-emitted VERBATIM (byte-for-byte — subprocess
            # checks are the unchanged file's own bytes; inprocess checks
            # are never deny_capable, so this branch never touches one).
            if out:
                sys.stdout.write(out)
            if err:
                sys.stderr.write(err)
            return 2

        if rc not in (0, 2):
            # Unexpected non-standard exit — fail OPEN (never let a runner
            # or check bug newly block a call that used to sail through),
            # loudly, and keep going.
            sys.stderr.write(f"[gate-runner] {name} exited {rc} unexpectedly (fail-open): {err}\n")
            continue

        if out.strip():
            advisory_parts.extend(_extract_advisory_context(out))

    if advisory_parts:
        combined = "\n".join(advisory_parts)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": hook_event_name,
                "additionalContext": combined,
            }
        }))
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in EVENT_CHAINS:
        sys.stderr.write(
            f"[gate-runner] usage: gate_runner.py <event>, one of {sorted(EVENT_CHAINS)}\n"
        )
        return 0  # fail open — never block a tool call over a misconfigured runner
    event_key = sys.argv[1]
    raw_payload = sys.stdin.read()
    return run_event(event_key, raw_payload)


if __name__ == "__main__":
    sys.exit(main())

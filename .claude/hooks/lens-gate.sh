#!/usr/bin/env python3
# SubagentStop hook: enforces Lens-before-done for implementing agents.
#
# Contract Rule 17: Forge / Pipeline / Hermes / Atlas returning NEXUS:DONE
# with files_changed touching source paths must have a Lens validation row
# in validation_log written within the last hour for the same task hash.
#
# S2-14 GROUND-TRUTH CROSS-CHECK: files_changed is the agent's SELF-REPORT and
# can omit (or docs-wash) real source changes — omitting it must not skip the
# Lens mandate. When a gated persona returns NEXUS:DONE and the self-report
# shows no gated paths, the gate ALSO consults git ground truth before
# skipping. Window heuristic — deliberately NARROW to bound false positives
# from orchestrator checkpoint commits that predate the agent return:
#   - uncommitted working-tree changes (git status --porcelain -uall: staged,
#     unstaged AND untracked, files listed individually), PLUS
#   - the single HEAD commit only (git diff --name-only HEAD~1..HEAD) — but
#     ONLY when the self-report is absent or unparseable (i.e. we cannot trust
#     files_changed at all). When the self-report IS present and docs-only, the
#     HEAD window is skipped: the agent plausibly only touched docs and the HEAD
#     commit is likely an unrelated checkpoint, not this task's work. This
#     prevents a false-block where a prior hooks-touching commit at HEAD causes
#     a subsequent docs-only NEXUS:DONE to be blocked (TASK-068).
#     Older history is always out of window.
# If ground truth shows gated-source changes, the Lens PASS row is required
# REGARDLESS of the self-report. Fail-soft: when git is unavailable or errors,
# the self-report remains the only signal (no block on git failure alone).
#
# 3.9 CONSTRAINT — the harness runs hooks under the system python3 (3.9.6 on
# macOS). No `X | None` runtime unions in signatures, no `datetime.UTC`, no
# match/case here.
#
# Returns exit 2 (block) or exit 0 (pass/skip).

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load _heartbeat from the same hooks directory. Best-effort only — see
# _heartbeat.py; this MUST NEVER change exit code/behavior of this gate.
try:
    _hb_path = Path(__file__).parent / "_heartbeat.py"
    _hb_spec = importlib.util.spec_from_file_location("_heartbeat", _hb_path)
    _heartbeat_mod = importlib.util.module_from_spec(_hb_spec)  # type: ignore[arg-type]
    _hb_spec.loader.exec_module(_heartbeat_mod)  # type: ignore[union-attr]
except Exception:
    _heartbeat_mod = None

# ADR-001 Phase 0: resolved relative to THIS file (never the env-overridable
# DB_PATH/GIT_ROOT below) so a test pointing _HOOK_DB_PATH at a scratch DB
# still invokes the REAL log.py — the single-writer connection, not a raw
# sqlite3.connect this hook no longer opens.
LOG_PY = Path(__file__).resolve().parents[2] / ".memory" / "log.py"


def _emit_heartbeat(event: str, decision: str, latency_ms: int) -> None:
    if _heartbeat_mod is None:
        return
    _heartbeat_mod.emit_heartbeat("lens-gate", event, decision, latency_ms)


_START_TIME = time.time()


def _elapsed_ms() -> int:
    try:
        return int((time.time() - _START_TIME) * 1000)
    except Exception:
        return 0


def _record_block(event: str, code: str, reason: str) -> None:
    """Append one JSONL row to the gate-block sink. BEST-EFFORT: swallows all errors.

    This package build of lens-gate.sh is self-contained (no _gate_deny.py
    import), so its own inline `print(...) + return 2` deny paths never wrote
    to the gate_blocks.jsonl sink. This mirrors _gate_deny.py's _record_block
    schema exactly ({ts, event, hook, code, reason}) so lens-gate deny events
    show up in the same telemetry stream as every other gate.
    """
    try:
        sink_path = os.environ.get("NEXUS_GATE_BLOCKS_PATH")
        if sink_path is None:
            repo_root = Path(__file__).resolve().parents[2]
            sink_path = str(repo_root / ".memory" / "files" / "gate_blocks.jsonl")
        sink = Path(sink_path)
        sink.parent.mkdir(parents=True, exist_ok=True)
        if "/" in code:
            hook, code_part = code.split("/", 1)
        else:
            hook, code_part = code, ""
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "event": event,
            "hook": hook,
            "code": code_part,
            "reason": reason[:200],
        }
        with open(sink, "a") as f:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        pass


DB_PATH = os.environ.get(
    "_HOOK_DB_PATH",
    "/Users/john.keeney/nexus-task-tracker/.memory/project.db",
)

# Repo the S2-14 ground-truth cross-check interrogates. Env seam mirrors
# _HOOK_DB_PATH so tests can point the check at a controlled temp repo. An
# unrendered token simply makes git fail -> fail-soft (self-report only).
GIT_ROOT = os.environ.get(
    "_HOOK_GIT_ROOT",
    "/Users/john.keeney/nexus-task-tracker",
)

# Code-writing personas the orchestrator dispatches: every persona that can emit
# source under a gated prefix must pass through Lens before NEXUS:DONE is
# accepted. Read-only personas (scout=investigate, lens=validate) and the
# design-only persona (palette → docs/design only) are deliberately excluded.
# Names mirror the nexus-broker registry DISPATCHABLE_PERSONAS keys; the --pro
# variants are Opus reworks of the same scope and gate identically. Membership is
# matched on the FULL persona name (not the base before '-') so the -pro and the
# sub-stack variants (forge-ui, pipeline-async, …) each gate on their own right.
GATED_AGENTS = frozenset({
    "forge-ui",
    "forge-wire",
    "forge-ui-pro",
    "forge-wire-pro",
    "pipeline-data",
    "pipeline-async",
    "pipeline-data-pro",
    "pipeline-async-pro",
    "atlas",
    "hermes",
    "quill-ts",
    "quill-py",
})

# Retired base personas (DEC-051): split into the variants above; kept here so a
# bare-name dispatch is still blocked (defense-in-depth). Do NOT add back to
# GATED_AGENTS — that frozenset is splits-only by design.
RETIRED_BASE_PERSONAS = frozenset({"forge", "pipeline", "quill"})

# Source paths that trigger the gate when listed in files_changed.
# Derived from the project's stack profile (socraticode_watched_prefixes — the
# source dirs implementers write to). Rendered by render_template from the
# /app/apps/, /app/packages/ token (same construct as socraticode-gate.sh, CL-21).
# _HOOK_WATCHED_PREFIXES is the same test-only override seam socraticode-gate.sh
# already exposes (_HOOK_WATCHED_PREFIXES) — added here so tests can point this
# gate at a non-default prefix list without depending on install-time rendering.
# The profile prefixes carry a leading slash (e.g. "/apps/web/src/"); strip it
# so they match _touches_source's normalization (which lstrips "./").
# Fallback (unrendered token / empty): the canonical AI-stack source dirs, so a
# raw, un-rendered hook still gates rather than silently failing open.
_RENDERED_WATCHED = os.environ.get("_HOOK_WATCHED_PREFIXES", "/app/apps/, /app/packages/")
_FALLBACK_PREFIXES = ("app/", "ingestion/src/", "models/", "design/", "app/components/")
if _RENDERED_WATCHED == "__" "WATCHED_PREFIXES__":
    GATED_PATH_PREFIXES = _FALLBACK_PREFIXES
else:
    GATED_PATH_PREFIXES = tuple(
        p.strip().lstrip("/") for p in _RENDERED_WATCHED.split(",") if p.strip()
    ) or _FALLBACK_PREFIXES

MARKER_RE = re.compile(
    r"##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)", re.IGNORECASE
)

VALIDATION_WINDOW = timedelta(hours=1)

# REVISE-detail floor: a verifier (lens/lens-fast) returning NEXUS:REVISE MUST
# include a "Failing criterion:" line so the implementer has a machine-readable
# anchor to fix.  Non-verifier REVISE passes freely (unaffected).
VERIFIER_PERSONAS = frozenset({"lens", "lens-fast"})

FAILING_CRITERION_RE = re.compile(
    r"^\s*Failing criterion\s*:\s*\S",
    re.IGNORECASE | re.MULTILINE,
)

# Content-probe: presence of any of these tokens in the diff/text forces T2.
SUBPROCESS_PROBE_RE = re.compile(
    r"subprocess|eval|exec|os\.system|socket|requests|urllib|http|curl",
    re.IGNORECASE,
)


def _classify_lens_tier(files_changed: list[str], assistant_text: str) -> str:
    """Return 'T1' (trivial/light) or 'T2' (risky/full-audit).

    T2 iff ANY of:
      (a) files_changed has >1 distinct path (multi-file)
      (b) any path starts with a GATED_PATH_PREFIX (after leading-dot-safe strip)
      (c) content-probe hit (SUBPROCESS_PROBE_RE) in assistant_text
      (d) ambiguity — files_changed empty/unparseable (default-deny)
    T1 iff ALL: exactly one file AND non-gated prefix AND no content-probe hit.
    """
    if len(files_changed) > 1:
        return "T2"
    if len(files_changed) == 1:
        f = files_changed[0]
        norm = f[2:] if f.startswith("./") else (f[1:] if f.startswith("/") else f)
        for prefix in GATED_PATH_PREFIXES:
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return "T2"
    if SUBPROCESS_PROBE_RE.search(assistant_text):
        return "T2"
    if not files_changed:
        return "T2"
    return "T1"


def _check_lens_gate(target_agent, task_hash, tier):
    """ADR-001 Phase 0: shell to `log.py validation check-gate` instead of
    opening a raw sqlite3.connect here — the same v1-floor + v2-tier-distinct
    query, now run through the single-writer connection so this hook can no
    longer race a concurrent hook's schema-init DDL (there is none left here
    to race with).

    Returns the parsed JSON dict, or None on ANY failure to invoke/parse
    (missing log.py, subprocess error, non-JSON stdout) — the caller treats
    None identically to the old sqlite3.Error branch: fail CLOSED, never a
    silent pass.
    """
    if not LOG_PY.is_file():
        return None
    cmd = [
        sys.executable, str(LOG_PY), "validation", "check-gate",
        "--target", target_agent, "--task-hash", task_hash, "--tier", tier,
    ]
    env = dict(os.environ)
    env["NEXUS_DB_PATH"] = DB_PATH
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    except Exception:
        return None
    try:
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_files_changed(text: str) -> list[str]:
    """Extract files_changed list from the first JSON block in the agent response."""
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        fc = obj.get("files_changed")
        if isinstance(fc, list) and all(isinstance(x, str) for x in fc):
            return fc
    return []


def _touches_source(files: list[str]) -> bool:
    """Return True if any path in files falls under a gated source directory."""
    for f in files:
        # Normalise: strip leading ./ or /
        norm = f.lstrip("./")
        for prefix in GATED_PATH_PREFIXES:
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return True
    return False


def _git_gated_changes(include_head_commit):
    """S2-14 ground truth: gated paths git says actually changed, or None.

    Window (see header): always includes uncommitted working-tree changes.
    The HEAD~1..HEAD half is included only when `include_head_commit` is True
    (i.e. when the self-report is absent/unparseable and we cannot trust
    files_changed at all). When the self-report is present-and-docs-only, the
    HEAD commit is excluded to avoid false-blocks from unrelated checkpoint
    commits (TASK-068).

    Returns the gated subset (possibly empty = clean), or None when git is
    unavailable/errors (fail-soft — self-report stays the signal).
    """
    paths = set()
    base = ["git", "-C", GIT_ROOT]
    try:
        # -uall: list untracked FILES individually — without it git collapses
        # a new directory to "?? dir/", which would never match a gated prefix
        # and the brand-new-file case would slip through.
        st = subprocess.run(
            base + ["status", "--porcelain", "-uall"],
            capture_output=True, text=True, timeout=10,
        )
        if st.returncode != 0:
            return None
        for line in st.stdout.splitlines():
            p = line[3:].strip()
            if " -> " in p:  # rename entry: "R  old -> new"
                p = p.split(" -> ", 1)[1].strip()
            if p:
                paths.add(p.strip('"'))
    except Exception:
        return None
    if include_head_commit:
        try:
            head = subprocess.run(
                base + ["diff", "--name-only", "HEAD~1..HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if head.returncode == 0:
                paths.update(ln.strip() for ln in head.stdout.splitlines() if ln.strip())
            # rc!=0 (e.g. single-commit repo: no HEAD~1) — working tree alone suffices.
        except Exception:
            pass
    return sorted(p for p in paths if _touches_source([p]))


# N22 (plans/13 item 3.2 hook half; plans/08 3.2): per-Workflow ownership
# consumption of the N21 daemon store (nexus-broker/src/broker/daemon/
# ownership.py) — narrows the S2-14 whole-tree git ground truth above to
# PER-WORKFLOW attribution, closing NATIVE-14 / NATIVE-4-2 structurally (a
# concurrent Workflow's unrelated dirty gated files no longer blame the
# finishing agent). Reimplements the daemon's socket-path derivation
# (broker.daemon.paths.socket_path_for) and newline-delimited-JSON wire
# protocol (broker.daemon.client._rpc) directly rather than importing
# broker.daemon.client — a target install may not ship nexus-broker/ at
# all, and this file must stay import-safe under the ambient system python3
# (3.9 CONSTRAINT above: no `X | None` runtime unions here either). Same
# env-var names as the real daemon client (NEXUS_DAEMON_SOCKET_DIR /
# NEXUS_DAEMON_CONNECT_TIMEOUT_S) so an operator/test override applies
# identically at both layers.
#
# FAIL-CLOSED CONTRACT: `_ownership_owners_of` returns None on ANY failure
# (no socket file, connection refused, timeout, malformed/error response) —
# None means "cannot attribute" and NEVER exempts a path; only a populated
# owners list naming a Workflow OTHER than this one does. A daemon that is
# simply down therefore leaves every git-gated path exactly as attributed
# as it was before this node — the whole-tree path, byte-for-byte (the
# daemon-down acceptance criterion).
NEXUS_DAEMON_SOCKET_DIR_ENV = "NEXUS_DAEMON_SOCKET_DIR"
NEXUS_DAEMON_CONNECT_TIMEOUT_ENV = "NEXUS_DAEMON_CONNECT_TIMEOUT_S"

# Identifies the Workflow this SubagentStop belongs to, so a file the daemon
# attributes to the finishing agent's OWN Workflow is never wrongly exempted.
# No orchestrator wiring sets this yet — N22 is the consumer half only; the
# dispatch-side register()/record_touch() caller that would tag this is
# future scope. Unset is the expected production value today; the filter
# below only ever EXEMPTS a path on POSITIVE evidence of a DIFFERENT tracked
# owner, so an unset id degrades safely rather than over-exempting.
FINISHING_WORKFLOW_ID = os.environ.get("_HOOK_WORKFLOW_ID") or None


def _ownership_socket_path(project_path):
    """Mirror broker.daemon.paths.socket_path_for() exactly: one per-project
    Unix socket, path derived from sha256(resolved project path)[:16]."""
    override = os.environ.get(NEXUS_DAEMON_SOCKET_DIR_ENV)
    base = Path(override) if override else Path.home() / ".nexus" / "daemon"
    resolved = str(Path(project_path).resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
    return base / (digest + ".sock")


def _ownership_owners_of(file_path):
    """RPC 'ownership_owners_of' against the N21 daemon store. Returns the
    owning workflow_id list on success (possibly empty — a real "no owner
    recorded" answer), or None on ANY failure — see the FAIL-CLOSED CONTRACT
    note above. Never spawns a daemon: a gate hook forking a background
    process on every SubagentStop is new, unbounded scope this node does
    not authorize — a missing daemon is simply treated as unreachable.
    """
    try:
        sock_path = _ownership_socket_path(GIT_ROOT)
        if not sock_path.exists():
            return None
        timeout = float(os.environ.get(NEXUS_DAEMON_CONNECT_TIMEOUT_ENV) or 2.0)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(sock_path))
            request = {"id": 1, "method": "ownership_owners_of", "params": {"file_path": file_path}}
            sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        finally:
            sock.close()
        if not buf:
            return None
        response = json.loads(buf.decode("utf-8"))
        if not isinstance(response, dict) or "error" in response:
            return None
        result = response.get("result")
        if not isinstance(result, dict):
            return None
        owners = result.get("owners")
        if not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
            return None
        return owners
    except Exception:
        return None


def _filter_foreign_owned(paths):
    """Drop paths the daemon POSITIVELY attributes to a different, still-
    tracked Workflow than this SubagentStop's own — the NATIVE-14 /
    NATIVE-4-2 structural fix (plans/13 item 3.2). Every other outcome
    (daemon unreachable for a path, no owner recorded, or the owner IS this
    Workflow) keeps the path attributed — the same, today-identical
    conservative default. Order is preserved so a fully-unreachable daemon
    returns the input list unchanged (the byte-identical fail-closed
    acceptance criterion).
    """
    kept = []
    for p in paths:
        owners = _ownership_owners_of(p)
        if owners and FINISHING_WORKFLOW_ID not in owners:
            continue
        kept.append(p)
    return kept


def _warn_extract_miss(payload: dict) -> None:
    """EXTRACT_OK canary (S1-22): valid SubagentStop JSON yielded NO assistant text.

    Harness schema drift (renamed payload keys) would silently disarm this gate —
    every return would look empty and exit 0 forever. Warn LOUDLY instead of
    staying silent (still exit 0: warn, not block). Once per session via a flag
    file keyed on session_id so repeat returns do not spam the orchestrator.
    """
    if not isinstance(payload, dict) or not payload:
        return
    import contextlib
    import tempfile
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "unknown"))[:64]
    flag = os.path.join(tempfile.gettempdir(), ".nexus-extract-miss-lens-gate-" + sid)
    if os.path.exists(flag):
        return
    with contextlib.suppress(OSError):
        open(flag, "w").close()
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": (
                "[lens-gate] EXTRACT-MISS: SubagentStop payload had no extractable "
                "assistant text — possible harness schema drift"
            ),
        }
    }))


def _derive_task_hash(payload: dict, assistant_text: str) -> str:
    """Produce a stable hash that Lens can reproduce when it calls `validation add`.

    Priority: explicit task_id > task_description > brief hash from assistant text.
    Nexus embeds task_id in the delegation payload when it exists.
    """
    task_id: str = (
        payload.get("task_id")
        or payload.get("tool_input", {}).get("task_id")
        or ""
    )
    task_desc: str = (
        payload.get("task_description")
        or payload.get("tool_input", {}).get("description")
        or os.environ.get("CLAUDE_TASK_DESCRIPTION", "")
        or ""
    )
    raw = task_id or task_desc or assistant_text[:500]
    return hashlib.sha256(raw.encode()).hexdigest()[:16]




def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    if not assistant_text:
        _warn_extract_miss(payload)
        return 0

    marker_match = MARKER_RE.search(assistant_text)
    if not marker_match:
        return 0

    marker = marker_match.group(1).upper()

    # Extract agent_name early — needed for both the REVISE floor and the DONE gate.
    #
    # NATIVE-4: agent_type / tool_input.agent_type added to the fallback chain.
    # This harness dispatches via the Agent tool, which carries the persona
    # under subagent_type for Task-shaped dispatches but under agent_type for
    # Agent/Team-shaped dispatches (see return-validator.py's _extract()) — a
    # SubagentStop payload for an Agent-tool dispatch was falling through all
    # three subagent_type-flavoured keys straight to "unknown", which this gate
    # then silently exempted (agent_name not in GATED_AGENTS -> return 0).
    _tool_input = payload.get("tool_input", {})
    if not isinstance(_tool_input, dict):
        _tool_input = {}
    agent_name: str = (
        payload.get("agent_persona")
        or payload.get("subagent_type")
        or payload.get("agent_type")
        or _tool_input.get("subagent_type")
        or _tool_input.get("agent_type")
        or "unknown"
    ).lower()

    # REVISE-detail floor: a verifier returning NEXUS:REVISE without a
    # "Failing criterion: <text>" line gives the implementer nothing to fix.
    # Block so the verifier is forced to add specifics before the implementer
    # can act. Non-verifier REVISE (any other persona) passes freely — exit 0.
    if marker == "REVISE":
        if agent_name in VERIFIER_PERSONAS:
            if not FAILING_CRITERION_RE.search(assistant_text):
                _code = "LENS/REVISE-NO-CRITERION"
                _reason = (
                    f"[GATE:{_code}] [lens-gate] BLOCK — {agent_name} NEXUS:REVISE is missing a "
                    "'Failing criterion: <text>' line (ORCHESTRATOR-GATES.md §REVISE-floor). "
                    "Add at least one 'Failing criterion: ...' line so the implementer "
                    "has a machine-readable anchor to fix, then re-emit NEXUS:REVISE."
                )
                print(_reason, file=sys.stderr)
                _record_block("SubagentStop", _code, _reason)
                return 2
        return 0

    if marker != "DONE":
        # Only NEXUS:DONE triggers the Lens-validation gate below.
        # BLOCKED/CHECKPOINT/NEEDS-DECISION pass freely.
        return 0

    if agent_name in RETIRED_BASE_PERSONAS:
        # Retired base name: block immediately — dispatch should have used the split variant.
        _code = "LENS/RETIRED-BASE-PERSONA"
        _reason = (
            f"[GATE:{_code}] [lens-gate] BLOCK — '{agent_name}' is a retired base persona (DEC-051). "
            f"Dispatch the split variant instead (e.g. forge-ui/forge-wire, "
            f"pipeline-data/pipeline-async). See .claude/agents/{agent_name}.md."
        )
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        return 2

    if agent_name not in GATED_AGENTS:
        return 0

    files_changed = _parse_files_changed(assistant_text)
    self_report_gated = _touches_source(files_changed)
    git_gated = None
    if not self_report_gated:
        # S2-14: the self-report alone says "gate does not apply" — do NOT
        # trust it. An absent/unparseable files_changed, or one listing only
        # docs paths, must not skip the Lens mandate when git ground truth
        # shows real gated-source changes.
        #
        # TASK-068: include the HEAD~1..HEAD window ONLY when the self-report
        # is absent/unparseable (files_changed is empty because parsing failed,
        # not because the agent listed docs). When files_changed is present and
        # docs-only, scope git to uncommitted changes only — the HEAD commit is
        # plausibly an unrelated checkpoint and including it causes false-blocks.
        self_report_present_docs_only = bool(files_changed)  # non-empty list, no gated paths
        git_gated = _git_gated_changes(not self_report_present_docs_only)

        # N22: narrow whole-tree git ground truth to per-Workflow attribution
        # before it is used as a signal below — a path the daemon positively
        # attributes to a DIFFERENT tracked Workflow is dropped here, so it
        # never reaches the "not self_report_gated and not git_gated" check
        # or the Lens-validation gate that follows (see
        # _filter_foreign_owned's fail-closed contract above). A fully-
        # unreachable daemon is a no-op: every path falls through
        # _ownership_owners_of->None->kept, so git_gated comes back
        # byte-identical to what _git_gated_changes produced.
        if git_gated:
            _owner_filtered = _filter_foreign_owned(git_gated)
            if _owner_filtered != git_gated:
                print(
                    "[lens-gate] INFO — per-Workflow ownership (N21 daemon "
                    "store) exempted "
                    f"{sorted(set(git_gated) - set(_owner_filtered))} from "
                    "this agent's attribution (daemon shows a different "
                    "tracked Workflow owns it).",
                    file=sys.stderr,
                )
            git_gated = _owner_filtered

    if not self_report_gated and not git_gated:
        # No gated source change — confirmed by self-report AND git ground
        # truth (or git unavailable: fail-soft). Gate does not apply.
        return 0

    gt_note = ""
    if git_gated and not self_report_gated:
        gt_note = (
            "  Ground truth (git) shows gated-source changes the self-report "
            f"omitted: {git_gated[:5]}\n"
        )

    task_hash = _derive_task_hash(payload, assistant_text)
    tier = _classify_lens_tier(files_changed, assistant_text)

    # ADR-001 Phase 0: v1 floor (structural backstop — NEVER removed) + v2
    # tier-distinct strengthening (R1-T08), both now evaluated by log.py's
    # single-writer connection (`_check_lens_gate` shells to `validation
    # check-gate`) instead of this hook opening its own raw sqlite3.connect +
    # schema-init DDL. The 3x100ms retry is kept (CL-12) — a transient
    # subprocess/lock hiccup still gets the same retry budget it had before.
    validated: "bool | None" = None
    v2_ok, v2_detail = True, "tier=T1 — v1 floor only (unchanged)"
    last_err = None
    for attempt in range(3):
        check = _check_lens_gate(agent_name, task_hash, tier)
        if check is not None and not check.get("db_error"):
            validated = bool(check.get("validated"))
            v2_ok = bool(check.get("v2_ok", True))
            v2_detail = check.get("v2_detail") or v2_detail
            break
        last_err = (check or {}).get("db_error") or "log.py validation check-gate did not return"
        if attempt < 2:
            time.sleep(0.1)

    if validated is None:
        # FAIL-CLOSED: the DB could not be read after 3 attempts. Rule 17 cannot
        # be verified, so we must BLOCK rather than silently allow unvalidated work.
        _code = "LENS/DB-ERROR"
        _reason = (
            f"[GATE:{_code}] [lens-gate] BLOCK — project memory DB is unavailable; Lens validation "
            "(CONTRACT.md Rule 17) could not be verified.\n"
            f"  DB path: {DB_PATH}\n"
            f"  Error after 3 retries: {last_err}\n"
            f"{gt_note}"
            "  Recover: confirm .memory/project.db exists and is a readable SQLite file "
            "(not a directory or locked by another process), then re-dispatch. "
            "Run `python3 .memory/log.py init` if the DB is missing."
        )
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        return 2

    if not validated:
        _code = "LENS/NO-VALIDATION"
        _reason = (
            f"[GATE:{_code}] [lens-gate] BLOCK — {agent_name.capitalize()} NEXUS:DONE requires Lens "
            "validation first (CONTRACT.md Rule 17). Dispatch Lens before re-claiming done.\n"
            f"  Agent: {agent_name}\n"
            f"  Task hash: {task_hash}\n"
            f"  Lens tier: {tier} ({'light — single non-gated file' if tier == 'T1' else 'full deep audit — multi-file or gated prefix or content probe'})\n"
            f"  Files changed (source): {[f for f in files_changed if _touches_source([f])][:5]}\n"
            f"{gt_note}"
            "  Lens must run: python3 .memory/log.py validation add "
            f"--agent lens --target {agent_name} --task-hash {task_hash} "
            "--verdict PASS|PARTIAL|FAIL --summary \"...\""
        )
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        return 2

    if not v2_ok:
        # v1 floor passed (>=1 PASS row exists) but v2 distinct-tier coverage
        # does not — the only in-window PASS row(s) are at the wrong tier (or
        # NULL/pre-migration) for this T2 dispatch.
        _code = "LENS/TIER-MISMATCH"
        _reason = (
            f"[GATE:{_code}] [lens-gate] BLOCK — {agent_name.capitalize()} NEXUS:DONE requires a Lens "
            "validation row at the SPECIFIC tier this dispatch requires (R1-T08 "
            "N-distinct-lens-row rule). A PASS row exists but not at the required "
            "tier — a stale or lower-tier row cannot satisfy a T2 (full audit) requirement.\n"
            f"  Agent: {agent_name}\n"
            f"  Task hash: {task_hash}\n"
            f"  {v2_detail}\n"
            f"{gt_note}"
            "  Lens must run (with --lens-type matching the required tier): "
            "python3 .memory/log.py validation add "
            f"--agent lens --target {agent_name} --task-hash {task_hash} "
            "--verdict PASS --lens-type T2 --risk-tier T2 --summary \"...\""
        )
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        return 2

    # Validated at both the v1 floor and (for T2) the v2 distinct-tier check —
    # emit advisory tier note so orchestrator knows which depth was expected.
    print(
        f"[lens-gate] INFO — Lens tier for this change: {tier} "
        f"({'light' if tier == 'T1' else 'full deep audit'}). "
        "agent_validated='lens' row accepted (PASS)."
        + ("" if tier != "T2" else f" v2 distinct-tier check: {v2_detail}"),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    # main() returns an int (0/2), never raising SystemExit itself — capture
    # it here so heartbeat covers every one of main()'s early-return exit
    # paths (deny/warn/allow) without touching its internal control flow.
    _rc = main()
    _emit_heartbeat("SubagentStop", "block" if _rc == 2 else "allow", _elapsed_ms())
    sys.exit(_rc)

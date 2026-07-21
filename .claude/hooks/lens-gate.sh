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

# Load _envelope_shadow from the same hooks directory. F1-08 CUTOVER
# (nexus-foundation/plans/wave-1.md track (c)): schema-parse via this
# module's resolve_marker() is now AUTHORITATIVE for this gate's marker
# resolution — MARKER_RE below is demoted to the single legacy-fallback
# branch (used only when no valid envelope is found). Rollback (kept 1
# release): env NEXUS_REGEX_AUTHORITY=1 restores regex-first ordering,
# reproducing the pre-cutover F1-07 behavior exactly. Best-effort import,
# same discipline as _heartbeat above — a failed import degrades to
# regex-only (never changes this gate's exit code / DENY reasoning beyond
# that degrade).
try:
    _es_path = Path(__file__).parent / "_envelope_shadow.py"
    _es_spec = importlib.util.spec_from_file_location("_envelope_shadow", _es_path)
    _envelope_shadow_mod = importlib.util.module_from_spec(_es_spec)  # type: ignore[arg-type]
    _es_spec.loader.exec_module(_envelope_shadow_mod)  # type: ignore[union-attr]
except Exception:
    _envelope_shadow_mod = None


def _resolve_marker(text: str, legacy_marker):
    """F1-08 AUTHORITATIVE marker resolution — see _envelope_shadow.py's
    resolve_marker() docstring. Degrades to `legacy_marker` outright if the
    shadow module failed to import (mirrors the prior _shadow_compare's
    fail-open discipline) — this call must never prevent the gate from
    reaching a verdict, whichever source ultimately wins."""
    if _envelope_shadow_mod is None:
        return legacy_marker
    try:
        return _envelope_shadow_mod.resolve_marker(
            hook="lens-gate", raw_text=text, legacy_regex_marker=legacy_marker
        )
    except Exception:
        return legacy_marker

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


# TASK-094 LEG B — gate-span emission + daemon RPC-miss/latency tracking. This
# package build is self-contained (no _gate_deny.py import — see _record_block
# above), so emit_gate_span below is its OWN inline mirror of
# _gate_deny.py's emit_gate_span (same span shape: gate_name/event/verdict/
# reason + the gate_fire attrs lens_verdict/lens_tier/revise_reasons/
# rpc_miss/rpc_latency_ms — spans.py's validate_gate_attributes), calling the
# package's own `_daemon_rpc.py` twin (dynamic same-dir import, same pattern
# as `_heartbeat_mod`/`_envelope_shadow_mod` above) directly rather than
# routing through a `_gate_deny_mod` this file deliberately does not import.
try:
    _dr_path = Path(__file__).parent / "_daemon_rpc.py"
    _dr_spec = importlib.util.spec_from_file_location("_daemon_rpc", _dr_path)
    _daemon_rpc_mod = importlib.util.module_from_spec(_dr_spec)  # type: ignore[arg-type]
    _dr_spec.loader.exec_module(_daemon_rpc_mod)  # type: ignore[union-attr]
except Exception:
    _daemon_rpc_mod = None

_GATE_SPAN_ATTR_KEYS = (
    "lens_verdict", "lens_tier", "revise_reasons", "rpc_miss", "rpc_latency_ms",
)
_GATE_SPAN_TOP_LEVEL_KEYS = ("task_id", "workflow_id", "phase_id")


def emit_gate_span(event, code, verdict, reason, span_attrs=None):
    """Best-effort `gate`-kind span emission. NO-OP (no RPC attempt, no
    `_daemon_rpc_mod` load even attempted beyond the try/except above) when
    `span_attrs` is falsy or carries no resolvable `trace_id`/`session_id` —
    a gate span's `trace_id` is REQUIRED (spans.validate_span). ANY failure
    (daemon down, malformed reply, missing `_daemon_rpc_mod`) is swallowed —
    this must never affect this gate's own exit code / stdout / stderr.
    """
    if not span_attrs or _daemon_rpc_mod is None:
        return
    trace_id = span_attrs.get("trace_id") or span_attrs.get("session_id")
    if not trace_id:
        return
    try:
        import uuid

        hook = code.split("/", 1)[0] if "/" in code else code
        attributes = {
            "gate_name": hook,
            "event": event,
            "verdict": verdict,
            "reason": str(reason)[:500],
        }
        for key in _GATE_SPAN_ATTR_KEYS:
            value = span_attrs.get(key)
            if value is not None:
                attributes[key] = value
        span = {
            "trace_id": str(trace_id),
            "span_id": "gate-" + uuid.uuid4().hex,
            "name": "gate:" + hook,
            "kind": "gate",
            "status": "ERROR" if verdict == "deny" else "OK",
            "attributes": attributes,
        }
        for key in _GATE_SPAN_TOP_LEVEL_KEYS:
            value = span_attrs.get(key)
            if value:
                span[key] = str(value)
        timeout = float(os.environ.get("NEXUS_GATE_SPAN_TIMEOUT_S", "0.2"))
        _daemon_rpc_mod.call(Path(GIT_ROOT), "span.emit", {"span": span}, timeout)
    except Exception:
        pass


def _lens_span_attrs(session_id, lens_verdict, lens_tier=None, revise_reasons=None):
    """Build `emit_gate_span`'s `span_attrs` for a `gate`-kind lens span.
    Returns None (no span attempted) when `session_id` never resolved —
    mirrors `emit_gate_span`'s own no-op-without-trace_id contract one level
    up so every call site here can pass the result straight through
    unconditionally."""
    if not session_id:
        return None
    attrs = {"trace_id": session_id, "lens_verdict": lens_verdict}
    if lens_tier:
        attrs["lens_tier"] = lens_tier
    if revise_reasons:
        attrs["revise_reasons"] = revise_reasons
    if _RPC_STATE["attempted"]:
        attrs["rpc_miss"] = _RPC_STATE["miss"]
        if _RPC_STATE["latency_ms"] is not None:
            attrs["rpc_latency_ms"] = _RPC_STATE["latency_ms"]
    return attrs


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
# Names mirror the nexus-broker registry DISPATCHABLE_PERSONAS keys. The four
# `-pro` escalation names (forge-ui-pro/forge-wire-pro/pipeline-data-pro/
# pipeline-async-pro) are RETIRED dispatch names (R2-T03 FIX-4) — escalation is
# now a tier=pro parameter on the base persona, never a distinct name, and
# persona-alias-resolver.sh redirects any stale -pro dispatch before this gate
# ever sees it. They are intentionally absent from this roster. Membership is
# matched on the FULL persona name (not the base before '-') so each sub-stack
# variant (forge-ui, pipeline-async, …) gates on its own right.
GATED_AGENTS = frozenset({
    "forge-ui",
    "forge-wire",
    "pipeline-data",
    "pipeline-async",
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

# F1-08: the single legacy-fallback marker regex (schema-parse via
# _resolve_marker above is now authoritative); used only when no valid typed
# envelope is found in the return.
MARKER_RE = re.compile(
    r"^\s*##\s+NEXUS:(DONE|REVISE|BLOCKED|CHECKPOINT|NEEDS-DECISION)",
    re.IGNORECASE | re.MULTILINE,
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

# TASK-094 LEG B — REVISE-reason capture: the FULL text of each "Failing
# criterion: ..." line (FAILING_CRITERION_RE above only confirms PRESENCE),
# for the gate_fire.revise_reasons span attribute.
FAILING_CRITERION_TEXT_RE = re.compile(
    r"^\s*Failing criterion\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def _revise_reasons(text):
    return [m.strip() for m in FAILING_CRITERION_TEXT_RE.findall(text) if m.strip()]


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

    Returns (gated, head_only_gated), or None when git is unavailable/errors
    (fail-soft — self-report stays the signal). `head_only_gated` is the
    subset of `gated` attributable ONLY to the HEAD commit — not also present
    in the uncommitted working tree. A committed HEAD may belong to an
    entirely different actor (another leg's checkpoint, a pre-existing
    commit) and the caller must not fabricate an omission finding from it
    alone (#281) — only the working-tree portion is directly attributable to
    the agent finishing right now.
    """
    wt_paths = set()
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
                wt_paths.add(p.strip('"'))
    except Exception:
        return None
    head_paths = set()
    if include_head_commit:
        try:
            head = subprocess.run(
                base + ["diff", "--name-only", "HEAD~1..HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if head.returncode == 0:
                head_paths.update(ln.strip() for ln in head.stdout.splitlines() if ln.strip())
            # rc!=0 (e.g. single-commit repo: no HEAD~1) — working tree alone suffices.
        except Exception:
            pass
    gated = sorted(p for p in (wt_paths | head_paths) if _touches_source([p]))
    head_only_gated = sorted(p for p in (head_paths - wt_paths) if _touches_source([p]))
    return gated, head_only_gated


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

# TASK-094 LEG B — daemon RPC-miss observability for THIS gate's own
# ownership RPC (gate_fire.rpc_miss/rpc_latency_ms, spans.py's
# validate_gate_attributes). Updated by `_ownership_owners_of` below; read by
# `_lens_span_attrs`. "attempted" stays False (attrs omitted, never
# fabricated) when this gate never had a reason to call
# `_ownership_owners_of` at all.
_RPC_STATE = {"attempted": False, "miss": False, "latency_ms": None}


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
        _RPC_STATE["attempted"] = True
        sock_path = _ownership_socket_path(GIT_ROOT)
        if not sock_path.exists():
            _RPC_STATE["miss"] = True
            return None
        timeout = float(os.environ.get(NEXUS_DAEMON_CONNECT_TIMEOUT_ENV) or 2.0)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        _t0 = time.time()
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
            _RPC_STATE["miss"] = True
            return None
        response = json.loads(buf.decode("utf-8"))
        if not isinstance(response, dict) or "error" in response:
            _RPC_STATE["miss"] = True
            return None
        result = response.get("result")
        if not isinstance(result, dict):
            _RPC_STATE["miss"] = True
            return None
        owners = result.get("owners")
        if not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
            _RPC_STATE["miss"] = True
            return None
        _RPC_STATE["latency_ms"] = (time.time() - _t0) * 1000.0
        return owners
    except Exception:
        _RPC_STATE["miss"] = True
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


def _derive_task_hash(payload: dict, agent_name: str) -> str:
    """Produce a stable hash that Lens can reproduce when it calls `validation add`.

    Priority: explicit task_id > task_description > dispatch identity
    (agent_name, session_id).

    WHY (NATIVE-12-3 / NATIVE-6-11 root cause + fix): task_id/task_description
    are only populated when the orchestrator threads them through the brief,
    which is frequently not the case — the REMOVED third fallback then hashed
    `assistant_text[:500]`, i.e. the LEAF AGENT'S OWN RESPONSE. A SubagentStop
    hook returning exit 2 (block) does not terminate the sub-agent: the
    harness feeds the deny reason back into the SAME ongoing sub-agent turn,
    which continues and produces a NEW final message. That rotated
    assistant_text — and the hash derived from it — on every retry, so a Lens
    PASS row written against attempt N's hash could never satisfy attempt
    N+1's re-derived hash, trapping the agent in a REVISE loop with no
    reachable exit and breaking validation_log dedup (keyed on task_hash).

    `(agent_name, session_id)` is the strongest identity this payload
    actually carries that stays CONSTANT across those forced retries:
    session_id identifies THIS sub-agent's own dispatch/turn — the same
    value on every SubagentStop firing within one Task/Agent invocation,
    including harness-forced continuations — and changes only when the
    orchestrator starts a genuinely NEW dispatch. agent_name is folded in
    defensively so two personas that ever shared a session id would still
    hash apart; this mirrors the (session_id, persona) pairing
    dispatch-capture.py / completion-capture.py already use to correlate a
    dispatch with its completion. The gate's own deny text prints the
    resulting hash for the orchestrator to hand to Lens via `--task-hash` —
    reproducibility does not require Lens to independently rediscover the
    value, only that it stay stable across an agent's own retries, which
    this fallback now guarantees (unlike the removed text-derived one).
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
    if task_id or task_desc:
        raw = task_id or task_desc
    else:
        session_id = str(payload.get("session_id") or "unknown-session")
        raw = f"dispatch:{agent_name}:{session_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# TASK-085 — lens-gate Stop-hook loop fix (fire-once-per-task_hash).
#
# RCA: a SubagentStop block does NOT terminate the sub-agent — the harness
# feeds the deny reason back into the SAME ongoing turn, which continues and
# produces a NEW final message (see _derive_task_hash's own docstring
# above). task_hash is deliberately STABLE across those retries (keyed off
# (agent_name, session_id), not the rotating response text), so an
# unresolved deny (no Lens row exists yet) re-fires byte-identically on
# every retry. A GATED_AGENTS leaf cannot dispatch Lens or write its own
# validation row (Task/Agent disallowed, separate-judge principle) — only
# the orchestrator can resolve it, and the orchestrator never regains
# control while this hook keeps re-blocking the same leaf turn. The loop
# cannot self-terminate and the leaf's real return envelope (files_changed,
# verification_result, marker) never reaches the orchestrator.
#
# FIX: the FIRST time a given (task_hash, deny code) pair fires, BLOCK
# exactly as before — the DEC-029 floor is untouched. Every REPEAT of that
# SAME pair on the SAME stuck turn allows the stop through with a WARN
# instead, so the envelope surfaces. A FRESH task_hash has no state file yet
# and blocks exactly like a first occurrence — the floor never weakens for
# different work.
#
# STATE: one flag file per (code, task_hash) — hooks run as separate
# processes per SubagentStop firing, so only a marker persisted to disk lets
# one firing know a PRIOR firing already blocked this exact pair. Mirrors
# this file's own _warn_extract_miss flag-file idiom above (tempdir,
# exists() gates a touch()) and its _HOOK_*-env-seam test-isolation
# convention (_HOOK_DB_PATH / _HOOK_GIT_ROOT / _HOOK_WATCHED_PREFIXES).
_LENS_GATE_STATE_DIR = os.environ.get("_HOOK_LENS_GATE_STATE_DIR")


def _fire_once_state_path(code, task_hash):
    """On-disk marker path for the fire-once dedup (TASK-085). `code` is
    always one of this module's own "LENS/..." literals and `task_hash` is
    normally a 16-char hex sha256 slice (_derive_task_hash) — both are
    defensively sanitized anyway since they end up in a filesystem path."""
    import tempfile

    base = Path(_LENS_GATE_STATE_DIR) if _LENS_GATE_STATE_DIR else Path(tempfile.gettempdir())
    safe_code = re.sub(r"[^A-Za-z0-9]+", "-", code).strip("-")[:64]
    safe_hash = re.sub(r"[^A-Za-z0-9]+", "", task_hash)[:64] or "unknown"
    return base / (".nexus-lens-gate-fired-" + safe_code + "-" + safe_hash)


def _already_fired(code, task_hash):
    """Returns True (writes NO new marker) on a REPEAT firing of this exact
    (task_hash, code) pair — the caller must WARN + allow instead of
    blocking. Returns False and WRITES the marker on a first firing — the
    caller blocks exactly as before; the floor is untouched."""
    state_path = _fire_once_state_path(code, task_hash)
    if state_path.exists():
        return True
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.touch()
    except OSError:
        pass
    return False


def main() -> int:
    # TASK-094 LEG B — reset per-invocation RPC-miss tracking (module-level
    # dict — a fresh process per hook firing in production, but reset
    # defensively in case a test harness ever imports this module and calls
    # main() >1x in-process).
    _RPC_STATE["attempted"] = False
    _RPC_STATE["miss"] = False
    _RPC_STATE["latency_ms"] = None

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    # TASK-094 LEG B — trace_id (== session_id) for gate-span emission; see
    # _lens_span_attrs above. Resolved once per invocation.
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()

    assistant_text: str = (
        payload.get("last_assistant_message")
        or payload.get("response", {}).get("text")
        or payload.get("tool_response", {}).get("text")
        or ""
    )
    if not assistant_text:
        _warn_extract_miss(payload)
        return 0

    legacy_match = MARKER_RE.search(assistant_text)
    legacy_marker = legacy_match.group(1).upper() if legacy_match else None

    marker = _resolve_marker(assistant_text, legacy_marker)
    if marker is None:
        return 0

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
                emit_gate_span(
                    "SubagentStop", _code, "deny", _reason,
                    _lens_span_attrs(session_id, "REVISE"),
                )
                return 2
        # TASK-094 LEG B — REVISE-reason capture at hook level: a valid
        # REVISE (criterion present, or a non-verifier persona) never
        # blocks/prints here, but the structured reason(s) are still worth
        # capturing as a `gate` span attribute — pure telemetry, this
        # branch's stdout/exit-code stay byte-identical.
        emit_gate_span(
            "SubagentStop", "LENS/REVISE", "advise", "REVISE captured",
            _lens_span_attrs(session_id, "REVISE", revise_reasons=_revise_reasons(assistant_text)),
        )
        return 0

    if marker != "DONE":
        # Leaf clean-terminal: a leaf executor (GATED_AGENTS) has Task/Agent
        # denied and structurally cannot dispatch Lens itself — a
        # NEEDS-DECISION/CHECKPOINT/BLOCKED handoff (or a non-verifier
        # REVISE, handled above) from that agent means a Lens phase is still
        # pending on the ORCHESTRATOR side, which is the normal T2 workflow
        # shape, not a violation. Only NEXUS:DONE triggers the Lens-
        # validation gate below, for every agent alike.
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
        _git_result = _git_gated_changes(not self_report_present_docs_only)
        git_gated, git_head_only = (None, []) if _git_result is None else _git_result

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
            git_head_only = [p for p in git_head_only if p in git_gated]

        # #281 FIX: a path attributable ONLY to the HEAD~1..HEAD commit (not
        # also uncommitted in the working tree) is committed history that may
        # belong to a different actor entirely (another leg's checkpoint, a
        # pre-existing commit) — fabricating a hard "omitted from self-report"
        # finding from it alone is exactly the shared-dirty-tree misattribution
        # #281 reported. Exclude it from the blocking signal; only the
        # directly-observable working-tree portion stays hard evidence.
        #
        # FLEET-FB-2 fix: this exclusion SCOPES a present self-report — it
        # must never fire when the self-report is absent entirely. An absent
        # self-report has nothing to trust, so the HEAD window must stay a
        # hard fail-closed signal exactly as before FLEET-FB-2 (an agent must
        # not be able to evade the gate by committing gated work to HEAD and
        # omitting a self-report).
        if git_gated and git_head_only and self_report_present_docs_only:
            print(
                "[lens-gate] INFO — excluded from ground-truth attribution "
                f"(HEAD-commit-only, not this agent's uncommitted work): {git_head_only[:5]}.",
                file=sys.stderr,
            )
            git_gated = [p for p in git_gated if p not in git_head_only]

        # NATIVE-14 (pragmatic fix, universal — DEC-068): the git cross-check
        # scans the WHOLE working tree and cannot attribute an uncommitted
        # change to THIS agent. A concurrently dirty tree (another in-flight
        # edit under a gated prefix) then false-blocks a docs-only DONE for
        # changes it never made. When the agent DID report its scope
        # (files_changed present, docs-only), trust the self-report and
        # DOWNGRADE the git discrepancy to an advisory WARN instead of
        # forcing the Lens gate. The structural floor is untouched: a
        # SELF-REPORTED gated path (self_report_gated, above) still hard-
        # requires a Lens row. Only when the self-report is ABSENT entirely
        # (nothing to trust) does git stay a hard signal — that path is
        # unchanged.
        if git_gated and self_report_present_docs_only:
            print(
                "[lens-gate] WARN — git shows gated paths not in the "
                f"docs-only self-report: {git_gated[:5]}. NOT blocking — "
                "whole-tree attribution is unreliable while a concurrent "
                "leg is in flight (NATIVE-14). Confirm the scope is "
                "intentional.",
                file=sys.stderr,
            )
            git_gated = None
            return 0

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

    task_hash = _derive_task_hash(payload, agent_name)
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
        if _already_fired(_code, task_hash):
            print(
                f"[GATE:{_code}] [lens-gate] WARN — REPEAT identical {_code} block for "
                f"{agent_name} task_hash {task_hash} on this leaf turn (TASK-085 fire-once "
                "floor — already blocked once this turn). Allowing the stop through so the "
                "return envelope reaches the orchestrator; the validation DB error is still "
                f"unresolved — fix it, then dispatch Lens for {task_hash} before accepting "
                "this DONE.",
                file=sys.stderr,
            )
            emit_gate_span(
                "SubagentStop", _code, "advise", _reason, _lens_span_attrs(session_id, "FAIL", tier),
            )
            return 0
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        emit_gate_span(
            "SubagentStop", _code, "deny", _reason, _lens_span_attrs(session_id, "FAIL", tier),
        )
        return 2

    if not validated:
        if tier == "T1":
            # DEC-068 (owner-approved TRADE, universal): a genuine T1 leg's
            # row is OPTIONAL — a green deterministic gate alone may satisfy
            # a single-file, non-gated leg. A missing row degrades to an
            # advisory WARN, never a block.
            print(
                f"[lens-gate] ADVISORY — {agent_name.capitalize()} NEXUS:DONE is T1 "
                "(single non-gated file) with no Lens validation row. DEC-068: the row "
                "is OPTIONAL at T1 — a green deterministic gate may satisfy this leg on "
                "its own. Proceeding without a block.\n"
                f"  Agent: {agent_name}\n"
                f"  Task hash: {task_hash}\n"
                f"{gt_note}",
                file=sys.stderr,
            )
            emit_gate_span(
                "SubagentStop", "LENS/T1-ROW-OPTIONAL", "advise",
                "T1 row optional — proceeding without a block",
                _lens_span_attrs(session_id, "T1-ROW-OPTIONAL", tier),
            )
            return 0
        _code = "LENS/NO-VALIDATION"
        _reason = (
            f"[GATE:{_code}] [lens-gate] BLOCK — {agent_name.capitalize()} NEXUS:DONE requires Lens "
            f"validation first (CONTRACT.md Rule 17). Orchestrator: dispatch Lens for "
            f"{task_hash} before accepting this DONE.\n"
            f"  Agent: {agent_name}\n"
            f"  Task hash: {task_hash}\n"
            f"  Lens tier: {tier} ({'light — single non-gated file' if tier == 'T1' else 'full deep audit — multi-file or gated prefix or content probe'})\n"
            f"  Files changed (source): {[f for f in files_changed if _touches_source([f])][:5]}\n"
            f"{gt_note}"
            "  Orchestrator: dispatch Lens, which runs: python3 .memory/log.py validation add "
            f"--agent lens --target {agent_name} --task-hash {task_hash} "
            "--verdict PASS|PARTIAL|FAIL --summary \"...\""
        )
        if _already_fired(_code, task_hash):
            print(
                f"[GATE:{_code}] [lens-gate] WARN — REPEAT identical {_code} block for "
                f"{agent_name} task_hash {task_hash} on this leaf turn (TASK-085 fire-once "
                "floor — already blocked once this turn; a leaf cannot dispatch Lens itself, "
                "so re-blocking would only loop). Allowing the stop through so the return "
                "envelope (files_changed, verification_result, marker) reaches the "
                f"orchestrator — Lens validation is STILL required before this DONE is truly "
                f"accepted; dispatch Lens for {task_hash}.",
                file=sys.stderr,
            )
            emit_gate_span(
                "SubagentStop", _code, "advise", _reason, _lens_span_attrs(session_id, "FAIL", tier),
            )
            return 0
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        emit_gate_span(
            "SubagentStop", _code, "deny", _reason, _lens_span_attrs(session_id, "FAIL", tier),
        )
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
            f"  Orchestrator: dispatch Lens for {task_hash} (with --lens-type matching "
            "the required tier); Lens runs: python3 .memory/log.py validation add "
            f"--agent lens --target {agent_name} --task-hash {task_hash} "
            "--verdict PASS --lens-type T2 --risk-tier T2 --summary \"...\""
        )
        if _already_fired(_code, task_hash):
            print(
                f"[GATE:{_code}] [lens-gate] WARN — REPEAT identical {_code} block for "
                f"{agent_name} task_hash {task_hash} on this leaf turn (TASK-085 fire-once "
                "floor — already blocked once this turn). Allowing the stop through so the "
                "return envelope reaches the orchestrator; the required-tier Lens row is "
                f"still missing — dispatch Lens for {task_hash} at the matching tier.",
                file=sys.stderr,
            )
            emit_gate_span(
                "SubagentStop", _code, "advise", _reason,
                _lens_span_attrs(session_id, "TIER-MISMATCH", tier),
            )
            return 0
        print(_reason, file=sys.stderr)
        _record_block("SubagentStop", _code, _reason)
        emit_gate_span(
            "SubagentStop", _code, "deny", _reason,
            _lens_span_attrs(session_id, "TIER-MISMATCH", tier),
        )
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
    emit_gate_span(
        "SubagentStop", "LENS/PASS", "advise", "Lens validated",
        _lens_span_attrs(session_id, "PASS", tier),
    )
    return 0


if __name__ == "__main__":
    # main() returns an int (0/2), never raising SystemExit itself — capture
    # it here so heartbeat covers every one of main()'s early-return exit
    # paths (deny/warn/allow) without touching its internal control flow.
    _rc = main()
    _emit_heartbeat("SubagentStop", "block" if _rc == 2 else "allow", _elapsed_ms())
    sys.exit(_rc)

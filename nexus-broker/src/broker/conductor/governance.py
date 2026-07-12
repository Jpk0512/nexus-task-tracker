"""broker.conductor.governance — R4-T04 in-process governance for the
conductor lane (plan-13 N07, plans/11-codex-lane-design.md SS9.5).

Three responsibilities, all pure/offline (no model calls; the one I/O this
module performs is a best-effort audit mirror into project.db via the
existing `.memory/log.py` CLI, and a read-only validation_log query):

  1. `allowed_tools_for_node` / `codex_sandbox_flags_for_write_scope` /
     `scope_grant_for_node` — SS9.5's mapping table: the SAME node write_scope
     projects to a FINE-grained claude `allowedTools` grant (per-glob
     Edit/Write) and a COARSE codex `--sandbox`/`-C`/`--add-dir` set (the
     whole worktree, undifferentiated) — the asymmetry is deliberate and
     documented (`CODEX_COARSER_ENFORCEMENT_NOTE`), not an oversight: codex
     has no PreToolUse-equivalent hook point inside its own sandbox, so a
     codex leg cannot be narrowed past "read-only" vs "workspace-write".

  2. `scope_callback` / `enforce_write_scope` — the PreToolUse-equivalent
     scope callback (claude/stub-worker side only, per (1)'s asymmetry): any
     Write/Edit-shaped tool call outside the node's write_scope is denied
     IN-PROCESS (no hook subprocess, no harness round-trip) before the write
     ever reaches disk, and the denial is recorded (appended to an in-memory
     audit list and best-effort mirrored into project.db).

  3. `assert_lens_gate_v2` — the native equivalent of
     `.claude/hooks/lens-gate.sh`'s `_has_lens_validation_v2` (R1-T08),
     run in-process inside the conductor so a leg's output can be gated
     BEFORE it merges into the DAG's completed-results set: >=1 distinct-lens
     PASS row per tier named in the node's `required_lens_types`. This
     module never edits `.claude/hooks/lens-gate.sh` — it reimplements the
     same distinct-lens-row semantics natively against the same
     `validation_log` table (`.memory/schema.sql`), read-only.
"""
from __future__ import annotations

import contextlib
import dataclasses
import fnmatch
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from broker import node_contract
from broker.state import REPO_ROOT

_LOG_PY = REPO_ROOT / ".memory" / "log.py"

# ---------------------------------------------------------------------------
# 1a. allowedTools grant (claude legs) — fine-grained, per-glob
# ---------------------------------------------------------------------------

_BASE_READ_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")


def allowed_tools_for_node(node: dict[str, Any]) -> list[str]:
    """Derive a per-leg `allowedTools` grant from `write_scope` (R4-T04).

    The read floor (Read/Grep/Glob) is persona-invariant and always granted
    (agent-protocol: discovery is free for every persona). An absent/empty
    `write_scope` grants NO Edit/Write at all — a read-only leg, matching
    `node_contract._codex_sandbox_mode_for_write_scope([]) == "read-only"`
    so the claude and codex arms express the identical scope contract for
    the same input. A non-empty `write_scope` grants `Edit(<glob>)` /
    `Write(<glob>)` per pattern — the in-process callback below
    (`check_write_scope`) is what actually enforces the glob at call time;
    this list is the DECLARED grant a leg is dispatched with.
    """
    write_scope = node.get("write_scope") or []
    grants = list(_BASE_READ_TOOLS)
    for pattern in write_scope:
        grants.append(f"Edit({pattern})")
        grants.append(f"Write({pattern})")
    return grants


# ---------------------------------------------------------------------------
# 1b. codex sandbox flags (codex legs) — coarse, whole-worktree
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class CodexSandboxFlags:
    """One row of the SS9.5 mapping table, codex side: `-s <sandbox> -C <cd>
    [--add-dir <dir> ...]`. `add_dirs` is part of the shape for SS9.5
    completeness (the doc's own `[--add-dir ...]` tail) but is always `[]`
    under the current node-contract restriction to relative, worktree-bounded
    `write_scope` entries — `node_contract._codex_sandbox_mode_for_write_scope`
    already rejects (returns None -> ValueError here) any entry that would
    need an `--add-dir` grant (an absolute/external path) before this
    dataclass is ever constructed. The field stays so a future relaxation of
    that upstream restriction is a data change here, not an interface one.
    """

    sandbox: str
    cd: str
    add_dirs: list[str] = dataclasses.field(default_factory=list)

    def to_argv(self) -> list[str]:
        argv = ["-s", self.sandbox, "-C", self.cd]
        for d in self.add_dirs:
            argv += ["--add-dir", d]
        return argv


def codex_sandbox_flags_for_write_scope(
    write_scope: list[str] | None, *, worktree: str,
) -> CodexSandboxFlags:
    """SS9.5 mapping table, codex half: the SAME `write_scope` that derives
    `allowed_tools_for_node` (above) maps to `--sandbox`/`-C`/`--add-dir`.

    Mode selection is delegated to `node_contract._codex_sandbox_mode_for_write_scope`
    (single source of truth — the plan-validation gate, RDEC-011, and
    `dag.build_codex_argv` all key off the identical function; duplicating
    the read-only/workspace-write decision here would risk drift). Raises
    `ValueError` when `write_scope` has no expressible codex sandbox mode —
    same contract as `dag.build_codex_argv`.

    `-C` is always the worktree itself (the leg's own dedicated worktree,
    `dag.build_codex_argv`'s existing convention — "cwd-as-governance",
    Skill sdk-workflow) — NOT narrowed to the individual `write_scope` glob.
    This is exactly the documented coarser bound: a claude leg's Edit/Write
    grant is narrowed per-glob by the in-process callback; a codex leg's
    writable root is the whole worktree, because codex has no per-tool
    callback of its own to narrow it further (see
    `CODEX_COARSER_ENFORCEMENT_NOTE`).
    """
    sandbox = node_contract._codex_sandbox_mode_for_write_scope(write_scope or [])
    if sandbox is None:
        raise ValueError(
            f"write_scope {write_scope!r} has no expressible codex sandbox mode "
            "(read-only or a bounded workspace-write scope)"
        )
    return CodexSandboxFlags(sandbox=sandbox, cd=worktree, add_dirs=[])


CODEX_COARSER_ENFORCEMENT_NOTE = (
    "codex legs get COARSER enforcement than claude legs for the identical "
    "write_scope: a claude leg's Edit/Write grant is narrowed per-glob and "
    "checked in-process by `scope_callback` before every write; a codex leg "
    "only gets `-s read-only` or `-s workspace-write -C <worktree>` — the "
    "whole worktree, undifferentiated — because the codex sandbox has no "
    "PreToolUse-equivalent hook point of its own. This is a deliberate, "
    "documented loosening (plans/11-codex-lane-design.md SS9.5), bounded by "
    "the v1 codex-lane doctrine: `executor: codex` is invalid when "
    "`irreversible: true` (node_contract._validate_executor_fields), so the "
    "coarser sandbox can never be paired with an irreversible leg."
)


@dataclasses.dataclass(frozen=True)
class ScopeGrant:
    """One full mapping-table row: a node's write_scope projected to BOTH the
    claude allowedTools grant and the codex sandbox flags, plus the
    coarser-enforcement note — the single call site that ties SS9.5's two
    halves (1a/1b, above) together for a given node + worktree."""

    node_id: str
    persona: str
    write_scope: list[str]
    allowed_tools: list[str]
    codex_sandbox: CodexSandboxFlags
    coarser_enforcement_note: str = CODEX_COARSER_ENFORCEMENT_NOTE


def scope_grant_for_node(node: dict[str, Any], *, worktree: str) -> ScopeGrant:
    write_scope = list(node.get("write_scope") or [])
    return ScopeGrant(
        node_id=node.get("node_id", "?"),
        persona=node.get("agent_persona", ""),
        write_scope=write_scope,
        allowed_tools=allowed_tools_for_node(node),
        codex_sandbox=codex_sandbox_flags_for_write_scope(write_scope, worktree=worktree),
    )


# ---------------------------------------------------------------------------
# 2. PreToolUse-equivalent scope callback (in-process deny + record)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ScopeDenial:
    node_id: str
    tool: str
    attempted_path: str
    write_scope: list[str]
    reason: str


class ScopeViolation(RuntimeError):
    """Raised by `enforce_write_scope` when a write falls outside write_scope."""

    def __init__(self, denial: ScopeDenial) -> None:
        self.denial = denial
        super().__init__(f"[{denial.node_id}] DENY {denial.tool} {denial.attempted_path!r}: {denial.reason}")


def _normalise_path(path: str) -> str:
    """Strip a single leading './' or '/' — mirrors lens-gate.sh's
    `_normalise_path` exactly (do not use `str.lstrip`, which mangles a
    dotfile prefix like '.claude/...' into 'claude/...')."""
    if path.startswith("./"):
        return path[2:]
    if path.startswith("/"):
        return path[1:]
    return path


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def check_write_scope(node: dict[str, Any], *, tool: str, path: str) -> ScopeDenial | None:
    """Return a `ScopeDenial` if `path` (a Write/Edit target) falls outside
    the node's `write_scope`; `None` if the write is in-scope. Pure/
    deterministic — the ALLOW/DENY decision a PreToolUse hook would compute,
    run natively inside the conductor process."""
    write_scope = list(node.get("write_scope") or [])
    norm = _normalise_path(path)
    if write_scope and _matches_any(norm, write_scope):
        return None
    reason = (
        "write_scope is empty (read-only leg)"
        if not write_scope
        else f"{norm!r} matches none of write_scope={write_scope!r}"
    )
    return ScopeDenial(
        node_id=node.get("node_id", "?"), tool=tool, attempted_path=norm,
        write_scope=write_scope, reason=reason,
    )


def _record_denial_best_effort(denial: ScopeDenial, *, cwd_root: Path = REPO_ROOT) -> None:
    """Best-effort mirror of an in-process denial into project.db, via the
    SAME existing `.memory/log.py context snapshot` CLI path `broker.db`
    already uses for broker-validation events — no new schema, no new
    table. Never raises: a logging failure must never change (or delay) the
    deny decision itself, which has already been made and recorded
    in-process (the caller's `audit_log`) before this is called."""
    cmd = [
        sys.executable, str(_LOG_PY), "context", "snapshot",
        "--action-type", "governance_scope_denial",
        "--files-modified", denial.attempted_path,
        "--summary", (
            f"[{denial.node_id}] DENY {denial.tool} {denial.attempted_path!r}: {denial.reason}"
        ),
    ]
    with contextlib.suppress(Exception):
        subprocess.run(cmd, capture_output=True, timeout=5, cwd=str(cwd_root))


def scope_callback(
    node: dict[str, Any], *, tool_name: str, tool_input: dict[str, Any],
    audit_log: list[ScopeDenial] | None = None, persist: bool = False,
) -> dict[str, Any]:
    """PreToolUse-equivalent scope callback (R4-T04) — the in-process
    analogue of a Claude Code PreToolUse hook's ALLOW/DENY decision, mirroring
    the real hook's JSON decision shape (`{'decision': 'allow'}` /
    `{'decision': 'deny', 'reason': ...}`) so a future real-hook wiring is a
    drop-in. Invoked directly inside the conductor/pool worker, no hook
    subprocess, no harness round-trip — a stub worker's write attempt is
    checked and can be denied BEFORE it ever reaches disk.

    Only Write/Edit(/NotebookEdit)-shaped tool calls carrying a `file_path`
    (or `path`) key are scope-checked; every other tool call (Read, Grep,
    Glob, Bash, ...) passes through untouched — this callback is a WRITE
    gate, not a general allow-list (that's `allowed_tools_for_node`).
    """
    path = tool_input.get("file_path") or tool_input.get("path")
    if tool_name not in ("Write", "Edit", "NotebookEdit") or not path:
        return {"decision": "allow"}

    denial = check_write_scope(node, tool=tool_name, path=path)
    if denial is None:
        return {"decision": "allow"}

    if audit_log is not None:
        audit_log.append(denial)
    if persist:
        _record_denial_best_effort(denial)
    return {
        "decision": "deny",
        "reason": denial.reason,
        "node_id": denial.node_id,
        "tool": denial.tool,
        "attempted_path": denial.attempted_path,
    }


def enforce_write_scope(
    node: dict[str, Any], *, tool: str, path: str,
    audit_log: list[ScopeDenial] | None = None, persist: bool = False,
) -> None:
    """Raise-style wrapper around `scope_callback` for a caller (a stub/pool
    worker) that wants a hard stop rather than a decision dict — the write
    NEVER reaches disk when this raises `ScopeViolation`."""
    decision = scope_callback(
        node, tool_name=tool, tool_input={"file_path": path}, audit_log=audit_log, persist=persist,
    )
    if decision["decision"] == "deny":
        denial = ScopeDenial(
            node_id=decision["node_id"], tool=decision["tool"], attempted_path=decision["attempted_path"],
            write_scope=list(node.get("write_scope") or []), reason=decision["reason"],
        )
        raise ScopeViolation(denial)


# ---------------------------------------------------------------------------
# 3. lens-gate v2 assertion (native, in-process, pre-merge)
# ---------------------------------------------------------------------------


def assert_lens_gate_v2(
    node: dict[str, Any], *, db_path: str | Path,
) -> tuple[bool, str]:
    """Native equivalent of `.claude/hooks/lens-gate.sh`'s
    `_has_lens_validation_v2` (R1-T08), run IN-PROCESS inside the conductor
    (no hook subprocess) so a leg's output can be gated BEFORE it merges into
    the DAG's completed-results set (R4-T04 acceptance).

    Requires >=1 DISTINCT `validation_log` PASS row
    (`agent_validated='lens'`, `verdict='PASS'`, `lens_type=<tier>`) per tier
    named in the node's `required_lens_types` — the identical distinct-lens-
    row semantics as the hook, with ONE deliberate difference: the conductor
    keys the lookup on the node's own `node_id` (`task_or_brief_hash ==
    node_id`) rather than the hook's `sha256(task_id/description)[:16]` —
    the conductor already carries a stable, unique `node_id` per leg, so no
    extra hashing indirection is needed for this in-lane check. (The hook's
    own hashing scheme, `.claude/hooks/lens-gate.sh`, is untouched — this is
    a SEPARATE, native check, not a shared code path with the hook.)

    An absent/empty `required_lens_types` is trivially satisfied (nothing
    required -> nothing to gate) — mirrors the hook's own early return.
    A DB that has no `validation_log` table, or one that pre-dates the
    `lens_type`/`risk_tier` migration, degrades to "skip the v2 check" (the
    same column-existence guard as the hook) rather than crashing. Any other
    DB error (missing file, locked, corrupt) FAILS CLOSED — mirrors the
    hook's own DB-error branch: a broken DB must never silently let an
    ungated leg merge.
    """
    required_tiers = node.get("required_lens_types") or []
    if not required_tiers:
        return True, "no tier requirement supplied"

    target_agent = node.get("agent_persona", "")
    task_hash = node.get("node_id", "")

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return False, f"validation DB unavailable ({db_path}): {exc}"

    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(validation_log)")}
        if "lens_type" not in cols or "risk_tier" not in cols:
            return True, "lens_type/risk_tier columns absent (pre-migration DB) — v2 check skipped"

        rows = conn.execute(
            """
            SELECT DISTINCT lens_type FROM validation_log
            WHERE agent_validated = 'lens'
              AND target_agent    = ?
              AND task_or_brief_hash = ?
              AND verdict = 'PASS'
              AND lens_type IS NOT NULL
              AND datetime(validated_at) > datetime('now', '-1 hours')
            """,
            (target_agent, task_hash),
        ).fetchall()
    except sqlite3.Error as exc:
        return False, f"validation DB query failed ({db_path}): {exc}"
    finally:
        conn.close()

    satisfied = {row[0] for row in rows}
    missing = [t for t in required_tiers if t not in satisfied]
    if missing:
        return False, f"required tiers={required_tiers}, satisfied={sorted(satisfied)}, missing={missing}"
    return True, f"required tiers={required_tiers}, satisfied={sorted(satisfied)}"

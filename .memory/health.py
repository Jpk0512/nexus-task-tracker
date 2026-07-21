#!/usr/bin/env python3
"""Nexus Health Monitor — core checks module.

Public API:
    run_checks(project_path, *, runtime, drift, embed_check) -> HealthReport
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Agents that ship in EVERY Nexus install regardless of stack profile
# (the agnostic floor — _AGNOSTIC_AGENTS in tools/stack_profile.py). These are
# unconditionally required; a missing one is always a FAIL. The floor is EXACTLY
# the truly-universal agents: orchestrator + the three read-only reviewers. It
# must NOT include stack-specific personas (e.g. palette is dashboard-only) or
# retired tombstones (e.g. quill, split into quill-py/quill-ts) — including those
# false-FAILs a correct non-dashboard / re-profiled install that legitimately
# omits them.
_AGNOSTIC_FLOOR: list[str] = [
    "nexus-orchestrator",
    "scout",
    "lens",
    "lens-fast",
]

# The full known universe of agent names the package can ship. Used only as the
# vocabulary for the present-but-unexpected INFO check — NOT as the assertion
# source (the per-install persona_set in .memory/nexus-stack.json is the single
# source of truth for what a given install is expected to contain).
_ALL_KNOWN_AGENTS: list[str] = [
    "atlas",
    "forge",
    "forge-ui",
    "forge-ui-pro",
    "forge-wire",
    "forge-wire-pro",
    "hermes",
    "lens",
    "lens-fast",
    "nexus-orchestrator",
    "palette",
    "pipeline",
    "pipeline-async",
    "pipeline-async-pro",
    "pipeline-data",
    "pipeline-data-pro",
    "quill",
    "quill-py",
    "quill-ts",
    "scout",
]


def _read_persona_set(project_path: str) -> list[str] | None:
    """Read persona_set from .memory/nexus-stack.json (the installed profile).

    Returns the list of expected agent names, or None when the file is absent,
    invalid JSON, or carries no non-empty persona_set list. None signals the
    caller to fall back to the agnostic floor only (e.g. the package source tree
    itself has no nexus-stack.json).
    """
    stack_path = Path(project_path) / ".memory" / "nexus-stack.json"
    if not stack_path.exists():
        return None
    try:
        stack = json.loads(stack_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    personas = stack.get("persona_set")
    if isinstance(personas, list) and personas:
        return [str(p) for p in personas]
    return None


# Test seam: when the env var NEXUS_HEALTH_FORCE_NO_TOMLLIB is set, behave as
# though tomllib is absent (Python <3.11). Lets the regression test exercise the
# degraded path on a 3.11+ box without uninstalling stdlib.
_FORCE_NO_TOMLLIB_ENV = "NEXUS_HEALTH_FORCE_NO_TOMLLIB"


def _load_tomllib() -> object | None:
    """Return the tomllib module, or None when it cannot be imported.

    tomllib is stdlib only on Python 3.11+. Under 3.9 the import raises
    ModuleNotFoundError (an ImportError subclass); callers MUST treat None as
    'TOML parsing unavailable — degrade' rather than letting it propagate, so
    that `health.py` stays usable (rc=0) under the install-time 3.9 interpreter.
    """
    if os.environ.get(_FORCE_NO_TOMLLIB_ENV):
        return None
    try:
        import tomllib  # noqa: PLC0415
    except ImportError:
        return None
    return tomllib

# Safety caps for check_leaks_prior_project — belt-and-suspenders against
# future callers that might add this to an always-run set.
LEAK_SCAN_MAX_FILES: int = 500
LEAK_SCAN_MAX_FILE_BYTES: int = 100_000

# Per-hook execute_with_stub probe budget (seconds). 1.10.0 used 2s, which
# false-FAILed fleet-wide with 'router.py: timed out after 2s' — a COLD-START
# artifact: a real-payload probe returns rc=0 in ~1.74s, but the first cold
# invocation (interpreter warm-up + import graph) routinely overran 2s. Raised
# to 8s, with ONE warm-retry on timeout (the second run hits a warm interpreter),
# so a cold first run no longer false-FAILs while a genuinely hung/blocking hook
# is still caught (it overruns BOTH the cold and the warm 8s run).
HOOK_STUB_TIMEOUT_S: int = 8

# Directories to skip during leak scan (generated/dependency trees — noise + cost)
LEAK_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    ".venv", "node_modules", "__pycache__", ".git",
    ".next", "dist", "build", "coverage",
})

# Path fragments that scope the leak scan to the INSTALL SURFACE (files Nexus
# ships/renders) and OFF accumulated runtime / pre-Nexus user state. These are
# matched as substrings of the POSIX path:
#   - agent-memory/ holds per-persona MEMORY.md — runtime state, never shipped,
#     and may legitimately reference sibling projects a persona has worked on.
#   - _archive* is the user's pre-Nexus archive (e.g. _archive_pre_nexus) — not
#     Nexus install surface, and may carry foreign paths the build never owned.
LEAK_SCAN_SKIP_PATH_PARTS: tuple[str, ...] = (
    "/.claude/agent-memory/",
    "/_archive",
    # lens-reports/ is accumulated .memory RUNTIME state (per-task Lens verdicts),
    # not rendered install-surface source — it legitimately records absolute paths
    # of whatever the run touched and is never clobbered by an install/update.
    "/.memory/lens-reports/",
    "/lens-reports/",
)

# Project-OWNED, never-clobbered files excluded from the leak scan by BASENAME.
# The leak scan polices RENDERED INSTALL-SURFACE source literals (files Nexus
# ships/renders and would be re-rendered on update). settings.local.json is
# project-owned local config (DEC: preserved on update, never re-rendered) and
# legitimately carries the install OWNER's own absolute home path (the 1.10.0
# fleet false-FAIL: '.claude/settings.local.json:7 /Users/john.keeney/'). It is
# the owner's own machine path in their own file — not a FOREIGN-project leak.
LEAK_SCAN_SKIP_FILE_NAMES: frozenset[str] = frozenset({
    "settings.local.json",
})

# Project-name denylist. The shipped package bakes in NOTHING: a clean install
# has no foreign project to leak, and a static list of real names would (a) ship
# the author's project names + internal hostnames into every install, and
# (b) false-FAIL when an install's OWN name legitimately appears in its files.
# Extra terms come from two own-name-safe sources at scan time: the live Plexus
# registry (OTHER projects' basenames, never this one) and the optional
# NEXUS_LEAK_EXTRA_TERMS env var (comma-separated). The high-value, own-name-safe
# signal — an un-tokenized absolute /Users/<name>/ build path — is detected
# structurally in check_leaks_prior_project, not via this list.
LEAK_DENYLIST: list[str] = []

# Matches an un-substituted absolute home path (e.g. a literal /Users/alice/...
# build path that should have been tokenized to __INSTALL_ROOT__). Own-name-safe:
# the scan skips any line that also contains the install's own root path.
_HOME_PATH_RE = re.compile(r"/(?:Users|home)/(?!<)[^/\s\"']+/")


def _leak_extra_terms_from_env() -> list[str]:
    """Optional extra denylist terms from NEXUS_LEAK_EXTRA_TERMS (comma-separated)."""
    raw = os.getenv("NEXUS_LEAK_EXTRA_TERMS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    severity: str  # PASS | WARN | FAIL | INFO | SKIP
    message: str
    hint: str = ""


@dataclass
class HealthReport:
    """Aggregated result of all health checks."""

    project_path: str
    version: str
    session_id: str
    elapsed: float
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passes(self) -> list[CheckResult]:
        """All PASS results."""
        return [r for r in self.results if r.severity == "PASS"]

    @property
    def warns(self) -> list[CheckResult]:
        """All WARN results."""
        return [r for r in self.results if r.severity == "WARN"]

    @property
    def fails(self) -> list[CheckResult]:
        """All FAIL results."""
        return [r for r in self.results if r.severity == "FAIL"]

    @property
    def infos(self) -> list[CheckResult]:
        """All INFO results."""
        return [r for r in self.results if r.severity == "INFO"]

    def to_json(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "project_path": self.project_path,
            "version": self.version,
            "session_id": self.session_id,
            "elapsed": round(self.elapsed, 3),
            "summary": {
                "passes": len(self.passes),
                "warns": len(self.warns),
                "fails": len(self.fails),
                "infos": len(self.infos),
            },
            "results": [
                {
                    "name": r.name,
                    "severity": r.severity,
                    "message": r.message,
                    "hint": r.hint,
                }
                for r in self.results
            ],
        }

    def to_markdown(self) -> str:
        """Render as markdown table suitable for PR comments."""
        lines = [
            f"## Nexus Health · {Path(self.project_path).name} · v{self.version}",
            "",
            "| Status | Check | Message | Hint |",
            "|--------|-------|---------|------|",
        ]
        icon_map = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗", "INFO": "ℹ", "SKIP": "–"}
        for r in self.results:
            icon = icon_map.get(r.severity, r.severity)
            hint = r.hint.replace("|", "\\|") if r.hint else ""
            msg = r.message.replace("|", "\\|")
            lines.append(f"| {icon} {r.severity} | `{r.name}` | {msg} | {hint} |")
        lines.append("")
        lines.append(
            f"**{len(self.passes)} PASS · {len(self.warns)} WARN · "
            f"{len(self.fails)} FAIL** · elapsed {self.elapsed:.2f}s"
        )
        return "\n".join(lines)

    def to_table(self, color: bool = True) -> str:
        """Render as rich table (falls back to ASCII if rich unavailable)."""
        try:
            return _render_rich(self, color=color)
        except ImportError:
            return _render_ascii(self)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _roll_up_status(tier_results: list[CheckResult]) -> str:
    """Collapse a tier's results to a single status icon: ✗ > ⚠ > ✓."""
    if any(r.severity == "FAIL" for r in tier_results):
        return "✗"
    if any(r.severity == "WARN" for r in tier_results):
        return "⚠"
    return "✓"


def _render_rich(report: HealthReport, color: bool = True) -> str:
    """Render using rich library."""
    from io import StringIO

    from rich import box
    from rich.console import Console
    from rich.table import Table

    buf = StringIO()
    console = Console(file=buf, highlight=False, markup=True, force_terminal=color)

    pname = Path(report.project_path).name
    title = f"Nexus Health Check · {pname} · v{report.version}"
    if report.session_id:
        title += f" · {report.session_id}"

    table = Table(title=title, box=box.SIMPLE_HEAVY, expand=False, show_header=False)
    table.add_column("Status", width=4, no_wrap=True)
    table.add_column("Check", min_width=30)
    table.add_column("Message")

    severity_style = {
        "PASS": "green",
        "WARN": "yellow",
        "FAIL": "red",
        "INFO": "blue",
        "SKIP": "dim",
    }
    severity_icon = {
        "PASS": "✓",
        "WARN": "⚠",
        "FAIL": "✗",
        "INFO": "ℹ",
        "SKIP": "–",
    }

    tier_order = [
        ("STATIC", ["agents", "hooks", "mcp.", "version", "ledger", "schema", "leaks", "broker.static", "core_tables"]),
        ("RUNTIME", ["broker.mcp", "db", "embeddings", "prism", "mcp_boot"]),
        ("SESSION", ["heartbeat", "router", "session"]),
        ("DRIFT", ["drift", "conformance"]),
    ]

    for tier_label, prefixes in tier_order:
        tier_results = [
            r for r in report.results
            if any(r.name.startswith(p) for p in prefixes)
        ]
        if not tier_results:
            continue
        status = _roll_up_status(tier_results)
        table.add_row(
            f"[bold]{status}[/bold]",
            f"[bold]{tier_label}[/bold]",
            "",
            style="bold",
        )
        for r in tier_results:
            icon = severity_icon.get(r.severity, r.severity)
            style = severity_style.get(r.severity, "")
            msg = r.message
            if r.hint:
                msg += f"\n  → {r.hint}"
            table.add_row(
                f"[{style}]{icon}[/{style}]",
                f"[{style}]{r.name}[/{style}]",
                msg,
            )

    console.print(table)
    n_pass = len(report.passes)
    n_warn = len(report.warns)
    n_fail = len(report.fails)
    summary = (
        f"[green]{n_pass} PASS[/green] · "
        f"[yellow]{n_warn} WARN[/yellow] · "
        f"[red]{n_fail} FAIL[/red] · "
        f"elapsed {report.elapsed:.2f}s"
    )
    console.print(summary)
    return buf.getvalue()


def _render_ascii(report: HealthReport) -> str:
    """Plain ASCII fallback renderer."""
    lines = [
        "─" * 70,
        f" Nexus Health Check · {Path(report.project_path).name} · v{report.version}",
        "─" * 70,
    ]
    icon_map = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗", "INFO": "ℹ", "SKIP": "–"}
    tier_order = [
        ("STATIC", ["agents", "hooks", "mcp.", "version", "ledger", "schema", "leaks", "broker.static", "core_tables"]),
        ("RUNTIME", ["broker.mcp", "db", "embeddings", "prism", "mcp_boot"]),
        ("SESSION", ["heartbeat", "router", "session"]),
        ("DRIFT", ["drift", "conformance"]),
    ]
    for tier_label, prefixes in tier_order:
        tier_results = [
            r for r in report.results
            if any(r.name.startswith(p) for p in prefixes)
        ]
        if not tier_results:
            continue
        lines.append(f" {tier_label}")
        for r in tier_results:
            icon = icon_map.get(r.severity, r.severity)
            lines.append(f"  {icon} {r.name:<35} {r.message}")
            if r.hint:
                lines.append(f"{'':38}→ {r.hint}")
    lines.append("─" * 70)
    lines.append(
        f" {len(report.passes)} PASS · {len(report.warns)} WARN · "
        f"{len(report.fails)} FAIL · elapsed {report.elapsed:.2f}s"
    )
    lines.append("─" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_ledger(project_path: str) -> dict | None:
    """Read .nexus-ledger.json; return None if absent or invalid."""
    p = Path(project_path) / ".nexus-ledger.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _read_settings(project_path: str) -> dict | None:
    """Read .claude/settings.json; return None if absent or invalid."""
    p = Path(project_path) / ".claude" / "settings.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _settings_hook_paths(project_path: str, settings: dict) -> list[str]:
    """Extract all .claude/hooks/<file> paths referenced in settings.json hooks."""
    paths: list[str] = []
    hooks_block = settings.get("hooks", {})
    for _event, matchers in hooks_block.items():
        for entry in matchers:
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                # match ./.claude/hooks/<file>.sh or ./.claude/hooks/<file>.py
                for m in re.finditer(r"\.\/\.claude\/hooks\/([\w.\-]+\.(?:sh|py))", cmd):
                    paths.append(m.group(1))
    return list(dict.fromkeys(paths))  # deduplicate, preserve order


def _parse_yaml_frontmatter(text: str) -> dict | None:
    """Parse YAML frontmatter from a markdown file. Returns None on parse error."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm_text = text[3:end].strip()
    result: dict = {}
    current_list: list[str] | None = None
    for line in fm_text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith(("  - ", "- ")):
            item = line.strip().lstrip("- ").strip()
            # Strip inline YAML comments before quote-stripping (mirrors the
            # key:val handling below) — e.g. "socraticode:codebase-exploration
            # # comment" must parse as the skill id, not swallow the comment.
            item = re.sub(r"\s+#.*$", "", item).strip().strip('"').strip("'")
            if current_list is not None:
                current_list.append(item)
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            # Strip inline YAML comments before processing value
            val = re.sub(r"\s+#.*$", "", val).strip().strip('"').strip("'")
            if val in ("", "[]"):
                # empty list — either "key:" with no value or "key: []" inline
                current_list = []
                result[key] = current_list
            else:
                result[key] = val
                current_list = None
    return result


def _plexus_registry_entry(project_path: str) -> dict | None:
    """Look up this project_path in Plexus's registry DB. Returns None if not found."""
    plexus_db = Path.home() / "nexus-installer" / ".memory" / "project.db"
    if not plexus_db.exists():
        return None
    try:
        conn = sqlite3.connect(str(plexus_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM project_registry WHERE project_path = ? LIMIT 1",
            (str(project_path),),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except sqlite3.Error:
        pass
    return None


def _get_project_version(project_path: str) -> str:
    """Get version from .nexus-ledger.json or 'unknown'."""
    ledger = _read_ledger(project_path)
    if ledger and "version" in ledger:
        return str(ledger["version"])
    return "unknown"


def _get_open_session_id(project_path: str) -> str:
    """Get the most recent open session ID from project.db."""
    db_path = Path(project_path) / ".memory" / "project.db"
    if not db_path.exists():
        return ""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row["id"] if row else ""
    except sqlite3.Error:
        return ""


# ---------------------------------------------------------------------------
# STATIC checks
# ---------------------------------------------------------------------------

def check_agents_canonical_inventory(project_path: str) -> list[CheckResult]:
    """Check the install's EXPECTED agents are present with valid frontmatter.

    The expected roster is profile-aware: it is the persona_set declared in
    .memory/nexus-stack.json unioned with the agnostic floor. When no
    nexus-stack.json is present (e.g. the package source tree itself), it falls
    back to the agnostic floor only. A missing expected agent or broken
    frontmatter is a FAIL; an on-disk agent that is present but not expected is
    reported as INFO (never a FAIL — a stack may legitimately omit personas).
    """
    agents_dir = Path(project_path) / ".claude" / "agents"
    if not agents_dir.exists():
        return [CheckResult(
            "agents.canonical_inventory", "FAIL",
            ".claude/agents/ directory missing",
            "Create the agents directory and populate with canonical agent files",
        )]

    persona_set = _read_persona_set(project_path)
    if persona_set is not None:
        expected_set = set(persona_set) | set(_AGNOSTIC_FLOOR)
    else:
        expected_set = set(_AGNOSTIC_FLOOR)
    expected = sorted(expected_set)

    results: list[CheckResult] = []
    for agent_name in expected:
        agent_file = agents_dir / f"{agent_name}.md"
        if not agent_file.exists():
            results.append(CheckResult(
                "agents.canonical_inventory", "FAIL",
                f"Missing agent: {agent_name}.md",
                f"Add {agent_name}.md to .claude/agents/",
            ))
            continue
        text = agent_file.read_text(encoding="utf-8")
        fm = _parse_yaml_frontmatter(text)
        if fm is None:
            results.append(CheckResult(
                "agents.canonical_inventory", "FAIL",
                f"Broken YAML frontmatter in {agent_name}.md",
                "Fix the YAML frontmatter (check for unclosed quotes or bad indentation)",
            ))
        else:
            results.append(CheckResult(
                "agents.canonical_inventory", "PASS",
                f"{agent_name}.md present, frontmatter valid",
            ))

    on_disk = {p.stem for p in agents_dir.glob("*.md")}
    for extra in sorted(on_disk - expected_set):
        results.append(CheckResult(
            "agents.canonical_inventory", "INFO",
            f"Agent present but not in expected roster: {extra}.md",
            "Present-but-unexpected agents are informational, not a failure",
        ))
    return results


def check_agents_skill_declarations(project_path: str) -> list[CheckResult]:
    """For each agent, verify every declared skill exists as a dir under .claude/skills/."""
    agents_dir = Path(project_path) / ".claude" / "agents"
    skills_dir = Path(project_path) / ".claude" / "skills"
    if not agents_dir.exists():
        return [CheckResult(
            "agents.skill_declarations", "SKIP",
            "agents dir missing — skipping skill declaration check",
        )]
    results: list[CheckResult] = []
    for agent_file in sorted(agents_dir.glob("*.md")):
        text = agent_file.read_text(encoding="utf-8")
        fm = _parse_yaml_frontmatter(text)
        if fm is None:
            continue
        skills = fm.get("skills", [])
        if not isinstance(skills, list):
            skills = [skills] if skills else []
        for skill in skills:
            skill_name = str(skill)
            if ":" in skill_name:
                # Colon-namespaced plugin skill (e.g. "anthropic-skills:pptx",
                # "codex:rescue") — provided by an installed plugin, never a
                # local .claude/skills/<name>/ dir. Satisfiable without a
                # local-dir check; asserting one here would false-FAIL every
                # install that legitimately relies on a plugin skill.
                results.append(CheckResult(
                    "agents.skill_declarations", "PASS",
                    f"{agent_file.name}: skill '{skill_name}' is plugin-namespaced (satisfied without local dir)",
                ))
                continue
            skill_path = skills_dir / skill_name
            if not skill_path.exists():
                results.append(CheckResult(
                    "agents.skill_declarations", "FAIL",
                    f"{agent_file.name}: skill '{skill}' dir missing",
                    f"Create .claude/skills/{skill}/ or remove from agent frontmatter",
                ))
            else:
                results.append(CheckResult(
                    "agents.skill_declarations", "PASS",
                    f"{agent_file.name}: skill '{skill}' present",
                ))
    if not results:
        results.append(CheckResult(
            "agents.skill_declarations", "PASS",
            "All agent skill declarations resolved",
        ))
    return results


def check_hooks_settings_resolves(project_path: str) -> list[CheckResult]:
    """Parse settings.json and verify every referenced hook file exists."""
    settings = _read_settings(project_path)
    if settings is None:
        return [CheckResult(
            "hooks.settings_resolves", "FAIL",
            ".claude/settings.json missing or invalid JSON",
            "Ensure settings.json exists and is valid JSON",
        )]
    hooks_dir = Path(project_path) / ".claude" / "hooks"
    hook_files = _settings_hook_paths(project_path, settings)
    results: list[CheckResult] = []
    for fname in hook_files:
        fpath = hooks_dir / fname
        if not fpath.exists():
            results.append(CheckResult(
                "hooks.settings_resolves", "FAIL",
                f"Referenced hook missing: {fname}",
                f"Create .claude/hooks/{fname} or remove from settings.json",
            ))
        else:
            results.append(CheckResult(
                "hooks.settings_resolves", "PASS",
                f"Hook present: {fname}",
            ))
    if not hook_files:
        results.append(CheckResult(
            "hooks.settings_resolves", "PASS",
            "No hook references to validate",
        ))
    return results


def check_hooks_executable(project_path: str) -> list[CheckResult]:
    """Verify all settings.json-referenced hook scripts have +x bit."""
    settings = _read_settings(project_path)
    if settings is None:
        return [CheckResult(
            "hooks.executable", "SKIP",
            "settings.json missing — skipping executable check",
        )]
    hooks_dir = Path(project_path) / ".claude" / "hooks"
    hook_files = _settings_hook_paths(project_path, settings)
    results: list[CheckResult] = []
    for fname in hook_files:
        fpath = hooks_dir / fname
        if not fpath.exists():
            continue  # already reported by settings_resolves
        mode = fpath.stat().st_mode
        if not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
            results.append(CheckResult(
                "hooks.executable", "FAIL",
                f"Missing +x bit: .claude/hooks/{fname}",
                f"Run: chmod +x .claude/hooks/{fname}",
            ))
        else:
            results.append(CheckResult(
                "hooks.executable", "PASS",
                f"+x set: {fname}",
            ))
    if not results:
        results.append(CheckResult(
            "hooks.executable", "PASS",
            "All referenced hooks are executable",
        ))
    return results


def check_mcp_config_valid(project_path: str) -> list[CheckResult]:
    """Validate .mcp.json: exists, parses, no placeholder residue, all --directory paths exist."""
    mcp_path = Path(project_path) / ".mcp.json"
    if not mcp_path.exists():
        return [CheckResult(
            "mcp.config_valid", "FAIL",
            ".mcp.json missing",
            "Ensure .mcp.json is present in the project root",
        )]
    try:
        cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [CheckResult(
            "mcp.config_valid", "FAIL",
            f".mcp.json is invalid JSON: {exc}",
            "Fix the JSON syntax in .mcp.json",
        )]
    raw_text = mcp_path.read_text(encoding="utf-8")
    placeholders = re.findall(r"__[A-Z_]+__", raw_text)
    if placeholders:
        return [CheckResult(
            "mcp.config_valid", "FAIL",
            f"Unresolved placeholders in .mcp.json: {', '.join(set(placeholders))}",
            "Run the installer substitution or set the missing env vars",
        )]
    results: list[CheckResult] = []
    for server_name, server_cfg in cfg.get("mcpServers", {}).items():
        args = server_cfg.get("args", [])
        for i, arg in enumerate(args):
            if arg == "--directory" and i + 1 < len(args):
                dir_path = Path(args[i + 1])
                if not dir_path.exists():
                    results.append(CheckResult(
                        "mcp.config_valid", "FAIL",
                        f"MCP server '{server_name}' --directory path missing: {args[i + 1]}",
                        f"Ensure {args[i + 1]} exists or update .mcp.json",
                    ))
    if not results:
        results.append(CheckResult(
            "mcp.config_valid", "PASS",
            ".mcp.json valid, no placeholders, all paths present",
        ))
    return results


def check_codex_surface(project_path: str) -> list[CheckResult]:
    """Validate the Codex-native install surface (.codex/) if present (TASK-109).

    Codex integration (DEC-102) ships a project-local .codex/ tree. This check is
    ADVISORY-tolerant: an install predating TASK-109 has no .codex/ and returns a
    single INFO (not a FAIL). When present, it validates the pieces Codex actually
    depends on:
      - config.toml exists, has no unresolved __TOKEN__ residue, and declares both
        [mcp_servers.nexus-broker] + [mcp_servers.nexus-vault] (self-contained MCP);
      - mcp.json parses, has no placeholder residue, and its nexus server
        --directory paths exist on disk;
      - hooks/codex-adapter.sh exists and is executable (Codex invokes it directly);
      - AGENTS.md (orchestrator identity carrier) is present.
    3.9-safe.
    """
    codex_dir = Path(project_path) / ".codex"
    if not codex_dir.is_dir():
        return [CheckResult(
            "codex.surface", "INFO",
            "no .codex/ surface (Codex integration not installed for this project)",
        )]

    # A Nexus-managed .codex surface is identified by its advisory adapter. A
    # .codex/ dir WITHOUT codex-adapter.sh is an unrelated / hand-maintained Codex
    # config (e.g. the Plexus meta-repo's own dev config, or a pre-TASK-109
    # install) — skip validation rather than FAIL on a surface Nexus does not own.
    adapter = codex_dir / "hooks" / "codex-adapter.sh"
    if not adapter.is_file():
        return [CheckResult(
            "codex.surface", "INFO",
            ".codex/ present but not a Nexus-managed surface (no codex-adapter.sh) — skipping codex checks",
        )]

    results: list[CheckResult] = []

    config = codex_dir / "config.toml"
    if not config.is_file():
        results.append(CheckResult(
            "codex.surface", "FAIL",
            ".codex/config.toml missing",
            "Re-run install.sh or update to reship the .codex surface",
        ))
    else:
        config_text = config.read_text(encoding="utf-8")
        placeholders = re.findall(r"__[A-Z_]+__", config_text)
        if placeholders:
            results.append(CheckResult(
                "codex.surface", "FAIL",
                f"Unresolved placeholders in .codex/config.toml: {', '.join(sorted(set(placeholders)))}",
                "Run the installer substitution pass (install.sh) for this project",
            ))
        for key in ("nexus-broker", "nexus-vault"):
            if not re.search(r"^\[mcp_servers\." + re.escape(key) + r"\]", config_text, re.MULTILINE):
                results.append(CheckResult(
                    "codex.surface", "FAIL",
                    f".codex/config.toml missing [mcp_servers.{key}] section",
                    "Reship .codex/config.toml (self-contained MCP per DEC-102 DEC-3)",
                ))

    mcp = codex_dir / "mcp.json"
    if not mcp.is_file():
        results.append(CheckResult(
            "codex.surface", "FAIL",
            ".codex/mcp.json missing",
            "Re-run install.sh or update to reship the .codex surface",
        ))
    else:
        raw = mcp.read_text(encoding="utf-8")
        placeholders = re.findall(r"__[A-Z_]+__", raw)
        if placeholders:
            results.append(CheckResult(
                "codex.surface", "FAIL",
                f"Unresolved placeholders in .codex/mcp.json: {', '.join(sorted(set(placeholders)))}",
                "Run the installer substitution pass (install.sh) for this project",
            ))
        else:
            try:
                cfg = json.loads(raw)
            except json.JSONDecodeError as exc:
                cfg = None
                results.append(CheckResult(
                    "codex.surface", "FAIL",
                    f".codex/mcp.json is invalid JSON: {exc}",
                    "Fix the JSON syntax in .codex/mcp.json",
                ))
            if cfg is not None:
                for server_name, server_cfg in cfg.get("mcpServers", {}).items():
                    args = server_cfg.get("args", [])
                    for i, arg in enumerate(args):
                        if arg == "--directory" and i + 1 < len(args):
                            dir_path = Path(args[i + 1])
                            if not dir_path.exists():
                                results.append(CheckResult(
                                    "codex.surface", "FAIL",
                                    f"Codex MCP server '{server_name}' --directory path missing: {args[i + 1]}",
                                    f"Ensure {args[i + 1]} exists or re-run install.sh",
                                ))

    # adapter presence is the marker gate above; here we only assert its +x bit.
    adapter_mode = adapter.stat().st_mode
    if not (adapter_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
        results.append(CheckResult(
            "codex.surface", "FAIL",
            "Missing +x bit: .codex/hooks/codex-adapter.sh",
            "Run: chmod +x .codex/hooks/codex-adapter.sh",
        ))

    if not (codex_dir / "AGENTS.md").is_file():
        results.append(CheckResult(
            "codex.surface", "FAIL",
            ".codex/AGENTS.md missing (orchestrator identity carrier)",
            "Re-run install.sh or update to reship the .codex surface",
        ))

    if not results:
        results.append(CheckResult(
            "codex.surface", "PASS",
            ".codex surface valid: config/mcp resolved, adapter executable, AGENTS.md present",
        ))
    return results


def check_version_matches_registry(project_path: str) -> list[CheckResult]:
    """Compare .nexus-ledger.json version to Plexus registry entry."""
    ledger = _read_ledger(project_path)
    if ledger is None:
        return [CheckResult(
            "version.matches_registry", "INFO",
            "No .nexus-ledger.json found — version unknown",
        )]
    if not ledger or "version" not in ledger:
        return [CheckResult(
            "version.matches_registry", "INFO",
            ".nexus-ledger.json exists but has no version field",
        )]
    ledger_version = str(ledger["version"])
    reg_entry = _plexus_registry_entry(project_path)
    if reg_entry is None:
        return [CheckResult(
            "version.matches_registry", "WARN",
            f"Ledger version={ledger_version}, but no registry entry found",
            "Run: python3 ~/nexus-installer/.memory/log.py registry add",
        )]
    reg_version = str(reg_entry.get("current_version", ""))
    if ledger_version == reg_version:
        return [CheckResult(
            "version.matches_registry", "PASS",
            f"Version {ledger_version} matches registry",
        )]
    return [CheckResult(
        "version.matches_registry", "FAIL",
        f"Ledger={ledger_version} vs registry={reg_version}",
        "Run plexus update or reconcile the ledger manually",
    )]


def check_ledger_present_and_consistent(project_path: str) -> list[CheckResult]:
    """Verify .nexus-ledger.json exists, is valid JSON, and registry has_ledger=1."""
    ledger_path = Path(project_path) / ".nexus-ledger.json"
    if not ledger_path.exists():
        return [CheckResult(
            "ledger.present_and_consistent", "INFO",
            "No ledger yet — created post-install by Plexus",
        )]
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [CheckResult(
            "ledger.present_and_consistent", "FAIL",
            ".nexus-ledger.json is invalid JSON",
            "Fix or regenerate .nexus-ledger.json",
        )]
    reg_entry = _plexus_registry_entry(project_path)
    if reg_entry is None:
        return [CheckResult(
            "ledger.present_and_consistent", "WARN",
            "Ledger present but no registry entry — has_ledger cannot be verified",
            "Run: python3 ~/nexus-installer/.memory/log.py registry add",
        )]
    has_ledger = int(reg_entry.get("has_ledger", 0))
    if has_ledger != 1:
        return [CheckResult(
            "ledger.present_and_consistent", "WARN",
            "Ledger exists but registry has_ledger=0",
            "Update registry: python3 ~/nexus-installer/.memory/log.py registry add",
        )]
    return [CheckResult(
        "ledger.present_and_consistent", "PASS",
        f"Ledger present, valid JSON, registry has_ledger=1 (v{ledger.get('version', '?')})",
    )]


def check_schema_vec_dim_aligned(project_path: str) -> list[CheckResult]:
    """Compare _EMBED_DIM in log.py source to vec_memory table schema dim."""
    log_py = Path(project_path) / ".memory" / "log.py"
    if not log_py.exists():
        return [CheckResult(
            "schema.vec_dim_aligned", "SKIP",
            ".memory/log.py not found — skipping vec dim check",
        )]
    source = log_py.read_text(encoding="utf-8")
    m = re.search(r"_EMBED_DIM\s*=\s*(\d+)", source)
    if not m:
        return [CheckResult(
            "schema.vec_dim_aligned", "SKIP",
            "_EMBED_DIM not found in log.py source",
        )]
    code_dim = int(m.group(1))

    db_path = Path(project_path) / ".memory" / "project.db"
    if not db_path.exists():
        return [CheckResult(
            "schema.vec_dim_aligned", "INFO",
            f"_EMBED_DIM={code_dim} in source; DB not found to verify schema",
        )]
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='vec_memory'"
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return [CheckResult(
                "schema.vec_dim_aligned", "INFO",
                "vec_memory table not yet created in DB",
            )]
        ddl = row[0]
        m2 = re.search(r"float\[(\d+)\]", ddl, re.IGNORECASE)
        if not m2:
            return [CheckResult(
                "schema.vec_dim_aligned", "WARN",
                "Could not parse float[N] dim from vec_memory DDL",
                f"DDL: {ddl[:120]}",
            )]
        db_dim = int(m2.group(1))
        if code_dim == db_dim:
            return [CheckResult(
                "schema.vec_dim_aligned", "PASS",
                f"_EMBED_DIM={code_dim} matches vec_memory schema dim={db_dim}",
            )]
        return [CheckResult(
            "schema.vec_dim_aligned", "FAIL",
            f"_EMBED_DIM={code_dim} in source but vec_memory dim={db_dim} in DB",
            "Re-initialize the DB or update _EMBED_DIM to match the schema",
        )]
    except sqlite3.Error as exc:
        return [CheckResult(
            "schema.vec_dim_aligned", "WARN",
            f"Could not query DB schema: {exc}",
        )]


# The canonical "persistence is alive" table set asserted by the install-time
# post-init gate (install.sh) and the update-time post-apply health gate
# (tools/safe_update.py). This is the SINGLE SOURCE OF TRUTH for that gate set:
# both consumers import CORE_TABLES from here rather than re-hardcoding the list,
# so the install gate, the update gate, and this module can never silently
# diverge. It is the floor every initialized project.db must carry for
# sessions/tasks/decisions/lessons/semantic_facts/context_log to be recordable.
#
# This is DISTINCT from _CORE_TABLES below (the schema-scoping vocabulary used by
# _expected_core_tables, which additionally tracks satellite tables such as
# validation_log/embed_outbox). CORE_TABLES is the deliberately narrower,
# always-required gate set — do not collapse the two.
CORE_TABLES: tuple[str, ...] = (
    "sessions",
    "tasks",
    "decisions",
    "lessons",
    "semantic_facts",
    "context_log",
)

# Plain relational tables that MUST exist in every initialized project.db. A
# missing one means persistence is structurally dead — log.py init never ran (or
# ran against an old schema), so sessions/tasks/decisions can't be recorded. This
# is a STATIC/structural check (no runtime services) so it fires even under
# --no-runtime, catching a dead schema at SessionStart.
_CORE_TABLES: list[str] = [
    "sessions",
    "tasks",
    "decisions",
    "lessons",
    "semantic_facts",
    "context_log",
    "validation_log",
    "embed_outbox",
]

# The structural backbone every persistence-alive DB carries — the subset of
# _CORE_TABLES whose CREATE statements have no embedded ';'/quoting that a
# naive split would shatter. This is the floor required when the install's own
# schema.sql is NOT alongside the DB (we can't then prove which core tables the
# deployed schema version even promises). The remaining satellite tables
# (lessons, embed_outbox) are additionally required whenever schema.sql is
# present and declares them — see _expected_core_tables.
_CORE_TABLE_BACKBONE: list[str] = [
    "sessions",
    "tasks",
    "decisions",
    "semantic_facts",
    "context_log",
    "validation_log",
]

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)",
    re.IGNORECASE,
)


def _expected_core_tables(project_path: str) -> list[str]:
    """The core tables this install is expected to carry, in canonical order.

    When the install ships its own schema.sql, scope the requirement to the core
    tables that schema actually declares — asserting every core table the
    deployed schema promises is materialized, while never demanding a table a
    different schema version doesn't create. When schema.sql is absent (we can't
    then prove the deployed schema's table set), fall back to the structural
    backbone floor.
    """
    schema_path = Path(project_path) / ".memory" / "schema.sql"
    if not schema_path.exists():
        return _CORE_TABLE_BACKBONE
    try:
        declared = {
            m.lower() for m in _CREATE_TABLE_RE.findall(
                schema_path.read_text(encoding="utf-8")
            )
        }
    except OSError:
        return _CORE_TABLE_BACKBONE
    scoped = [t for t in _CORE_TABLES if t in declared]
    return scoped or _CORE_TABLE_BACKBONE


def check_core_tables_present(project_path: str) -> list[CheckResult]:
    """Verify every expected core relational table exists in project.db."""
    expected = _expected_core_tables(project_path)
    db_path = Path(project_path) / ".memory" / "project.db"
    if not db_path.exists():
        return [CheckResult(
            "core_tables.present", "FAIL",
            "project.db not found — persistence is not initialized",
            "run: python3 .memory/log.py init",
        )]
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        return [CheckResult(
            "core_tables.present", "FAIL",
            f"Could not read project.db schema: {exc}",
            "run: python3 .memory/log.py init",
        )]

    present = {row[0] for row in rows}
    missing = [t for t in expected if t not in present]
    if missing:
        return [CheckResult(
            "core_tables.present", "FAIL",
            f"missing core table(s): {', '.join(missing)}",
            "run: python3 .memory/log.py init",
        )]
    return [CheckResult(
        "core_tables.present", "PASS",
        f"all {len(expected)} core tables present",
    )]


# The structural files that MUST exist for the broker to be importable at all.
# Relative to nexus-broker/. A missing one means the broker tree is broken and
# `python -m broker.server` / `broker.vault.stdio` cannot boot — a fail-open
# broker the SessionStart banner must surface WITHOUT paying the multi-second
# full-import cost of check_broker_mcp_boots (RUNTIME).
_BROKER_REQUIRED_FILES: list[str] = [
    "pyproject.toml",
    "uv.lock",
    "src/broker/__init__.py",
    "src/broker/server.py",
    "src/broker/vault/stdio.py",
]


def check_broker_static_structure(project_path: str) -> list[CheckResult]:
    """Fast STATIC proxy for broker bootability — no import, no resolve, no .venv.

    Complements (does NOT replace) the RUNTIME check_broker_mcp_boots, which does
    the real ~5s `uv run python -c "from broker import server"` boot AND the deep
    'do deps actually resolve' verification. This check runs in the STATIC tier
    (under --no-runtime, the SessionStart banner path) and catches the obvious
    unbootable-broker breakages in ~50–100ms using ONLY parse + path-existence
    probes — it spawns NO uv subprocess, so it can never populate a .venv or leak
    bytecode while rendering the banner:

      1. `uv` is on PATH (the only runner the broker boots under).
      2. nexus-broker/pyproject.toml exists and is valid TOML.
      3. nexus-broker/uv.lock exists and parses (TOML).
      4. the broker src tree is intact (src/broker/{__init__,server}.py,
         src/broker/vault/stdio.py).

    The 'do deps actually resolve' check deliberately lives in the RUNTIME
    check_broker_mcp_boots (explicit `log.py health`), NOT here — keeping this
    check truly static and non-populating.

    Severity: FAIL when uv is missing, a structural path is missing, or
    pyproject/uv.lock is unparseable (the fail-open-broker surface — clear
    message + remediation hint). PASS when every structural check holds. SKIP
    when nexus-broker/ is absent, INFO when the install's .mcp.json still carries
    the pre-substitution __INSTALL_ROOT__ placeholder (install.sh not run yet).
    """
    broker_dir = Path(project_path) / "nexus-broker"
    if not broker_dir.exists():
        return [CheckResult(
            "broker.static_structure", "SKIP",
            "nexus-broker/ dir not found — skipping broker structure check",
        )]

    mcp_json = Path(project_path) / ".mcp.json"
    if mcp_json.exists():
        try:
            if "__INSTALL_ROOT__" in mcp_json.read_text(encoding="utf-8"):
                return [CheckResult(
                    "broker.static_structure", "INFO",
                    "Broker not yet configured (install.sh substitution pending)",
                )]
        except OSError:
            pass

    if shutil.which("uv") is None:
        return [CheckResult(
            "broker.static_structure", "FAIL",
            "uv not found on PATH — broker cannot boot",
            "Install uv (https://docs.astral.sh/uv/) and ensure it is on PATH",
        )]

    missing = [
        rel for rel in _BROKER_REQUIRED_FILES
        if not (broker_dir / rel).exists()
    ]
    if missing:
        return [CheckResult(
            "broker.static_structure", "FAIL",
            f"broker tree incomplete — missing: {', '.join(missing)}",
            "Re-run the installer/update — nexus-broker/ is corrupt or partial",
        )]

    # Lazy import: tomllib is 3.11+. Importing it locally (not at module top)
    # keeps health.py importable under Python 3.9 — which the install-time
    # post-init gate relies on when it does `from health import CORE_TABLES`.
    # When tomllib is unavailable (Python <3.11, or the test seam below forces
    # it), DEGRADE rather than raise: we already proved the broker tree is
    # structurally intact (uv on PATH, pyproject.toml + uv.lock present, src
    # tree complete) above — so return PASS with a WARN-style note that the
    # TOML *parse* was skipped, never a traceback.
    tomllib = _load_tomllib()
    if tomllib is None:
        pyproject = broker_dir / "pyproject.toml"
        lock = broker_dir / "uv.lock"
        for label, probe in (("pyproject.toml", pyproject), ("uv.lock", lock)):
            try:
                if not probe.is_file() or probe.stat().st_size == 0:
                    return [CheckResult(
                        "broker.static_structure", "FAIL",
                        f"nexus-broker/{label} is missing or empty",
                        "Re-run the installer/update — broker config is corrupt",
                    )]
            except OSError as exc:
                return [CheckResult(
                    "broker.static_structure", "FAIL",
                    f"nexus-broker/{label} is unreadable: {exc}",
                    "Re-run the installer/update — broker config is corrupt",
                )]
        return [CheckResult(
            "broker.static_structure", "PASS",
            "broker tree intact + uv present + pyproject/uv.lock exist "
            "(TOML parse skipped — tomllib unavailable, python<3.11)",
        )]

    pyproject = broker_dir / "pyproject.toml"
    try:
        with pyproject.open("rb") as fh:
            tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [CheckResult(
            "broker.static_structure", "FAIL",
            f"nexus-broker/pyproject.toml is unparseable: {exc}",
            "Restore a valid pyproject.toml (re-run installer/update)",
        )]

    lock = broker_dir / "uv.lock"
    try:
        with lock.open("rb") as fh:
            tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [CheckResult(
            "broker.static_structure", "FAIL",
            f"nexus-broker/uv.lock is unparseable: {exc}",
            "Re-resolve deps: cd nexus-broker && uv lock",
        )]

    # NOTE: deliberately NO `uv lock --check` (or any uv subprocess) here. That
    # invocation populates nexus-broker/.venv (bytecode leak) and slows the
    # SessionStart banner. The deep 'do deps actually resolve' verification lives
    # in the RUNTIME check_broker_mcp_boots (explicit `log.py health`), keeping
    # this STATIC check truly static and non-populating.

    return [CheckResult(
        "broker.static_structure", "PASS",
        "broker tree intact + uv present + pyproject/uv.lock parse (static)",
    )]


def check_vec_memory_available(project_path: str) -> list[CheckResult]:
    """STATIC probe: can semantic-recall load (sqlite-vec), or is it deferred?

    Semantic recall needs an interpreter that can BOTH load the sqlite C
    extension AND `import sqlite_vec`. log.py's bootstrap re-execs into
    .memory/.venv/bin/python (python 3.12 + sqlite-vec) precisely because the
    system interpreter usually cannot. A FRESH install may not yet have that
    venv (the operator builds it with `uv venv .memory/.venv` + sqlite-vec), so
    recall is *deferred*, not *broken*: relational memory (sessions/tasks/
    decisions) still works — only semantic search degrades to keyword fallback.

    Severity is WARN, never FAIL (lesson from the broker-gate: a missing vec
    layer is a degradation, not a blocker). This is a pure import/filesystem
    probe — no embed round-trip, no subprocess — so it is safe + fast in the
    STATIC tier under --no-runtime (the SessionStart banner path).

      PASS — this interpreter can load sqlite-vec; recall is live.
      WARN — sqlite-vec is unavailable here; recall falls back to keyword
             search until .memory/.venv is built (hint names the fix).
    """
    venv_py = Path(project_path) / ".memory" / ".venv" / "bin" / "python"
    build_hint = (
        "Build the memory venv: uv venv .memory/.venv --python 3.12 && "
        "uv pip install --python .memory/.venv/bin/python sqlite-vec"
    )

    try:
        probe = sqlite3.connect(":memory:")
        try:
            probe.enable_load_extension(True)
        finally:
            probe.close()
        import sqlite_vec  # noqa: F401
    except ImportError:
        deferred = "build pending" if not venv_py.exists() else ".venv present but sqlite-vec not importable"
        return [CheckResult(
            "schema.vec_available", "WARN",
            f"sqlite-vec not installed — semantic recall deferred ({deferred}), "
            "keyword fallback in use; relational memory unaffected",
            build_hint,
        )]
    except (AttributeError, sqlite3.Error):
        return [CheckResult(
            "schema.vec_available", "WARN",
            "sqlite extension loading unavailable on this interpreter — "
            "semantic recall deferred, keyword fallback in use; "
            "relational memory unaffected",
            build_hint if not venv_py.exists() else (
                "Run memory ops via .memory/.venv/bin/python (log.py re-execs there)"
            ),
        )]
    except Exception as exc:
        return [CheckResult(
            "schema.vec_available", "WARN",
            f"sqlite-vec probe failed ({type(exc).__name__}) — semantic recall "
            "deferred, keyword fallback in use; relational memory unaffected",
            build_hint,
        )]

    return [CheckResult(
        "schema.vec_available", "PASS",
        "sqlite-vec loadable — semantic recall available",
    )]


def check_leaks_prior_project(
    project_path: str,
    *,
    registry_denylist: list[str] | None = None,
) -> list[CheckResult]:
    """Scan the install SURFACE (.claude/ and .memory/files/) for leaked references.

    Two own-name-safe signals:
      1. denylist terms — OTHER projects' basenames from the live Plexus registry
         (never this install's own name) plus any NEXUS_LEAK_EXTRA_TERMS. The
         shipped static list (LEAK_DENYLIST) is empty by design.
      2. un-tokenized absolute home paths — a literal /Users/<name>/ or
         /home/<name>/ build path that should have been substituted. Lines
         containing the install's own root path are ignored (own-name-safe).

    Scope is the install surface only: accumulated runtime state
    (.claude/agent-memory/<persona>/MEMORY.md) and the user's pre-Nexus archive
    (_archive*) are NOT policed — they are not files Nexus ships/renders, and may
    legitimately reference sibling/foreign projects (see LEAK_SCAN_SKIP_PATH_PARTS).

    registry_denylist: pre-built extra terms to add (avoids redundant DB query
    when caller already has the list). Pass [] to suppress the DB lookup entirely.
    """
    proot = Path(project_path)
    own_root = str(proot)

    # Own-owner-home prefix (own-name-safe). The install owner's own absolute home
    # path (e.g. /Users/john.keeney/) is NOT a foreign-project leak — it is the
    # owner's own machine path. The home-path heuristic already ignores lines that
    # contain own_root, but a project-owned file may carry the owner's home prefix
    # WITHOUT the full project path (the 1.10.0 settings.local.json:7
    # /Users/john.keeney/ false-FAIL). Derive the prefix structurally from the
    # project path: if own_root is itself under /Users/<name>/ or /home/<name>/,
    # that match IS the owner's home prefix. Falls back to Path.home() (the user
    # running the check). Lines containing this prefix are own-name-safe and skip
    # the home-path signal; a FOREIGN /Users/someone-else/ prefix still FAILs.
    _owner_home_prefixes: set[str] = set()
    _m_own = _HOME_PATH_RE.search(own_root + "/")
    if _m_own:
        _owner_home_prefixes.add(_m_own.group(0))
    try:
        _home = str(Path.home())
        _m_home = _HOME_PATH_RE.search(_home + "/")
        if _m_home:
            _owner_home_prefixes.add(_m_home.group(0))
    except (OSError, RuntimeError):
        pass

    # Build full denylist from static list (empty) + env extras + other registry
    # project basenames. None of these ever contains this install's own name.
    denylist = set(LEAK_DENYLIST)
    denylist.update(_leak_extra_terms_from_env())
    if registry_denylist is None:
        reg_entry = _plexus_registry_entry(project_path)
        if reg_entry is not None:
            plexus_db = Path.home() / "nexus-installer" / ".memory" / "project.db"
            try:
                conn = sqlite3.connect(str(plexus_db))
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT project_path FROM project_registry").fetchall()
                conn.close()
                for row in rows:
                    other_path = row["project_path"]
                    if other_path != str(project_path):
                        denylist.add(Path(other_path).name)
            except sqlite3.Error:
                pass
    else:
        denylist.update(registry_denylist)

    # Exclude these basenames from scan (install memos, scout-reports, subagent-returns)
    scan_exclude_names = {"install-memos", "scout-reports", "subagent-returns", "CHANGELOG.md"}

    scan_dirs = [proot / ".claude", proot / ".memory" / "files"]
    leaks: list[str] = []
    n_scanned = 0

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for fpath in scan_dir.rglob("*"):
            if fpath.is_dir():
                continue
            # Skip generated / dependency directories
            if any(part in LEAK_SCAN_SKIP_DIRS for part in fpath.parts):
                continue
            # Skip .nexus-backup-* directories
            if any(part.startswith(".nexus-backup-") for part in fpath.parts):
                continue
            # Scope to the install surface: skip accumulated runtime state
            # (agent-memory/) and the user's pre-Nexus archive (_archive*).
            posix = fpath.as_posix()
            if any(frag in posix for frag in LEAK_SCAN_SKIP_PATH_PARTS):
                continue
            if fpath.name in scan_exclude_names:
                continue
            # Skip project-OWNED, never-clobbered files (e.g. settings.local.json):
            # they are not rendered install-surface source and legitimately carry
            # the owner's own absolute home path.
            if fpath.name in LEAK_SCAN_SKIP_FILE_NAMES:
                continue
            if fpath.suffix in {".pyc", ".db", ".db-wal", ".db-shm"}:
                continue

            # Safety cap: stop scanning if we've already visited too many files
            if n_scanned >= LEAK_SCAN_MAX_FILES:
                return [CheckResult(
                    "leaks.prior_project", "INFO",
                    f"Skipped scan: file count > {LEAK_SCAN_MAX_FILES} cap",
                    "Run a single-project health check to scan fully",
                )]

            # Skip large files — denylist matches in binary blobs are noise
            try:
                file_size = fpath.stat().st_size
            except OSError:
                continue
            if file_size > LEAK_SCAN_MAX_FILE_BYTES:
                continue

            n_scanned += 1
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            file_flagged = False
            for term in denylist:
                # skip CHANGELOG / docs reference lines
                if term.lower() in text.lower():
                    # allow changelog / history references
                    lines_with_term = [
                        ln for ln in text.splitlines()
                        if term.lower() in ln.lower()
                        and not any(
                            kw in ln.lower()
                            for kw in ["changelog", "history", "# ", "version"]
                        )
                    ]
                    if lines_with_term:
                        leaks.append(f"{fpath.relative_to(proot)}: '{term}'")
                        file_flagged = True
                        break  # one leak per file is enough

            if file_flagged:
                continue
            # Un-tokenized absolute home path (own-name-safe: ignore the install's
            # own root AND the owner's own home prefix, and skip changelog /
            # history reference lines). A FOREIGN /Users/someone-else/ build path
            # still FAILs; the owner's own /Users/<owner>/ is not a leak.
            for i, ln in enumerate(text.splitlines(), 1):
                if own_root in ln:
                    continue
                if any(kw in ln.lower() for kw in ["changelog", "history", "# ", "version"]):
                    continue
                m = _HOME_PATH_RE.search(ln)
                if m and m.group(0) not in _owner_home_prefixes:
                    leaks.append(f"{fpath.relative_to(proot)}:{i}: '{m.group(0)}'")
                    break  # one leak per file is enough

    if leaks:
        return [CheckResult(
            "leaks.prior_project", "FAIL",
            f"{len(leaks)} leaked project reference(s) found",
            "; ".join(leaks[:5]),
        )]
    return [CheckResult(
        "leaks.prior_project", "PASS",
        "No leaked project references found",
    )]


# ---------------------------------------------------------------------------
# RUNTIME checks
# ---------------------------------------------------------------------------

def check_broker_mcp_boots(project_path: str) -> list[CheckResult]:
    """Run a quick import test of the nexus-broker server."""
    broker_dir = Path(project_path) / "nexus-broker"
    if not broker_dir.exists():
        return [CheckResult(
            "broker.mcp_boots", "SKIP",
            "nexus-broker/ dir not found — skipping broker boot check",
        )]
    mcp_json = Path(project_path) / ".mcp.json"
    if mcp_json.exists():
        try:
            raw = mcp_json.read_text(encoding="utf-8")
            if "__INSTALL_ROOT__" in raw:
                return [CheckResult(
                    "broker.mcp_boots", "INFO",
                    "Broker not yet configured (install.sh substitution pending)",
                )]
        except OSError:
            pass
    # --no-sync: install builds the .venv up front and the update flow runs an
    # explicit `uv sync`, so this probe never needs to resolve/fetch — a bare
    # `uv run` can stall for minutes on lock resolution when the network is
    # slow/unreachable (observed twice against the same install: 5s timeout
    # hit while `.venv/bin/python -c "import broker.server"` completed in
    # 0.64s and `uv run --no-sync` in 1.4s). 15s budget (up from 5s) covers a
    # cold-cache `--no-sync` boot without reopening the network-stall window.
    cmd = [
        "uv", "run", "--no-sync", "--quiet", "python", "-c",
        "from broker import server; print(server.mcp.name)",
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(broker_dir), capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return [CheckResult(
                "broker.mcp_boots", "FAIL",
                f"broker server import failed (rc={result.returncode})",
                (result.stderr or result.stdout or "")[:200],
            )]
        name = result.stdout.strip()
        return [CheckResult(
            "broker.mcp_boots", "PASS",
            f"Broker MCP server boots: name='{name}'",
        )]
    except subprocess.TimeoutExpired:
        return [CheckResult(
            "broker.mcp_boots", "FAIL",
            "Broker boot timed out after 15s",
            "Check nexus-broker/ for import errors or blocking code",
        )]
    except FileNotFoundError:
        return [CheckResult(
            "broker.mcp_boots", "FAIL",
            "uv not found — cannot run broker boot check",
            "Ensure uv is installed and on PATH",
        )]


def check_hooks_execute_with_stub(project_path: str) -> list[CheckResult]:
    """Run each settings-referenced hook with empty stdin; expect exit 0 or 1."""
    settings = _read_settings(project_path)
    if settings is None:
        return [CheckResult(
            "hooks.execute_with_stub", "SKIP",
            "settings.json missing — skipping hook stub execution",
        )]
    hooks_dir = Path(project_path) / ".claude" / "hooks"
    hook_files = _settings_hook_paths(project_path, settings)
    results: list[CheckResult] = []
    for fname in hook_files:
        fpath = hooks_dir / fname
        if not fpath.exists():
            continue
        try:
            result = subprocess.run(
                [str(fpath)],
                input="{}",
                capture_output=True,
                text=True,
                timeout=HOOK_STUB_TIMEOUT_S,
                cwd=str(project_path),
            )
        except subprocess.TimeoutExpired:
            # Cold-start false-FAIL guard: the first invocation pays interpreter
            # warm-up + import-graph cost. Retry ONCE — the second run hits a warm
            # interpreter. A genuinely hung/blocking hook overruns BOTH runs and
            # still FAILs (detection is preserved).
            try:
                result = subprocess.run(
                    [str(fpath)],
                    input="{}",
                    capture_output=True,
                    text=True,
                    timeout=HOOK_STUB_TIMEOUT_S,
                    cwd=str(project_path),
                )
            except subprocess.TimeoutExpired:
                results.append(CheckResult(
                    "hooks.execute_with_stub", "FAIL",
                    f"{fname}: timed out after {HOOK_STUB_TIMEOUT_S}s (warm retry)",
                    "Hook may be blocking or waiting for input",
                ))
                continue
            except (OSError, PermissionError) as exc:
                results.append(CheckResult(
                    "hooks.execute_with_stub", "FAIL",
                    f"{fname}: exec error — {exc}",
                    f"chmod +x .claude/hooks/{fname}",
                ))
                continue
        except (OSError, PermissionError) as exc:
            results.append(CheckResult(
                "hooks.execute_with_stub", "FAIL",
                f"{fname}: exec error — {exc}",
                f"chmod +x .claude/hooks/{fname}",
            ))
            continue

        if result.returncode in (0, 1):
            results.append(CheckResult(
                "hooks.execute_with_stub", "PASS",
                f"{fname}: exit {result.returncode} (ok)",
            ))
        else:
            results.append(CheckResult(
                "hooks.execute_with_stub", "FAIL",
                f"{fname}: unexpected exit code {result.returncode}",
                (result.stderr or result.stdout or "")[:150],
            ))
    if not results:
        results.append(CheckResult(
            "hooks.execute_with_stub", "PASS",
            "No hooks to execute",
        ))
    return results


def check_db_write_probe(project_path: str) -> list[CheckResult]:
    """Insert + rollback a marker row in context_log to verify DB write access.

    context_log.session_id is TEXT NOT NULL with an FK to sessions(id), and
    logged_at is TEXT NOT NULL — so the probe must supply a valid session_id and
    a logged_at. We reuse the most-recent existing session when one exists, else
    insert a sentinel session inside the transaction. Everything (sentinel + probe
    row) is rolled back so the probe leaves no residue on a healthy DB, while a
    genuinely unwritable DB still surfaces a sqlite3.Error -> FAIL.
    """
    from datetime import datetime, timezone

    db_path = Path(project_path) / ".memory" / "project.db"
    if not db_path.exists():
        return [CheckResult(
            "db.write_probe", "SKIP",
            "project.db not found — skipping write probe",
        )]
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='context_log'"
            ).fetchone()
            if row is None:
                return [CheckResult(
                    "db.write_probe", "INFO",
                    "DB not initialized yet — run python3 .memory/log.py init",
                )]
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017
            conn.execute("BEGIN")
            existing = conn.execute(
                "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if existing is not None:
                session_id = existing[0]
            else:
                session_id = "S-health-probe"
                conn.execute(
                    "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                    (session_id, now_iso),
                )
            conn.execute(
                "INSERT INTO context_log (session_id, logged_at, action_type, summary) "
                "VALUES (?, ?, ?, ?)",
                (session_id, now_iso, "health_probe", "probe"),
            )
            conn.execute("ROLLBACK")
            return [CheckResult(
                "db.write_probe", "PASS",
                "DB write probe succeeded (rolled back)",
            )]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [CheckResult(
            "db.write_probe", "FAIL",
            f"DB write probe failed: {exc}",
            "Check DB schema and permissions",
        )]


def check_embeddings_endpoint_reachable(project_path: str) -> list[CheckResult]:
    """Check if LM Studio embeddings endpoint is reachable (WARN not FAIL if down)."""
    import urllib.error as _uerr
    import urllib.request as _req

    # Check cache first (5 min TTL)
    cache_file = Path(project_path) / ".memory" / "files" / ".health_cache.json"
    now_ts = time.time()
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if now_ts - cached.get("ts", 0) < 300:
                ok = cached.get("embed_ok", False)
                return [CheckResult(
                    "embeddings.endpoint_reachable",
                    "PASS" if ok else "WARN",
                    "(cached) " + ("LM Studio endpoint reachable" if ok else "LM Studio endpoint unreachable"),
                    "" if ok else "Start LM Studio or set LMSTUDIO_URL",
                )]
        except (json.JSONDecodeError, KeyError):
            pass

    lmstudio_url = os.getenv("LMSTUDIO_URL", "http://localhost:1234/v1/models")
    try:
        req = _req.Request(lmstudio_url, method="GET")
        with _req.urlopen(req, timeout=1):
            pass
        ok = True
        result = CheckResult(
            "embeddings.endpoint_reachable", "PASS",
            f"LM Studio endpoint reachable at {lmstudio_url}",
        )
    except (_uerr.URLError, OSError):
        ok = False
        result = CheckResult(
            "embeddings.endpoint_reachable", "WARN",
            f"LM Studio endpoint unreachable at {lmstudio_url}",
            "Start LM Studio or set LMSTUDIO_URL env var — embeddings won't work until then",
        )

    # Write cache
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"ts": now_ts, "embed_ok": ok}))
    except OSError:
        pass

    return [result]


# Default timeout (seconds) for the PRISM boot probe. Overridable via
# NEXUS_HEALTH_BOOT_TIMEOUT for slower/loaded environments where a cold `uv run`
# import routinely exceeds a few seconds.
PRISM_BOOT_TIMEOUT_S: float = 15.0


def _prism_boot_timeout() -> float:
    """NEXUS_HEALTH_BOOT_TIMEOUT override (float/int seconds); invalid -> default."""
    raw = os.getenv("NEXUS_HEALTH_BOOT_TIMEOUT", "")
    if not raw.strip():
        return PRISM_BOOT_TIMEOUT_S
    try:
        return float(raw)
    except ValueError:
        return PRISM_BOOT_TIMEOUT_S


def check_prism_mcp_boots(project_path: str) -> list[CheckResult]:
    """Run a quick import test of the PRISM server if .prism/ exists."""
    prism_dir = Path(project_path) / "prism"
    if not prism_dir.exists():
        return [CheckResult(
            "prism.mcp_boots", "SKIP",
            "prism/ dir not found — skipping PRISM boot check",
        )]
    cmd = [
        "uv", "run", "--quiet", "python", "-c",
        "from prism.synthesis.mcp_server import mcp; print(mcp.name)",
    ]
    timeout_s = _prism_boot_timeout()
    try:
        result = subprocess.run(
            cmd, cwd=str(prism_dir), capture_output=True, text=True, timeout=timeout_s
        )
        if result.returncode != 0:
            return [CheckResult(
                "prism.mcp_boots", "FAIL",
                f"PRISM server import failed (rc={result.returncode})",
                (result.stderr or result.stdout or "")[:200],
            )]
        name = result.stdout.strip()
        return [CheckResult(
            "prism.mcp_boots", "PASS",
            f"PRISM MCP server boots: name='{name}'",
        )]
    except subprocess.TimeoutExpired:
        return [CheckResult(
            "prism.mcp_boots", "FAIL",
            f"PRISM boot timed out after {timeout_s:g}s",
        )]
    except FileNotFoundError:
        return [CheckResult(
            "prism.mcp_boots", "FAIL",
            "uv not found",
        )]


# Timeout (seconds) for the per-server import probe. SHORT by design: the probe
# only imports the module, it never serves — a hang means a blocking import.
MCP_IMPORT_PROBE_TIMEOUT_S: int = 5


def _arg_value_after(args: list[str], flag: str) -> str | None:
    """Return the arg immediately following `flag` in `args`, or None."""
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
    return None


def _mcp_import_target(server_cfg: dict, project_path: str) -> tuple[str | None, str | None]:
    """Derive (run_directory, import_module) for a NON-SPAWNING import probe.

    Reads the server's args for a `--directory <dir>` (the run dir whose venv we
    use) and a `-m <module>` (the server module to import-test). Returns
    (None, None) when the server cannot be probed in-place:
      - no `-m <module>` in args (nothing to import), OR
      - no `--directory` *inside the install* (e.g. a global launcher binary like
        lsp-py / mcp-language-server — we don't own its env, so we SKIP it).

    The import target is the MODULE ONLY — we run `python -c "import <module>"`,
    NOT `python -m <module>` (which would START the server and block).
    """
    args = server_cfg.get("args", [])
    if not isinstance(args, list):
        return (None, None)
    module = _arg_value_after(args, "-m")
    if not module:
        return (None, None)
    directory = _arg_value_after(args, "--directory")
    if not directory:
        return (None, None)
    dir_path = Path(directory)
    install_root = Path(project_path).resolve()
    try:
        dir_resolved = dir_path.resolve()
    except OSError:
        return (None, None)
    # Only probe servers whose run directory lives inside this install — we own
    # that uv-managed venv. Anything outside is another tool's environment.
    if install_root not in dir_resolved.parents and dir_resolved != install_root:
        return (None, None)
    return (str(dir_path), module)


def check_mcp_servers_boot(project_path: str) -> list[CheckResult]:
    """Registry-driven, NON-SPAWNING import probe for every MCP server in .mcp.json.

    Generalizes the hand-rolled check_broker_mcp_boots / check_prism_mcp_boots:
    parses .mcp.json `mcpServers`, and for each stdio server whose `--directory`
    is inside the install and whose args name a `-m <module>`, runs an IMPORT-ONLY
    probe — `uv run --directory <dir> python -c "import <module>"` — under a 5s
    timeout. This NEVER starts the server (no `python -m`, no socket, no serve
    loop); the subprocess imports the module and exits on its own. subprocess.run
    reaps it, and kills + reaps on timeout overrun, so no process is left running.

    An ImportError / ModuleNotFoundError (nonzero exit) -> FAIL. This is exactly
    how a registered-but-broken server (e.g. nexus-vault missing its module/deps)
    is caught instead of reporting a false-green install.

    Severities:
      FAIL  — module import failed (rc != 0), timed out, or uv missing.
      PASS  — module imported cleanly (rc == 0).
      SKIP  — server has no in-install `--directory` + `-m <module>` to probe
              (e.g. a global launcher binary), or .mcp.json absent/invalid, or
              placeholders still unresolved.
    """
    mcp_path = Path(project_path) / ".mcp.json"
    if not mcp_path.exists():
        return [CheckResult(
            "mcp_boot.servers", "SKIP",
            ".mcp.json missing — skipping MCP boot probe",
        )]
    try:
        raw = mcp_path.read_text(encoding="utf-8")
    except OSError:
        return [CheckResult(
            "mcp_boot.servers", "SKIP",
            ".mcp.json unreadable — skipping MCP boot probe",
        )]
    if "__INSTALL_ROOT__" in raw or re.search(r"__[A-Z_]+__", raw):
        return [CheckResult(
            "mcp_boot.servers", "SKIP",
            "MCP config not yet substituted (install.sh pending) — skipping boot probe",
        )]
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return [CheckResult(
            "mcp_boot.servers", "SKIP",
            ".mcp.json invalid JSON — boot probe skipped (see mcp.config_valid)",
        )]

    servers = cfg.get("mcpServers", {})
    if not isinstance(servers, dict) or not servers:
        return [CheckResult(
            "mcp_boot.servers", "SKIP",
            "No mcpServers entries to probe",
        )]

    results: list[CheckResult] = []
    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        directory, module = _mcp_import_target(server_cfg, project_path)
        if directory is None or module is None:
            results.append(CheckResult(
                "mcp_boot.servers", "SKIP",
                f"'{server_name}': no in-install module to import-probe",
                "Server has no '-m <module>' under an in-install '--directory' "
                "(e.g. a global launcher binary) — not boot-probed here",
            ))
            continue
        # IMPORT-ONLY — `import <module>`, never `-m <module>`. This does not
        # start the server; the child imports and exits immediately.
        cmd = [
            "uv", "run", "--quiet", "--directory", directory,
            "python", "-c", f"import {module}",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=MCP_IMPORT_PROBE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run terminates + reaps the child on timeout, so nothing
            # is left running — a hang here means a blocking import, still a FAIL.
            results.append(CheckResult(
                "mcp_boot.servers", "FAIL",
                f"'{server_name}': import of '{module}' timed out "
                f"after {MCP_IMPORT_PROBE_TIMEOUT_S}s",
                f"Check {directory} for a blocking import in {module}",
            ))
            continue
        except FileNotFoundError:
            results.append(CheckResult(
                "mcp_boot.servers", "FAIL",
                f"'{server_name}': uv not found — cannot run import probe",
                "Ensure uv is installed and on PATH",
            ))
            continue
        if result.returncode != 0:
            results.append(CheckResult(
                "mcp_boot.servers", "FAIL",
                f"'{server_name}': import of '{module}' failed "
                f"(rc={result.returncode})",
                (result.stderr or result.stdout or "")[:200],
            ))
        else:
            results.append(CheckResult(
                "mcp_boot.servers", "PASS",
                f"'{server_name}': module '{module}' imports cleanly",
            ))
    return results


# ---------------------------------------------------------------------------
# SESSION checks
# ---------------------------------------------------------------------------

def check_heartbeat_recent(project_path: str) -> list[CheckResult]:
    """Check recency of hook_heartbeat.jsonl last entry."""
    hb_path = Path(project_path) / ".memory" / "files" / "hook_heartbeat.jsonl"
    if not hb_path.exists():
        return [CheckResult(
            "heartbeat.recent", "INFO",
            "hook_heartbeat.jsonl not found — no heartbeat data",
        )]
    try:
        lines = [ln for ln in hb_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return [CheckResult(
                "heartbeat.recent", "INFO",
                "hook_heartbeat.jsonl is empty",
            )]
        last = json.loads(lines[-1])
        ts_str = last.get("ts") or last.get("timestamp") or ""
        if not ts_str:
            return [CheckResult(
                "heartbeat.recent", "INFO",
                "Last heartbeat entry has no timestamp field",
            )]
        # 3.9-safe: timezone.utc, not datetime.UTC (the UTC alias is 3.11+).
        # health.py must stay importable under the system python that runs the
        # install-time `from health import CORE_TABLES` gate.
        from datetime import datetime, timedelta, timezone
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = now - ts
        if age < timedelta(hours=1):
            return [CheckResult(
                "heartbeat.recent", "PASS",
                f"Last heartbeat {int(age.total_seconds() / 60)}m ago",
            )]
        if age < timedelta(hours=24):
            return [CheckResult(
                "heartbeat.recent", "WARN",
                f"Last heartbeat {int(age.total_seconds() / 3600)}h ago (>1h)",
                "Hooks may not be firing — check session is active",
            )]
        return [CheckResult(
            "heartbeat.recent", "INFO",
            f"Last heartbeat {age.days}d ago (>24h)",
        )]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        return [CheckResult(
            "heartbeat.recent", "INFO",
            f"Could not parse heartbeat file: {exc}",
        )]


def check_daemon_liveness(project_path: str) -> list[CheckResult]:
    """TASK-105 fail-out-loud daemon health.

    daemon.liveness — if .claude/daemon.enabled is present and the daemon
    socket does not answer a health ping, that is a FAIL (never a WARN): the
    silent-no-spawn / socketless-zombie incident class must surface at the
    very next health run, not accumulate 13k RPC misses in the dark.

    daemon.rpc_misses — >50 "no-socket" misses in daemon_rpc_misses.jsonl in
    the last 10 minutes is a WARN even when the socket answers now (a daemon
    that only just came up after a long dark window).
    """
    # 3.9-safe local imports; the socket-path derivation is hand-inlined
    # (mirrors broker.daemon.paths.socket_path_for — health.py must stay
    # importable without the nexus-broker venv, same convention as
    # .claude/hooks/_daemon_rpc.py).
    import hashlib
    import socket as socket_mod
    from datetime import datetime, timedelta, timezone

    results: list[CheckResult] = []
    root = Path(project_path)
    flag = root / ".claude" / "daemon.enabled"
    override = os.environ.get("NEXUS_DAEMON_SOCKET_DIR")
    sock_dir = Path(override) if override else Path.home() / ".nexus" / "daemon"
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    sock_path = sock_dir / f"{digest}.sock"

    if not flag.exists():
        results.append(CheckResult(
            "daemon.liveness", "INFO",
            "daemon not enabled (.claude/daemon.enabled absent)",
        ))
    else:
        ok = False
        detail = f"no socket at {sock_path}"
        if sock_path.exists():
            sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
            sock.settimeout(1.0)
            try:
                sock.connect(str(sock_path))
                sock.sendall(b'{"id": 1, "method": "health", "params": {}}\n')
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                payload = json.loads(buf.decode("utf-8")) if buf else {}
                result = payload.get("result") if isinstance(payload, dict) else None
                if isinstance(result, dict) and result.get("status") == "ok":
                    ok = True
                    detail = (
                        f"pid {result.get('pid')}, "
                        f"resident_version {result.get('resident_version', '?')}"
                    )
                else:
                    detail = f"socket answered but not healthy: {payload!r:.200}"
            except (OSError, ValueError) as exc:
                detail = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
        if ok:
            results.append(CheckResult(
                "daemon.liveness", "PASS",
                f"daemon answers health ping ({detail})",
            ))
        else:
            results.append(CheckResult(
                "daemon.liveness", "FAIL",
                f"daemon.enabled is set but the daemon is NOT answering ({detail})",
                "Run: uv run --directory nexus-broker python -m broker.daemon ensure "
                "--project-path . — or install auto-start: tools/install_daemon_launchd.sh",
            ))

    misses_path = root / ".memory" / "files" / "daemon_rpc_misses.jsonl"
    if misses_path.exists():
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
            recent = 0
            lines = misses_path.read_text(encoding="utf-8").splitlines()
            for ln in reversed(lines):
                if not ln.strip():
                    continue
                try:
                    row = json.loads(ln)
                    ts = datetime.fromisoformat(str(row.get("ts", "")).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if ts < cutoff:
                    break
                if row.get("reason") == "no-socket":
                    recent += 1
            if recent > 50:
                results.append(CheckResult(
                    "daemon.rpc_misses", "WARN",
                    f"{recent} no-socket daemon RPC misses in the last 10m (>50)",
                    "Hooks are falling back inline — check daemon liveness / ensure-daemon.sh "
                    "failures in .memory/files/daemon-ensure-failures.log",
                ))
            else:
                results.append(CheckResult(
                    "daemon.rpc_misses", "PASS",
                    f"{recent} no-socket daemon RPC misses in the last 10m",
                ))
        except OSError as exc:
            results.append(CheckResult(
                "daemon.rpc_misses", "INFO",
                f"could not read daemon_rpc_misses.jsonl: {exc}",
            ))
    return results


def check_router_recent_decisions(project_path: str) -> list[CheckResult]:
    """Check if router_decisions.jsonl has any entry from past 24h."""
    rd_path = Path(project_path) / ".memory" / "files" / "router_decisions.jsonl"
    if not rd_path.exists():
        return [CheckResult(
            "router.recent_decisions", "INFO",
            "router_decisions.jsonl not found",
        )]
    try:
        # 3.9-safe: timezone.utc, not datetime.UTC (the UTC alias is 3.11+).
        from datetime import datetime, timedelta, timezone
        lines = [ln for ln in rd_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if not lines:
            return [CheckResult(
                "router.recent_decisions", "INFO",
                "router_decisions.jsonl is empty",
            )]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        recent = False
        for ln in reversed(lines):
            try:
                entry = json.loads(ln)
                ts_str = entry.get("ts") or entry.get("timestamp") or ""
                if ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts > cutoff:
                        recent = True
                        break
            except (json.JSONDecodeError, ValueError):
                continue
        if recent:
            return [CheckResult(
                "router.recent_decisions", "PASS",
                "Router decision logged within past 24h",
            )]
        return [CheckResult(
            "router.recent_decisions", "INFO",
            "No router decisions in past 24h",
        )]
    except OSError as exc:
        return [CheckResult(
            "router.recent_decisions", "INFO",
            f"Could not read router_decisions.jsonl: {exc}",
        )]


def check_session_has_open(project_path: str) -> list[CheckResult]:
    """Check for open sessions in project.db (INFO only, never FAIL)."""
    db_path = Path(project_path) / ".memory" / "project.db"
    if not db_path.exists():
        return [CheckResult(
            "session.has_open", "INFO",
            "project.db not found",
        )]
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return [CheckResult(
                "session.has_open", "INFO",
                f"Open session: {row['id']}",
            )]
        return [CheckResult(
            "session.has_open", "INFO",
            "No open session",
        )]
    except sqlite3.Error as exc:
        return [CheckResult(
            "session.has_open", "INFO",
            f"DB error reading sessions: {exc}",
        )]


# Recent-row window + rounding precision for the completion-time KPI summary.
_DISPATCH_KPI_RECENT_N = 20


def check_dispatch_telemetry_kpi(project_path: str) -> list[CheckResult]:
    """Completion-time KPI (NATIVE-42 / R1-T01) — INFO only, never FAIL.

    Surfaces the exact, orchestrator-captured per-dispatch token+time telemetry
    that makes the doer+reviewer "architecture beats capability" thesis
    falsifiable: a recent-N row count plus per-persona AND per-model
    aggregates (count, avg tokens, avg duration_ms, exact-vs-approx ratio).
    Missing project.db or missing/empty dispatch_telemetry renders a graceful
    empty state — this check must never raise or FAIL.
    """
    db_path = Path(project_path) / ".memory" / "project.db"
    if not db_path.exists():
        return [CheckResult(
            "dispatch_telemetry.kpi", "INFO",
            "project.db not found",
        )]
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dispatch_telemetry'"
        ).fetchone()
        if not exists:
            conn.close()
            return [CheckResult(
                "dispatch_telemetry.kpi", "INFO",
                "dispatch_telemetry table not present (pre-NATIVE-42 install)",
            )]

        total = conn.execute("SELECT COUNT(*) AS n FROM dispatch_telemetry").fetchone()["n"]
        if not total:
            conn.close()
            return [CheckResult(
                "dispatch_telemetry.kpi", "INFO",
                "dispatch_telemetry is empty — no dispatches recorded yet",
            )]

        results: list[CheckResult] = [CheckResult(
            "dispatch_telemetry.kpi", "INFO",
            f"{total} dispatch(es) recorded; aggregates below scoped to last "
            f"{min(total, _DISPATCH_KPI_RECENT_N)}",
        )]

        # Aggregates are scoped to the same recent-N window named in the summary
        # above (not all-time) — both must describe the same population or the
        # KPI surface silently drifts out of sync once the table exceeds N rows.
        recent_window = """SELECT * FROM dispatch_telemetry
                            ORDER BY recorded_at DESC LIMIT ?"""

        by_persona = conn.execute(
            f"""SELECT persona,
                      COUNT(*) AS n,
                      AVG(tokens) AS avg_tokens,
                      AVG(duration_ms) AS avg_duration_ms,
                      SUM(CASE WHEN token_source='exact' THEN 1 ELSE 0 END) AS exact_n
               FROM ({recent_window})
               GROUP BY persona
               ORDER BY n DESC""",
            (_DISPATCH_KPI_RECENT_N,),
        ).fetchall()
        for row in by_persona:
            avg_tokens = round(row["avg_tokens"]) if row["avg_tokens"] is not None else 0
            avg_ms = round(row["avg_duration_ms"]) if row["avg_duration_ms"] is not None else 0
            exact_ratio = f"{row['exact_n']}/{row['n']} exact"
            results.append(CheckResult(
                "dispatch_telemetry.by_persona", "INFO",
                f"{row['persona']}: n={row['n']} avg_tokens={avg_tokens} "
                f"avg_duration_ms={avg_ms} ({exact_ratio})",
            ))

        by_model = conn.execute(
            f"""SELECT COALESCE(model, 'unknown') AS model,
                      COUNT(*) AS n,
                      AVG(tokens) AS avg_tokens,
                      AVG(duration_ms) AS avg_duration_ms,
                      SUM(CASE WHEN token_source='exact' THEN 1 ELSE 0 END) AS exact_n
               FROM ({recent_window})
               GROUP BY model
               ORDER BY n DESC""",
            (_DISPATCH_KPI_RECENT_N,),
        ).fetchall()
        for row in by_model:
            avg_tokens = round(row["avg_tokens"]) if row["avg_tokens"] is not None else 0
            avg_ms = round(row["avg_duration_ms"]) if row["avg_duration_ms"] is not None else 0
            exact_ratio = f"{row['exact_n']}/{row['n']} exact"
            results.append(CheckResult(
                "dispatch_telemetry.by_model", "INFO",
                f"{row['model']}: n={row['n']} avg_tokens={avg_tokens} "
                f"avg_duration_ms={avg_ms} ({exact_ratio})",
            ))

        conn.close()
        return results
    except sqlite3.Error as exc:
        return [CheckResult(
            "dispatch_telemetry.kpi", "INFO",
            f"DB error reading dispatch_telemetry: {exc}",
        )]


# Subprocess timeout for the observability-report CLI probe below — the
# report itself is local sqlite reads + in-memory bus/journal wiring (no
# network, no b4 eval run), timed at ~0.2s warm; 10s leaves headroom for a
# cold `uv` resolve on the first call, matching the margin
# check_broker_mcp_boots/check_mcp_servers_boot leave over their own
# measured-fast subprocess probes.
_OBS_REPORT_TIMEOUT_S = 10


def check_observability_report(project_path: str) -> list[CheckResult]:
    """R5-T06 observability graduation (N58) — plan-gate accuracy + cost
    panels, the skills-actually-loaded panel, and a live bus/tracing
    structural probe, rendered from `broker.observability.report`.

    Shells out to `uv run python -m broker.observability.report` (the
    check_broker_mcp_boots subprocess-not-import convention: health.py runs
    under the ambient interpreter, not nexus-broker's own >=3.12 uv venv)
    rather than importing the module directly.

    Severity: SKIP when nexus-broker/ is absent or not yet substituted
    (mirrors check_broker_mcp_boots). FAIL only when the subprocess itself
    is broken (nonzero exit, timeout, malformed JSON, uv missing) — a
    genuinely dormant capability, same bar check_broker_mcp_boots already
    applies to broker boot. Every individual panel's own data-availability
    state (e.g. an empty project.db, a fresh install with no router
    decisions yet) renders as INFO, never FAIL — panels degrade
    gracefully by design (see `broker.observability.metrics`/`cost`), so an
    empty-but-working install must stay green here.
    """
    # Resolve to absolute BEFORE handing to the subprocess: the child's cwd
    # is broker_dir (below), so a relative project_path would silently
    # resolve against nexus-broker/ instead of the real project root inside
    # report.py's own Path(project_path) reads.
    project_path = str(Path(project_path).resolve())
    broker_dir = Path(project_path) / "nexus-broker"
    if not broker_dir.exists():
        return [CheckResult(
            "observability.report", "SKIP",
            "nexus-broker/ dir not found — skipping observability report",
        )]
    mcp_json = Path(project_path) / ".mcp.json"
    if mcp_json.exists():
        try:
            if "__INSTALL_ROOT__" in mcp_json.read_text(encoding="utf-8"):
                return [CheckResult(
                    "observability.report", "INFO",
                    "Broker not yet configured (install.sh substitution pending)",
                )]
        except OSError:
            pass

    cmd = [
        "uv", "run", "--quiet", "python", "-m", "broker.observability.report",
        "--project-path", str(project_path),
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(broker_dir), capture_output=True, text=True,
            timeout=_OBS_REPORT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return [CheckResult(
            "observability.report", "FAIL",
            f"observability report timed out after {_OBS_REPORT_TIMEOUT_S}s",
            "Check nexus-broker/src/broker/observability/ for a blocking call",
        )]
    except FileNotFoundError:
        return [CheckResult(
            "observability.report", "FAIL",
            "uv not found — cannot run observability report",
            "Ensure uv is installed and on PATH",
        )]
    if result.returncode != 0:
        return [CheckResult(
            "observability.report", "FAIL",
            f"observability report failed (rc={result.returncode})",
            (result.stderr or result.stdout or "")[:200],
        )]
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [CheckResult(
            "observability.report", "FAIL",
            f"observability report produced non-JSON stdout: {exc}",
            (result.stdout or "")[:200],
        )]

    results: list[CheckResult] = []
    plan_gate = report.get("plan_gate", {})
    accuracy = plan_gate.get("accuracy", {})
    if accuracy.get("available"):
        results.append(CheckResult(
            "observability.plan_gate_accuracy", "INFO",
            f"accuracy={accuracy['accuracy']} reject_rate={accuracy['reject_rate']} "
            f"(window={accuracy['window']})",
        ))
    else:
        results.append(CheckResult(
            "observability.plan_gate_accuracy", "INFO",
            accuracy.get("reason", "plan-gate accuracy unavailable"),
        ))
    cost_panel = report.get("cost", {})
    dispatch_cost = cost_panel.get("dispatch", {})
    if dispatch_cost.get("available"):
        results.append(CheckResult(
            "observability.cost", "INFO",
            f"total_tokens={dispatch_cost['total_tokens']} "
            f"estimated_cost_usd={dispatch_cost['estimated_cost_usd']} "
            f"(window={dispatch_cost['window']})",
        ))
    else:
        results.append(CheckResult(
            "observability.cost", "INFO",
            dispatch_cost.get("reason", "dispatch cost unavailable"),
        ))
    live_feed = report.get("live_feed", {})
    if live_feed.get("wired"):
        bus_stats = live_feed.get("bus", {})
        results.append(CheckResult(
            "observability.live_feed", "INFO",
            f"bus+skill_load_recorder+tracing wired live "
            f"(published_total={bus_stats.get('published_total')}, "
            f"trace_id={live_feed.get('probe_trace_id')})",
        ))
    else:
        results.append(CheckResult(
            "observability.live_feed", "WARN",
            "live-feed self-check did not report wired=true",
        ))
    return results


# ---------------------------------------------------------------------------
# CONFORMANCE check (DRIFT tier)
# ---------------------------------------------------------------------------

# Test seam: forces the detector import to fail even when the installer's
# stack_profile IS importable, so the target-safe skip path can be exercised on
# a Plexus box. Production never sets this.
_FORCE_NO_DETECTOR_ENV = "NEXUS_HEALTH_FORCE_NO_DETECTOR"


def _load_detect_stack() -> Callable[[Path], dict] | None:
    """Best-effort import of detect_stack from the installer's stack_profile.

    The detector ships PACKAGE-side at nexus-package/tools/stack_profile.py and
    is present only when health.py is being driven from the Plexus installer
    (cmd_registry_health). On a bare TARGET it is absent — the import is GUARDED
    and returns None so the conformance check degrades to a non-FAIL skip rather
    than exploding. None means 'no installer-side detector available here'.
    """
    if os.environ.get(_FORCE_NO_DETECTOR_ENV):
        return None
    tools_dir = Path.home() / "nexus-installer" / "nexus-package" / "tools"
    if not (tools_dir / "stack_profile.py").is_file():
        return None
    import importlib.util  # noqa: PLC0415

    try:
        spec = importlib.util.spec_from_file_location(
            "nexus_stack_profile", tools_dir / "stack_profile.py"
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, OSError, SyntaxError):
        return None
    detect = getattr(module, "detect_stack", None)
    if not callable(detect):
        return None
    return detect


def _jaccard(expected: list[str], actual: list[str]) -> float:
    """Jaccard similarity of two string lists; 1.0 when both are empty."""
    a = set(expected)
    b = set(actual)
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def check_conformance_stack_profile(project_path: str) -> list[CheckResult]:
    """Re-run detect_stack on an installed project and compare to its committed roster.

    The SAME-bug second detector: the install-time canary catches a mis-install
    as it happens; conformance catches an ALREADY-INSTALLED project whose layout
    has since outgrown its committed persona_set (the insites under-install class:
    a frontend now present but forge-ui never installed). Runs effective only
    Plexus-side, where the installer's stack_profile.detect_stack is co-located.

    Semantics:
      - detector unavailable (bare target) -> single SKIP (never FAIL).
      - no .memory/nexus-stack.json -> WARN (predates profile-aware install).
      - persona_set: expected − actual non-empty -> FAIL naming the missing
        personas; extra installed personas -> at most INFO.
      - socraticode_watched_prefixes: jaccard < 0.5 -> WARN (code-layout drift).
      - framework/language/db divergence -> INFO only.

    Report-only (v1): returns CheckResult(s); writes nothing to project_version_history.
    """
    name = "conformance.stack_profile"

    detect_stack = _load_detect_stack()
    if detect_stack is None:
        return [CheckResult(
            name, "SKIP",
            "conformance skipped — installer-side detector unavailable; "
            "run via Plexus registry health --drift",
        )]

    stack_path = Path(project_path) / ".memory" / "nexus-stack.json"
    if not stack_path.exists():
        return [CheckResult(
            name, "WARN",
            "no nexus-stack.json — project predates profile-aware install",
            "Re-run plexus-update to write a stack profile for this project",
        )]
    try:
        actual = json.loads(stack_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [CheckResult(
            name, "WARN",
            f"nexus-stack.json unreadable: {exc}",
        )]

    try:
        expected = detect_stack(Path(project_path))
    except (ValueError, OSError) as exc:
        return [CheckResult(
            name, "SKIP",
            f"conformance skipped — detect_stack could not profile project: {exc}",
        )]

    results: list[CheckResult] = []

    expected_personas = [str(p) for p in expected.get("persona_set", [])]
    actual_personas = _read_persona_set(project_path)
    if actual_personas is None:
        actual_personas = [str(p) for p in actual.get("persona_set", [])]

    missing = sorted(set(expected_personas) - set(actual_personas))
    extra = sorted(set(actual_personas) - set(expected_personas))
    if missing:
        surface = _conformance_surface_for(missing, expected)
        results.append(CheckResult(
            name, "FAIL",
            f"installed roster missing required persona(s): {', '.join(missing)}"
            + (f" — required by {surface}" if surface else ""),
            "Re-run plexus-update to install the personas the current layout requires",
        ))
    elif extra:
        results.append(CheckResult(
            name, "INFO",
            f"installed roster has extra persona(s) beyond detected: {', '.join(extra)}",
        ))

    expected_prefixes = [str(p) for p in expected.get("socraticode_watched_prefixes", [])]
    actual_prefixes = [str(p) for p in actual.get("socraticode_watched_prefixes", [])]
    if _jaccard(expected_prefixes, actual_prefixes) < 0.5:
        results.append(CheckResult(
            name, "WARN",
            "socraticode_watched_prefixes drift — committed prefixes diverge "
            "from current code layout",
            f"expected {expected_prefixes} vs installed {actual_prefixes}",
        ))

    for surface_key in ("frontend", "backend"):
        exp_fw = expected.get(surface_key, {}).get("framework")
        act_fw = actual.get(surface_key, {}).get("framework")
        if exp_fw != act_fw:
            results.append(CheckResult(
                name, "INFO",
                f"{surface_key}.framework evolved: installed {act_fw!r} vs detected {exp_fw!r}",
            ))
    exp_db = expected.get("data", {}).get("db")
    act_db = actual.get("data", {}).get("db")
    if exp_db != act_db:
        results.append(CheckResult(
            name, "INFO",
            f"data.db evolved: installed {act_db!r} vs detected {exp_db!r}",
        ))

    if not results:
        results.append(CheckResult(
            name, "PASS",
            "installed roster covers detected stack; watched prefixes aligned",
        ))
    return results


# Maps a required persona to the stack surface that pulls it in, so a FAIL names
# WHY the persona is needed (the under-install diagnostic). Best-effort: an
# unmapped persona simply contributes no surface phrase.
_PERSONA_SURFACE: dict[str, str] = {
    "forge": "a detected frontend",
    "forge-ui": "a detected frontend",
    "forge-ui-pro": "a detected frontend",
    "forge-wire": "a detected backend/server layer",
    "forge-wire-pro": "a detected backend/server layer",
    "quill-ts": "a detected TS surface",
    "atlas": "a detected database layer",
    "pipeline": "a detected data pipeline",
    "pipeline-data": "a detected data pipeline",
    "pipeline-data-pro": "a detected data pipeline",
    "pipeline-async": "detected async workers",
    "pipeline-async-pro": "detected async workers",
    "hermes": "a detected MCP server",
    "quill-py": "a detected Python surface",
}


def _conformance_surface_for(missing: list[str], expected: dict) -> str:
    """Human phrase naming the stack surface(s) that require the missing personas."""
    surfaces: list[str] = []
    for persona in missing:
        surface = _PERSONA_SURFACE.get(persona)
        if surface and surface not in surfaces:
            surfaces.append(surface)
    return "; ".join(surfaces)


# ---------------------------------------------------------------------------
# DRIFT checks
# ---------------------------------------------------------------------------

def check_drift_agents(project_path: str) -> list[CheckResult]:
    """Compare project agents/ to nexus-package agents/ — report byte diffs as INFO."""
    plexus_agents = Path.home() / "nexus-installer" / "nexus-package" / ".claude" / "agents"
    project_agents = Path(project_path) / ".claude" / "agents"
    return _drift_check("drift.agents", project_agents, plexus_agents)


def check_drift_hooks(project_path: str) -> list[CheckResult]:
    """Compare project hooks/ to nexus-package hooks/ — report diffs as INFO."""
    plexus_hooks = Path.home() / "nexus-installer" / "nexus-package" / ".claude" / "hooks"
    project_hooks = Path(project_path) / ".claude" / "hooks"
    exclude = {".env.template", "tests", ".bak"}
    return _drift_check("drift.hooks", project_hooks, plexus_hooks, exclude=exclude)


def check_drift_skills(project_path: str) -> list[CheckResult]:
    """Compare project skills/ to nexus-package skills/ — project-only = INFO custom-skills."""
    plexus_skills = Path.home() / "nexus-installer" / "nexus-package" / ".claude" / "skills"
    project_skills = Path(project_path) / ".claude" / "skills"
    return _drift_check("drift.skills", project_skills, plexus_skills)


def _drift_check(
    check_name: str,
    project_dir: Path,
    package_dir: Path,
    exclude: set[str] | None = None,
) -> list[CheckResult]:
    """Generic drift comparison between project dir and package canonical dir."""
    if not project_dir.exists():
        return [CheckResult(check_name, "SKIP", f"{project_dir} not found")]
    if not package_dir.exists():
        return [CheckResult(check_name, "SKIP", f"Package canonical dir {package_dir} not found")]

    exclude = exclude or set()
    changed: list[str] = []
    project_only: list[str] = []

    pkg_files = {
        f.name: f for f in package_dir.iterdir()
        if not f.is_dir() and f.name not in exclude and not any(
            ex in f.name for ex in exclude
        )
    }
    proj_files = {
        f.name: f for f in project_dir.iterdir()
        if not f.is_dir() and f.name not in exclude and not any(
            ex in f.name for ex in exclude
        )
    }

    for fname, pkg_file in pkg_files.items():
        if fname in proj_files and proj_files[fname].read_bytes() != pkg_file.read_bytes():
            changed.append(fname)
        # if not in project, not reported as missing here (that's canonical_inventory)

    for fname in proj_files:
        if fname not in pkg_files:
            project_only.append(fname)

    results: list[CheckResult] = []
    if changed:
        results.append(CheckResult(
            check_name, "INFO",
            f"{len(changed)} file(s) differ from canonical package",
            ", ".join(changed[:10]),
        ))
    if project_only:
        results.append(CheckResult(
            check_name, "INFO",
            f"{len(project_only)} project-only file(s) (custom additions)",
            ", ".join(project_only[:10]),
        ))
    if not results:
        results.append(CheckResult(
            check_name, "INFO",
            "No drift from canonical package",
        ))
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_checks(
    project_path: str,
    *,
    runtime: bool = True,
    drift: bool = False,
    embed_check: bool = True,
    leak_check: bool = True,
) -> HealthReport:
    """Run all health checks and return a HealthReport.

    Args:
        project_path: Absolute path to the project root.
        runtime: If True, run RUNTIME-tier checks (broker, hooks, DB, embeddings).
        drift: If True, run DRIFT-tier checks comparing to canonical package.
        embed_check: If True, include embeddings endpoint reachable check.
        leak_check: If False, skip check_leaks_prior_project (O(files) scan).
            Fleet callers should pass False — leak scan is install-time-only.
    """
    t0 = time.monotonic()
    version = _get_project_version(project_path)
    session_id = _get_open_session_id(project_path)

    static_checks: list[Callable[[str], list[CheckResult]]] = [
        check_agents_canonical_inventory,
        check_agents_skill_declarations,
        check_hooks_settings_resolves,
        check_hooks_executable,
        check_mcp_config_valid,
        check_codex_surface,
        check_version_matches_registry,
        check_ledger_present_and_consistent,
        check_schema_vec_dim_aligned,
        check_vec_memory_available,
        check_core_tables_present,
        check_broker_static_structure,
    ]

    runtime_checks: list[Callable[[str], list[CheckResult]]] = [
        check_broker_mcp_boots,
        check_hooks_execute_with_stub,
        check_db_write_probe,
    ]
    if embed_check:
        runtime_checks.append(check_embeddings_endpoint_reachable)
    runtime_checks.append(check_prism_mcp_boots)
    # Registry-driven import probe — covers EVERY -m server in .mcp.json (incl.
    # ones the hardcoded broker/prism checks miss, e.g. nexus-vault), so a
    # registered-but-broken server can no longer report a false-green install.
    runtime_checks.append(check_mcp_servers_boot)
    # R5-T06 (N58): spawns its own `uv run` subprocess (like the two checks
    # above), so it belongs in RUNTIME, not SESSION — never adds subprocess
    # latency to the --no-runtime SessionStart banner path.
    runtime_checks.append(check_observability_report)

    session_checks: list[Callable[[str], list[CheckResult]]] = [
        check_heartbeat_recent,
        check_daemon_liveness,
        check_router_recent_decisions,
        check_session_has_open,
        check_dispatch_telemetry_kpi,
    ]

    drift_checks: list[Callable[[str], list[CheckResult]]] = [
        check_drift_agents,
        check_drift_hooks,
        check_drift_skills,
        check_conformance_stack_profile,
    ]

    all_results: list[CheckResult] = []
    for fn in static_checks:
        all_results.extend(fn(project_path))

    # Leak scan is O(files × denylist_terms); skip in fleet mode to avoid hang.
    if leak_check:
        all_results.extend(check_leaks_prior_project(project_path))
    else:
        all_results.append(CheckResult(
            name="leaks.prior_project",
            severity="INFO",
            message="leak scan skipped (fleet mode)",
            hint="Run per-project health check or pass --leak-check to scan fully",
        ))

    if runtime:
        for fn in runtime_checks:
            all_results.extend(fn(project_path))

    for fn in session_checks:
        all_results.extend(fn(project_path))

    if drift:
        for fn in drift_checks:
            all_results.extend(fn(project_path))

    elapsed = time.monotonic() - t0
    return HealthReport(
        project_path=project_path,
        version=version,
        session_id=session_id,
        elapsed=elapsed,
        results=all_results,
    )

"""Seam test: render_install into a temp target, assert the rendered surface
(TASK-118).

Real integration boundary: the actual `render_install()` template-rendering
pipeline (nexus-package/tools/render_install.py) driven end to end against
the REAL nexus-package/ source tree and a REAL profile
(profiles/examples/qa-dashboard.json) into a fresh tmp_path target — nothing
stubbed. Assertions are structural properties of the rendered surface (a
round-tripped stack profile, no unresolved __TOKEN__ placeholders anywhere in
the delivered tree, every JSON file the renderer writes actually parses,
every shipped persona in the resolved manifest lands on disk) rather than
brittle prose matches, per DEC-068 "assert properties, not prose" — the
renderer's prose changes across releases; these structural guarantees do not.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_NEXUS_PACKAGE = Path(__file__).resolve().parents[3] / "nexus-package"
_TOOLS_DIR = _NEXUS_PACKAGE / "tools"

if not _NEXUS_PACKAGE.is_dir():
    pytest.skip(
        "nexus-package/ absent — this tree is an installed target, not the Plexus "
        "meta-repo; this seam only has a real render_install() source tree to drive "
        "where nexus-package/ ships",
        allow_module_level=True,
    )

if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from render_install import render_install  # noqa: E402
from stack_profile import _TOKEN_PATHS, resolve_file_manifest  # noqa: E402

# The EXACT set of tokens render_template actually resolves (stack_profile.py's
# own substitution map) — not a generic `__[A-Z_]+__` sweep, which would also
# flag doc prose that legitimately uses that pattern as illustrative syntax
# (e.g. docs/STACK-PROFILE.md's own `__NAME__` example) and the two
# INSTALL-TIME tokens (`__INSTALL_ROOT__`/`__ARIZE_PROJECT_NAME__`) render_install
# deliberately passes through untouched for install.sh's later pass.
_KNOWN_TOKENS = tuple(sorted(_TOKEN_PATHS))
_TEXT_SUFFIXES = frozenset({".md", ".json", ".sh", ".py", ".toml", ".yml", ".yaml", ".txt"})


def _load_qa_profile() -> dict:
    path = _NEXUS_PACKAGE / "profiles" / "examples" / "qa-dashboard.json"
    return json.loads(path.read_text())


def _iter_rendered_text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in _TEXT_SUFFIXES:
            yield path


def test_seam_render_install_produces_valid_round_tripped_surface(tmp_path: Path) -> None:
    """GIVEN the real qa-dashboard profile, WHEN render_install renders the
    real nexus-package/ template tree into a fresh temp target, THEN the
    written stack profile round-trips byte-for-JSON-equal, no __TOKEN__
    placeholder residue survives ANYWHERE in the rendered tree, every JSON
    file on disk actually parses, and every persona the manifest says to ship
    is present as a real .claude/agents/<name>.md file."""
    profile = _load_qa_profile()
    dest = tmp_path / "rendered"
    dest.mkdir()

    render_install(profile, _NEXUS_PACKAGE, dest)

    # --- round-tripped stack profile ---
    stack_json = dest / ".memory" / "nexus-stack.json"
    assert stack_json.is_file(), "render_install must write .memory/nexus-stack.json"
    assert json.loads(stack_json.read_text()) == profile

    # --- no unresolved KNOWN placeholder tokens anywhere in the rendered surface ---
    residual: dict[str, list[str]] = {}
    for f in _iter_rendered_text_files(dest):
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = [tok for tok in _KNOWN_TOKENS if tok in text]
        if hits:
            residual[str(f.relative_to(dest))] = hits
    assert residual == {}, f"unresolved __TOKEN__ placeholders survived rendering: {residual}"

    # --- every JSON file the renderer wrote is actually valid JSON ---
    bad_json: list[str] = []
    for f in dest.rglob("*.json"):
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            bad_json.append(str(f.relative_to(dest)))
    assert bad_json == [], f"render_install wrote invalid JSON: {bad_json}"

    # --- roster coverage: every manifest-'ship' persona with a source .md lands on disk ---
    manifest = resolve_file_manifest(profile)
    src_agents_dir = _NEXUS_PACKAGE / ".claude" / "agents"
    dest_agents_dir = dest / ".claude" / "agents"
    missing = [
        name
        for name in manifest["ship"]
        if (src_agents_dir / f"{name}.md").is_file()
        and not (dest_agents_dir / f"{name}.md").is_file()
    ]
    assert missing == [], f"shipped personas missing from the rendered surface: {missing}"

    # --- an omitted persona with a source .md must NOT leak into the rendered surface ---
    omitted_with_source = [
        name for name in manifest["omit"] if (src_agents_dir / f"{name}.md").is_file()
    ]
    assert omitted_with_source, "fixture invalid: qa-dashboard must omit >=1 real persona"
    leaked = [n for n in omitted_with_source if (dest_agents_dir / f"{n}.md").is_file()]
    assert leaked == [], f"omitted personas leaked into the rendered surface: {leaked}"

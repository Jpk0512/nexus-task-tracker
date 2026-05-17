"""
router_core.py — importable library for the Nexus single-stage router.

Provides:
  build_persona_enum(agents_dir)         — dynamic list from .claude/agents/*.md
  build_schema(personas)                 — JSON schema with dynamic enum
  call_qwen(user_msg, agents_dir, ...)   — LM Studio HTTP call; returns dict or None

Default model: granite-4.1-3b (override via _HOOK_ROUTER_MODEL env var).
"""

import json
import os
import re
from pathlib import Path
from typing import Any

QWEN_URL = os.environ.get("_HOOK_QWEN_URL", "http://127.0.0.1:1234/v1/chat/completions")
# Configurable via _HOOK_ROUTER_MODEL env var — update when switching models in LM Studio
ROUTER_MODEL = os.environ.get("_HOOK_ROUTER_MODEL", "granite-4.1-3b")
QWEN_TIMEOUT = float(os.environ.get("_HOOK_ROUTER_TIMEOUT", "10.0"))
QWEN_MAX_TOKENS = 256
DIFFICULTIES = ["trivial", "simple", "standard", "complex"]


def _parse_frontmatter_field(content: str, field: str) -> str | None:
    """Extract a single YAML frontmatter field value. Returns None if not found."""
    fm_match = re.search(r'^\---\s*\n(.*?)\n\---', content, re.DOTALL | re.MULTILINE)
    if not fm_match:
        return None
    fm = fm_match.group(1)
    field_match = re.search(rf'^{re.escape(field)}:\s*"?([^"\n]+)"?', fm, re.MULTILINE)
    return field_match.group(1).strip() if field_match else None


def _read_persona_descriptions(agents_dir: str) -> str:
    """Return formatted persona list from agent frontmatter.

    Always injects 'meta' first (orchestrator-internal routing).
    Excludes _-prefixed files, DOMAIN-AGENT-TEMPLATE, and -pro escalation variants.
    """
    dir_path = Path(agents_dir)
    lines = ["- meta: Status / ops / discussion — Nexus handles directly, no dispatch"]

    if not dir_path.is_dir():
        return "\n".join(lines)

    for md_file in sorted(dir_path.glob("*.md")):
        stem = md_file.stem
        if (
            stem.startswith("_")
            or stem == "DOMAIN-AGENT-TEMPLATE"
            or stem.endswith("-pro")
        ):
            continue

        desc = ""
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
            # Fast YAML frontmatter parse for description field
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    frontmatter = text[3:end]
                    for line in frontmatter.splitlines():
                        if line.startswith("description:"):
                            raw = line[len("description:"):].strip().strip('"').strip("'")
                            desc = raw
                            break
        except OSError:
            pass

        # Strip "Nexus-dispatched only" boilerplate
        for marker in ("(Nexus-dispatched only", "Nexus-dispatched only"):
            if marker in desc:
                desc = desc[: desc.index(marker)].strip(" —-(")
                break

        # Cap at 100 chars to prevent any single persona from dominating attention
        if len(desc) > 100:
            desc = desc[:97] + "..."

        lines.append(f"- {stem}: {desc}" if desc else f"- {stem}")

    return "\n".join(lines)


def _read_skill_names(skills_dir: str) -> list[str]:
    """Return sorted list of skill names from subdirectory names in skills_dir."""
    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return []
    return sorted(
        p.name for p in skills_path.iterdir() if p.is_dir() and not p.name.startswith("_")
    )


def _build_system_prompt(agents_dir: str, skills_dir: str) -> str:
    """Build routing system prompt dynamically from project's agent/skill files."""
    personas_block = _read_persona_descriptions(agents_dir)
    skills_list = _read_skill_names(skills_dir)

    # Group skills by persona prefix for better model attention
    skill_groups: dict[str, list[str]] = {}
    ungrouped: list[str] = []
    for skill in skills_list:
        matched = False
        for skill_prefix in [
            "forge-ui", "forge-wire", "pipeline-data", "pipeline-async",
            "quill-ts", "quill-py", "atlas", "hermes", "lens", "palette",
            "scout", "meta",
        ]:
            if skill.startswith(skill_prefix):
                skill_groups.setdefault(skill_prefix, []).append(skill)
                matched = True
                break
        if not matched:
            ungrouped.append(skill)

    if skill_groups or ungrouped:
        skills_lines = ["REQUIRED_SKILLS (pick from the persona's group, zero-to-many):"]
        for prefix, skills in sorted(skill_groups.items()):
            skills_lines.append(f"  {prefix}: {', '.join(skills)}")
        if ungrouped:
            skills_lines.append(f"  (shared): {', '.join(ungrouped)}")
        skills_section = "\n".join(skills_lines)
    else:
        skills_section = "REQUIRED_SKILLS: [] (no skills configured)"

    examples = """EXAMPLES (calibrate from these):
{"persona":"meta","difficulty":"trivial","confidence":0.95,"required_skills":[],"tdd_required":false}  // "what's next?" / "why did X happen?"
{"persona":"scout","difficulty":"simple","confidence":0.88,"required_skills":["codebase-exploration"],"tdd_required":false}  // "this build is failing — investigate"
{"persona":"lens","difficulty":"trivial","confidence":0.90,"required_skills":["verification-protocols"],"tdd_required":false}  // "validate the last change"
{"persona":"meta","difficulty":"complex","confidence":0.90,"required_skills":[],"tdd_required":false}  // "ship all open tasks" / "what's the status?" """  # noqa: E501

    return f"""You are a routing classifier for the Nexus orchestrator. Given a user request, emit ONE JSON object.

PERSONAS (pick exactly one):
{personas_block}

DIFFICULTY:
- trivial: ≤1 file, ≤5 LOC, no logic change
- simple: ≤2 files, no design decision
- standard: 3-10 files, single domain
- complex: cross-domain, multi-persona, planning required

{skills_section}

TDD_REQUIRED: true if production code will be written that needs tests; false otherwise.
CONFIDENCE: fractional 0.0-1.0. Use 0.95 when obvious, 0.70-0.85 when uncertain. Never output an integer.

{examples}"""


def _normalize_confidence(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize confidence from 0-100 integer range to 0.0-1.0 float if needed."""
    conf = parsed.get("confidence")
    if isinstance(conf, (int, float)) and conf > 1.0:
        parsed["confidence"] = conf / 100.0
    return parsed


def build_persona_enum(agents_dir: str) -> list[str]:
    """Return sorted persona names from .md files in agents_dir.

    Excludes _-prefixed stems, DOMAIN-AGENT-TEMPLATE, and -pro variants.
    Appends 'meta' at the end (not a real agent file — Nexus handles meta routing).
    """
    personas = sorted(
        p.stem
        for p in Path(agents_dir).glob("*.md")
        if not p.stem.startswith("_")
        and p.stem != "DOMAIN-AGENT-TEMPLATE"
        and not p.stem.endswith("-pro")
    )
    personas.append("meta")
    return personas


def build_schema(personas: list[str]) -> dict[str, Any]:
    """Return a JSON schema with dynamic persona enum for structured output."""
    return {
        "type": "object",
        "required": ["persona", "difficulty", "confidence", "required_skills", "tdd_required"],
        "properties": {
            "persona": {"type": "string", "enum": personas},
            "difficulty": {"type": "string", "enum": DIFFICULTIES},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "required_skills": {"type": "array", "items": {"type": "string"}},
            "tdd_required": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


def call_qwen(
    user_msg: str,
    agents_dir: str | None = None,
    skills_dir: str | None = None,
) -> dict[str, Any] | None:
    """
    Call the router model via LM Studio. Returns parsed dict on success, None on any failure.

    Respects test env vars:
      _MOCK_QWEN_RESPONSE       — JSON string to return instead of real HTTP call
      _MOCK_QWEN_CONNECT_ERROR  — if set, simulate connection failure
    """
    if os.environ.get("_MOCK_QWEN_CONNECT_ERROR"):
        return None

    mock_resp = os.environ.get("_MOCK_QWEN_RESPONSE")
    if mock_resp is not None:
        try:
            return _normalize_confidence(json.loads(mock_resp))
        except json.JSONDecodeError:
            return None

    try:
        import urllib.request

        # Path resolution: hook lives at .claude/hooks/router_core.py
        # parent = .claude/hooks/, parent.parent = .claude/, + "agents" = .claude/agents/
        resolved_agents_dir = agents_dir or str(
            Path(__file__).parent.parent / "agents"
        )
        resolved_skills_dir = skills_dir or str(
            Path(__file__).parent.parent / "skills"
        )

        personas = build_persona_enum(resolved_agents_dir)
        schema = build_schema(personas)
        system_prompt = _build_system_prompt(resolved_agents_dir, resolved_skills_dir)

        payload = json.dumps({
            "model": ROUTER_MODEL,
            "temperature": 0.0,
            "max_tokens": QWEN_MAX_TOKENS,
            "seed": 42,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "routing_classification",
                    "strict": True,
                    "schema": schema,
                },
            },
        }).encode()

        req = urllib.request.Request(
            QWEN_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        import socket
        socket.setdefaulttimeout(QWEN_TIMEOUT)
        with urllib.request.urlopen(req, timeout=QWEN_TIMEOUT) as resp:
            body = json.loads(resp.read())

        content = body["choices"][0]["message"]["content"]
        return _normalize_confidence(json.loads(content))

    except Exception:
        return None

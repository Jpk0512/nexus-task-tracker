#!/usr/bin/env python3
"""UserPromptSubmit hook — single-stage Qwen router (Phase E1)."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Threshold 0.85 → 0.70: Qwen outputs confidence in 0.70-0.85 band for
# correct-but-not-obvious routing; 0.85 was leaving all useful signals on the floor.
# Override via _HOOK_THRESHOLD_LLM env var for per-project tuning.
THRESHOLD_LLM = float(os.environ.get("_HOOK_THRESHOLD_LLM", "0.70"))
HOOKS_DIR = Path(__file__).parent


def _files_dir() -> Path:
    override = os.environ.get("_HOOK_MEMORY_FILES_DIR")
    if override:
        return Path(override)
    return HOOKS_DIR.parent.parent / ".memory" / "files"


def _shadow_personas() -> set[str]:
    # Env var takes precedence over JSON file (enables testing without touching disk)
    env_val = os.environ.get("_HOOK_SHADOW_PERSONAS")
    if env_val is not None:
        return {p.strip() for p in env_val.split(",") if p.strip()}

    shadow_path = _files_dir() / "router_shadow_personas.json"
    try:
        data = json.loads(shadow_path.read_text())
        return set(data.get("shadow", []))
    except Exception:
        # No shadow file or unreadable → all personas live (fail-open)
        return set()


def _append_jsonl(path: Path, record: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
        user_msg = payload.get("prompt", "")
    except Exception:
        sys.exit(0)

    start_ns = time.monotonic_ns()

    try:
        sys.path.insert(0, str(HOOKS_DIR))
        from router_core import call_qwen  # type: ignore[import]

        agents_dir = str(HOOKS_DIR.parent / ".claude" / "agents")
        result = call_qwen(user_msg, agents_dir=agents_dir)
    except Exception:
        result = None

    latency_ms = (time.monotonic_ns() - start_ns) / 1_000_000

    files_dir = _files_dir()
    now_iso = datetime.now(timezone.utc).isoformat()

    if result is None or result.get("confidence", 0.0) < THRESHOLD_LLM:
        decision = "fallthrough"

        _append_jsonl(
            files_dir / "hook_heartbeat.jsonl",
            {"hook": "router", "ts": now_iso, "decision": decision},
        )
        if result is not None:
            _append_jsonl(
                files_dir / "router_decisions.jsonl",
                {
                    "timestamp": now_iso,
                    "qwen_persona": result.get("persona", "unknown"),
                    "qwen_confidence": result.get("confidence", 0.0),
                    "decision": decision,
                    "latency_ms": latency_ms,
                },
            )
        sys.exit(0)

    persona = result["persona"]
    confidence = result["confidence"]
    difficulty = result.get("difficulty", "standard")
    required_skills = result.get("required_skills", [])
    tdd_required = result.get("tdd_required", False)

    shadow_set = _shadow_personas()
    is_shadow = persona in shadow_set

    if is_shadow:
        decision = "shadow"
        tag_name = "routing-shadow"
    else:
        decision = "prefill"
        tag_name = "routing-pre-fill"

    skills_str = ", ".join(required_skills) if required_skills else "none"
    tag_attrs = (
        f'persona="{persona}" difficulty="{difficulty}" '
        f'confidence="{confidence:.2f}" tdd="{str(tdd_required).lower()}" '
        f'required_skills="{skills_str}"'
    )
    additional_context = (
        f"<{tag_name} {tag_attrs}>"
        f"Route to {persona} (difficulty={difficulty}, tdd={tdd_required}, "
        f"skills={skills_str})"
        f"</{tag_name}>"
    )

    _append_jsonl(
        files_dir / "hook_heartbeat.jsonl",
        {"hook": "router", "ts": now_iso, "decision": decision},
    )
    _append_jsonl(
        files_dir / "router_decisions.jsonl",
        {
            "timestamp": now_iso,
            "qwen_persona": persona,
            "qwen_confidence": confidence,
            "decision": decision,
            "latency_ms": latency_ms,
        },
    )

    print(json.dumps({"hookSpecificOutput": {"additionalContext": additional_context}}))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)

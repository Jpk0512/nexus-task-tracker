"""Tiered distill router.

Routes distill sub-tasks between a local LM Studio chat model (mechanical
work: cleanup, summarize, tag) and `claude --print` (judgment: relevance,
cross-note, full distill).

Config: `research/00-meta/distill-routing.yaml` — see `config.py` for schema.

Critical: LM Studio's `/v1/models` is LRU-ordered and includes non-chat
models (privacy-filter, intent-classification, embedding models). The
selector applies `skip_model_patterns` from config and prefers the env-pinned
`LM_STUDIO_CHAT_MODEL` over LRU order.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from broker.vault.config import RoutingConfig, TierConfig, load_routing_config

# Task-specific system prompts. Mechanical tasks → local; judgment → claude.
_TASK_PROMPTS: dict[str, str] = {
    "cleanup_clip": (
        "You clean web-clipped text. Strip navigation, ads, share buttons, "
        "cookie banners, related-article boilerplate. Preserve the article "
        "body verbatim — do not paraphrase. Output ONLY the cleaned text."
    ),
    "summarize_bullets": (
        "Summarize the input into 3 to 7 bullet points. One bullet per "
        "distinct idea. No preamble. Output ONLY the bullets, one per line, "
        "each prefixed with '- '."
    ),
    "extract_tags": (
        "Extract 3 to 7 short tag words (kebab-case) describing the input. "
        "Output ONLY a JSON array of strings, no prose."
    ),
    "extract_title": (
        "Extract or synthesize a concise title (under 70 chars) for the input. "
        "Output ONLY the title, no quotes, no markdown."
    ),
    "extract_excerpt": (
        "Write a 1 to 2 sentence excerpt (under 240 chars) summarizing the "
        "input. Output ONLY the excerpt."
    ),
    "relevance_score": (
        "Score the input's relevance to the user's Nexus / second-brain "
        "project on a 1-5 integer scale. 1 = unrelated, 5 = directly applicable. "
        "Output ONLY a single JSON object: {\"score\": <int>, \"reason\": <str>}."
    ),
    "cross_note_connect": (
        "Suggest wikilink connections from the input to other notes the user "
        "might already have. Output ONLY a JSON array of objects: "
        "[{\"wikilink\": \"[[name]]\", \"reason\": \"...\"}]."
    ),
    "distill_full": (
        "You are the distill-note skill. Produce strict JSON conforming to "
        ".claude/skills/distill-note/distill-note.schema.json — every claim "
        "carries verbatim quote-span evidence (B1 contract). Output ONLY the JSON."
    ),
    "repo_analyzer_stage": (
        "Run the requested repo-analyzer pipeline stage. Output ONLY the "
        "structured JSON the stage spec demands."
    ),
}


class DistillRouterError(RuntimeError):
    """Surfaces config / backend errors that should halt the caller."""


class DistillRouter:
    """Tiered router for distill sub-tasks.

    Lazy: backends are not pinged at construction time. Each call decides
    its tier from the config and dispatches.
    """

    def __init__(
        self,
        config_path: Path,
        *,
        http_client: httpx.Client | None = None,
        subprocess_run=subprocess.run,
    ):
        self._config: RoutingConfig = load_routing_config(config_path)
        self._http = http_client or httpx.Client(timeout=60.0)
        self._owns_http = http_client is None
        self._subprocess_run = subprocess_run
        self._cached_local_model: str | None = None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # --- public API -------------------------------------------------------

    def distill(
        self,
        task: str,
        content: str,
        kind: str = "default",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Dispatch (task, kind) to its configured tier.

        Returns a dict: {result, tier, model, cost_usd_informational, duration_ms}.
        """
        if task not in _TASK_PROMPTS:
            raise DistillRouterError(f"unknown task: {task!r}")
        tier_name = self._config.resolve_tier(task, kind=kind)
        tier = self._config.tiers.get(tier_name)
        if tier is None:
            raise DistillRouterError(
                f"tier {tier_name!r} resolved for task={task!r} kind={kind!r} "
                "but not defined in config.tiers"
            )

        started = time.monotonic()
        if tier.backend == "lm_studio":
            payload = self._call_local(task, content, tier=tier, **kwargs)
        elif tier.backend == "claude_print":
            payload = self._call_judgment(task, content, tier=tier, **kwargs)
        else:
            raise DistillRouterError(f"unknown backend: {tier.backend!r}")
        duration_ms = int((time.monotonic() - started) * 1000)

        return {
            "result": payload["result"],
            "tier": tier_name,
            "model": payload["model"],
            "cost_usd_informational": payload.get("cost_usd_informational", 0.0),
            "duration_ms": duration_ms,
        }

    # --- local backend (LM Studio) ---------------------------------------

    def _select_local_chat_model(self, tier: TierConfig) -> str:
        """Pick a chat model from `/v1/models`, applying skip patterns + env pin.

        Order of preference:
        1. `os.environ[tier.chat_model_env]` if set AND present in /v1/models
        2. `tier.chat_model_default` if set AND present in /v1/models
        3. First model not matching any skip pattern
        """
        if self._cached_local_model is not None:
            return self._cached_local_model

        if tier.endpoint is None:
            raise DistillRouterError("local tier missing endpoint")
        r = self._http.get(f"{tier.endpoint}/models", timeout=tier.timeout_s)
        r.raise_for_status()
        body = r.json()
        ids = [m["id"] for m in body.get("data", [])]

        skip_regexes = [re.compile(p) for p in tier.skip_model_patterns]

        def is_chat(model_id: str) -> bool:
            return not any(rx.search(model_id) for rx in skip_regexes)

        # 1. Env pin
        if tier.chat_model_env:
            pinned = os.environ.get(tier.chat_model_env)
            if pinned and pinned in ids and is_chat(pinned):
                self._cached_local_model = pinned
                return pinned

        # 2. Config default
        if tier.chat_model_default and tier.chat_model_default in ids and is_chat(
            tier.chat_model_default
        ):
            self._cached_local_model = tier.chat_model_default
            return tier.chat_model_default

        # 3. First surviving model
        for mid in ids:
            if is_chat(mid):
                self._cached_local_model = mid
                return mid

        raise DistillRouterError(
            "no chat-model survived skip_model_patterns from /v1/models — "
            f"loaded: {ids!r}"
        )

    def _call_local(
        self,
        task: str,
        content: str,
        *,
        tier: TierConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        model = self._select_local_chat_model(tier)
        system_prompt = _TASK_PROMPTS[task]
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": kwargs.get("temperature", 0.2),
            "max_tokens": kwargs.get("max_tokens", 1024),
        }
        r = self._http.post(
            f"{tier.endpoint}/chat/completions",
            json=body,
            timeout=tier.timeout_s,
        )
        r.raise_for_status()
        payload = r.json()
        text = payload["choices"][0]["message"]["content"]
        return {
            "result": text,
            "model": model,
            "cost_usd_informational": 0.0,  # local = free
        }

    # --- judgment backend (claude --print) -------------------------------

    def _call_judgment(
        self,
        task: str,
        content: str,
        *,
        tier: TierConfig,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not tier.binary:
            raise DistillRouterError("judgment tier missing binary path")
        system_prompt = _TASK_PROMPTS[task]
        # claude --print takes the prompt as a positional arg; system prompt
        # is prepended into the user message via --append-system-prompt.
        cmd = [
            tier.binary,
            "--print",
            "--output-format",
            "json",
            "--model",
            tier.model or "sonnet",
            "--append-system-prompt",
            system_prompt,
            content,
        ]
        proc = self._subprocess_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=tier.timeout_s,
        )
        if proc.returncode != 0:
            raise DistillRouterError(
                f"claude --print failed (rc={proc.returncode}): {proc.stderr[:400]}"
            )
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise DistillRouterError(
                f"claude --print returned non-JSON: {proc.stdout[:400]!r}"
            ) from exc

        # `claude --print --output-format json` returns an envelope with a
        # `result` field carrying the model's text output.
        result_text = parsed.get("result", "")
        cost = float(parsed.get("total_cost_usd", 0.0) or 0.0)
        model = parsed.get("model") or tier.model or "sonnet"
        return {
            "result": result_text,
            "model": model,
            "cost_usd_informational": cost,
        }

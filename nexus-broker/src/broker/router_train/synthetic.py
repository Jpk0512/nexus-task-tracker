"""WF-C / WF-G: Synthetic augmentation — balanced paraphrase + targeted contrastive corpus.

generate_contrastive() (WF-G) produces HARD examples for confusable persona groups
and starved classes.  For confusable pairs (forge-ui/forge-wire, hermes/no-dispatch)
it emits matched pairs with discriminative features so the boundary is learnable.
For starved classes (atlas, pipeline-async, quill-ts, quill-py) it produces diverse
realistic in-domain requests.  label_source='synthetic_contrastive', confidence=0.5.

generate_synthetic() takes the real labeled pairs, identifies eligible starved
personas (those below FLOOR), and calls generate_fn(persona, seeds, n) to produce
n new prompt strings. The result is a list of synthetic pairs ready to union with
real pairs via collect_labeled_pairs(include_synthetic=True).

Design choices:
- generate_fn is INJECTABLE for deterministic testing (see tests/router_train/test_synthetic.py).
- Default generate_fn (_claude_generate) uses 'claude --print' with batched calls
  (~15-20 prompts per call).  Falls back to _template_generate if the LLM is
  unavailable/slow.
- DEDUP: any synthetic prompt whose sha256 hash collides with a real or already-seen
  synthetic hash is dropped (real always wins).
- seed_prompt_hash (provenance): each synthetic pair records which real seed it was
  derived from (round-robin over the persona's real prompts).
- Output is deterministic when the injected generate_fn is deterministic (same
  input -> same output); _claude_generate is non-deterministic by nature.
"""
from __future__ import annotations

import json
import logging
import re as _re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from broker.router_train.aggregate import prompt_hash
from broker.router_train.transcript import is_genuine_user_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (contract-pinned — must match test_synthetic.py)
# ---------------------------------------------------------------------------

QUERY_ROUTABLE_ELIGIBLE: frozenset[str] = frozenset(
    {
        "scout",
        "forge-ui",
        "forge-wire",
        "pipeline-data",
        "pipeline-async",
        "atlas",
        "hermes",
        "palette",
        "quill-ts",
        "quill-py",
    }
)

FLOOR: int = 50

# Where the persisted synthetic corpus lives — importable so tests can patch it.
# parents[3] = nexus-broker/ (the package root), so this resolves to
# nexus-broker/router_train_data/synthetic_pairs.jsonl regardless of install location.
SYNTHETIC_ARTIFACT_PATH: Path = (
    Path(__file__).resolve().parents[3] / "router_train_data" / "synthetic_pairs.jsonl"
)

# ---------------------------------------------------------------------------
# Template-based fallback generator (deterministic, always available)
# ---------------------------------------------------------------------------

# Domain-specific paraphrase templates per persona.  Each slot will be
# format()-called with {idx} for uniqueness.
_TEMPLATES: dict[str, list[str]] = {
    "scout": [
        "Explore the {idx}th module in this codebase and summarize its purpose.",
        "Do a recon pass on feature area #{idx} — what files own it?",
        "Read the current state of component {idx} and give me a brief overview.",
        "Map out how subsystem {idx} is structured without making any changes.",
        "Give me a bird's-eye view of area {idx}; no edits needed.",
    ],
    "forge-ui": [
        "Add a React component for the {idx}-step onboarding flow.",
        "Update the {idx}th dashboard widget to show live data.",
        "Fix the layout bug in the step-{idx} form — inputs are misaligned.",
        "Build a {idx}-column data table that sorts on click.",
        "Refactor the settings page section {idx} to use the new design tokens.",
    ],
    "forge-wire": [
        "Implement the REST endpoint for resource type {idx}.",
        "Add a WebSocket handler for event stream #{idx}.",
        "Write the GraphQL resolver for query #{idx}.",
        "Fix the {idx}th middleware chain — it is not passing the auth header.",
        "Create an OpenAPI spec for the v{idx} API surface.",
    ],
    "pipeline-data": [
        "Write a Polars transform that deduplicates the {idx}th ingestion batch.",
        "Implement a DuckDB writer for schema revision {idx}.",
        "Add an embedding generation step for document type {idx}.",
        "Build a Polars pipeline that normalizes the {idx}th raw source.",
        "Write the aggregate stage that joins sources {idx} and {idx} for analytics.",
    ],
    "pipeline-async": [
        "Create a Dramatiq worker that processes queue type {idx} asynchronously.",
        "Wire a retry policy for background task {idx} with exponential back-off.",
        "Add a Dramatiq middleware for tracing job type {idx}.",
        "Implement the async polling loop for external resource {idx}.",
        "Fix the dead-letter handling for queue #{idx} — messages are silently dropped.",
    ],
    "atlas": [
        "Write an Alembic migration that adds table schema revision {idx}.",
        "Design the ERD for entity group {idx} and add the migration.",
        "Add a foreign-key constraint between tables in schema version {idx}.",
        "Create an index on the {idx}th high-cardinality column.",
        "Write the rollback migration for schema change #{idx}.",
    ],
    "hermes": [
        "Update the docker-compose config for service #{idx}.",
        "Add a Caddyfile route for the {idx}th subdomain.",
        "Fix the health-check for container version {idx}.",
        "Configure the reverse-proxy rule for path prefix /{idx}/.",
        "Add the environment variable block for service {idx} in docker-compose.",
    ],
    "palette": [
        "Generate a colour palette variation #{idx} for the design system.",
        "Update design token set {idx} to reflect the new brand guidelines.",
        "Create a Figma-compatible colour export for theme variant {idx}.",
        "Audit contrast ratios for palette revision {idx} against WCAG AA.",
        "Build the dark-mode token map for design system version {idx}.",
    ],
    "quill-ts": [
        "Write a Vitest test for the {idx}th TypeScript service function.",
        "Add RTL tests for the {idx}-step wizard component.",
        "Create a Vitest stub for the async hook used by feature {idx}.",
        "Write integration tests for the {idx}th API client module.",
        "Add type-safe test fixtures for the {idx}th data model.",
    ],
    "quill-py": [
        "Write a pytest suite for the {idx}th Python module.",
        "Add a parameterised test for edge case set {idx}.",
        "Create a pytest fixture for the {idx}th external service mock.",
        "Write property-based tests for transformation function {idx}.",
        "Add async pytest tests for background task handler {idx}.",
    ],
}

_FALLBACK_TEMPLATE = [
    "Handle task #{idx} for persona {{persona}} in the Nexus system.",
    "Do step {idx} of the implementation for the assigned scope.",
    "Complete sub-task {idx} as scoped by your persona responsibilities.",
    "Investigate and resolve issue #{idx} in your domain.",
    "Implement feature component {idx} according to the contract.",
]


def _template_generate(persona: str, seeds: list[str], n: int) -> list[str]:
    """Deterministic template-based paraphrase generator.

    Uses persona-specific templates; cycles through them if n > len(templates).
    Unique across calls because the seed index is embedded.
    """
    templates = _TEMPLATES.get(persona, _FALLBACK_TEMPLATE)
    results: list[str] = []
    for i in range(n):
        tpl = templates[i % len(templates)]
        # idx makes each generated string unique regardless of template count
        seed_excerpt = seeds[i % len(seeds)][:30].replace("\n", " ") if seeds else ""
        try:
            prompt_text = tpl.format(idx=i, persona=persona)
        except KeyError:
            prompt_text = tpl.replace("{idx}", str(i)).replace("{persona}", persona)
        if seed_excerpt:
            # Append a short seed fingerprint for diversity / grounding
            prompt_text = f"{prompt_text}  [ctx: {seed_excerpt}]"
        results.append(prompt_text)
    return results


# ---------------------------------------------------------------------------
# LLM-backed generator (default; falls back to templates on failure)
# ---------------------------------------------------------------------------

_CLAUDE_BATCH_SIZE: int = 15


def _claude_generate(persona: str, seeds: list[str], n: int) -> list[str]:
    """Call 'claude --print' to generate n diverse paraphrase prompts for persona.

    Batches into calls of up to _CLAUDE_BATCH_SIZE prompts each to bound cost.
    Returns a list of exactly n strings (may fall back to template if LLM fails).
    """
    results: list[str] = []
    seed_text = "\n".join(f"- {s}" for s in seeds[:5])

    remaining = n
    while remaining > 0:
        batch = min(remaining, _CLAUDE_BATCH_SIZE)
        system_prompt = (
            f"You are a data-augmentation assistant. "
            f"Generate exactly {batch} DIVERSE, realistic dispatch prompts for the "
            f"'{persona}' agent persona in the Nexus multi-agent system. "
            f"Each prompt must be a real work request a developer would send. "
            f"Output ONLY one prompt per line, no numbering, no blank lines, no prose."
        )
        user_message = (
            f"Reference examples (paraphrase and vary these, do not copy verbatim):\n"
            f"{seed_text}\n\n"
            f"Now output exactly {batch} new prompts for '{persona}':"
        )
        full_prompt = f"{system_prompt}\n\n{user_message}"

        try:
            proc = subprocess.run(  # noqa: S603
                ["claude", "--print", full_prompt],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                logger.warning(
                    "claude --print failed (rc=%d) for persona %s; using templates",
                    proc.returncode,
                    persona,
                )
                results.extend(_template_generate(persona, seeds, remaining))
                break
            lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
            # Take up to batch lines; if LLM produced fewer, pad with templates
            if lines:
                results.extend(lines[:batch])
                if len(lines) < batch:
                    pad = batch - len(lines)
                    offset = len(results)
                    results.extend(
                        _template_generate(persona, seeds, pad + offset)[offset : offset + pad]
                    )
            else:
                results.extend(_template_generate(persona, seeds, batch))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            logger.warning(
                "claude --print unavailable for persona %s (%s); using templates",
                persona,
                exc,
            )
            results.extend(_template_generate(persona, seeds, remaining))
            break
        remaining -= batch

    return results[:n]


# ---------------------------------------------------------------------------
# Core generate_synthetic
# ---------------------------------------------------------------------------

GenerateFn = Callable[[str, list[str], int], list[str]]


def generate_synthetic(
    real_pairs: list[dict[str, Any]],
    *,
    floor: int = FLOOR,
    eligible: frozenset[str] = QUERY_ROUTABLE_ELIGIBLE,
    max_per_persona: int = 60,
    generate_fn: GenerateFn | None = None,
) -> list[dict[str, Any]]:
    """Generate synthetic labeled pairs for starved eligible personas.

    Args:
        real_pairs: The real labeled pairs from collect_labeled_pairs().
        floor: Target minimum count per eligible persona (default 50).
        eligible: The set of persona names eligible for augmentation.
        max_per_persona: Hard cap on synthetic pairs per persona.
        generate_fn: Injectable seam for testing.  Default: _claude_generate.
            Signature: (persona: str, seeds: list[str], n: int) -> list[str]

    Returns:
        A list of synthetic labeled-pair dicts.  Only eligible starved personas
        are represented; excluded labels never appear in the output.
    """
    fn = generate_fn if generate_fn is not None else _claude_generate

    # Build per-persona seed lists from real pairs
    seeds_by_persona: dict[str, list[str]] = {}
    counts_by_persona: dict[str, int] = {}
    for pair in real_pairs:
        persona = str(pair.get("label_persona") or "")
        if not persona:
            continue
        counts_by_persona[persona] = counts_by_persona.get(persona, 0) + 1
        if persona in eligible:
            prompt_text = str(pair.get("prompt") or "")
            # Only use genuine user prompts as seeds — noisy seeds produce contaminated
            # synthetic prompts via the [ctx: <excerpt>] fingerprint appended by
            # _template_generate (the root cause of the WF-C2 contamination).
            if prompt_text and is_genuine_user_prompt(prompt_text):
                seeds_by_persona.setdefault(persona, []).append(prompt_text)

    # Build the real-hash de-dup set
    real_hashes: set[str] = set()
    for pair in real_pairs:
        ph = pair.get("prompt_hash")
        if not ph:
            prompt_text = pair.get("prompt")
            if isinstance(prompt_text, str) and prompt_text:
                ph = prompt_hash(prompt_text)
        if ph:
            real_hashes.add(str(ph))

    synthetic_output: list[dict[str, Any]] = []
    seen_hashes: set[str] = set(real_hashes)  # dedup across both real and already-generated

    for persona in sorted(eligible):  # sorted for determinism
        current_count = counts_by_persona.get(persona, 0)
        if current_count >= floor:
            continue  # already at or above floor — skip

        n_needed = min(floor - current_count, max_per_persona)
        seeds = seeds_by_persona.get(persona, [])

        # If no real seeds exist for this persona, use a generic placeholder
        if not seeds:
            seeds = [f"Task for {persona}"]

        generated_prompts = fn(persona, seeds, n_needed)

        # Build pairs with dedup
        seed_hashes = [prompt_hash(s) for s in seeds]
        for idx, gen_prompt in enumerate(generated_prompts):
            if not isinstance(gen_prompt, str) or not gen_prompt.strip():
                continue
            ph = prompt_hash(gen_prompt)
            if ph in seen_hashes:
                continue  # collision — drop
            seen_hashes.add(ph)
            seed_ph = seed_hashes[idx % len(seed_hashes)]
            synthetic_output.append(
                {
                    "prompt": gen_prompt,
                    "prompt_hash": ph,
                    "label_persona": persona,
                    "label_status": "ok",
                    "label_source": "synthetic",
                    "label_confidence": 0.5,
                    "synthetic": True,
                    "seed_prompt_hash": seed_ph,
                }
            )

    return synthetic_output


# ---------------------------------------------------------------------------
# WF-G: Contrastive synthetic generator
# ---------------------------------------------------------------------------

# Contrastive target groups.  Each entry is either:
#   ("single", persona, description, discriminative_cues)
#       → produce n hard examples that are *clearly* this persona, not the confusable
#   ("pair", persona_a, persona_b, shared_topic, cue_a, cue_b)
#       → produce matched pairs: one request that is clearly persona_a, one that is
#         clearly persona_b, both on a similar topic so the boundary is the signal

_CONTRASTIVE_PROMPTS: dict[
    str,
    dict[str, str],
] = {
    # ---------- forge-ui: clearly-pixel-side requests ----------
    "forge-ui": {
        "role": "forge-ui",
        "discriminative": (
            "UI component in app/components/, RSC page in app/(routes)/, "
            "Tremor chart, Tailwind styling, dark-mode token, animation, loading skeleton, "
            "empty state, responsive layout — renders in the browser, no server action"
        ),
        "avoid": "server actions, API routes, DuckDB queries, data fetching",
    },
    # ---------- forge-wire: clearly-server-side requests ----------
    "forge-wire": {
        "role": "forge-wire",
        "discriminative": (
            "server action in app/actions/, API route in app/api/, "
            "Vercel AI SDK streaming, DuckDB read query, auth middleware, webhook handler, "
            "edge runtime — processes data on the server, no UI component"
        ),
        "avoid": "React components, Tailwind, visual layout, Tremor charts",
    },
    # ---------- atlas: schema design (DDL / Malloy / Parquet) ----------
    "atlas": {
        "role": "atlas",
        "discriminative": (
            "DuckDB DDL (CREATE TABLE / ALTER TABLE / CREATE INDEX), "
            "Malloy source or query definition, Parquet schema design, "
            "embedding vector table design, HNSW index, column type decision, "
            "migration design doc — design-only, no Python execution"
        ),
        "avoid": "executing migrations, writing Polars transforms, Python code",
    },
    # ---------- pipeline-async: Dramatiq / Redis / HTTP clients ----------
    "pipeline-async": {
        "role": "pipeline-async",
        "discriminative": (
            "Dramatiq actor in ingestion/src/workers/, Redis queue, "
            "background job, async polling loop, Tableau REST client in ingestion/src/clients/, "
            "Azure AI enrichment HTTP call, rate limiting, retry with backoff"
        ),
        "avoid": "Polars dataframes, DuckDB write path, synchronous transforms",
    },
    # ---------- quill-ts: TypeScript / Vitest tests ----------
    "quill-ts": {
        "role": "quill-ts",
        "discriminative": (
            "Vitest test for a TypeScript function or React component, "
            "React Testing Library (RTL) render/userEvent, test stub for an async hook, "
            "type-safe test fixture, integration test for an API client module — "
            "tests only, no implementation code"
        ),
        "avoid": "implementation code, Python tests, pytest",
    },
    # ---------- quill-py: Python / pytest tests ----------
    "quill-py": {
        "role": "quill-py",
        "discriminative": (
            "pytest test suite for a Python module, parametrized test, "
            "pytest fixture for an external service mock, property-based test, "
            "async pytest test for a background task handler — "
            "tests only in ingestion/tests/ or nexus-broker/tests/"
        ),
        "avoid": "implementation code, TypeScript tests, Vitest",
    },
    # ---------- hermes: cross-service integration (NOT catch-all) ----------
    "hermes": {
        "role": "hermes",
        "discriminative": (
            "Tableau REST API PAT sign-in, VDS endpoint config, "
            "Azure AI Services base URL wiring (AI_API_BASE_URL), "
            "MCP server registration in .mcp.json, Docker Compose service block, "
            "Caddyfile route, env-var plumbing for cross-service auth — "
            "the wire between services, not the business logic"
        ),
        "avoid": "vague implementation requests, UI, Python transforms, schema design",
    },
    # ---------- no-dispatch: non-actionable / conversational ----------
    "no-dispatch": {
        "role": "no-dispatch",
        "discriminative": (
            "questions about status/history, meta-statements, vague commentary, "
            "requests for explanation or summary with no implementation deliverable — "
            "orchestrator answers inline without dispatching any persona"
        ),
        "avoid": "any concrete code deliverable, file writes, service wiring",
    },
}

# Default contrastive batch size — small to keep prompt+rubric under 8KB.
_CONTRASTIVE_BATCH_SIZE: int = 5

# Confusable boundary pairs for matched-pair generation.
# Each tuple: (persona_a, persona_b, shared_topic_hint).
_BOUNDARY_PAIRS: list[tuple[str, str, str]] = [
    ("forge-ui", "forge-wire", "dashboard feature"),
    ("forge-ui", "forge-wire", "AI response display"),
    ("hermes", "no-dispatch", "integration setup question"),
    ("hermes", "pipeline-async", "Tableau data"),
]

LABEL_SOURCE_CONTRASTIVE = "synthetic_contrastive"
LABEL_CONFIDENCE_CONTRASTIVE = 0.5


# ---------------------------------------------------------------------------
# Response parser — strips preamble / fences / prose before returning lines
# ---------------------------------------------------------------------------

# Patterns that identify non-request lines in LLM output.
_FENCE_RE = _re.compile(r"^`{3}")
_PREAMBLE_RE = _re.compile(
    r"^(here are|sure[,!]?|the following|below are|output:|here'?s)\b",
    _re.IGNORECASE,
)
# A line is a header/preamble if it ends with ':' and is short (<=80 chars).
_HEADER_COLON_RE = _re.compile(r"^.{1,80}:$")
# Numbered list items ("1.", "2)", etc.) are prose structure, not requests.
_NUMBERED_RE = _re.compile(r"^\d+[.)]\s+")
# Very short fragments are not real requests (< 10 chars after stripping).
_MIN_LEN = 10


def _parse_contrastive_response(raw: str) -> list[str]:
    """Parse a raw 'claude --print' response into plausible request lines.

    Drops:
    - Markdown code-fence markers (lines starting with ```)
    - Preamble / header lines ("Here are N ...", "Sure, ...", any line ending ':')
    - Numbered list prefixes — the line is kept but the number prefix stripped
    - Lines shorter than _MIN_LEN characters after strip
    - Blank lines

    Keeps only lines that look like imperative developer requests.
    """
    results: list[str] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop fences
        if _FENCE_RE.match(line):
            continue
        # Drop preamble lines
        if _PREAMBLE_RE.match(line):
            continue
        # Drop header-colon lines (e.g. "Five HARD requests for atlas:")
        if _HEADER_COLON_RE.match(line):
            continue
        # Strip leading number prefix (keep the content)
        line = _NUMBERED_RE.sub("", line).strip()
        if not line:
            continue
        # Drop very short fragments
        if len(line) < _MIN_LEN:
            continue
        results.append(line)
    return results


def _claude_generate_contrastive(
    persona: str,
    discriminative: str,
    avoid: str,
    n: int,
) -> list[str]:
    """Call 'claude --print' to generate n hard contrastive prompts for one persona.

    Each prompt must be clearly in-class (carries the discriminative features) and
    must NOT be routable to the `avoid` description.  Falls back to empty list on
    failure so the caller can skip gracefully.
    """
    system_prompt = (
        f"You are a training-data engineer creating HARD discriminative examples for a "
        f"router classifier.  Generate exactly {n} realistic developer requests that are "
        f"CLEARLY routable to the '{persona}' agent and NOT to any other agent.\n\n"
        f"The '{persona}' agent handles: {discriminative}\n\n"
        f"DO NOT generate requests that could be confused with: {avoid}\n\n"
        f"Rules:\n"
        f"- Each request must be a realistic sentence a developer would actually send.\n"
        f"- Each request must contain at least one concrete discriminative noun/verb from "
        f"  the '{persona}' description above.\n"
        f"- No paraphrase duplicates — vary the topic, verb, and object each time.\n"
        f"- Output ONLY one request per line, no numbering, no blank lines, no prose."
    )
    user_message = f"Output exactly {n} requests for '{persona}':"
    full_prompt = f"{system_prompt}\n\n{user_message}"
    try:
        proc = subprocess.run(  # noqa: S603
            ["claude", "--print", full_prompt],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            logger.warning(
                "claude --print failed (rc=%d) for contrastive persona %s",
                proc.returncode,
                persona,
            )
            return []
        lines = _parse_contrastive_response(proc.stdout)
        return lines[:n]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning(
            "claude --print unavailable for contrastive persona %s (%s)",
            persona,
            exc,
        )
        return []


def _claude_generate_boundary_pair(
    persona_a: str,
    persona_b: str,
    topic: str,
    spec_a: dict[str, str],
    spec_b: dict[str, str],
    n_per_side: int,
) -> tuple[list[str], list[str]]:
    """Generate matched pairs on `topic`: n_per_side for persona_a and persona_b.

    Both sets come from a single claude call to keep them topically paired.
    Returns (prompts_for_a, prompts_for_b); either may be [] on failure.
    """
    system_prompt = (
        f"You are a training-data engineer creating MATCHED BOUNDARY PAIRS for a router "
        f"classifier.  On the topic of '{topic}', generate two sets of requests:\n\n"
        f"SET A — {n_per_side} requests clearly routable to '{persona_a}':\n"
        f"  Handles: {spec_a['discriminative']}\n"
        f"  NOT: {spec_a['avoid']}\n\n"
        f"SET B — {n_per_side} requests clearly routable to '{persona_b}':\n"
        f"  Handles: {spec_b['discriminative']}\n"
        f"  NOT: {spec_b['avoid']}\n\n"
        f"Rules:\n"
        f"- Requests in SET A and SET B should be on similar sub-topics so the "
        f"  only discriminating signal is which agent owns the deliverable.\n"
        f"- Each request must be realistic and contain discriminative cues.\n"
        f"- Output format (strict):\n"
        f"  SET_A: <request>\n"
        f"  SET_B: <request>\n"
        f"  Alternate: SET_A then SET_B, repeat {n_per_side} times.  No other text."
    )
    user_message = f"Generate {n_per_side} matched pairs on the topic '{topic}':"
    full_prompt = f"{system_prompt}\n\n{user_message}"
    try:
        proc = subprocess.run(  # noqa: S603
            ["claude", "--print", full_prompt],
            capture_output=True,
            text=True,
            timeout=240,
        )
        if proc.returncode != 0:
            logger.warning(
                "claude --print failed (rc=%d) for boundary pair %s/%s",
                proc.returncode,
                persona_a,
                persona_b,
            )
            return [], []
        prompts_a: list[str] = []
        prompts_b: list[str] = []
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            # Skip fence / preamble lines before SET_A/SET_B parsing
            if _FENCE_RE.match(line) or _PREAMBLE_RE.match(line) or _HEADER_COLON_RE.match(line):
                continue
            if line.upper().startswith("SET_A:"):
                text = line[len("SET_A:"):].strip()
                if text and len(text) >= _MIN_LEN:
                    prompts_a.append(text)
            elif line.upper().startswith("SET_B:"):
                text = line[len("SET_B:"):].strip()
                if text and len(text) >= _MIN_LEN:
                    prompts_b.append(text)
        return prompts_a[:n_per_side], prompts_b[:n_per_side]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning(
            "claude --print unavailable for boundary pair %s/%s (%s)",
            persona_a,
            persona_b,
            exc,
        )
        return [], []


def _make_contrastive_pair(
    prompt_text: str,
    persona: str,
    seen_hashes: set[str],
) -> dict[str, Any] | None:
    """Build one contrastive pair dict; returns None on dedup collision."""
    if not prompt_text.strip():
        return None
    ph = prompt_hash(prompt_text)
    if ph in seen_hashes:
        return None
    seen_hashes.add(ph)
    return {
        "prompt": prompt_text,
        "prompt_hash": ph,
        "label_persona": persona,
        "label_status": "ok",
        "label_source": LABEL_SOURCE_CONTRASTIVE,
        "label_confidence": LABEL_CONFIDENCE_CONTRASTIVE,
        "synthetic": True,
    }


def generate_contrastive(
    targets: list[str],
    *,
    n_per_target: int = 10,
    n_per_boundary_side: int = 5,
    generate_fn: GenerateFn | None = None,
    out_path: Path | None = None,
    batch_size: int = _CONTRASTIVE_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """WF-G: Generate hard contrastive synthetic pairs for confusable groups + starved classes.

    For each persona in `targets`:
    - If the persona has a boundary pair in _BOUNDARY_PAIRS (and the pair partner is
      also in `targets`), generate `n_per_boundary_side` matched pairs per side so
      the decision boundary is explicitly represented.
    - Always generate `n_per_target` hard single-class examples for that persona.

    Args:
        targets: Persona names to generate for (e.g. ["atlas", "forge-ui", "forge-wire"]).
        n_per_target: Hard single-class examples per persona.
        n_per_boundary_side: Matched examples per side for each boundary pair.
        generate_fn: Injectable seam.  When provided it is called as
            generate_fn(persona, seeds=[], n=n_per_target) and its output is used
            for BOTH the single-class AND boundary-pair prompts (seeds are ignored).
            When None, _claude_generate_contrastive / _claude_generate_boundary_pair
            are used.  Tests should inject a deterministic stub.
        out_path: When provided, results are appended to this JSONL path
            incrementally (one batch at a time) so partial results survive timeout.
        batch_size: Number of prompts per sub-batch (controls prompt size).

    Returns:
        A list of synthetic_contrastive labeled-pair dicts.
    """
    target_set = set(targets)
    output: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()

    def _append_output(pairs: list[dict[str, Any]]) -> None:
        output.extend(pairs)
        if out_path is not None and pairs:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("a", encoding="utf-8") as fh:
                for p in pairs:
                    fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    # --- Boundary pair generation (matched pairs for confusable groups) ---
    done_pairs: set[tuple[str, str]] = set()
    for persona_a, persona_b, topic in _BOUNDARY_PAIRS:
        if persona_a not in target_set or persona_b not in target_set:
            continue
        key = (min(persona_a, persona_b), max(persona_a, persona_b), topic)
        if key in done_pairs:
            continue
        done_pairs.add(key)

        if generate_fn is not None:
            # Injectable seam: use generate_fn for both sides.
            batch_results: list[dict[str, Any]] = []
            for persona in (persona_a, persona_b):
                try:
                    raw_prompts = generate_fn(persona, [], n_per_boundary_side)
                except Exception as exc:  # noqa: BLE001 — injectable fn may raise anything
                    logger.warning(
                        "generate_fn raised for boundary persona %s (%s); skipping",
                        persona,
                        exc,
                    )
                    continue
                for p in raw_prompts:
                    pair = _make_contrastive_pair(p, persona, seen_hashes)
                    if pair is not None:
                        batch_results.append(pair)
            _append_output(batch_results)
        else:
            spec_a = _CONTRASTIVE_PROMPTS.get(persona_a, {})
            spec_b = _CONTRASTIVE_PROMPTS.get(persona_b, {})
            if not spec_a or not spec_b:
                continue
            prompts_a, prompts_b = _claude_generate_boundary_pair(
                persona_a, persona_b, topic, spec_a, spec_b,
                n_per_side=n_per_boundary_side,
            )
            batch_results = []
            for p in prompts_a:
                pair = _make_contrastive_pair(p, persona_a, seen_hashes)
                if pair is not None:
                    batch_results.append(pair)
            for p in prompts_b:
                pair = _make_contrastive_pair(p, persona_b, seen_hashes)
                if pair is not None:
                    batch_results.append(pair)
            _append_output(batch_results)

    # --- Single-class hard examples per persona ---
    for persona in sorted(target_set):
        spec = _CONTRASTIVE_PROMPTS.get(persona)
        remaining = n_per_target
        while remaining > 0:
            n_batch = min(remaining, batch_size)
            if generate_fn is not None:
                try:
                    raw_prompts = generate_fn(persona, [], n_batch)
                except Exception as exc:  # noqa: BLE001 — injectable fn may raise anything
                    logger.warning(
                        "generate_fn raised for persona %s (%s); skipping batch",
                        persona,
                        exc,
                    )
                    remaining -= n_batch
                    continue
            elif spec is not None:
                raw_prompts = _claude_generate_contrastive(
                    persona,
                    discriminative=spec["discriminative"],
                    avoid=spec["avoid"],
                    n=n_batch,
                )
            else:
                raw_prompts = _template_generate(persona, [], n_batch)

            batch_results = []
            for p in raw_prompts:
                pair = _make_contrastive_pair(p, persona, seen_hashes)
                if pair is not None:
                    batch_results.append(pair)
            _append_output(batch_results)
            remaining -= n_batch

    return output


# ---------------------------------------------------------------------------
# Persist / load artifact
# ---------------------------------------------------------------------------


def persist_synthetic(pairs: list[dict[str, Any]], path: Path = SYNTHETIC_ARTIFACT_PATH) -> int:
    """Write synthetic pairs to a JSONL artifact.  Returns the number of rows written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(json.dumps(pair, ensure_ascii=False) + "\n")
            count += 1
    return count


def load_synthetic(path: Path = SYNTHETIC_ARTIFACT_PATH) -> list[dict[str, Any]]:
    """Read the persisted synthetic JSONL artifact.  Returns [] if file absent."""
    if not path.exists():
        return []
    pairs: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    pairs.append(rec)
    except OSError:
        return pairs
    return pairs

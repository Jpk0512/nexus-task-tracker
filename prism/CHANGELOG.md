# Changelog

## 0.2.0 — 2026-06-16

Lean, Claude-CLI-backed (OAuth, no API key), on-demand v1. See
`docs/research/PROPOSAL-prism-v1.md` for the full rationale.

> Note: 0.2.0 never shipped, so this entry is amended in place. The default backend
> was changed from `anthropic` (SDK + API key) to `claude_cli` (the Claude Code CLI in
> print mode, reusing the user's existing OAuth) before release.

### Changed
- **Default backend is the Claude Code CLI** (`PRISM_BACKEND=claude_cli`), reusing the
  user's existing **OAuth** (Claude subscription) — **no API key required**, no env
  beyond `claude` being on PATH. Each call spawns `claude -p --output-format json
  --model <model>` as an async subprocess (prompt on stdin) and parses the JSON
  envelope's `result` field. Triage/diagnose use a cheap Haiku-tier model; the RCA
  escalation uses the configured stronger Claude model. `Config.validate()` checks
  `shutil.which("claude")` at startup and logs LOUDLY if it is missing; every per-call
  failure (missing CLI, non-zero exit, error envelope, unparseable output, timeout)
  raises `ClaudeCliError` + WARN — never a silent no-op.
- **Anthropic SDK is now an optional backend** (`PRISM_BACKEND=anthropic`), for users
  who DO have an `ANTHROPIC_API_KEY`. The key is validated at startup and logged LOUDLY
  if missing.
- **LM Studio is now an optional backend** (`PRISM_BACKEND=lmstudio`). When selected and
  unreachable it fails LOUDLY (raise + WARN log) — never a silent no-op.
- **Storage needs no running model.** Findings are stored in Qdrant at a single consistent
  256-dim using a deterministic, dependency-free hash vector that exists only to satisfy
  upsert. v1 does not do semantic similarity. All read tools render from stored payloads.
- `genome.py` takes its configuration from `Config`; it no longer re-reads `os.getenv`.
- Pipeline tests now mock an injected LLM client; no network, no real Anthropic call.

### Added
- `logging.getLogger("prism")` with WARN-level messages at every degradation point
  (backend unreachable, key missing, parse failure).
- Wiring tests for the MCP server (`get_risk_map` / `get_recent_findings` /
  `get_convergence_report` / `trigger_deep_scan`) and a CLI `status` test.
- Consumer grant: `scout` may call the four `mcp__prism__*` tools; `nexus-prism.mdc`
  documents on-demand usage.

### Removed
- `prism/daemon.py`, `prism/sensor/watcher.py`, `prism/sensor/ochiai.py`, and
  `tests/test_ochiai.py` (continuous-scan + Ochiai SBFL deferred to v2/v3).
- The `start` CLI subcommand.
- `BugGenome.index_function`, `BugGenome.find_similar_bugs`, the `_random_unit_vector`
  silent fallback, and the dead `fuzz_seeds` / `mutation_gaps` / `code_vectors` collections.
- The `_MOCK_QWEN_RESPONSE` / `_MOCK_QWEN_CONNECT_ERROR` env-var test seams from
  production code (replaced by dependency injection).
- Dependencies `watchdog`, `numpy`, and `coverage`.

## 0.1.0

Initial (inert) release.

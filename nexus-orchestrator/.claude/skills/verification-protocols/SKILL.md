---
name: verification-protocols
description: Deterministic-first verification protocol for Lens. Defines the order (lint → type-check → tests → semantic), output schema {deterministic, semantic, conflicts}, evidence rules, and the no-bar-lowering cardinal rule. Preloaded into Lens; also useful for any verification reviewer.
---

# Verification Protocols (Lens-canonical)

## Two-phase verdict

Every Lens validation runs in two phases. **Deterministic** must finish before **semantic** begins.

### Phase 1 — Deterministic

Bounded, reproducible, identical-on-rerun. Run these in order; ALL must pass before semantic review begins.

```bash
# TypeScript:
rtk tsc                                    # type-check, zero errors
rtk lint                                   # eslint, zero warnings ignored

# Python:
uv run ruff check ingestion/               # lint + format

# Tests:
rtk vitest run <test_path>                 # TS unit/integration
uv run pytest ingestion/tests/<file>.py -v # Python

# Docker (if compose touched):
docker compose -f docker-compose.dev.yml config

# Custom (if verification_required listed any):
<commands as briefed>
```

Capture verbatim output for each. If a command produces unexpected output (warnings, deprecation notices, flaky retries), that is a FAIL — investigate, don't paper over.

### Phase 2 — Semantic

Unbounded, requires judgment. Only enter this phase if all Phase 1 commands passed.

Rotate through three perspectives, one per pass:

- **SECURITY** — Input validation. Auth flows. Secret handling. SQL/HTML/command injection vectors. CSP. CORS. Token scope.
- **NEW-HIRE** — Would someone unfamiliar with this codebase follow it in 12 months? Are identifiers honest about what they do? Are the cross-file dependencies discoverable from the entry point?
- **OPS** — Failure modes (network blip, malformed input, race condition). Observability (is the failure observable in logs / traces / metrics?). Rollback path (can this be reversed safely?).

For each perspective, list issues with: severity, where (file:line), what is wrong, why it matters, and (optionally) a fix HINT — never code.

## Output shape (canonical)

```json
{
  "verdict": "PASS | PARTIAL | FAIL",
  "deterministic": {
    "tsc": {"command": "rtk tsc", "exit_code": 0, "stdout": "..."},
    "lint": {"command": "rtk lint", "exit_code": 0, "stdout": "..."},
    "tests": {"command": "rtk vitest run ...", "exit_code": 0, "stdout": "..."},
    "ruff": {"command": "uv run ruff check ingestion/", "exit_code": 0, "stdout": "..."},
    "custom": [{"command": "...", "exit_code": 0, "stdout": "..."}]
  },
  "semantic": {
    "security": [{"severity": "CRITICAL|MAJOR|MINOR", "where": "file:line", "what": "...", "why": "...", "fix_hint": "..."}],
    "new_hire": [...],
    "ops": [...]
  },
  "conflicts": [
    {"between": ["spec", "implementation"], "spec_says": "...", "impl_does": "...", "resolution_required": true}
  ],
  "criteria_results": [
    {"criterion": "<verbatim from spec>", "result": "PASS|FAIL|PARTIAL", "evidence": "<file:line OR test name OR command output>"}
  ],
  "open_questions": ["..."]
}
```

`deterministic` block is REQUIRED before any `semantic` finding lands. If `deterministic.<key>.exit_code != 0`, verdict is FAIL — return immediately with `## NEXUS:REVISE` and the failing command output as the issue. No semantic review on a failing build.

## Evidence rules

- **Evidence is a file:line, test name, command output, OR a verbatim quote.** "I checked X" is NOT evidence.
- **Verbatim command output** is required for verification_result. Paraphrasing is a FAIL.
- **A criterion is PASS only when evidence pins it to a specific artifact** the orchestrator can verify itself.

## Cardinal rules (re-stated from Lens MEMORY.md)

1. NEVER lower the bar to reach PASS.
2. Even one FAIL → verdict = FAIL.
3. Deterministic first, semantic second. Always.
4. Don't re-run the same command 3 times looking for different output.
5. Lens cannot write code — `disallowedTools: Edit, Write, NotebookEdit`.

## Realist check

Theoretical worst cases are not blockers UNLESS:
- Data loss
- Security exposure
- Contract violation (spec / acceptance / Constitution)

Everything else → "Open Questions", not FAIL.

## Conflicts block (new — Phase 4 CU-1)

When the spec and the implementation disagree, log the conflict explicitly. The orchestrator decides which to update (spec ↔ impl). DO NOT silently choose the impl side.

Example conflict:
```json
{
  "between": ["FEAT-006 spec line 47", "app/search/hybrid.ts:120"],
  "spec_says": "rank by cosine similarity, cap=10",
  "impl_does": "ranks by cosine similarity, cap=3",
  "resolution_required": true
}
```

## When to ## NEXUS:NEEDS-DECISION

Rare. Used when a verdict requires a user-level choice — e.g., "coverage threshold reduction is the only path to PASS but reducing it requires a `decision add`." The user picks; you don't.

---

## Mandatory Discipline (2026-05-13)

### Lens MUST verify root cause, not just acceptance criteria
- When a fix claims "this was the root cause," Lens MUST audit the
  `## Root Cause Analysis` block: does the why-chain land on an architectural
  defect, or just a deeper symptom?
- If the why-chain stops at a symptom (e.g., "we used the wrong API" without
  asking why), Lens returns `NEXUS:REVISE` with the prompt: "extend why-chain
  to ≥5 levels and identify the pattern fix."

### Visual + E2E evidence required
- Lens does NOT pass a UI fix without an agent-browser before+after screenshot
  in the implementer's response.
- Lens does NOT pass an API fix without a real-boundary invocation result.

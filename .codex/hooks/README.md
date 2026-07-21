# Codex → Nexus hook adapter (ADVISORY / class-F3)

Codex CLI reads the Claude files (`CLAUDE.md`, `.claude/agents`, skills) natively, but
uses a **JSON stdin/stdout hook system** like Cursor's. This directory contains a thin
adapter, `codex-adapter.sh`, that **reuses** the existing `.claude/hooks/*.sh` enforcement
scripts — it does NOT duplicate their logic.

## The one thing that is different from Cursor: advisory-only enforcement

Claude Code's PreToolUse gates and Cursor's `failClosed:true` events can **hard-block** an
action. **Codex has no fail-closed hook contract this adapter can rely on** (DEC-102 DEC-2).
So `codex-adapter.sh` runs every Nexus gate in **ADVISORY (class-F3) mode**:

1. reads Codex's stdin JSON,
2. translates it into the Claude-Code-shaped JSON the target `.claude/hooks` script expects
   (matching each script's exact `jq` field paths),
3. runs each target Nexus gate in order and captures its verdict,
4. **LOGS every DENY verdict** to stderr and, best-effort, to `.memory/files/codex-adapter.log`,
5. **ALWAYS emits an ALLOW envelope and exits 0** — it NEVER fails closed, NEVER exits
   non-zero to block the Codex action.

The verdict log line is stable and greppable:

```
[codex-adapter] ADVISORY (class-F3): <event> gate <hook> → DENY: <reason> — Codex has no fail-closed PreToolUse contract (DEC-102); NOT blocking.
```

**Do NOT "fix" the adapter to exit 2.** The advisory-only contract is intentional and is
asserted by `tests/test_codex_adapter.py`. The enforcement of last resort is the orchestrator
itself, instructed by `.codex/rules/nexus-enforcement.mdc` + the `alwaysApply` operating-model
rule to honor a logged DENY exactly as a hard block would force.

`conversation_id` is mapped to `session_id` for the hooks that key on it. `PROJECT_ROOT` is
resolved from `$PWD` (Codex sets cwd to the workspace root when invoking hooks) — no absolute
paths appear in the adapter.

## Mapping (Codex event → bridged `.claude/hooks`)

| Codex event            | Bridged Nexus gates                                                              | Enforcement |
|------------------------|----------------------------------------------------------------------------------|-------------|
| `beforeShellExecution` | `no-direct-push-to-session-branch.sh`, `worktree-guard.sh`                       | advisory — verdict logged, never blocked |
| `beforeReadFile`       | `read-injection-scanner.sh`                                                       | advisory (observational anyway) |
| `afterFileEdit`        | `verify-after-edit.sh`, `reflection-capture.sh`                                   | observational |
| `beforeSubmitPrompt`   | `context-reset-monitor.py`                                                        | advisory |
| `stop`                 | `memory-consolidator.sh`, `log.py context snapshot`, `session-end-reminder.sh`   | observational |
| `sessionStart`         | `log.py session reap`, `log.py memory retain`, `lesson-harvester.sh`, `router-health-check.sh`, `health-banner.sh` | observational |
| `subagentStart`        | `broker-gate.py`, `skills-required-guard.sh`, `persona-alias-resolver.sh`, `parallel-first-check.sh` | advisory — every dispatch gate logged, none blocked |
| `subagentStop`         | `lens-gate.sh`, `root-cause-gate.sh`, `verify-deliverables.sh`, `return-summarizer.sh` | observational |

Note that under Claude Code `persona-alias-resolver.sh` HARD-blocks a retired base name, and
under Cursor it stays a hard block. Under **Codex it is advisory** like every other gate — the
retired-name verdict is logged, and the orchestrator is trusted to honor it. This is the honest
ceiling for a runtime with no fail-closed hook contract; a future upgrade to F1 parity would
require reverse-engineering a Codex hard-deny envelope per event (DEC-102 rejected that as
brittle for now).

## SocratiCode read/grep gate

The SocratiCode-first read/grep gate (`socraticode-gate.sh`) is **not wired** into the Codex
adapter — same rationale as the Cursor bridge: the MCP→flag open-path is unreliable outside
Claude Code and the gate would block reads/greps permanently. SocratiCode-first remains fully
enforced under Claude Code via `.claude/settings.json`.

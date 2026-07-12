# Nexus Install Guide

Operator reference for **fresh installs** via `install.sh`. For updating an existing
install see [UPDATE.md](UPDATE.md). For the stack-profile schema and the full
install/update flow diagram see [STACK-PROFILE.md](STACK-PROFILE.md).

---

## Prerequisites

- `git` on PATH (required for the worktree sweep when installing over an existing Nexus)
- `uv` on PATH (recommended; `python -m venv` + `pip` are the fallback)
- Python ≥ 3.11 on PATH (for the `.memory/.venv` build — see below)
- The target directory must exist OR will be created by the script

---

## Usage

```bash
# Fresh install — auto-detect the target's stack
./install.sh <target-directory>

# Fresh install — supply an explicit profile
./install.sh <target-directory> profile.json
```

- `<target-directory>` is the project root you are installing into.
- `[profile.json]` is an optional path to a pre-built stack profile. When omitted the
  script calls `tools/stack_profile.detect_stack` to auto-detect the stack from the
  target. If detection raises `DetectionError` (no recognisable signals found), the
  bundled `profiles/reference.json` is used as a fallback.

The script uses `set -euo pipefail`; any unhandled error exits non-zero immediately
with a named `[install] FAIL:` message.

---

## What happens — step by step

### 1. Pre-backup preparation (installing over an existing install only)

Skipped cleanly on a genuinely fresh target (no `.claude/` present).

When `.claude/` exists the script runs a Python inline block that:

**a) Orphan-worktree sweep (DEC-008)**

DEC-008 parallel workflows create agent git worktrees under `.claude/worktrees/` and
own their removal. A worktree that was never cleaned up can run several GB on disk
(the `ai-interaction-dash` incident reached 5.4 GB). The sweep runs **before** the
size estimate so orphan bulk never counts toward the ceiling:

1. `git worktree prune` — clears administrative records for dirs that are already gone.
2. For each remaining dir under `.claude/worktrees/`: `git worktree unlock` (best-effort,
   no-ops for non-locked worktrees), then `git worktree remove --force`.
3. If `git worktree remove` fails and the dir still exists, a direct `shutil.rmtree`
   reclaims the disk.
4. Any worktree that cannot be removed ends up in `unreclaimed` and a WARNING is
   printed naming the manual reclaim command; the install is NOT blocked.

**b) Backup-size guard (`NEXUS_BACKUP_MAX_MB`)**

After the sweep, the script estimates the size of **only the surfaces the backup
actually archives** — `.claude/` and `.memory/` — with excluded bulk (`.venv`,
`node_modules`, `__pycache__`, `models`, `worktrees`, model blobs) stripped from the
count.

The default ceiling is **200 MB**. Override it:

```bash
NEXUS_BACKUP_MAX_MB=500 ./install.sh <target>
```

If the estimated size exceeds the ceiling the script prints the dominant surfaces and
exits 1 with `[install] FAIL: backup aborted`. Reclaim space or raise the ceiling.

**c) Timestamped backup**

When the size check passes:

```
<target>/.claude  →  <target>/.claude.pre-nexus.<epoch-seconds>
<target>/.memory  →  <target>/.memory.pre-nexus.<epoch-seconds>  (if present)
```

Both are plain directory renames (atomic on the same filesystem). The epoch timestamp
makes successive reinstalls non-colliding. These directories are never removed by
Nexus; the operator manages them.

### 2. Profile-aware render

```
render_install(profile, package_dir, target)
```

`render_install` resolves the file manifest (`resolve_file_manifest`), copies only the
agents and skills the profile's stack needs, and substitutes all double-underscore token
placeholders from the profile. Files outside the ship-set are never written.

After render, the **verify-coverage gate** asserts the rendered roster covers the
detected stack before any post-render step commits the tree:

- Agnostic floor always required: `nexus-orchestrator`, `scout`, `lens`, `lens-fast`.
- `frontend.present=true` → `forge-ui` AND `forge-wire` must be in the roster.
- `workers.present=true` → `pipeline-async` must be in the roster.
- `backend.present=true` → at least one of `forge-wire`, `pipeline-data`,
  `pipeline-async` must be in the roster.
- `.claude/settings.json` must carry a non-empty top-level `agent` key (so
  `nexus-orchestrator` auto-loads at session start).
- `.claude/agents/nexus-orchestrator.md` and `.memory/nexus-stack.json` must exist
  non-empty.

A coverage miss aborts with `[install] FAIL: verify-coverage gate` naming the
uncovered surface and missing personas.

### 3. Bytecode prune (first pass)

`find` sweeps `__pycache__/` and `*.pyc` from the entire target tree. The render
filter already excludes bytecode; this is a defence-in-depth pass.

### 4. nexus-broker copy

If the package ships a `nexus-broker/` tree the script:

1. Removes any pre-existing `<target>/nexus-broker/` (prevents cp permission errors
   on existing `.venv` symlinks).
2. `cp -r nexus-broker/ <target>/`.
3. Prunes ephemeral build artifacts that rode along in the copy: `.venv`,
   `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `__pycache__`, `*.pyc`. The
   target rebuilds its own `.venv` via `uv` on first use.
4. Asserts four required source files are present post-copy:
   `src/broker/server.py`, `src/broker/vault/stdio.py`, `pyproject.toml`, `uv.lock`.
   A missing file is a hard `[install] FAIL`.

### 5. Router-capture carry-forward

If a `.memory/` backup exists from step 1, the script copies two accumulated
training-data log files forward into the new install (only when they did not already
land via render) so they are not orphaned in the backup:

- `.memory/files/router_decisions.jsonl`
- `.memory/files/router_dispatches.jsonl`

### 6. Post-render token rebase

`render_install` bakes the staging/install path and the Arize project slug into every
rendered file. A follow-up Python inline block substitutes two tokens across all files
under `.claude/`, `.memory/`, `.cursor/`, and the top-level `CLAUDE.md` and `.mcp.json`:

| Token (bare name) | Substituted value |
|---|---|
| `INSTALL_ROOT` | Absolute path to `<target>` |
| `ARIZE_PROJECT_NAME` | `basename(<target>)` lowercased, spaces/underscores → `-` |

Scanned extensions: `.md`, `.json`, `.sh`, `.py`, `.toml`, `.yaml`, `.yml`, `.sql`.
`settings.local.json` is excluded (project-owned). `.cursor/mcp.json` is included
because Cursor reads its own copy of the broker path.

### 7. Hook permissions

`chmod +x` is applied to every `.sh` and `.py` file under `.claude/hooks/`. The hooks
directory is asserted to exist; a missing hooks dir is `[install] FAIL`.

### 8. Un-substituted-source-literal scan

A post-install scan checks rendered files for:

- **Home-path literals** — `/Users/<name>/...` or `/home/<name>/...` that should have
  been replaced by the `INSTALL_ROOT` token. Lines containing the install's own root are
  skipped (own-path-safe). `.memory/*.py` tooling files are scanned unconditionally
  (no own-path skip) because they must carry no absolute paths.
- **Surviving double-underscore token placeholders** in install-critical configs (`.mcp.json`,
  `.cursor/mcp.json`). A surviving token here means the broker path is unrendered and
  the broker will not boot; this is a **hard `[install] FAIL`**.
- **Custom deny terms** via `NEXUS_LEAK_EXTRA_TERMS` (comma-separated).

Warnings (non-fatal) are printed for home-path hits; a surviving unrendered double-underscore token in an
MCP config is fatal.

### 9. Version stamp

Two files are written into `<target>`:

| File | Content |
|---|---|
| `.memory/.nexus-version` | Single-line version string (e.g. `1.14.0\n`) |
| `.nexus-ledger.json` | `{version, installed_at, updated_at, source, phase_markers}` |

`installed_at` is set only on first stamp and preserved across subsequent updates.
`updated_at` always refreshes. `phase_markers` is an append-only list of
`{version, applied_at, summary}` entries; a version entry is updated in-place if it
already exists.

### 10. .memory tooling delivery

The following files are copied byte-for-byte from the package (not rendered — no
profile tokens):

| File | Required |
|---|---|
| `.memory/log.py` | Yes — hard fail if missing in package |
| `.memory/schema.sql` | Yes |
| `.memory/sync_docs.py` | Yes — the Stop hook runs it every session turn |
| `.memory/health.py` | No — copied if present |

`project.db` is **never** seeded by the install; the schema init in the next step
creates it.

### 11. Python ≥ 3.11 resolver and `.memory/.venv` build

`log.py` re-execs into `.memory/.venv/bin/python` at startup to obtain Python ≥ 3.12
and `sqlite-vec` (semantic recall). The venv build is **best-effort**: a failure warns
loudly and the install continues — the core DB still initialises under ambient `python3`.

The resolver tries candidates in order:

1. `python3.13` / `python3.12` / `python3.11` — versioned binaries on PATH.
2. Absolute Homebrew paths (Apple Silicon `/opt/homebrew` then Intel `/usr/local`),
   both versioned binary and `opt/python@N.N` formula link, for each of 3.13/3.12/3.11.
3. Bare `python3` on PATH, only if it self-reports `>= (3, 11)`.

If a `>= 3.11` interpreter is found:
- **`uv` present:** `uv venv <venv_dir> --python <py>` then
  `uv pip install sqlite-vec`.
- **`uv` absent:** `<py> -m venv <venv_dir>` then `<venv>/bin/python3 -m pip install sqlite-vec`.

**Remediation when no Python ≥ 3.11 is found:**

```bash
brew install python@3.12
cd <target> && python3.12 .memory/log.py init
```

### 12. DB schema init

```bash
python3 <target>/.memory/log.py init
```

`log.py init` is idempotent (`CREATE TABLE IF NOT EXISTS`). A non-zero exit is a hard
`[install] FAIL: project.db not initialized`.

### 13. Post-install health gate

Three behavioural checks — not just "files copied":

1. `.memory/log.py` exists.
2. Core tables are present in `project.db`. The canonical table set is loaded from
   `health.CORE_TABLES` (6 tables: `sessions`, `tasks`, `decisions`, `lessons`,
   `semantic_facts`, `context_log`). A missing table is a hard fail. If the import
   fails (degraded box), a fallback literal of 8 tables (adding `validation_log` and
   `embed_outbox`) is used — but the normal path is the 6-table public export.
3. `log.py notepad list --topic _health_gate_check` exits 0 (the step-0 notepad
   ritual works).

Any failure → `[install] FAIL: persistence layer not initialized`.

### 14. Broker-boot probe (loud, non-fatal)

```bash
uv run --quiet --directory <target>/nexus-broker \
  python -c 'import broker.server, broker.vault.stdio; print(broker.server.mcp.name)'
```

A failed import prints a boxed WARNING naming the remediation steps but does **not**
fail the install. The broker's health state persists: the SessionStart health banner
shows a static `✗` for the broker on every session until the boot succeeds.

**Remediation:**

```bash
uv sync --directory <install-root>/nexus-broker
# then re-probe:
uv run --directory <install-root>/nexus-broker \
  python -c 'import broker.server, broker.vault.stdio'
```

After the probe, ephemeral build artifacts the probe created (`.venv`, `__pycache__`,
`*.pyc`) are pruned from the nexus-broker tree so the shipped install carries only
source files.

### 15. Final bytecode prune

A final `find` sweep removes all `__pycache__/` and `*.pyc` outside `.venv`. This
runs last because the health-gate import and broker-boot probe can regenerate bytecode
in `.memory/` and `nexus-broker/src/`.

---

## Completion

On success:

```
Nexus <version> installed at <target>
Next (Claude Code): cd "<target>" && claude
```

---

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `NEXUS_BACKUP_MAX_MB` | `200` | Backup-surface size ceiling in MB; raise to allow a larger `.claude/` + `.memory/` |
| `NEXUS_LEAK_EXTRA_TERMS` | _(empty)_ | Comma-separated extra terms the post-install literal scan will flag |
| `NEXUS_FORCE_PERSONA_SET` | _(unset)_ | **Test-only seam** — never set in production |

---

## Next step (Claude Code)

```bash
cd "<target>"
claude
```

Nexus auto-loads as the main session via `agent: nexus-orchestrator` in
`.claude/settings.json`. The SessionStart health banner prints `Nexus v<version>` and
checks the broker, DB, and key surfaces.

**Cursor users:** go to Settings > Tools & MCP and toggle `nexus-broker` ON (Cursor
does not auto-enable MCP servers from `mcp.json`). If you opted into PRISM, toggle
`prism` ON there too.

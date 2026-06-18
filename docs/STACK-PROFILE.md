# Stack Profile — Design Contract

Version: 1.0  
Status: ACTIVE — the renderer and detector are implemented and covered by tests; additive changes only.

---

## Purpose

`nexus-stack.json` (a `.memory/nexus-stack.json` file in each installed project) is the single source of truth for what technology a project uses. The installer reads it to:

1. Render `__TOKEN__` placeholders in persona, hook, and settings templates.
2. Resolve which agent `.md` files and skill directories ship vs. are omitted.

The profile never encodes Nexus orchestration details — only the project's own stack.

---

## Schema location

Canonical JSON Schema (draft 2020-12): `nexus-package/.memory/nexus-stack.schema.json`

Every profile file under `nexus-package/profiles/` MUST validate against it.

---

## Token Vocabulary

Each `__TOKEN__` maps to exactly one profile field. Renderers substitute the field value verbatim (string) or via a simple transform (array → comma-joined, bool → "true"/"false").

| Token | Source field | Notes |
|---|---|---|
| `next` | `frontend.framework` | e.g. `next`, `vite`, `none` |
| `app/apps/dashboard/src` | `frontend.src_dir` | relative path |
| `app/apps/dashboard` | `frontend.test_dir` | relative path |
| `app/apps/dashboard` | `frontend.ts_check_dir` | dir passed to `tsc --noEmit` |
| `none` | `frontend.test_runner` | `vitest`, `jest`, `none` |
| `ts` | `backend.language` | `python`, `ts`, `none` |
| `app/apps/api/src` | `backend.src_dir` | relative path |
| `` | `backend.py_check_dir` | dir passed to `ruff`/`mypy`; empty string when null |
| `postgres` | `data.db` | `duckdb`, `postgres`, `sqlite`, `none` |
| `pgvector` | `data.vector` | `hnsw`, `pgvector`, `lmstudio`, `none` |
| `` | `data.ingestion_dir` | empty string when null |
| `none` | `data.semantic_layer` | `malloy`, `none` |
| `` | `data.models_dir` | empty string when null |
| `vercel-ai-sdk-v4` | `ai_layer` | full enum value |
| `` | `ai_model` | e.g. `claude-sonnet-4-6` |
| `bun` | `package_manager` | `pnpm`, `npm`, `uv`, `mixed` |
| `/app/apps/, /app/packages/` | `socraticode_watched_prefixes` | comma-joined, e.g. `/app/, /ingestion/` |
| `supabase, slack, trigger.dev, posthog` | `integration_targets` | comma-joined; empty string when `[]` |
| `mcp-server` | `mcp_server_dir` | empty string when null |

---

## File-Inclusion Manifest

### Agnostic set — always ship

These files ship regardless of stack profile:

**Agents:**
- `nexus-orchestrator`
- `scout`
- `lens`
- `lens-fast`
- `palette`
- `quill`

**Skills (core protocol):**
- `nexus-protocol`
- `nexus-health`
- `tdd-patterns`
- `verification-protocols`
- `parallel-first-check`
- `team-routing`
- `project-context`
- `log-work`
- `contract-schema`

---

### Conditional rules

Rules are evaluated top-to-bottom; a file may match multiple rules (union, not exclusive).

#### Python backend / ingestion

Condition: `backend.language == "python"` OR `data.has_ingestion == true`

Ship agents:
- `pipeline`
- `pipeline-data`
- `pipeline-data-pro`
- `pipeline-async`
- `pipeline-async-pro`
- `quill-py`

Ship skills:
- `pytest-idioms`
- `pipeline-data-conventions`
- `pipeline-async-conventions`

#### DuckDB data layer

Condition: `data.db == "duckdb"`

Ship agents:
- `atlas`

Ship skills:
- `duckdb-read-patterns`
- `duckdb-test-shims`
- `polars-duckdb-mapping`
- `polars-test-fixtures`
- `atlas-schema-patterns`

#### Malloy semantic layer

Condition: `data.semantic_layer == "malloy"`

Ship skills:
- `atlas-schema-patterns` (also covers Malloy `.malloy` model conventions)

Note: `data.semantic_layer == "malloy"` implies `data.db == "duckdb"` in practice, so atlas agent will already be included by the DuckDB rule.

#### Dramatiq workers

Condition: `workers.present == true` AND `workers.framework == "dramatiq"`

Ship skills:
- `dramatiq-patterns`

#### Next.js or Vite frontend

Condition: `frontend.framework in ["next", "vite"]`

Ship agents:
- `forge`
- `forge-ui`
- `forge-ui-pro`
- `forge-wire`
- `forge-wire-pro`
- `quill-ts`

Ship skills:
- `forge-ui-conventions`
- `forge-wire-conventions`
- `rsc-boundary-rules`
- `server-action-contract`
- `vitest-rtl-idioms` (alias: `tdd-patterns` already in agnostic set; this is the Vitest-specific extension)

Note: `rsc-boundary-rules` and `server-action-contract` are Next.js-specific but low-cost to include for Vite too; they document inapplicability clearly.

#### Tremor UI library

Condition: `frontend.ui_lib == "tremor"`

Ship skills:
- `tremor-patterns`

#### Tailwind / shadcn UI library

Condition: `frontend.ui_lib in ["tailwind", "shadcn"]`

Ship skills:
- `tailwind-design-tokens`

#### MUI UI library

Condition: `frontend.ui_lib == "mui"`

Ship skills:
- (no dedicated MUI skill in current package; omit tremor/tailwind skills)

#### MCP server present

Condition: `mcp_server_dir != null`

Ship agents:
- `hermes`

Ship skills:
- `hermes-auth-patterns`

#### Vercel AI SDK

Condition: `ai_layer in ["vercel-ai-sdk-v4", "vercel-ai-sdk-v6"]`

Ship skills:
- `ai-sdk-patterns`
- `embedding-patterns`

#### Anthropic direct

Condition: `ai_layer == "anthropic-direct"`

Ship skills:
- `embedding-patterns`

#### Tableau integration

Condition: `"tableau" in integration_targets`

Ship skills:
- `tableau`
- `tableau-client-patterns`

#### Postgres data layer

Condition: `data.db == "postgres"`

Ship agents:
- `atlas`

Ship skills:
- `atlas-schema-patterns`

---

## Omit logic

Any agent or skill NOT matched by the agnostic set or a conditional rule above is OMITTED. The `resolve_file_manifest` function returns both a `ship` list and an `omit` list for auditability.

---

## API Signatures (implemented + tested)

These functions are implemented and covered by tests. Signatures are fixed here as the contract.

### `detect_stack`

```python
def detect_stack(project_root: Path) -> dict:
    ...
```

**Input:** Absolute path to a project root directory.

**Output:** A `dict` conforming to `nexus-stack.schema.json`. The detector inspects the filesystem (presence of `package.json`, `pyproject.toml`, `next.config.*`, `vite.config.*`, etc.) and produces a best-guess profile. The caller is responsible for writing the result to `.memory/nexus-stack.json`.

**Errors:** Raises `ValueError` if `project_root` does not exist or is not a directory. Raises `DetectionError` (custom) if too few signals are found to produce a valid profile.

**Note:** Detection is a best-effort heuristic. The profile should always be reviewed by a human before a fresh install finalises it.

---

### `render_template`

```python
def render_template(text: str, profile: dict) -> str:
    ...
```

**Input:**
- `text`: raw template string containing zero or more `__TOKEN__` placeholders from the vocabulary table above.
- `profile`: a validated stack profile dict (conforming to the schema).

**Output:** `text` with all `__TOKEN__` placeholders replaced by their derived values. Unknown tokens (not in the vocabulary) are left unchanged with a warning logged.

**Errors:** Does not raise on unknown tokens. Raises `KeyError` only if a token maps to a field that is required but absent from `profile` (schema-invalid profile).

---

### `resolve_file_manifest`

```python
def resolve_file_manifest(profile: dict) -> dict[str, list[str]]:
    ...
```

**Input:** A validated stack profile dict.

**Output:** A dict with exactly two keys:
- `"ship"`: list of file identifiers (agent names and skill directory names) that should be included.
- `"omit"`: list of file identifiers that are NOT included for this profile.

The union of `ship` and `omit` MUST equal the full set of files in the nexus-package. Callers use `ship` to copy files into the target project; `omit` is provided for audit/logging.

---

## Never-clobber rule for updates

An update run MUST follow this invariant:

> If `.memory/nexus-stack.json` already exists in the target project, it MUST be read and used as-is. An update MUST NOT regenerate or overwrite it.

Rationale: The profile may have been hand-tuned after initial install (e.g., `my-dashboard`'s reference profile). Auto-detection runs only during a fresh install when no profile exists yet.

The update flow is therefore:
1. Read existing `.memory/nexus-stack.json`.
2. Validate it against `nexus-stack.schema.json` (fail loudly if invalid).
3. Run `render_template` over all template files using the validated profile.
4. Run `resolve_file_manifest` to determine which agents/skills to sync.
5. Sync files. Never touch `.memory/nexus-stack.json`.

If the profile is schema-invalid (e.g., after a schema version bump), the update MUST halt with a clear migration error rather than silently overwriting.

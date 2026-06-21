# FEAT-002 — Next features: Todos, Knowledge vault, Prompt library, MCP server

## Overview / Goal

Build four personal-assistant capabilities on top of the now-local Nexus app (FEAT-001), in dependency order: **Todos → Knowledge vault → Prompt library → MCP server**. The first three are user-facing dashboard pages; the fourth wraps all of them in a standalone stdio MCP server so Claude can drive them as a personal assistant.

**Stack:** Bun + Turborepo monorepo under `app/`; `apps/api` (Hono + tRPC + AI SDK), `apps/dashboard` (Next 15 App Router, Tiptap editor, dnd-kit, shadcn/ui); Postgres/pgvector + Redis local via docker-compose. New standalone `mcp-server/` Node project (built with `bun build` → `dist/index.js`).

**Why now:** FEAT-001 stripped the SaaS layers and made the app local-only and API-accessed. The owner now wants daily-driver capture (todos), an Obsidian-backed second brain (knowledge), a versioned prompt library, and a single MCP surface that exposes all three to any Claude Code session. The data tables for todos, todo_attachments, prompt_products, prompts, prompt_versions, knowledge_vaults, and knowledge_notes already exist in the schema; this feature adds only the wiki-link graph table and the FTS column for knowledge, then layers UI + the MCP wrapper on top.

**Scope of NEW schema (this feature):** one new table (`knowledge_links`) and one new column + index (`knowledge_notes.content_fts` tsvector + GIN). All other tables are pre-existing and require no migration.

---

## Acceptance Criteria (Given/When/Then)

### TASK-013 ✓ — Prompt library

**Seeded product card on a fresh team**
Given: a freshly provisioned team with no prompt_products rows created by hand
When: the owner opens `/team/[team]/prompts`
Then: a `kbuddy` product card is shown (auto-seeded on team bootstrap — no manual creation step), displaying its icon, name, and a prompt count of zero.

**Creating a prompt assigns slug + version 1**
Given: the `kbuddy` product is open with an empty prompt list
When: the owner creates a prompt named "Onboarding"
Then: the new `prompts` row has a generated `slug` derived from the name and `version = 1`, and the editor opens on that prompt.

**Variable auto-detection from content**
Given: a prompt editor open on a new prompt
When: the owner types content containing `{{name}}` and `{{email}}`
Then: the right rail auto-detects both `name` and `email` as variables, each with an editable default field, without the owner declaring them manually.

**Copy filled interpolates variables**
Given: a prompt whose content contains `{{name}}` and `{{email}}`, with the variable defaults filled to `Ada` and `ada@x.dev`
When: the owner clicks "Copy filled"
Then: the clipboard receives the prompt text with `{{name}}` replaced by `Ada` and `{{email}}` replaced by `ada@x.dev` (no remaining `{{...}}` placeholders for filled vars).

**Save as new version bumps the badge and preserves history**
Given: a prompt at `version = 1` open in the editor with edited content
When: the owner clicks "Save as new version"
Then: a new `prompt_versions` row is written, the version badge increments to 2, and the prior version (1) remains retrievable and visible in the version history.

**Project picker persists and is team-scoped**
Given: a prompt open in the editor and a project picker in the rail
When: the owner selects a project, reloads the page, then later clears the project via the picker
Then: the selected `projectId` persists on `prompts` across reload, clearing it nulls the field, AND a `setProject` call with a `projectId` that does not belong to the caller's team is rejected (not silently written).

**List rows show a project badge when scoped**
Given: a prompt list where some prompts have a `projectId` set and some do not
When: the owner views the prompt list
Then: each row that has a `projectId` shows a project badge, and rows without one show no badge.

**MCP exposes the prompt**
Given: a saved prompt under the `kbuddy` product
When: an MCP client calls `list_prompts` and then `get_prompt(productSlug, promptSlug)`
Then: `list_prompts` includes the prompt and `get_prompt` returns its content (with variables filled when `vars` is supplied).

### TASK-011 ✓ — Todos

**Quick-add at top, check moves to Completed**
Given: the todos page open with the header `+` input focused
When: the owner types a todo and presses Enter, then later checks its checkbox
Then: the todo is inserted at the top of the active list and the input clears for the next entry; checking it moves the row from the active list into the Completed group.

**Tag filter pill filters active todos**
Given: an active list with todos carrying different tags
When: the owner clicks a tag filter pill, then clicks "All"
Then: the active list is filtered to todos bearing that tag, and clicking "All" clears the filter and restores the full active list.

**Row project pill filters by project**
Given: a todo row that has a project pill
When: the owner clicks the project pill on that row
Then: the list is filtered to todos belonging to that project.

**Attachments render inline (note = editor, doc_link = embed)**
Given: a todo with one `note` attachment and one `doc_link` attachment
When: the owner opens the todo's attachments
Then: the `note` is shown as an editable Tiptap editor and the `doc_link` is shown as an inline `LibraryDetailView` embed of the rendered document (not a plain hyperlink or download).

**Check animation respects reduced motion**
Given: a todo being checked off
When: the checkbox is toggled with default motion preferences, and separately under `prefers-reduced-motion`
Then: with default preferences the row animates down to the Completed group; under `prefers-reduced-motion` the move is applied instantly with no animation.

**Drag reorder persists**
Given: the active todos list with a drag handle on each row
When: the owner drags a row to a new position
Then: the visual order updates and the new order persists (the `order` field is rewritten) so it survives a reload.

**Completed Collapsible animation timing**
Given: the Completed group rendered as a Collapsible
When: the owner expands it with default motion preferences, and separately under `prefers-reduced-motion`
Then: with default preferences it animates open over 150ms; under `prefers-reduced-motion` it opens instantly.

### TASK-012 ✓ — Knowledge vault

**Wiki-link creates a knowledge_links row on scan**
Given: a vault note whose content contains `[[Another Note]]`
When: the vault scanner processes that note
Then: a `knowledge_links` row is written with `from_note_id` = the source note, `link_text` = `Another Note`, and `to_note_id` resolved to the target note when it exists (nullable when unresolved).

**Backlinks group for inbound links**
Given: a note that is the target of one or more `[[...]]` links from other notes
When: the owner opens that note
Then: a "Knowledge backlinks" group is shown listing the notes that link to it.

**Search is FTS-ranked, not ILIKE**
Given: a populated vault and a search box
When: the owner searches for a term
Then: results are produced by a Postgres full-text query ranked over the `content_fts` tsvector (via `ts_rank`/`@@`), not an `ILIKE` substring scan, and the ordering reflects FTS rank.

**Resolved vs unresolved link coloring**
Given: a note rendered with `[[Note]]` links where some targets exist and some do not
When: the note renders
Then: links whose target resolves render blue and links whose target is unresolved render red.

**Auto-save on blur within 500ms**
Given: an open note editor with unsaved edits
When: the owner blurs the editor (leaves it)
Then: the note auto-saves within 500ms (atomic `.tmp` + rename on disk, mirrored to `knowledge_notes`).

**Slash menu on `/`**
Given: the note editor focused
When: the owner types `/`
Then: a slash command menu opens offering block insertions (consistent with the docs Tiptap editor).

**Focus mode route**
Given: a note with id `noteId`
When: the owner navigates to `/team/[team]/knowledge/[noteId]`
Then: the single-note focus-mode view renders that note.

**Vault path setting persists**
Given: the knowledge settings page at `/team/[team]/settings/knowledge`
When: the owner changes the vault `root_path` and saves
Then: the new path is written to `knowledge_vaults.root_path` and persists across reload.

### TASK-014 ✓ — MCP server

**Type check and build succeed**
Given: the `mcp-server/` project source
When: `bun run check-types` and then `bun run build` are run
Then: `check-types` exits 0 and `build` produces `dist/index.js`.

**List tools returns all 11 tools**
Given: the built MCP server registered over stdio
When: a client issues a LIST TOOLS request
Then: all 11 tools are returned: `add_todo`, `list_todos`, `check_todo`, `add_task`, `list_tasks_due_soon`, `list_projects`, `search_knowledge`, `read_note`, `write_note`, `list_prompts`, `get_prompt`.

**add_task creates a task**
Given: a connected MCP client and a valid project
When: the client calls `add_task(title, project)`
Then: a new task row is created on that project and the tool returns the created task's id.

**get_prompt substitutes placeholders**
Given: a prompt whose content contains `{{var}}`
When: the client calls `get_prompt(productSlug, promptSlug, vars)` with a value for `var`
Then: the returned content has every `{{var}}` placeholder substituted with the supplied value.

**write_note is confined to the knowledge root**
Given: a configured vault root path
When: the client calls `write_note(path, content)` with a `path` that escapes the knowledge root (e.g. `../outside.md`)
Then: the call errors and writes nothing outside the root.

**Team scoping isolates list_todos**
Given: two teams each with their own todos and the server configured with `NEXUS_TEAM_ID`
When: the client calls `list_todos`
Then: only todos belonging to the configured team are returned; other teams' todos are not visible.

---

## Constitution Check

- `Article I` (TDD): UI-behavior tasks (TASK-011, TASK-012, TASK-013) and the MCP task (TASK-014) require feat-2-tagged Quill tests written before Forge/Pipeline implement — quick-add ordering, check-to-Completed move, drag-persist, variable auto-detection, FTS ranking, wiki-link row creation, path-escape rejection, and team-scoping isolation are all asserted by tests first. Schema work (the `knowledge_links` table + `content_fts` GIN index) is verified by the Verification SQL in this spec, executed by Pipeline.
- `Article X` (RCA): the knowledge scanner change documents its full blast radius (which notes re-scan, how `to_note_id` resolves on rename/delete) before merge; no silent link deletion — orphaned `knowledge_links` rows are nulled, not dropped, when a target disappears.
- `Article XII` (deploy via human handoff): each implementation phase ends with a `## Deploy step` block naming the Docker restart action (api + dashboard) + the verification command; the owner approves before rebuild; Nexus does not deploy autonomously. The `mcp-server` build (`bun run build`) and `~/.claude/mcp.json` registration are an owner-approved manual step.
- `Article XIII` (parallel-first): TASK-013 (prompts), TASK-011 (todos), and TASK-012 (knowledge) touch disjoint route subtrees and tables and may be dispatched in parallel where the dependency graph allows; TASK-014 (MCP) depends on all three tRPC surfaces and is sequenced last. The single schema migration (knowledge_links + content_fts) is a one-shot Pipeline dispatch that gates TASK-012's FTS search and backlinks.
- `Article XIV` (session-branch commit-as-checkpoint): one focused commit per task on the session branch; each commit is the rollback unit; no per-task feature branches; sub-agents commit and do not push.

---

## Architecture Summary

**Routes added:**

| Area | Routes |
|---|---|
| Todos | `/team/[team]/todos` |
| Knowledge | `/team/[team]/knowledge`, `/team/[team]/knowledge/[noteId]`, `/team/[team]/settings/knowledge` |
| Prompts | `/team/[team]/prompts`, `/team/[team]/prompts/[productSlug]`, `/team/[team]/prompts/[productSlug]/[promptSlug]` |

**tRPC surfaces:** `todos.*` (get/create/update/check/uncheck/reorder/delete/attach/detach), `prompts.*` (product + prompt CRUD, setProject, save-as-new-version), `knowledge.*` (tree/search/scan/note CRUD/vault settings). The MCP server is a thin stdio wrapper over these procedures, authenticated by the local-dev injection / static `NEXUS_API_TOKEN` established in FEAT-001.

**Knowledge vault on disk:** Obsidian-compatible markdown directory bind-mounted into the api container (Library-style scoped mount). Default root `/Users/john.keeney/nexus-knowledge`. Scanner indexes files into `knowledge_notes`, extracts `[[wiki links]]` into `knowledge_links`, and maintains `content_fts` for ranked search. `.obsidian/` is ignored.

**Editor reuse:** Tiptap component with the mermaid node + slash menu (already built for docs) is reused by todo `note` attachments and the knowledge note editor. Todo `doc_link` attachments embed `LibraryDetailView` inline.

---

## Schema Changes

### Existing tables (already present — NO migration needed)

These tables already exist in `app/packages/db/src/schema.ts` and are documented here for reference only. No DDL is emitted for them.

```sql
-- todos: quick-capture checklist items, optionally project-scoped.
--   id PK, team_id FK teams NOT NULL, user_id FK users NOT NULL,
--   content TEXT NOT NULL, project_id FK projects NULLABLE,
--   checked BOOLEAN DEFAULT false, checked_at TIMESTAMPTZ NULLABLE,
--   tags TEXT[] DEFAULT '{}', "order" NUMERIC(100,5) NOT NULL,
--   created_at / updated_at TIMESTAMPTZ.
-- todo_attachments: inline note or doc_link attached to a todo.
--   id PK, todo_id FK todos NOT NULL, kind ENUM('note','doc_link'),
--   title TEXT NOT NULL, content TEXT NULLABLE (markdown when kind=note),
--   doc_id FK documents NULLABLE (when kind=doc_link), created_at TIMESTAMPTZ.
-- prompt_products: a product grouping prompts (e.g. kbuddy).
--   id PK, team_id FK teams, name/slug NOT NULL, description/icon/color,
--   archived BOOLEAN DEFAULT false, UNIQUE(team_id, slug).
-- prompts: a prompt belonging to a product, with detected variables.
--   id PK, product_id FK prompt_products, name/slug/content NOT NULL,
--   notes TEXT NULLABLE, variables JSONB, tags TEXT[] DEFAULT '{}',
--   version INTEGER DEFAULT 1, project_id FK projects NULLABLE,
--   UNIQUE(product_id, slug).
-- prompt_versions: immutable history rows captured on "Save as new version".
--   id PK, prompt_id FK prompts, version INTEGER NOT NULL, content TEXT NOT NULL,
--   notes TEXT NULLABLE, created_at TIMESTAMPTZ.
-- knowledge_vaults: a vault source (Obsidian directory).
--   id PK, team_id FK teams NOT NULL, label/root_path NOT NULL,
--   is_default BOOLEAN DEFAULT true, created_at TIMESTAMPTZ.
-- knowledge_notes: a scanned markdown note.
--   id PK, vault_id FK NOT NULL, relative_path/absolute_path/name NOT NULL,
--   kind ENUM('note','folder'), content TEXT NULLABLE, frontmatter JSONB NULLABLE,
--   file_sha TEXT NOT NULL, last_seen_at/last_edited_at/created_at/updated_at,
--   UNIQUE(vault_id, relative_path).
```

### NEW schema (this feature) — forward-only migration M-002

Two changes, both for TASK-012 (knowledge vault):

1. `knowledge_links` — the wiki-link graph edge table.
2. `knowledge_notes.content_fts` — a generated tsvector column + GIN index powering FTS search and backlinks.

#### `upgrade()` (migration body)

```sql
-- 1. Wiki-link graph edges. One row per [[...]] occurrence in a source note.
CREATE TABLE IF NOT EXISTS knowledge_links (
  id           TEXT PRIMARY KEY,                        -- edge id
  from_note_id TEXT NOT NULL                            -- the note containing the [[link]]
                 REFERENCES knowledge_notes(id) ON DELETE CASCADE,
  to_note_id   TEXT                                     -- resolved target; NULL when unresolved
                 REFERENCES knowledge_notes(id) ON DELETE SET NULL,
  link_text    TEXT NOT NULL,                           -- raw inner text of the [[wiki link]]
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()       -- when the edge was first indexed
);

COMMENT ON COLUMN knowledge_links.id           IS 'Edge id (text PK).';
COMMENT ON COLUMN knowledge_links.from_note_id IS 'Source note containing the [[link]]; cascades on note delete.';
COMMENT ON COLUMN knowledge_links.to_note_id   IS 'Resolved target note; NULL when the link is unresolved or the target is later deleted (SET NULL).';
COMMENT ON COLUMN knowledge_links.link_text    IS 'Raw inner text of the [[wiki link]] (case-insensitive basename resolution).';
COMMENT ON COLUMN knowledge_links.created_at   IS 'Timestamp the edge was first indexed by the scanner.';

-- Forward-lookup (outbound links from a note) and backlink-lookup (inbound links to a note).
CREATE INDEX IF NOT EXISTS idx_knowledge_links_from_note_id ON knowledge_links (from_note_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_links_to_note_id   ON knowledge_links (to_note_id);

-- 2. FTS column on knowledge_notes: generated tsvector over name + content.
ALTER TABLE knowledge_notes
  ADD COLUMN IF NOT EXISTS content_fts tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(content, '')), 'B')
  ) STORED;

COMMENT ON COLUMN knowledge_notes.content_fts IS 'Generated FTS vector: name (weight A) + content (weight B), english config. Drives ranked search and replaces ILIKE scans.';

-- GIN index for fast @@ / ts_rank queries against content_fts.
CREATE INDEX IF NOT EXISTS idx_knowledge_notes_content_fts
  ON knowledge_notes USING gin (content_fts);
```

#### `downgrade()` (exact reverse — forward-only, but rollback DDL is required and tested)

```sql
DROP INDEX IF EXISTS idx_knowledge_notes_content_fts;
ALTER TABLE knowledge_notes DROP COLUMN IF EXISTS content_fts;

DROP INDEX IF EXISTS idx_knowledge_links_to_note_id;
DROP INDEX IF EXISTS idx_knowledge_links_from_note_id;
DROP TABLE IF EXISTS knowledge_links;
```

#### Notes on design choices

- `to_note_id` is **nullable** and `ON DELETE SET NULL`: a `[[link]]` can point at a not-yet-created note (unresolved → NULL) and must not vanish if its target is later deleted (Article X: no silent link deletion — the edge survives with a null target and renders red).
- `from_note_id` is `ON DELETE CASCADE`: deleting the source note removes its outbound edges (they have no meaning without the source).
- `content_fts` is a **STORED generated column** so it is always consistent with `name`/`content` without a trigger; the GIN index makes `WHERE content_fts @@ websearch_to_tsquery(...)` ranked queries index-backed.
- No vector column is introduced in this feature — knowledge search is lexical FTS, not embedding similarity. (If semantic search is added later it is a separate migration adding `embedding vector(768)` + an HNSW `vector_cosine_ops` index, out of scope here.)

### Apply plan (Pipeline executes — Atlas cannot run these)

1. `alembic revision -m "feat-002 knowledge_links + content_fts"` (or the project's Drizzle migration generate), pasting the `upgrade()` / `downgrade()` bodies above.
2. Review the generated revision.
3. `alembic upgrade head` (or `drizzle-kit migrate`).

### Verification SQL (Pipeline runs and reports back)

```sql
-- expected to succeed: the migration applies cleanly (run the upgrade() body above).

-- knowledge_links table exists with both indexes:
\d knowledge_links
-- expected: columns id, from_note_id, to_note_id (nullable), link_text, created_at;
--           indexes idx_knowledge_links_from_note_id, idx_knowledge_links_to_note_id present.

-- content_fts column + GIN index exist:
\d knowledge_notes
-- expected: a content_fts column of type tsvector (generated), and index
--           idx_knowledge_notes_content_fts USING gin (content_fts).

-- expected: a ranked FTS query uses the GIN index (not a seq scan):
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, ts_rank(content_fts, websearch_to_tsquery('english', 'onboarding')) AS rank
FROM knowledge_notes
WHERE content_fts @@ websearch_to_tsquery('english', 'onboarding')
ORDER BY rank DESC
LIMIT 20;
-- expected plan: Bitmap Index Scan on idx_knowledge_notes_content_fts (assumes > ~1k notes;
--                below that Postgres may legitimately prefer a seq scan — re-check at scale).

-- backlink lookup uses the to_note_id index:
EXPLAIN
SELECT * FROM knowledge_links WHERE to_note_id = '<some-note-id>';
-- expected plan: Index Scan using idx_knowledge_links_to_note_id.

-- expected count after a scan of a note containing one [[Another Note]] that resolves:
SELECT count(*) FROM knowledge_links WHERE link_text = 'Another Note';
-- expected: = 1 (one edge per [[...]] occurrence).
```

**Index benchmark plan:** capture `EXPLAIN (ANALYZE, BUFFERS)` for the ranked FTS query above both before (no `content_fts`, ILIKE baseline) and after (GIN-backed) on a vault of ~2,000 notes; the after-plan must show a Bitmap Index Scan on `idx_knowledge_notes_content_fts` and lower total buffers than the ILIKE seq-scan baseline.

---

## Test Strategy

**Schema (M-002):** verified by the Verification SQL above, executed by Pipeline (`\d` structure checks, `EXPLAIN` index-usage checks, and the link-count assertion).

**TASK-011 / TASK-012 / TASK-013 (UI behavior):** feat-2-tagged Quill tests (Vitest + RTL) written before Forge implements — quick-add inserts at top + clears input; check moves row to Completed; `prefers-reduced-motion` skips the animation; drag reorder rewrites `order` and persists; tag/project pill filters; variable auto-detection from `{{...}}`; "Copy filled" interpolation; "Save as new version" writes a `prompt_versions` row and bumps the badge; `setProject` rejects a foreign-team project; wiki-link scan writes a `knowledge_links` row; FTS search returns ranked results; resolved/unresolved link coloring; auto-save within 500ms.

**TASK-014 (MCP server):** feat-2-tagged tests assert `bun run check-types` exits 0, `bun run build` emits `dist/index.js`, LIST TOOLS returns all 11 tools, `add_task` creates a row, `get_prompt(vars)` substitutes `{{var}}`, `write_note` rejects a path escaping the knowledge root, and `list_todos` is isolated by `NEXUS_TEAM_ID`.

---

## Decisions

**DEC-002a** (knowledge link model): wiki-links are stored as a dedicated edge table `knowledge_links` (not a JSONB array on the note) so backlinks are an indexed `to_note_id` lookup. `to_note_id` is nullable + `ON DELETE SET NULL` to support unresolved/forward links and survive target deletion; `from_note_id` is `ON DELETE CASCADE`.

**DEC-002b** (knowledge search): lexical FTS via a STORED generated `content_fts` tsvector + GIN index, weighting `name` (A) over `content` (B). No embedding/vector search in this feature; semantic search is a deferred separate migration.

**DEC-002c** (build order): Todos → Knowledge → Prompts → MCP, with the MCP server sequenced last because it wraps the other three tRPC surfaces. The single schema migration gates TASK-012.

**Path mappings (from FEAT-001):**
- Knowledge vault default root: `/Users/john.keeney/nexus-knowledge` (in-container `/host/knowledge`).
- MCP server project: `/Users/john.keeney/nexus-task-tracker/mcp-server/`.

---

## Do-Not-Touch

| Item | Reason |
|---|---|
| Existing `todos`, `todo_attachments`, `prompt_products`, `prompts`, `prompt_versions`, `knowledge_vaults`, `knowledge_notes` table DDL | Already present; this feature adds only `knowledge_links` + `content_fts`. No migration on existing tables. |
| `@mimir/*` package scope | Kept per FEAT-001 DEC-002; not renamed here. |
| `.obsidian/` directory inside any vault | Ignored by the scanner for Obsidian compatibility. |
| `prompt_versions` history rows | Append-only; "Save as new version" never mutates a prior version. |

# Mimrai — next-feature design brief

Four things to build, in this order: **Todos → Knowledge → Prompt Library → MCP**. The first three are user-facing pages in the dashboard; the fourth wraps them all in an MCP server so Claude can drive them as your personal assistant.

---

## 1. Todos

Different from "tasks." Tasks live inside projects and have due dates, priority, status. **Todos are quick captures — a daily checklist of random thoughts.** They might or might not belong to a project.

### Model

```
todos
  id            text pk
  team_id       text fk teams       not null
  user_id       text fk users       not null
  content       text                not null
  project_id    text fk projects    nullable
  checked       boolean default false
  checked_at    timestamp           nullable
  tags          text[] default {}
  order         numeric(100, 5)     not null
  created_at    timestamp
  updated_at    timestamp

todo_attachments
  id            text pk
  todo_id       text fk todos       not null
  kind          enum("note","doc_link")
  title         text                not null
  content       text                nullable   -- inline markdown when kind=note
  doc_id        text fk documents   nullable   -- when kind=doc_link
  created_at    timestamp
```

### Behavior (the part that matters)

- **Header has a `+` icon.** Click it → small inline text input opens right in the header. Type, hit Enter → todo created at top, input clears for the next one. Esc closes. Same muscle memory as Linear's `C`.
- **Drag handle on the left** of each row (using the `dnd-kit` library mimrai already uses for the kanban). Drag to reorder. Order persists.
- **Checkbox checks it off** → row gets line-through, content fades to muted color, **row animates down to the bottom of the list**. Unchecked items naturally float back to the top because checked items live in a "done" zone.
- **Click the row body** → modal opens showing attachments. If an attachment is a `note`, the modal renders its inline markdown (with our mermaid + slash-menu editor). If it's a `doc_link`, the modal shows the doc preview inline (not a download — actual rendered content, like the Library detail page).
- **Tag chips** inline on each row. Click `+ tag` to add. Filter the whole list by tag from the header.
- **Project pill** on rows that have one. Click to filter by project.

### Routes

- `/team/[team]/todos`

### tRPC

`todos.get`, `todos.create`, `todos.update`, `todos.check`, `todos.uncheck`, `todos.reorder`, `todos.delete`, `todos.attach`, `todos.detach`.

---

## 2. Knowledge (Obsidian-vault-backed)

Brain center. **Same directory Obsidian uses** so you can edit from either side. Markdown files on disk, indexed by mimrai, editable in both apps.

### Vault location

Default: **`/Users/john.keeney/mimrai-knowledge`** (fresh dir, per your choice). The Library-style scoped bind-mount pattern brings it into the api container as `/host/knowledge`. **You can switch this any time from `/team/local-dev/settings/knowledge`** — the model has a `knowledge_vaults` table so multiple vaults are supported.

Open Obsidian → "Open folder as vault" → point at `/Users/john.keeney/mimrai-knowledge`. Both apps see the same files.

### Model

```
knowledge_vaults
  id           text pk
  team_id      text fk teams        not null
  label        text                 not null
  root_path    text                 not null    -- in-container path
  is_default   boolean              default true
  created_at   timestamp

knowledge_notes
  id              text pk
  vault_id        text fk            not null
  relative_path   text               not null   -- "ideas/2026-05-16-mimrai.md"
  absolute_path   text               not null
  name            text               not null   -- basename without .md
  kind            enum("note","folder")
  content         text               nullable
  frontmatter     jsonb              nullable
  file_sha        text               not null
  last_seen_at    timestamp
  last_edited_at  timestamp
  created_at      timestamp
  updated_at      timestamp

  unique (vault_id, relative_path)

knowledge_links
  from_note_id    text fk
  to_note_id      text fk
  link_text       text               -- raw [[wiki link]] text
  primary key (from_note_id, to_note_id, link_text)
```

### Behavior

- **Left rail: tree** of folders + notes (folder structure mirrors disk)
- **Top: search box** (full-text on `name` + `content` via Postgres FTS)
- **Right pane: editor** — same Tiptap component as docs, with the mermaid node and the slash menu we built today
- **Wiki-style `[[Note Name]]` links** are resolved at render time — clicking jumps to that note; unresolved links rendered red. Backlinks panel under each note shows what links to it.
- **Auto-save on blur** (atomic `.tmp + rename` like the library editor)
- **Daily-log preset**: a single-click "Today" button creates `daily/YYYY-MM-DD.md` if missing and opens it. Useful for journaling / quick capture.

### Routes

- `/team/[team]/knowledge` — tree + editor (split-pane)
- `/team/[team]/knowledge/[noteId]` — single-note focus mode
- `/team/[team]/settings/knowledge` — vault sources (label, path, default-toggle, scan)

### Obsidian compat

- Standard markdown with YAML frontmatter
- `[[Note Name]]` wiki links (case-insensitive basename resolution)
- `#tag` inline tags pulled out into the tag index
- `.obsidian/` directory inside the vault is ignored by our scanner

---

## 3. Prompt Library

Same shape as Projects → Tasks, swapped for AI product → prompts.

### Model

```
prompt_products
  id           text pk
  team_id      text fk
  name         text                 not null    -- "kbuddy"
  slug         text                 not null
  description  text
  icon         text                 -- emoji or lucide id
  color        text                 -- hex
  archived     boolean              default false
  created_at   timestamp
  updated_at   timestamp

  unique (team_id, slug)

prompts
  id            text pk
  product_id    text fk prompt_products
  name          text                 not null
  slug          text                 not null
  content       text                 not null   -- the prompt itself
  notes         text                 nullable   -- markdown context/usage notes
  variables     jsonb                            -- detected {{var}} → defaults
  tags          text[] default {}
  version       integer default 1
  created_at    timestamp
  updated_at    timestamp

  unique (product_id, slug)
```

### Behavior

- **Top-level**: card grid of products. Each card shows icon, name, prompt count, last edited.
- **Click a product**: list of prompts (search + tag filter + new-prompt button)
- **Click a prompt**: full-screen editor with the prompt text in a CodeMirror panel, notes below in Tiptap. **Save** updates the row; **Save as new version** duplicates with `version + 1` so you keep history.
- **Variables** — content is scanned for `{{var}}` markers; the right rail lists each one with an editable default. Hitting **Copy filled** copies the prompt with the placeholders replaced.
- **Tags** + filter, same chip pattern.

### Routes

- `/team/[team]/prompts`
- `/team/[team]/prompts/[productSlug]`
- `/team/[team]/prompts/[productSlug]/[promptSlug]`

### Seed

One product on launch: **kbuddy** (per your answer). Empty prompt list; you add via the UI.

---

## 4. MCP server — "Claude as your personal assistant"

Standalone stdio MCP server wraps the existing tRPC procedures. Drops into `~/.claude/mcp.json`. From any Claude Code session you can then say things like:

> "add 'buy coffee filters' to my todo list, tag it home"
> "what tasks are coming up this week?"
> "find my kbuddy onboarding prompt"
> "save this conversation as a knowledge note in projects/mimrai/"

### Tools exposed

| Tool | What it does |
|---|---|
| `add_todo(content, project?, tags?)` | New todo, optionally project-scoped, optionally tagged |
| `list_todos(showChecked?, project?, tag?)` | Returns active todos |
| `check_todo(id_or_search)` | Mark done by id or content match |
| `add_task(title, project, status?, priority?, dueDate?)` | New task on a project |
| `list_tasks_due_soon(days?)` | Tasks due in the next N days |
| `list_projects` | Project list (id, name, prefix) |
| `search_knowledge(query)` | FTS over knowledge notes; returns top N with snippets |
| `read_note(path)` | Returns note content |
| `write_note(path, content, frontmatter?)` | Creates or overwrites a note |
| `list_prompts(product?)` | Prompt list, optionally per-product |
| `get_prompt(productSlug, promptSlug, vars?)` | Returns prompt content with variables filled |

### Install

`/Users/john.keeney/mimrai/mcp-server/` — a small Node project. Single-file `dist/index.js` after `bun build`. Registered in `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "mimrai": {
      "command": "node",
      "args": ["/Users/john.keeney/mimrai/mcp-server/dist/index.js"],
      "env": {
        "MIMRAI_API": "http://localhost:3003",
        "MIMRAI_TEAM_ID": "local-dev-team"
      }
    }
  }
}
```

Authentication is the existing local-dev injection (the api accepts the seed user when `MIMRAI_LOCAL_DEV=1`), so the MCP server doesn't need its own auth for local use. We add a real bearer-token path before this leaves your machine.

---

## Build order — confirmed by you

1. **Audit + fix 404s** ← running in the background now
2. **Todos** — biggest daily-driver
3. **Knowledge vault** — Obsidian compat, scoped bind-mount setup
4. **Prompt library** — `kbuddy` seeded, empty prompt list to start
5. **MCP server** — wraps all the above

Two checkpoints with screenshots before each move-on. The audit results land first; I'll fix anything broken, then start Todos.

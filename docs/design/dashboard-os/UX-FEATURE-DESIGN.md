# Dashboard OS — UX & Feature Design (the real work)

**Purpose:** Trace how the product *feels* to use, name what’s broken in the experience, design impactful feature changes and net-new capabilities, and explain **how each one works** end-to-end.  

**Not this doc:** implementation tickets, build order as the main story, or HTML-as-the-product. Visual mockups are a **capstone at the end** (§11), not the goal.

**Inputs:** Locked IA/visuals (`DASHBOARD-OS.md` §0b, `final.html`), capability audit (`FEATURE-UX-AUDIT.md`), live code behavior (Home capture, Lens, Todos promote, Health updates, Knowledge vault, etc.).

---

## 1. The job of the app (restated as experience)

You open Nexus because your life and work are **fragmented across tools**. The job is not “have a notes page and a board.” The job is:

> **Get a thought from brain → trusted system → done (or filed) with as little ceremony as possible — and find it again without archaeology.**

Every feature either **reduces ceremony**, **reduces search cost**, or **connects two places that currently force a context switch**. If it doesn’t do one of those three, it’s decoration.

---

## 2. How you actually move through a day (traced)

These are not wireframes. They are **behavioral traces** — what happens in your head and fingers today vs what should happen.

### Trace A — “I just thought of something” (ADHD capture)

**Today**
1. Thought appears.  
2. You choose: Home bar? Todos? Inbox? Chat? Notes? Phone?  
3. Home bar only makes a **Task** (with @ # ! : tokens) — wrong gravity for “call dentist” or “remember path X.”  
4. Todos can later **Promote → Task**, but it **deletes the todo first**, opens a dialog, and if you cancel the thought is gone (code comment admits this).  
5. Notes require deciding folder/category (agent taxonomy).  
6. Result: fragments in 4 apps; trust in “the system” drops.

**What “working” feels like**
1. One global capture (always the same muscle).  
2. Default = **uncommitted Capture item** (no project, no type).  
3. Later (or immediately via chips): file as Todo / Task / Note / Project update.  
4. Promote is **non-destructive until confirm**; cancel restores.  
5. You never pick a folder to *have* the thought.

**Feature:** **Universal Capture** (see §5.1) — expands Home quick-capture from “task factory” to “thought inbox with typed promote.”

---

### Trace B — “What should I do right now?”

**Today**
1. Home shows agenda (due) + up next (in progress) + projects % — three partial answers.  
2. Or open My Tasks (flat dump).  
3. Or Lens (good segments, separate app in the nav).  
4. Or Triage N/N/L (third mental model).  
5. Or Zen (fourth focus mode).  
6. Result: **meta-work choosing a list** before work.

**What “working” feels like**
1. Home **Do now** = one ranked strip (overdue → today → waiting-on-me → starter HITL → capture debt). Max 5.  
2. Click → **Focus** already on the right segment with that item selected.  
3. No second app named Lens/Triage/My Tasks in the nav — those are **views inside Focus**.  
4. Finishing an item visibly shrinks Do now (closure signal).

**Feature:** **Attention Graph** (§5.2) — single ranking function feeding Home + Focus, not four UIs.

---

### Trace C — “I’m working a project this afternoon”

**Today**
1. Open project → pick among ~9 tabs.  
2. Board for tasks, Knowledge tab for notes (soft path match), Updates for health form, Library for skills, Docs for documents…  
3. Last status lives in a form-ish update, not a living conversation.  
4. Related MCP/secrets/skills invisible unless you remember Settings.  
5. Result: project feels like a **folder of features**, not a **place**.

**What “working” feels like**
1. Project = **one hub**: pulse (last update), board slice, notebook root, resource chips.  
2. Dropping an update is as easy as a chat message (health optional, not a modal gauntlet).  
3. “Open notes” is the project notebook, not a global vault search hoping path matches.  
4. Secrets/MCPs/skills that belong here show as counts you can click.

**Feature:** **Project Place** (§5.3) — hub composition + initiative stream + notebook binding.

---

### Trace D — “I have a meeting recording / notes”

**Today**
1. Transcript lives in Otter/Zoom/files/chat paste.  
2. App can transcribe audio for chat (`transcription` API) but there’s **no meetings place**.  
3. Action items become… maybe tasks if you manually create them.  
4. Result: meetings don’t enter the system of record.

**What “working” feels like**
1. Drop transcript → land in Meetings.  
2. Default screen is **Summary + open actions**, not a wall of text.  
3. One click: actions → tasks on the right project.  
4. One click: file summary into project notebook + optional project update.  
5. Later you find it under the project, not in Downloads.

**Feature:** **Meeting Pipeline** (§5.4) — capture → understand → act → file.

---

### Trace E — “I need something from my brain / an agent needs it”

**Today**
1. Knowledge vault is powerful (FTS, wiki, editor) but organized for **agent/Zettel paths**.  
2. Library is skills/agents but labeled like a warehouse.  
3. Claude gets nexus-mcp search/read/write note — good start.  
4. No “ask my notes” in the UI; embeddings in-app are stub/OpenAI-shaped.  
5. ZenNotes is your real editor; Nexus still says Obsidian.

**What “working” feels like**
1. Notes are **your projects and areas**, not `iter12/drafts`.  
2. Deep writing can jump to ZenNotes; Nexus stays the map + links + ask.  
3. “Ask vault” answers with citations to notes.  
4. Skills are browsable squares you pin to a project so agents and you share the same shelf.

**Features:** **Human Notebook Tree** (§5.5), **Ask Vault** (§5.6), **Skills Shelf** (§5.7).

---

### Trace F — “New idea might become a real build”

**Today**
1. Blank project or external chat grill.  
2. FEAT-003 designed, not in product.  

**What “working” feels like**
1. Start from idea from Projects or Home.  
2. Workshop states live in Home (“Continue starter”).  
3. Seal → project + notebook scaffold + board — no manual re-entry.

**Feature:** **Project Starter** (FEAT-003) as a first-class loop, not a side demo (§5.8).

---

## 3. What’s missing (experience holes, not just pages)

| Hole | Why it matters | Not fixed by |
|---|---|---|
| **Single capture gravity** | ADHD needs one door | Adding more nav items |
| **Single attention surface** | Choosing lists is work | More filters on each list |
| **Non-destructive promote** | Trust; cancel-safe | “Just use tasks for everything” |
| **Project as place** | Context switching kills flow | More project tabs |
| **Meeting → system of record** | High-value time currently evaporates | Better chat transcription alone |
| **Human vault shape** | You won’t file if taxonomy is alien | Prettier knowledge list |
| **Ask / local brain** | Search ≠ understanding | FTS only |
| **Ops visibility** | MCPs/secrets fail silently | Settings CRUD |
| **Idea factory in-app** | Greenfield still external | Blank project button |
| **Bidirectional chips** | Things don’t “know” each other | Wiki links only inside notes |
| **Closure feedback** | Done must feel done | Status changes without Home impact |
| **Filter literacy everywhere** | Tasks taught a skill other surfaces ignore | One good board |

---

## 4. What’s already there but under-leveraged (impact without greenfield)

These are **high ROI** because behavior mostly exists:

| Existing | Gap in experience | Impact move |
|---|---|---|
| Home quick-capture → task | Only tasks; Home-only | Become Universal Capture entry + modes |
| Todos promote → task | Destructive + dialog | Confirm step; keep todo until task saved; add → Note |
| Lens segments | Separate nav island | **Become Focus** (same math, new shell) |
| Triage N/N/L | Third model | Layout toggle inside Focus, not a destination |
| Zen mode | Fourth focus mode | “Focus theater” mode of Focus, not peer nav |
| Health updates | Form, not stream | Sticky composer + timeline as default project social layer |
| Knowledge FTS/wiki/editor | Agent folders + Obsidian copy | Notebook tree + ZenNotes open + rename |
| Library scan + kind colors | Buried list | Skills shelf squares + pin |
| task_dependencies API | Weak frontier UX | Ready vs blocked on Focus/Board |
| task↔note / task↔skill tables | Not productized | Chips on task row + note context rail |
| nexus-mcp note/task tools | Agent-only story | Same verbs in UI (“Ask”, “File”) |
| Transcription API | Chat-adjacent | Meetings ingest step 1 |
| Home config modules | Power-user, sparse defaults | Ship opinionated Do now default |
| Task filters + saved views | Island of excellence | Extract pattern as product language |

---

## 5. Impactful features — how they work

Each feature: **problem → behavior → rules → connections → empty/error → success signal**.

### 5.1 Universal Capture

**Problem:** Capture gravity is split; Home bar over-commits to Task.

**Behavior**
- Global shortcut opens Capture drawer/page with one input.  
- Modes (segmented): **Inbox** (default) | Todo | Task | Note | Update.  
- Inbox mode stores a `capture_items` row (or inbox source=`capture`) with raw text + parse tokens (@project still soft-links).  
- Process view: each row shows promote chips.  
- Parser keeps today’s @ # ! : for Task mode; other modes ignore unknown tokens gracefully.

**Rules**
- Default mode = Inbox (lowest commitment).  
- Promote Todo→Task: create task first, **then** archive/delete todo; if dialog cancelled, todo remains.  
- Promote → Note: requires project if text has @project or prompts “which notebook?”.  
- Never auto-file into `daily/` without user intent.

**Connections:** Home bar is a thin client of the same service; Capture page is the full process UI; Focus can show “Capture debt” count.

**Success:** You stop opening Apple Notes “just for a second.”

---

### 5.2 Attention Graph (Do now + Focus)

**Problem:** Four prioritization UIs; no single “now.”

**Behavior**
- One pure function `rankAttention(user)` → ordered items with reasons (`overdue`, `due_today`, `in_progress`, `starter_hitl`, `capture_debt`, `blocked_needs_you`).  
- Home Do now = top 5 of that list with reason chips.  
- Focus = full list browsable by segment (Lens rules reused).  
- Selecting Do now item deep-links `focus?segment=today&task=DEV-69`.

**Rules**
- Max 5 on Home (overflow “+N in Focus”).  
- Starter HITL only if starter in-flight and phase is HITL.  
- Blocked tasks appear only if **you** are the unblocker (assignee or explicit).  
- Completing item removes within 300ms (optimistic) — closure.

**Connections:** Project pulse does **not** compete with Do now; pulse is social/status, Do now is personal attention.

**Success:** Opening the app answers “what now?” in under 5 seconds without nav decisions.

---

### 5.3 Project Place

**Problem:** Project is a tab farm.

**Behavior**
- **Overview** becomes the place: header (soft project icon), last update, Do-now-for-this-project (attention graph filtered), resource chips (Notes · Skills · MCPs · Secrets · Meetings), board peek.  
- **Updates**: sticky composer (“Drop an update…”) + chronological stream; health is a chip on the post, not a separate workflow. Comments optional later.  
- **Notes** tab = vault subtree `projects/{slug}/` only + “Open notebook root in ZenNotes.”  
- Board remains the execution surface; fewer chrome tabs (Docs/Knowledge merge into Notes; Library → Skills chip).

**Rules**
- Creating a project scaffolds notebook `_index.md`.  
- Starter seal uses same scaffold.  
- Resource counts are live; zero still shows chip (invite to add).

**Connections:** Pulse on Home = latest update snippet; Meetings file into this notebook; Secrets/MCP scoped badges.

**Success:** You “go to VAS” the way you go to a Linear project — one place, not a submenu puzzle.

---

### 5.4 Meeting Pipeline

**Problem:** Meeting value dies outside the system.

**Behavior**
1. **Ingest:** paste / .txt .vtt .srt / audio→existing transcription API.  
2. **Normalize:** store raw + metadata (date, source, optional project guess).  
3. **Understand:** summary + decisions + action lines (LM Studio when up; cloud fallback explicit; manual edit always).  
4. **Act:** action checklist with → Task / → Todo / bulk to project.  
5. **File:** write note under project meetings path; mark session `filed`; optional auto project update (“Meeting: …”).  

**Default UI after ingest:** Summary + Actions (transcript secondary).  
**Filters:** needs filing, open actions, project, date — because the job is clearing the pipeline, not archiving audio.

**Rules**
- Actions without project stay personal todos until assigned.  
- Re-summarize never deletes user-edited actions without confirm.  
- Filing is idempotent (update note if re-filed).

**Connections:** New tasks appear in Focus; note appears in Notes tree; Home Do now may include “File 2 meetings.”

**Success:** Week’s meetings leave **zero** open actions and **zero** unfiled sessions.

---

### 5.5 Human Notebook Tree (Notes)

**Problem:** Vault taxonomy is agent-first; editor app mismatch.

**Behavior**
- Tree roots: **Inbox · Projects · Areas · Resources · Archive** (+ optional Daily).  
- Under Projects: one folder per Nexus project (slug), with `_index`, `notes`, `decisions`, `meetings`.  
- Global Notes search still works; Ask vault optional.  
- **Open in ZenNotes** on any note/folder.  
- Context rail: backlinks, linked tasks, open Focus.

**Rules**
- Nexus owns organization + links; ZenNotes owns long writing comfort.  
- Migration script maps old `projects/nexus/…`, `ideas/`, `references/` → new tree with stubs.  
- Creating project creates folder; renaming project updates path carefully (or stable id folder + title in frontmatter — prefer **stable project id folder** long-term to survive renames).

**Recommendation:** folder key = `projects/{projectId}/` with `_meta.json` or frontmatter title = project name — human sees name, disk survives rename.

**Success:** You file under “VAS” without thinking about Zettel.

---

### 5.6 Ask Vault

**Problem:** Search finds strings; you need answers with receipts.

**Behavior**
- Embed note chunks (LM Studio embeddings endpoint when available).  
- UI: “Ask” on Notes (global or notebook-scoped).  
- Answer streams with **citation chips** (note title → open).  
- Same capability as MCP `notes_ask` for Claude.

**Rules**
- Offline LM Studio → clear disabled state + “embed pending.”  
- Never invent paths; citations must resolve.  
- Scope default = current notebook if inside project notes.

**Success:** “What did we decide about embeddings?” → answer + link to the decision note.

---

### 5.7 Skills Shelf

**Problem:** Library is a warehouse; you want a shelf.

**Behavior**
- Square tiles (icon + name + kind dot).  
- Filters: kind, source, pinned-to-project, recent.  
- Pin to project → shows on Project Place resource strip + available to starter/agents.  
- Open file / copy path / preview README.

**Rules**
- Disk remains SoT (existing scan).  
- Pin is DB relation (task_skill style already hinted in schema).

**Success:** You can point at “our wayfinder skill” on a project without digging `~/.claude`.

---

### 5.8 Project Starter (in the loop)

**Problem:** Greenfield still happens outside Nexus.

**Behavior (from FEAT-003, as experience)**
- Entry: Projects CTA + Home tile + ⌘K.  
- Phases visible on Home while in-flight.  
- Seal creates Project Place + notebook scaffold + board tasks.  
- Execute uses same Focus/Board as everything else.

**Rules:** Starter never becomes a second task system — it **emits** into the main graph.

**Success:** Idea Monday → board tasks Wednesday without retyping the plan.

---

### 5.9 Ops: MCP Catalog + Secrets

**Problem:** Infrastructure is invisible until broken.

**MCP Catalog**
- Card per server: status probe, tool count, used-by (Claude/Codex/Chat), scope.  
- Test button runs list_tools.  
- Link “env from Secrets.”

**Secrets (Infisical)**
- Browse masked secrets; copy-once; import selection.  
- Inject into MCP env or project runtime map — **never** into chat logs.  
- API-keys page becomes one source among Secrets, not the product.

**Success:** Before a session you see “zennotes MCP up · VAS secrets present.”

---

### 5.10 Connection tissue (chips & promote)

**Problem:** Entities don’t point at each other.

**Behavior**
- Every task row may show: project chip, note chip, meeting chip, blocked-by.  
- Every note context rail: tasks, project, zennotes.  
- Promote palette is shared component (Capture, Meetings actions, Note selection → task).

**Rules**
- Links are first-class IDs, not only path heuristics.  
- Soft path match remains fallback during migration.

**Success:** You stop re-searching for “the note that goes with this task.”

---

### 5.11 Filters as product language

**Problem:** Only Tasks taught you to slice; other surfaces feel dumb.

**Behavior**
- Same FilterBar grammar everywhere (§ audit §6).  
- Saved views where lists are long (Focus, Notes, Meetings).  
- URL-serializable filters so Home deep-links stay honest.

**Success:** Muscle memory transfers: search → chips → saved view.

---

## 6. Feature impact ranking (what moves the needle)

| Rank | Feature | Impact | Effort | Why this rank |
|---|---|---|---|---|
| 1 | Attention Graph + Focus merge | Extreme | M | Ends list-shopping every morning |
| 2 | Universal Capture + safe promote | Extreme | M | Ends fragment leakage |
| 3 | Project Place + initiative stream | High | M | Makes projects feel pro/Linear |
| 4 | Human Notebook Tree + ZenNotes | High | M–L | Makes Notes yours |
| 5 | Meeting Pipeline | High | L | Recovers lost meeting value |
| 6 | Connection chips (task↔note) | High | S–M | Multiplies value of 1–5 |
| 7 | Skills Shelf squares + pin | Med-High | S | Cheap clarity |
| 8 | MCP catalog | Med | S–M | Trust for agents |
| 9 | Secrets/Infisical | Med | M | Trust for ops |
| 10 | Ask Vault | Med-High | L | Magic once notes trusted |
| 11 | Project Starter | High for greenfield | L | Factory; after core graph solid |
| 12 | FilterBar everywhere | Med | S | Coherence tax |

**Note on order vs earlier “I1 nav first”:** Nav IA still ships **with** #1–2 because Attention + Capture need destinations — but the *point* is the behavior change, not the sidebar screenshot.

---

## 7. What we change in each existing feature (delta design)

| Feature today | Keep | Change | Drop/merge |
|---|---|---|---|
| Home cards | config system | Default layout = Do now + pulse + rail | Sparse 2-card emptiness as default |
| Quick capture | parser tokens | Multi-mode + inbox default | Task-only identity |
| My Tasks | TasksView | Live under Focus | Top-level nav item |
| Lens | segment math | **Is** Focus rail | Separate nav island |
| Triage | N/N/L idea | Optional Focus layout | Standalone route emphasis |
| Zen | theater mode | Mode of Focus | Peer nav |
| Todos | checklist+dnd+tags | Capture tab + safe promote + outline later | Competing with Focus for “work” |
| Inbox | mentions/pending | Capture Inbox tab | Isolated dead-end |
| Knowledge | engine | Notes product + tree + ZenNotes | Obsidian framing; agent folders as default UX |
| Documents | long-form | Narrow scope | Dual project notes |
| Library | scan | Skills shelf | “Library” name in primary nav |
| Prompts | product | Stay; link from Skills/Chat | — |
| Project tabs | board/overview/updates | Fewer tabs; Notes binds vault; resource strip | Tab sprawl |
| Health updates | model | Initiative UX | Modal-heavy create as only path |
| MCP settings | CRUD | Catalog surface | Settings-only discovery |
| API keys | app keys | Under Secrets | Pretending to be the vault |
| Chat | agents | Header/cmd access; tools to notes/tasks | Competing with Home in Focus cluster |

---

## 8. Solid “wow” additions (worth building, not gimmicks)

1. **Do now reason chips** (“overdue · 3d”) — teaches the ranking, builds trust.  
2. **Capture debt** on Home — makes unprocessed thoughts visible debt (healthy pressure).  
3. **Waiting on you** — blocked tasks where dependency is done but you haven’t moved.  
4. **Meeting zero** weekly ritual card — like inbox zero for meetings.  
5. **Notebook _index as project brief** — Starter/handoff lands here; agents read it.  
6. **Stable project-id paths** — renames don’t shatter vault.  
7. **Promote palette everywhere** — one component, many surfaces.  
8. **Citation-first Ask** — refuse answer without sources.  
9. **MCP preflight on Focus** — “zennotes down” before you start agent work.  
10. **Starter continue as first-class Home citizen** — greenfield doesn’t vanish.  
11. **Closure animation on Do now** — tiny but ADHD-relevant.  
12. **Filter “Has note” / “Has open meeting actions”** — cross-entity queries.

---

## 9. Experience principles (decision filters for later debates)

1. **One gravity well per job** — capture, attention, project, brain, ops.  
2. **Commit late** — inbox before task/note/project.  
3. **Promote is sacred** — never destructive before success.  
4. **Places beat tabs** — project/notebook/meeting are places.  
5. **Agents drink from the same graph** — no parallel shadow data.  
6. **Local when possible** — LM Studio/ZenNotes/Infisical; cloud explicit.  
7. **Closure is UI** — Home and pipelines must show zero.  
8. **Filters are literacy** — teach once, use everywhere.

---

## 10. How we’ll know the UX worked (signals, not vanity)

| Signal | Before (felt) | After (target) |
|---|---|---|
| Time to first action after open | List shopping | &lt;5s Do now click |
| Capture destinations used / week | 3–5 apps | 1 primary |
| Orphan todos deleted on failed promote | Happens | Never |
| Unfiled meetings &gt;7d | Normal | Rare |
| “Where is the note for task X?” | Search panic | Chip click |
| Project update frequency | Rare form | Steady stream |
| Agent asks you for paths/secrets | Often | Preflight green |
| Starter ideas retyped into board | Always | Never |

---

## 11. Capstone mockup (end of design thinking)

Interactive locked shell (updated skills squares):  
[`final.html`](./final.html)

Use it as a **memory palace for the features above**, not as the deliverable itself. When a feature’s behavior is agreed, the mockup should be adjusted to match the behavior — never the reverse.

Optional next visual pass (only after behavior agreement): annotate `final.html` with the Trace A–F ribbons (“you are here in Trace B”).

---

## 12. What I misunderstood earlier (correction)

| You wanted | What I over-indexed on |
|---|---|
| Trace UX, improve features, impact | Implementation phases / I1–I8 as the story |
| HTML at the **end** | HTML as the main artifact mid-stream |
| How features **work** | Where files live and EXISTS/MISSING tables alone |

The audit (`FEATURE-UX-AUDIT.md`) remains useful as **inventory**. This doc is the **experience design** layer that should drive what we build.

---

## 13. Suggested decision checkpoints (no code yet)

1. Capture default = **Inbox** (not Task)?  
2. Focus absorbs Lens + My Tasks + Triage (+ Zen as mode)?  
3. Project folder key = **project id** (stable) vs slug (readable)?  
4. Meeting summary default model = LM Studio with cloud fallback?  
5. Promote palette MVP surfaces: Capture + Meetings + Todo only?  
6. Starter after Attention+Capture+Project Place, or parallel track?

Once those are answered, implementation has a behavioral spec — not just a prettier shell.

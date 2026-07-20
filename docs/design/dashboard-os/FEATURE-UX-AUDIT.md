# Dashboard OS — Feature & UX Audit (iterative pass 1)

**Date:** 2026-07-20  
**Baseline:** Locked design `docs/design/DASHBOARD-OS.md` §0b + `final.html`  
**Method:** Multi-pass codebase inventory (nav · brain · projects · ops). Subagent fan-out attempted; Bedrock auth unavailable in this harness — audit executed by orchestrator with the same four-lane structure.  
**Minor lock add:** Skills catalog tiles = **small squares** (not full-width rows) — applied in `final.html`.

---

## 1. Executive verdict

| Area | Reality vs lock | Grade | Headline |
|---|---|---|---|
| **IA / Sidebar** | Flat multi-cluster; not Brain/Ops | D | Biggest UX tax — too many peer destinations |
| **Home** | Configurable cards exist; sparse composition | C+ | Parts exist; not “Do now + pulse + rail” |
| **Focus** | Lens EXISTS separate; My Tasks dump; Triage third model | C | Merge ready (Lens segments already coded) |
| **Capture** | Todos solid; Inbox separate; no outline | C | Promote flows missing |
| **Notes / Knowledge** | Vault + wiki + FTS strong | B- | Wrong taxonomy + Obsidian copy; no ZenNotes/Ask |
| **Skills / Library** | Scan + kind colors exist | B- | Needs human catalog IA + square tiles + pin UX |
| **Projects** | Rich tabs + board + updates CRUD | B | Updates thin vs initiative; tab overload |
| **Tasks / Board** | Filters + views + deps API | B+ | Best existing surface; density polish |
| **MCPs** | Settings CRUD only | D | No health, no catalog, buried |
| **Secrets** | API-keys only | F | No Infisical |
| **Meetings** | Transcription API only (chat voice) | F | No meetings product |
| **Starter** | Design only (FEAT-003) | F | No code path yet |
| **Cross-links** | task↔document/note tables exist partially | D+ | Not productized in UI flows |
| **Filters everywhere** | Strong on Tasks; weak elsewhere | C | Pattern exists — extend |

**Bottom line:** You are not starting from zero. **Tasks/Board + Knowledge vault engine + Library index + Lens math + Home cards + Health updates** are real. What’s broken is **information architecture, naming, composition, and the connective tissue** — exactly what the lock fixes. New build mass is Meetings, Secrets/Infisical, Ask vault, Starter, Outline, and nav merge.

---

## 2. Current route map → locked target

### 2.1 Sidebar today (actual)

| Cluster | Labels | Route |
|---|---|---|
| Focus | My Tasks | `/views/my-tasks` or `/my-tasks` |
| Focus | Lens | `/lens` |
| Focus | Chat | `/chat` |
| Projects | per-project expand | `/projects/[id]/…` |
| My work | Home | `/` |
| My work | To-do | `/todos` |
| My work | Now / Next / Later | `/triage` |
| My work | Inbox | `/inbox` |
| Knowledge | Documents | `/documents` |
| Knowledge | Library | `/library` |
| Knowledge | Knowledge | `/knowledge` |
| Knowledge | Prompts | `/prompts` |
| System | Recurring | `/recurring` |
| System | Settings | `/settings/*` |

**Also exists, not primary-nav:** `/tasks`, `/zen`, `/notifications`, `/pr-reviews`, `/overview`, project sub-routes (docs, knowledge, library, todos, updates, views, members, overview, board).

### 2.2 Mapping to locked IA

| Locked nav | Absorb | Status |
|---|---|---|
| **Home** | Home + optional inbox preview + starter card | PARTIAL — rebuild composition |
| **Focus** | Lens + My Tasks + Triage (+ optional Zen) | PARTIAL — Lens segments ready; merge UI missing |
| **Capture** | Todos + Inbox + new Outline | PARTIAL — no outline; no unified page |
| **Projects** | Projects list + Start from idea | PARTIAL — no starter CTA |
| **Favorites** | new (pin) | MISSING |
| **Notes** | Knowledge (+ project knowledge tab) | PARTIAL — rename + tree reorg + ZenNotes |
| **Skills** | Library | PARTIAL — elevate + square grid |
| **Meetings** | new (use transcription primitive) | MISSING product |
| **Prompts** | Prompts | EXISTS |
| **MCPs** | settings/mcp-servers | PARTIAL — wrong place, thin UX |
| **Secrets** | api-keys → Infisical | MISSING Infisical |
| **Settings** | rest | EXISTS |
| **Documents** | narrow long-form | EXISTS — scope shrink |
| **Chat** | header / ⌘J not peer of Home | EXISTS — demote from Focus cluster |
| **Project Starter** | FEAT-003 | MISSING impl |

---

## 3. Capability matrix (locked feature → code)

Legend: **E** = exists usable · **P** = partial · **M** = missing · **N** = not needed (by design)

### 3.1 Home (lock B)

| Capability | State | Evidence |
|---|---|---|
| Greeting + day brief | E | `home/greeting-card.tsx` |
| Quick capture bar | E | `home/quick-capture.tsx` |
| Configurable modules | E | `home-shell.tsx` + `home-config.ts` |
| Agenda / Up next / Active projects | E | respective cards |
| Stale digest / EOD recap / Activity | E | cards exist |
| **Do now** (max 5 unified queue) | M | No single ranked queue component |
| **Project pulse** (last initiative snippet) | P | Active projects show progress % not last update text |
| Right rail Ops (MCP/Infisical/LM) | M | |
| Continue starter card | M | |
| Capture mode switch (Task/Todo/Note/Update) | M | capture is task-oriented |
| Soft icon quick tiles | M | |
| Empty-state polish | P | sparse when empty |

### 3.2 Focus (lock A)

| Capability | State | Evidence |
|---|---|---|
| Today / Upcoming / Anytime / Someday / Logbook | E | `lens/personal-lens.tsx` + `LENS_SEGMENTS` |
| My Tasks filtered list | E | `my-tasks/page.tsx` → TasksView |
| Board / list / calendar | E | `tasks-view` |
| Rich filters (status, assignee, project, labels, dates) | E | `tasks-view/filters/*` |
| Saved task views | E | `task-views` router + `tasks-views-list.tsx` |
| Unified Focus route | M | three URLs today |
| Sticky filter chip row (locked chrome) | P | filters exist; not universal chip pattern |
| Has-note filter | M | |
| N/N/L as optional layout | E as separate `/triage` | should become layout not route |
| Row → open linked note | P | schema `task`–`note` link in tasks router area; UI weak |

### 3.3 Capture

| Capability | State | Evidence |
|---|---|---|
| Todos CRUD, tags, project, dnd, check | E | `todos` router + `todos-view.tsx` |
| Todo attachments (note/doc) | E | design + implementation in todos view |
| Inbox pending/mentions | E | `inbox` router + sidebar badge |
| Unified Capture page | M | |
| Promote Todo→Task | M | |
| Promote line→Note under project | M | |
| WorkFlowy outline / nest / zoom | M | |
| Global ⌘⇧N quick capture | P | quick-capture on home only |

### 3.4 Notes / Brain

| Capability | State | Evidence |
|---|---|---|
| Disk-backed vault index | E | `knowledge` router, vaults table |
| FTS search | E | `content_fts` / ts_rank |
| Wiki links + backlinks | E | `wiki-link-inline`, backlinks panel, link reindex |
| Editor + preview | E | BlockEditor in knowledge-view |
| Categories daily/projects/ideas/… | E | path matchers in knowledge-view |
| Project soft-link knowledge tab | E | `project-knowledge-view.tsx` |
| **Project notebook tree** (`projects/{slug}/…`) | P | convention only, not enforced UX |
| Rename Knowledge → Notes | M | copy still Knowledge/Obsidian |
| Open in ZenNotes | M | settings say Obsidian |
| Ask vault / local embeddings on notes | M | task embeddings stub/OpenAI; MEMORY.md has LM Studio for nexus memory not app notes |
| MCP search/read/write note | E | nexus-mcp tools |
| MCP notes_ask | M | |

### 3.5 Skills

| Capability | State | Evidence |
|---|---|---|
| Multi-source scan | E | `library` router getSources/scan/get |
| kind skill/agent/orch + colors | E | `kind-color.ts` soft bg already |
| List + preview panel | E | `library/list-view.tsx` |
| task↔skill link table | E | appears in library/tasks schema snippets |
| Human Skills home (square grid) | M | list-first |
| Pin to project UX | P | data may allow; productize |
| Top-level nav Skills | M | under Knowledge as Library |

### 3.6 Projects & updates

| Capability | State | Evidence |
|---|---|---|
| Grid/list projects | E | `projects-grid.tsx` |
| Tabs overview/board/todos/docs/knowledge/library/updates/views/members | E | `project-tabs.tsx` |
| Health updates CRUD | E | `project-health-updates` + form |
| Latest update card | E | `latest-update-card.tsx` |
| Initiative composer always-on + comments thread | P | create form exists; not Linear conversation density |
| Resource strip (notes/skills/mcps/secrets counts) | M | |
| Soft project icon tiles | P | color folder icons weak |
| Start from idea CTA | M | |
| Tab overload (9 tabs) | UX debt | collapse Docs/Knowledge→Notes, Library→Skills chip |

### 3.7 Tasks / dependencies

| Capability | State | Evidence |
|---|---|---|
| tasks_dependencies table + CRUD | E | schema + `task-dependencies` router |
| Dependency UI | P | `dependency-icon.tsx` exists; frontier UX incomplete |
| Permalink IDs, priority, labels | E | |
| Bulk ops | E | `bulk-ops-bar.tsx` |

### 3.8 Ops / AI / Meetings / Starter

| Capability | State | Evidence |
|---|---|---|
| MCP server CRUD in settings | E | `mcp-servers` router + settings page |
| MCP health probe / tools catalog UI | M | |
| API keys list/create | E | `api-keys` — app keys not Infisical |
| Infisical bridge | M | design only |
| Chat via AI gateway | E | `createAgent` + gateway models |
| LM Studio chat provider in dashboard | M | |
| App embeddings LM Studio | M | stub in local dev; OpenAI model const |
| Audio transcription endpoint | E | `transcription.ts` gpt-4o-mini-transcribe |
| Meetings module | M | |
| Project Starter runtime/UI | M | FEAT-003 design only |
| ⌘K actions | P | new task/doc/project, inbox — not note/skill/mcp/secret/meeting/starter |

---

## 4. UX friction audit (ADHD / findability)

| # | Friction | Sev | Why it hurts | Lock fix |
|---|---|---|---|---|
| 1 | 12+ peer nav items | **H** | Decision fatigue every open | Brain/Ops IA |
| 2 | Knowledge vs Documents vs Library vs Prompts | **H** | Same word “knowledge” 4 ways | Notes + Skills + narrow Docs |
| 3 | Three prioritization UIs (My Tasks, Lens, Triage) | **H** | Which is “real” work list? | Single Focus |
| 4 | Home empty/sparse feels broken | **H** | No “what do I do in 5 min” | Do now + pulse |
| 5 | Obsidian mental model while user on ZenNotes | **H** | Tool mismatch | Open ZenNotes + copy |
| 6 | Updates feel like a form not a conversation | **M** | Skipped → projects go silent | Initiative composer |
| 7 | No outline brain dump | **M** | Fragments scatter to todos/notes/chat | Capture outline |
| 8 | MCPs/secrets buried | **M** | Ops invisible until fire drill | Top-level Ops |
| 9 | Meeting actions die in chat transcripts | **M** | No filing pipeline | Meetings module |
| 10 | Soft icons missing in content; sidebar cluttered icons | **M** | Scan failure | Plain SB + soft content |
| 11 | Filters uneven (great on tasks, weak on knowledge/library) | **M** | Can’t slice brain | Filters every screen |
| 12 | Starter not in product | **H** for new work | Greenfield still external | FEAT-003 wire-in |
| 13 | Chat peers Home in Focus cluster | **L** | Competes with command center | Demote chat |
| 14 | Project 9 tabs | **M** | Hunting | Resource strip + fewer tabs |
| 15 | No Favorites | **L** | Slow return to VAS | Favorites section |

---

## 5. Relationship / flow audit (§7b loops)

| Loop | Required path | Today | Gap |
|---|---|---|---|
| **1 Morning** | Home Do now → Focus → Note/Update | Agenda/Up next partial | No Do now; weak note link |
| **2 Brain dump** | ⌘⇧N Capture → promote | Home quick-capture only | No global capture; no promote |
| **3 Project day** | Board ↔ Notes ↔ Updates ↔ Skills ↔ Secrets/MCP | Board+updates+soft knowledge | No resource strip; knowledge not notebook |
| **4 Meeting** | Import → actions → tasks → notebook | Transcription API only | Entire product |
| **5 Agent** | Skills + nexus-mcp + secrets | mcp-server tools + library | No secrets inject; no skills pin UX |
| **6 Idea→build** | Starter → board → Home continue | Design only | Implement FEAT-003 |

### Cross-link chip debt

| Entity | Needed chips | Today |
|---|---|---|
| Task | Project, Note, Meeting, Skill | Project yes; note/skill link data partial; meeting no |
| Note | Project, Tasks, ZenNotes, Ask | Path-based project soft link; tasks weak; ZenNotes/Ask no |
| Project | Board, Notes, Updates, Skills, MCPs, Secrets, Starter | Tabs yes for some; no MCP/Secrets/Starter |
| Meeting | — | n/a |
| MCP | Secrets, Projects | name/url in settings only |
| Starter | — | n/a |

---

## 6. Filter chrome standard (locked requirement)

**Adopt TasksView as the reference implementation**, then clone a thinner `FilterBar` primitive:

```
[Search] [Segment chips] [Project] [Status/Tag] [Date] [Has-link] [Saved views ▾] [List|Board|…]
```

| Screen | Search | Segments | Facets | Saved views | View switch |
|---|---|---|---|---|---|
| Focus | must | Today/… | project, priority, has-note | must (reuse task views) | list/board |
| Capture | must | Inbox/Todos/Outline | unfiled, project, today | nice | — |
| Notes | must | Projects/Areas/Inbox | project, updated, unfiled | must | tree/list |
| Skills | must | skill/agent/orch | source, pinned | nice | **square grid**/list |
| Projects | must | active/archived | status, has-starter | nice | grid/list |
| Meetings | must | needs-filing/open-actions | project, source, date | must | — |
| MCPs | must | up/down | used-by, scoped | nice | cards |
| Secrets | must | global/project | injected | nice | list |
| Board (project) | E today | — | E today | E today | E today |

---

## 7. Gap backlog (implementation-ready)

### P0 — Feel + navigation (unblocks everything)

1. **Sidebar IA rewrite** (plain icons; Brain/Ops; demote Chat)  
2. **Home B composition** (Do now, pulse from latest health update, rail, soft tiles, starter slot)  
3. **Focus merge route** (`/focus` wrapping Lens segments + TasksView)  
4. **FilterBar primitive** extracted from tasks-view  
5. **Copy pass:** Knowledge→Notes, Obsidian→ZenNotes in settings/UI  

### P1 — Brain + project connective tissue

6. Notes project-notebook tree + migrate vault folders  
7. Open in ZenNotes (`zennotes://` or `open -a`)  
8. Skills square-grid home + pin-to-project UI  
9. Project tab cleanup + resource strip  
10. Initiative updates UX (composer sticky, timeline density, optional comments)  
11. Capture page = Inbox | Todos | Outline; promote actions  
12. Task row “linked note” + has-note filter  
13. ⌘K entity expansion (notes, skills, mcps, projects, starter)  

### P2 — Ops + AI + net-new modules

14. MCPs top-level catalog + health probe + tools count  
15. Secrets + Infisical import/inject  
16. Meetings pipeline (reuse transcription; LM summary later)  
17. LM Studio provider for chat + note embeddings + Ask vault  
18. FEAT-003 Project Starter (runtime + UI) wired to Projects/Home  
19. Favorites  
20. Documents scope shrink  

### P3 — Polish

21. SoftIcon design-system component  
22. Empty states OpenShip-quality  
23. Dependency frontier on board  
24. ADHD mode (fewer home modules default)  

---

## 8. Reuse map (do not rebuild)

| Build on | For |
|---|---|
| `TasksView` + filters + task-views | Focus, project board |
| `PersonalLens` segment math | Focus left rail |
| `knowledge` router + BlockEditor + wiki | Notes engine |
| `library` router + kind colors | Skills catalog |
| `projectHealthUpdates` | Initiative timeline data |
| `todos` + dnd | Capture todos tab |
| `inbox` | Capture inbox tab |
| `mcp-servers` CRUD | MCP catalog backend |
| `api-keys` patterns | Secrets UI patterns (not storage) |
| `transcription` REST | Meetings ingest |
| `nexus-mcp` | Agent bridge; extend tools |
| `home-config` | Home module toggles under ADHD mode |
| `global-search` + actions-catalogue | ⌘K expansion |
| FEAT-003 design | Starter implementation |

---

## 9. Journey maps (target, post-lock)

### 9.1 Morning start (happy path)

```
Open app → Home
  Do now shows DEV-69 overdue
  → click → Focus/Today filtered to that task
  → complete or open linked Note
  → optional: drop Project update from pulse card
Home counts drop; dopamine loop closed
```

### 9.2 Fragment capture (ADHD)

```
Thought hits → ⌘⇧N Capture (or Home bar)
  lands Inbox unfiled
Later Process:
  “call dentist” → Todo
  “VAS harness path” → Note under projects/voice-agent-studio/
  “ship export” → Task on VAS board
Inbox → 0
```

### 9.3 Meeting → work

```
Meetings → Upload transcript
  Summary tab (local model when available)
  Actions rail → Create all tasks in VAS
  File to notebook → notes/projects/vas/meetings/…
  Optional Post update on project
Focus Today gains new tasks; Notes tree gains meeting note
```

### 9.4 Idea → shipped slices

```
Projects → Start from idea (Starter)
  Concept…Handoff…Board materialize
Home Continue starter while in-flight
On seal: Project appears + notebook scaffold + board
Execute via MCP; Focus shows ready frontier
```

---

## 10. Iteration plan (audit → build)

| Iter | Outcome | Exit criteria |
|---|---|---|
| **I0** This audit | Shared gap list | Accepted by you |
| **I1** Nav + Home + Focus shell | IA feels locked | Sidebar matches final.html; Home Do now live; /focus ships |
| **I2** FilterBar + Capture promote | Find + file thoughts | Filters on Notes/Skills/Projects; promote Todo↔Task |
| **I3** Notes notebooks + ZenNotes | Brain usable | Tree by project; Open ZenNotes works |
| **I4** Project hub + initiatives | Projects feel Linear-pro | Resource strip; composer timeline |
| **I5** Skills squares + MCP catalog | Ops visible | Square skills; MCP health |
| **I6** Secrets + Meetings MVP | New modules | Infisical list; meeting→tasks |
| **I7** Ask vault + LM Studio | Local AI | Embed notes; ask citations |
| **I8** Project Starter | Factory | FEAT-003 end-to-end |

Each iter ends with a **mini UX audit** (same matrix columns) — only statuses should move E/P/M.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Big-bang nav break | Feature-flag IA; redirect old routes 30d |
| Vault move breaks wiki links | Redirect stubs + link rewrite script |
| Infisical auth variety | Start read-only list + copy; inject later |
| LM Studio flaky | Graceful offline; cloud fallback explicit |
| Scope creep vs FEAT-003 | Starter is I8; don’t block I1–I4 |
| Subagent infra down | This audit done inline; restore Bedrock/OAuth for next iters |

---

## 12. Acceptance for “audit complete” (this pass)

- [x] Four-lane inventory (nav, brain, projects, ops)  
- [x] EXISTS/PARTIAL/MISSING vs every major lock  
- [x] Flow audit for six loops  
- [x] P0–P3 backlog  
- [x] Reuse map  
- [x] Skills square-tile lock noted + mockup fixed  
- [x] Journey maps  
- [x] Iteration plan for continuous UX audits  

---

## 13. Next action (recommend)

**Start I1 immediately:** Sidebar IA + Home B + Focus merge — maximum user-visible win, pure composition over existing data, lowest backend risk.

Confirm I1 scope or adjust priority (e.g. pull Notes/ZenNotes earlier if brain pain > nav pain).

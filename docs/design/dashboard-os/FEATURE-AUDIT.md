# Nexus Dashboard — Feature Audit & Roadmap (Round 2)

Source: 4 parallel read-only `scout` audits (Groups 1–4), consolidated.
Citations are `file:line`. Status: **DONE** / **PARTIAL** / **STUB** / **MISSING** / **DEAD CODE**.

---

## Cross-cutting themes (highest signal)

1. **Several features are fully built but unreachable.** Chat (no sidebar entry), Library multi-root settings (zero inbound links), Zen settings (orphaned). Surfacing them is near-free ROI.
2. **There are two libraries for the same pattern.** Library already has a working multi-root "sources picker" (`addSource`/`removeSource`/`scan` + `sources-settings.tsx`). Notes needs the **exact same thing** and its DB schema already supports multi-root — only the API + UI are single-root.
3. **Several mutations exist but have zero UI callers** — `agentConfig.updateRoot`, `agentConfig.ensureDefaults`, `prompts.deleteProduct`, `projects.addProjectMember`. Wiring them is cheap.
4. **State leakage to localStorage** in security/data-loss-sensitive spots: **Vault secrets are plaintext in localStorage** (lost on cache clear, never synced, never encrypted); Focus sessions, Rituals progress, Capture outline, Home layout, project pin-order are all localStorage-only.
5. **Per-section settings coverage is thin** — the user's explicit ask. Most surfaces have no gear/config. See §Settings coverage gaps.
6. **Keyboard collisions & dead code**: ⌘J is bound twice (ProjectSwitcher + Dump); 4 Home cards + `quick-capture.tsx` UI are orphaned.

---

## GROUP 1 — Daily Driver (Home · Capture · Dump · Todos · Focus · Inbox)

| Feature | Route/Component | Status | What's incomplete | Settings gap | Convenience opportunity |
|---|---|---|---|---|---|
| Home shell | `page.tsx` → `home/home-shell.tsx` | DONE | Layout localStorage-only (`home-config.ts:121`) | One gear → modal; no per-card settings | **No live card drag** — reorder only inside `dashboard-config-modal.tsx:66-95`; cards render with zero drop zones (`home-shell.tsx:171-198`) |
| Greeting / Do-now / Todos / Agenda / Up-next / Active-projects / Health-strip / Starter-continue / OS-tiles | `home/*` | DONE | Do-now read-only (no inline mark-done/defer); Health-strip counts stubbed | — | Give Do-now the same RowActionStrip as Agenda |
| Quick capture (Home) | `home/quick-capture.tsx` | **DEAD CODE** | 347-line component never imported (only `parseQuickCapture` reused by `capture-bar.tsx:11`) | n/a | Revive on Home or delete |
| Legacy Home cards | `recent-documents-card`, `active-projects-card`, `my-issues-card`, `inbox-preview-card` | **DEAD CODE** | Exported, unreferenced | n/a | Delete |
| Stale digest | `home/stale-commitment-digest.tsx` | PARTIAL | "Archive" reuses `done` status | — | Real cancelled/archived status |
| EOD recap | `home/end-of-day-recap.tsx` | PARTIAL | Counts only; narrative deferred | — | Wire summarizer |
| Capture (header) | `components/capture-bar.tsx` (in `header.tsx:7`) | DONE | ⌘N bound via raw `useHotkeys` (`:96`), bypasses registry | — | Route ⌘N through `useShortcut("capture.focus")` |
| Capture (route) | `capture/page.tsx` → `capture/capture-shell.tsx` | PARTIAL | Outline only, localStorage, uses `window.prompt` for add-child, no promote-to-task | — | Promote outline nodes to Todo/Task |
| Dump modal | `dump/dump-modal.tsx` + `dump-store.ts` | PARTIAL | ⚠️ **⌘J collision** with ProjectSwitcher (`global-shortcuts.tsx:60-67` vs `dump-modal.tsx:72`); `DumpTrigger` (`:329`) never rendered; promote-to-Task hardcodes `list[0]` (`:96-99`) | — | Fix chord; per-item `@project`; surface DumpTrigger in header |
| Todos | `todos/todos-view.tsx` | DONE | group-by selector updates state but rows don't re-bucket (`:1145,1244`) | No per-section gear | Make group-by actually bucket |
| Focus shell + Personal lens | `focus/focus-shell.tsx`, `lens/personal-lens.tsx` | DONE | — | — | — |
| Focus session | `focus/focus-session.tsx` | PARTIAL | Built; logs to `localStorage["nexus.focus.log"]` only (`:48-52`) | — | Persist via tRPC; surface in Logbook |
| Inbox (route) | `inbox/page.tsx` | DONE | Redirects to `/focus?tab=needs-you` | — | — |
| Inbox detail | `inbox/[id]/page.tsx` | PARTIAL | **Discards path `id`** (`void id`) — deep-linking broken | — | Map id → `selectedInboxId` |
| Inbox list/row | `inbox/{view,list,row}.tsx`, `use-inbox.tsx` | DONE | group-by non-functional (`list.tsx:60-65`) | — | Snooze + functional group-by |
| Inbox inline actions | `inbox-row.tsx` | PARTIAL | **Snooze is fake** (`:127-135`); convert-to-task doesn't link source | — | Real snooze date; link converted task |
| Inbox counts | `inbox/use-inbox-counts.ts` | PARTIAL | `mentions`/`subscribed` hardcoded 0 (`:31`) — under-counts Home/Focus/Rituals badges | — | Lift heuristics from `use-inbox.tsx:21-49` |

### Group 1 top opportunities
1. **Live in-place Home card drag** (infra exists — wrap grid in `DndContext`, persist on drop).
2. **Resolve ⌘J collision** + surface `DumpTrigger` in header.
3. **Per-dump-item `@project` routing** (reuse `parseQuickCapture`).
4. **Real Inbox counts everywhere** (one shared hook fixes Home/Focus/Rituals).
5. **Fix Inbox deep-linking + fake Snooze.**

---

## GROUP 2 — Knowledge & Content (Notes · Site Docs · Skills · Prompts · Meetings)

| Feature | Route/Component | Status | What's incomplete | Settings gap | Convenience opportunity |
|---|---|---|---|---|---|
| **Notes** | `notes/page.tsx` → `knowledge/knowledge-view.tsx` + `editor/` | DONE (real tRPC) | `NoteCard` dead code; editor "preview" is plain `<pre>` not markdown (`:716-720`); ZenNotes deep-link fire-and-forget | **BLOCKER** — single-root. `knowledge.get` server-picks `LIMIT 1` vault (`knowledge.ts:209-214`); view never calls `getVaults`, no selector; `vault-settings-form.tsx:14` hardcoded "One vault per team". **DB `knowledgeVaults` already supports multi-root** (no unique on teamId, has `isDefault`) | Vault switcher + "New note in…" picker; render markdown preview |
| **Site Docs** | `documents/page.tsx` → `site-docs/site-docs-view.tsx` + `api/.../site-docs.ts` | PARTIAL | No `deleteMap`; maps auto-seeded, can't reset/regenerate; no file create/rename/delete; preview = edit-bound BlockEditor | Sites derived from projects w/ `docsPath`; management only at `create-project/existing`; inspector shows docsPath read-only | Inline "Adjust docs path…"; "Regenerate maps"; delete-map; "Open on disk" |
| **Skills/Library** | `skills/page.tsx` + `library/*` → `library/list-view.tsx` | DONE (gold standard) | Detail write-back 400s if frontmatter incomplete | **Multi-root picker fully built but ORPHANED** at `settings/library` → `library/sources-settings.tsx` (add/remove/scan); **no link in `nav-list.tsx`** | 1-line add to nav-list surfaces it. **Template to clone for Notes.** |
| **Prompts** | `prompts/*` → `prompts/products-view.tsx` + `edit-view.tsx` + `api/.../prompts.ts` | DONE (versioned) | `deleteProduct` mutation exists (`prompts.ts:179`) but **no UI**; "send to chat" missing; run telemetry stubbed | — | Trash on product card → `deleteProduct`; "Copy → Chat"; edit product meta |
| **Meetings** | `meetings/page.tsx` → `meetings/meetings-shell.tsx` | **STUB** | Client-only regex extraction (`:21-46`); no audio/transcript/AI/persistence; only side-effect = bulk `todos.create` | N/A (no data layer) | Persist transcript + actions; agent extraction; link → project |

### Notes multi-root — exact touch-points
1. **API** `trpc/routers/knowledge.ts`: add `createVault` (mirror `library.addSource`; validate via existing `safeResolve()` `:18`) + `removeVault` (confirm FK cascade on `knowledge_notes`/`knowledge_links`). `getVaults` (`:186-202`) + `updateVault` (`:282`) already exist.
2. **View** `knowledge-view.tsx`: add `getVaults` query, a vault `<Select>` in the header (`:616-654`), pass `vaultId` into `knowledge.get` (input already accepts it, `:200-208`) and into `createNote` (`:690-707`, input accepts `vaultId` `:262`).
3. **Settings form** `vault-settings-form.tsx`: rewrite single-row → list+add+remove using `library/sources-settings.tsx` (88 lines) as the literal template. Settings page `settings/knowledge/page.tsx:11` description → plural.
4. **Schema** `schema.ts:2486`: no structural change (already multi-root capable).
5. **State**: local `vaultId` + localStorage key mirroring `LIST_WIDTH_KEY` (`:60-62`); optional `?vault=` share param.

### Group 2 top opportunities
1. **Add `Library` to settings nav-list** (1 line) — surfaces already-built multi-root picker.
2. **Notes multi-root picker** (port Library pattern; no migration risk).
3. **Site Docs inline site/path management + delete maps.**
4. **Prompts: wire `deleteProduct` + "send to chat".**
5. **Meetings: persist + AI extract.**

---

## GROUP 3 — Projects & Config (Projects hub · Project tabs · Agent Config · Vault)

| Feature | Route/Component | Status | What's incomplete | Settings gap | Convenience opportunity |
|---|---|---|---|---|---|
| Projects hub grid | `projects-grid.tsx` | DONE | Stale comment (`:11-21`); drag-reorder localStorage-only (no `projects.reorder`) | — | `projects.reorder` route; fix comment |
| Hub filters/group-by | `projects-grid.tsx:325-362` | DONE | — | — | — |
| Starter templates | `projects-grid.tsx:117-205` | DONE | Real `projects.create` | — | — |
| Create — Existing on disk | `create-project/existing/page.tsx` | DONE | Probe auto-fills; redirects to `/documents` not project | No folder-browser (paste raw path) | Tree path picker; redirect to project board |
| Create — Starter workshop | `create-project/starter/page.tsx` | **STUB** | Only "Seed" phase interactive; seed written to localStorage only — **not** sent to `projects.create`; phases 2–6 are shells | Needs host OAuth agent runtime | After seed, call `projects.create({name,description})`; mark phases "coming soon" |
| Project tabs / layout | `project-tabs.tsx`, `[projectId]/layout.tsx` | DONE | — | — | — |
| Project Overview | `overview/overview.tsx` | DONE | `ProgressCard`/`LatestUpdateCard` exist but **not rendered** (dead code) | — | Wire them in or delete |
| Project Docs | `project-docs-view.tsx` | PARTIAL | Read-only list; no "new doc in project" | — | Add create affordance |
| Project Knowledge | `project-knowledge-view.tsx` | PARTIAL | Client-side filtering of ALL notes; no way to tag a note's project frontmatter | — | Inline "tag with {project}" |
| Project Library | `project-library-view.tsx` | DONE | No unpin action (`library.unlinkProject`) | — | Unpin button |
| Project Members | `project-members-view.tsx` | PARTIAL | "Add member" invites to **workspace**, not project (`:64-83`) | No project-scoped add/remove | Wire `addProjectMember`/`removeProjectMember` |
| Project Updates / Views | `updates/`, `views/` | DONE | — | — | — |
| Agent Config — roots/tree/chips | `agent-config-view.tsx` + `api/.../agent-config.ts` | DONE | "Oh" has no default; "custom" agent has no chip | — | "Add Oh root" one-click; "Custom" chip |
| Agent Config — add/remove root | `:519-562`, `:874-884` | DONE | — | — | — |
| Agent Config — **edit/toggle/reorder root** | `updateRoot` (`:464-486`) — **no UI callers** | **MISSING** | `updateRoot` mutation exists, never called; can't disable/rename/reassign/reorder a root | Cannot tune active roots per agent | Wire enable/disable + inline edit + drag-reorder in inspector |
| Agent Config — read/secret-mask/write/create file | `:632-685`, `agent-config.ts:139-424` | DONE | Extension allow-list enforced | **Masking rules hardcoded** (`SECRET_RE`/`redactJson`) | Settings panel for masking rules + exemptions; show allowed-ext hint |
| Agent Config — **delete/rename/move file** | none | **MISSING** | No mutations, no context menu | — | `deleteFile` + `renameFile` |
| Agent Config — **SKIP_DIRS / host home** | `SKIP_DIRS` (`:60-116`), env (`:23-25`) | **MISSING (hardcoded/env)** | `ensureDefaults` exists, uncalled; can't tune skipped dirs / host home / allowed root | — | "Agent Config → Settings" tab; "Re-seed defaults" |
| **Vault — Secrets/MCPs** | `vault/vault-shell.tsx` | **PARTIAL (mocked)** | ⚠️ **100% client-side plaintext localStorage** (`:11-13,39-44`); no tRPC/DB/encryption; lost on cache clear | Not server-backed/synced/encrypted | Move to server-backed encrypted store; localStorage as read-cache only |
| Vault — edit secret | none | MISSING | Edit = delete + re-add | — | In-place edit |
| Vault — MCP validation | `vault-shell.tsx` | PARTIAL | Free-text, no JSON validation, no "write to agent config" | — | Validate; "Write to {agent} settings.json" |

### Group 3 top opportunities
1. **Promote Vault to server-backed encrypted store** (security + data-loss fix).
2. **Wire `agentConfig.updateRoot`** (enable/disable + rename + reorder).
3. **"Oh" root + Agent Config Settings tab** (SKIP_DIRS, masking rules, re-seed defaults).
4. **Starter: carry seed forward or mark stub honestly.**
5. **Agent Config file delete/rename + project-scoped Members.**

---

## GROUP 4 — Insight · Ops · Chat · Settings

| Feature | Route/Component | Status | What's incomplete | Settings gap | Convenience opportunity |
|---|---|---|---|---|---|
| Health | `health/workspace-health-shell.tsx` | PARTIAL | 4 hard-coded cards; MCP card `count:0` literal (`:65`) | No thresholds config | Wire MCP card to `trpc.mcp.*`; add snooze |
| Activity | `activity/page.tsx` → `home/activity-feed` | DONE | No filters/pagination | No filter persistence | Date-range + entity chips; "since last visit" |
| Rituals | `rituals/rituals-shell.tsx` | DONE (session-only) | `doneSteps` in-memory only (`:21`) | No cadence/custom-steps config | Persist to DB; "next ritual" nudge |
| **Chat** | `chat/[chatId]/page.tsx` + `chat-provider.tsx` | **DONE-but-hidden** | Full `useChat` streaming (`chat-provider.tsx:76-110`); **not in sidebar**; `<ChatTitle/>` commented out (`chat-interface.tsx:13`) | **No AI-provider settings panel** | Add to sidebar cluster; key-status banner |
| Chat API | `api/rest/routers/chat.ts` | DONE-but-unconfigured | Real resumable streaming; needs `AI_GATEWAY_API_KEY` (`.env.example:7` empty); default `anthropic/claude-haiku-4.5` | Key env-only | Empty-state CTA → settings |
| Settings — General/Profile/Agents/API Keys/Autopilot/Import/Integrations hub/GitHub/Mattermost/Labels/MCP Servers/Members/Notifications/Statuses | `settings/(navigation)/…` | DONE | Autopilot has stray `… copy.tsx` file; Profile email disabled | — | Remove ` copy` file; rename import |
| Settings — SMTP | `integrations/smtp/page.tsx` | PARTIAL | No logs/test-send (vs GitHub/Mattermost) | — | Test-send + logs for parity |
| Settings — WhatsApp | `integrations/whatsapp/page.tsx` | **STUB** | Single static card | — | Real config or remove from nav |
| Settings — Knowledge | `vault-settings-form.tsx` | DONE | One-vault only | — | Multi-vault (see Group 2) |
| Settings — Tags | `tags/page.tsx` | PARTIAL (read-only) | No merge/rename/delete | — | Merge + bulk-delete |
| **Settings — Zen** | `settings/zen` + `focus-guard-form.tsx` | **DONE-but-ORPHANED** | Not in `nav-list.tsx`; only link from `zen-mode/break.tsx:190` | — | Add to Account group |
| **Settings — Library (sources)** | `settings/library` → `sources-settings.tsx` | **DONE-but-ORPHANED** | Outside `(navigation)`; **zero inbound links** | — | Add to Data group (or delete route) |
| Settings nav/sidebar | `nav-list.tsx:44-167` | MOSTLY DONE | Missing `zen` + `library` entries | — | Add both |

### Settings coverage gaps (sections with NO per-section settings panel)
- **Home layout** — hard-coded overview grid; only the modal-toggled card set is configurable.
- **Chat** — no AI-provider/model/default-agent panel; key is env-only.
- **Health** — no thresholds/which-signals.
- **Rituals** — no cadence/time-window/custom-steps.
- **Activity** — no filter/date-range config.
- **Notes/Knowledge folders** — only single-vault root; multi-root missing (see Group 2).
- **Agent Config defaults** — live config view, no "defaults/templates" panel.
- **Prompts / Skills / Meetings / Documents / Projects / Vault sources** — no per-section settings in the hub.

### Group 4 top opportunities
1. **Surface Chat** (add to sidebar; biggest reachability win).
2. **Recover orphaned Zen + Library settings pages** (add to `nav-list.tsx`).
3. **Add Chat/AI-provider settings panel** (model picker + key-status + empty-state CTA).
4. **Wire Health MCP card to real data.**
5. **Promote thin integrations to parity** (WhatsApp real-or-remove; SMTP logs/test-send; remove ` copy` file; un-comment `<ChatTitle/>`).

---

## Prioritized roadmap

### P0 — Quick wins (≤1 line to ~1 hour, high ROI, low risk)
- [ ] Add `Library` to settings `nav-list.tsx` → surfaces already-built multi-root picker.
- [ ] Add `Zen` to settings `nav-list.tsx` → de-orphan.
- [ ] Add **Chat** to a sidebar cluster → de-hide.
- [ ] Fix **⌘J collision** (Dump vs ProjectSwitcher); surface `DumpTrigger` in header.
- [ ] Route ⌘N through shortcut registry (not raw `useHotkeys`).
- [ ] Un-comment `<ChatTitle/>` (`chat-interface.tsx:13`); remove autopilot `… copy.tsx`.
- [ ] Delete dead Home cards + orphaned `quick-capture.tsx` UI.

### P1 — User-requested features
- [ ] **Home in-place drag-and-drop** (wrap card grid in `DndContext`, persist order — infra exists).
- [ ] **Notes multi-root folder picker** (port Library `addSource`/`removeSource`/`scan` → `createVault`/`removeVault`; rewrite `vault-settings-form.tsx` from `sources-settings.tsx` template; add vault `<Select>` in view header). DB already supports it.
- [ ] **Floating quick-capture dock** (Todo + Notes quick input, accessible app-wide — like the discussed floating dock).
- [ ] **Electron: dynamic shell** (responsive min sizes, drag-to-resize, remember window state).
- [ ] **Per-section settings affordances** — gear icon on Notes/Site Docs/Agent Config/Home leading to real config panels.

### P2 — Deeper builds
- [ ] **Vault → server-backed encrypted store** (replace plaintext localStorage; envelope encryption).
- [ ] **Wire `agentConfig.updateRoot`** (enable/disable/rename/reorder) + Agent Config Settings tab (SKIP_DIRS, masking rules, re-seed defaults).
- [ ] **Agent Config file delete/rename/move** + Oh default root + Custom chip.
- [ ] **Site Docs** inline site/path management, delete/reset maps, file create/rename/delete.
- [ ] **Prompts** delete product + "send to chat".
- [ ] **Inbox** real snooze + deep-link fix + real counts (shared hook) + functional group-by.
- [ ] **Meetings** persist transcript + agent extraction (currently a regex stub).
- [ ] **Project Starter** carry seed forward (or mark stub).
- [ ] **Project-scoped Members** (wire `addProjectMember`/`removeProjectMember`).
- [ ] Server-persist Focus sessions, Rituals progress; Activity filters; Health real data; integrations parity.

---

## Immediate fixes applied this round
- Electron/Chromium scrollbar-hide on all menus/nav/sidebar + app-wide under `html.nexus-desktop` (`packages/ui/src/index.css`).
- Electron preload tags `<html class="nexus-desktop">` and exposes `window.nexusDesktop` (`apps/desktop/src/preload.js`).

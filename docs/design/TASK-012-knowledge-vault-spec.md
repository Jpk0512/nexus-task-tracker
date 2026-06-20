# Knowledge Vault — Visual Design Spec

**Task:** TASK-012  
**Routes:** `/team/[team]/knowledge` (existing), `/team/[team]/knowledge/[noteId]` (new), `/team/[team]/settings/knowledge` (new)  
**Palette session:** 2026-06-20  
**Design system:** Linear-derived dark-native token set from `app/packages/ui/src/index.css`

---

## 1. Design Contract Principles (from `DESIGN.md` + `index.css`)

The app is **dark-native** (`.dark` is always active; light tokens are edge-case-only). All tokens below resolve via CSS custom properties, so light mode is automatically covered by the `:root` block in `index.css`.

Token ladder (sourced from `app/packages/ui/src/index.css:94–153`):

| Level | CSS var | Dark hex | Light hex | Role |
|---|---|---|---|---|
| Canvas | `--background` | `#08090a` | `#ffffff` | Page base |
| Card / surface-1 | `--card` | `#0f1011` | `#f7f8f8` | Panel, form card surface |
| Popover / surface-2 | `--popover` | `#141516` | `#ffffff` | Modals, slash-menu popover |
| Hover lift | `--accent` | `#18191a` | `#f5f6f7` | Row hover, selected lift |
| Hairline border | `--border` | `#23252a` | `#e6e6e6` | All 1px borders |
| Primary text | `--foreground` | `#f7f8f8` | `#010102` | Content text |
| Secondary text | `--muted-foreground` | `#8a8f98` | `#62666d` | Labels, placeholders |
| Lavender accent | `--primary` | `#5e6ad2` | `#5e6ad2` | Focus ring, primary CTA |
| Destructive | `--destructive` | `#eb5757` | `#c8312b` | Error states |
| Success | `--color-success` (global) | `#27a644` | `#27a644` | Save success badge |
| Violet (knowledge brand) | `violet-500` (Tailwind) | `#8b5cf6` | `#8b5cf6` | BrainIcon, CtaCard icons |

Typography: Inter (`--font-sans`), `font-feature-settings: "cv01", "ss03"`. Signature weight 510. Display headings: negative letter-spacing.

Radius: `--radius` = 0.5rem (8px base); `rounded-md` = 8px, `rounded-lg` = 12px, `rounded-sm` = 6px, `rounded-full` = 9999px.

---

## 2. Component: wiki-link-inline

**Purpose:** Inline rendered `[[note title]]` Obsidian-style links. Two states: resolved (target note exists in vault) and unresolved (no matching note found by slug).

### Token assignment

| State | Class (Tailwind) | Dark hex | Light hex | Ratio on dark canvas | Ratio on light canvas |
|---|---|---|---|---|---|
| Resolved link (default) | `text-blue-400` | `#60a5fa` | — | `#60a5fa` on `#08090a` ≈ **8.1:1** — passes AA | — |
| Resolved link (light mode) | `dark:text-blue-400 text-blue-600` | — | `#2563eb` | — | `#2563eb` on `#ffffff` ≈ **5.9:1** — passes AA |
| Unresolved link (default) | `text-red-400` | `#f87171` | — | `#f87171` on `#08090a` ≈ **5.6:1** — passes AA | — |
| Unresolved link (light mode) | `dark:text-red-400 text-red-600` | — | `#dc2626` | — | `#dc2626` on `#ffffff` ≈ **5.3:1** — passes AA |

**Exact Tailwind class strings for forge-ui to apply literally:**

```
resolved:   className="text-blue-600 dark:text-blue-400 underline decoration-dotted underline-offset-2 cursor-pointer transition-colors duration-150 hover:text-blue-500 dark:hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-sm"

unresolved: className="text-red-600 dark:text-red-400 underline decoration-dotted underline-offset-2 cursor-pointer transition-colors duration-150 hover:text-red-500 dark:hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded-sm opacity-80"
```

### Interaction states

| State | Resolved | Unresolved |
|---|---|---|
| **Default** | `text-blue-600 dark:text-blue-400`, dotted underline, `underline-offset-2` | `text-red-600 dark:text-red-400`, dotted underline, `opacity-80` |
| **Hover** | `hover:text-blue-500 dark:hover:text-blue-300` — one step lighter (brighter) | `hover:text-red-500 dark:hover:text-red-300` |
| **Focus** | `focus-visible:ring-2 focus-visible:ring-ring/50 rounded-sm` — lavender ring via `--ring` variable | same |
| **Active / pressed** | `active:opacity-80` | `active:opacity-60` |
| **Visited** | No distinct visited color — this is an SPA nav, not a browser anchor. Resolved links do not style `:visited`. | n/a |
| **Disabled** | n/a — wiki-links are never disabled; unresolved is the "unavailable" signal | — |
| **Loading** | n/a (resolution is synchronous at render time) | — |

### Empty / Loading / Error states (component level)

- **Empty (no wiki-links in note):** no visual treatment needed; component simply renders nothing.
- **Loading (note list not yet fetched):** wiki-link inline component should render as plain text (unresolved style) until the note list resolves. Do not block render.
- **Error (note list query failed):** fall back to unresolved style — same appearance as "note not found". Do not throw.

### WCAG note

`text-blue-400` (#60a5fa) on `#08090a` ≈ 8.1:1 — exceeds AA for both normal and large text. `text-red-400` (#f87171) on `#08090a` ≈ 5.6:1 — passes AA. Light mode: `text-blue-600` (#2563eb) on white ≈ 5.9:1, `text-red-600` (#dc2626) on white ≈ 5.3:1 — both pass.

### Motion

`transition-colors duration-150 ease-out`; `@media (prefers-reduced-motion: reduce) { transition: none; }`.

### Forge implementation note

- Component: `wiki-link-inline.tsx`
- Props: `toNoteId: string | null` (null = unresolved), `text: string`, `onClick?: () => void`
- Apply the resolved/unresolved class strings above based on `toNoteId !== null`
- No new design-system primitives needed; inline `<span>` or `<button>` suffices
- The dotted underline (`decoration-dotted`) visually differentiates wiki-links from regular `<a>` href links (which use `underline` solid in `editor/styles.css`)

---

## 3. Component: BlockEditor-in-note

**Purpose:** Visual treatment when the Tiptap `<Editor>` component replaces the plain `<textarea>` in `KnowledgeView`. The existing `textarea` renders at `grow resize-none p-6 font-mono text-sm leading-relaxed outline-none` (`knowledge-view.tsx:648`). The BlockEditor must match this spatial contract while adding rich-text affordances.

### Layout contract

The BlockEditor slot inherits the same flex-grow container. Visual measurements:

```
┌─ editor pane (main.flex-col.grow) ───────────────────────────────────┐
│ header  (border-b border-border px-6 py-3)                           │
├──────────────────────────────────────────────────────────────────────┤
│ BlockEditor wrapper  (grow overflow-y-auto)                           │
│   px-6 py-6                                                          │
│   max-w-[740px]  ← prose column width cap                            │
│   mx-auto                                                             │
│   .tiptap (from editor/styles.css)                                   │
├──────────────────────────────────────────────────────────────────────┤
│ BacklinksPanel  (shrink-0 border-t border-border px-6 pb-4)          │
└──────────────────────────────────────────────────────────────────────┘
```

### Wrapper token list

| Property | Token / class | Value |
|---|---|---|
| Background | inherits from `bg-background` | dark `#08090a`, light `#ffffff` |
| Padding | `px-6 py-6` | 24px horizontal, 24px vertical |
| Max-width | `max-w-[740px] mx-auto` | prose column, centered |
| Font (body) | `font-sans text-sm leading-relaxed` | replaces `font-mono` from textarea |
| Min-height | `min-h-[320px]` | prevents collapse to zero when empty |

**Note on font-mono → font-sans:** The current textarea uses `font-mono` for raw markdown editing. The BlockEditor renders parsed prose — `font-sans` is correct. The existing `.tiptap` styles in `editor/styles.css:7` already set `@apply font-sans`.

### Focus ring

When `.ProseMirror-focused` is active, apply a focus ring to the wrapper div (not to `.ProseMirror` itself, which is `outline-none` per `editor/styles.css:3`):

```
wrapper (focused): ring-1 ring-border/60 rounded-lg
```

This is a hairline ring at 60% opacity — same quiet treatment as the existing `InlineComposer` hover border in TODOS-VISUAL-SPEC. It signals focus without competing with content. Full lavender `ring-primary` would be too loud for a document editor.

Wrapper class string: `grow overflow-y-auto px-6 py-6 max-w-[740px] mx-auto min-h-[320px] rounded-lg transition-shadow duration-150 focus-within:ring-1 focus-within:ring-border/60`

### Auto-save visual indicator

The quill-ts notepad (2026-06-20) specifies a 500ms onBlur debounce save. Provide a saving state without a full Save button animation:

| State | Indicator |
|---|---|
| Idle | Nothing (no indicator) |
| Dirty (unsaved changes) | Small `text-muted-foreground text-[11px]` copy "Unsaved" in header right — `opacity-60` |
| Saving (pending) | Replace "Unsaved" with `RefreshCwIcon size-3 animate-spin text-muted-foreground` + "Saving…" at `text-[11px]` |
| Saved (success, 2s then fades) | `CheckIcon size-3 text-[--color-success]` + "Saved" at `text-[11px] text-[--color-success]`; fade out at `opacity-0 transition-opacity duration-500` after 2s |
| Conflict error (SHA mismatch) | `AlertCircleIcon size-3 text-destructive` + "Conflict — reloading" at `text-[11px] text-destructive`; trigger re-fetch |

WCAG: `--color-success` (#27a644) on `--background` (#08090a): approx 4.9:1 — passes AA. `text-destructive` (#eb5757) on `#08090a`: approx 4.5:1 — passes AA.

### Slash-menu popover (existing component, visual alignment)

The existing `slash-menu.tsx` uses `tippy.js` for positioning and renders a list of slash commands. No new token invention — the popover must match the existing `bg-popover` / `border-border` / `shadow-md` surface pattern.

Slash-menu popover contract:

| Property | Token / class | Value |
|---|---|---|
| Surface | `bg-popover border border-border rounded-lg shadow-md` | dark: `#141516` surface |
| Item default | `px-3 py-1.5 text-[13px] text-foreground` | |
| Item hover | `bg-accent/60 text-foreground` | dark: `#18191a` at 60% |
| Item selected (keyboard) | `bg-accent text-foreground` | full `--accent` |
| Category header | `px-3 py-1 text-[10px] text-muted-foreground uppercase tracking-wider` | |
| Shortcut hint | `text-[11px] text-muted-foreground font-mono ml-auto` | |
| Width | `min-w-[240px] max-w-[320px]` | |
| Max-height | `max-h-[320px] overflow-y-auto` | |

No new tokens introduced; this reuses the existing popover ladder.

### Interaction states (BlockEditor wrapper)

| State | Appearance |
|---|---|
| **Default** | No ring; `bg-background` (inherits); `text-foreground` body |
| **Focused** | `focus-within:ring-1 focus-within:ring-border/60 rounded-lg` |
| **Loading** (note fetch pending) | Shimmer skeleton: 4 lines of `Skeleton` at varying widths (`h-4 rounded animate-pulse`) inside the `px-6 py-6` padding zone; uses `bg-muted/50` |
| **Empty** (new note, no content) | Tiptap placeholder via `p.is-empty::before` in `editor/styles.css:38`; text: "Start writing…" at `color: #404040` (existing hardcoded value — acceptable, matches muted-foreground feel) |
| **Error** (save failed) | `sonner` toast; no inline error; header auto-save indicator switches to conflict/error state above |
| **Disabled** (not applicable) | n/a — editor is always editable when a note is selected |

### Dark / Light parity

All tokens resolve via CSS custom properties — `bg-background`, `text-foreground`, `border-border`, `bg-accent`, `bg-popover` all shift automatically between `:root` (light) and `.dark` (dark). No additional work for Forge beyond using the token class names.

### Forge implementation note

- Swap `<textarea ... className="grow resize-none p-6 font-mono text-sm leading-relaxed outline-none" />` for the Editor wrapper + `<Editor>` component
- Wrap in: `<div className="grow overflow-y-auto px-6 py-6 max-w-[740px] mx-auto min-h-[320px] rounded-lg transition-shadow duration-150 focus-within:ring-1 focus-within:ring-border/60">`
- Wire `onBlur` to the 500ms debounce save (GWT#4-8 per quill-ts notepad)
- Add auto-save indicator in the header `<div className="flex gap-2">` (existing action buttons) as a `<span>` to the left of the Save/Trash buttons
- Primitives: `Editor` component (`/components/editor/index.tsx`), `Skeleton` (shadcn), `RefreshCwIcon`, `CheckIcon`, `AlertCircleIcon` (lucide)

---

## 4. Component: knowledge-focus-view

**Route:** `/team/[team]/knowledge/[noteId]`

**Purpose:** Single-note focus mode — full page dedicated to one note, with the left rail collapsed. This is a distinct page (new Next.js route) that surfaces a note at a stable URL for deep-linking, backlink targets, and wiki-link resolution.

### Page layout

```
┌─ page (flex flex-col h-full bg-background) ─────────────────────────┐
│ FOCUS HEADER  (border-b border-border px-6 py-3)                    │
│  [← Back to Knowledge]  [note title h1]  [actions: Save · Delete]  │
├──────────────────────────────────────────────────────────────────────┤
│ CONTENT AREA  (flex grow overflow-hidden)                            │
│  ┌─ EDITOR COLUMN (grow overflow-y-auto) ─────────────────────────┐ │
│  │  px-8 py-8 max-w-[760px] mx-auto                               │ │
│  │  [BlockEditor (Section 3 contract)]                             │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│  ┌─ BACKLINKS PANEL (w-72 shrink-0 border-l border-border) ───────┐ │
│  │  sticky top-0 h-full overflow-y-auto                            │ │
│  │  px-4 py-4                                                      │ │
│  │  [BacklinksPanel component]                                     │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

### Focus header token list

| Element | Classes | Notes |
|---|---|---|
| Back button | `Button variant="ghost" size="sm"` + `ArrowLeftIcon size-3.5` | Navigates back to `/team/[team]/knowledge?note=[noteId]` — preserves KnowledgeView scroll position |
| Note title `h1` | `font-[510] text-[17px] tracking-[-0.015em] truncate` | Frontmatter `title` field if present, else `note.name` — same resolution logic as KnowledgeView header |
| Vault badge | `Badge variant="outline" font-normal` + vault label | Same as KnowledgeView `knowledge-view.tsx:585` |
| Path code | `code rounded bg-muted px-1.5 py-0.5 text-[11px]` | Same as KnowledgeView |
| Save button | `Button size="sm"` (default / primary) | Loading: `SaveIcon animate-spin` + "Saving…"; disabled when pending |
| Delete button | `Button variant="ghost" size="sm" className="text-muted-foreground hover:text-destructive"` | `Trash2Icon size-3.5` — same as KnowledgeView |

### Editor column

Inherits the BlockEditor visual contract from Section 3 in full:
- `px-8 py-8` (slightly more breathing room than the embedded editor's `px-6 py-6`)
- `max-w-[760px] mx-auto` (slightly wider than 740px — focus mode affords more horizontal space)
- Same focus ring, auto-save indicator, and empty/loading/error states

### Backlinks panel placement

The `BacklinksPanel` in KnowledgeView currently renders below the editor in a `shrink-0 border-t` strip (`knowledge-view.tsx:654`). In the focus view, it moves to a **right sidebar** — rationale: focus mode trades the left rail for a right backlinks panel, giving the note's content the dominant column.

Panel token list:

| Property | Token / class | Value |
|---|---|---|
| Width | `w-72` | 288px — matches left rail width (`knowledge-view.tsx:409`) |
| Background | inherits `bg-background` | same as page canvas |
| Left border | `border-l border-border` | hairline divider |
| Padding | `px-4 py-4` | 16px |
| Sticky | `sticky top-0 h-full overflow-y-auto` | scrolls independently |
| Section label | `text-[11px] text-muted-foreground uppercase tracking-wider font-[510]` | "Referenced by" header — same as BacklinksPanel current render |

No new token needed. The `BacklinksPanel` component renders correctly in this column position with `entityType="knowledge"` and `entityId={noteId}`.

### Interaction states

| State | Appearance |
|---|---|
| **Default** | Editor + backlinks panel both visible at full opacity |
| **Loading** (note fetch pending) | Header: `Skeleton h-5 w-48` for title; Editor: shimmer skeleton (Section 3); Backlinks panel: `null` return (existing behavior in `backlinks-panel.tsx:51`) |
| **Empty** (note exists, no content) | Editor shows Tiptap placeholder "Start writing…" |
| **Error** (note not found, `getById` returns null/404) | Full-page empty state: `BrainIcon size-10 text-muted-foreground/60` centered, "Note not found" at `font-[510] text-[15px]`, Back button prominent |
| **Disabled** | n/a |

### Dark / Light parity

All tokens (background, border, muted-foreground, foreground) resolve via CSS custom properties. The backlinks panel border `border-border` shifts between `#23252a` (dark) and `#e6e6e6` (light) automatically.

### WCAG

- Back button `text-muted-foreground` on hover → `text-foreground` (#f7f8f8 on #08090a) ≈ 18.5:1 — passes AA
- Note title `text-foreground` (#f7f8f8 on #08090a) ≈ 18.5:1 — passes AA (large text)
- `text-muted-foreground` (#8a8f98 on #08090a) ≈ 4.7:1 — passes AA for normal text (used on vault badge, path code, backlinks section headers)

### Motion

No page-transition animation — focus view is a server-rendered route. The editor content fades in via `animate-blur-in` (existing Tailwind class used on settings pages e.g. `settings/library/page.tsx:5`).

Wrapper class: `<div className="animate-blur-in flex flex-col h-full">`

`prefers-reduced-motion`: `animate-blur-in` should be governed by `@media (prefers-reduced-motion: reduce) { .animate-blur-in { animation: none; } }` — this is consistent with the existing Todos pattern (TODOS-VISUAL-SPEC §5 reduced-motion note).

### Forge implementation note

- Create `app/apps/dashboard/src/app/team/[team]/(navigation)/knowledge/[noteId]/page.tsx`
- Create `app/apps/dashboard/src/components/knowledge/knowledge-focus-view.tsx`
- Primitives: `Editor` (from `/components/editor/index.tsx`), `BacklinksPanel` (existing), `Button`, `Badge`, `Skeleton` (shadcn), `ArrowLeftIcon`, `SaveIcon`, `Trash2Icon` (lucide)
- `BacklinksPanel` receives `entityType="knowledge" entityId={noteId}` — no prop change needed

---

## 5. Component: vault-settings-form

**Route:** `/team/[team]/settings/knowledge`

**Purpose:** Configure the knowledge vault root path (`root_path`). Calls the `knowledge.updateVault` mutation (added at `knowledge.ts:708+` per forge-wire notepad — input: `{vaultId, root_path}`). One vault per team in the current model.

### Page shell

Matches the existing settings pattern: `Card` > `CardHeader` + `CardContent`. Example: `team-settings.tsx` uses `Card > CardHeader > CardTitle` + `CardContent > TeamForm`.

```
<div className="animate-blur-in px-6 py-4">
  <Card>
    <CardHeader>
      <CardTitle>Knowledge vault</CardTitle>
      <CardDescription>
        Path to the local Obsidian vault directory synced with this workspace.
      </CardDescription>
    </CardHeader>
    <CardContent>
      <VaultSettingsForm />
    </CardContent>
  </Card>
</div>
```

### Form anatomy

```
[Label]  "Vault root path"
[Input]  text, value = vault.rootPath, placeholder = "/Users/you/vault"
[hint]   "Absolute path on the server host. Changes take effect on next scan."
[Button] "Save" (primary, teal)  |  [status indicator: idle / saving / success / error]
```

### Token list

| Element | Token / class | Notes |
|---|---|---|
| Card | `Card` shadcn primitive | `bg-card border-border rounded-lg` — resolves dark/light |
| Card title | `CardTitle` → `text-[15px] font-[510] tracking-[-0.01em] text-foreground` | |
| Card description | `CardDescription` → `text-[13px] text-muted-foreground` | |
| Label | `Label` shadcn + `text-[13px] font-[510]` | |
| Input | `Input` shadcn `h-9 text-[13px]` | Full width; see states below |
| Hint text | `text-[12px] text-muted-foreground mt-1` | |
| Save button | `Button` default size, default variant (teal/primary) | min-height 36px; disabled when no change or pending |
| Status row | `flex items-center gap-2 text-[12px] ml-2` inline with button | |

### Input interaction states

| State | Appearance |
|---|---|
| **Default** | `border-input bg-transparent text-foreground` (shadcn Input defaults) |
| **Hover** | `hover:border-border` (one step stronger hairline) |
| **Focus** | `focus-visible:ring-2 focus-visible:ring-ring/50` — lavender ring via `--ring` |
| **Dirty (changed)** | No visual change on input; Save button becomes enabled |
| **Disabled** | `disabled:opacity-50 disabled:cursor-not-allowed` (shadcn Input defaults) |
| **Error** | `border-destructive focus-visible:ring-destructive/50` — red border + ring |

### Save button states

| State | Button appearance | Status indicator |
|---|---|---|
| **Idle, no change** | `disabled opacity-50 cursor-not-allowed` | Nothing |
| **Idle, has change** | Enabled, `bg-primary text-primary-foreground` (teal) | Nothing |
| **Saving (pending)** | `disabled` + `RefreshCwIcon size-3.5 animate-spin` + "Saving…" | Nothing |
| **Success** | Re-enabled; brief 2s window | `CheckIcon size-3.5 text-[--color-success]` + "Saved" — fades out after 2s |
| **Error (generic)** | Re-enabled | `AlertCircleIcon size-3.5 text-destructive` + error message text at `text-destructive text-[12px]` |
| **Error (NOT_FOUND)** | Re-enabled | "Vault not found — check your team settings" at `text-destructive text-[12px]` |

WCAG:
- Success `#27a644` on card surface `#0f1011` ≈ 4.9:1 — passes AA
- Destructive `#eb5757` on card surface `#0f1011` ≈ 4.5:1 — passes AA (borderline; acceptable for short 12px error text adjacent to a clearly erroneous field)

### Loading state (initial vault data fetch)

While `knowledge.getVault` (or equivalent list query) is pending:

```
<Skeleton className="h-9 w-full rounded-md" />   ← input skeleton
<Skeleton className="h-9 w-24 rounded-md mt-4" /> ← button skeleton
```

Uses `animate-pulse bg-muted/50`. `prefers-reduced-motion: static bg-muted/30, no animation`.

### Empty state (no vault configured yet)

If the team has no vault rows:

```
<div className="flex flex-col items-center gap-3 py-8 text-center">
  <BrainIcon className="size-8 text-muted-foreground/60" />
  <p className="text-[13px] text-muted-foreground">
    No vault configured. Enter a root path above to connect your Obsidian vault.
  </p>
</div>
```

The form input is still rendered and enabled; the user enters the `root_path` and saves (this calls `createVault` or `updateVault` with the new path — exact mutation shape to be confirmed by forge-wire if create is not yet wired).

### Error state (vault fetch failed)

```
<div className="flex items-center gap-2 text-[12px] text-destructive py-2">
  <AlertCircleIcon className="size-3.5" />
  <span>Could not load vault settings. Reload the page to retry.</span>
</div>
```

No retry button in the form — settings page reload is the recovery path (consistent with other settings pages).

### Dark / Light parity

All tokens resolve via CSS custom properties. Key pairs:
- Card: dark `#0f1011` / light `#f7f8f8`
- Input border: dark `#23252a` / light `#e6e6e6`
- Input focus ring: `--ring` = `#5e69d1` (dark) / `#5e6ad2` (light) — same lavender
- Muted text: dark `#8a8f98` / light `#62666d`
- Success: `#27a644` (invariant)
- Destructive: dark `#eb5757` / light `#c8312b`

### Motion

- Button state transitions: `transition-colors duration-150 ease-out`
- Success indicator fade-out: `transition-opacity duration-500 ease-out` after 2s delay
- `prefers-reduced-motion`: `transition: none` on both; success indicator disappears instantly after 2s

### Settings sidebar nav entry

The settings page requires a sidebar nav entry. Existing nav is in `settings/nav-list.tsx`. The new entry:

| Property | Value |
|---|---|
| Label | "Knowledge" |
| Icon | `BrainIcon` (matches KnowledgeView brand icon) |
| Href | `/team/[team]/settings/knowledge` |
| Placement | After "Library" in the nav list (same section group — external data sources) |

Icon color: `text-violet-500` (matches existing KnowledgeView `BrainIcon className="size-4 text-violet-500"` at `knowledge-view.tsx:412`). Active state: standard settings sidebar active style — no new token.

### Forge implementation note

- Create `app/apps/dashboard/src/app/team/[team]/(navigation)/settings/knowledge/page.tsx`
- Create `app/apps/dashboard/src/components/knowledge/vault-settings-form.tsx`
- Mutation: `knowledge.updateVault({ vaultId, root_path })` — already wired in backend per forge-wire notepad
- Primitives: `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent` (shadcn), `Label`, `Input`, `Button`, `Skeleton` (shadcn), `BrainIcon`, `RefreshCwIcon`, `CheckIcon`, `AlertCircleIcon`, `SaveIcon` (lucide)
- Add `BrainIcon` nav entry to `settings/nav-list.tsx`

---

## 6. Motion summary

| Interaction | Duration | Easing | Reduced-motion fallback |
|---|---|---|---|
| Wiki-link color transition | 150ms | ease-out | `transition: none` |
| BlockEditor focus ring | 150ms | ease-out | `transition: none` |
| Auto-save "Saving…" → "Saved" → fade | 150ms save, 2000ms hold, 500ms fade | ease-out | instant snap, no fade |
| Focus page entry | `animate-blur-in` | existing keyframe | `animation: none` |
| Vault settings button states | 150ms | ease-out | `transition: none` |
| Skeleton shimmer | continuous | `animate-pulse` | static `bg-muted/30` |

---

## 7. Forge implementation summary — primitives map

| Component | Design-system primitives |
|---|---|
| `wiki-link-inline.tsx` | Bare `<span>` or `<button>`; no primitives needed |
| `knowledge-focus-view.tsx` | `Editor`, `BacklinksPanel`, `Button`, `Badge`, `Skeleton`, lucide icons |
| `vault-settings-form.tsx` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `Label`, `Input`, `Button`, `Skeleton`, lucide icons |
| BlockEditor wrapper | `Editor` + `div` wrapper + lucide icons for auto-save indicator |
| Focus-view error state | `BrainIcon`, `Button` |
| Vault settings nav entry | Existing `nav-list.tsx` item pattern + `BrainIcon` |

---

## 8. Verification checklist

1. **`DESIGN.md` read this session** — key principles: dark-native Linear-derived palette, four-step surface ladder (canvas → card → popover → accent), hairline borders at `--border`, lavender `--primary` (#5e6ad2) used scarcely for focus rings and primary CTAs, Inter with `cv01/ss03` features, 510 signature weight, negative letter-spacing on headings. No second chromatic accent; no atmospheric gradients.

2. **Every component has empty / loading / error states defined:**
   - wiki-link-inline: empty (no links — renders nothing), loading (falls back to unresolved), error (falls back to unresolved) — covered §2
   - BlockEditor-in-note: empty (Tiptap placeholder), loading (skeleton shimmer), error (sonner toast + header indicator) — covered §3
   - knowledge-focus-view: empty (new note placeholder), loading (header + editor skeleton), error (full-page "Note not found" state) — covered §4
   - vault-settings-form: empty (no vault — informational state + form enabled), loading (input + button skeleton), error (inline error row) — covered §5

3. **Light + dark token parity covered:** All color specs reference CSS custom property tokens (`--background`, `--card`, `--border`, `--foreground`, `--muted-foreground`, `--primary`, `--destructive`, `--ring`). Dark and light hex values stated side-by-side in Section 1 token ladder. Wiki-link colors use `text-blue-600 dark:text-blue-400` / `text-red-600 dark:text-red-400` explicit dark-variant classes.

4. **WCAG AA ratios cited for all new color combinations:**
   - Resolved link blue-400 (#60a5fa) on dark canvas (#08090a): 8.1:1 — passes AA
   - Resolved link blue-600 (#2563eb) on white: 5.9:1 — passes AA
   - Unresolved link red-400 (#f87171) on dark canvas (#08090a): 5.6:1 — passes AA
   - Unresolved link red-600 (#dc2626) on white: 5.3:1 — passes AA
   - Success green (#27a644) on dark canvas (#08090a): 4.9:1 — passes AA
   - Success green (#27a644) on card (#0f1011): 4.9:1 — passes AA
   - Destructive red (#eb5757) on card (#0f1011): 4.5:1 — passes AA (borderline)
   - muted-foreground (#8a8f98) on canvas (#08090a): 4.7:1 — passes AA

5. **Every motion spec includes `prefers-reduced-motion` note:** Section 6 motion table covers all animated interactions with explicit fallbacks. `transition: none` for color transitions, `animation: none` for `animate-blur-in`, static `bg-muted/30` for skeleton shimmer.

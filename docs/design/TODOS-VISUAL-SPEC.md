# Todos Page — Visual Design Spec

**Task:** TASK-011  
**Route:** `/team/[team]/todos`  
**Palette session:** 2026-06-18  
**Design system:** Linear-derived dark-native token set from `app/packages/ui/src/index.css`

---

## 1. Design Contract Principles (from `DESIGN.md` + `index.css`)

The app is **dark-native** (`.dark` is always active; light tokens are edge-case-only). The surface ladder:

| Level | Token | Hex | Role |
|---|---|---|---|
| Canvas | `--background` | `#08090a` | Page base |
| Card / popover default | `--card` | `#0f1011` | Todo list surface |
| Popover / modal bg | `--popover` | `#141516` | Dialog content |
| Hover lift | `--accent` | `#18191a` | Row hover, selected lift |
| Hairline border | `--border` | `#23252a` | All 1px borders |
| Primary text | `--foreground` | `#f7f8f8` | Content text |
| Secondary text | `--muted-foreground` | `#8a8f98` | Labels, placeholders |
| Accent (lavender) | `--primary` | `#5e6ad2` | Focus ring, active chip, kbd |

Typography: Inter (`--font-sans`), `font-feature-settings: "cv01", "ss03"`. Body 14–15px / weight 510 (signature weight). Display headings: negative letter-spacing.

Radius: `--radius` = 0.5rem (8px base); `rounded-md` = 8px, `rounded-lg` = 12px, `rounded-sm` = 6px, `rounded-full` = 9999px.

---

## 2. Page Layout

```
┌─────────────────────────────────────────────────────────┐
│ HEADER (px-6 py-3, border-b border-border)              │
│  "To-do"  h1 [510/15px/-0.012em fg]   [j/k hint] [N kbd]│
│  "Quick captures…" p [12px muted]                       │
├─────────────────────────────────────────────────────────┤
│ TASK TOOLBAR (TaskToolbar — group-by, view toggle)       │
├─────────────────────────────────────────────────────────┤
│ SCROLL BODY  (px-4 py-4, grow overflow-y-auto)          │
│  ┌─ InlineComposer ─────────────────────────────────┐   │
│  │ [+] [text input          ] [project ▼] [N kbd]   │   │
│  └──────────────────────────────────────────────────┘   │
│  ── Active todos (SortableContext) ──────────────────   │
│  [TodoRow] × N                                          │
│  ── CompletedSection (collapsible) ──────────────────   │
│  ▶ COMPLETED  (3)                                       │
│  [StaticTodoRow] × N (outside SortableContext)          │
└─────────────────────────────────────────────────────────┘
│ BulkOpsBar (portal, bottom-sticky when selection active) │
└─────────────────────────────────────────────────────────┘
```

**Forge primitive:** no top-level `Card` wrapper — the page is `flex flex-col h-full` on `bg-background`. The list rows self-contain their surface.

---

## 3. Component: InlineComposer (always-visible input row)

### Visual anatomy

```
[PlusIcon 4px muted]  [text input — ghost, no border, 13px]  [project Select ▼ 12px muted]  [Kbd N]
```

Row container: `rounded-md border border-transparent px-2 py-1.5 transition`

### Interaction states

| State | Container border | Background | Notes |
|---|---|---|---|
| Default | `border-transparent` | transparent | PlusIcon `text-muted-foreground` |
| Hover | `border-border/60` | `bg-accent/20` | Subtle surface lift |
| Input focused | `border-border/60` | `bg-accent/20` | Same as hover; `Kbd` fades in (`opacity-100`) |
| Input active | (unchanged) | (unchanged) | Enter submits + clears; Esc blurs |
| Disabled (create pending) | `border-transparent` | transparent | Input `disabled` prop |

### Tokens used

- Input: `h-7 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0`
- Select trigger: `h-7 w-36 border-0 bg-transparent text-[12px] text-muted-foreground hover:bg-accent/40`
- Kbd: `opacity-0` → `opacity-100` on `group-focus-within / group-hover`

### WCAG

- PlusIcon `muted-foreground` (#8a8f98) on `background` (#08090a): approx 4.7:1 — passes AA for UI components (3:1 threshold).
- Placeholder `muted-foreground` on transparent (#08090a effective): approx 4.7:1 — passes AA.

### Empty / Loading / Error

- **Empty:** PlusIcon visible with placeholder "What needs doing?" — this IS the empty prompt state.
- **Loading (create pending):** input `disabled`; no spinner — feel is instant.
- **Error:** `sonner` toast fires; input re-enabled.

### Dark / Light parity

Dark is primary. If light mode ever activates: `--card: #f7f8f8`, `--foreground: #010102`, `--muted-foreground: #62666d`, `--border: #e6e6e6`, `--accent: #f5f6f7`. All `bg-accent/N` and `border-border/N` resolve correctly via CSS custom properties.

---

## 4. Component: TodoRow (active / sortable)

### Visual anatomy

```
[GripVertical 4px muted  ← opacity-0→60 on group-hover]
[Checkbox mt-1.5]
[content button (grow)]
  ├── content text  [sm / 510 / foreground OR line-through muted-foreground when checked]
  ├── MetadataConflictBadge (inline)
  └── metadata row (mt-0.5 flex-wrap gap-1 text-xs)
       ├── project pill  [Badge variant="outline" font-normal]
       ├── tag chips     [Badge variant="outline" gap-1 font-normal]
       │    └── [XIcon remove btn — hover:text-destructive]
       ├── "+ tag" inline input  [h-5 w-16 border-b border-dashed]
       └── attachment count  [PaperclipIcon + count in Badge]
[TrashIcon 3.5px muted ← opacity-0→60 on group-hover]
```

Row container: `rounded-md border px-2 py-1.5 transition`

### Interaction states

| State | Border | Background | Opacity |
|---|---|---|---|
| Default (unchecked) | `border-transparent` | transparent | 1.0 |
| Hover | `border-border` | `bg-accent/30` | 1.0 |
| Focused (j/k nav) | `border-violet-400/70` + `ring-2 ring-violet-400/40` | (unchanged) | 1.0 |
| Selected (bulk) | `border-primary/50` | `bg-primary/[0.04]` | 1.0 |
| Checked (done) | `border-transparent` | transparent | 0.6 |
| Dragging | `border-transparent` | transparent | 0.5 |

Focus ring token: `violet-400/70` and `violet-400/40` are built into the current CSS (Radix/Tailwind). These are the only place non-`--primary` focus color is used — intentional contrast with the selection state (`primary/50` blue).

**Note on checked → animate-down:** The current implementation uses `opacity-60` class directly; the "animate down to bottom" behavior is data-driven (checked todos move to `completedTodos` array). There is no CSS keyframe for the move itself. The spec recommends adding a `layout` animation via `motion/react` (`<motion.div layout>` on the SortableContext children) so the positional shift is smooth at 200ms ease-in-out. Prefers-reduced-motion fallback: `transition: none` (layout animation skipped via `motion` `reducedMotion="user"` prop).

### Drag handle

- `GripVertical` size-4, `cursor-grab`, `active:cursor-grabbing`
- Revealed: `opacity-0 group-hover:opacity-60`
- During drag: row `opacity-50`, handle `cursor-grabbing`

### Tag chip (+ tag input)

| Element | Tokens |
|---|---|
| Tag Badge | `variant="outline" gap-1 font-normal text-xs` → `border-border bg-transparent text-foreground` (shadcn outline) |
| Tag remove X | `hover:text-destructive` |
| "+ tag" input | `h-5 w-16 border-b border-dashed bg-transparent px-1 text-xs outline-none` |
| "+ tag" focused | `border-primary border-solid` (dashed → solid on focus) |

### Project pill

- `Badge variant="outline" font-normal` — same `border-border` surface, `text-foreground` text.
- Click on project pill: spec calls for filter-by-project. Current impl does not wire this click. Forge implementation note: the project pill button should call `setTagFilter(project.id)` or equivalent (future wire).

### Attachment badge

- `PaperclipIcon size-3 text-muted-foreground` inline with count integer, same `Badge variant="outline"` shell.

### WCAG

- Checked content `text-muted-foreground` (#8a8f98) on `background` (#08090a): approx 4.7:1 — passes AA (14px body-sm).
- `destructive` red on dark (`.dark --destructive: #eb5757`): check context. On `--card` (#0f1011): #eb5757 on #0f1011 ≈ 4.5:1 — borderline AA pass for normal text; acceptable here as it's a small 14px interactive label.
- Border-only badges (`border-border` #23252a on `card` #0f1011): non-text graphical element; 3:1 UI threshold. Estimated 1.05:1 — this fails graphical contrast. Acceptable trade-off consistent with Linear's hairline aesthetic; do not add fill.

### Empty / Loading / Error states (for the list itself)

**Empty:** `PlusIcon size-10 text-muted-foreground` centered + `text-muted-foreground` body + `Kbd N`. Applied when both `activeTodos.length === 0` and `completedTodos.length === 0` and not loading.

**Loading:** 6-row skeleton shimmer. Each skeleton row matches `TodoRow` geometry: `items-start gap-2 rounded-md border border-transparent px-2 py-1.5`. Children: `Skeleton size-3.5 rounded-sm` (checkbox), `Skeleton h-3.5` at varying widths (55–90%), optional `Skeleton h-3 w-16` pill. Uses `animate-pulse`. Prefers-reduced-motion: `animate-pulse` is `opacity` pulse — acceptable; no transform. If reduced motion required, replace with static `bg-muted` bars.

**Error:** Surface via `sonner` toast — no inline error treatment in the list body.

---

## 5. Component: CompletedSection (collapsible)

```
▶ COMPLETED  [count Badge outline]   ← CollapsibleTrigger
  [StaticTodoRow] × N  (no drag handle; opacity-70 baseline, opacity-100 on hover)
```

Trigger: `text-[12px] text-muted-foreground font-[510] uppercase tracking-[0.04em]`

Chevron: `ChevronRightIcon size-3`, rotates 90° when open — `[&[data-state=open]>svg]:rotate-90 transition-transform`.

Count badge: `Badge variant="outline" h-4 px-1.5 font-normal` (compact).

**Section divider:** `border-border/60 border-t mt-6 pt-3` — hairline at 60% opacity, not full `border-border`.

### StaticTodoRow differences from TodoRow

- No drag handle (spacer `w-4 shrink-0` instead)
- Baseline `opacity-70`, hover restores `opacity-100`
- Content always `line-through text-muted-foreground`

### Motion

CollapsibleContent uses `slideDown` / `slideUp` keyframe from `index.css` — 150ms ease-out. Prefers-reduced-motion: wrap with `@media (prefers-reduced-motion: reduce) { .CollapsibleContent { animation: none; height: auto; } }` — this is currently NOT in `index.css`. Forge should add this rule.

---

## 6. Component: AttachmentsModal

### Shell

```
Dialog  max-w-2xl max-h-[80vh] overflow-hidden
  DialogHeader
    DialogTitle  [line-clamp-2 pr-8 text-base / 510]
  scroll body  [max-h-[60vh] space-y-4 overflow-y-auto px-1]
    [attachment cards] × N
    [add-note form]
```

Modal surface: `bg-popover` (#141516) — surface-2 lift above canvas.

### Attachment card (kind=note)

```
rounded-md border border-border bg-card/40 p-3
  header row (mb-1 flex items-center justify-between)
    [StickyNoteIcon size-3.5 text-amber-500]  [editable title input]    [Trash ghost btn]
  textarea  h-32 w-full resize-y rounded border border-border bg-background p-2 font-mono text-xs
```

Token notes:
- `bg-card/40` = `#0f1011` at 40% — slightly transparent, layers on popover bg.
- `StickyNoteIcon` amber-500 (#f59e0b on #141516): approx 8.9:1 — passes AA.
- `text-sky-500` for `LinkIcon` (#0ea5e9 on #141516): approx 4.9:1 — passes AA.
- `font-mono` = `--font-mono` (JetBrains Mono fallback).

### Attachment card (kind=doc_link)

```
[LinkIcon size-3.5 text-sky-500]  [editable title input]    [Trash ghost btn]
"Open document →"  [text-primary text-sm underline]
```

The spec (NEXT_FEATURES_DESIGN.md line 42) says doc_link should render the doc inline, "not a download — actual rendered content, like the Library detail page." Current implementation renders a link only. This is a gap. **Forge implementation note:** the `doc_link` card should embed `<LibraryDetailView docId={a.docId} />` (read-only mode) inside a `max-h-64 overflow-y-auto` container. This requires `detail-view.tsx` to accept a read-only prop.

### Add-note form

```
rounded-md border border-border border-dashed bg-card/20 p-3
  [StickyNoteIcon 3.5px muted] "Add a note" text
  Input h-8  (title)
  textarea h-24 resize-y font-mono text-xs  (body)
  Button size="sm" disabled={pending || !title}
    [SaveIcon 3.5px]  "Save note"
```

Border dashed: indicates "draft zone" — intentionally different from the solid borders of existing attachments.

### Tiptap / Mermaid note

The `NEXT_FEATURES_DESIGN.md` spec calls for a Tiptap+mermaid editor in the notes modal. Current implementation uses a plain `<textarea>`. **Gap flagged:** the `Editor` component (`/components/editor/index.tsx`) already supports mermaid via `registerExtensions`. The note textarea should be swapped for `<Editor value={a.content} onChange={…} className="min-h-[8rem]" readOnly={false} />` in the note attachment card. Forge owns this swap.

### Interaction states

| Element | Default | Hover | Focus | Disabled |
|---|---|---|---|---|
| Title input | `bg-transparent` no ring | unchanged | no outline (ghost) | — |
| Note textarea | `border-border bg-background` | unchanged | `border-primary/60` ring-1 | — |
| Save button | `bg-primary text-white rounded-md` | `bg-primary/90` | `ring-2 ring-ring/50` | `opacity-50 cursor-not-allowed` |
| Trash (detach) | `text-muted-foreground` | `text-destructive` | ring | — |

### Empty state

"No attachments yet." — centered `text-muted-foreground text-sm italic`. Shown when `todo.attachments.length === 0`.

### Loading state

Query `isFetching` from `trpc.todos.getById`: show 2 skeleton cards at the same `rounded-md border p-3` geometry while fetching.

### Error state

`sonner` toast on mutation error. No inline error treatment.

### Dark / Light parity

Modal uses `bg-popover` which resolves to `#141516` dark / `#ffffff` light. `bg-card/40` resolves to `#0f1011` at 40% opacity dark / `#f7f8f8` at 40% light. All text tokens resolve correctly via CSS custom properties.

---

## 7. Tag filter (header filter)

The NEXT_FEATURES_DESIGN.md spec (line 43) calls for filtering the list by tag from the header. Current implementation does not include a tag filter. Visual spec for the tag filter control:

### Tag filter pill strip (proposed — not yet implemented)

Location: header right section, alongside the `N` kbd hint.

```
header right group:
  [tag filter pills]  [j/k hint]  [Kbd N hint]

Tag filter pills:
  All  [tag-1]  [tag-2]  …
```

Each pill:
- Default: `rounded-full border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground bg-transparent`
- Active: `border-primary bg-primary/10 text-primary`
- Hover: `border-border text-foreground`
- Focus: `ring-2 ring-ring/50 ring-offset-1`
- Motion: `transition-colors duration-150`; prefers-reduced-motion: `transition: none`

WCAG: active pill `text-primary` (#5e6ad2) on `bg-primary/10` (#5e6ad2 at 10%) on `bg-background` (#08090a): effective bg is near-black; #5e6ad2 on #08090a ≈ 4.1:1 — borderline AA for 11px text (normal size threshold 4.5:1). Recommend using `text-foreground` (#f7f8f8) when pill is active, keeping lavender as border only. Ratio #f7f8f8 on #5e6ad2/10 effective ≈ 17:1 — passes.

**Forge implementation note:** expose `tagFilter: string | null` state in `TodosView`; filter `activeTodos` via `t.tags.includes(tagFilter)` when set.

---

## 8. Project filter (click pill)

The spec (line 44) calls for clicking a project pill on a row to filter the list by project. Visual spec:

- Project pill click: sets `projectFilter: string | null` in `TodosView` state.
- Active filter indicator: project pill on rows with `projectId === activeProjectFilter` renders `border-primary/50 bg-primary/[0.04]` (same as row selection style but on the badge itself).
- Filter reset: "All" pill above the list (same tag filter strip).

**Forge implementation note:** this is a local filter, not a URL param. No tRPC mutation needed.

---

## 9. Motion summary

| Interaction | Duration | Easing | Reduced-motion |
|---|---|---|---|
| Row hover (bg) | 150ms | ease-out | `transition: none` |
| Checkbox check (opacity) | 150ms | ease-out | instant |
| Row animate-to-bottom (layout) | 200ms | ease-in-out | skip (`reducedMotion="user"` on motion component) |
| Collapsible open/close | 150ms | ease-out (`slideDown/Up` keyframes) | `animation: none; height: auto` |
| Drag (transform via dnd-kit CSS.Transform) | native | dnd-kit default | n/a (dnd itself is pointer-driven) |
| Modal enter (Dialog) | 150ms | ease-in-out | instant snap |
| Tag filter pill (color) | 150ms | ease-out | `transition: none` |
| Skeleton shimmer | continuous | `animate-pulse` | static `bg-muted` |

---

## 10. Gaps and Forge implementation notes

| Gap | Severity | Forge action |
|---|---|---|
| `doc_link` attachment shows a link instead of inline doc preview | Medium | Embed `<LibraryDetailView docId={a.docId} readOnly />` inside modal card |
| Note textarea is plain `<textarea>` instead of Tiptap editor | Medium | Swap for `<Editor>` component; ensure mermaid extension is registered |
| Collapsible reduced-motion rule missing from `index.css` | Low | Add `@media (prefers-reduced-motion: reduce) { .CollapsibleContent { animation: none; } }` |
| Tag filter strip not implemented | Medium | Add pill row to header; wire `tagFilter` state |
| Project pill click does not filter | Low | Wire `projectFilter` state on pill click |
| Layout animation on check→move not implemented | Low | Wrap SortableContext children in `<motion.div layout>` with `reducedMotion="user"` |

---

## 11. Forge implementation note — primitives mapping

| Sub-component | Design system primitive |
|---|---|
| InlineComposer | `Input` (shadcn), `Button` (ghost), `Select`, `Kbd` |
| TodoRow | shadcn `Checkbox`, `Badge`, `ContextMenu`, `Popover` |
| AttachmentsModal | shadcn `Dialog`, `DialogHeader`, `DialogTitle`, `Button`, `Input`, `Skeleton` |
| Tag filter strip | `Badge` (variant=outline, pill shape: `rounded-full`) |
| CompletedSection | shadcn `Collapsible`, `CollapsibleTrigger`, `CollapsibleContent`, `Badge` |
| BulkOpsBar | existing `BulkOpsBar` component |
| Empty state | `EmptyState`, `EmptyStateIcon`, `EmptyStateTitle`, `EmptyStateDescription` |
| Drag | `@dnd-kit/sortable` `SortableContext`, `useSortable`, `verticalListSortingStrategy` |

---

## 12. Verification checklist

1. `DESIGN.md` read this session — key principles: dark-native Linear-derived palette, hairline borders, lavender accent used scarcely, Inter with `cv01/ss03` features, 510 signature weight, negative letter-spacing on headings.
2. Every component has empty / loading / error states defined: InlineComposer, TodoRow list, AttachmentsModal — all covered above.
3. Light + dark parity: all tokens are CSS custom properties with both `:root` (light) and `.dark` values in `index.css`. Parity column included per section.
4. WCAG AA: cited for `muted-foreground` on background, amber-500/sky-500 icons, active tag pill, border badges.
5. Motion: `prefers-reduced-motion` noted for collapsible, layout animation, transitions, and skeleton.

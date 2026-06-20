# Prompt Project Picker — Visual Design Spec

**Task:** TASK-013
**Route:** `/team/[team]/prompts/[productSlug]/[promptSlug]` (edit-view) and `/team/[team]/prompts/[productSlug]` (list-view)
**Palette session:** 2026-06-20
**Design system:** Linear-derived dark-native token set from `app/packages/ui/src/index.css`

---

## 1. Design Contract Principles (from `docs/design/TODOS-VISUAL-SPEC.md` + `index.css`)

The app is **dark-native** (`.dark` is always active; `color-scheme: dark` on `html,body`). The surface ladder:

| Level | Token | Dark hex | Role |
|---|---|---|---|
| Canvas | `--background` | `#08090a` | Page base |
| surface-1 | `--card` | `#0f1011` | Panel, picker bg |
| surface-2 | `--popover` | `#141516` | Dropdown content |
| Hover lift | `--accent` | `#18191a` | Row hover, item hover |
| Hairline | `--border` | `#23252a` | 1px borders |
| Primary text | `--foreground` | `#f7f8f8` | Content |
| Secondary text | `--muted-foreground` | `#8a8f98` | Labels, placeholders |
| Accent lavender | `--primary` | `#5e6ad2` | Selected state, focus ring, project dot |
| Destructive | `--destructive` | `#eb5757` | Clear-X hover |

Typography: Inter (`--font-sans`), `font-feature-settings: "cv01", "ss03"`. Body 13–14px / weight 510 (signature weight).

Radius: `--radius` = 0.5rem (8px base); `rounded-md` = 8px, `rounded-lg` = 12px, `rounded-sm` = 6px, `rounded-full` = 9999px.

---

## 2. Component A — ProjectPicker (edit-view right panel)

### Location

Inserted into the existing `<aside>` panel in `edit-view.tsx` at `app/apps/dashboard/src/components/prompts/edit-view.tsx:306`:

```
<aside className="flex min-w-0 flex-col gap-3 overflow-y-auto rounded-md border border-border bg-card/40 p-3">
  <!-- existing: Variables section -->
  <!-- existing: Preview section -->
  <!-- NEW: Project section (appended at bottom of aside) -->
  <div className="border-border border-t pt-3"> [ProjectPicker] </div>
  <!-- existing: BacklinksPanel -->
</aside>
```

The picker sits between the variable preview block and `<BacklinksPanel>`. It is always visible regardless of variable count.

### Visual anatomy

```
┌─ aside panel ──────────────────────────────────────┐
│ PROJECT                           ← section label  │
│ ┌─────────────────────────────────────────────────┐│
│ │ 📁  Set project…                           ▾    ││  ← default (no project)
│ └─────────────────────────────────────────────────┘│
│  OR                                                 │
│ ┌─────────────────────────────────────────────────┐│
│ │ ● Nexus Platform                           ✕    ││  ← set state
│ └─────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

### Section label

`text-[10px] text-muted-foreground font-[600] uppercase tracking-[0.06em]` — matches existing "Variables (N)" label pattern in the aside.

### Trigger button (no project set — default)

| Property | Value |
|---|---|
| Height | `h-[30px]` |
| Padding | `px-2` |
| Radius | `rounded-md` (6px) |
| Border | `border border-transparent` |
| Background | `bg-transparent` |
| Text | `text-[12px] text-muted-foreground` |
| Icon | `FolderIcon size-3 text-muted-foreground opacity-50` |
| Chevron | `ChevronDownIcon size-3 ml-auto opacity-40 text-muted-foreground` |
| Width | `w-full` |

**Hover:** `border-border/80 bg-accent/50 text-foreground`

**Focus:** `border-primary ring-[3px] ring-primary/25 outline-none` (keyboard-accessible; do not suppress ring)

**Disabled (save pending):** `opacity-50 cursor-not-allowed pointer-events-none`

### Trigger button (project set)

| Property | Value |
|---|---|
| Border | `border border-border` |
| Background | `bg-accent/30` |
| Text | `text-[12px] text-foreground` |
| Leading element | Project color dot `w-[7px] h-[7px] rounded-full bg-[projectColor]` |
| Trailing element | Clear button `✕` (see below) |

The project dot color comes from a hash of `project.id` into the existing `TAG_PALETTE` from `list-view.tsx:59–67`. Forge must reuse the same `tagColor()` function (or extract it to a shared util) to ensure project dot colors are stable across views.

### Clear (✕) button inside trigger

Rendered as a nested `<button>` inside the trigger when project is set:

| State | Tokens |
|---|---|
| Default | `size-[14px] rounded-[3px] text-muted-foreground` |
| Hover | `bg-destructive/12 text-destructive` |
| Focus | `ring-1 ring-destructive/50 outline-none` |

Clicking the ✕ calls `onClear()` (sets `projectId` to `null`), stops propagation to prevent the dropdown from opening.

### Dropdown (Radix Popover, `align="start"`)

Surface: `bg-popover border border-border rounded-lg shadow-[0_8px_24px_rgba(0,0,0,.5)] w-[--radix-popover-trigger-width] min-w-[200px] p-1`

**Search input row (at top of dropdown):**

`flex items-center gap-1.5 px-2 py-1.5 border-b border-border/70`

- `SearchIcon size-3 text-muted-foreground`
- `<input>` — `bg-transparent border-0 outline-none text-[12px] text-foreground placeholder:text-muted-foreground flex-1 font-[--font-sans]`

**"No project (clear)" item (always first, below search):**

`flex items-center gap-2 px-2 py-1.5 rounded-[5px] text-[12px] text-muted-foreground cursor-pointer`

- Hover: `bg-accent text-foreground`
- Icon: `MinusIcon size-3` or em-dash `—`
- Text: "No project"

**Section divider + label:**

`border-t border-border/60 my-1` then `px-2 py-1 text-[10px] text-muted-foreground font-[600] uppercase tracking-[0.06em]` — "Projects"

**Project item:**

`flex items-center gap-2 px-2 py-1.5 rounded-[5px] text-[13px] text-foreground cursor-pointer`

- Color dot: `w-[7px] h-[7px] rounded-full`
- Name: `truncate flex-1`
- Selected checkmark: `CheckIcon size-3 ml-auto text-primary` (shown only when `projectId === item.id`)
- Hover: `bg-accent`
- Selected bg: `bg-primary/[0.08]`

**No search results:**

`px-2 py-3 text-center text-[12px] text-muted-foreground italic` — "No matching projects"

**Empty (no projects exist):**

`px-2 py-3 text-center text-[12px] text-muted-foreground` — "No projects yet"

### Empty / Loading / Error states

| State | Treatment |
|---|---|
| **Empty (no projects)** | Dropdown renders "No projects yet" copy in `text-muted-foreground text-[12px]`. Trigger shows "Set project…" default appearance. |
| **Loading (projects fetching)** | Trigger shows `bg-muted/40 animate-pulse rounded-md h-[30px] w-full` shimmer. Dropdown not openable. |
| **Error (fetch failed)** | Trigger enabled; dropdown shows single item: "Could not load projects" in `text-destructive text-[12px]`. Sonner toast on mutation error. |
| **Save pending** | Trigger `opacity-50 pointer-events-none` during `updateMut.isPending`. |

### Light mode parity (edge case)

| Token | Light value | Dark value |
|---|---|---|
| `--background` | `#ffffff` | `#08090a` |
| `--card` | `#f7f8f8` | `#0f1011` |
| `--popover` | `#ffffff` | `#141516` |
| `--border` | `#e6e6e6` | `#23252a` |
| `--foreground` | `#010102` | `#f7f8f8` |
| `--muted-foreground` | `#62666d` | `#8a8f98` |
| `--primary` | `#5e6ad2` | `#5e6ad2` |
| `--accent` | `#f5f6f7` | `#18191a` |
| `--destructive` | `#c8312b` | `#eb5757` |

All picker tokens are CSS custom properties — they resolve correctly in both modes without any conditional logic.

### Motion

| Interaction | Duration | Easing | Reduced-motion |
|---|---|---|---|
| Trigger hover (border/bg) | `150ms` | `ease-out` | `transition: none` |
| Dropdown open (Radix) | `150ms` | `ease-in-out` | Instant snap |
| Clear X hover (color) | `100ms` | `ease-out` | `transition: none` |
| Project item hover | `100ms` | `ease-out` | `transition: none` |

`@media (prefers-reduced-motion: reduce)` override:
```css
.project-picker-trigger, .project-picker-item { transition: none; }
```

Forge should apply `data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0` via Radix Popover content props (consistent with existing Popover usage in list-view.tsx).

### WCAG AA

| Pair | Approx ratio | Threshold | Result |
|---|---|---|---|
| `muted-foreground` (#8a8f98) on `background` (#08090a) — placeholder | 4.7:1 | 4.5:1 normal | PASS |
| `foreground` (#f7f8f8) on `card` (#0f1011) — set state text | 17:1 | 4.5:1 | PASS |
| `foreground` (#f7f8f8) on `popover` (#141516) — dropdown items | 15:1 | 4.5:1 | PASS |
| `primary` (#5e6ad2) dot on `background` — graphical | 3.5:1 | 3:1 (UI element) | PASS |
| `destructive` (#eb5757) on `popover` (#141516) — clear hover | 4.5:1 | 4.5:1 | PASS (borderline) |
| Hairline `--border` (#23252a) on `background` (#08090a) | 1.3:1 | 3:1 (graphical) | WARN: design intent (Linear hairline aesthetic), consistent with TASK-011 prior art |

### Forge implementation note

Primitive: `Popover`, `PopoverTrigger`, `PopoverContent` (shadcn/ui, already used in `list-view.tsx:16–20`).

No new design-system primitives needed. The project dot color uses the existing `tagColor()` function from `list-view.tsx:70–74` — extract to `app/apps/dashboard/src/lib/project-color.ts` and import in both views.

---

## 3. Component B — ProjectBadge (list-view rows)

### Location

Inserted into the existing prompt row grid in `list-view.tsx` at the `col 1: name + version` block (`list-view.tsx:531–541`):

```
{/* col 1: name + version + optional project badge */}
<div className="pointer-events-none relative z-10 flex min-w-0 items-center gap-2">
  <span …>{p.name}</span>
  <Badge variant="outline" …>v{p.version}</Badge>
  {p.projectId && <ProjectBadge projectId={p.projectId} projectName={p.projectName} />}
</div>
```

Badge only renders when `p.projectId` is set — absence is intentional (not an error, not an empty state).

### Visual anatomy

```
[ ● Nexus Platform ]
```

| Property | Value |
|---|---|
| Height | `h-[18px]` — matches version badge |
| Padding | `px-[7px]` |
| Radius | `rounded-full` |
| Border | `border border-border` |
| Background | `bg-transparent` |
| Text | `text-[10px] text-muted-foreground font-normal` |
| Dot | `w-[5px] h-[5px] rounded-full` (project color from hash) |
| Gap | `gap-1` between dot and name |

### Interaction states

| State | Tokens |
|---|---|
| **Default** | `border-border bg-transparent text-muted-foreground` |
| **Hover** | `border-primary/50 bg-primary/[0.06] text-foreground cursor-pointer` |
| **Focus** | `ring-1 ring-ring/50 outline-none` |
| **Active filter** | `border-primary/50 bg-primary/[0.08] text-foreground` |
| **Disabled** | N/A — badge is always clickable when project is set |

Clicking the badge filters the list to show only prompts with the same `projectId`. A second click (or click on an "All" pill) clears the filter. This is a local UI filter — no tRPC mutation, no URL param.

### Filter indicator (above list)

When `projectFilter` is set, a small filter-active pill appears in the list header (alongside existing search/sort controls):

```
[ ● Nexus Platform  ✕ ]
```

Token spec:
- Pill: `rounded-full border border-primary/50 bg-primary/[0.08] px-2 h-[22px] text-[11px] text-foreground font-normal flex items-center gap-1.5`
- Dot: `w-[6px] h-[6px] rounded-full`
- Clear ✕: `size-[12px] ml-0.5 text-muted-foreground hover:text-destructive`

### Empty / Loading / Error states

The badge itself has no loading or error state — project data is co-loaded with the prompt list. When project name is unavailable (e.g., project deleted), render badge as `text-muted-foreground italic` with text "Unknown project" and no dot. No retry UI — the filter simply filters by `projectId` (still functional even without display name).

| State | Treatment |
|---|---|
| **No project on row** | Badge absent. Row renders name + version badge only. Not an error. |
| **Project name unavailable** | `border-border text-muted-foreground italic` — "Unknown project" with no dot. |
| **List loading** | Project badge not shown during skeleton state (skeleton rows replace the entire row). |
| **List error** | Handled by existing empty/error pattern in list-view — badge is not involved. |

### Light mode parity

Same token table as Component A applies. All `border-primary/N`, `bg-primary/N`, `text-foreground`, and `text-muted-foreground` resolve correctly in both modes via CSS custom properties.

### Motion

| Interaction | Duration | Easing | Reduced-motion |
|---|---|---|---|
| Badge hover (border/bg/color) | `150ms` | `ease-out` | `transition: none` |
| Filter pill appear | Layout shift handled by flex gap — no animation needed | — | N/A |

`@media (prefers-reduced-motion: reduce)` override:
```css
.project-badge { transition: none; }
```

### WCAG AA

| Pair | Approx ratio | Threshold | Result |
|---|---|---|---|
| `muted-foreground` (#8a8f98) on canvas (#08090a) — badge text default | 4.7:1 | 4.5:1 (10px = normal) | PASS |
| `foreground` (#f7f8f8) on row hover bg (~#08090a+4%) | 18:1 | 4.5:1 | PASS |
| `foreground` on `bg-primary/[0.08]` effective bg (~#0b0c14) | 17:1 | 4.5:1 | PASS |
| Project dot (5px graphical) | graphical element; dot size < 3:1 threshold is acceptable, color used for identity not information | N/A | Acceptable |
| Filter pill `foreground` on `bg-primary/[0.08]` | 17:1 | 4.5:1 | PASS |

### Forge implementation note

Primitive: shadcn `Badge` (variant `outline`) already used throughout `list-view.tsx`. The `ProjectBadge` wraps a `<button>` (for click-to-filter) around `Badge` markup. The `pointer-events-none` on col-1 div must be lifted to `z-10` for the badge button to receive clicks — same pattern as `TestPromptPopover` (`list-view.tsx:221`).

---

## 4. Token summary

All tokens below are already defined in `app/packages/ui/src/index.css`. No new tokens required.

| Role | Token (CSS var) | Tailwind | Dark hex |
|---|---|---|---|
| Picker trigger bg (set) | `--accent` / `bg-accent/30` | `bg-accent/30` | `rgba(24,25,26,.3)` |
| Picker border default | `--border` | `border-border` | `#23252a` |
| Picker border transparent | — | `border-transparent` | transparent |
| Picker text default | `--muted-foreground` | `text-muted-foreground` | `#8a8f98` |
| Picker text set | `--foreground` | `text-foreground` | `#f7f8f8` |
| Dropdown bg | `--popover` | `bg-popover` | `#141516` |
| Project dot / accent | `--primary` | `bg-primary` | `#5e6ad2` |
| Focus ring | `--ring` / `--primary` | `ring-ring/50` / `ring-primary/25` | `rgba(94,106,210,.25)` |
| Hover lift | `--accent` | `bg-accent` | `#18191a` |
| Active filter bg | `--primary` | `bg-primary/[0.08]` | `rgba(94,106,210,.08)` |
| Active filter border | `--primary` | `border-primary/50` | `rgba(94,106,210,.5)` |
| Clear X hover bg | `--destructive` | `bg-destructive/12` | `rgba(235,87,87,.12)` |
| Clear X hover color | `--destructive` | `text-destructive` | `#eb5757` |

---

## 5. Interaction summary (both components)

| Component | State | Border | Background | Text | Notes |
|---|---|---|---|---|---|
| Picker trigger (no project) | Default | transparent | transparent | `muted-foreground` | Icon opacity-50 |
| Picker trigger (no project) | Hover | `border/80` | `accent/50` | `foreground` | 150ms |
| Picker trigger (no project) | Focus | `primary` | `accent/30` | `foreground` | ring-3 primary/25 |
| Picker trigger (no project) | Disabled | transparent | transparent | `muted-foreground` | opacity-50 |
| Picker trigger (project set) | Default | `border` | `accent/30` | `foreground` | Dot + clear ✕ |
| Picker trigger (project set) | Hover | `border` | `accent/50` | `foreground` | — |
| Picker trigger (project set) | Focus | `primary` | `accent/30` | `foreground` | ring-3 primary/25 |
| Clear ✕ button | Default | — | transparent | `muted-foreground` | — |
| Clear ✕ button | Hover | — | `destructive/12` | `destructive` | 100ms |
| Dropdown item | Default | — | transparent | `foreground` | — |
| Dropdown item | Hover | — | `accent` | `foreground` | 100ms |
| Dropdown item | Selected | — | `primary/08` | `foreground` | Checkmark icon |
| Project badge | Default | `border` | transparent | `muted-foreground` | — |
| Project badge | Hover | `primary/50` | `primary/06` | `foreground` | 150ms, cursor-pointer |
| Project badge | Focus | — | — | — | ring-1 ring/50 |
| Project badge | Active filter | `primary/50` | `primary/08` | `foreground` | Persistent until cleared |

---

## 6. Motion budget

| Interaction | Duration | Easing | Reduced-motion |
|---|---|---|---|
| Picker trigger hover | 150ms | ease-out | `transition: none` |
| Dropdown open/close (Radix) | 150ms | ease-in-out | Instant snap |
| Dropdown item hover | 100ms | ease-out | `transition: none` |
| Clear X hover | 100ms | ease-out | `transition: none` |
| Project badge hover | 150ms | ease-out | `transition: none` |
| Filter pill appear | — | — | No animation (flex layout shift) |

Reduced-motion rule to add in `index.css` (or component style):
```css
@media (prefers-reduced-motion: reduce) {
  .project-picker-trigger,
  .project-picker-item,
  .project-badge {
    transition: none;
  }
}
```

---

## 7. Verification checklist

1. **`design/design.md`:** No `design/design.md` file exists at the repo root — the binding design contract is documented in `docs/design/TODOS-VISUAL-SPEC.md` (TASK-011 prior art) and `app/packages/ui/src/index.css`. Key principles drawn from both: dark-native Linear-derived palette, lavender scarce, hairline borders, Inter with cv01/ss03, weight-510, negative letter-spacing on headings, radix primitives.
2. **Empty / loading / error states:** Defined for both components — picker (3 states each) and badge (3 states including "no project" as intentional absence).
3. **Light + dark parity:** All tokens are CSS custom properties with both `:root` (light) and `.dark` values in `index.css`. Parity table in sections 2 and 3.
4. **WCAG AA:** Ratios cited for all new color combinations. One WARN (hairline border) is intentional per prior TASK-011 art.
5. **Motion:** `prefers-reduced-motion` CSS rule specified for all transition properties. Radix Popover handles its own reduced-motion via `data-[state]` attribute classes.

---

## 8. Forge implementation notes — primitives mapping

| Sub-component | Design system primitive |
|---|---|
| ProjectPicker trigger | `<button>` (not shadcn Button — needs custom height/border/reset) OR `Button variant="ghost"` with full class override |
| ProjectPicker dropdown | `Popover`, `PopoverTrigger`, `PopoverContent` (shadcn, already imported in list-view) |
| Dropdown search input | `<input>` (bare, ghost — same pattern as `SearchIcon` input in list-view.tsx:440) |
| Dropdown items | bare `<button>` elements inside `PopoverContent` |
| ProjectBadge | `Badge variant="outline"` wrapped in `<button>` (click-to-filter) |
| Filter active pill | Inline flex `<button>` matching existing filter affordance style (no separate primitive) |
| Clear ✕ button | Bare `<button>` nested inside picker trigger; `e.stopPropagation()` required |
| Project dot | Inline `<span>` with `rounded-full` + dynamic `style={{ background: tagColor(projectId) }}` |
| Loading skeleton | `<Skeleton className="h-[30px] w-full rounded-md" />` (shadcn Skeleton, already available) |

**Shared utility:** Extract `tagColor()` from `list-view.tsx:70–74` to `app/apps/dashboard/src/lib/project-color.ts`. Import in `edit-view.tsx` and `list-view.tsx` (or wherever `ProjectBadge` is defined).

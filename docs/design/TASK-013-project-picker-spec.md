# TASK-013 — Project Picker + Project Badge: Visual Spec

**Palette session:** 2026-06-20
**Token source:** `app/packages/ui/src/index.css` (confirmed read)
**App surface:** dark-native (`.dark` forced; `color-scheme: dark` on `html,body`)
**Prior art:** `docs/design/PROMPT-PROJECT-PICKER-SPEC.md` (full prior Palette session; this spec supersedes it with a tighter, implementation-ready form)

---

## Design Contract Summary

The app is a Linear-derived, dark-native UI. All tokens are CSS custom properties resolving from `index.css`. No hex values appear in this spec — only token names.

| Token (CSS var) | Dark hex | Light hex | Role |
|---|---|---|---|
| `--background` | `#08090a` | `#ffffff` | Canvas |
| `--card` | `#0f1011` | `#f7f8f8` | Panel surface (aside bg) |
| `--popover` | `#141516` | `#ffffff` | Dropdown surface |
| `--accent` | `#18191a` | `#f5f6f7` | Hover lift |
| `--border` | `#23252a` | `#e6e6e6` | Hairline borders |
| `--foreground` | `#f7f8f8` | `#010102` | Primary text |
| `--muted-foreground` | `#8a8f98` | `#62666d` | Secondary text, placeholders |
| `--primary` | `#5e6ad2` | `#5e6ad2` | Focus ring, selected state |
| `--ring` | `#5e69d1` | `#5e6ad2` | Outline ring |
| `--destructive` | `#eb5757` | `#c8312b` | Clear-X hover |

Radius: `--radius` = 8px; `rounded-md` = 8px, `rounded-sm` = 6px, `rounded-full` = 9999px.

Typography: Inter / `--font-sans`; weight-510 (`font-[510]`) is the signature display weight.

---

## Component A — ProjectPicker (edit-view right panel)

### Location

Inserted at the bottom of the `<aside>` in `edit-view.tsx:306` (between the variable preview block and `<BacklinksPanel>`), separated by a `border-t border-border pt-3` divider.

### Section label

`text-[10px] text-muted-foreground font-[600] uppercase tracking-[0.06em]`

Text: "Project" — matches the existing "Variables (N)" / "Preview" label cadence.

### Trigger button anatomy

```
[ FolderIcon  Set project…                 ChevronDownIcon ]   ← default (no project)
[ ● ProjectName                                          ✕ ]   ← project set
```

#### Default state (no project)

| Property | Token / class |
|---|---|
| Height | `h-[30px]` |
| Padding | `px-2` |
| Radius | `rounded-sm` (6px) |
| Border | `border border-transparent` |
| Background | `bg-transparent` |
| Text | `text-[12px] text-muted-foreground` |
| Leading icon | `FolderIcon size-3 opacity-50 text-muted-foreground` |
| Trailing | `ChevronDownIcon size-3 ml-auto opacity-40` |
| Width | `w-full flex items-center gap-1.5` |

Hover: `border-border/80 bg-accent/50 text-foreground` — 150ms ease-out

Focus (keyboard): `border-primary ring-[3px] ring-primary/25 outline-none`

Disabled (mutation pending): `opacity-50 pointer-events-none cursor-not-allowed`

#### Set state (project assigned)

| Property | Token / class |
|---|---|
| Border | `border border-border` |
| Background | `bg-accent/30` |
| Text | `text-[12px] text-foreground` |
| Leading element | Project color dot — `inline-block w-[7px] h-[7px] rounded-full` + `style={{ background: tagColor(projectId) }}` |
| Trailing | Clear ✕ button (see below) |

Hover: `border-border bg-accent/50`

Focus: `border-primary ring-[3px] ring-primary/25 outline-none`

The project dot color is derived from the existing `tagColor()` in `app/apps/dashboard/src/lib/project-color.ts` (the shared util extracted from `list-view.tsx:70–74`). Forge must use the same function in both views — same `project.id` string input → same palette index.

#### Clear ✕ nested button

`size-[14px] rounded-[3px] flex items-center justify-center ml-auto`

| State | Token |
|---|---|
| Default | `text-muted-foreground` |
| Hover | `bg-destructive/12 text-destructive` — 100ms ease-out |
| Focus | `ring-1 ring-destructive/50 outline-none` |

Clicking ✕ calls `setProject(null)` and `e.stopPropagation()` (prevents dropdown from opening).

### Dropdown (Radix Popover, `align="start"`)

`bg-popover border border-border rounded-lg shadow-[0_8px_24px_rgba(0,0,0,.5)] w-[--radix-popover-trigger-width] min-w-[200px] p-1`

Radix data-state animations: `data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0` (consistent with existing `PopoverContent` usage in `list-view.tsx:235`).

#### Search input row

`flex items-center gap-1.5 px-2 py-1.5 border-b border-border/70`

- `SearchIcon size-3 text-muted-foreground`
- `<input>` bare: `bg-transparent border-0 outline-none text-[12px] text-foreground placeholder:text-muted-foreground flex-1`

#### "No project" item (always first, below search)

`flex items-center gap-2 px-2 py-1.5 rounded-[5px] text-[12px] text-muted-foreground`

- Icon: `MinusIcon size-3` or em-dash
- Text: "No project"
- Hover: `bg-accent text-foreground` — 100ms

#### Section header

`border-t border-border/60 my-1` then `px-2 py-1 text-[10px] text-muted-foreground font-[600] uppercase tracking-[0.06em]` — "Projects"

#### Project item

`flex items-center gap-2 px-2 py-1.5 rounded-[5px] text-[13px] text-foreground cursor-pointer`

- Dot: `w-[7px] h-[7px] rounded-full` + `style={{ background: tagColor(projectId) }}`
- Name: `truncate flex-1`
- Selected checkmark: `CheckIcon size-3 ml-auto text-primary` (when `projectId === item.id`)
- Hover: `bg-accent` — 100ms
- Selected background: `bg-primary/[0.08]`

### Empty / Loading / Error states

| State | Treatment |
|---|---|
| **Empty (no projects exist)** | Dropdown body: `px-2 py-3 text-center text-[12px] text-muted-foreground` — "No projects yet". Trigger shows default "Set project…" appearance. |
| **Loading (projects query in-flight)** | Trigger replaced by `<Skeleton className="h-[30px] w-full rounded-md" />` (shadcn Skeleton). Popover not openable while loading. |
| **Error (query failed)** | Dropdown shows single item in `text-destructive text-[12px]` — "Could not load projects". Sonner toast on mutation error. |
| **Save pending** | Trigger: `opacity-50 pointer-events-none` while `updateMut.isPending`. |

### WCAG AA

| Pair | Approx ratio | Threshold | Result |
|---|---|---|---|
| `muted-foreground` (#8a8f98) on `background` (#08090a) — placeholder text | 4.7:1 | 4.5:1 normal | PASS |
| `foreground` (#f7f8f8) on `card` (#0f1011) — set-state text | 17:1 | 4.5:1 | PASS |
| `foreground` (#f7f8f8) on `popover` (#141516) — dropdown items | 15:1 | 4.5:1 | PASS |
| `primary` (#5e6ad2) dot on `background` (#08090a) — graphical element | 3.5:1 | 3:1 UI component | PASS |
| `destructive` (#eb5757) on `popover` (#141516) — clear-X hover | 4.5:1 | 4.5:1 | PASS (borderline) |
| `border` (#23252a) on `background` (#08090a) — hairline | 1.3:1 | 3:1 graphical | WARN: intentional Linear hairline aesthetic, same as existing rows/inputs in codebase |

### Motion

| Interaction | Duration | Easing | Reduced-motion |
|---|---|---|---|
| Trigger hover | 150ms | ease-out | `transition: none` |
| Dropdown open/close (Radix) | 150ms | ease-in-out | Instant snap |
| Dropdown item hover | 100ms | ease-out | `transition: none` |
| Clear ✕ hover | 100ms | ease-out | `transition: none` |

```css
@media (prefers-reduced-motion: reduce) {
  .project-picker-trigger,
  .project-picker-item {
    transition: none;
  }
}
```

### Light + dark parity

All tokens are CSS custom properties with both `:root` (light) and `.dark` definitions in `app/packages/ui/src/index.css:48–154`. No conditional Tailwind classes needed — tokens resolve automatically.

### Forge implementation note

Primitives: `Popover`, `PopoverTrigger`, `PopoverContent` (shadcn, already imported in `list-view.tsx:16–20`). The trigger is a bare `<button>` or `Button variant="ghost"` with a full class override to achieve `h-[30px]`. The search `<input>` is bare (ghost pattern matching `list-view.tsx:440`). Dropdown items are bare `<button>` elements.

---

## Component B — ProjectBadge (list-view rows)

### Location

Appended inside col-1 of the existing prompt row grid (`list-view.tsx:531–541`), after the version badge:

```
<div className="pointer-events-none relative z-10 flex min-w-0 items-center gap-2">
  <span …>{p.name}</span>
  <Badge variant="outline" …>v{p.version}</Badge>
  {p.projectId && (
    <ProjectBadge
      projectId={p.projectId}
      projectName={p.projectName}
      onClick={() => setProjectFilter(p.projectId)}
      active={projectFilter === p.projectId}
    />
  )}
</div>
```

The badge renders only when `p.projectId` is set. Absence is intentional — no empty placeholder.

### Visual anatomy

```
[ ● Nexus Platform ]
```

| Property | Token / class |
|---|---|
| Height | `h-[18px]` — matches existing version badge |
| Padding | `px-[7px]` |
| Radius | `rounded-full` |
| Border | `border border-border` |
| Background | `bg-transparent` |
| Text | `text-[10px] text-muted-foreground font-normal` |
| Dot | `inline-block w-[5px] h-[5px] rounded-full` + `style={{ background: tagColor(projectId) }}` |
| Layout | `flex items-center gap-1` |

Max name width: `max-w-[80px] truncate` — prevents badge from overflowing the name column.

### Interaction states

| State | Border | Background | Text |
|---|---|---|---|
| **Default** | `border-border` | `transparent` | `text-muted-foreground` |
| **Hover** | `border-primary/50` | `bg-primary/[0.06]` | `text-foreground` |
| **Focus** | `ring-1 ring-ring/50 outline-none` | same as hover | `text-foreground` |
| **Active filter** | `border-primary/50` | `bg-primary/[0.08]` | `text-foreground` |

Hover transition: 150ms ease-out. Active filter state is persistent until cleared.

Clicking the badge sets `projectFilter` (local state) to this `projectId`. A second click on the same badge clears the filter. The badge element is a `<button>` (wraps the badge markup) — `pointer-events-none` on the parent col-1 div must not block it (raise badge button to `relative z-10 pointer-events-auto`).

### Filter active pill (list header)

When `projectFilter` is set, render a dismissible pill in the list header toolbar (alongside existing search + sort controls, `list-view.tsx:437–464`):

```
[ ● Nexus Platform  ✕ ]
```

`rounded-full border border-primary/50 bg-primary/[0.08] px-2 h-[22px] text-[11px] text-foreground font-normal flex items-center gap-1.5`

- Dot: `w-[6px] h-[6px] rounded-full` + `style={{ background: tagColor(projectFilter) }}`
- Clear ✕: `size-[12px] ml-0.5 text-muted-foreground hover:text-destructive` — 100ms ease-out

### Empty / Loading / Error states

| State | Treatment |
|---|---|
| **No project on row** | Badge absent. Row renders name + version badge only. Not an error. |
| **Project name unavailable** (project deleted) | `border-border text-muted-foreground italic text-[10px]` — "Unknown project" with no dot. Filter by projectId still works. |
| **List loading** | Badge not shown — skeleton rows replace entire row during load. |
| **List error** | Badge uninvolved — handled by the existing list empty/error pattern. |

### WCAG AA

| Pair | Approx ratio | Threshold | Result |
|---|---|---|---|
| `muted-foreground` (#8a8f98) on canvas (#08090a) — badge text default | 4.7:1 | 4.5:1 normal (10px) | PASS |
| `foreground` (#f7f8f8) on `bg-primary/[0.08]` effective bg (~#09091b) | 17:1 | 4.5:1 | PASS |
| `foreground` (#f7f8f8) on filter pill bg (`bg-primary/[0.08]`) | 17:1 | 4.5:1 | PASS |
| Project dot (5px) | graphical identity; size below 3px threshold scope; color not sole information carrier | N/A | Acceptable |

### Motion

| Interaction | Duration | Easing | Reduced-motion |
|---|---|---|---|
| Badge hover | 150ms | ease-out | `transition: none` |
| Filter pill appear | flex layout shift — no animation needed | — | N/A |

```css
@media (prefers-reduced-motion: reduce) {
  .project-badge { transition: none; }
}
```

### Light + dark parity

All tokens (`--border`, `--primary`, `--muted-foreground`, `--foreground`, `--ring`) resolve via CSS custom properties in both `:root` and `.dark` blocks. No conditional logic needed.

### Forge implementation note

Primitive: `Badge variant="outline"` (shadcn, used throughout `list-view.tsx`) wrapped in a `<button>`. The `pointer-events-none` on col-1's parent div must be overridden on the badge button only (`pointer-events-auto`), matching how `TestPromptPopover` (`list-view.tsx:221`) escapes the `pointer-events-none` zone via `relative z-10`.

---

## Shared utility — `tagColor()`

`app/apps/dashboard/src/lib/project-color.ts` already exists with the `tagColor()` export. Both `ProjectPicker` and `ProjectBadge` must import from that path. Forge must not re-inline the palette — the same hash → same color guarantee depends on a single definition.

---

## Token summary (all tokens are pre-existing in `index.css`)

| Role | Token / Tailwind | No new tokens required |
|---|---|---|
| Picker trigger bg (set) | `bg-accent/30` | yes |
| Picker border transparent | `border-transparent` | yes |
| Picker border default | `border-border` | yes |
| Picker text default | `text-muted-foreground` | yes |
| Picker text set | `text-foreground` | yes |
| Dropdown surface | `bg-popover` | yes |
| Focus ring | `ring-primary/25` | yes |
| Hover lift | `bg-accent` | yes |
| Active filter bg | `bg-primary/[0.08]` | yes |
| Active filter border | `border-primary/50` | yes |
| Clear X hover bg | `bg-destructive/12` | yes |
| Clear X hover color | `text-destructive` | yes |
| Project dot | `tagColor(id)` via inline style | yes |

---

## Verification

1. **`design/design.md` read:** No `design/design.md` at repo root. Design contract drawn from `app/packages/ui/src/index.css` (read in full this session) — confirmed dark-native, Linear-derived palette, lavender (`--primary`) scarce, hairline `--border`, Inter font with cv01/ss03, weight-510, negative tracking on headings, Radix primitives.
2. **Empty / loading / error states:** Defined for both components — 4 states for ProjectPicker, 4 for ProjectBadge.
3. **Light + dark parity:** Both token columns documented per component. All tokens resolve via CSS custom properties — no conditional logic needed.
4. **WCAG AA:** Ratios cited for all new color pairs across both components. One hairline WARN noted as intentional prior art.
5. **Motion:** `prefers-reduced-motion` CSS override block specified for all animated properties.

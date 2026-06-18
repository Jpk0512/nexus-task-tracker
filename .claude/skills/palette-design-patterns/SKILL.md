---
name: palette-design-patterns
description: Canonical design tokens, component visual patterns, mockup library index, and light/dark parity pairs for this project's design system. Preloaded into Palette — also useful when reviewing Palette output or briefing Forge on visual requirements.
---

# Palette Design Patterns

Fast-reference for the `design/` tree. Palette reads `design/design.md` as the binding contract; this skill surfaces the frequently-needed tables so you can spec without re-reading every token file.

---

## Token table (canonical)

All values from `design/tokens/design-tokens.ts`. Tailwind class aliases are in `design/tokens/tailwind-preset.ts`.

### Color tokens

| Token name (JS key) | Tailwind alias | Value | Role |
|---|---|---|---|
| `bgApp` | `bg-ds-app` | `#f6f7f9` | Page shell background |
| `bgSurface` | `bg-ds-surface` | `#ffffff` | Card / panel surface |
| `bgSidebar` | `bg-ds-sidebar` | `#0f1419` | Left navigation |
| `textPrimary` | `text-ds-primary` | `#0f1419` | Default body text |
| `textSecondary` | `text-ds-secondary` | `#5c6570` | Secondary / helper text |
| `textMuted` | `text-ds-muted` | `#8a9299` | Captions, placeholders |
| `textStrong` | `text-ds-strong` | `#080b0f` | Page titles, headings |
| `borderSubtle` | `border-ds-border` | `#e6e8eb` | Default card / input border |
| `accent` | `text-ds-accent` / `bg-ds-accent` | `#2563eb` | Links, chips, chart series |
| `accentSoft` | `bg-ds-accent-soft` | `#eff6ff` | Active chip background |
| `filterPanelBorder` | `border-ds-filter-border` | `#d9e3ec` | Filter panel outer border |
| `filterInputBorder` | — | `#c8d4df` | Filter input border |
| `filterLabel` | `text-ds-filter-label` | `#64748b` | Filter field labels |
| `primaryTeal` | `bg-ds-teal` / `text-ds-teal` | `#0f766e` | Primary action buttons |
| `kpiLabel` | — | `#8b939e` | KPI metric label |
| `chartGrid` | — | `#eef2f7` | Chart gridlines |
| `chartAxisTick` | — | `#8a9299` | Chart axis tick labels |
| `drawerBackdrop` | — | `rgba(15,23,42,0.42)` | Drawer / modal overlay |
| `badgeCategoryText` | — | `#1d4ed8` | Category badge text |
| `badgeSubcategoryText` | — | `#047857` | Subcategory badge text |

### Radius tokens

| Key | Value | Where used |
|---|---|---|
| `md` | `10px` | `rounded-ds` (inputs, cards default) |
| `lg` | `14px` | Larger cards |
| `xl` | `16px` | Filter panel (`rounded-ds-xl`) |
| `2xl` | `18px` | Drawer panel left edge |
| `full` | `9999px` | Chips, badges, pill buttons |

### Shadow tokens

| Key | Value | Where used |
|---|---|---|
| `card` | `0 1px 2px rgba(15,20,25,.05), 0 6px 20px rgba(15,20,25,.06)` | Cards |
| `filterPanel` | `0 10px 24px rgba(15,23,42,.06)` | Filter panel |
| `drawer` | `-16px 0 40px rgba(15,23,42,.18)` | Drawer panel |

### Spacing scale

| Step | px | Tailwind |
|---|---|---|
| 1 | 4px | `gap-1`, `p-1` |
| 2 | 8px | `gap-2`, `p-2` |
| 3 | 12px | `gap-3`, `p-3` |
| 4 | 16px | `gap-4`, `p-4` |
| 5 | 24px | `gap-6`, `mb-6` |
| 6 | 32px | `gap-8`, `p-8` |

### Typography scale

| Role | Size | Weight | Token/class |
|---|---|---|---|
| Page title | 22px | bold | `text-[22px] font-bold tracking-[-0.025em] text-ds-strong` |
| Section title | 18px | semibold | `text-lg font-semibold text-ds-strong` |
| Body | 15px | regular | `text-ds-body font-sans` |
| Table cell | 13px | regular | — |
| Table header | 11px | uppercase, tracked | `text-xs text-ds-muted uppercase tracking-wide` |
| KPI label | 10px | uppercase | `MetricTile` built-in |
| Filter label | small caps | extrabold uppercase | `text-ds-filter-label` |

---

## Light + dark token pairs

The current app is light-mode only. All specs must include a dark surface mapping for future-proofing.

| Role | Light value | Dark surface (spec target) |
|---|---|---|
| Page background | `#f6f7f9` | `#0d1117` |
| Card surface | `#ffffff` | `#161b22` |
| Primary text | `#0f1419` | `#e6edf3` |
| Secondary text | `#5c6570` | `#8b949e` |
| Muted text | `#8a9299` | `#6e7681` |
| Border subtle | `#e6e8eb` | `#30363d` |
| Accent blue | `#2563eb` | `#58a6ff` |
| Primary teal | `#0f766e` | `#3fb950` |

> These dark-mode values are spec targets, not yet implemented in `tailwind-preset.ts`. When authoring a component spec, note both columns so Forge can wire the dark: variant without re-reading the spec.

---

## Common component patterns

### Card

- Primitive: `design/components/Card.tsx`
- Surface: `bg-ds-surface`, `rounded-ds`, `border border-ds-border`, `shadow-ds-card`
- Padding: via `padding` prop or explicit `p-4` / `p-6`
- Empty state: single centered `<p className="text-ds-muted text-sm">No data</p>` inside card body
- Loading state: skeleton shimmer bars replacing content (Forge uses `animate-pulse` divs)
- Error state: muted error copy + optional retry button (secondary variant)

### Button

- Primitive: `design/components/Button.tsx`
- `primary`: teal (`bg-ds-teal`), bold white label, `rounded-xl`, min-height ~40–44px
- `secondary`: white background, `border-ds-border`, `text-ds-primary`
- `ghost`: `bg-slate-50` family
- Focus ring: `0 0 0 4px rgba(37,99,235,0.12)` — do not remove
- Disabled: `opacity-50 cursor-not-allowed` — no color change
- Motion: `transition-colors duration-150`; `prefers-reduced-motion: no transition`

### Table

- Primitive: `design/components/DataTable.tsx`
- Outer chrome: `DataTable` (border, shadow, optional title row)
- Inner: `DataTableTable` (sticky header, cell rules)
- Header padding: `p-[14px]`; cell padding: `px-[14px] py-2.5`
- Header text: `text-xs uppercase tracking-wide text-ds-muted`
- Empty state: single `<tr>` with `colSpan` full width, centered muted copy
- Loading state: skeleton rows (3–5 rows of shimmer)
- Error state: single row with error copy + retry link

### Filter panel

- Primitives: `FilterPanel`, `FilterPanelGroup`, `FilterPanelActions` from `design/components/FilterPanel.tsx`
- Outer: gradient fill, `rounded-ds-xl` (16px), `border-ds-filter-border`, `shadow-ds-filter-panel`
- Padding: `p-4` outer; `gap-3` between groups
- Labels: `text-ds-filter-label`, extrabold uppercase, small size
- Primary action: `Button variant="primary"` (teal)
- Reset: `Button variant="secondary"` or `ghost`
- Chip (inactive): slate border + light bg
- Chip (active): `border-ds-accent` + `bg-ds-accent-soft` + blue text

### KPI / metric tile

- Primitive: `design/components/MetricTile.tsx`
- Variants: default (number + label) and `insight` (left accent + soft blue gradient)
- Value font: `clamp(21px, 1.9vw, 26px)`, bold, `text-ds-strong`
- Label: 10px uppercase, `kpiLabel` color
- Caption: 11px, `text-ds-muted`
- Empty state: `—` dash in value slot, muted label
- Loading state: shimmer block sized to match value height

### Badge

- Primitive: `design/components/Badge.tsx`
- `tone="category"`: blue bg/border/text (`badgeCategory*` tokens)
- `tone="subcategory"`: green bg/border/text (`badgeSubcategory*` tokens)
- `tone="neutral"`: slate family
- Do not invent new badge colors — extend tones in Badge.tsx only

### Drawer

- Primitive: `design/components/Drawer.tsx`
- Backdrop: `rgba(15,23,42,0.42)`, click to close
- Panel: white, `max-w-[1040px]`, `rounded-l-[18px]`, `border-ds-filter-border`, `shadow-ds-drawer`
- Header: title + optional subtitle + `DrawerCloseButton` in one flex row
- Empty state: muted centered copy in panel body
- Loading state: skeleton in panel body
- Error state: error copy + retry action in panel body

### Chart

- Primitives: `ChartCard`, `ChartCardEmpty`, `LineChart`, `BarChart` from `design/components/`
- Grid stroke: `#eef2f7`; axis ticks: `#8a9299` / `#5f6c7b`
- Series stroke width: 2–2.5px
- Do NOT import from `recharts` directly — always use wrappers
- Empty state: `ChartCardEmpty` component
- Loading state: shimmer block at chart height
- Error state: `ChartCardEmpty` with error copy

---

## Mockup library index

| File | Content | Key patterns |
|---|---|---|
| `docs/ui-mockups/workbook-discovery.html` | Workbook discovery page (52K) | Filter rail, workbook card grid, chip selectors, table header chrome |
| `docs/ui-mockups/workbook-discovery-v2.html` | Workbook discovery v2 (89K) | Refined filter panel, drawer detail view, badge usage in rows, KPI strip |

When citing either file, include line range. Example: `workbook-discovery-v2.html:L440–L512 — drawer panel layout`.

---

## Interaction-state checklist

Every component spec MUST answer all of these before Palette calls it done:

- [ ] **Default** — resting appearance
- [ ] **Hover** — color / shadow shift (subtle; do not overdo)
- [ ] **Focus** — blue ring `0 0 0 4px rgba(37,99,235,0.12)`; keyboard-accessible
- [ ] **Active / pressed** — slight scale or darken
- [ ] **Disabled** — `opacity-50 cursor-not-allowed`; no interactive styles
- [ ] **Loading** — shimmer or spinner; element not interactive
- [ ] **Empty** — zero-data state; not an error, just no content yet
- [ ] **Error** — destructive copy + optional retry; never silent
- [ ] **Dark mode** — token mapping for all of the above

---

## WCAG AA contrast thresholds

| Text size | Minimum ratio | Notes |
|---|---|---|
| Normal text (<18px or <14px bold) | 4.5:1 | Body, labels, badges |
| Large text (≥18px or ≥14px bold) | 3:1 | Section titles, KPI values |
| UI components / graphical | 3:1 | Button borders, input borders, chart lines |

Always state the approximate ratio when proposing a new color pair. Use the `textPrimary` (#0f1419) on `bgApp` (#f6f7f9) pair as the baseline (ratio ~17:1).

---

## Motion budget

| Use | Duration | Easing | Reduced-motion fallback |
|---|---|---|---|
| Color / opacity transitions | 150ms | `ease-out` | `transition: none` |
| Height / layout expand | 200ms | `ease-in-out` | Instant snap |
| Drawer slide | 250ms | `cubic-bezier(0.4,0,0.2,1)` | Instant snap |
| Skeleton shimmer | continuous | `animate-pulse` | Static muted bg |
| Chart mount animation | 400ms | `ease-out` | Skip animation |

Always wrap motion specs with: `@media (prefers-reduced-motion: reduce) { … }` note in the spec.

---

## Pairing rules

- Palette authors the spec → Forge implements TypeScript/React
- Design conflicts with design.md → surface via `## NEXUS:NEEDS-DECISION` before speccing
- New token needed beyond existing set → surface via `## NEXUS:NEEDS-DECISION` (Atlas-equivalent gate for design tokens)
- Verification of implemented output → Lens's job, not Palette's

---

## Mandatory Discipline (2026-05-13)

### Token integrity
- Every CSS custom property referenced in a Tailwind arbitrary value
  (`bg-[rgb(var(--color-foo))]`) MUST be defined in `app/app/globals.css` (light
  + dark blocks). Use a token lint pass or codebase_search before introducing.
  See `--color-surface` regression from 2026-05-13.

### Visual gate
- All Palette designs require a screenshot or visual mockup in the response —
  not just CSS specs.

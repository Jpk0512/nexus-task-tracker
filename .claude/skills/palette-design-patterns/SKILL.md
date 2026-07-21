---
name: palette-design-patterns
description: Discover a project's REAL design-token and component vocabulary before speccing, then structure specs (interaction states, contrast, motion) the way Palette needs them. Preloaded into Palette — also useful when reviewing Palette output or briefing a UI-writing persona on visual requirements. Ships one worked EXAMPLE token system (Tailwind `ds-*`); it is illustrative only — never assume it is THIS project's vocabulary.
---

# Palette Design Patterns

## When this fires

Any Palette spec, or any review of Palette output / brief to a UI-writing persona
on visual requirements. This skill does NOT ship a canonical token set for every
project — each installed project has its own real stylesheet/component vocabulary
(Tailwind + `ds-*` custom properties, plain CSS custom properties, CSS Modules,
styled-components, a dark-only palette, …). Discover it before speccing anything.

---

## Discover the project's real vocabulary FIRST (mandatory, before any spec)

Never assume `design/tokens/design-tokens.ts`, a `ds-*` token prefix, Tailwind, or
any specific component file exists — those are one project's choices, not a Nexus
convention. A project can be dark-mode-only, plain CSS (`gg-*` custom properties,
no Tailwind), or something else entirely.

1. Find the project's real design/token source: `grep -rn '^\s*--[a-zA-Z-]' <css dirs>`
   for CSS custom properties, or `find <repo> -iname '*.css' -o -iname 'tokens*' -o
   -iname 'theme*'` for a tokens file, or `codebase_search "design tokens" /
   "theme"` if SocratiCode is indexed.
2. Find the project's real component primitives the same way — `codebase_symbols`
   / grep for `Card`, `Button`, `Table`, `Panel`, `Drawer`, `Chart`, `Badge`
   equivalents; do not assume any of the file names below exist.
3. Confirm light vs. dark: read the actual stylesheet's `:root` / `[data-theme]`
   blocks (or equivalent) rather than assuming a light-first design.
4. Spec using the vocabulary you just found — token names, file paths, and prefix
   conventions in every spec MUST be ones you verified exist in THIS project.

If discovery finds nothing (a genuinely new project with no design system yet),
say so explicitly in the spec rather than inventing paths from the example system
below.

---

## Interaction-state checklist (stack-agnostic)

Every component spec MUST answer all of these before Palette calls it done:

- [ ] **Default** — resting appearance
- [ ] **Hover** — color / shadow shift (subtle; do not overdo)
- [ ] **Focus** — visible, keyboard-accessible ring/outline using the project's real focus-ring token
- [ ] **Active / pressed** — slight scale or darken
- [ ] **Disabled** — reduced opacity + no interactive styles
- [ ] **Loading** — shimmer or spinner; element not interactive
- [ ] **Empty** — zero-data state; not an error, just no content yet
- [ ] **Error** — destructive copy + optional retry; never silent
- [ ] **Dark mode** — token mapping for all of the above (verify the project actually needs a light AND dark mapping — a dark-only project needs no light fallback)

---

## WCAG AA contrast thresholds

| Text size | Minimum ratio | Notes |
|---|---|---|
| Normal text (<18px or <14px bold) | 4.5:1 | Body, labels, badges |
| Large text (≥18px or ≥14px bold) | 3:1 | Section titles, KPI values |
| UI components / graphical | 3:1 | Button borders, input borders, chart lines |

Always state the approximate ratio when proposing a new color pair, computed
against the project's own real background/text token values (discovered above),
never against the example system's values.

---

## Motion budget (stack-agnostic defaults)

| Use | Duration | Easing | Reduced-motion fallback |
|---|---|---|---|
| Color / opacity transitions | 150ms | `ease-out` | `transition: none` |
| Height / layout expand | 200ms | `ease-in-out` | Instant snap |
| Drawer / modal slide | 250ms | `cubic-bezier(0.4,0,0.2,1)` | Instant snap |
| Skeleton shimmer | continuous | `animate-pulse` (or equivalent) | Static muted bg |
| Chart mount animation | 400ms | `ease-out` | Skip animation |

Always wrap motion specs with a `prefers-reduced-motion: reduce` note in the spec.

---

## Pairing rules

- Palette authors the spec → the project's UI-writing persona implements it
- Design conflicts with the project's own design doc → surface via `## NEXUS:NEEDS-DECISION` before speccing
- New token needed beyond the project's existing real set → surface via `## NEXUS:NEEDS-DECISION` (schema-owner-equivalent gate for design tokens)
- Verification of implemented output → Lens's job, not Palette's

---

## Mandatory Discipline

### Token integrity
- Every token/custom-property referenced in a spec MUST already be defined in the
  project's real stylesheet (discovered above) — never invent a token name from
  the example system below. Grep or `codebase_search` before introducing one.

### Visual gate
- All Palette designs require a screenshot or visual mockup in the response —
  not just CSS specs.

---

## EXAMPLE SYSTEM — Tailwind `ds-*` tokens (illustrative only)

The tables below are ONE worked example from a prior light-mode-first
Tailwind/React project — they are NOT this project's tokens, and the file paths
(`design/tokens/design-tokens.ts`, `design/components/Card.tsx`,
`design/components/FilterPanel.tsx`, etc.) are that project's paths, not a Nexus
convention. Use this section only as a reference for how to STRUCTURE a token
table / component-pattern list once you've discovered the real one (see
Discovery, above) — never cite these paths or values as if they exist in the
current project.

### Color tokens (example — from `design/tokens/design-tokens.ts` in the source project this example was drawn from; verify a matching path exists here before citing it)

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

### Radius tokens (example)

| Key | Value | Where used |
|---|---|---|
| `md` | `10px` | `rounded-ds` (inputs, cards default) |
| `lg` | `14px` | Larger cards |
| `xl` | `16px` | Filter panel (`rounded-ds-xl`) |
| `2xl` | `18px` | Drawer panel left edge |
| `full` | `9999px` | Chips, badges, pill buttons |

### Shadow tokens (example)

| Key | Value | Where used |
|---|---|---|
| `card` | `0 1px 2px rgba(15,20,25,.05), 0 6px 20px rgba(15,20,25,.06)` | Cards |
| `filterPanel` | `0 10px 24px rgba(15,23,42,.06)` | Filter panel |
| `drawer` | `-16px 0 40px rgba(15,23,42,.18)` | Drawer panel |

### Spacing scale (example)

| Step | px | Tailwind |
|---|---|---|
| 1 | 4px | `gap-1`, `p-1` |
| 2 | 8px | `gap-2`, `p-2` |
| 3 | 12px | `gap-3`, `p-3` |
| 4 | 16px | `gap-4`, `p-4` |
| 5 | 24px | `gap-6`, `mb-6` |
| 6 | 32px | `gap-8`, `p-8` |

### Typography scale (example)

| Role | Size | Weight | Token/class |
|---|---|---|---|
| Page title | 22px | bold | `text-[22px] font-bold tracking-[-0.025em] text-ds-strong` |
| Section title | 18px | semibold | `text-lg font-semibold text-ds-strong` |
| Body | 15px | regular | `text-ds-body font-sans` |
| Table cell | 13px | regular | — |
| Table header | 11px | uppercase, tracked | `text-xs text-ds-muted uppercase tracking-wide` |
| KPI label | 10px | uppercase | `MetricTile` built-in |
| Filter label | small caps | extrabold uppercase | `text-ds-filter-label` |

### Light + dark token pairs (example)

The example project this table was drawn from was light-mode only, so it kept a
dark-surface spec-target column for future-proofing. A dark-only project (or an
already-dual-mode one) needs the mapping the other direction, or none at all —
check the real project before assuming this shape applies.

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

### Common component patterns (example)

The primitive paths below (`design/components/*.tsx`) are the source project's
paths — verify the equivalent components' real locations in the current project
before citing them in a spec.

**Card** — `design/components/Card.tsx` in the source project. Surface:
`bg-ds-surface`, `rounded-ds`, `border border-ds-border`, `shadow-ds-card`.
Padding via `padding` prop or explicit `p-4`/`p-6`. Empty: single centered muted
`<p>`. Loading: skeleton shimmer bars. Error: muted error copy + optional retry.

**Button** — `design/components/Button.tsx`. `primary`: teal, bold white label,
`rounded-xl`. `secondary`: white bg, bordered. `ghost`: subtle bg family. Focus
ring `0 0 0 4px rgba(37,99,235,0.12)` — do not remove. Disabled: reduced opacity,
no color change. Motion: `transition-colors duration-150`.

**Table** — `design/components/DataTable.tsx`. Sticky header, `p-[14px]` header
padding, `px-[14px] py-2.5` cell padding. Empty: single full-width `<tr>` with
centered muted copy. Loading: 3–5 skeleton rows. Error: single row, copy + retry.

**Filter panel** — `FilterPanel`/`FilterPanelGroup`/`FilterPanelActions` from
`design/components/FilterPanel.tsx`. Gradient outer fill, `p-4` outer / `gap-3`
between groups. Primary action `primary` variant; reset `secondary`/`ghost`.
Inactive chip: subtle border + light bg; active chip: accent border + soft bg.

**KPI / metric tile** — `design/components/MetricTile.tsx`. Default (number +
label) and `insight` (left accent + soft gradient) variants. Empty: em-dash in
value slot. Loading: shimmer block sized to value height.

**Badge** — `design/components/Badge.tsx`. Tone-based (`category`/`subcategory`/
`neutral`) bg/border/text triples. Extend tones in the component, never invent a
one-off color at the call site.

**Drawer** — `design/components/Drawer.tsx`. Backdrop overlay, click to close.
Header: title + optional subtitle + close button in one row. Empty/loading/error
states in the panel body, same pattern as Card.

**Chart** — `ChartCard`/`ChartCardEmpty`/`LineChart`/`BarChart` from
`design/components/`. Always wrap the underlying charting library — never import
it directly in a spec or implementation. Empty/error both route through
`ChartCardEmpty`.

### Mockup library index (example)

A project MAY maintain an HTML mockup library (e.g. under `docs/ui-mockups/`) —
verify it exists before citing a path. When citing a mockup file, include a line
range: `<file>.html:L440–L512 — drawer panel layout`.

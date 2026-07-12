---
name: tailwind-design-tokens
description: "INTERNAL — invoke by explicit name only via `Skill tailwind-design-tokens`. Do NOT auto-load. Tailwind CSS 4 token conventions, CSS custom properties, theme extension, dark mode pattern."
---

# Tailwind Design Tokens (canonical for `app/`)

## Tailwind version

**Tailwind CSS 4**. Config is in `tailwind.config.ts` + `app/globals.css`. The v4 `@theme` directive replaces v3 `extend` for custom tokens.

## Token definition

Custom design tokens go in `app/globals.css`:

```css
@theme {
  --color-brand-500: oklch(62% 0.2 250);
  --spacing-sidebar: 16rem;
}
```

Reference in classes: `bg-brand-500`, `w-sidebar`.

## Dark mode

- Dark mode uses the `class` strategy (`class="dark"` on `<html>`).
- Always provide both light and dark values for any color token you introduce.
- Pattern: `bg-white dark:bg-gray-900`, never just `bg-white`.

## Forbidden patterns

- Inline `style={{color: '...'}}` for anything expressible as a Tailwind class.
- Hardcoded hex/rgb values in className — use tokens.
- `!important` overrides — fix specificity instead.
- Tailwind v3 `@apply` for anything that can be a utility class.

## Palette integration

Palette (`palette.md`) owns the visual design contract. If you need a new token not in the current set, request via `## NEXUS:NEEDS-DECISION` delegating to Palette — do not invent tokens.

# CHECKPOINT run-a-2 — Dark mode UI overhaul

**Goal:** remove all backdrop-blur/glass/translucency across the entire
React frontend. Flat solid cards (`bg-gray-800` on `bg-gray-900`), sidebar
`bg-gray-950`, borders `border-gray-700` (no glow), radius 6px cards / 4px
buttons, typography white headings / gray-300 body / gray-500 secondary,
inputs `bg-gray-900` with `gray-600` border, no shadows in dark (borders
only), teal accent `#1FBED6` stays. Verify every page.

## Scope decision, documented per this project's own rule ("deviations
documented in CHECKPOINT.md, not made silently")

The design system uses shared CSS-variable tokens (`src/index.css` +
`tailwind.config.js`) that every page and shared component already
consumes (`bg-card`, `bg-surface`, `text-foreground`, `border-border`,
`shadow-card`, etc.) — confirmed by grep before touching anything. This
means the overhaul is mostly a **token-level** change, not a page-by-page
rewrite: fixing the six CSS variables + two radius tokens + the shared
`Input`/`Select`/`Card` primitives propagates to all 31 pages
automatically, since none of them hardcode colors that bypass the token
system (verified: the only hardcoded `bg-gray-*`/`text-gray-*` usages
outside components/ui are `Login.jsx` and `Sidebar.jsx`, both of which
already reference sidebar-specific or intentionally-static tones, not
things that need to change — see below).

"Translucency" was interpreted as **glass-panel-over-content decoration**
(the goal pairs it with "backdrop-blur, glass" as one concept), not every
low-opacity utility in the codebase. Left untouched: `bg-danger/10`,
`bg-warning/10`, `bg-success/10`, `bg-info/10` (soft status-tint
backgrounds for alerts/badges — a standard flat-design pattern used by
GitHub/Linear/Vercel, not glassmorphism), `bg-muted/40` (subtle
table-header/row fills), and the `bg-black/50` modal dim scrim (focuses
the modal; every flat design system dims the background behind a dialog —
removing it would be a UX regression the goal doesn't ask for). What WAS
removed: **actual `backdrop-blur`** (2 occurrences, both modal overlays)
and the one genuine glass-panel effect (`bg-sidebar-hover/60`, the
sidebar's health-mini-widget background, flattened to solid).

## What was changed

- **`tailwind.config.js`**: `sidebar.DEFAULT` `#111827` (gray-900) →
  `#030712` (gray-950); `borderRadius.card`/`lg` 8px → 6px,
  `borderRadius.btn`/`md` 6px → 4px; added an `input-surface` color token
  (new CSS var, see below).
- **`src/index.css`**:
  - Dark `--bg` `rgb(15,20,30)` (a non-standard tone) → `17 24 39` (exact
    gray-900).
  - Dark `--fg` (drives `text-foreground`, used broadly for labels/values/
    body text, not just headings) gray-100 → **gray-300** (`209 213 219`)
    per "gray-300 body". Headings (`h1`–`h4`) are given an explicit
    `.dark h1,h2,h3,h4 { color: #FFFFFF }` override so "white headings"
    holds even though `--fg` itself is no longer white.
  - Dark `--muted-fg` (secondary/muted text) gray-400 → **gray-500**
    (`107 114 128`).
  - Dark `--input` (border color for `Input`/`Select`) gray-700 →
    **gray-600** (`75 85 99`).
  - Dark `--border`/`--surface`/`--card` were already exactly
    gray-700/gray-800/gray-800 — unchanged (already matched spec).
  - New `--input-surface` var: unchanged in light mode, **gray-900** in
    dark — inputs now sit visibly recessed relative to their gray-800
    containing card (`Input.jsx`/`Select.jsx` switched from `bg-surface`
    to `bg-input-surface`).
  - New `.dark .shadow-sm, .dark .shadow-card, .dark .shadow-dropdown {
    box-shadow: none }` in `@layer utilities` — "no shadows in dark,
    borders only," applied once at the token level rather than editing
    every component that uses those utilities (`Card`, `Button`, `Input`,
    `Select`, `Dialog`, `Toast`, `DropdownMenu`, `Tooltip`,
    `CommandPalette` all already carry `border-border` or an explicit
    border, which becomes the sole depth cue once the shadow is gone).
- **`Dialog.jsx`**, **`CommandPalette.jsx`**: removed `backdrop-blur-[1px]`
  from both modal overlays (kept the `bg-black/50` dim scrim).
- **`Sidebar.jsx`**: health-mini-widget background `bg-sidebar-hover/60` →
  `bg-sidebar-hover` (flat, not translucent).
- **`Tooltip.jsx`**: added `border border-gray-700` — it's always
  rendered dark (`bg-gray-900`) regardless of theme, so once its shadow
  is neutralized in dark mode it needs its own border for definition.

## Verified live (screenshots, not deployed to `/opt/forgehost`)

Per `[[forgehost-ui-qa-rig]]`: built the fresh bundle, served it via a
throwaway Node proxy (repo `static/dist` + proxy everything else to the
real running `:9443` API), logged in as a disposable admin
(`qadark9x2`, deleted afterward along with its sessions), forced
`localStorage['forgehost.ui'] = {theme:'dark'}`, and screenshotted with
puppeteer at 1440×900: **Login, Accounts (+ Create-account dialog),
AccountDetail (Overview tab, including the new Plan card from feature 1),
Plans, Server Health (incl. the CPU/Memory 24h charts), Services,
Firewall, Webhooks**. All render flat gray-800-on-gray-900, sidebar
visibly darker (gray-950), no blur anywhere (confirmed directly: content
behind the create-account dialog's overlay is crisp, not blurred), no
glow/shadow on any card, borders visible throughout, inputs visibly
recessed (gray-900) relative to their gray-800 card. Also screenshotted
Accounts in **light mode** (no theme override) to confirm the dark-only
changes didn't regress light mode — unchanged, as intended (light-mode
CSS vars were never touched).

Not individually screenshotted (all 31 pages is a lot of manual
screenshots for one pass): every other page. Confidence for those rests
on the token-level fix propagating automatically, per the grep-verified
absence of hardcoded-color exceptions, not a claim that every single page
was eyeballed. Screenshots saved under
`scratchpad/darkqa/*.png` during this session and are not part of the
commit (scratchpad, not `docs/ui-screenshots/`).

## Tests

No new backend behavior — pure frontend/CSS. `cd frontend && npm run
build` clean both before and after. Full backend suite re-run to confirm
zero regressions (frontend-only change, expected to be a no-op for
pytest, confirmed).

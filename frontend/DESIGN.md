# Boron panel design system

Usability comes first in every theme: any tool must be easy to find from any
page. Customers coming from other hosting panels should feel at home, but the
artwork and names are our own. A theme may follow a familiar layout; it must not
use another product's logo, icons or name.

## Themes

The server administrator chooses one theme for the whole server (Panel theme
page, stored in `branding_settings.panel_theme`). Nobody else can change it.
Themes differ in everything a person sees, not only the home screen.

| Theme | id | Frame | Home | Internal pages | Icons |
|---|---|---|---|---|---|
| Classic | `classic` | Navy side menu with the logo, plain white bar with search, footer on every page | Panels of icon-and-link rows, details on the right | Large thin titles, no cards: sections flat on the page, boxed tabs, blue table heads between dark rules, bold labels, banded notices | Phosphor duotone as dark outlines with an orange accent |
| Grid | `grid` | Blue header with a white logo, a Menu button opening a panel of every tool, a search card, no side menu | Groups under solid blue title bars, borderless icon tiles, a filter, statistics on the right | Plain title over a rule, bold section titles with rules between them, outlined active tab, boxed striped tables, capital action buttons | Flat Color pictograms for tools, Remix fill elsewhere |
| Modern | `modern` | Light side menu of hubs, search first in the bar | Greeting, search, at-a-glance numbers, common tools, all tools | Rounded cards, pill tabs, airy tables. Has a dark mode | lucide |

Classic and Grid were matched against the running interfaces of the panels they
follow, using measured colours, sizes and spacing. Keep that closeness when
changing them, with our own logo, icons and names.

How it fits together:

- `src/themes/<id>/` holds `Layout`, the home screen, the icon set and the
  stylesheet. `themes/registry.js` loads only the active theme's chunks.
- The server writes `data-theme` on `<html>` (`api/main.py`), so the first paint
  is already the right theme. `public/theme-init.js` falls back to the cached
  public branding when a page is served without it.
- Pages keep importing icons from `lucide-react`. `vite.config.js` points that
  at `themes/lucide-proxy.gen.js`, which draws from the active set. A new icon
  needs a row in `themes/icon-table.mjs`; `npm run build` refuses a missing one.
- Pages keep using the shared components. Theme stylesheets restyle them, so a
  new page is themed for free. Prefer those components over one-off markup.
- Each theme sets its accent and typeface in `public/appearance-preferences.js`.
  People keep only comfort settings: text size, spacing, motion, and light or
  dark in a theme that has a dark palette.
- Adding a theme: a folder with those parts, a row in `registry.js`, the id in
  `shared/panel_themes.py`, `public/theme-init.js` and the appearance script.

## Brand

- Navy `#0B1F45` comes from the wordmark. The wordmark is navy on light chrome
  and white on navy chrome; custom branding logos override it.
- Every accent use goes through the `--accent*` / `--link` / `--ring` tokens.
  The script derives them from the theme's accent so they stay readable.

## Tokens (RGB triplets, as today, so Tailwind alpha works)

| Token | Light | Dark |
|---|---|---|
| `--bg` page | `#F4F6FA` | `#0B1120` |
| `--surface` / `--card` | `#FFFFFF` | `#121A2B` |
| `--muted` subtle fill | `#EEF1F6` | `#1A2336` |
| `--border` | `#E1E6EE` | `#243049` |
| `--input` (input border, 3:1 against its surface) | `#8792A4` | `#5B6A86` |
| `--input-surface` | `#FFFFFF` | `#0E1626` |
| `--fg` text | `#0F1B2D` | `#E5EAF2` |
| `--muted-fg` secondary text | `#5A6779` | `#93A1B8` |
| `--chrome-bg` (navy chrome) | `#0B1F45` | `#0A1530` |
| accent | `#2563EB` | `#5B8DEF` (derived for contrast) |
| `--scrim` (dialog and drawer backdrop) | navy at 45% | black at 65% |

Semantic colours: success `#047857`, warning `#B45309`, danger `#B91C1C`, info
`#2563EB`. These are the fills (buttons, bars, dots). Text on the soft tints
uses darker variants so it stays readable: success `#046C4E`, warning
`#9A4608`, info `#1D4ED8`. Each has a soft background tint at about 10% for
badges and alerts (`--warning-soft` is also the license/impersonation banner
band), and a lighter readable variant in dark mode. All text meets WCAG AA (4.5:1
for body, 3:1 for large text and UI components) in both modes and both skins.

Category hues for icon chips (one per tool group, used as a soft tint
background with a darker stroke icon): blue, violet, teal, amber, rose, sky,
emerald, slate, indigo, orange. In dark mode the chips use a low-alpha tint
and a light stroke.

## Shape, type, depth

- Font: Inter (already bundled via `@fontsource/inter`) is the default; the
  Appearance font choice still works. Base size 14px, line height 1.5.
  Page title 22px/600, section title 15px/600, labels 13px/500, table text
  14px. Use tabular numerals for numbers in tables and meters.
- Radius: 10px cards and dialogs, 8px inputs and buttons, 12px icon chips;
  badges, avatars and status dots are fully round.
- Depth: cards are a 1px border plus `0 1px 2px rgb(16 24 40 / 0.05)`. Menus
  and dialogs get a larger soft shadow. In dark mode use borders only, no
  shadows.
- Spacing scale 4/8/12/16/20/24/32. Page gutter 24px desktop, 16px phone.
- Motion: 120–160ms ease-out on hover and focus only; honour reduced motion.

## Components

- **Buttons:** primary (accent fill, white text), secondary (surface, border),
  ghost, danger, and danger-ghost (red text, no fill) for destructive actions
  inside table rows. Disabled buttons use the muted style, not a faded fill. Height 36px default, 32px small. Visible focus ring
  `0 0 0 3px` of accent at 30%.
- **Inputs and selects:** 36px, border `--input`, focus border accent plus
  ring, placeholder in muted-fg.
- **Tables:** header row on `--muted`, 13px/600 muted-fg labels (sentence
  case, not uppercase), 48px rows, a subtle row hover, no zebra stripes.
- **Badges:** pill with a soft tint background and a coloured dot (Active,
  Suspended, Failed, …).
- **Cards:** title row with an optional icon chip, actions on the right, body
  padding 20px.
- **Tool groups (home screen):** a card with a header row (category icon chip,
  title in sentence case, collapse chevron). No solid coloured header bars.
  Evo tiles: 44px chip with a 22px icon, label underneath, about 120px wide,
  hover shows a soft accent background. Paper rows: 32px chip, label to the
  right in link colour, 3 columns desktop, 2 tablet, 1 phone.
- **Right rail (home screen):** "Your account" key/value list and "Resource
  usage" meters (thin 6px bars, colour by threshold: accent below 70%, warning
  from 70%, danger from 90%).
- **Navigation:** the Evo admin sidebar is navy with group labels in 11px/600
  white at 55%, items in white at 80%, and the active item as an accent pill.
  Paper uses a light rail with the active item as an accent tint plus a left
  bar.
- **Login:** a centred card on a navy background with a subtle radial glow,
  the wordmark above the card and the version below.

## Icons

Modern uses `lucide-react` stroke icons (1.75 stroke)
inside the category chips. Third-party product logos (WordPress, Node.js,
Python, Redis, Git) stay as their brand marks, inside a neutral chip. No
decorative dots or badges on tool icons.

## Rules for code

- Style through tokens and shared component classes; no new hard-coded hex
  values in JSX.
- Keep one stylesheet per concern under `src/styles/` (tokens, base, shell,
  components, home, pages). Remove dead and overridden rules instead of
  layering more `!important` overrides.
- Keep the content security policy: no inline scripts, fonts are
  self-hosted.
- Every screen must work at 375px wide without horizontal scrolling.

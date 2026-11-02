# Boron panel design system

Direction: **classic hosting, refined.** Customers coming from cPanel,
DirectAdmin or Plesk should feel at home: a home screen of tools grouped by
category. The drawing is our own and modern: calm surfaces, one brand colour,
consistent icons, generous but efficient spacing. It must not look like a
copy of cPanel's Jupiter or Paper Lantern.

There are two skins. Users pick one under Appearance. Both share every token
and component; they differ only in shell layout and the home screen.

| Skin | id | Layout |
|---|---|---|
| Evo | `evolution` | Light top bar. Admins get a navy left sidebar. The home screen is a **tile grid**: an icon chip above the label. |
| Paper | `paper-lantern` | Navy top bar. The home screen is a **compact list**: a small icon chip beside the label, 3–4 columns. Light rail navigation. |

## Brand

- Navy `#0B1F45` comes from the wordmark. It is used for chrome (Paper top
  bar, Evo admin sidebar) and headings in light mode.
- The wordmark is navy on light chrome and white on navy chrome. The gold
  wordmark is retired as the default; custom branding logos still override it.
- Accent blue `#2563EB` is the default accent for both skins. It is used for
  primary buttons, links, focus rings, the active navigation item and
  selection. Users can still choose another accent (Appearance), so every
  accent use goes through the `--accent*` / `--link` / `--ring` tokens.
  Presets: Blue `#2563EB`, Navy `#1E3A8A`, Teal `#0F766E`, Violet `#6D28D9`,
  Emerald `#047857`, Amber `#B45309`, Rose `#BE123C`, Slate `#334155`.
- Nothing is mustard or gold by default.

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

One icon family across both skins: `lucide-react` stroke icons (1.75 stroke)
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

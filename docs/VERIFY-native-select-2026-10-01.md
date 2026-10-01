# Global native-select arrow fix — 1 October 2026

Installed on the current v3.1.2 server. No release is published.

## Styling contract

`frontend/src/index.css` owns native-select appearance, the mode-specific `--select-chevron`, non-repeating 16px background, centred right placement, ellipsis and the 36px arrow gutter. The gutter intentionally has priority over page padding shorthands. Multiple/size list boxes have no arrow and a 12px right gutter. Disabled controls retain dimming; ordinary focus and invalid-state styling remain.

The shared Select component no longer defines an inline SVG or background placement. Inner-page surfaces, WordPress forms and domain pickers use `background-color` instead of resetting the background longhands. The subdomain select no longer suppresses its keyboard outline. No options, handlers, form payloads, backend code or routes changed. The combined input/select/textarea surface rule renders the same colour for other controls.

The separate API-documentation viewer has its own vendor stylesheet and already uses a non-repeating arrow; that vendor asset is outside the SPA and was not changed.

## Verification

- `npm run build`: passed.
- In the combined `npx playwright test e2e/native-select.spec.js e2e/interiors.spec.js` run, all four interior theme/mode cases passed, covering DNS/database/SSL forms and mobile layout.
- `npx playwright test e2e/native-select.spec.js`: all four theme/mode cases passed. Real shared and plain native controls are covered, including surface/padding overrides, disabled state, keyboard focus, long text, list boxes and value selection. The initial run passed styling assertions but its final locator became ambiguous after adding fixture selects; the locator was corrected and the four cases rerun successfully.
- Live inspection: **40 page/dialog views passed**, comprising Accounts, WordPress, PHP, DNS, Backup Manager, Panel settings and Developer tools, plus WordPress installation, Create job and Add record dialogs, in Evo/Paper light/dark. Configuration writes were blocked; no runtime errors or horizontal page overflow occurred.
- Every native single-select present returned `background-repeat: no-repeat`, `background-size: 16px 16px`, `appearance: none`, a themed image and a reserved arrow gutter. Visible enabled chevrons passed 3:1 against their field surface; the minimum measured ratio was 6.07:1. Accounts and Panel settings use styled Radix controls rather than visible native selects.
- Live WordPress installation screenshots were visually inspected in all four combinations: exactly one right-centred chevron, no tiling or additional OS arrow, and a visible focus outline. WP-CLI's actual WordPress-install and Command native selects were also checked on Developer tools.
- `git diff --check` passed. The deployed frontend entry matches the production build.

Protected live inspection code, computed-style proof and screenshots are under `/root/boron-setup/select-arrow-20261001`; browser logs are there as well. No credentials are included in repository artifacts.

| WordPress install dialog | Light | Dark |
|---|---|---|
| Evo | [Screenshot](/root/boron-setup/select-arrow-20261001/wordpress-install-evolution-light.png) | [Screenshot](/root/boron-setup/select-arrow-20261001/wordpress-install-evolution-dark.png) |
| Paper | [Screenshot](/root/boron-setup/select-arrow-20261001/wordpress-install-paper-lantern-light.png) | [Screenshot](/root/boron-setup/select-arrow-20261001/wordpress-install-paper-lantern-dark.png) |

## Recovery

Only frontend assets were installed. The immediate previous entry is `/root/boron-setup/select-arrow-20261001/index-before-select-fix.html`; previous hashed assets remain available for existing tabs and static rollback. No service restart or database restoration is required.

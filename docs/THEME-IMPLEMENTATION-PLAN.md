# Two-theme implementation plan

The selected references and design analysis are in [THEME-RESEARCH.md](THEME-RESEARCH.md). Target: Evolution **Icons Grid** and cPanel **Paper Lantern Basic**, applied across both Boron roles.

1. Add a validated, persisted `skin` preference independent of light/dark mode. Apply both before first paint, migrate existing preferences, and synchronize across browser tabs.
2. Define theme tokens for accent colors, surfaces, typography, borders, component radii, and application chrome. Reuse current UI primitives so every existing form/table/dialog inherits the skin.
3. Build role-aware tool categories from Boron's actual route inventory. Implement Evolution vertical icon tiles and Paper Lantern horizontal icon links, searchable and collapsible. Show actual account/server statistics with loading/error/empty states.
4. Add the admin home dashboard, redesign the customer dashboard while retaining onboarding/alerts, and adapt the top bar, complete navigation drawer, Paper Lantern rail, and detail-page frame.
5. Add an Appearance page with visual previews and immediate switching; expose the selector in the header and home sidebar. Retain light/dark controls. Preserve active route and unsaved form state during switching.
6. Add browser tests for both roles/themes, persistence, filter/collapse/navigation, appearance switching, dark mode, mobile sizes, and form preservation. Build production assets, run relevant existing regression tests, inspect desktop/mobile screenshots, and fix failures.
7. Back up current assets, deploy the tested build on this development server, and run authenticated live smoke tests. Record evidence and provide the switch location. Leave source changes local; GitHub/update release publication is a later user-authorized step.

Acceptance: both themes visibly match their selected layout families; no missing existing navigation; no session/permission changes; no new horizontal overflow at phone sizes; no broken assets or browser runtime errors in tested flows; persistent first-paint preference; successful build and theme tests; live admin login/health unchanged.

## Completion

All seven steps are complete for the development server. See [THEME-VERIFICATION.md](THEME-VERIFICATION.md) for test results, live verification, screenshots, and the rollback archive. GitHub/update-channel publication remains a separate later step.

# Boron interface themes

Boron includes two complete UI skins:

- **Evolution — Icons Grid:** light, spacious chrome, grouped vertical icon tiles, and a statistics column.
- **Paper Lantern — Classic:** dark slate chrome, a compact navigation rail, horizontal icon-and-label tool groups, and account/statistics panels.

Both skins cover administrator and customer workspaces and share the existing protected routes and backend APIs. Their dark modes retain the corresponding layout. Boron supplies its own branding and icons; the skins are visual adaptations of the researched references.

## Switching

Use **Choose theme** (palette icon) in the top bar, the **Theme** selector on the dashboard, or **Appearance** in the navigation/account menu. The Appearance page shows previews and light/dark controls. Theme changes apply immediately without leaving the current page or clearing form inputs.

Preferences are saved in this browser under `boron.ui`, alongside the existing light/dark preference. They are not a server-wide administrator setting or a per-account database field. If browser storage is blocked, the choice applies to the current session. The login page also offers the theme selector. Other open tabs synchronize the selected skin and mode.

Administrators now land on `/app/overview`; customers keep `/app/dashboard`. All prior feature URLs continue to work. The home tool directory supports filtering and collapse/expand, with group collapse state saved separately by role and theme. **Home**, the navigation drawer, and Ctrl/Cmd+K provide navigation from detail pages. The Paper Lantern rail provides additional shortcuts.

## Development

Use Node.js 20 or newer for browser testing; this server has Node 22 at `/opt/boron-nodejs/22/bin`. `frontend/.nvmrc` selects 22 for nvm users.

```bash
cd /root/boronpanel
export PATH=/opt/boron-nodejs/22/bin:$PATH
cd frontend
npm ci
npx playwright install --with-deps chromium
npm run build
npm run test:themes
```

The browser suite serves the real production build locally and mocks API responses for deterministic role, usage, empty/error, and navigation coverage. It does not create customer accounts or change hosting data. Screenshots and failure traces are generated under `frontend/test-results/` and the HTML report under `frontend/playwright-report/`; these are excluded from Git and deployment.

Live smoke checks use credentials from a separate file:

```bash
BORON_CREDENTIALS_FILE=/root/boron-setup/credentials.json \
BORON_TEST_URL=https://127.0.0.1:9443 \
BORON_SCREENSHOT_DIR=/root/boron-setup/theme-live \
node e2e/live-smoke.mjs
```

This verifies actual login, live admin statistics, Accounts, an unsaved password form, Appearance, persistence, mobile rendering, missing assets, and uncaught browser errors. It does not submit the password form or mutate customer data.

## Structure

- `src/config/themes.js`: supported skins and preference validation.
- `src/store/ui.js`, `public/theme-init.js`: persistent preferences and first-paint initialization.
- `src/themes.css`, `tailwind.config.js`: layout rules and shared theme tokens, including portalled components.
- `src/config/nav.js`: canonical role-aware navigation inventory.
- `src/config/toolGroups.js`: theme-specific categorization of that inventory; new routes are retained in a fallback group.
- `src/components/themes/ToolDashboard.jsx`: dashboard tools, filtering, collapse, and statistics.
- `src/pages/Appearance.jsx`: preview and preference UI.
- `src/components/layout/`: shared shell and navigation.

Research and implementation decisions are documented in [THEME-RESEARCH.md](THEME-RESEARCH.md) and [THEME-IMPLEMENTATION-PLAN.md](THEME-IMPLEMENTATION-PLAN.md).

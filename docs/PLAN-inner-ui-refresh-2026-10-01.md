# Inner-page UI refresh — 1 October 2026

Scope: frontend presentation only. Keep the existing dashboard bodies, logo, feature names, routes, role boundaries, API calls and data semantics. v3.1.2 already contains the reported critical write-path fixes; this project must preserve them and display errors honestly.

## Review boundary

Implement a complete shared shell and three reference pages, validate and deploy a frontend-only preview on the current server, then stop for the user's design review. Do not publish a release or migrate the remaining tools before that review. Existing pages receive the navigation shell and common header treatment; their forms and workflows remain intact until their migration batch.

## 1. Shared shell and foundations

- Mount persistent role-aware navigation on inner routes only; reuse the dashboard group registry and preserve existing links. Evo: compact navy navigation. Paper: lighter navigation, comfortable rows, tool search. Persist collapsed state and group expansion separately from dashboard preferences. Auto-reveal current group and highlight the exact active tool.
- Mount a Radix mobile drawer with focus trapping, Escape/close, route-close and an accessible Menu button. Keep impersonation and account/domain switching. Leave dashboard geometry intact.
- Mount shared global search on every route: Ctrl/Cmd+K, Ctrl+/, and slash outside editable controls. Search existing tools and explicit major-settings deep links with the current typo/synonym matcher. Restore focus on dismissal.
- Add an inner-page token layer for typography, spacing, radii, density, navigation, fields, tables and semantic states. No dashboard style overrides, gradients, decorative artwork or API changes.
- Standardize breadcrumb, title, description and right-side action placement. Build opt-in column selection, long-value copy/expand, compact setting rows, sticky save/discard and navigation/reload guards. Preserve existing accessible labels.

## 2. Reference pages

- **Account detail** `/app/accounts/:user`: sticky identity/plan/domain/PHP summary and real usage meters, without fabricated values; wrap existing tabs and retain query-string compatibility; primary Login as user/File Manager actions; account lifecycle controls only in a bottom Danger zone, object-named and typed-name confirmations. Preserve all existing sections and API calls.
- **Panel settings** `/app/panel-settings`: Access, Hostname/SSL, Telemetry and Recent changes sub-navigation; compact label/description/control rows; styled confirmation checkbox; unchanged port/hostname/certificate jobs, progress and recovery links; blur validation, independent save/discard state and leave-page protection. Do not change real ports, hostname or certificates during UI testing.
- **Customer Domains** `/app/domains`: sortable/searchable compact list, optional Columns menu, selection and confirmed bulk use of existing per-domain operations, a visible Manage action plus kebab, plain kind text and status-only pills, mobile card rows, named confirmations and visible per-item progress. Keep subdomain and parked-domain flows and account context.

## 3. Verification and presentation

- Chromium matrix: Evo/Paper × light/dark × 1280/768/375 widths for all reference pages; screenshots, no page overflow/clipped actions, keyboard focus/drawer/search, persisted navigation, real empty/error/loading/unsaved states and destructive-confirmation gating.
- Meaningful request assertions prove the UI uses unchanged endpoints and payloads. Mock destructive operations; public live inspection uses disposable QA accounts and read-only requests. No customer data or configuration changes for screenshots.
- Check sampled body/semantic/navigation/focus colors against WCAG AA and preserve existing dashboard DOM/layout with screenshot/geometry checks. Run the affected existing frontend suites and production build; no long backend suite for presentation-only work.
- Deploy only built static assets with prior entry files and hashed chunks retained. Provide screenshots and direct review links. No release.

## 4. After reference approval

Migrate in the requested order: Accounts list → Plans → Backup Manager → DNS → Email → Databases → PHP → SSL → Firewall/Security → Logs → remaining tools. Each coherent batch adopts the same components, maintains workflows/labels, adds only meaningful existing bulk operations, and updates relevant browser tests. Do not mark the full acceptance checklist complete at the reference stage.

## Sources and limits

- [cPanel interface](https://docs.cpanel.net/cpanel/the-cpanel-interface/the-cpanel-interface/): navigation and main menu persist on every page; slash focuses tool search. Current documentation's Jupiter main menu is simpler than WHM's grouped navigation, so Boron's grouped Paper sidebar follows the user's explicit brief.
- [cPanel Jupiter introduction](https://cpanel.net/blog/products/introducing-jupiter-a-new-look-for-cpanel/): grouped tools and right-side account information; existing Boron dashboards remain unchanged.
- [DirectAdmin skin/workflow documentation](https://docs.directadmin.com/directadmin/customizing-workflow/): Evolution workflow and navigation customization. The supplied QA screenshots/observations determine the required table/settings structure.
- No authorized live cPanel user session supplied. Official documentation/images are references; do not claim a live cPanel inspection or reuse proprietary assets.

## Automation contract for the reference batch

- Route paths and existing operation button labels are retained. API endpoints, request payloads and permission checks stay unchanged.
- Reference-page selects now use Radix `combobox` and `option` roles. Choose the combobox, then its option; do not use native `selectOption`. The existing subdomain browser suite is updated accordingly. Other pages retain their existing native/shared selects until migration.
- Account tabs wrap on all widths. The former mobile account-section select is replaced by the same tabs. Identity, SSL, PHP Functions, Processes and Notes remain available through “More account sections” and their original `?tab=` links.
- Parked-domain “Remove” is a menu item under the named row Actions menu; its confirmation button is still “Remove”.
- Both the domain and parked-domain tables have a Columns button. Scope column-picker locators to the intended table.
- Dashboard search keeps its existing textbox/option contract. Global search is a separate named dialog and combobox. Account lifecycle buttons retain their labels and appear only in the bottom Danger zone.
- Reference matrix cases use isolated browser storage and mocked API data, so they run safely in parallel with two workers. Live screenshots use existing QA sessions and block API writes.

## Reference-batch status

The shared inner-page shell and the three reference pages are implemented. A frontend-only preview is installed on the current v3.1.2 server with the prior static distribution preserved. The dashboard source, tool groups, branding and dashboard theme stylesheet are unchanged.

See [the review guide and screenshot gallery](REVIEW-inner-ui-refresh-2026-10-01.md) for direct links, implementation scope and verification evidence. The remaining migration order above is pending reference-design approval; no new release is published at this boundary.

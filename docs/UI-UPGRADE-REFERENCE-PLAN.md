# Evo and Paper reference-driven UI upgrade

## Reference review — every supplied image inspected

Archive: `da-cpanel-ss.zip`, 18 PNGs, downloaded 2026-09-24. Originals are kept
outside the repository at `/root/ui-references` (they contain customer/server data).

| Reference | Observations to apply |
|---|---|
| DA_.png | Navy topbar, blue module headers, borderless centered icon grid, right stats |
| DA_2.png | Consistent continuation of admin groups; links and metrics remain readable |
| Da3.png | Flat Users heading, primary action right, striped full-width account table |
| Da_user1.png | Same Evo structure for customer; domains/email groups; dense quota rows |
| Da_user2.png | Uniform grid spacing across secondary modules; recognizable software marks |
| da_domainsection.png | Flat domain table, clear selection/actions, thin separators, blue domain links |
| cpanelroot.png | WHM-style admin hierarchy, account/service tasks first, statistics at right |
| cpanel_root2.png | Account filters and compact records, clear suspended-account states |
| cpanelroot3.png | Account actions grouped together, avoiding scattered icon-only controls |
| cpanel_user1.png | White tool modules, horizontal icon/link pairs, general information column |
| cpaneluser3.png | Three-column links; clean section titles; full quota/limit labels |
| cpanel_user4.png | Software and security separation; thin outline icons with accent detail |
| cpanel_user5.png | Preferences separate from advanced tools; restrained footer |
| cpanel_user6.png | Email search/filter toolbar, storage data, directly visible row actions |
| cpanel_user7.png | Database creation is inline; username prefix, current database table |
| cpanel_user8.png | Domain table with document roots, status and manage actions |
| cpanel_user9.png | SSL inventory and issuance have separate headings and tabular workflows |
| cpanel_user10.png | Malware tabs, filters and clear table/empty states |

The cPanel screenshots are Jupiter/WHM, despite the earlier Paper Lantern request.
Use these latest supplied references, keeping the product theme names Evo and Paper.
Do not copy competitor branding, customer records, notices, unsupported tools, or
commercial plugin identities. Preserve the user's prior no-sidebar requirement.

## Implementation sequence

1. Replace accumulated conflicting theme overrides with a maintained shared base
   and distinct Evo/Paper rules. Keep existing larger readable type, refine Paper
   headings/weight, and make both light/dark modes coherent throughout portals.
2. Evo: navy horizontal chrome, full-width dashboard search, blue section strips,
   unboxed centered icons (not individual cards), 3:1 workspace/statistics split,
   compact striped stats and tables, small radii, plain underlined page headings.
3. Paper: navy branding chrome, light content, white title-case tool groups,
   three horizontal icon/link columns, thin black/blue icon strokes with orange
   accents, lighter large page titles, borderless interior sections and compact
   striped tables. Dark mode uses the same hierarchy with readable contrast.
4. Shared components: semantic hooks for page heading/action areas, table toolbar,
   pagination, form sections, dialogs and tabs. Fix styling at component level so
   all internal pages inherit the intended theme, not only the dashboard.
5. Apply explicit workflow improvements where the reference differs structurally:
   inline database creation with username prefix, clear current-database section;
   inspect domain/email/SSL/admin account/malware screens and retain their working
   actions while aligning tables/forms/status displays. No cosmetic dummy controls.
6. Preserve dashboard fuzzy search, keyboard focus, collapse preferences, theme
   persistence, responsive layouts and tenant authorization. Theme styles are small;
   loading both is acceptable, but correctness takes precedence over instant switching.
7. Validate with focused backend import/RPC tests, frontend production build and
   Playwright across roles, themes, color modes and mobile/desktop sizes. Capture
   and personally inspect dashboard and interior screenshots, fix observed defects.
8. Run remaining release gates once on the final tree. Publish the combined release
   (including DirectAdmin migration), self-update the primary panel, inspect HTTPS
   health and deployed UI. Record evidence and distinguish mocked migration checks
   from real-source acceptance (no DirectAdmin source credentials supplied yet).

## Completion criteria

Every supplied screenshot has an entry above. Both themes have visibly different
but internally consistent dashboards and interiors; no unused left gutter, clipped
labels/actions, focus rectangles around the whole search, unreadable dark labels,
or new dead tools. Forms retain accessible labels and confirmations. Existing
functional checks pass. Final report states actual build/test/release/deployment
results and any real-server migration validation limitation.

## Implementation verification

The production frontend build passes with the reference-driven theme rules. The
full browser run passed 132 existing checks; six newly added checks initially
failed because their fixtures omitted cached login identity. After correcting
that test setup, all 25 focused checks passed, including those six cases, both
DirectAdmin transports, reference interiors, PHP controls and theme behavior.

Desktop and mobile captures were personally inspected for admin/customer grids,
DNS, databases, SSL, PHP, migrations and malware. This caught and corrected dark
link contrast, compressed mobile header text and identical Evo tool artwork. The final section-header correction also passed
all seven targeted reference-interior and malware checks.
Screenshots and detailed logs remain outside the repository in
`/root/boron-ui-verification-1.6.0/`; source reference screenshots are not shipped.

New DirectAdmin backend regression cases: 17 passed. Existing importer coverage
also passed. Real source-server migration acceptance remains unperformed; see
`DIRECTADMIN-SERVER-MIGRATION.md` for the operator checklist. The full release gate passed, v1.6.0 was published, and the primary panel
completed self-update job 12. See `RELEASE-VERIFICATION-1.6.0.md`.

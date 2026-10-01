# Inner-page UI preview — 1 October 2026

This is the shared shell and three reference-page batch, ready for design review on the current server. The release remains v3.1.2; no GitHub release is published. The [re-review follow-up](VERIFY-qa-re-review-2026-10-01.md) records Redis/rename fixes, stronger theme differences, the approved and verified live isolation fix, and current screenshots.

## Open the reference pages

- [Admin Account detail](https://boron.sitecountry.com:2222/app/accounts/qafix30): identity and real usage summary, wrapping tabs, routine account settings, bottom Danger zone, named/typed lifecycle confirmations.
- [Admin Panel settings](https://boron.sitecountry.com:2222/app/panel-settings): section navigation, compact setting rows, styled controls, field validation, independent Save/Discard and leave-page protection.
- [Customer Domains](https://boron.sitecountry.com:2222/app/domains): sign in as a customer or use **Login as user** on the test account. Search, sortable columns, column picker, selection, confirmed bulk operations, per-item results, Manage plus row menu, copy/expand paths and mobile card rows.

Choose Evo or Paper and light/dark in the existing top-bar controls. The persistent sidebar belongs to inner pages; the dashboard bodies retain their existing layout. Sidebar collapse and group choices persist separately from dashboard preferences. Global search is available throughout the authenticated app with Ctrl/Cmd+K, Ctrl+/ and slash outside editable fields; it uses the existing typo/synonym matcher and role-filtered tools.

## Verification

- Production build completed successfully.
- Focused browser runs cover 79 distinct cases across the new reference/accessibility suites and affected existing panel-settings, subdomains, QA-remediation, themes, interiors, app-navigation and search suites. Initial fixture/locator issues were corrected and rerun. A new density assertion also identified Paper row height exceeding the target after cell borders; the table control height was corrected before the final rerun. The latest nine accessibility/operation checks all passed; these include focus restoration, mobile drawer-to-search handoff, typed confirmations, drafts, request contracts and safe error references.
- Four Node error-contract checks passed: GB validation copy, safe server failure copy with existing server reference, network failure without a fabricated reference, and retained Axios credential/interceptor behavior.
- All 13 final Domains matrix/operation checks passed, including actual desktop row-height assertions of at most 44 px.
- All 36 live reference views passed: three reference pages × two themes × two modes × three widths (1280, 768, 375). All 12 customer Domains views were captured and checked again after the final table-density correction. Live checks use existing QA sessions and block API writes: no real account lifecycle, port, hostname or certificate changes are performed.
- Dashboard tool-column, group and tile geometry matches the prior static build in all eight role/theme/mode combinations. Dashboard source, group configuration, branding and theme stylesheet are unchanged. Live statistics and font/data readiness vary the statistics-panel height, so exact statistics-height equality is not claimed. A separate controlled comparison matched its first layout, but browser interception teardown failed; it is not counted as a passed suite.
- Accessibility checks sample body/semantic text, enabled button text and placeholders against 4.5:1, and field borders/focus indicators against 3:1 in all four combinations. This is targeted verification, not a full WCAG certification of every existing page.

Backend routes, authorization, jobs and operation payloads are unchanged. Error presentation preserves server-issued reference IDs rather than manufacturing IDs that cannot be found in the Error Log. Embedded account tools and the remaining pages retain their workflows pending their own migration batches.

## Screenshot gallery

Screenshots are server-local review artifacts; these links work in the shared workspace. No passwords or session cookies are included.

| Reference | Evo light | Evo dark | Paper light | Paper dark |
|---|---|---|---|---|
| Account detail, 1280px | [1280px](/root/boron-setup/ui-refresh-20261001/live-account-evolution-light-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-account-evolution-dark-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-account-paper-lantern-light-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-account-paper-lantern-dark-1280.png) |
| Account detail, 768px | [768px](/root/boron-setup/ui-refresh-20261001/live-account-evolution-light-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-account-evolution-dark-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-account-paper-lantern-light-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-account-paper-lantern-dark-768.png) |
| Account detail, 375px | [375px](/root/boron-setup/ui-refresh-20261001/live-account-evolution-light-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-account-evolution-dark-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-account-paper-lantern-light-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-account-paper-lantern-dark-375.png) |
| Panel settings, 1280px | [1280px](/root/boron-setup/ui-refresh-20261001/live-settings-evolution-light-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-settings-evolution-dark-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-settings-paper-lantern-light-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-settings-paper-lantern-dark-1280.png) |
| Panel settings, 768px | [768px](/root/boron-setup/ui-refresh-20261001/live-settings-evolution-light-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-settings-evolution-dark-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-settings-paper-lantern-light-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-settings-paper-lantern-dark-768.png) |
| Panel settings, 375px | [375px](/root/boron-setup/ui-refresh-20261001/live-settings-evolution-light-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-settings-evolution-dark-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-settings-paper-lantern-light-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-settings-paper-lantern-dark-375.png) |
| Customer Domains, 1280px | [1280px](/root/boron-setup/ui-refresh-20261001/live-domains-evolution-light-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-domains-evolution-dark-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-domains-paper-lantern-light-1280.png) | [1280px](/root/boron-setup/ui-refresh-20261001/live-domains-paper-lantern-dark-1280.png) |
| Customer Domains, 768px | [768px](/root/boron-setup/ui-refresh-20261001/live-domains-evolution-light-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-domains-evolution-dark-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-domains-paper-lantern-light-768.png) | [768px](/root/boron-setup/ui-refresh-20261001/live-domains-paper-lantern-dark-768.png) |
| Customer Domains, 375px | [375px](/root/boron-setup/ui-refresh-20261001/live-domains-evolution-light-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-domains-evolution-dark-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-domains-paper-lantern-light-375.png) | [375px](/root/boron-setup/ui-refresh-20261001/live-domains-paper-lantern-dark-375.png) |

## Review boundary and next batch

Review these three pages before extending their patterns to the remaining tools. The full app acceptance checklist is not complete at this stage: page-specific table controls, settings migration, bulk operations and confirmations are implemented in the references, with the shared shell available across inner routes.

After approval: Accounts list → Plans → Backup Manager → DNS → Email → Databases → PHP → SSL → Firewall/Security → Logs → remaining pages. Each batch keeps existing routes, permissions, payloads and operation labels, and receives focused regression checks.

## Preview recovery

The original shell preview installed only static frontend assets. The prior entry file and full distribution are preserved in `/root/boron-setup/ui-refresh-20261001/static-before-preview`; old hashed chunks remain in the active distribution so existing tabs continue to work. Rolling back that static preview requires restoring the saved `index.html` atomically to `/opt/boron/static/dist/index.html`; no backend restart or data restoration is required. The subsequent re-review also installed focused daemon/template fixes; their separate recovery requirements are recorded in the follow-up report. Session material stays outside the repository in protected files.

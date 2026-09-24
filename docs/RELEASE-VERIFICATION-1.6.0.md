# Boron 1.6.0 verification — 2026-09-24

## Delivered

All 18 supplied reference images were inspected and recorded in
`UI-UPGRADE-REFERENCE-PLAN.md`. Evo and Paper received distinct dashboard styles,
shared interior/form/table/dialog styling, inline database creation, separated SSL
inventory/issuance, responsive refinements and original Evo tool artwork.
DirectAdmin server migration is included; see its migration guide for scope.

Implementation commit: `825bf64`. Signed release commit/tag: `c36f6a4`, `v1.6.0`.
Release: https://github.com/adityavs5/boronpanel/releases/tag/v1.6.0

## Verification

- Full release backend gate: **3,570 passed, 9 skipped**, 7 warnings,
  6,134.86 seconds. No test skips were added for this release.
- Full browser run: 132 passed; six new tests failed because their fixtures
  omitted cached login identity. After fixing the fixture, all 25 focused cases
  passed, including those six and dashboard/PHP/theme behavior. Final shared
  header refinement: all seven reference-interior/malware cases passed.
  Together these cover all 138 browser cases, with affected cases rerun.
- Fresh production build, checksum and pinned Ed25519 signature verification
  passed in the release pipeline. GitHub has the archive, checksum and signature.
- Live update was started through the authenticated panel API, job **12**,
  from 1.5.1 to 1.6.0. Focused live preflight, rollback backup, download,
  checksum/signature verification, staging, migrations, service integration,
  symlink swap and final health check succeeded. Completed at
  **2026-09-24 12:26:49 UTC**; rollback to 1.5.1 is available.
- Deployed browser assertions passed for real admin login, live statistics,
  accounts, both themes, light/dark form persistence, appearance, mobile,
  plan editor, OLS TLS, customer metrics, subdomains, database backup, PHP,
  domain/email/database/SSL/malware interiors and completed update history.
  No uncaught browser errors or failed static assets were reported.
- Screenshots were personally inspected. Initial captures of some asynchronous
  pages caught loading states; they were retaken after images/fonts/data loaded.
  The retake completed every assertion but its process exited with SIGTERM during
  cleanup. A separate final authenticated check then exited successfully,
  explicitly confirmed clean browser shutdown, v1.6.0, completed job 12,
  rollback availability, both active services and public HTTPS health `ok`.

Artifacts, screenshots and logs remain outside Git at
`/root/boron-ui-verification-1.6.0/`; screenshots contain private server/account
information and are not shipped. Original references remain outside Git too.

## Limits and timing

No real DirectAdmin source server was supplied. Transport behavior, SQL preflight
and UI flows were checked with offline fixtures/mocks; real-source migration and
application compatibility still require operator acceptance before DNS cutover.

The slowest backend cases were real encrypted database backup round-trip
(125.21s), encrypted mail backup/metadata (120.20s), and thousand-mailbox storage
handling (105.64s), followed by interrupted restore/rollback recovery. This timing
report is evidence for future test optimization; the release gate was not reduced.

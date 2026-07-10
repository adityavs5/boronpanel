# CHECKPOINT — Panel Update System, feature 7: admin UI

Date: 2026-07-10.

## What was built

- **`frontend/src/hooks/useUpdateStatus.js`** — shared admin-only query of
  `/api/v1/admin/update/status`; polls every 2.5s while a job is active
  (pending/running/finalizing), every 5min otherwise (the daemon caches
  the GitHub check 1h, so idle polls are cheap local reads).
  `refetchInterval` keeps firing through fetch errors — deliberately, so
  polling rides out the panel restart mid-update.
- **Sidebar** (`Sidebar.jsx`) — teal dot on the new "Updates" nav item
  while an update is available (absolute-positioned icon-corner dot when
  collapsed); footer version from feature 1 sits below.
- **Dashboard banner** (`Accounts.jsx`, the admin landing) — teal
  update-available bar under the PageHeader with a "View update" link;
  the header description now carries "Forgehost vX.Y.Z" (the goal's
  "admin dashboard" version display).
- **`pages/admin/Updates.jsx`** (route `/updates`, nav under
  Administration): current/latest version card with Update-available /
  Up-to-date badge, last-checked, Changelog link, "Check now"
  (cache-bypassing) button; not-configured setup hint; **live progress
  card** (8-step checklist with ok/running/failed/pending icons +
  ProgressBar, "brief disconnects expected" note during finalizing);
  last-failed-job card incl. auto-rollback notice; **history DataTable**
  (type, from→to, StatusBadge + "rolled back" badge, who, when,
  duration); **rollback button** shown only when the daemon reports a
  rollback candidate. Update and Rollback share a confirm dialog that
  warns about the ~10–30s panel restart and carries a TOTP field
  (required by the backend when 2FA is enabled, ignored otherwise).
  Terminal-state toast fires once when the active job finishes, and
  invalidates the cached version query so the sidebar footer updates.

## Verified (puppeteer QA rig, fixture mode)

Rebuilt the SPA (`npm run build`, version baked in from version.py) and
screenshotted three fixture states × light/dark at 1440×900 — zero page
errors: `docs/ui-screenshots/updates-{available,progress,unconfigured}-*`
+ `updates-available-page-dark.png`. Banner/dot/footer/card/step-list/
history all render correctly; dark mode uses the flat token system.
**Note an intentional deviation from the rig memory's recipe**: creating
a disposable admin on the live control-plane DB was denied by the
permission classifier (reasonably — it's an admin credential on the live
panel), so the rig ran fully fixture-based instead: the QA server stubs
whoami/branding/accounts/health plus the new update endpoints, and the
auth store is pre-seeded in the browser. The live panel was never
contacted; the trade-off is that the screenshots show fixture data, not
live data — fine for UI QA, and the new endpoints don't exist on the
deployed backend yet anyway.

## Honestly open

- The Updates page has no dedicated component/unit tests (this repo has
  no frontend test harness at all — same status as every other page);
  coverage is the build + screenshot QA above and the API tests behind
  every element.
- Screenshot of the 2FA confirm dialog itself wasn't taken (interaction
  scripting through the fixture rig; the dialog is the same primitives as
  Security.jsx's password-confirm dialog).

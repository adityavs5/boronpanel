# QA round 2 — Item 1: file manager opens in a new tab

## Finding that changed the plan

The user's ask assumed FileBrowser was embedded in-panel via an iframe.
Investigation found it was neither an iframe nor already a new tab: both
`frontend/src/pages/customer/Files.jsx` and the admin `AccountDetail.jsx`
"File Manager" button used `window.location.assign(...)` — a same-tab
full-page navigation that replaces the SPA entirely. No iframe exists
anywhere in the frontend (confirmed by a repo-wide grep). The actual fix
is "same-tab redirect → new tab", not "iframe → new tab".

## Fix

- `Files.jsx` (the dedicated `/files` SPA route, reached via sidebar nav):
  now `window.open(launchUrl, '_blank', 'noopener')` then
  `navigate('/dashboard', { replace: true })` — opens FileBrowser Quantum
  in its own tab and returns the SPA tab to the dashboard instead of being
  stuck on a spinner that never resolves (the old same-tab version relied
  on actually navigating away; a bare `window.open` with no follow-up would
  leave the SPA tab parked on "Opening file manager…" forever).
- `AccountDetail.jsx` admin "File Manager" button: same launch URL, now
  `window.open(..., '_blank', 'noopener')` — admin stays on the account
  page (not a dedicated route, so no navigate-away needed).

The auth handoff itself (`/files/launch` → `fb.open` audit RPC → signed
`fh_fb_target` cookie → 302 into the FileBrowser Quantum proxy) is
untouched — it's cookie-based, not URL-token-based, so it works identically
whether the redirect lands in the same tab or a new one.

## Verification

`npm run build` — clean, no errors, `Files-*.js`/`Databases-*.js`/`Cron-*.js`
chunks all built. No backend change, so no new pytest coverage needed for
this item (pure client-side navigation change). Live click-through deferred
to deploy, same as every other frontend-only change in this batch.

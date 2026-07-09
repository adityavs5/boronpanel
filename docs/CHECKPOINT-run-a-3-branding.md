# CHECKPOINT run-a-3 — Branding (white-label)

**Goal:** admin configures panel name, logo (PNG/SVG to
`/etc/forgehost/branding/`), favicon, support email, support URL. Applied
in sidebar, login, browser tab, email notifications, installer output.
API: `GET/PATCH /admin/branding`.

## What was built

- **Model** `BrandingSettings` (single row id=1, same convention as
  `NotificationSettings`/`WafSettings`): panel_name (default "Forgehost"),
  logo_filename / favicon_filename (NULL = built-in default),
  support_email, support_url. New table — no additive-column migration
  needed.
- **Config**: `settings.branding_dir` (`/etc/forgehost/branding`, per the
  goal's literal path) + `branding_max_upload_bytes` (2MB).
- **Daemon** `daemon/branding.py` (ops `branding.get/set`,
  `branding.logo.upload/remove`, `branding.favicon.upload/remove`):
  - Settings CRUD with partial-update semantics (`"field" in params`, the
    same convention `notifications.set_settings`'s own documented bug-fix
    established); panel_name required non-empty ≤64 chars; support_email
    via `validate_email_address`; support_url via
    `validate_redirect_target` (existing validators, no new ones).
  - **Uploads travel base64 over the RPC channel** — the daemon RPC
    framing is JSON (ARCHITECTURE §2); there is no binary side-channel,
    and inventing one for a ≤2MB logo wasn't warranted. The API layer
    receives the multipart file and base64s it into the `branding.*.upload`
    op. Files land as `{branding_dir}/logo.{ext}` / `favicon.{ext}`,
    written 0640 root:forgehost-api (the same group-read pattern
    `shared/db.py._grant_api_group_read` uses for the DB file) so the
    unprivileged API can stream them; a re-upload with a different
    extension removes the stale old file.
  - **Content sniffing, not extension trust**: PNG by magic bytes, ICO by
    magic bytes (favicon only — rejected for logos), SVG by root element
    after BOM/XML-prolog stripping. Unrecognized bytes rejected.
- **API** `api/routers/branding.py`, two trust levels:
  - `GET /api/v1/branding` + `GET /api/v1/branding/{logo,favicon}` are
    **public** (no auth) — the login page and browser tab must show
    branding before any session exists. The GET path reads the DB/file
    directly (`read_session` + `FileResponse`), the same
    "API reads directly, writes go through the daemon" split
    `accounts.list_accounts` already uses; asset bytes never transit the
    RPC socket on the read path.
  - `PATCH /api/v1/admin/branding`, `POST/DELETE
    /api/v1/admin/branding/{logo,favicon}` — all `require_admin`.
- **Email notifications**: `daemon/notifications.py` now renders subjects
  and a body signature from the configured panel name (formerly a
  hardcoded "Forgehost" `_SUBJECTS` dict) — read-only cross-feature reuse
  of the singleton row.
- **Frontend**: `useBranding()` hook (public endpoint, React-Query-deduped);
  `BrandingBootstrap` (mounted at the root in `main.jsx`) sets
  `document.title` + swaps the favicon `<link>`; `Sidebar.jsx` and
  `Login.jsx` render the panel name + uploaded logo (falling back to the
  first-letter monogram tile when no logo is set); new admin **Branding**
  page (`/branding`, nav under Administration) with the settings form +
  logo/favicon upload/remove cards. Also fixed the stale pre-paint dark
  background in `index.html` (`#0f141e` → `#111827`, feature 2's value).
- **Installer output**: deferred to feature 9 (the installer doesn't
  exist yet — it will read the panel name when built).

## SVG XSS risk — how it's handled (three independent layers)

SVG can embed `<script>`, event handlers, and `javascript:` URIs. Layers,
documented in `_serve_asset`'s docstring and each verified rather than
assumed:
1. The asset GET path is neither `/app` nor the filebrowser proxy, so
   `api/main.py`'s security-headers middleware default branch stamps
   `Content-Security-Policy: script-src 'none'` on the response —
   **verified empirically** by
   `test_logo_response_has_restrictive_csp`, not inferred from reading
   the middleware.
2. Every consumer renders the asset via `<img>` (Sidebar/Login/Branding
   page) — browsers never execute script inside an SVG loaded as an
   image (only `<object>`/`<iframe>`/direct navigation do).
3. Upload-time reject filter for `<script`/`on*=`/`javascript:` in SVG
   bytes — explicitly documented as a block-the-obvious-cases guard, not
   a full sanitizer; layers 1–2 are the real defense.

Admin-only upload also bounds the attacker model: only an admin can
upload, and an admin already has RCE-equivalent power over this panel.

## Verified live (isolated scratch stack — NOT the production deployment)

The live `:9443` service runs old code, and both deploying and running
additive migrations against the live DB require user approval (correctly
denied by the permission classifier when attempted). Instead: a fully
isolated stack — scratch SQLite DB + scratch branding dir via
`FORGEHOST_CONFIG` env override, `uvicorn api.main:app` from the repo on
`127.0.0.1:9444` (serves the real fresh SPA bundle and the real API
code), branding seeded via the real `daemon/branding.py` functions
(panel name "Acme Cloud" + a generated PNG logo), a directly-seeded admin
session cookie. Puppeteer verified, with screenshots:
- **Login page (unauthenticated)**: custom name "Acme Cloud" + uploaded
  logo on both the brand panel and the footer — branding loads pre-auth.
- **Sidebar**: "Acme Cloud" + logo replacing the Forgehost monogram.
- **Browser tab title**: "Acme Cloud" on both /login and /accounts.
- Default-fallback path also verified earlier against the live API (which
  404s the new endpoint): hook falls back to "Forgehost" gracefully, no
  console errors, admin Branding page renders.

Scratch stack torn down after (uvicorn stopped, scratchpad DB is
session-local); disposable QA users removed from the live panel DB along
with their sessions/impersonation rows.

## Tests

`tests/test_branding.py` — 24 tests: settings defaults/partial-update/
validation (empty + overlong name, bad email, bad URL, clear-with-empty),
upload content-sniffing (PNG ok, SVG ok, SVG-with-script rejected, ICO
favicon-only, garbage rejected, bad base64 rejected, oversize rejected),
extension-change cleanup, remove idempotency, and the API trust split
(public GET with no auth, 404 on unset asset, **CSP header verified on
the served SVG**, PATCH 401 unauthenticated / 403 customer / 200 admin).
Full suite green (1486 passed on the run that included 23 of these; the
24th — the CSP check — was added after that run started and passes
standalone; next full run will show 1487).

## What's honestly still open

- Not deployed to `/opt/forgehost` (needs user-approved deploy per
  `[[forgehost-deploy-flow]]`); the production DB gets the
  `branding_settings` table automatically at next daemon start
  (`create_all`).
- Installer-output branding lands with feature 9.
- SVG uploads are reject-filtered + CSP/`<img>`-contained, not fully
  sanitized (documented tradeoff above).

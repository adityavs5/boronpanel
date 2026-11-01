# Boron license server

Runs on the distribution host at `https://license.boronpanel.com`. Design:
[docs/LICENSING-AND-UPDATES.md](../docs/LICENSING-AND-UPDATES.md). This folder is
`export-ignore`d, so it never ships inside panel packages.

## Issuing and managing licenses (on the server, as root)

```bash
boron-license issue --tier pro --name "Acme Hosting" --email ops@acme.example --expires 2027-11-30
boron-license list acme              # search by email, name, IP, key, key hint or ID
boron-license show 12                # details and installations
boron-license suspend 12 --reason "unpaid invoice"
boron-license resume 12
boron-license rebind 12 --clear      # customer moved servers: next activation binds
boron-license update 12 --tier unlimited --expires 2028-11-30
boron-license tier list
boron-license tier set pro --max-accounts 30
boron-license token create billing   # admin API token, shown once
```

The license key is printed **once** at issue time and only its hash is stored.
Expiry dates given as `YYYY-MM-DD` mean the end of that day, UTC.

## API

| Endpoint | Caller | Purpose |
|---|---|---|
| `POST /v1/activate` | Panel/installer | Bind an unbound license to the caller's IP; signed document |
| `POST /v1/check` | Panel, every 12 h | Refresh the signed document; never binds |
| `POST /v1/updates` | Panel updater | Current (or an exact) release with 15-minute download links |
| `GET /v1/public-key` | Anyone | License-signing public key |
| `/v1/admin/...` | Bearer token | Issue, list, show, update, suspend/resume/cancel, rebind, events, tiers |
| `GET /admin` | Browser | Admin portal (static page using the admin API) |

Client body: `license_key`, `install_id` (8–64 of `A-Za-z0-9-`), optional
`hostname`, `version`, `account_count`. Any real key gets a signed document,
including refusals (`suspended`, `cancelled`, `expired`, `ip_mismatch`,
`not_activated`), so panels can act on refusals they can verify. Unknown keys get
`403 {"error": "invalid_license"}`.

### Updates

`POST /v1/updates` with `license_key`, `install_id`, `channel` (`stable` or
`beta`), optional `version` (exact `X.Y.Z`) and `current_version`. The license
must be active, unexpired and bound to the caller's IP (same client-IP rules as
`/v1/check`). A `beta` license may ask for either channel, a `stable` one only
for `stable`; an exact version must belong to a channel the request may use.

The answer comes from the index the release tooling writes into
`BORON_LICENSE_RELEASES_DIR` (default `/srv/download/protected`):
`channels/<channel>.json`, or `releases/<ver>/release.json` for an exact
version. Indexes are validated before use (format 1, `X.Y.Z` version, the five
file names must be plain `boron-<ver>.*` names). Every URL is

```
https://download.boronpanel.com/protected/releases/<ver>/<file>?md5=<token>&expires=<unix>
token = base64url_nopad(md5(f"{expires}{uri}{client_ip} {secret}"))
```

which is exactly what nginx `secure_link_md5
"$secure_link_expires$uri$remote_addr <secret>"` checks, so links work only from
the caller's address and only for 15 minutes. Panels still verify the checksum,
the publisher signature and the signed metadata with the pinned release key; the
index only routes.

| Status | Body |
|---|---|
| 200 | `format`, `channel`, `version`, `published_at`, `changelog`, `archive_size`, `urls` (archive, checksum, signature, metadata, metadata_signature), `links_expire_at` |
| 403 | `{"error": "invalid_license"}` unknown key |
| 403 | `{"error": "license_not_usable", "reason": "suspended\|cancelled\|expired\|ip_mismatch\|not_activated"}` |
| 403 | `{"error": "channel_not_allowed"}` |
| 404 | `{"error": "no_release"}` (also for a missing or malformed index; the reason is in the audit log) |
| 503 | `{"error": "updates_unavailable"}` when `BORON_LICENSE_DOWNLOAD_SECRET` is not set |

Every call is recorded in `events` (`updates` with `result`, or `unknown_key`).

### Admin API additions

`GET /v1/admin/me` (token name, used by the portal to sign in),
`GET /v1/admin/licenses/{id}/events?limit=50` (newest first),
`POST /v1/admin/tiers` `{"name": "...", "max_accounts": 30 | null}` (create or
change; returns all tiers).

## Admin portal

`https://license.boronpanel.com/admin`. Paste an admin token
(`boron-license token create NAME`); it stays in that browser tab's
`sessionStorage` and is gone when the tab closes. From there: search and list
licenses, issue one (the key is shown once, with a copy button), open a license
with its installations and recent events, suspend/resume/cancel with a reason,
bind or clear the server IP, change tier, expiry, channel, customer and notes,
and list or set tiers. Every change is audited under `token:<name>`.

The page, `app.js` and `app.css` ship inside the package (`boron_license/admin/`),
load nothing from third parties and are served with `Content-Security-Policy:
default-src 'self'` (no inline code), `X-Frame-Options: DENY` and
`Cache-Control: no-store`. Put Cloudflare Access in front of `/admin` and
`/v1/admin/` as well, and use one token per person so they can be revoked
individually.

## Operations

- Deploy or upgrade: upload this folder and run `bash deploy/install_server.sh` as
  root. It's idempotent and keeps the database, configuration and signing key.
- Service: `systemctl status boron-license` (uvicorn on `127.0.0.1:8100`, user
  `boronlic`, sandboxed by systemd).
- Protected downloads: the installer adds `BORON_LICENSE_DOWNLOAD_SECRET`
  (random, URL-safe) and `BORON_LICENSE_RELEASES_DIR` to `config.env` if
  missing, creates `/srv/download/protected/{releases,channels}` (0755 root),
  renders `deploy/nginx-download-protected.conf` with the secret into
  `/etc/nginx/snippets/boron-download-protected.conf` (0600 root) and inserts
  `include snippets/boron-download-protected.conf;` into the
  `download.boronpanel.com` 443 block of `/etc/nginx/sites-available/boron-dist.conf`
  before its `location / { return 404; }` (`deploy/nginx_include.py`; idempotent,
  previous file kept as `.bak-boron`, restored if `nginx -t` fails). If that
  file has another layout it changes nothing and prints what to add by hand.
  nginx answers 403 for a bad token, 410 for an expired one, only GET/HEAD,
  no listings, `Cache-Control: private, no-store`.
- Rotating the download secret: delete its line from `config.env` and rerun the
  installer (links already handed out stop working; panels just ask again).
- Configuration: `/etc/boron-license/config.env`. Signing key:
  `/etc/boron-license/keys/license-signing.key`. **Back up the key off the
  server.** Panels embed its public key, so losing it means re-keying every panel.
- Database: PostgreSQL `boron_license`. A nightly dump goes to
  `/var/backups/boron-license` (14 kept); copy those off the server too.
- Tests: `python -m pytest` from this folder (SQLite, no services needed).

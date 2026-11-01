# Licensing and updates

Design for Boron as licensed software. This replaces the GitHub-release updater
and the public GitHub install path. Status: the distribution server and signed
installer (part 1) and the license server MVP (part 2: API and CLI; the admin
portal UI is still to come) are live. The panel client (part 3) and
license-gated updates (part 4) are built for 3.3.0. Operations:
[licensing/README.md](../licensing/README.md); releasing:
[RELEASING.md](RELEASING.md).

## Decisions

| Topic | Decision |
|---|---|
| License binding | One license per **server public IP**, as seen by the license server |
| Pricing | **Tiers by hosting-account count** (e.g. Solo 1, Admin 5, Pro 30, Unlimited) |
| Offline behaviour | **14-day grace** on a signed cached license; then only the admin UI locks |
| Issuing | **Admin portal + API** on `license.boronpanel.com`; billing systems connect later |

Hosted websites, mail, DNS and databases **never stop** because of a license
problem. Only panel management is restricted.

## Servers

| Host | Role |
|---|---|
| `104.234.179.66` (`dist.boronpanel.com`) | `download.boronpanel.com` (installer, signed packages) and `license.boronpanel.com` (license API, admin portal) |
| `80.47.12.101` (`demo.boronpanel.com`) | Test and demo panel; target for update testing |

Both public hostnames on `.66` are behind the Cloudflare proxy. Ports 80/443
accept only Cloudflare's ranges (`/usr/local/sbin/cf-ufw-sync`, weekly), and nginx
restores the client IP from `CF-Connecting-IP`. SSH is key-only.

Cloudflare zone settings: SSL **Full (strict)**, Always Use HTTPS on, minimum
TLS 1.2, Bot Fight Mode off (panels and `curl` are not browsers). The origin
serves a Cloudflare Origin CA certificate (ECC, expires 2041) for
`boronpanel.com` and `*.boronpanel.com`; its key never left the server
(`/etc/nginx/boron-tls/`). Automation uses the API token
`boron-distribution-server`: zone `boronpanel.com` only (SSL and Certificates,
Zone Settings, DNS, Zone WAF, Bot Management: Edit; Cache Purge), usable only
from `104.234.179.66`. It is stored at `/root/.cloudflare/token` there and in
`secrets/cloudflare.token` locally.

## Keys

| Key | Lives | Signs | If stolen |
|---|---|---|---|
| Release signing (Ed25519) | Owner's machine only (`secrets/release-signing.key`), offline backup | Release manifests | Attacker can ship code to every panel — keep offline |
| License signing (Ed25519) | `.66`, readable only by the license service | License documents | Attacker can forge licenses; rotate and re-issue |
| Download URL secret | `.66`, shared by license service and nginx | Short-lived download links | Attacker can download packages; rotate |

The release key is never on an internet-facing server. Panels embed both public
keys.

## Part 1 — Distribution and installer (live)

```
/srv/download/
  install.sh                         launcher (public)
  channels/<channel>.manifest(.sig)  signed pointer to the current release
  releases/<version>/boron-<version>.tar.gz
  releases/<version>/boron-<version>.manifest(.sig)
```

A manifest is `key=value` text (`format`, `product`, `channel`, `version`,
`commit`, `file`, `sha256`, `size`, `published`) with a raw Ed25519 signature.
The launcher verifies the signature with its embedded public key, checks the
archive's SHA-256, and only then extracts and runs `scripts/install.sh`.
Published versions are immutable. `scripts/dist_publish.sh` builds
reproducibly from `HEAD`, signs locally and uploads over one SSH connection;
`scripts/dist_release.sh` does the same together with the update release
(part 4).

Install command (`--license` is optional):

```bash
curl -fsSLo install.sh https://download.boronpanel.com/install.sh && bash install.sh --license BRN-XXXX-XXXX-XXXX-XXXX
```

## Part 2 — License server

FastAPI service on `127.0.0.1` behind nginx at `license.boronpanel.com`, with
PostgreSQL (local only). Matches the panel's stack.

**Data**
- `tiers`: name, `max_accounts` (null = unlimited).
- `licenses`: key hash (keys shown once, stored as SHA-256; format
  `BRN-XXXX-XXXX-XXXX-XXXX`), customer, tier, `bound_ip`, status
  (`active`/`suspended`/`cancelled`), `expires_at`, channel entitlement,
  IP-change history.
- `installations`: install ID, licence, last check, version, account count.
- `events`: append-only audit of every admin and API action.

**API (`/v1`)**
- `POST /activate` — key + install ID. Binds an unbound license to the caller's
  IP, or accepts it if the IP matches. Returns a signed license document.
- `POST /check` — every 12 h with jitter: key, install ID, version, account
  count. Returns a fresh signed document or a reason (`ip_mismatch`,
  `suspended`, `expired`).
- `POST /updates` — key, install ID, channel, optional exact version. Returns
  the release index and short-lived download links for valid licenses only
  (part 4).
- Admin API (token + IP-restricted): issue, suspend, resume, change tier,
  rebind IP, list, search. The billing integration later uses this too.

**Signed license document**: license ID, tier, `max_accounts`, bound IP, status,
`issued_at`, `valid_until` (= issued + 14 days), `expires_at`,
`updates_until`. JSON, Ed25519-signed.

**Admin portal**: `license.boronpanel.com/admin`, behind Cloudflare Access plus
its own password and TOTP.

## Part 3 — Panel client (built)

| Piece | Where |
|---|---|
| Verification and state (API and daemon) | `shared/license.py` (embeds the license server public key) |
| Key, install ID, server calls | `daemon/license_client.py`, RPC `license.status`/`refresh`/`activate` |
| Admin lock (HTTP 402 `license_required`) | `api/security.py` `enforce_license`, inside `get_identity` |
| Account limit | `handlers_account.create_account` → `require_account_capacity` |
| Admin API | `GET/POST /api/v1/admin/license`, `POST …/refresh`, `GET …/state` |
| Periodic check | `scripts/license_check.py` via `deploy/boron-license.cron` (01:07 and 13:07 plus up to 30 min jitter) |
| UI | `/app/license` page, admin banner, automatic redirect on 402 |
| Installer | `--license KEY` / `FH_LICENSE_KEY` / prompt; checked before installing, activated after |

Files: `/etc/boron/license.key` (0600), `/etc/boron/license.json` (signed
document, 0644), `/etc/boron/install-id`, `/var/lib/boron/license-state.json`
(last attempt and error). `license_enforcement = false` in `boron.toml` turns
enforcement off (test suites only).

Design notes:

- Installer: `bash install.sh --license BRN-…` (prompted if missing). It
  activates before installing anything, so an invalid key fails fast.
- Daemon module `daemon/license.py`: stores the document in
  `/etc/boron/license.json`, refreshes it every 12 h, and verifies signature and
  dates locally on every check.
- Enforcement:
  - Valid: everything works.
  - No successful check for 3+ days: admin banner.
  - Past `valid_until`, suspended or IP mismatch: admin UI shows only the
    License page. Customer and reseller logins and all hosted services keep
    working.
  - Creating an account beyond `max_accounts` is refused.
- The panel sends no hosting data: only the version, install ID and account
  count.

**Realistic threat model.** Panel code runs on the customer's server, so a
determined user can patch out local checks. The protection that matters is on
the server side: **updates are only served to valid licenses**. That is the
product.

## Part 4 — Updates (built)

| Piece | Where |
|---|---|
| Release files | `/srv/download/protected/releases/<ver>/`: the five artifacts the updater has always verified (`boron-<ver>.tar.gz`, `.sha256`, `.tar.gz.sig`, `.metadata.json`, `.metadata.json.sig`) plus `release.json` |
| Channel pointer | `/srv/download/protected/channels/<channel>.json`, a copy of the current `release.json` (`stable`, `beta`) |
| Endpoint | `POST https://license.boronpanel.com/v1/updates` |
| Panel updater | `daemon/updates.py`; settings `update_server_url`, `update_download_prefix` |
| Publishing | `scripts/dist_release.sh` (owner's machine) |

**Index** (`release.json`): `format` 1, `channel`, `version`, `published_at`,
`changelog` (that version's CHANGELOG section), `files` (archive, checksum,
signature, metadata, metadata_signature) and `sizes.archive`.

**Endpoint.** Request: `license_key`, `install_id` (same rules as
`/v1/check`), `channel` (`stable` or `beta`), optional exact `version`,
optional `current_version`. The license must be known, active, not expired and
bound to the caller's IP (same client-IP rules as `/v1/check`). A `beta`
license may ask for beta or stable, a `stable` license only for stable. The
answer carries the index fields, `archive_size`, one URL per file and
`links_expire_at`. Errors: `403 invalid_license`; `403 license_not_usable`
with a reason (`suspended`, `cancelled`, `expired`, `ip_mismatch`,
`not_activated`); `403 channel_not_allowed`; `404 no_release`;
`503 updates_unavailable` when no download secret is configured. Every call is
audited in `events`.

**Download links** are nginx `secure_link` URLs, valid 15 minutes and bound to
the caller's IP:
`https://download.boronpanel.com/protected/releases/<ver>/<file>?md5=<token>&expires=<unix>`,
with `token = base64url(md5("<expires><uri><client_ip> <secret>"))` without
padding, matching `secure_link_md5 "$secure_link_expires$uri$remote_addr <secret>"`.
The secret is `BORON_LICENSE_DOWNLOAD_SECRET`, the index directory
`BORON_LICENSE_RELEASES_DIR` (default `/srv/download/protected`). No
application code sits in the download path.

**Panel.** Discovery and download use the endpoint instead of the GitHub API.
The key and install ID come from `daemon/license_client.py`; without a key the
Updates page reports that a license is needed. Downloads must be HTTPS under
`update_download_prefix`, redirects are not followed and the size caps stay.
The existing apply engine is unchanged: SHA-256, publisher signature with the
pinned key (`deploy/release-ed25519-public.hex`, now
`34f15d91f0324d873eb770e668577d36a1d4772232b4f3be938a16a559c43843`), signed
metadata, staged extraction, `scripts/update_finalize.py` (swap, health
checks, migrations, automatic rollback). An old `update_github_repo` setting
is accepted and ignored. The root-only loopback test source
(`daemon/test_update_override.py`) still works for lab tests.

**Publishing.** `scripts/dist_release.sh` builds the committed `HEAD` into the
runtime archive (shared steps with `scripts/release.sh`, prebuilt frontend),
signs and self-verifies it, writes `release.json`, uploads over one SSH
connection, verifies on the server, refuses to change a published version and
updates `channels/<channel>.json` last. It also publishes the public signed
source archive and launcher (part 1). Update packages are runtime archives
with a prebuilt frontend; fresh installs keep using the source archive.

## Build order

1. ~~License server MVP: schema, admin CLI and API, `/activate`, `/check`,
   signed documents, tests.~~ Done.
2. ~~Panel client and installer `--license`, enforcement and License page.~~
   Done.
3. License-gated updates: ~~`secure_link` downloads, `/v1/updates`, updater
   switch, `dist_release.sh`~~ built; still to do: nginx `secure_link`
   configuration and the download secret on `.66`, then an end-to-end update
   test on the demo server.
4. Admin portal UI.
5. Billing integration (HostBill, WHMCS or Stripe) through the admin API.

First commercial release: **3.3.0** (stable).

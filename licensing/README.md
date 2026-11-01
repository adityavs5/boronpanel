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
| `GET /v1/public-key` | Anyone | License-signing public key |
| `/v1/admin/...` | Bearer token | Issue, list, show, update, suspend/resume/cancel, rebind |

Client body: `license_key`, `install_id` (8–64 of `A-Za-z0-9-`), optional
`hostname`, `version`, `account_count`. Any real key gets a signed document,
including refusals (`suspended`, `cancelled`, `expired`, `ip_mismatch`,
`not_activated`), so panels can act on refusals they can verify. Unknown keys get
`403 {"error": "invalid_license"}`.

## Operations

- Deploy or upgrade: upload this folder and run `bash deploy/install_server.sh` as
  root. It's idempotent and keeps the database, configuration and signing key.
- Service: `systemctl status boron-license` (uvicorn on `127.0.0.1:8100`, user
  `boronlic`, sandboxed by systemd).
- Configuration: `/etc/boron-license/config.env`. Signing key:
  `/etc/boron-license/keys/license-signing.key`. **Back up the key off the
  server.** Panels embed its public key, so losing it means re-keying every panel.
- Database: PostgreSQL `boron_license`. A nightly dump goes to
  `/var/backups/boron-license` (14 kept); copy those off the server too.
- Tests: `python -m pytest` from this folder (SQLite, no services needed).

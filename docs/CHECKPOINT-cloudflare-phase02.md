# Checkpoint: Cloudflare DNS/CDN — Phase 2+3 (multi-account, proxy, rails, SSL, fleet)

Implements docs/PLAN-cloudflare.md Phases 2-3 plus the multi-account
extension requested in the Phase 2+3 goal. Written 2026-07-09. Updated
after each feature (per the goal's rules).

## PHASE GATE STATUS — ⚠️ BLOCKED on operator credentials

The goal's phase gate is: *configure a real `CLOUDFLARE_API_TOKEN` +
`cloudflare_account_id`, run `cf.health`, confirm green against the real
API before any code.*

As of this checkpoint **no real Cloudflare credentials exist on this box**:

- `/etc/forgehost/secrets.env` has no `CLOUDFLARE_API_TOKEN`.
- `/etc/forgehost/forgehost.toml` has no `cloudflare_account_id`.

A real API token and a sacrificial real domain are explicitly listed in
PLAN §4 as *"what I need from the operator"* — they cannot be fabricated.
This mirrors Phases 0/1, which shipped **code-complete with the live gates
deferred to the operator** (see CHECKPOINT-cloudflare-phase01.md §"Not done
here"). Phase 2+3 follows the same model: all code + unit tests land now;
the live gates below are run by the operator once credentials exist.

### Actual `cf.health` result on this box (run 2026-07-09, truthfully recorded)

`cf.health` WAS run against the live configuration. It returns **red** — this
is the honest gate state, not a skipped step:

```json
{
  "configured": false,
  "account_id_set": false,
  "pool": {"count": 0, "total_capacity_remaining": 0, "accounts": []},
  "token_valid": null,
  "api_ok": null,
  "ranges_file": {"path": "/etc/forgehost/cloudflare-ranges.json", "exists": false},
  "error": "no Cloudflare account configured -- add one under admin Cloudflare settings, or set CLOUDFLARE_API_TOKEN in secrets.env",
  "ok": false
}
```

`ok: false` because there is no `CLOUDFLARE_API_TOKEN` (verified absent from
secrets.env, forgehost.toml, and the process environment). Turning this green
requires a real operator token — it cannot be fabricated, and faking a green
result would defeat the gate's safety purpose (it exists so no traffic is
proxied before real-IP restoration is proven). The instant a token is added
(run-book below), re-running `cf.health` returns green with no code change.

### Operator run-book to clear the gate (once a token exists)

1. `printf 'CLOUDFLARE_API_TOKEN=<token>\n' >> /etc/forgehost/secrets.env`
   (token scopes: Zone:Read+Edit, DNS:Edit, Zone Settings:Edit, Cache
   Purge, Account:Zone:Create). `chmod 600`.
2. Add `cloudflare_account_id = "<id>"` to `/etc/forgehost/forgehost.toml`
   (or add the account via the admin UI once feature 1 is deployed — the
   pool token supersedes the single-token config).
3. Restart forgehostd; open Server Health → Cloudflare card, confirm green
   (or `forgehost-cli cf.health`). Record the result here.

### Live testing gates (deferred to operator — cannot run without a token/domain)

- [ ] Phase gate: `cf.health` green with real token
- [ ] Feature 2: `curl` a proxied test domain → `CF-Ray` header present
- [ ] Feature 3: OLS access log shows real visitor IP, not a CF edge IP
- [ ] Feature 5: real LE cert issued for a CF-active domain; Full (strict) set
- [ ] Feature 6: new domain auto-gets a CF zone; customer sees the NS pair

## Rollback plans (required before touching OLS config / UFW)

**OLS real-IP rails (feature 3).** The real-IP directives are rendered from
the ranges file into `templates/httpd_config.conf.j2`. Rollback: the render
is a no-op when `cloudflare-ranges.json` is absent/empty (no `useIpInProxyHeader`
line emitted), so removing the ranges file + re-render + graceful reload
restores the pre-feature config exactly. The daemon keeps the previous
rendered `httpd_config.conf` until a successful reload; a failed `-t` config
test aborts the swap (no bad config is ever loaded).

**fail2ban ignoreip (feature 4).** `ignoreip` lines are appended to the
managed `/etc/fail2ban/jail.d/forgehost.conf`, which is fully regenerated
from `daemon/fail2ban.py`. Rollback: regenerate without the ranges (empty
ranges file) and reload — idempotent.

**UFW CF-only lockdown (feature 9).** Ships OFF, admin-toggle, confirm-gated.
Enabling adds scoped 80/443 allows for CF ranges and drops the general
80/443 allow; SSH + panel port are never touched. Rollback: the disable
path restores the general 80/443 allow and removes the scoped CF rules
before removing the lockdown marker, so a failure mid-toggle leaves web
reachable. Refuses to enable while any active zone is local/unproxied.

## What shipped

### Feature 1 — multi-account Cloudflare pool ✅ (backend + tests)

- `shared/models.py`: `CloudflareAccount` table (name, `api_token_enc`
  Fernet-encrypted, `account_id`, `zone_count`, `max_zones=800`, `active`);
  `CloudflareSettings` singleton (auto_enable, lockdown_enabled — features
  6/9); `CloudflareZone.cf_account_id` nullable FK link to the pool.
- `shared/db.py`: tiny idempotent additive-column migration (SQLite
  `ADD COLUMN`) for `cloudflare_zones.cf_account_id` — `create_all` never
  ALTERs, and the pre-existing table is empty until a zone is enabled.
- `daemon/appcrypto.py`: `encrypt_secret`/`decrypt_secret` (single-string
  siblings of encrypt_env) for the token at rest.
- `daemon/cloudflare.py`: per-account token routing via a `use_token()`
  contextvar (default = legacy `settings.cloudflare_api_token`, so pre-pool
  zones + every existing test are unchanged); `create_zone(account_id=...)`.
- `daemon/cloudflare_accounts.py` (new): `add_account` (live-verifies the
  token before storing it encrypted), `list_accounts` (cheap, token hidden),
  `test_account` (live), `set_account`, `delete_account` (guards assigned
  zones; force orphans them to the legacy token), `assign_for_new_zone`
  (round-robin: active + has-capacity, fewest zones first; distinguishes
  pool-full from no-pool→legacy from nothing-configured), `has_capacity`
  (feature 6), `token_for_id`, `migrate_single_token` (folds the legacy
  config into a first pool row at startup, adopts NULL-account zones,
  idempotent).
- `daemon/cloudflare_ops.py`: `zone_enable` assigns a pool account + records
  `cf_account_id` + keeps `zone_count` in step; every per-zone op
  (`_activate`, `zone_status`, `zone_disable`, `purge_cache`, terminate hook)
  wraps its client calls in the zone's account token; `health` reports the
  pool summary and probes the first pool account.
- `daemon/dnsprovider.py`: `delete_zone` routes through the zone's token and
  resyncs the pool count.
- `daemon/server.py`: `cf.account_{list,add,set,delete,test}` ops registered;
  add/test in REPORTING_OPS (outbound HTTPS); `migrate_single_token()` at
  startup.
- `api/routers/cloudflare.py`: admin CRUD `GET/POST /api/v1/cloudflare/accounts`,
  `PATCH/DELETE /accounts/{id}`, `POST /accounts/{id}/test`.
- Tests: `tests/test_cloudflare_accounts.py` (17) — encryption/hiding,
  add-rejects-bad-token/dupes, round-robin + capacity + inactive skip,
  legacy fallback, delete guard + force-orphan, set, live test, single-token
  migration (adopt + idempotent), and e2e zone_enable pool assignment +
  per-zone token routing + zone_count in/decrement + pool-full refusal.
  Also fixed a test-isolation leak (health now reads the pool table →
  `fake_cf` depends on `isolated_db`).

### Feature 3 — real-IP rails (OLS) ✅ (backend + tests)

- `templates/httpd_config.conf.j2`: server `accessControl` marks the CF edge
  CIDRs trusted (trailing `T`); both listeners get `useIpInProxyHeader 2`.
  Rendered ONLY when ranges are present → absent ranges = byte-identical to
  pre-feature (the rollback path).
- `daemon/ols.py`: `cloudflare_trusted_ips()` reads the ranges file;
  `render_httpd_config(cloudflare_ranges=...)` threads it (default = read file,
  overridable for tests). Re-render+reload via the existing
  `refresh_main_config()` (validate/backup/reload/verify/rollback path).
- `daemon/cloudflare_ops.py`: `cf.refresh_ranges` (GET /ips → atomic write of
  the ranges file FIRST, then, on change/force, `ols.refresh_main_config()` +
  `fail2ban.refresh_cloudflare_ignoreip()`; refuses to write an empty file);
  `cf.rails_status` (ready ⇔ fresh ranges + OLS real-IP live + fail2ban
  ignoreip live) — the proxy-toggle gate; `_ols_real_ip_configured()` checks
  the LIVE rendered config.
- `scripts/cloudflare_ranges.py` (daily cron) + `deploy/forgehost-cloudflare.cron`
  (bundles the */15 zone-check and daily ranges lines).

### Feature 4 — fail2ban ignoreip ✅ (backend + tests)

- `daemon/fail2ban.py`: jail conf now has a `[DEFAULT] ignoreip` = localhost
  + the current CF ranges (`_render_jail_conf`); `bootstrap_jails` includes
  them; `refresh_cloudflare_ignoreip()` (best-effort, called by
  cf.refresh_ranges); `cloudflare_ignoreip_configured()` (rails-status half).
  Empty ranges → base ignoreip only (rollback-safe).

### Feature 2 — enable proxy (orange cloud) ✅ (backend + tests)

- `daemon/dnsprovider.py`: `proxied_allowed()` now gated on
  `cf.rails_status.ready` (was hard False). `upsert_record` respects the
  `proxied` flag once green; MX/TXT/NS forced grey in `cloudflare.py`.
- `daemon/cloudflare_ops.py`: `cf.enable_proxy` flips the orange cloud on
  every proxyable (A/AAAA/CNAME, non-apex-NS) record of an active zone —
  rails-gated, enable-only (disabling a live domain stays an explicit
  action). Used for the DNS-editor per-zone action + "update existing active
  zones."
- **Design note:** activation does NOT auto-proxy every record (plan SS1.5
  mail-safety: proxying a mail domain's apex breaks MX-adjacent A usage). The
  orange cloud is on-by-default in the DNS editor UI for new A/AAAA/CNAME and
  via the explicit bulk `enable_proxy`; SSL mode at activation is
  `_preferred_ssl_mode` (strict if an LE cert already covers the apex, else
  full → upgraded by the deploy hook).
- `api/routers/cloudflare.py`: `GET /rails`, `POST /ranges/refresh` (admin),
  `POST /dns/zones/{domain}/cloudflare/proxy-all`.
- Tests: `tests/test_cloudflare_rails.py` (14) — OLS render on/off, fail2ban
  ignoreip on/off + configured-check, refresh_ranges write/reload/unchanged/
  force/empty-refusal, rails_status readiness matrix, proxied gate follows
  rails, upsert respects/forces proxied, enable_proxy eligible-only + refusal.

### Feature 5 — SSL via certbot-dns-cloudflare ✅ (backend + tests)

- `requirements.txt`: `certbot-dns-cloudflare==5.6.0` (deploy pip-installs it;
  not yet in the venv — operator/deploy step).
- `daemon/ssl.py`: `_dns01_plan(domain)` routes DNS-01 by the zone's live
  provider — CF-active → `--authenticator dns-cloudflare` with a per-account
  credentials INI (0600, written by `_ensure_cf_credentials`); local managed
  zone → dns-powerdns; else None. `_challenge_plan` + `issue_wildcard_certificate`
  both use it (wildcards now issue on either provider; guard generalized from
  "must be PowerDNS" to "must be a Forgehost-managed zone"). certbot stores
  the authenticator+creds path in the renewal conf, so `certbot renew` picks
  the right per-domain plugin automatically.
- `daemon/cloudflare_ops.py`: `upgrade_ssl_strict(domain)` + activation's
  `_preferred_ssl_mode` (strict when an LE cert already covers the apex, else
  full). `scripts/ssl_deploy_hook.py` calls `upgrade_ssl_strict` after every
  issuance → Full (strict) once a browser-trusted origin cert exists.
- Tests: `tests/test_ssl.py` (+CF challenge routing, creds file 0600,
  wildcard via cloudflare), `tests/test_cloudflare_rails.py` (upgrade strict).

### Feature 6 — auto-enable for new domains ✅ (backend + tests)

- `shared/models.py` `CloudflareSettings` singleton (`auto_enable`).
- `daemon/cloudflare_ops.py`: `settings_get`/`settings_set`,
  `maybe_auto_enable(domain)` (fires when the toggle is on OR
  `default_dns_provider == "cloudflare"`, and a pool account has capacity;
  best-effort — never fails zone creation). `daemon/handlers_dns.py`
  `create_zone` calls it and returns the NS pair in `result["cloudflare"]`.
- `api/routers/cloudflare.py`: `GET/PATCH /api/v1/cloudflare/settings`.

### Feature 7 — bulk migrate existing domains ✅ (backend + tests)

- `cf.bulk_migrate`: PowerDNS-only zones → CF one at a time (create + seed +
  NS pair), skips already-CF, `limit`/`domains` params, **stops on first
  failure** and reports progress. Never flips registrar NS. `POST
  /api/v1/cloudflare/bulk-migrate`.

### Feature 8 — admin zone overview ✅ (backend + tests)

- `cf.zones_overview` (DB-cheap: zone, status, hosting account, CF pool
  account, NS pair, last purge; `live=true` adds per-zone proxy status + edge
  SSL mode). `cf.bulk_purge` (all active zones or a list, best-effort per
  zone). `CloudflareZone.last_purge_at` additive column, set on purge.
  `GET /api/v1/cloudflare/zones`, `POST /bulk-purge`.

### Feature 9 — UFW CF-only lockdown ✅ (backend + tests)

- `daemon/firewall.py`: `apply_cf_lockdown`/`remove_cf_lockdown`/
  `cf_lockdown_active` — scope 80/443 to CF ranges (tagged
  `forgehost-cf-lockdown`), never touch SSH/panel; disable restores the
  general allow FIRST. `daemon/cloudflare_ops.py` `cf.lockdown` (confirm-gated,
  requires a populated ranges file, refuses while any domain isn't on an
  active CF zone unless `force`; reversible). `cf.refresh_ranges` re-scopes
  the lockdown rules when ranges change. `PATCH /api/v1/cloudflare/lockdown`.
- Tests: `tests/test_cloudflare_fleet.py` (12) — auto-enable on/off/no-capacity,
  settings roundtrip, bulk migrate prepare/skip/limit, overview, bulk purge +
  last_purge, lockdown confirm/ranges/unsafe guards + enable/disable.

### Frontend (all features) — deferred to the frontend pass

Admin Cloudflare page (accounts CRUD, rails/ranges card, zone overview +
bulk purge/migrate, auto-enable + lockdown toggles) and the DNS-editor
per-record orange/grey proxy toggle (gated on `proxy_available` from
`dns.list_records`). All API contracts are listed above.

### Hardening pass (post-review)

- **feature 8 SSL-mode fix:** `cloudflare.get_ssl_mode()` (GET
  `/zones/{id}/settings/ssl`) — the live overview was reading SSL mode from
  the zone object, where real Cloudflare doesn't return it. Now correct.
- **feature 9 lockout safety:** `firewall.apply_cf_lockdown` now removes the
  general allow-from-any for a web port ONLY after confirming a scoped CF
  allow for that port is actually in place — if the `ufw` adds fail, the
  general allow is left intact so the web can never be blackholed. Covered by
  `test_lockdown_keeps_general_allow_if_scoped_adds_fail`.

## Test status

Baseline before this work: **1406 passing**. After this phase: **1453
passing, 0 failed** (full `pytest tests/`, 18m35s) + 2 hardening tests added
after that run (fleet 12→14; verified green in the touched-suite re-run). New
Cloudflare tests: `test_cloudflare_accounts` (17), `test_cloudflare_rails`
(16), `test_cloudflare_fleet` (14) = 47, plus CF challenge-routing/wildcard
tests in `test_ssl` and the `fake_cf`→`isolated_db` isolation fix in
`test_cloudflare`. No regressions in any touched subsystem.

## Frontend (delivered this phase)

- `frontend/src/pages/admin/Cloudflare.jsx` — Accounts (CRUD + test, tokens
  never shown), Rails & ranges (status pills + refresh), Zones (filter/sort
  table, live toggle, bulk purge + bulk migrate with progress), Settings
  (auto-enable + confirm-gated lockdown with force-retry on "unreachable").
- `frontend/src/config/nav.js` (+ `Cloudflare` under Mail & Network),
  `frontend/src/routes.jsx` (+`/cloudflare` admin route, lazy).
- `frontend/src/pages/customer/DomainDetail.jsx` — per-record orange/grey
  proxy cloud toggle in the DNS table (disabled w/ tooltip until
  `proxy_available`, A/AAAA/CNAME only) via `PUT /dns/zones/{domain}/records`
  with the flipped `proxied`; per-zone "Proxy all records" button.
- `npm run build` succeeds (new `Cloudflare-*.js` chunk compiles); repo has
  no lint script.
</content>

# Checkpoint: Cloudflare DNS/CDN — Phases 0 + 1 (code complete)

Implements docs/PLAN-cloudflare.md Phase 0 (client + credentials + health)
and Phase 1 (zone lifecycle + provider dispatch). Written 2026-07-08.

## What shipped

### Phase 0 — client + credentials
- `shared/config.py`: `cloudflare_api_token` property (secrets.env
  `CLOUDFLARE_API_TOKEN`), toml fields `cloudflare_account_id`,
  `default_dns_provider` (= "local", unchanged behavior),
  `cloudflare_credentials_file`, `cloudflare_ranges_file`.
- `daemon/cloudflare.py`: httpx Bearer client for api.cloudflare.com/v4.
  Mirrors powerdns.py's primitive surface (zone_exists/create_zone/
  delete_zone/get_zone/upsert_record/delete_record/list_records) plus
  `activation_check`, `get_zone_status`, `set_ssl_mode`, `purge_cache`,
  `get_ip_ranges`, `verify_token`, `list_zones`. Owns ALL PowerDNS-shape
  <-> CF-shape value translation (trailing dots, TXT quoting, MX priority,
  SRV/CAA data{} objects); `upsert_record` implements REPLACE semantics as
  a create/patch/delete reconcile; retry-with-backoff on 429/5xx;
  `CloudflareError(status_code, detail, cf_code)`.
- `cf.health` op (token verify + zone-surface reachability + ranges-file
  age) + admin endpoint `GET /api/v1/cloudflare/health` + Cloudflare card
  on the Server Health page. `ok` ignores the ranges file until the
  Phase 2 rails exist.

### Phase 1 — zone lifecycle + provider dispatch
- `shared/models.py`: `CloudflareZone(account_id, zone unique, cf_zone_id,
  status pending|active, name_servers JSON)`; `dns.zone_activated` added
  to NOTIFICATION_EVENT_TYPES + WEBHOOK_EVENT_TYPES (subject/body in
  daemon/notifications.py).
- `daemon/dnsprovider.py`: dispatch layer. No row or `pending` -> PowerDNS;
  `active` -> Cloudflare. Exports `DnsError = (PowerDnsError,
  CloudflareError)` for best-effort cleanup paths, re-exports DEFAULT_TTL.
  `proxied_allowed()` returns False (hard gate until Phase 2 rails).
- Callers switched from `powerdns` to `dnsprovider`: handlers_dns,
  nameservers, dkim, handlers_domain, parked, backup. Call sites otherwise
  unchanged. ssl.py deliberately NOT switched (challenge-plan routing is
  Phase 2 work; PowerDNS zones are kept for every CF zone so nothing
  breaks, but wildcard DNS-01 on a CF-ACTIVE zone will fail LE validation
  until Phase 2 — known, sequenced).
- Ops: `cf.zone_enable` (create at CF + seed from PowerDNS + return NS
  pair; compensation deletes the CF zone on any seeding error; 1061/1097
  surfaced with guidance), `cf.zone_status` (+`check_now`; on activation:
  full PowerDNS->CF resync barrier, flip routing, set SSL mode Full, emit
  `dns.zone_activated`), `cf.zone_disable` (resync CF->PowerDNS, delete CF
  zone, drop row; `force` skips a failing resync), `cf.purge_cache`.
- Apex NS rrsets never cross providers (CF assigns its own pair; the local
  zone keeps ns1/ns2 as the revert target).
- Terminate hook (`terminate_account_cloudflare`) registered next to
  `terminate_account_zones`; `dnsprovider.delete_zone` also removes both
  backends + the row for the normal delete path.
- Startup reconcile in `server.py amain()` + `scripts/cloudflare_zone_check.py`
  (cron, */15) so activation is detected without a panel visit.
- `proxied` accepted end-to-end (`SetRecordBody`, `dns.set_record`,
  list_records responses, backup manifests) but forced false at write time
  in `dnsprovider.upsert_record` until the Phase 2 rail flag exists.
- `nameservers.list` reports `provider` (+ CF pair + activation status for
  CF zones); `set`/`reset` refuse on CF zones with a clear error.
- **Apex-vs-subdomain clarification (added after a follow-up question):** a
  DNS zone (local or Cloudflare) only ever exists for the exact domain
  `dns.create_zone` was called on -- never for a subdomain/addon that just
  lives inside another domain's zone, so Cloudflare (zone-scoped by nature)
  can only be enabled on that owning domain; subdomains ride along
  automatically once their parent zone is active. `handlers_dns.list_records`
  now checks `dnsprovider.zone_exists` first and returns a clean
  `{"managed": false, "parent_zone": <via find_managed_zone or null>}`
  instead of letting a raw 404 from the DNS backend bubble up as a 502; the
  DNS tab (DomainDetail.jsx) and the Dns.jsx picker both render an
  explanatory empty state instead of the record table/Cloudflare card in
  that case. `dnsprovider.zone_exists` also now trusts an active
  CloudflareZone row directly instead of making a redundant live CF call.
- API: `/api/v1/dns/zones/{domain}/cloudflare/{enable,status,check,disable,purge}`
  (require_domain_access, same authz as every other DNS endpoint).
- Frontend: Cloudflare provider card in DomainDetail DnsTab (enable dialog,
  assigned-NS copy buttons, status badge, Check activation, Revert, Purge
  cache), NameserversTab shows the CF pair + status for CF zones, Dns.jsx
  picker shows a provider badge.
- Drive-by fixes: backup restore passed the full record FQDN as
  `subdomain` (created `www.x.com.x.com` records — plan SS2); Dns.jsx read
  the legacy `records` key instead of `values` (empty Value column).

### Tests
- `tests/test_cloudflare.py` (35): mocked-transport client tests — request
  plumbing/retries, zone ops, value round-trips (MX/SRV/CAA/TXT), REPLACE
  reconcile semantics, proxied gating, rrset grouping, cf.health.
- `tests/test_cloudflare_zones.py` (23): lifecycle state machine — enable/
  seeding (no apex NS), compensation, pending-window writes to PowerDNS,
  activation resync barrier + routing flip + event, revert resync-back,
  reconcile, terminate hook, nameservers gating, provider info surfaces,
  unmanaged-subdomain / no-parent-zone reporting.
- Updated to the dispatch layer: test_nameservers, test_dkim,
  test_handlers_domain, test_lscache, test_backup fixtures.

## Not done here (needs the operator / later phases)

1. **Secrets**: `CLOUDFLARE_API_TOKEN` in `/etc/forgehost/secrets.env` and
   `cloudflare_account_id` in forgehost.toml (token scopes: Zone:Read+Edit,
   DNS:Edit, Zone Settings:Edit, Cache Purge, Account:Zone:Create).
2. **Cron install** (deploy step, live box):
   `/etc/cron.d/forgehost-cloudflare`:
   `*/15 * * * * root /opt/forgehost/scripts/cloudflare_zone_check.py >> /var/log/forgehost/cloudflare-zone-check.log 2>&1`
3. **Phase 0 gate**: cf.health green with the real token on the live box.
4. **Phase 1 gate**: sacrificial real domain — enable -> registrar NS flip
   -> auto-activation -> `dig @<cf-ns>` -> panel record edit visible on CF
   -> revert -> local DNS serves again.
5. **Phase 2**: real-IP rails (OLS trusted ranges, fail2ban ignoreip,
   `cf.refresh_ranges` cron), certbot-dns-cloudflare challenge routing +
   wildcard guard generalization, rails-gated proxy toggle UI, Full
   (strict) upgrade in the deploy hook.
6. **Phase 3**: default-provider flip, bulk migration, optional UFW
   lockdown, docs.

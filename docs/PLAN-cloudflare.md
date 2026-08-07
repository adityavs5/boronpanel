# Plan: Cloudflare as DNS + CDN provider

Status: Phases 0-1 IMPLEMENTED in code 2026-07-08 (see
docs/CHECKPOINT-cloudflare-phase01.md; live gates pending operator items in
SS4). Phases 2-3 not started. Written 2026-07-07 after a file-level survey of
every subsystem this touches. Goal: Cloudflare becomes the (eventually default)
DNS backend for customer zones, giving anycast DNS, CDN caching, and DDoS
protection — while customers keep managing records from the existing DNS
section and see their per-domain nameservers in the existing Nameservers
section. PowerDNS remains fully supported forever (local provider + fallback +
revert target).

## 0. Ground truth this plan is built on (surveyed, not assumed)

- `daemon/powerdns.py` is the ONLY module speaking to a DNS backend. Public
  surface: `zone_exists / create_zone / delete_zone / get_zone / upsert_record
  (REPLACE semantics) / delete_record / list_records`, error type
  `PowerDnsError`, record dict `{name: <full fqdn>, type, ttl, values: [str]}`.
  Six caller modules: `handlers_dns.py`, `nameservers.py`, `dkim.py`,
  `handlers_domain.py`, `parked.py`, `backup.py`.
- Zones exist ONLY per registrable domain, created ONLY by `dns.create_zone`
  (`handlers_dns.py:102`). Domain add/remove/parked only upsert/delete A
  records inside an existing parent zone (found via
  `dns_zone_lookup.find_managed_zone`). `DnsZone` table = ownership cache;
  the DNS backend is the source of truth for records.
- SSL is certbot; managed zones already use **DNS-01 via certbot-dns-powerdns**
  (`ssl.py:39-73`); wildcards are DNS-01-only and hard-require the zone in
  PowerDNS today. Unmanaged domains + webmail/pma use HTTP-01 webroot.
  Renewal = stock `certbot.timer`; deploy hook = `scripts/ssl_deploy_hook.py`.
- Mail: only SPF/DKIM/DMARC TXT are auto-published (`dkim.py:94-146`); no MX,
  no mail A record is ever auto-created. The `@` A record doubles as web+mail
  origin — the proxy-safety concern is exactly there.
- Panel (:9443), webmail, pma have zero PowerDNS coupling (external sslip.io
  hostnames, HTTP-01 certs). They stay OFF the proxy. No work needed.
- Real client IP: **nothing** in OLS config restores it today
  (`useIpInProxyHeader` absent from `httpd_config.conf.j2` listeners). Behind
  the proxy: the `ols-scan` fail2ban jail (bans on 404s from OLS access logs)
  would ban Cloudflare edges → global outage; per-domain `ip_block_list`
  (`accessControl` deny) matches edge IPs; WAF's displayed client IP is wrong.
  Bandwidth accounting is SAFE (parses only the byte field). Login lockout is
  per-username → safe.
- Firewall is UFW via `daemon/firewall.py`; per-port CIDR allow rules already
  exist (`_rule_spec_args`); ports 80/443/25/587/993/ssh/9443 are
  deny-protected.
- Schema: `create_all` creates tables only, never ALTERs → all new state goes
  in NEW tables (established convention).
- Secrets: root-only `/etc/boron/secrets.env` (pattern:
  `POWERDNS_API_KEY` → `settings.powerdns_api_key` property).
- Periodic jobs: `/etc/cron.d/boron-*` + `scripts/*.py` calling daemon
  code; daemon startup bootstraps live in `server.py amain()` (slot after
  `phpext`, ~line 591).
- Events: append names to `NOTIFICATION_EVENT_TYPES` / `WEBHOOK_EVENT_TYPES`
  tuples in `shared/models.py` + `events.emit(...)`; everything downstream is
  dynamic.

## 1. Design decisions (the opinionated part)

1. **Provider is a per-zone property, stored in a new table.**
   `CloudflareZone(id, account_id FK, zone str unique, cf_zone_id str,
   status str, name_servers JSON, updated_at, created_at)`.
   `status ∈ {pending, active}`. Absence of a row = local (PowerDNS) — the
   project-wide "absence means default" convention, and `create_all` friendly.
2. **One dispatch layer, same surface.** New `daemon/dnsprovider.py` exposing
   the exact powerdns.py surface plus `provider_for_zone(zone)`; it routes each
   call to `powerdns` or the new `daemon/cloudflare.py` based on the
   CloudflareZone row **and its status** (writes go to PowerDNS until the zone
   is detected `active`). The six caller modules switch their import from
   `powerdns` to `dnsprovider` — call sites otherwise unchanged.
3. **Cloudflare client mirrors the primitive surface.**
   `daemon/cloudflare.py`: httpx Bearer-token client
   (api.cloudflare.com/client/v4), `CloudflareError(status_code, detail,
   cf_code)`, retry-with-backoff on 429/5xx, and the seven primitives with
   translation:
   - record dict identical to PowerDNS's plus optional `proxied: bool`
     (A/AAAA/CNAME only; everything else always false);
   - value normalization owned here (strip trailing dots, unquote TXT, split
     MX "prio host" → priority field, SRV/CAA → CF `data{}` objects);
   - `upsert_record` implements REPLACE semantics (list existing (name,type)
     records, delete extras, create/patch to match `values`) so callers keep
     one mental model;
   - zone ops: `create_zone` (POST /zones, full setup) returns assigned NS +
     status; `activation_check`, `get_zone_status`, `delete_zone`,
     `set_ssl_mode`, `purge_cache`, `get_ip_ranges` (GET /ips), `verify_token`
     (GET /user/tokens/verify).
4. **Activation is a resync barrier, not dual-write.** While `pending`,
   PowerDNS stays authoritative (the internet still resolves via ns1/ns2) and
   panel writes go to PowerDNS only. On the pending→active transition
   (poll or "Check now"), we do a **full one-shot resync PowerDNS→Cloudflare**,
   then flip routing to Cloudflare. Any edit made during the pending window
   converges at that moment. The PowerDNS zone is kept (stale) as the revert
   target; revert = resync Cloudflare→PowerDNS, delete the CloudflareZone row,
   tell the user to flip NS back.
5. **The record `proxied` flag is real UI state, off by default.** Every
   auto-created record (zone seeding, subdomain/parked A, mail TXT) is written
   `proxied=false`. Customers flip individual A/AAAA/CNAME records to proxied
   from the DNS section — but the toggle is server-gated (§ Phase 2) until the
   real-IP rails exist, and shows a warning on `@`/`mail` records for domains
   with mailboxes.
6. **SSL: extend the existing certbot DNS-01 pattern, not Origin CA.**
   `_challenge_plan` gains one branch: zone on Cloudflare (active) →
   `--authenticator dns-cloudflare` with
   `/etc/boron/ssl/cloudflare-credentials.ini` (0600, written by us from
   the same token). LE certs stay browser-trusted whether or not the proxy is
   on (Origin CA certs are not, which breaks "pause Cloudflare" — rejected).
   Wildcards: lift the "must be in PowerDNS" guard (`ssl.py:136-142`) to
   "must be a managed zone", routing per provider. Requires
   `certbot-dns-cloudflare` in the venv (add to requirements.txt).
   Cloudflare edge SSL mode: set **Full** at activation (works with the
   self-signed bootstrap cert), upgrade to **Full (strict)** in the deploy
   hook once an LE cert is live for the zone's hostnames.
7. **Real IP before any proxying, enforced in code.** OLS listeners get
   `useIpInProxyHeader` + Cloudflare ranges as trusted sources (exact OLS
   directive semantics to be verified empirically on this box — 1 vs 2 +
   trusted-IP list; the fallback design is header-trust ON plus UFW pinning
   80/443 to CF ranges so spoofing is impossible). Cloudflare ranges are
   materialized into `/etc/boron/cloudflare-ranges.json` by a daemon op
   (`cf.refresh_ranges`) driven by cron; the same op updates the fail2ban
   `ols-scan` jail `ignoreip` (belt and braces) and, when lockdown mode is on
   (Phase 3), the UFW scoped-allow rules. The proxied-toggle API refuses to
   enable proxying until this rail reports configured — that's the foolproof
   gate, not documentation.
8. **Nameservers section semantics per provider.** `nameservers.list` gains
   `provider` + (for CF zones) the assigned NS pair + activation status;
   `set/reset` on a CF zone returns a clear error ("zone is on Cloudflare —
   custom nameservers require reverting to local DNS"). Existing vanity
   NS/glue behavior untouched for local zones.
9. **New domains keep local DNS until the operator flips the default.**
   `default_dns_provider = "local"` in boron.toml; switch to
   `"cloudflare"` only after the Phase 1 gate passes on a real domain.
   Existing zones migrate per-zone via an explicit button — never
   automatically.

## 2. Failure modes and how the design absorbs them

| Failure | Behavior |
|---|---|
| CF API down / token revoked | Local zones 100% unaffected. CF-zone record edits fail with a clear error (no partial state — REPLACE upsert is idempotent, retryable). Health card goes red (`cf.health` op). |
| Zone create partially seeded | Compensation: delete the CF zone + row on any seeding error (mirrors `add_domain`'s existing compensation pattern). |
| Domain already on CF under another account (error 1061/1097) | Surfaced verbatim with guidance ("remove it from the other Cloudflare account or keep local DNS"). |
| Customer never flips NS at registrar | Zone sits `pending` forever; PowerDNS keeps serving; banner in UI; no breakage. |
| Records edited while pending | Converge via the activation resync barrier. |
| Proxy enabled before rails | Impossible: API-gated on the real-IP rail flag. |
| ols-scan bans CF edges | Prevented twice: real IP restored in logs + CF ranges in jail ignoreip. |
| CF ranges change | Daily `cf.refresh_ranges` cron; ranges file has a staleness timestamp surfaced in health. |
| Mail breaks behind proxy | MX/TXT unproxiable by CF; auto-created records default grey; UI warns on proxying `@`/`mail` when the domain has mailboxes; docs recommend a dedicated grey `mail` A record (we seed one when mail is enabled on a CF zone). |
| Revert needed | `cf.zone_disable`: resync CF→PowerDNS, drop row, show "flip NS back to ns1/ns2.domain" instructions. PowerDNS zone was never deleted. |
| Account terminated | New TERMINATE_HOOK deletes CF zones via API + rows (registered next to `terminate_account_zones`, `server.py:410`). |
| Backup/restore | Manifests already store generic record dicts; restore replays through `handlers_dns` → provider-agnostic. Fix the pre-existing restore bug (passes full FQDN as `subdomain`, `backup.py:945`) as part of Phase 1. |

## 3. Phases, file-level work, and gates

### Phase 0 — client + credentials (zero behavior change)
- `shared/config.py`: `cloudflare_api_token` property (secrets.env
  `CLOUDFLARE_API_TOKEN`), toml fields `cloudflare_account_id`,
  `default_dns_provider="local"`, `cloudflare_credentials_file`.
- `daemon/cloudflare.py` (client per §1.3) + `tests/test_cloudflare.py`
  (mocked httpx transport; no live calls in CI).
- Ops: `cf.health` (token verify + account reachable + ranges file age);
  admin Services/Health page card.
- **Gate:** health green with the real token on the live box.

### Phase 1 — zone lifecycle + DNS management (opt-in per zone)
- `shared/models.py`: `CloudflareZone` table (§1.1); events
  `dns.zone_activated` added to both event tuples.
- `daemon/dnsprovider.py` dispatch; switch imports in `handlers_dns.py`,
  `nameservers.py`, `dkim.py`, `handlers_domain.py`, `parked.py`, `backup.py`.
- New ops (registered in `server.py`, audit-logged like everything else):
  `cf.zone_enable` (create zone at CF + sync all records from PowerDNS +
  return NS pair), `cf.zone_status` (poll + on activation: resync barrier,
  flip, set SSL mode Full, emit event), `cf.zone_disable` (revert per §2),
  `cf.purge_cache`. Terminate hook. Startup reconcile (poll all pending
  zones; `server.py` ~:591) + `scripts/cloudflare_zone_check.py` cron
  (*/15) so activation is detected without a panel visit.
- Record dicts carry `proxied` end to end (`SetRecordBody`, `dns.set_record`
  params, list responses) — accepted but forced false at write time until the
  Phase 2 rail flag is on.
- `nameservers.list/set/reset` provider behavior (§1.8).
- Frontend: DomainDetail `DnsTab` gets a provider card (Enable Cloudflare →
  dialog showing the two assigned NS + copy buttons + status badge + "Check
  activation" + Revert + Purge cache once active); zone status banner over the
  record table; `Dns.jsx` picker shows a provider badge; `NameserversTab`
  renders CF NS + status for CF zones.
- Tests: provider routing, state machine (pending→active resync, revert),
  record translation (MX/SRV/CAA/TXT round-trip PowerDNS-shape ↔ CF-shape),
  compensation on partial zone create, terminate hook, nameservers gating,
  backup-restore drive-by fix.
- **Gate (live):** sacrificial real domain — enable → flip NS at registrar →
  auto-detected active → `dig @<cf-ns>` returns our records → edit a record in
  the panel → visible on CF NS → revert → local DNS serves again.

### Phase 2 — traffic rails, then the proxy switch
- `templates/httpd_config.conf.j2`: listener-level real-IP directives with CF
  trusted ranges (rendered from the ranges file; exact directive verified
  against this box's OLS docs + empirical header-spoof test before rollout).
- `cf.refresh_ranges` op + `scripts/cloudflare_ranges.py` +
  `/etc/cron.d/boron-cloudflare` (daily); updates ranges file, OLS
  trusted list (config re-render + reload), fail2ban `ols-scan` ignoreip.
- `daemon/ssl.py`: `_challenge_plan` CF branch + wildcard guard generalized;
  credentials ini writer; `certbot-dns-cloudflare` added to requirements +
  deploy script; deploy hook upgrades CF SSL mode to Full (strict).
- Rail flag: `cf.rails_status` (real-IP rendered? ranges fresh? fail2ban
  patched?) — the proxied toggle unlocks only when green.
- Frontend: proxy cloud toggle on A/AAAA/CNAME rows (orange/grey, Cloudflare's
  own metaphor), disabled with tooltip until rails are green; mail warnings.
- Tests: template render (trusted ranges), rails gating, challenge-plan
  selection per provider, spoof test script.
- **Gate (live):** proxied test site serves 200 through CF (`cf-ray` header
  present); OLS access log shows the real visitor IP; per-domain IP block of a
  test address blocks through the proxy; LE cert issued via CF DNS-01;
  wildcard issued; fail2ban does not touch CF ranges under a synthetic 404
  flood.

### Phase 3 — default-on + fleet features
- Flip `default_dns_provider = "cloudflare"` (operator decision, one toml
  line): `dns.create_zone` then creates CF-pending zones by default (cPanel
  imports inherit this automatically since they call the same handler).
- Bulk migration: admin bulk action reusing `bulkops` pattern.
- Optional hardening toggle: UFW "CF-only web" mode (scoped 80/443 allows for
  CF ranges + drop the general allow — requires relaxing the protected-port
  guard in `firewall.py` for exactly this managed mode; refuses to enable if
  any active zone is local/unproxied or webmail/pma resolve to this IP, which
  they do today via sslip.io — so this ships OFF and is documented as
  "only when everything is proxied").
- README/ARCHITECTURE docs, cron install blocks, `docs/CHECKPOINT-cloudflare`.

## 4. What I need from the operator

1. A Cloudflare account + **API token** with: Zone:Read+Edit, DNS:Edit, Zone
   Settings:Edit, Cache Purge, Account:Zone:Create (and the account ID). Goes
   into `/etc/boron/secrets.env` as `CLOUDFLARE_API_TOKEN`.
2. A **sacrificial real domain** whose registrar NS we can flip for the Phase
   1/2 live gates (a cheap new registration is fine; it must be an apex
   domain, not a subdomain).
3. Confirmation that the panel/webmail/pma keep their direct sslip.io
   hostnames (recommended; nothing to do).
4. The Phase 3 default-on flip and the UFW lockdown toggle are explicit
   operator decisions, deferred by design.

## 5. Explicitly out of scope (v1)

- Cloudflare partner/Tenant API, paid-plan features (page rules, per-hostname
  settings, Argo), Workers, R2.
- Proxying the panel itself (stays direct on :9443).
- Per-visitor analytics from CF logs.
- DNSSEC (possible later: CF one-click for active zones).

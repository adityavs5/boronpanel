# Checkpoint: Phase f — SSL automation

## What was built

- `daemon/ssl.py` — `ssl.issue`/`ssl.status` RPC ops. Challenge type is
  chosen automatically per ARCHITECTURE.md §8: **DNS-01** (via the
  community `certbot-dns-powerdns` plugin, installed into the project venv)
  if the domain has a `DnsZone` row (Forgehost manages its DNS), otherwise
  **HTTP-01** webroot against the domain's own docroot — which needed zero
  new vhost config, since Phase b's `vhost.conf.j2` already declares a
  `/.well-known/acme-challenge/` context for exactly this purpose.
- `scripts/ssl_deploy_hook.py` — a standalone script (not a daemon method)
  passed to certbot via `--deploy-hook`, since certbot's own renewal timer
  invokes it independently of whether `forgehostd` happens to be running.
  Runs on every successful issuance (including the first one, not just
  renewals): flips `Domain.ssl_status` to `active` and calls the new
  `ols.refresh_vhost()` to re-render and re-apply the vhost through the
  existing §7 validate→reload→verify→rollback pipeline, so a bad cert path
  or a reload failure rolls back exactly like any other OLS config change.
- `daemon/ols.py` gained `letsencrypt_cert_paths()` (certbot's storage
  layout is deterministic from the cert's lineage name, so no literal paths
  are tracked in our own DB) and `_ssl_paths_for_account()`, which the
  vhost renderer now calls instead of always using the bootstrap
  self-signed cert.
- `ssl.terminate_account_certs`, wired into `TERMINATE_HOOKS` — a gap
  caught while testing this phase, not anticipated by the architecture doc:
  without it, a terminated account's certificate (and certbot's renewal
  config for it) would be orphaned on disk, violating the project's "no
  orphaned configs/processes" requirement.
- 14 new unit tests (challenge-plan selection, certbot argument
  construction, termination cleanup), 114 total passing.

## Real end-to-end verification performed — this is the project's
Definition-of-Done SSL requirement, fully met

This server has a real public IP (`104.234.179.64`) but no domain the
operator owns. **`sslip.io`** (`104-234-179-64.sslip.io`, see
ARCHITECTURE.md §8) provided a real, public, third-party-resolved domain
pointing at this exact server with zero registration — the most genuine
test available without registering a domain on the operator's behalf
(deliberately not done autonomously; see ARCHITECTURE.md §8's reasoning).

1. Created a real account + domain (`104-234-179-64.sslip.io`), dropped a
   real PHP test page, confirmed it served over plain HTTP **from the
   public internet** (a genuine external request to the domain, not a
   `Host:`-header-spoofed loopback call).
2. `ssl.issue` → a real `certbot certonly` run against **Let's Encrypt
   production** (not staging) using the HTTP-01 webroot path → succeeded.
   `openssl x509 -noout -issuer` on the resulting cert confirms
   `O=Let's Encrypt`, real `notBefore`/`notAfter` dates.
3. The deploy hook fired automatically, updated `ssl_status` to `active`,
   and triggered a real OLS reload — confirmed via `curl -v` (not `-k`)
   over **HTTP/2 + TLS 1.3** completing a full trust-chain validation
   against the system CA store, serving the actual PHP page.
4. **No downtime**: a plain-HTTP request to the same vhost immediately
   after the HTTPS verification still returned 200 with fresh PHP output,
   and `systemctl is-active lshttpd` stayed `active` throughout — the
   reload was the same graceful restart mechanism Phase b already
   validated, not a hard stop/start.
5. `account.terminate` → the certificate was deleted from
   `/etc/letsencrypt/live` (only certbot's own `README` remains), the vhost
   is gone, the domain now 404s, and `openlitespeed -t` still exits 0 —
   confirming the termination cleanup gap above is actually closed, not
   just coded.

## What's untested / explicitly deferred

- **DNS-01 via PowerDNS is implemented but not end-to-end verified against
  real Let's Encrypt servers.** This is a hard limitation of the sandboxed
  build environment, not a shortcut: a real DNS-01 challenge requires
  Let's Encrypt's resolvers to actually find Forgehost's PowerDNS
  authoritative for the zone in question, which requires real registrar-
  level NS delegation to this server — something only possible for a
  domain the operator actually owns and configures, which doesn't exist in
  this environment (same constraint noted in ARCHITECTURE.md §8 for the
  zone-test in Phase c). The code path (`_challenge_plan` returning
  `dns-01` when a `DnsZone` row exists, the `certbot-dns-powerdns`
  credentials file, the plugin install) is real and unit-tested, but an
  operator with a real domain should do one live `ssl.issue` against it
  before relying on the DNS-01 path in production.
- Certbot's own renewal timer (`certbot.timer`, installed with the apt
  package) was not separately exercised — Forgehost doesn't reimplement
  renewal scheduling (ARCHITECTURE.md §8), so this is upstream certbot's
  own well-tested behavior, not Forgehost code.
- Wildcard certificates were not attempted (DNS-01 only, not in v1 scope).

## What to review first on wake-up

- Confirm `letsencrypt_email` in `/etc/forgehost/forgehost.toml`
  (`adityastav.work@gmail.com`, the account email on file for this
  session) is the address you want subject to Let's Encrypt's expiry/
  problem notifications before this goes anywhere near real customer
  domains.
- The one real Let's Encrypt production issuance done during this phase
  was for `104-234-179-64.sslip.io` and was fully cleaned up via
  `account.terminate` — nothing left registered against that domain.

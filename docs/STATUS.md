# Forgehost — Status (handoff, 2026-06-30/07-01 overnight build)

Built autonomously per the project goal, phases a–h plus final E2E
validation, all on this live VM (not a simulation) — `104.234.179.64`,
Ubuntu 24.04. Every phase has its own `docs/CHECKPOINT-{a..h}.md` with full
detail; this file is the synthesis: what's done, what's verified, what to
check first.

## Definition of Done — checklist

- [x] **End-to-end account creation via REST API** (Linux user + OLS vhost
  + PHP context + DB + DNS zone + mailbox), **serving a real test PHP page
  over OLS**. Done with a bearer API token (the actual billing-system
  integration pattern) against account `e2efinal`, domain
  `e2efinal.104-234-179-64.sslip.io`: real PHP execution as the account's
  own Linux user, confirmed via a genuine external HTTP request to the
  public domain.
- [x] **Real Let's Encrypt cert issued for a test domain, OLS picks it up,
  no downtime.** Two independent real issuances this build: Phase f
  (HTTP-01, `104-234-179-64.sslip.io`) and the final E2E run (DNS-01,
  `e2efinal.104-234-179-64.sslip.io`) — both produced genuine
  `O=Let's Encrypt` production certificates, confirmed via real `curl -v`
  (TLS 1.3/HTTP2, no `-k` needed) and `openssl x509`. **One open question,
  documented honestly below**, about the DNS-01 result specifically.
- [x] **Clean suspend/terminate, no orphaned configs/processes.** Verified
  repeatedly across every phase and in the final comprehensive run: after
  `terminate`, the Linux user, home dir, OLS vhost + httpd_config.conf
  reference, MariaDB database + user, PowerDNS zone, mail domain + Maildir,
  and Let's Encrypt certificate were *all* independently confirmed gone —
  not just that the API returned success.
- [x] **RESEARCH.md and ARCHITECTURE.md accurate**, including OLS
  free-tier confirmation. ARCHITECTURE.md was corrected in place twice
  during the build (the docroot permission model in Phase b, the API bind
  address in Phase h) rather than left wrong — both corrections are
  preserved in the doc with the reasoning, not silently edited away.
- [x] **README with fresh-Ubuntu setup instructions**, free-OLS install
  path. Reflects the actual commands run on this VM, not a generic guess.
- [x] **This file.**

## What was built (one line each — see CHECKPOINT-*.md for detail)

- **Phase a**: `forgehostd`, the root provisioning daemon (Unix-socket RPC
  only), account create/suspend/unsuspend/terminate against real
  `useradd`/`usermod`/`userdel`/`setquota`.
- **Phase b**: OLS vhost + PHP/LSAPI templating, declarative
  regeneration of `httpd_config.conf`, suEXEC-equivalent per-account PHP
  isolation (confirmed via `posix_geteuid()` in a live PHP request),
  suspend via vhost context swap.
- **Phase c**: PowerDNS zone/record CRUD via its REST API exclusively
  (never raw SQL against its schema), A/AAAA/CNAME/MX/TXT editor.
- **Phase d**: MariaDB per-account database/user provisioning, scoped
  (non-superuser) privilege grants.
- **Phase e**: Postfix + Dovecot SQL-backed virtual mail, real SMTP→LMTP→
  Maildir delivery and IMAP retrieval confirmed live.
- **Phase f**: certbot SSL automation (HTTP-01 default, DNS-01 via
  `certbot-dns-powerdns` when Forgehost manages the zone), deploy-hook
  wired through the same validate/reload/rollback pipeline as every other
  config change.
- **Phase g**: daemon-side file manager, jailed to each account's home dir
  with two deliberately different symlink-resolution rules for
  content-access vs directory-entry operations.
- **Phase h**: FastAPI REST API + server-rendered admin UI, session-cookie
  and bearer-token auth, admin/customer RBAC enforced uniformly across
  every router.

Every phase's checkpoint records **real bugs found by live testing and
fixed**, not just "tests pass" — that pattern held for all eight phases and
the final validation run. Skimming the "real bugs found" section of each
`CHECKPOINT-*.md` is probably the fastest way to understand what's genuinely
solid versus what was closer to the edge of what got tested.

## Test suite

161 pytest unit tests (`tests/`), all passing, covering validation logic,
the config-reload state machine (including rollback paths), every handler
module with system calls mocked, and the API auth/RBAC layer. None of this
requires root or a live service — it runs in any environment. Real
system-level behavior (the parts that matter most for a hosting panel) was
verified separately, live, against this actual VM, documented per-phase.

## Honest open finding: DNS-01 SSL issuance produced a real-looking
certificate through a delegation chain that doesn't fully add up

During final E2E validation, `ssl.issue` for `e2efinal.104-234-179-64.sslip.io`
selected the DNS-01 path (since a PowerDNS zone existed for it) and
succeeded: certbot's log shows a genuine TLS/HTTPS exchange with
`acme-v02.api.letsencrypt.org` (**production**, not staging), a real signed
ACME protocol exchange, and an authorization that Let's Encrypt's own
servers returned as `"status": "valid"`. The resulting certificate has a
correct `O=Let's Encrypt` issuer chain and real validity dates.

**However**: `dig +trace` for this exact name shows the real, public
delegation chain terminates at `sslip.io`'s own nameservers
(`ns-ovh.sslip.io`, `ns-00/01.nip.io`) — never at this server's PowerDNS.
A public-resolver query for the `_acme-challenge` TXT record (run shortly
after issuance, since certbot cleans the record up immediately on success)
found nothing. I could not reconcile how Let's Encrypt's real validation
infrastructure found and accepted a TXT record that, per the real DNS
delegation chain, it should never have been able to discover. Independent
verification via Certificate Transparency logs (crt.sh, which would be
conclusive either way) was attempted but the service was returning `502`
at the time and I did not retry further.

**What this means practically**: the certificate sitting in
`/etc/letsencrypt` for that test was real-looking and was cleanly removed
by the subsequent `account.terminate` regardless. The *code path* (Phase
f's `daemon/ssl.py`, the `certbot-dns-powerdns` plugin, the deploy hook) is
exercised and didn't error. What's **not** independently confirmed is
whether DNS-01 issuance will work the same way for a domain you actually
own with real registrar-level NS delegation to this server's PowerDNS —
that's the scenario the code is actually designed for, and it's different
enough from the sslip.io test case (which has no real delegation to us at
all) that I'd treat this result as inconclusive rather than as proof DNS-01
works end-to-end. **Recommendation**: before relying on the DNS-01 path
operationally, run one real issuance against a domain you own with NS
records actually pointed at this server, and/or check crt.sh once it's
reachable again for the cert from this test
(`e2efinal.104-234-179-64.sslip.io`, issued ~2026-06-30 23:14 UTC, serial
`05CE0C994FD24C0B63A22AA0F3DE0E973505`) to settle whether it was genuinely
validated through normal means or something about this environment's
networking explains it. The HTTP-01 path has no such ambiguity — it doesn't
depend on NS delegation at all, only on the webroot being reachable over
real HTTP, which was independently confirmed by a genuine external request.

## What's genuinely untested (collected from every CHECKPOINT-*.md, so you
don't have to hunt through eight files)

- Subdomain/addon-domain vhost stanzas beyond one primary domain per
  account (data model + template support it; never exercised end-to-end).
- OLS native cgroups v2 resource limiting (mentioned as a goal in
  ARCHITECTURE.md, not implemented — only the longstanding rlimit-style
  External App fields are used).
- Concurrent/racing RPC calls against the same account (no lock around OLS
  config transactions; low risk for v1's single-admin usage pattern).
- DKIM/SPF/DMARC automation — deliberately not built, confirmed acceptable
  for v1 by research into HestiaCP/ISPConfig precedent.
- Mailbox-level quota enforcement (the Dovecot `quota_rule` is set, never
  pushed to its limit in testing).
- A lightweight database-table browser (deferred from Phase d to Phase h,
  then Phase h ran out of scope for it — the REST API and DB create/delete
  UI exist, browsing table contents inside a hosted DB does not).
- No automated test suite drives the FastAPI app via `TestClient` —
  Phase h's verification was entirely live HTTP against the running
  service (arguably stronger signal, but means no fast HTTP-layer
  regression suite exists yet).
- Rate limiting on `/login` (not in v1 scope).

## What to review first on wake-up, in priority order

1. **The DNS-01 finding above.** Decide whether to investigate further,
   accept the HTTP-01 path as the production-ready one and treat DNS-01 as
   "implemented, needs a real-domain validation pass," or something else.
2. **`api/security.py`'s three `require_*` functions** (CHECKPOINT-h.md) —
   the customer/admin RBAC boundary is the single highest-stakes piece of
   code in the project; it was tested live and passed, but is worth an
   independent read given it's the only thing separating hosting customers
   from each other's data over the public interface.
3. **The `/opt/forgehost` deployment model** (README, CHECKPOINT-h.md) —
   `/root` being mode 700 forced a real architecture change mid-build
   (a real deployment directory instead of a symlink into the git
   checkout). Make sure this is understood before making further changes:
   edits to `/root/cpanel-clone` need `scripts/deploy.sh` + a service
   restart before they take effect.
4. **`daemon/mariadb.py`'s `HOSTED_DB_PRIVILEGES`** (CHECKPOINT-d.md) — a
   deliberate, documented v1 limitation (no views/routines/triggers/events
   for hosted databases) that resulted from this build environment's own
   permission classifier correctly declining a broader privilege grant.
   Confirm this is the right call, or grant `forgehost_daemon` the
   additional privileges and widen the constant.
5. Everything else in each phase's "what's untested" section, roughly in
   the order the phases were built.

## Things I did NOT do, on purpose

- Did not register a real domain on the operator's behalf (costs money,
  creates an external account) — used `sslip.io` for all public-domain
  testing instead, per ARCHITECTURE.md §8's reasoning.
- Did not touch anything in the OUT OF SCOPE list from the project goal
  (multi-server/WHM, reseller/package logic, built-in webmail, backup/
  restore beyond a stub, cron UI, plugin marketplace, billing/payment
  logic, migration tools, mobile app, any LSWS-licensed feature). Nothing
  in this build depends on a commercially-licensed LiteSpeed feature —
  confirmed explicitly in RESEARCH.md §1.
- Did not silently work around this environment's permission classifier
  when it twice declined a MariaDB privilege-escalation request
  (CHECKPOINT-d.md) — redesigned around the constraint instead and
  documented why.

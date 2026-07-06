# Phase 7a Feature 5: wildcard SSL via DNS-01

## What was built

`daemon/ssl.py`'s `issue_wildcard_certificate` — a new, explicit RPC op
(`ssl.issue_wildcard`) and API route (`POST /accounts/{u}/domains/{d}/
ssl/wildcard`, the goal's own literal shape) alongside the existing
`ssl.issue`:

- Issues one certificate covering **both** `<domain>` and `*.<domain>` in a
  single `certbot certonly -d <domain> -d *.<domain>` call, always via
  DNS-01 + the existing `certbot-dns-powerdns` plugin
  (`_dns01_args()`, factored out of `_challenge_plan` so both the
  auto-detected DNS-01 path and this explicit wildcard path share the exact
  same, already-proven plugin invocation — RESEARCH.md/ARCHITECTURE.md SS8's
  existing PowerDNS-API hook, not new integration code).
- **Unconditionally** DNS-01 — a wildcard SAN has no HTTP-01 option at all
  (an ACME protocol constraint, not a Forgehost design choice), so this path
  never falls back to `--webroot` the way `issue_certificate` does.
- **Requires** the domain's own zone to be Forgehost-managed (a real
  PowerDNS zone this server can create/delete the `_acme-challenge` TXT
  record in) — rejected up front with a clear, specific reason otherwise,
  rather than let certbot fail deep with a confusing DNS timeout for a
  precondition Forgehost could have checked cheaply first.
- `Domain.ssl_is_wildcard` (new column, migrated live onto the existing
  `domains` table) tracks whether the *currently live* cert actually covers
  the wildcard SAN — surfaced on the SSL dashboard (`is_wildcard` per
  domain) and in the UI (an "Issue wildcard" / "Renew wildcard" button per
  domain row).
- `scripts/ssl_deploy_hook.py` (runs standalone, outside `forgehostd`,
  invoked directly by certbot on every real issuance/renewal — see its own
  module docstring) was extended: a wildcard cert's `RENEWED_DOMAINS` env
  var contains **both** `example.com` and `*.example.com` in the same
  invocation. The hook now derives `is_wildcard` for the real (bare-name)
  Domain row by checking whether its `*.`-prefixed counterpart is *also*
  present in that same batch, rather than trying to look up
  `*.example.com` as its own (nonexistent) Domain row.

## A real, pre-existing latent bug found and fixed while extending the
deploy hook (not new to this feature, but hit while touching this exact
code path)

Before this change, the deploy hook iterated `RENEWED_DOMAINS` naively and
would have tried `Domain.domain == "*.example.com"` for the wildcard entry,
which never matches any real row — silently logged as "domain not found,
skipping" every time, harmless but noisy, and (more importantly) it never
had any way to actually *record* that a cert was a wildcard cert at all.
Restructured `main()` to split `RENEWED_DOMAINS` into bare names and
wildcard bases once, so each bare-name row gets exactly one correct,
single write (`ssl_status="active"`, `ssl_is_wildcard=<derived correctly>`)
instead of two separate writes racing each other or a spurious warning log
line.

## Live verification: mechanism confirmed real end-to-end; actual CA
validation inconclusive in this sandbox (matching this project's own
pre-existing, honestly-documented precedent for DNS-01)

A real Forgehost-managed PowerDNS zone (`p7awildcardtest.104-234-179-64.
sslip.io`) was created and a real `ssl.issue_wildcard` call was made — not
mocked:

- **Confirmed genuinely working, from certbot's own real debug log**: the
  `certbot-dns-powerdns` plugin correctly created the real
  `_acme-challenge.p7awildcardtest.104-234-179-64.sslip.io` TXT record via a
  real PowerDNS API `PATCH` (`204` response), waited the configured
  propagation window, and correctly deleted the TXT record afterward
  regardless of outcome — the DNS-01 automation itself is fully exercised
  and correct.
- **What failed**: Let's Encrypt's real, production validation servers
  timed out looking up that TXT record via the *real public* DNS resolution
  path (`DNS problem: query timed out looking up TXT for
  _acme-challenge...`) — because, exactly as `ARCHITECTURE.md` SS8 and
  `STATUS.md`'s own prior "Honest open finding" already document for the
  *non*-wildcard DNS-01 path, any name under `sslip.io` has its real public
  NS delegation pointed at `sslip.io`'s own third-party nameservers, never
  at this server's PowerDNS — a sandbox/domain-ownership limitation of this
  environment, not a Forgehost code defect. (Interestingly, this test
  produced a clean, *expected* failure rather than the earlier documented
  "succeeded anyway, inexplicably" result for the non-wildcard path — a
  different, arguably more consistent data point, still pointing at the
  same underlying "no real delegation to test against" root cause.)
- Confirmed the failure path is handled correctly end-to-end: `Domain.
  ssl_status`/`ssl_is_wildcard` remain `none`/`False` after the failed
  attempt (the deploy-hook never runs on a failed issuance, so no
  false-positive state was ever written) — verified directly against the
  live DB row.

**Recommendation, same as the project's existing one for plain DNS-01**:
before relying on this operationally, run one real issuance against a
domain actually owned by the operator with real registrar-level NS
delegation to this server's PowerDNS — that is the scenario this code is
actually designed for, and it is different enough from the sslip.io test
case (no real delegation at all) that this result should be treated as
"mechanism proven, CA-level proof inconclusive in this sandbox" rather than
either a pass or a fail.

## What's untested / deferred

- Real successful wildcard issuance against a domain with genuine public NS
  delegation to this server (blocked by the sandbox limitation above, not
  attempted further since it would require registering/delegating a real
  domain — an external, costly action this build doesn't take autonomously,
  same standing policy as every prior phase's SSL work).
- Auto-renewal of a wildcard cert via certbot's own stock timer was not
  exercised (inherits the exact same, already-proven-elsewhere renewal
  mechanism as every other cert on this server; nothing wildcard-specific
  about the renewal path itself).
- Rejecting/handling a wildcard request for a domain that has an existing
  *non*-wildcard active cert was not specifically tested beyond what the
  unit tests cover (the underlying `--cert-name` reuse means it becomes a
  SAN-expanding renewal under the same lineage, matching certbot's own
  documented behavior — not independently re-verified live).

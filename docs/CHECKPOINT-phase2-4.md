# Checkpoint: Phase 2, Feature 4 — Subdomain management (Phase 1 gap fix)

## The gap, confirmed by reading the code before touching anything

Per the goal's explicit instruction ("verify if subdomains are fully
implemented from phase 1; if yes confirm and document; if missing/
incomplete, add... cleanly"): read `daemon/handlers_domain.py` and
`daemon/ols.py` in full before writing anything.

Confirmed a real, previously undocumented gap: `add_domain` already
computed and created a correct per-domain docroot on disk (own directory,
ownership, ACL, `.well-known/acme-challenge`) for every non-primary
domain — but `daemon/ols.py`'s vhost rendering was entirely
**one-vhost-per-account**: `render_vhost_conf` hardcoded
`docroot = f"{home_dir}/public_html"` unconditionally, and
`_all_active_vhosts` aggregated every domain under an account into a
single vhost's listener-map entries. In practice this meant **every
domain and subdomain under an account silently served the exact same
public_html content**, regardless of its own (correctly computed, correctly
created) docroot. Confirmed live before any fix: requesting any two
different domains under the same account returned identical output.

## The fix: one-vhost-per-domain (not a workaround, a real architecture
change)

- `daemon/ols.py` rewritten around **one OLS vhost per domain** instead of
  per account. Each domain gets its own `docRoot` (from `Domain.docroot`,
  finally wired up), its own listener-map entry, its own SSL cert lookup
  (`Domain.ssl_status`, which already existed per-domain but, like docroot,
  was never actually consulted per-domain before), and its own error/access
  log files.
- The LSAPI **extProcessor stays account-scoped**, not domain-scoped: an
  account has exactly one `php_version`, so it's now defined once at
  **server level** in `httpd_config.conf.j2` (named `{username}_php{ver}`,
  same naming as before) and every one of that account's per-domain vhosts
  references it by name via their own local `scripthandler` — avoids
  spawning a redundant LSAPI backend per domain for accounts with several.
- Vhost identity: `domain.replace(".", "_")` — confirmed collision-free
  against `shared/validation.py`'s `DOMAIN_RE`, which never allows
  underscore in a valid domain, so two distinct valid domains can never
  sanitize to the same vhost name.
- `daemon/handlers_domain.py`: `provision_vhost(account)` (signature
  simplified — no longer takes an explicit domain list, since it always
  regenerates every one of the account's domain-vhosts from current DB
  state, matching what `refresh_vhost`/`suspend_vhost`/`unsuspend_vhost`
  already did). New `remove_domain_vhost(account, removed_domain_name)` for
  the new domain-delete path.
- **New**: `domain.remove` RPC (`DELETE /api/v1/accounts/{u}/domains/{domain}`
  + UI button) — refuses to remove an account's primary domain (that's a
  different, unhandled state, not "delete a domain"); deliberately leaves
  the docroot's files on disk (removing a domain is a routing change, not
  a request to destroy content — confirmed this is the conservative,
  cPanel-equivalent-consistent choice per the goal's "never idle on
  ambiguity, pick conservative" rule).
- **New**: subdomain DNS automation. A subdomain doesn't get its own DNS
  zone (unusual/wasteful in real-world DNS) — `add_domain` now checks
  whether the new domain falls under a Forgehost-managed `DnsZone` (exact
  match or suffix match) and, if so, automatically creates an A record for
  it pointing at `server_public_ip`, with the same commit-then-compensate
  pattern the rest of `add_domain` already used for the DB row (a failed
  OLS apply rolls back the DNS record too, not just the DB row).
  `remove_domain` mirrors this symmetrically on the way out. Turned out to
  also benefit primary/addon domain adds for free (same suffix-match logic
  doesn't special-case `kind`), confirmed idempotent/harmless where it
  overlaps with `dns.create_zone`'s own `@` record.

## A real, separate, pre-existing bug found (not fixed — out of scope,
flagged instead)

While writing tests that modeled a DNS zone with no owning account (to
mirror what `api/routers/dns.py`'s `create_zone` explicitly supports —
`owner_username` is `None` whenever the zone's domain has no corresponding
hosted `Domain` row), inserting `DnsZone(account_id=None, ...)` crashed
with a raw SQLite `IntegrityError`: `shared/models.py`'s `DnsZone.account_id`
is `Mapped[int]` (NOT NULL), but `handlers_dns.create_zone`'s own code
explicitly handles `account=None` (`account_id=account.id if account else
None`). This is a real, reachable bug — an admin creating a DNS zone for a
domain not yet tied to any hosting account crashes instead of succeeding
or failing cleanly — but it is **not** the "subdomains incomplete" gap
this feature was scoped to fix, is not exercised by any of this feature's
own new code (subdomain DNS automation always operates on zones that do
have a real owning account), and the live `dns_zones` table currently has
**zero rows** (confirmed via direct query), so nothing is silently broken
today. Flagged here rather than expanding scope; a proper fix needs either
making `account_id` nullable (and deciding what "an unowned zone" should
mean for `require_domain_access`'s authorization logic, which currently
keys off `Domain`, not `DnsZone.account_id`, per the comment already in
`api/routers/dns.py`) or making the API always require a real owner. Not
decided here — a real design question, not a one-line fix.

## Real end-to-end verification performed

1. Created one account with a primary domain, a subdomain, and an addon
   domain, dropped **distinct** content in each of the three docroots —
   confirmed via real HTTP requests that each domain now serves its own
   content (this is the direct, definitive fix for the confirmed Phase 1
   gap above).
2. Confirmed PHP execution on the subdomain's vhost runs as the correct
   Linux user (`posix_geteuid()`), proving the shared server-level
   extProcessor reference works correctly from a per-domain vhost file.
3. Created a real PowerDNS zone for the primary domain, then added another
   subdomain — confirmed via `dns.list_records` that a real A record was
   automatically created for it (`shop.subtest.local -> 104.234.179.64`).
4. `account.suspend` — confirmed **all three** domains (primary, subdomain,
   addon) simultaneously switched to the suspended page in one atomic
   transaction; `account.unsuspend` reverted all three together.
5. PHP-version switch (8.3 -> 8.1) on the multi-domain account — confirmed
   via real `phpversion()` requests that **both** remaining domains picked
   up the new version together (proving the shared-extprocessor-by-name
   design works correctly across a version change, not just at creation).
6. `domain.remove` on one subdomain — confirmed via `dns.list_records` that
   its specific A record was removed (others untouched), the removed
   domain now 404s, the **other** domain under the same account is
   completely unaffected, and the docroot directory itself is preserved
   on disk (empty, since no content had been written to it, but not
   deleted).
7. `account.terminate` — confirmed all of that account's domain-vhost
   directories are removed (only the pre-existing `roundcube` webmail
   vhost remains), the account's DNS zone is fully deleted, and every
   domain now 404s.
8. Cross-account isolation re-verified under the new model specifically
   (not just inherited from Phase 1): two fresh accounts, suspending one
   left the other's vhost completely unaffected.
9. Full 212-test suite (196 existing + 16 new: `test_ols.py` rewritten for
   the new per-domain signatures, `test_handlers_domain.py` extended with
   subdomain DNS automation + `remove_domain` coverage) passing.

## What's untested

- SSL issuance for a subdomain specifically (Feature 4 wires per-domain
  SSL cert *lookup* correctly, and Phase f's `ssl.issue` already works
  per-domain by design — but a real Let's Encrypt cert was not issued for
  a subdomain in this pass, only exercised earlier for account primary
  domains and the webmail hostname).
- Concurrent domain adds/removals under the same account (each is its own
  full `ConfigWriterMulti` transaction against the shared
  `httpd_config.conf` — same untested-concurrency caveat already noted in
  Phase b's and Feature 1's checkpoints, not newly introduced here).

# Checkpoint: Phase c — DNS via PowerDNS

## What was built

- PowerDNS configured for real on this VM: `gsqlite3` backend
  (`/var/lib/powerdns/pdns.sqlite3`, schema loaded from the package's own
  `schema.sqlite3.sql`), DNSSEC off, REST API enabled on
  `127.0.0.1:8081` with a generated API key in `/etc/forgehost/secrets.env`.
  The `pdns-backend-bind` package's `bind.conf` (a transitive apt dependency
  we don't use) was disabled so only `gsqlite3` loads — confirmed via
  `journalctl` that only one backend initializes.
- `daemon/powerdns.py` — a thin REST API client (`httpx`): zone create/get/
  delete/exists, and `upsert_record`/`delete_record` using PowerDNS's own
  `PATCH .../zones/{id}` rrset `changetype: REPLACE`/`DELETE` semantics
  (RESEARCH.md SS6's explicit recommendation over raw SQL).
- `daemon/handlers_dns.py` — `dns.create_zone`/`dns.delete_zone`/
  `dns.list_records`/`dns.set_record`/`dns.delete_record`, with per-type
  value validation (IPv4/IPv6 syntax for A/AAAA, FQDN + trailing-dot
  normalization for CNAME, `<priority> <host>` for MX, length-capped and
  auto-quoted for TXT) and a `terminate_account_zones` hook wired into
  `handlers_account.TERMINATE_HOOKS`.
- `DnsZone` is a thin existence/ownership cache in Forgehost's SQLite
  control plane (ARCHITECTURE.md SS4) — PowerDNS itself stays the source of
  truth for zone/record content; Forgehost never writes PowerDNS's own
  schema directly.
- New zones get sensible defaults: vanity nameservers `ns1.<zone>`/
  `ns2.<zone>` with A records pointing at `server_public_ip` (set in
  `forgehost.toml`, the real value for this VM is `104.234.179.64`), an
  apex A record, and a `www` CNAME to the apex.
- 12 new unit tests for the record-value validators (`tests/test_handlers_dns.py`),
  88 total passing.

## Real end-to-end verification performed

1. `dns.create_zone` for `dnstest.example` → confirmed via **real `dig`
   queries against PowerDNS on port 53** (not just the REST API echoing
   back what was sent): apex A, `ns1`/`ns2` A records, and `www` CNAME all
   resolved correctly.
2. Full record CRUD: `dns.set_record` for MX, TXT (auto-quoted), AAAA, then
   `dns.delete_record` for AAAA — each change verified with a fresh `dig`
   query, including confirming the deleted AAAA record stopped resolving.
3. Invalid input rejected before ever reaching PowerDNS (`dns.set_record`
   with a non-IP value for an A record → `bad_request` error, zone
   untouched).
4. `account.terminate` on an account with a managed zone → the zone's NS
   records stop resolving entirely (`dig` returns empty/NXDOMAIN) and the
   `dns_zones` cache table is empty afterward — no orphaned PowerDNS zone or
   cache row.

## What's untested / explicitly deferred

- DNSSEC (left disabled per ARCHITECTURE.md/RESEARCH.md — out of v1 scope,
  zones are created unsigned; enabling it later is a PowerDNS-side-only
  change).
- DKIM/SPF/DMARC automation (RESEARCH.md SS6: confirmed acceptable to skip
  for v1 — the generic TXT record editor is sufficient for an operator to
  add these by hand; no special-cased automation was built).
- Real external NS delegation: the vanity nameservers this server creates
  (`ns1.<zone>`/`ns2.<zone>`) only become the zone's *actual* authoritative
  servers once the domain's registrar is told to delegate to them (glue
  records pointing at `104.234.179.64`) — that's an operator action outside
  any software this project controls, not something Forgehost could
  automate even in principle without registrar API credentials the operator
  would have to supply per-domain (explicitly out of scope; not attempted).
- Concurrent zone mutations aren't specifically tested, though PowerDNS's
  REST API is the one Forgehost component that already gets atomicity for
  free from the upstream service rather than from Forgehost's own code.

## What to review first on wake-up

- `server_public_ip` in `/etc/forgehost/forgehost.toml` is hardcoded to this
  VM's current IP. On a real deployment this needs to be set per-install
  (covered in the README's setup steps) — it is **not** auto-detected, by
  design (a network call as a config-loading side effect would be
  surprising), so a fresh install with this left blank would create zones
  with no default A/NS-glue records until the operator sets it.

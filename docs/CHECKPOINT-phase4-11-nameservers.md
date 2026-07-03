# Checkpoint: Phase 4 feature 11 — nameserver management (NS + glue)

## What was built

`daemon/nameservers.py` -- no new DB table: PowerDNS is already the
source of truth for zone/record content project-wide
(`daemon/handlers_dns.py`'s own docstring), and its REST API primitives
(`list_records`/`upsert_record`) are already generic enough to carry NS
and glue A/AAAA records like any other rrset. This feature's actual job
is the validation layer the general DNS editor deliberately doesn't have
-- `validate_record_type`'s allowed set excludes `NS` on purpose, since
NS at the zone apex is normally auto-managed
(`handlers_dns.create_zone` seeds `ns1`/`ns2` defaults pointed at this
server's own IP when a zone is first created).

- **Glue detection**: `_needs_glue(domain, ns_hostname)` -- true when the
  chosen nameserver hostname equals or is a subdomain of the zone being
  delegated. This is the actual circular-resolution problem glue records
  solve (you can't look up `ns1.example.com`'s own address without
  already being able to resolve *something* in `example.com`), and it's
  exactly the goal's own "glue form if NS is subdomain of managed domain"
  instruction -- an *external* nameserver (`ns1.someotherprovider.com`)
  needs no glue at all, since its own A record lives in a zone Forgehost
  doesn't manage.
- `set_nameservers` requires a glue IP for every in-zone nameserver
  hostname (rejects the request with a clear error if one's missing),
  validates each as a real IPv4/IPv6 address, writes the glue records
  first, then replaces the NS rrset as one atomic-per-rrset PowerDNS call
  (REPLACE semantics -- matches how the general DNS editor's own
  `set_record` already works, not an append).
- `reset_nameservers` restores exactly what a brand-new zone would have
  had (`ns1`/`ns2` pointed at `settings.server_public_ip`) rather than
  merely deleting the NS rrset -- a PowerDNS zone must always have at
  least one NS record; leaving none would break resolution entirely, not
  "reset" it.
- API: `GET`/`PUT`/`DELETE /accounts/{u}/domains/{d}/nameservers`,
  matching the goal's literal "CRUD" spec (`PUT` for set, `DELETE` for
  reset-to-default, since there's exactly one NS rrset per zone -- no
  per-item id to route a `POST`/individual-delete around).

## Testing

`tests/test_nameservers.py` (new, 12): external nameservers needing no
glue, in-zone nameservers correctly rejected without glue and accepted
with it (both IPv4 and IPv6), glue only required for the in-zone subset
when nameservers are mixed, malformed glue IP rejection, missing-zone
rejection, empty/over-8/malformed-hostname rejection, reset restoring the
original defaults, and the glue-detection helper's own edge cases
(apex-equals-domain, subdomain, external domain, and a same-string-prefix
non-subdomain like `example.com.evil.com` that must NOT match). Uses the
same `monkeypatch.setattr(<module>.powerdns, ...)` fake-PowerDNS pattern
already established elsewhere in this suite
(`tests/test_handlers_domain.py`, `tests/test_dkim.py`). 710 tests
passing (up from 698 after Feature 10).

## Live verification performed (the real Definition of Done)

A real account, a real Forgehost-managed PowerDNS zone, and real `dig`
queries against this server's own authoritative PowerDNS instance
(`@127.0.0.1`, port 53) -- not the panel's own API responses trusted at
face value:

1. Default zone creation already seeds `ns1`/`ns2.p4nstest.example.` --
   confirmed via `dig NS` before any change.
2. Set custom NS to the same two hostnames with **different** glue IPs
   (`203.0.113.55`/`.56`) -- `dig A ns1.p4nstest.example` /
   `ns2.p4nstest.example` returned exactly those IPs, not the original
   defaults.
3. Set custom NS to **entirely different hostnames**
   (`dns1`/`dns2.p4nstest.example`, distinct glue IPs) -- `dig NS`
   afterward returned exactly `dns1`/`dns2`, confirming REPLACE
   semantics (the old `ns1`/`ns2` NS entries were gone, not merely
   appended to) -- the goal's literal DONE WHEN bar, *"custom NS
   resolves via dig,"* directly confirmed.
4. Attempted a third in-zone hostname (`dns3.p4nstest.example`) with **no
   glue supplied** -- rejected with a clear error naming the exact
   missing hostname, before any PowerDNS write was attempted.
5. `nameservers.reset` -- `dig NS` afterward returned `ns1`/`ns2` again,
   with glue resolving to this server's real public IP
   (`104.234.179.64`), matching what a brand-new zone gets.
6. Account terminated; `dig NS` on the now-deleted zone returned nothing
   (confirming `handlers_dns.terminate_account_zones`' existing zone
   teardown still correctly removes NS/glue records along with
   everything else -- no separate cleanup needed since this feature adds
   no state outside PowerDNS). `daemon.log`/`journalctl` grepped for the
   test account's generated password -- clean.

## What's untested / explicitly out of scope

- Old glue records left behind after switching to different NS hostnames
  (e.g. `ns1.p4nstest.example`'s A record, still resolvable after
  `dns1`/`dns2` became the active NS set in step 3 above) are not
  automatically cleaned up -- observed directly during live verification,
  not merely theorized. Matches how the general DNS record editor already
  behaves (an edited-away rrset isn't retroactively hunted down and
  deleted elsewhere), and a stale, unreferenced A record for an old glue
  hostname is not itself a functional or security problem -- but it's a
  real, minor untidiness worth naming rather than silently leaving
  undocumented.
- External-DNS-provider delegation (registering these nameservers/glue
  with an actual domain registrar so the wider internet's resolvers
  follow the delegation) is entirely outside Forgehost's control and
  scope -- this feature manages the authoritative zone data Forgehost
  itself serves; verifying it live required only this server's own
  `dig`, matching how PowerDNS zone changes are validated everywhere else
  in this project.

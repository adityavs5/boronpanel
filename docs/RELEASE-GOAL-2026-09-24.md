# Release goal: operations, email and DNS cluster

This checklist is the acceptance contract for the release requested on
2026-09-24. A checked item needs implementation, focused regression coverage,
production build verification and a rendered/live check where applicable.

- [x] Remove remaining user-dashboard horizontal whitespace in Evo and Paper.
- [x] Make account termination converge after slow `userdel --remove`; repair
      the existing `wpdevqa` error row and keep terminated accounts out of
      active management lists.
- [x] Add optional, lightweight error telemetry suitable for early releases,
      with secrets/tenant data scrubbed and an operator-controlled endpoint.
- [x] Repair bandwidth collection so OLS keeps writing and rotating per-domain
      logs; backfill ACLs and prove a real request changes the counted log.
- [x] Remove Account Manager's duplicate Change Password tool.
- [x] Add separate admin Node.js and Python application inventory screens.
- [x] Put Add New User first in Account Manager; support moving an account
      between resellers; support additional administrator identities.
- [x] Consolidate listener/hostname and panel certificate management under
      Panel Settings.
- [x] Complete lightweight spam protection and add account/admin mail tracking
      with sender, recipient, domain, result and locally attributable script.
- [x] Merge integrations, metrics and logs into one coherent admin category.
- [x] Apply similarly balanced categories to the customer dashboard.
- [x] Add durable multi-server DNS clustering with Boron, DirectAdmin and
      cPanel/WHM peers, encrypted credentials, health/status, retryable full
      zone synchronization, deletion propagation, loop protection and an
      explicit sync-all action. Preserve existing DA/cPanel nameservers.
- [ ] Run focused backend/security/browser tests, production build, install on
      the development panel, inspect both themes, push master, publish release
      artifacts, and verify the panel self-update path.

## DNS interoperability decisions

DirectAdmin peers use its documented `CMD_API_DNS_ADMIN` protocol:
`action=exists`, raw zone POST with `action=rawsave`, and zone deletion. A
restricted login key should allow only `CMD_API_DNS_ADMIN`,
`CMD_API_LOGIN_TEST`, and `CMD_API_USER_EXISTS` and be restricted to the Boron
server IP. This lets current DA authoritative nameservers remain unchanged.

cPanel peers use WHM API 1 over TLS with a restricted API token. Boron exports
the local zone as RFC 1035 text and applies it through the supported DNS-zone
API surface. The peer adapter is kept separate because cPanel's dnsadmin
cluster has its own command/loop identifiers.

Boron peers use a narrow authenticated cluster endpoint with idempotency IDs.
Every mutation enqueues the current complete zone rather than replaying a
record-level operation, so retries converge after missed/intermediate changes.
Deletes are explicit tombstones. Cloudflare-active zones remain outside this
cluster because Cloudflare's assigned nameservers are authoritative.

Primary references:

- https://docs.directadmin.com/directadmin/general-usage/multi-server-setup.html
- https://docs.directadmin.com/developer/hooks/dns.html
- https://docs.directadmin.com/changelog/version-1.25.0.html
- https://api.docs.cpanel.net/guides/guide-to-custom-dnsadmin-plugins
- https://api.docs.cpanel.net/openapi/whm/operation/export_zone_files/
- https://doc.powerdns.com/authoritative/

# Product expansion implementation plan

The initial product and release gate closed on 2026-09-14 with the successful protected 1.1.3-to-1.2.1 self-update. This plan covers the twelve queued expansion requirements in dependency order. A feature is complete only after backend authorization, both-theme UI behavior, focused regression checks and a live-server proof where it changes the host. The expansion release gate closed on 2026-09-15 with published release v1.3.0 and successful protected self-update job 8; see `EXPANSION-FINAL-AUDIT-2026-09-15.md`.

## Batch A — navigation and simpler plans

Implementation status: complete in commit `5afe051`; focused browser coverage
passes in both themes and the implementation is deployed in v1.3.0.

- Reorder customer tools into the requested hosting, email, WordPress, backup, application and advanced sequence.
- Give subdomains and the three email areas direct entries while retaining the shared underlying management workflows.
- Add four editable starting templates for hosting plans and keep Custom for administrators who need every field.
- Verify sidebar, dashboard search, keyboard navigation, mobile layout and both skins.

## Batch B — filesystem malware protection

Implementation status: complete on 2026-09-14 and deployed in v1.3.0. The
scanner uses descriptor-relative `O_NOFOLLOW` walks and actions,
runs ClamAV as the account UID/GID, binds findings to pre/post-scan SHA-256
content, and keeps quarantine reversible. Focused backend/API coverage passed 21
checks; the Evolution and Paper Lantern customer flows plus the administrator
fleet flow passed 3 browser checks. Ubuntu ClamAV 1.5.3 with current signatures
detected the isolated EICAR standard test through Boron's unprivileged engine
handoff; both temporary test files were removed afterward.

- Add persistent scan jobs, findings, exclusions and quarantine records with account/domain ownership.
- Use ClamAV when installed, supplement it with bounded Boron checks for common WordPress web shells, obfuscated PHP, malicious upload patterns and unexpected executable files.
- Run scans with resource limits and without following links outside the selected account root.
- Provide customer scans for owned sites and an administrator fleet view, with explicit quarantine/restore actions and audit history.

## Batch C — firewall, OLS and administrator certificates

Implementation status: complete on 2026-09-14 and deployed in v1.3.0. UFW
bypass entries use tagged, first-position full-access rules; a live
add/show/delete cycle with reserved TEST-NET address `192.0.2.254` succeeded and
left the original active rules restored. OpenLiteSpeed tuning is persisted in
the control-plane database and applied through the existing validate/reload/
verify/rollback transaction. WebAdmin reset uses the installed official
`admpass.sh`; existing one-way hashes are reported as unrecoverable while a new
root-private generated credential can be revealed to an authenticated admin.
The server's installed OLS 1.9.2 configuration check passes. The shared SSL page
now gives admins a fleet inventory and direct normal/wildcard issuance. Focused
backend/API checks passed 158 tests and both themes passed the combined admin UI
workflow at desktop and mobile widths.

- Extend the existing UFW manager with full-access bypass IP/CIDR entries that survive rule refreshes and cannot silently remove protected SSH/panel access.
- Add an editable OLS administration surface for validated configuration changes and a credential reset flow. Existing one-way hashes will never be presented as recoverable passwords; only a newly reset credential may be revealed once.
- Add a global administrator certificate inventory and issuance/renewal actions using the existing ownership-safe certificate engine.

## Batch D — addresses and server resources

Implementation status: complete on 2026-09-14 and deployed in v1.3.0. Boron
inventories only addresses that the host or installer already
exposes, so IP allocation cannot rewrite netplan or disconnect the server.
Shared and dedicated pools, primary/random/specific new-account policies,
per-account overrides, managed-zone DNS updates, and termination cleanup are
implemented. The existing Server Health dashboard is now the unified resource
surface: it shows every filesystem, RAM and swap, load, total and per-interface
network traffic, uptime, and 24-hour CPU/memory/throughput history. Focused
backend/API checks passed 73 tests, the production frontend build passed, and
the IP/account flow passed in both themes at desktop and mobile widths.

- Discover configured server addresses and persist shared/dedicated/default/allocation policy metadata.
- Assign dedicated addresses to accounts, support one or more shared addresses, random shared allocation and a selected default for new accounts.
- Feed selected addresses into domain DNS and OLS listeners without changing unrelated host networking.
- Add a unified administrator resource page for disk, filesystem, memory, swap, load, network throughput and top account consumers.

## Batch E — portable accounts and imports

Implementation status: complete on 2026-09-14 and deployed in v1.3.0. Full
account backups are now versioned Boron archives with a manifest,
per-component sizes and SHA-256 hashes. Administrator downloads work for local
and remote backup destinations; imports copy uploads into root-only staging,
enforce compressed and expanded size limits, reject unsafe paths and links,
verify every component, and reveal a recreated account credential once. The
external migration pipeline now accepts cPanel and DirectAdmin user archives
through one history and detail interface, retains best-effort per-item reports,
and rewrites imported WordPress database credentials. Existing cPanel jobs are
migrated additively. Focused backend/API coverage passed 116 tests, the
production frontend build passed, and the complete migration flow passed in
both themes at desktop and mobile widths.

- Define a versioned universal Boron account archive with a manifest, checksums and explicit component inventory.
- Add administrator export/restore jobs that reuse existing snapshot safety, tenant validation and rollback boundaries.
- Extend the existing cPanel importer and add DirectAdmin archive ingestion into the same normalized import plan, preview and job history UI.

## Batch F — resellers and suspension pages

Implementation status: complete on 2026-09-14 and deployed in v1.3.0. Reseller
identities use the administrator listener and have their own
role-specific panel. Plans cap account count and allocated disk while supplying
per-account PHP, disk, CPU, memory, I/O and process defaults. Ownership is
stored explicitly and rechecked by both the API authorization helpers and the
privileged daemon before lifecycle actions. Plan edits and reassignments cannot
drop below current usage. Four responsive suspension designs can be previewed,
branded and applied from the admin interface; all editable copy is HTML-escaped,
and the raw HTML editor remains available for advanced customization.

- Add reseller identities, plans, account ownership, quotas and a restricted reseller panel. Enforce scope in API and daemon layers rather than relying on hidden UI controls.
- Add built-in responsive HTML suspension templates, preview, selection and safe customization. Render the selected template for suspended accounts without exposing account files.

## Release gate — complete 2026-09-15

- [x] Run focused suites after each batch and the complete backend/browser suites after all batches.
- [x] Exercise host-mutating workflows on disposable or existing designated QA resources, restore their baselines, then deploy with rollback evidence.
- [x] Publish a versioned GitHub release, verify public artifacts independently and complete a protected panel self-update with configuration and inventory preservation checks.

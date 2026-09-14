# Product expansion implementation plan

The initial product and release gate closed on 2026-09-14 with the successful protected 1.1.3-to-1.2.1 self-update. This plan covers the twelve queued expansion requirements in dependency order. A feature is complete only after backend authorization, both-theme UI behavior, focused regression checks and a live-server proof where it changes the host.

## Batch A — navigation and simpler plans

- Reorder customer tools into the requested hosting, email, WordPress, backup, application and advanced sequence.
- Give subdomains and the three email areas direct entries while retaining the shared underlying management workflows.
- Add four editable starting templates for hosting plans and keep Custom for administrators who need every field.
- Verify sidebar, dashboard search, keyboard navigation, mobile layout and both skins.

## Batch B — filesystem malware protection

- Add persistent scan jobs, findings, exclusions and quarantine records with account/domain ownership.
- Use ClamAV when installed, supplement it with bounded Boron checks for common WordPress web shells, obfuscated PHP, malicious upload patterns and unexpected executable files.
- Run scans with resource limits and without following links outside the selected account root.
- Provide customer scans for owned sites and an administrator fleet view, with explicit quarantine/restore actions and audit history.

## Batch C — firewall, OLS and administrator certificates

- Extend the existing UFW manager with full-access bypass IP/CIDR entries that survive rule refreshes and cannot silently remove protected SSH/panel access.
- Add an editable OLS administration surface for validated configuration changes and a credential reset flow. Existing one-way hashes will never be presented as recoverable passwords; only a newly reset credential may be revealed once.
- Add a global administrator certificate inventory and issuance/renewal actions using the existing ownership-safe certificate engine.

## Batch D — addresses and server resources

- Discover configured server addresses and persist shared/dedicated/default/allocation policy metadata.
- Assign dedicated addresses to accounts, support one or more shared addresses, random shared allocation and a selected default for new accounts.
- Feed selected addresses into domain DNS and OLS listeners without changing unrelated host networking.
- Add a unified administrator resource page for disk, filesystem, memory, swap, load, network throughput and top account consumers.

## Batch E — portable accounts and imports

- Define a versioned universal Boron account archive with a manifest, checksums and explicit component inventory.
- Add administrator export/restore jobs that reuse existing snapshot safety, tenant validation and rollback boundaries.
- Extend the existing cPanel importer and add DirectAdmin archive ingestion into the same normalized import plan, preview and job history UI.

## Batch F — resellers and suspension pages

- Add reseller identities, plans, account ownership, quotas and a restricted reseller panel. Enforce scope in API and daemon layers rather than relying on hidden UI controls.
- Add built-in responsive HTML suspension templates, preview, selection and safe customization. Render the selected template for suspended accounts without exposing account files.

## Release gate

- Run focused suites after each batch and the complete backend/browser suites after all batches.
- Exercise every host-mutating workflow on disposable or existing designated QA resources, restore its baseline, then deploy with rollback evidence.
- Publish a versioned GitHub release, verify public artifacts independently and complete a protected panel self-update with configuration and inventory preservation checks.

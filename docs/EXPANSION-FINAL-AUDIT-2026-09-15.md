# Expansion final audit — 2026-09-15

The initial product objective was completed and deployed in v1.2.1 as recorded
in `INITIAL-PHASE-AUDIT-2026-09-14.md`. This audit closes the twelve-item
follow-up expansion without narrowing either phase.

## Requirement audit

| Requirement | Completion evidence |
| --- | --- |
| Requested customer menu order and direct sections | Batch A placed domains, subdomains, FTP, SSL, databases and DNS first; email tools, WordPress, backups, runtimes and advanced tools follow. Both-theme navigation and fuzzy-search browser cases pass. |
| File/script malware scanner | Batch B combines account-UID ClamAV execution with bounded Boron WordPress checks, descriptor-relative no-follow traversal, content-bound findings and reversible quarantine. A deployed scan on the designated `wpdevqa` account completed across 30,272 files with zero findings, zero skipped and no error; no file was quarantined. |
| Miniature CSF-style firewall | Batch C provides UFW rule, port and full-access IP/CIDR bypass management while retaining protected-access rails. Its live reserved TEST-NET bypass add/show/delete cycle restored the original rules. The deployed rules, bypass and status APIs all pass authenticated reads. |
| Editable OpenLiteSpeed administration | Batch C provides validated settings apply/reload/rollback and WebAdmin credential reveal-or-reset semantics. The installed OLS configuration check, focused mutation tests and deployed authenticated status API pass. Existing one-way password hashes are never presented as reversible. |
| Administrator SSL issuance | Batch C provides fleet certificate inventory plus normal and wildcard issuance from the admin role using the existing guarded certificate engine. The deployed authenticated inventory API passes. |
| Package templates and selectable inputs | Batch A provides four editable plan starting points plus Custom and replaces practical free-text choices with selectors. Both-theme plan creation coverage passes. |
| Multiple IP management | Batch D discovers host addresses, supports shared/dedicated modes, primary/random/specific new-account allocation, dedicated assignment, managed DNS propagation and termination cleanup without rewriting host networking. Backend coverage and both-theme account-allocation flows pass; the deployed inventory API passes. |
| Portable administrator backup/restore | Batch E produces versioned Boron account archives with a component manifest, sizes and SHA-256 hashes and restores them through bounded, path-confined staging. Local/remote download, verification, import and rollback-boundary coverage pass. |
| cPanel and DirectAdmin imports | Batch E accepts cPanel, DirectAdmin and Boron archives through one normalized, job-backed interface with bounded extraction and component reporting. Both-theme migration workflows pass and the deployed history APIs respond. |
| Administrator disk/resource utility | Batch D extends Server Health with every filesystem, RAM/swap, load, uptime, inode, per-interface network traffic, 24-hour trends and top-account allocation data. Both the deployed live and history APIs respond through an authenticated admin session. |
| Reseller management and panel | Batch F adds reseller identities, capped plans, explicit account ownership, a dedicated panel and API-plus-daemon scope enforcement. Plan downgrades below usage are rejected and failed provisioning is compensated. Sixty-five focused reseller/template/auth/port tests and both-theme admin/reseller browser flows pass; the deployed plan and reseller APIs respond. |
| Prebuilt suspension templates | Batch F provides clean, gradient, classic and minimal responsive designs, safe colors and escaped copy while retaining the raw HTML editor. A deployed preset apply and persisted readback passed; the original custom page, metadata row set and test backup residue were then restored exactly. |

## Release validation

The final source-tree gate passed **2,733 backend tests** with seven dependency,
OpenAPI and `fork()` deprecation warnings and no failures. The complete browser
gate passed **130 of 130** cases in Evolution and Paper Lantern, including light,
dark, desktop and mobile variants where applicable. It covers administrator,
reseller and customer roles, WordPress, backups/restores, migrations, malware,
firewall/OLS/SSL, IP allocation, package and suspension templates, responsive
layouts and fuzzy search. Exact pre-release evidence is in
`EXPANSION-RELEASE-VALIDATION-2026-09-14.md`.

The real release pipeline repeated all 2,733 backend tests in 55 minutes 26
seconds, rebuilt the frontend, staged 770 tracked files, and verified archive
path confinement, runtime contents and checksum. It published commit `244cde4`,
annotated tag `v1.3.0`, and release assets. The public 4,969,862-byte archive was
downloaded independently, passed its published SHA-256, and matched the local
artifact byte-for-byte:

```text
87faa9b997478496ce5f7a28bd2c5aa10f3922a25b55d078b24e8b98a018d755
```

## Protected self-update and preservation

Installed panel job **8** performed the real 1.2.1-to-1.3.0 update. Its full
live preflight passed, then it created backup
`/var/backups/boron/pre-update-1.3.0-20260915-045219`, downloaded and verified
the public artifact, built an isolated environment, migrated additively,
activated the mailbox recovery guard, switched `/opt/boron` to
`/opt/boron-1.3.0`, restarted the provisioner and API, and passed API-plus-daemon
health. It completed in 3,473.31 seconds with no rollback; rollback to 1.2.1 is
available.

The private before/after verifier passed database quick-check and foreign-key
integrity, idle operation queues, trusted HTTPS/admin authentication, version,
shared admin/customer port 2222, synchronized chrony and six active core
services. It proved exact preservation of panel configuration, secrets, and
selected account, domain, DNS, database grant, mail, FTP, WordPress, application,
plan and backup inventories. Evidence files
`/root/boron-setup/release-1.3.0-before.json` and
`/root/boron-setup/release-1.3.0-after.json` are root-only mode 0600.

A deployed real-browser smoke passed login, live statistics, accounts, form
state, appearance persistence, mobile layout and the completed Updates page in
both themes, with no uncaught browser error or failed static asset. Every new
expansion read API passed through a real authenticated session. API,
provisioner, OpenLiteSpeed, PowerDNS, Dovecot and chrony are active; the clock is
synchronized. Both authoritative Cloudflare nameservers return
`phpmyadmin.boron.sitecountry.com A 104.234.179.66`, and trusted HTTPS redirects
to `/boron_signon.php`.

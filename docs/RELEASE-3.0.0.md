# Boron v3.0.0

Boron v3.0.0 adds the mail, DNS, resource-control and server-stack foundation needed for shared-hosting operation on Ubuntu 24.04. It keeps potentially disruptive enforcement in staged modes on upgraded servers.

## Customer workflow changes

- Each mailbox has a secure one-click **Webmail** action. The browser submits a one-use token to Roundcube by POST; the mailbox password, temporary credential and token do not appear in the URL or audit log.
- The SSL page now shows Panel, Webmail, Mail and phpMyAdmin service certificates with issuance and diagnostics.
- Email DNS shows MX, SPF, DKIM, mail/FTP host readiness, preserves conflicts for explicit review, and offers DMARC Monitor, Quarantine and Reject presets.
- Empty mail domains now explain that no email accounts exist and point to the next action.
- Single-mailbox backups use domain and full-address selectors.
- Database creation, database-user creation, grants and maintenance actions are separated into clearer sections.
- Node.js, Python, Redis, Git and WordPress use their recognizable product marks in Evo and Paper.

## Administrator workflow changes

- **Resource Manager** applies inherited or temporary account policies for CPU cores and priority, memory pressure/hard limits, read/write throughput, IOPS, NPROC and dynamic entry processes. It records usage and limit faults.
- **Filesystem Isolation** reports the actual boundary for web/PHP, terminal, SSH/SFTP and application services, with rebuild and self-test actions.
- **DB Monitor** now includes the MariaDB governor. Monitor mode observes; Enforce installs native per-user connection/query/update/statement limits; Pause removes enforced limits.
- **Security Center** combines firewall state, WAF modes and OpenLiteSpeed abuse controls without replacing the existing firewall recovery rails.
- OpenLiteSpeed can reload `.htaccess` changes through a coalescing watcher and a manual per-domain action.
- **Stack Manager** inventories and runs typed, serialized install/update jobs for OpenLiteSpeed, supported LSPHP versions and Ubuntu 24.04 MariaDB 10.11.
- Server Setup includes the stack inventory review, while Local DNS remains the fresh-install default.
- Reseller and hosting-plan editors use core-count presets and expose the same memory, I/O, IOPS, NPROC and entry-process vocabulary.

## Mail and DNS runtime changes

- Postfix and Dovecot use `/etc/boron/ssl/mail.crt` and `mail.key`, so issuing a mail certificate no longer replaces the OpenLiteSpeed default certificate.
- OpenDKIM is installed/configured as a Postfix milter. DKIM DNS is published only after local signing becomes active; SPF remains owned by the conflict-aware mail DNS reconciler.
- New local zones receive non-conflicting MX, SPF, mail and FTP records. DMARC remains an explicit policy choice.
- Existing DNS zones and provider assignments are not overwritten during upgrade.

## Safe rollout and recovery

- Upgraded servers retain their current DNS provider, WAF/firewall rules and resource values.
- DB Governor starts in Monitor. Move a test account to Enforce before broader use.
- Filesystem isolation should be canaried with the account's WordPress, WP-CLI, Composer, Node.js, Python, Redis, mail and backup workflows.
- WAF begins in Detect; review events before Protect.
- Stack Manager simulates package transactions, backs up service configuration and validates the affected service. It does not automatically downgrade packages. Restore the operator VM snapshot or configuration archive when a package-level rollback is required.
- The updater runs `scripts/upgrade_runtime.py` to create the webmail exchange schema, install the Roundcube plugin, move mail TLS to the dedicated pair, configure Dovecot's temporary credential query and activate OpenDKIM. A failure aborts finalization before the release is accepted.

## Verification

- Root RPC/session authorization, webmail replay/expiry/revocation and secret-redaction coverage.
- Mail DNS, DKIM, certificate deployment/rollback and upgrade-runtime coverage.
- Resource inheritance, cgroup translation, expiry retry, DB governor lifecycle and `.htaccess` watcher coverage.
- Stack job and setup-wizard contracts.
- Evo and Paper in light/dark, desktop/mobile, including Server Manager placement and one-click webmail POST handoff.
- Production frontend build, Python compile check, release artifact checksum/signature verification and full repository test suite in the release pipeline.

Live external-provider checks still depend on the operator's public DNS, Cloudflare token and test mail destination. Cross-series MariaDB upgrades remain outside this release.

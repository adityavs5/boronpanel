# Changelog

## 3.3.2 — Stable

A large maintenance release. Every customer, reseller and administrator
flow was tested on a live server, and the panel has a new design.

### Security

- API tokens now belong to the administrator who created them. A token stops
  working as soon as that administrator is disabled, deleted or no longer an
  administrator. Disabling or deleting an administrator, changing their
  password or resetting their 2FA also revokes every token they created. A
  token now acts, and is recorded in the audit log, under its creator's login
  name. The label is only a description, so it can no longer make actions look
  like they came from someone else. The token list shows who created each
  token. The same rule applies to customer tokens, which administrators
  create too.
- Login lockout can no longer be used to keep an administrator out. After
  repeated failures, only the client address that failed (the IPv4 address or
  IPv6 /64) is locked for 15 minutes. If a login name gets more failures from
  many addresses than the guard limit allows, sign-ins from addresses that
  have never signed in as that user are refused for 15 minutes. Addresses with
  a successful sign-in in the last 90 days can still sign in.
- Sign-in attempts for login names that do not exist or are disabled now take
  as long as attempts for real accounts, so response time no longer shows
  which login names exist.
- Domains on public providers and infrastructure, and names that overlap
  another account's domains, can no longer be claimed. Local mail for a
  domain becomes active only once its MX points to this server.
- Senders must own the address they send from on SMTP submission, local
  injection and loopback. Hosting users can no longer send directly to
  remote port 25.
- A suspended or terminated customer is refused at sign-in with the reason,
  and an administrator's "log in as customer" session ends when the account
  stops being active or suspended.
- Suspension now also blocks the account's email logins (IMAP, POP3, SMTP
  and webmail), closes open mail sessions and ends processes the customer
  left running (SSH background jobs, running cron jobs, systemd user
  services, PHP workers). Incoming mail is still delivered.
- Redirect, forwarding and branding URLs may no longer contain control
  characters, and redirect paths may no longer contain "." or "..".
- PHP scan directories, cPanel import uploads, certificate writes and web
  server logs moved out of locations the account can write to. Notification
  secrets are redacted from logs.
- Internal errors no longer show raw text, tar output or server paths; they
  show a reference that the Error Log can find.

### New

- New design for both skins (Evo tile grid and Paper list): navy brand
  chrome, a blue default accent with presets, one icon family, and every
  screen checked at 375 px wide in light and dark mode.
- Force HTTPS per domain, once the domain has a valid certificate.
- One-click staging sites (create, sync, delete) on the domain page.
- Autoresponders with optional start and end dates.
- POP3 (110 with STARTTLS, 995) and SMTP on port 465. The Email page lists
  the mail app settings for each domain.
- Customers can download their own backups. Backup jobs, legacy schedules
  and unused snapshot destinations can be deleted.
- Administrators can restore or release an account's SQL import recovery
  copies.
- File Manager "Change permissions", a per-domain .htaccess report,
  editable Node.js and Python app settings with "Install dependencies".
- Diagnostics checks for the mail hostname and certificate, and for
  phpMyAdmin.
- Resellers can add subdomains and addon domains to their accounts through
  the task flow and see those accounts' tasks.

### Fixed and changed

- User errors (not found, conflicts, limits) answer 400, 404 or 409 with a
  readable message instead of 502. Database, DNS and account locks wait for
  a bounded time instead of failing at once.
- Cron entries run through the panel's Python again, so scheduled backups,
  usage snapshots, inode reconciliation and SSL checks run.
- Mail: DKIM signing for all outgoing mail, Sieve filters and spam
  preferences on delivery, forwarders keep the local copy, DMARC defaults,
  Postfix greets with the mail hostname, mail readiness checks public DNS
  when the zone is hosted elsewhere, "Add missing records" keeps unrelated
  TXT records, and a separate mail hostname can get its certificate.
- Databases: SQL import keeps unmentioned tables and rolls back on failure,
  uploads up to 2 GiB, phpMyAdmin installs in the background when its
  hostname is saved, and MariaDB errors are shown.
- Accounts: plan limits are enforced consistently (CPU, memory, I/O,
  processes, inodes), plans in use cannot be deleted, resellers can be
  deleted, terminated usernames can be reused, and restoring or importing a
  terminated account works even when its uid changes.
- Web: .htaccess access rules are enforced, removing a domain cleans up its
  WAF rules, PHP overrides, certificate and DNS, Python apps start, staging
  and WordPress clones work in new domains, certificates include www when it
  points here, parked domains get DNS, and PHP errors go to a log instead
  of the page.
- DNS: shared record validation in both editors, apex NS and glue handling,
  CNAMEs with underscores, a real SOA primary nameserver, long TXT records
  split into 255-byte strings, Cloudflare TXT round-trips, and cPanel or
  DirectAdmin imports skip and report records they cannot use.
- Backups: selective and full restores recreate deleted mailboxes,
  databases, domains, parked domains and cron jobs; a domain now owned by
  another account is skipped and listed; a failed restore of a terminated
  account terminates it again.
- Sign-in shows how long to wait after too many attempts; the lock-out
  response carries Retry-After.
- Bandwidth and site statistics include rotated, compressed logs.

### Upgrade notes

- Database schema revision 6 (additive) adds the token creator. **Existing
  API tokens have no recorded creator, so the upgrade revokes all of them,
  both administrator and customer tokens.** After updating, create new
  tokens under Admin > API tokens and update any integrations that use them.
- Schema revision 7 (additive) adds per-domain Force HTTPS; older releases
  ignore it.
- Web server logs move to `/var/log/boron-vhosts`. Each log rolls at 50 MB
  and keeps at most 10 archives for 90 days. Check free space on /var first.
- Admin PHP `disable_functions` overrides are now enforced; the upgrade
  merges each stored override once with the default list, so no account
  becomes less restricted. `display_errors` is set to Off in the lsphp
  php.ini files.
- The update installs missing packages (opendkim, dovecot-pop3d and others)
  and adds the port 465 submission service; where UFW is active it opens
  465. Close 110, 995 or 465 if you do not want to offer them.
- The update re-applies every live account's resource limits and blocks the
  mail logins and processes of accounts that are already suspended.
- phpMyAdmin is installed within 5 minutes on servers with a database; a
  failure appears as an update advisory and in Diagnostics.
- Existing certificates gain www only when reissued. Parked domains added
  earlier keep their DNS until parked again. Copies left by failed 3.3.1
  staging runs are not deleted automatically.
- Rollback caveat: after rolling back to 3.3.1, accounts suspended under
  3.3.2 can sign in to email again and keep an expired Linux login after
  unsuspension. Unsuspend them before rolling back, or run
  `usermod --expiredate '' <username>` afterwards.
- The update reloads OpenLiteSpeed, Dovecot and Postfix and restarts the
  panel services.

## 3.3.1 — Stable

### Fixed

- Minor-version updates (for example 3.3 to 3.4) no longer fail their
  post-swap health check and roll back: the update finalizer now expects the
  RPC protocol of the release being installed rather than the outgoing one.

### Upgrade notes

- No database schema change. Update from 3.3.0 through Admin > Updates.

## 3.3.0 — Stable

First commercial release. Boron Panel is proprietary software and requires a
license from [boronpanel.com](https://boronpanel.com).

### New

- Licensing. A license server at `license.boronpanel.com` issues licenses
  bound to the server's public IP and returns Ed25519-signed license documents
  that the panel verifies locally.
- License enforcement. Without a valid license, or after the 14-day offline
  grace period, administrators see only the new **License** page. Customer and
  reseller logins, websites, mail, DNS and databases keep working. Account
  creation respects the plan's account limit.
- **License** page for administrators: add or replace a key, check the status
  and refresh it. A banner warns when the license has not been confirmed for
  three days.
- Installer `--license BRN-XXXX-XXXX-XXXX-XXXX` (or `FH_LICENSE_KEY`). The key
  is checked before anything is installed and activated afterwards. It is
  optional; it can be added later on the License page.
- New signed installer at `https://download.boronpanel.com/install.sh`. It
  verifies the release manifest's Ed25519 signature and the archive's SHA-256
  before running anything.

### Changed

- Panel updates are license-gated and served from our own servers. The panel
  asks `license.boronpanel.com` for the release and downloads it from
  `download.boronpanel.com` through short-lived links tied to the licensed
  server. Checksum, publisher signature, signed metadata, staged extraction
  and automatic rollback are unchanged.
- New release signing key. Panels now pin
  `34f15d91f0324d873eb770e668577d36a1d4772232b4f3be938a16a559c43843`.
- GitHub is no longer used for installation, updates or releases.
  `update_github_repo` in `boron.toml` is accepted but ignored; the new
  settings are `update_server_url` and `update_download_prefix`.

### Upgrade notes

- Add a license on the License page after updating; without one, update
  checks report that a license is needed and the admin area is locked.
- The signed metadata accepts panels from 3.2.0 onwards. The update reloads
  OpenLiteSpeed, Dovecot and Postfix and restarts the panel services.
- 3.2.x panels still look for updates on GitHub and trust only the previous
  release key, so they do not offer 3.3.0 on their own. Moving them is a
  one-time operator step; see [docs/RELEASING.md](docs/RELEASING.md).

## 3.2.2 — Beta

### Security

- Remove reusable passwords from welcome emails and integration events.
- Require the current password to enroll two-factor authentication.
- Restrict one-time credential retrieval to authorized, write-capable callers and same-origin browser requests.
- Bound File Manager transfer concurrency, upload size and idle time; close transfers cleanly on disconnect.
- Redact sensitive audit parameters and historical diagnostic output.
- Apply mandatory resource limits before account activation and quarantine accounts when startup enforcement fails.
- Protect local backup destinations and artifact writes against writable ancestors and symlink substitution.
- Require replacement credentials when changing a DNS cluster peer's connection settings.
- Use bounded, expiring shared storage for SQL exports across the API service's private temporary-directory boundary.
- Allow administrators to revoke their own impersonation sessions.
- Keep the MariaDB root password out of installer process arguments.

### Fixed and changed

- Avoid the first-update-check singleton creation race and repair SQL upload handoff between services.
- Treat an update tier with no eligible release as unavailable rather than a failed check.
- Refine gold accents, branding and appearance preferences; prevent preference synchronization loops.

### Upgrade notes

- Beta prerelease: intended for testing before Stable promotion. Release metadata binds this candidate to the beta tier.
- Two-factor enrollment API requests now require `current_password`; read-only tokens cannot consume one-time credentials.
- Existing unsafe backup destinations require administrator repair. SQL exports are limited to 2 GiB each and preserve a 5 GiB free-space reserve.
- Resource enforcement failures deliberately block account activation or suspend affected accounts until repaired.
- Previously disclosed credentials in emails, audit exports or old backups should be rotated; an update cannot retract old copies.
- No new database schema migration. Review the declared service reloads before updating.

## 3.2.1

### Changed

- Refined shared inner-page surfaces, form fields, tables and dialogs in Evo and Paper, with consistent header styling.
- Redesigned Create account with paired fields, password generation and visibility, username feedback, plan limits and sticky actions.
- Simplified Manage Accounts with grouped account/domain identity, optional detail columns and a dedicated reseller assignment dialog.
- Preserved desktop navigation across inner pages and improved mobile form actions.
- Added a one-line installer launcher pinned to this release, README theme previews and a dark-mode wordmark.

### Fixed

- Removed the duplicate rectangular focus outline inside global search while retaining a visible keyboard focus indicator.
- Corrected account selection checkbox sizing and excess list-header spacing.

### Upgrade notes

- No database schema or API contract changes in this patch. Existing updater service-reconciliation behavior is unchanged.


## 3.2.0 — release candidate

### New

- Correlation IDs now follow HTTP requests through root-helper calls, audit
  entries, background work and structured logs. Error and audit screens can
  find a failure by its reference.
- Administrators can run bounded diagnostics, create redacted support bundles,
  inspect richer root-action steps, review unified task history and configure
  retention, alerts and administrator session policy.
- The installer supports Minimal, Standard and Full profiles, dependency-aware
  setup steps and resource-aware tuning previews. PHP lifecycle status,
  per-account session paths, inode controls, isolation results and site-health
  checks are visible in the panel.
- The update system has signed release metadata, preflight checks, an additive
  migration ledger, protocol/config compatibility checks, runtime rewrite
  transactions, readiness checks and encrypted control-plane recovery exports.

### Changed

- Account, backup, DNS, email, database, security and operations screens use a
  consistent hosting-panel layout with safer progress and failure feedback.
- Evo keeps its menu-based navigation while Paper keeps its persistent desktop
  sidebar. Long DNS/mail values, table actions and narrow-screen layouts are
  bounded and readable.
- File Manager sessions run per account and are available to customer sessions,
  scoped impersonation sessions and the administrator account action.
- Backup/import staging, database work directories and portable archive
  transfers use private, symlink-resistant locations across systemd
  `PrivateTmp` boundaries.

### Upgrade notes

- The supported rollback guarantee is the immediately previous release. Schema
  changes in this release are additive; rollback restores the compatible
  configuration snapshot and leaves additive database fields in place.
- Updates reload data-plane services only when signed release metadata declares
  them and the administrator acknowledges the list.
- Fresh Full installations provide PHP 8.1 through 8.5 and Ubuntu 24.04's
  shared MariaDB 10.11. Existing installations retain operator configuration
  and tuning until an administrator reviews and applies a diff.

### Known limits

- GitHub pre-releases are deliberately absent from normal update discovery and
  become visible only after the owner promotes them to a normal release.
- SSH/SFTP uses the account UID, Linux permissions and account cgroup. The
  supported private filesystem/process namespace applies to Web/PHP workloads.
- GeoLite2 needs an operator-supplied MaxMind license. Webmail installation
  needs a configured webmail hostname.
- MariaDB is one shared instance with per-account grants; alternate database
  series and per-account MariaDB instances are not offered.

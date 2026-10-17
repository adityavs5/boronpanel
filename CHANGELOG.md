# Changelog

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

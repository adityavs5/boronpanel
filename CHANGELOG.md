# Changelog

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

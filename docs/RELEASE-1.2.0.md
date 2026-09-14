# BoronPanel 1.2.0 — release candidate

This candidate is not yet published. Full repository regression and a live self-update verification are pending.

WordPress management now supports discovering sites uploaded or migrated outside the installer, refreshing registered installations, removing a panel record while retaining the site, and permanently removing the selected installation's files and database after confirmation. Installation and clone workflows support HTTP/HTTPS and www/non-www addresses.

The backup manager adds reusable scheduled jobs, encrypted incremental recovery points, account and path filters, local and SSH destinations with pinned host keys, retention, and selected email/webhook notifications. Customers can browse recovery points and restore files, databases, mailbox messages, PHP settings, scheduled tasks, DNS and email-routing settings. Restore operations retain encrypted previous-state recovery copies; failed or active recovery operations protect their required copies from retention cleanup. Whole-account portable migration is a separate planned feature.

Domains and subdomains use independent site roots and DNS records. Mailbox creation provisions missing mail-domain configuration. Node.js and Python applications have separate customer entries. Database, SSL, FTP, Git, application, mailbox and SSH-key controls are easier to access directly, including keyboard and mobile interaction.

Panel configuration supports shared or separate admin/customer ports, defaulting to 2222. The terminal welcome is configurable. PHP configuration supports account defaults, per-site versions, Lite/Moderate/Max presets and editable Custom limits. Both themes include typography and dashboard improvements using local assets. Admin and customer two-factor authentication includes recovery and clock-health diagnostics.

## Validation and upgrade status

The development server has valid panel and phpMyAdmin HTTPS, synchronized time, and verified live WordPress and backup workflows. Core backup integration checks passed 66 tests. Update/release checks passed 89 tests with one optional ShellCheck skip. Browser checks cover both themes, light/dark appearance, mobile flows and keyboard controls; detailed evidence is in FINAL-REGRESSION-2026-09-14.md.

Cloudflare DNS recovery has native-record, legacy-format and encrypted queue tests using a simulated provider. No Cloudflare account is connected to the development panel, so a live provider-write test has not been performed.

Before publication, audit upgrade provisioning for restic, time synchronization and mailbox-recovery dependencies on installations predating this release. The development server already has these dependencies; its configuration alone does not prove their automatic installation on an older server. Preserve the previous version and verify authenticated service health after the panel's self-update.

# BoronPanel 1.2.0

> Update notice: the built-in updater can stop safely before switching versions when Dovecot reports its normal transient reload state during mailbox-guard activation. The live installation remains on its previous version. This is corrected in the 1.2.1 patch release; administrators using the panel updater should select 1.2.1 instead.

WordPress management now supports discovering sites uploaded or migrated outside the installer, refreshing registered installations, removing a panel record while retaining the site, and permanently removing the selected installation's files and database after confirmation. Installation and clone workflows support HTTP/HTTPS and www/non-www addresses.

The backup manager adds reusable scheduled jobs, encrypted incremental recovery points, account and path filters, local and SSH destinations with pinned host keys, retention, and selected email/webhook notifications. Customers can browse recovery points and restore files, databases, mailbox messages, PHP settings, scheduled tasks, DNS and email-routing settings. Restore operations retain encrypted previous-state recovery copies; failed or active recovery operations protect their required copies from retention cleanup. Whole-account portable migration is a separate planned feature.

Domains and subdomains use independent site roots and DNS records. Mailbox creation provisions missing mail-domain configuration. Node.js and Python applications have separate customer entries. Database, SSL, FTP, Git, application, mailbox and SSH-key controls are easier to access directly, including keyboard and mobile interaction.

Panel configuration supports shared or separate admin/customer ports, defaulting to 2222. The terminal welcome is configurable. PHP configuration supports account defaults, per-site versions, Lite/Moderate/Max presets and editable Custom limits. Both themes include typography and dashboard improvements using local assets. Admin and customer two-factor authentication includes recovery and clock-health diagnostics.

## Validation

The development server has valid panel and phpMyAdmin HTTPS, synchronized time, and verified live WordPress and backup workflows. The full backend regression passed 2,655 tests; two ShellCheck checks skipped at collection passed separately after installation. All 114 browser checks passed across both themes, light/dark appearance, mobile flows and keyboard controls. The tested source deployment passed authenticated health checks. Detailed evidence is in FINAL-REGRESSION-2026-09-14.md.

Cloudflare DNS recovery has native-record, legacy-format and encrypted queue tests using a simulated provider. No Cloudflare account is connected to the development panel, so a live provider-write test has not been performed.

## Upgrading older installations

The original v1.1.3 updater does not provision every new system dependency. Existing servers need restic for incremental storage, configured chrony synchronization, and the Dovecot mailbox recovery guard before using the corresponding features. Fresh installations provision these dependencies. The development server already has them; its verification does not establish automatic provisioning on an unmodified older server.

For an older Ubuntu 24 installation, an administrator should install `restic`, `chrony`, `build-essential` and `libssl-dev`, apply the release's `scripts/install_time_sync.sh`, and install/verify the mailbox guard using `daemon.snapshot_mail_guard_config` with a private configuration backup. Review the commands against the server's existing mail configuration and use the release's Python environment. Preserve the previous version and verify authenticated service health after updating.

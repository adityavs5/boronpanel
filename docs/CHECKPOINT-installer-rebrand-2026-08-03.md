# Checkpoint — installer and namespace audit (2026-08-03)

Scope: source-only audit and repair. No deployment, systemd action, firewall
change, or other live-server mutation was performed.

Completed:

- Normalized all non-checkpoint references to the Boron namespace, including
  service, configuration, MariaDB, and user-visible panel names.
- Expanded the installer for LSAPI PHP 8.1–8.5, Node 18/20/22, FileBrowser
  Quantum, ImapSync, GeoIP tooling, ACME renewal, and the required base tools.
- Made virtual Postfix/Dovecot mail a configured installer step, including
  SQL maps, LMTP/auth sockets, TLS, SMTP submission, and the mail feature
  tables.
- Installed and bootstrapped SpamAssassin and the application's complete
  fail2ban jails rather than leaving partial static configuration.
- Added the FileBrowser systemd unit and ACME renewal cron installation.
- Set UFW's SSH allowance before enablement and opened panel, HTTP(S), mail,
  DNS, FTP control, and the Pure-FTPd passive `30000:30100/tcp` range.

Verification:

- `bash -n scripts/install.sh`
- `bash scripts/install.sh --dry-run` (no mutations; completed with zero
  failures)
- production UI bundle rebuilt from the current frontend source
- repository-wide case-insensitive namespace scan: only historical
  `docs/CHECKPOINT-*.md` records retain prior-name references.

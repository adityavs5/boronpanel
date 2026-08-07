# Boron namespace inventory

The current runtime namespace is exclusively Boron:

- Code and configuration: `/opt/boron`, `/etc/boron`, and `boron.toml`.
- State and logs: `/var/lib/boron` and `/var/log/boron`.
- Services: `boron-api`, `boron-provisiond`, and `boron-filebrowser`.
- MariaDB service identities: `boron_daemon`, `boron_mailro`, and the
  `boron_mail` schema.
- Generated jobs, log rotation, and firewall configuration use the same
  namespace.

The repository-wide case-insensitive check deliberately permits prior-name
references only in `docs/CHECKPOINT-*.md`, which are immutable historical
records. Current source, deploy definitions, user-visible UI copy, and
operational documentation must remain namespace-clean.

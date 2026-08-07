<div align="center">

# ⚡ Boron Panel

### A focused, security-first hosting control panel for one Ubuntu server

Manage domains, PHP, email, databases, DNS, SSL, WordPress, files, backups,
applications, and the server itself from one fast operator and customer UI.

<br />

**OpenLiteSpeed · FastAPI · React · MariaDB · PowerDNS · Postfix/Dovecot**

</div>

<p align="center">
  <a href="#quick-start">Install</a> ·
  <a href="#feature-tour">Features</a> ·
  <a href="docs/FRESH-INSTALL-CHECKLIST.md">Install checklist</a> ·
  <a href="docs/ADMIN-GUIDE.md">Admin guide</a> ·
  <a href="docs/CUSTOMER-GUIDE.md">Customer guide</a>
</p>

## What Boron is

Boron is a single-server Linux hosting panel for operators who want the
workflow of a commercial control panel without a per-server license. It is
designed for Ubuntu 24.04 LTS and uses mature, freely available components:
OpenLiteSpeed and LSAPI for web traffic, MariaDB for hosted databases,
PowerDNS for zones, Postfix/Dovecot for mail, Pure-FTPd for file transfer, and
FastAPI + React for the panel experience.

The current release is **1.0.1**. The panel is intentionally opinionated:
one server, clear privilege boundaries, per-account Linux users, generated
configuration with validation and rollback, and secrets kept outside the
application tree.

## Why operators choose it

| | Boron’s approach |
|---|---|
| **Control** | Own the host, packages, data paths, backups, and update channel. |
| **Isolation** | Customers are separate Linux accounts; privileged work is delegated to a root-only Unix-socket daemon. |
| **Predictability** | OLS changes follow a validate → reload → verify → rollback path. |
| **Security** | RBAC, TOTP, rate limits, audit logs, hardened PHP defaults, UFW/fail2ban, and scoped customer paths. |
| **No lock-in** | Standard Linux services and portable data formats; no proprietary runtime is required. |

## Feature tour

### Customer workspace

- **Domains:** primary, addon, parked, and subdomains; redirects, maintenance
  mode, custom error pages, hotlink protection, and per-domain PHP settings.
- **DNS and SSL:** PowerDNS zones, optional Cloudflare zones, ACME certificates,
  renewals, wildcard DNS-01, and certificate expiry visibility.
- **Email:** virtual mailboxes, quotas, forwarders, catch-all routing,
  autoresponders, spam filtering, delivery logs, and IMAP migrations.
- **Databases:** account-scoped MariaDB databases and users with one-time
  credentials plus a scoped phpMyAdmin launch.
- **WordPress:** install in a document root or subdirectory, then manage core,
  plugins, themes, cache, maintenance mode, admin passwords, and safe
  search/replace through WP-CLI.
- **Files and access:** FileBrowser Quantum, scoped FTP with explicit TLS and
  passive ports, Git deployment, SSH keys, web terminal, cron jobs, and logs.
- **Applications:** Node.js and Python apps, Composer, Redis, PHP version
  selection, and disk/resource usage views.
- **Backups:** scheduled or on-demand account backups for files, databases,
  mail, DNS, and configuration, with browse and restore workflows.

### Operator workspace

- Accounts, plans, quotas, CPU/memory/I/O/process limits, namespace isolation,
  suspension, unsuspension, termination, notes, and customer impersonation.
- Server health, service control, bandwidth rankings, site statistics, DB and
  slow-query monitoring, mail queue, audit/account/error logs, and webhooks.
- UFW firewall rules, permanent IP/CIDR bans, IP allowlists, fail2ban, WAF,
  Cloudflare, cPanel import, notifications, and editable templates/branding.
- Signed-release update checks, staged updates, health checks, rollback, and
  release history from the configured GitHub repository.

## Security architecture

~~~mermaid
flowchart LR
    B[Browser] -->|TLS :9443| A[boron-api<br/>(unprivileged)]
    A <-->|Unix socket<br/>/run/boron/provisiond.sock| D[boron-provisiond<br/>(root only)]
    D --> O[OpenLiteSpeed]
    D --> M[MariaDB / mail / DNS / FTP]
    D --> F[FileBrowser Quantum<br/>loopback + owner firewall]
    A --> S[(SQLite control plane)]
~~~

- <code>boron-api</code> owns HTTP, authentication, RBAC, request validation,
  and the UI; it does not hold root or MariaDB administrator privileges.
- <code>boron-provisiond</code> is the only process that changes Linux users,
  OLS/mail/DNS configuration, quotas, certificates, or hosted database grants.
- Account paths are jailed and customer operations are scoped by account
  ownership. FileBrowser’s backend port is restricted to the API service user
  and the rule is re-applied on daemon start.
- Runtime secrets live in <code>/etc/boron/secrets.env</code> and
  <code>/etc/boron/api-secrets.env</code>, never in the application tree. See
  the [security review](docs/AUDIT3-FINDINGS.md) and
  [architecture](docs/ARCHITECTURE.md) for the threat model and boundaries.

## Quick start

### Requirements

| Requirement | Minimum | Recommended |
|---|---:|---:|
| Operating system | Ubuntu 24.04 LTS, amd64 | Ubuntu 24.04 LTS, amd64 |
| Memory | 1 GiB RAM | 2–4 GiB+ for mail, databases, and builds |
| Disk | 10 GiB free on <code>/</code> | SSD sized for hosted data and backups |
| Access | Root or unrestricted <code>sudo</code> | Provider console access as a recovery path |
| Network | Public IP and outbound HTTPS | DNS A/AAAA records ready before SSL issuance |

The installer opens SSH, panel <code>9443</code>, HTTP/HTTPS
<code>80/443</code>, mail <code>25/110/143/587/993/995</code>, DNS
<code>53</code>, FTP <code>21</code>, and FTP passive TCP
<code>30000–30100</code>. It permits SSH before enabling UFW so a fresh install
does not lock out the operator.

### One-command fresh install

Run this on a clean Ubuntu 24.04 server. The checkout is temporary, but keep it
until the installer and post-install checks finish:

~~~bash
sudo apt-get update && sudo apt-get install -y git && \
  work="$(mktemp -d)" && \
  git clone --depth 1 https://github.com/adityavs5/boronpanel.git \
    "$work/boronpanel" && \
  sudo bash "$work/boronpanel/scripts/install.sh"
~~~

The installer is source-aware: it copies the code to <code>/opt/boron</code>,
installs the Python and system dependencies, runs <code>npm ci</code> +
<code>npm run build</code>, generates configuration and secrets, configures
services, enables scheduled jobs, and starts the panel. It does not depend on a
pre-built <code>static/dist</code> directory being committed.

### Installer prompts

Interactive installs ask for:

1. **Panel domain** — e.g. <code>panel.example.com</code>; blank uses the
   server IP.
2. **Let's Encrypt/admin email** — ACME notices and operator notifications.
3. **Admin username** — defaults to <code>admin</code>.
4. **Admin password** — entered securely when not pre-seeded.
5. **Optional MaxMind GeoLite2 license key** — blank keeps the panel fully
   usable but disables top-country statistics.

Cloudflare is deliberately not required during installation. PowerDNS is the
default provider. Add a scoped Cloudflare token later through the admin
workflow and keep it in <code>/etc/boron/secrets.env</code> (mode
<code>0600</code>).

For unattended installs, use the installer’s current environment interface:

~~~bash
export FH_NONINTERACTIVE=1
export FH_PANEL_DOMAIN=panel.example.com
export FH_LE_EMAIL=admin@example.com
export FH_ADMIN_USER=admin
export FH_ADMIN_PASSWORD='replace-with-a-long-unique-password'
export FH_SERVER_IP=203.0.113.10              # optional
export FH_MAXMIND_LICENSE_KEY=''               # optional

sudo --preserve-env=FH_NONINTERACTIVE,FH_PANEL_DOMAIN,FH_LE_EMAIL,\
FH_ADMIN_USER,FH_ADMIN_PASSWORD,FH_SERVER_IP,FH_MAXMIND_LICENSE_KEY \
  bash scripts/install.sh
~~~

Treat the password environment variable as sensitive: avoid shell history and
shared process listings. The [fresh-install checklist](docs/FRESH-INSTALL-CHECKLIST.md)
contains the full step-by-step trace, verification commands, and rollback
notes.

## What gets installed

The installer configures the complete Boron runtime, not just the API:

- OpenLiteSpeed with LSAPI PHP 8.1–8.5 and common extensions.
- MariaDB, Postfix, Dovecot, PowerDNS (SQLite backend), and Pure-FTPd.
- Certbot with PowerDNS and Cloudflare DNS plugins, plus renewal jobs.
- Node.js 18/20/22 runtimes, npm, Composer, WP-CLI, and ImapSync.
- Redis, SpamAssassin, fail2ban, UFW, rclone, Python 3 + pip, geoipupdate,
  and the verified FileBrowser Quantum binary.
- <code>boron-api</code>, <code>boron-provisiond</code>, and
  <code>boron-filebrowser</code> systemd services.
- Cron jobs, logrotate, PHP hardening, TLS bootstrap certificates, and the
  firewall rules required by the stack.

## First login and smoke test

Open:

~~~text
https://<panel-domain-or-server-ip>:9443/app
~~~

The initial certificate is a bootstrap certificate; replace it through the
SSL workflow once DNS and ACME prerequisites are ready. Verify the service and
health endpoint before creating customer data:

~~~bash
sudo systemctl is-active boron-api boron-provisiond boron-filebrowser
curl -sk https://127.0.0.1:9443/healthz
sudo ufw status verbose
~~~

Then follow the checklist to create a disposable account, add a domain,
connect over FTP, install WordPress, open phpMyAdmin, suspend/unsuspend the
account, and run a backup/restore test.

## Configuration and runtime paths

| Path | Purpose |
|---|---|
| <code>/opt/boron</code> | Deployed application and Python virtual environment |
| <code>/etc/boron/boron.toml</code> | Non-secret operator configuration |
| <code>/etc/boron/secrets.env</code> | Root-only infrastructure credentials |
| <code>/etc/boron/api-secrets.env</code> | API-readable session secret |
| <code>/var/lib/boron/boron.db</code> | SQLite control-plane database |
| <code>/var/lib/boron/backups</code> | Backup and pre-reload rollback data |
| <code>/var/log/boron</code> | API, daemon, FileBrowser, and job logs |
| <code>/run/boron/provisiond.sock</code> | API → root-daemon RPC socket |

Never place real credentials in the repository. Use the supplied
[environment template](.env.example) and deployment examples under
<code>deploy/</code> only as placeholder documentation.

## Common operator commands

~~~bash
# Service status and logs
sudo systemctl status boron-api boron-provisiond boron-filebrowser
sudo journalctl -u boron-api -u boron-provisiond -f
sudo tail -f /var/log/boron/api.log /var/log/boron/daemon.log

# Installer plan / help (safe, no changes)
sudo bash scripts/install.sh --dry-run
sudo bash scripts/install.sh --help

# Release/update checks
sudo bash scripts/release.sh --dry-run
~~~

Use the admin **Updates** page for production updates. Releases are packaged
from a fresh source tree, SHA256 verified, and optionally GPG-signed; do not
replace a running installation with an unverified checkout.

## Development

~~~bash
git clone https://github.com/adityavs5/boronpanel.git
cd boronpanel

python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

cd frontend
npm ci
npm run build
cd ..

python -m pytest -q
bash -n scripts/install.sh scripts/release.sh
~~~

<code>static/dist/</code> is a generated build artifact and is intentionally
not part of the source-only repository. Production installs and release
packaging build it fresh. See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for
design constraints and [STATUS.md](docs/STATUS.md) for the current
verification record.

## Documentation map

- [Fresh-install checklist](docs/FRESH-INSTALL-CHECKLIST.md) — complete clean
  server runbook and post-install acceptance checks.
- [Administrator guide](docs/ADMIN-GUIDE.md) — accounts, plans, suspension,
  monitoring, updates, Cloudflare, and firewall operations.
- [Customer guide](docs/CUSTOMER-GUIDE.md) — domains, email, databases,
  WordPress, files, FTP, applications, and backups.
- [Architecture](docs/ARCHITECTURE.md) — privilege boundaries, data model,
  OLS lifecycle, TLS, and explicit scope limits.
- [Security findings](docs/AUDIT-FINDINGS.md),
  [Audit 2](docs/AUDIT2-FINDINGS.md), and
  [Audit 3](docs/AUDIT3-FINDINGS.md) — threat model and remediation history.
- [Release guide](docs/RELEASING.md) — versioning, packaging, checksums, and
  signatures.

## Scope and trade-offs

Boron is a focused single-server panel, not a multi-server orchestration
platform. Reseller billing, WHM-style fleet management, proprietary mail
hosting, and a built-in third-party webmail product are outside the v1 scope.
Roundcube/phpMyAdmin can be configured as optional operator-managed services;
the panel remains usable without them.

## License

See the repository’s source files and dependency licenses for the applicable
terms. Boron is assembled from open-source components; verify the licenses of
any package or theme you add to a production image.

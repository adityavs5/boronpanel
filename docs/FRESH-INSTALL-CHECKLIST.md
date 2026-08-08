# Boron Panel fresh-install checklist

This is the operator runbook for a new Ubuntu 24.04 amd64 server. It is a
static trace of the current `scripts/install.sh`; it does not replace the
installer and it does not require running any commands from this repository
before the clone step.

## Before you start

- [ ] The server is Ubuntu 24.04 LTS with systemd, at least 1 GiB RAM and 10
      GiB free on `/` (2 GiB+ and additional storage are recommended).
- [ ] You have root or unrestricted `sudo`, an SSH session, and provider
      console access in case a provider firewall is separate from UFW.
- [ ] The server has a public IP and outbound HTTPS access. Point the panel
      hostname and hosted domains at it when you are ready; the installer does
      not register a domain or change an external DNS provider.
- [ ] Decide whether to use the built-in PowerDNS provider (the default) or
      Cloudflare for individual zones. Cloudflare is optional.
- [ ] If using top-country site statistics, create a free MaxMind account and
      have a GeoLite2-Country license key ready. Leaving it blank is supported.

## One-command start

Run this as a normal SSH user with `sudo`:

```bash
sudo apt-get update && sudo apt-get install -y git && \
  work="$(mktemp -d)" && \
  git clone --depth 1 https://github.com/adityavs5/boronpanel.git "$work/boronpanel" && \
  sudo bash "$work/boronpanel/scripts/install.sh"
```

The installer derives its source directory from the script path, so use a real
checkout rather than piping `install.sh` to `bash`.

For an unattended run, set these before invoking the script (protect the shell
history and process environment appropriately):

```bash
export FH_NONINTERACTIVE=1
export FH_PANEL_DOMAIN=panel.example.com
export FH_LE_EMAIL=admin@example.com
export FH_ADMIN_USER=admin
export FH_ADMIN_PASSWORD='use-a-long-unique-password'
export FH_SERVER_IP=203.0.113.10       # optional; otherwise auto-detected
export FH_MAXMIND_LICENSE_KEY=''       # optional; blank disables GeoLite2
sudo --preserve-env=FH_NONINTERACTIVE,FH_PANEL_DOMAIN,FH_LE_EMAIL,FH_ADMIN_USER,FH_ADMIN_PASSWORD,FH_SERVER_IP,FH_MAXMIND_LICENSE_KEY \
  bash scripts/install.sh
```

## Installer prompts and values

The interactive prompts are intentionally small. Defaults and preseed names
below are taken directly from `scripts/install.sh`.

1. **Panel domain** (`FH_PANEL_DOMAIN`): a hostname such as
   `panel.example.com`; blank is allowed and leaves the panel reachable by IP.
   It is used for the bootstrap certificate common name and the completion
   URL. It does not create DNS records.
2. **Let's Encrypt / admin email** (`FH_LE_EMAIL`): an address for ACME
   registration/expiry notices and operator notifications.
3. **Admin username** (`FH_ADMIN_USER`): defaults to `admin`. The script's
   default means there is normally no username prompt; set the variable to
   choose another name. The username is passed to `scripts/create_admin.py`.
4. **Admin password** (`FH_ADMIN_PASSWORD`): if not preseeded, the
   `create_admin.py` step securely prompts for `Password (min 12 chars)`.
   Prefer this prompt over putting a password in shell history.
5. **Optional MaxMind GeoLite2 license key** (`FH_MAXMIND_LICENSE_KEY`): the
   hidden prompt accepts a blank value. A key installs GeoLite2-Country and a
   weekly refresh; blank means top-countries cards are unavailable while the
   rest of the panel works.

**Cloudflare is not prompted for by the installer.** This is deliberate: the
local PowerDNS provider works without a third-party credential, and Cloudflare
accounts/tokens are managed by the admin. If needed, add a scoped
`CLOUDFLARE_API_TOKEN` to `/etc/boron/secrets.env` (mode 0600) and configure a
Cloudflare account/provider from the admin panel's **Cloudflare** page. Do not
put a real token in this repository or in command-line history. The optional
`cloudflare_account_id` and `default_dns_provider` settings belong in
`/etc/boron/boron.toml`; see `deploy/*.example`.

## What the installer does, in order

The following follows `main()` and the functions it calls. A real run writes a
best-effort transcript to `/var/log/boron-install.log`.

### 1. Pre-flight and prompts

- Checks root, Ubuntu 24.04 (other Ubuntu releases warn; non-Ubuntu is
  rejected), RAM, free root disk, and conflicts on the panel/web/mail/FTP/DNS
  ports.
- Prompts for the values above, unless `FH_NONINTERACTIVE` or corresponding
  variables are set.

### 2. Package repositories and base tools

- Runs `apt-get update` with a noninteractive frontend.
- Installs Python 3, venv/pip/dev headers, build tools, curl/wget/jq, SQLite,
  UFW, ACL/quota tooling, Git, CA certificates, rsync, OpenSSL, cron,
  logrotate, Apache password helpers, `geoipupdate`, Node/npm, and Composer.
- Downloads the pinned WP-CLI PHAR to `/usr/local/bin/wp` if it is missing.

### 3. OpenLiteSpeed and PHP

- Adds LiteSpeed's official apt repository when needed.
- Installs OpenLiteSpeed and LSAPI PHP 8.1, 8.2, 8.3, 8.4, and 8.5 with the
  common, curl, MySQL, OPcache, intl, Redis, SQLite, and Imagick extensions.
- Enables and starts `lshttpd`.

### 4. Hosting stack packages

Installs and later configures MariaDB; Postfix and Dovecot (including SQL,
LMTP, IMAP, and Sieve support); PowerDNS with the SQLite backend; Pure-FTPd;
Certbot; rclone; SpamAssassin; fail2ban; Redis; and ImapSync. The unused
PowerDNS bind configuration is disabled. `imapsync` is exposed at the stable
`/usr/local/bin/imapsync` path.

### 5. Node runtimes and FileBrowser Quantum

- Downloads and SHA256-verifies Node.js 18.20.8, 20.19.6, and 22.16.0 into
  `/opt/boron-nodejs/<major>`.
- Downloads and verifies the pinned FileBrowser Quantum amd64 binary into
  `/usr/local/bin/filebrowser-quantum`. The provisioning daemon later writes
  `/etc/boron/filebrowser.yaml`, the root-owned systemd unit, and the loopback
  access rules.

### 6. Quotas and system users

- Adds `usrquota,grpquota` to the root filesystem's `/etc/fstab` entry when
  possible, remounts `/`, runs `quotacheck`, and enables quotas.
- Creates fixed low-ID `vmail` (UID/GID 150) for virtual mail and the
  unprivileged `boron-api` service user/group. Hosting accounts are separate
  Linux users with `/usr/sbin/nologin`.

### 7. Deploy code, Python environment, and web UI

- Rsyncs the checkout to `/opt/boron`, excluding `.git`, `.venv`, and frontend
  dependencies; applies safe directory modes.
- Creates `/opt/boron/.venv`, upgrades pip, and installs the pinned
  `requirements.txt` (including Certbot and both DNS plugins).
- Runs `npm ci --no-audit --no-fund` and `npm run build` in `frontend/`.
  `static/dist` is generated on this install and is not trusted from the
  source-only repository.
- Creates `/etc/boron`, `/var/lib/boron`, and `/var/log/boron`; restricts the
  log directory to root and the `boron-api` group.

### 8. Configuration and secrets

- Writes `/etc/boron/boron.toml` (0640, `root:boron-api`) with the detected or
  supplied public IP and Let's Encrypt email.
- Generates `/etc/boron/api-secrets.env` (0640, `root:boron-api`) containing
  the API session secret.
- Generates root-only MariaDB, PowerDNS, and mail credentials in
  `/etc/boron/secrets.env`; generated keys are never printed as prompts.

### 9. MariaDB

- Enables MariaDB, sets a password on the local root account, creates the
  restricted `boron_daemon` and SELECT-only `boron_mailro` users, and creates
  the `boron_mail` schema/tables for virtual mail, forwarders, catch-all, and
  autoresponder data.
- Stores root credentials in `/root/.my.cnf` (0600) for the setup process and
  writes the runtime secrets file (0600).

### 10. PowerDNS

- Initializes `/var/lib/powerdns/pdns.sqlite3` from the packaged schema.
- Writes `/etc/powerdns/pdns.d/boron.conf` for the gsqlite3 backend and the
  loopback-only REST API on `127.0.0.1:8081`, with a generated API key.
- Writes the Certbot PowerDNS credentials file under `/etc/boron/ssl/` and
  restarts `pdns`.

### 11. TLS bootstrap and suspended page

- Creates long-lived self-signed default and panel certificates under
  `/etc/boron/ssl/`; the panel key is mode 0600 and owned by `boron-api`.
- Writes `/var/www/_suspended/index.html`. Real trusted certificates are
  requested later from the panel's SSL workflow once DNS/webroot prerequisites
  are ready.

### 12. Postfix and Dovecot virtual mail

- Writes protected MariaDB lookup files for domains, mailboxes, and forwards.
- Configures Postfix virtual delivery through Dovecot LMTP, authenticated
  submission on port 587, and TLS.
- Configures Dovecot SQL authentication, Maildir storage under `/var/vmail`,
  IMAP/LMTP listeners, and required UID/GID bounds, then validates both
  daemons before enabling/restarting them.

### 13. Services, scheduled jobs, and logging

- Installs and enables `boron-provisiond`, `boron-api`, and
  `boron-filebrowser` systemd units, then reloads systemd.
- Installs logrotate plus monitoring, Cloudflare polling, update checks,
  backup scheduling, maintenance, site-statistics, SSL-renewal, and other
  Boron cron jobs under `/etc/cron.d/`.

### 14. Firewall and FTP

- Resets UFW to deny incoming/allow outgoing, permits SSH **before** enabling
  it, then opens panel 9443, HTTP/HTTPS 80/443, DNS TCP/UDP 53, mail
  25/110/143/587/993/995, FTP 21, and passive FTP TCP 30000–30100.
- Configures Pure-FTPd to accept the nologin shell, chroot every login,
  enable PureDB subaccounts, require TLS, and use passive ports 30000–30100;
  then restarts Pure-FTPd.
- Enables fail2ban. Boron's own jails are generated after the daemon starts.

### 15. PHP hardening

The installer reads `daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS` and
writes the same hardened list (including `exec`, `system`, `shell_exec`,
`passthru`, `proc_open`, and `popen`) into every installed `lsphp` php.ini it
finds, then restarts OLS. Admin per-account/per-domain overrides are applied
later through the panel's PHP settings and OLS vhost `phpIniOverride`; they
layer on the defaults rather than changing the global baseline.

### 16. First start, bootstrap, security services, and admin

- Restarts `boron-provisiond`, waits for `/run/boron/provisiond.sock`, and
  bootstraps a clean OLS baseline without the stock Example vhost.
- Enables/starts `boron-api` and FileBrowser Quantum. FileBrowser is loopback
  only and the daemon inserts the owner-allow and reject rules at the top of
  iptables `OUTPUT` on every start.
- Generates SpamAssassin and fail2ban configuration through the daemon.
- Runs `scripts/create_admin.py` to create the first admin. If the admin
  already exists, the installer warns and leaves it intact.

### 17. Optional GeoLite2

- With a MaxMind key, downloads GeoLite2-Country into Boron's data area,
  stores the key root-only at `/etc/boron/maxmind-license`, and installs the
  weekly `/etc/cron.d/boron-geoip` refresh.
- With a blank key, removes any stale GeoLite2 key/cron and reports clearly
  that only top-countries statistics are unavailable. Other statistics and
  all hosting features remain usable. The key can be added later from the
  admin site-statistics settings/API.

## Post-install verification

Use the panel at `https://<panel-domain-or-ip>:9443/app` (accept the
self-signed certificate for this first check). Confirm the three services and
health endpoint first:

```bash
systemctl --no-pager --full status boron-provisiond boron-api boron-filebrowser
curl -sk https://127.0.0.1:9443/healthz
ufw status verbose
```

Then complete this functional checklist with a disposable test domain and
account:

### Create an account and domain

- [ ] Sign in as the bootstrap admin and open **Accounts → Create account**.
- [ ] Choose a valid username, primary domain, PHP version, quota, contact
      email, and (optionally) a plan. Save the one-time initial password if
      the API generated it.
- [ ] Point the domain's A/AAAA record at the server, or create a zone in
      **DNS** if this server is authoritative. Visit the domain over HTTP and
      HTTPS after DNS propagates; the default page should load.

### Connect via FTP

- [ ] Open the account's **FTP** page and create a scoped FTP login, choosing
      a path inside the account home.
- [ ] Connect with an FTP client to the server on port 21 using explicit TLS
      (TLS is required), the generated login, and the password.
- [ ] Enable passive mode. Verify a directory listing and upload/download
      succeed through TCP ports 30000–30100, and verify `..` cannot escape the
      account's chroot.

### Install WordPress

- [ ] Open the domain detail page and its **WordPress** tab.
- [ ] Ensure the target document root is empty, enter the site title, admin
      username/email/password, and start the install.
- [ ] Wait for the background job to complete, open the site, then use the
      WordPress management card to list/update plugins or flush cache.
- [ ] For a second install, provide a non-root subdirectory; confirm it has a
      separate database and is tracked independently.

### Open phpMyAdmin

- [ ] In **Databases**, create a database and record the one-time credentials.
- [ ] Use that database row's **phpMyAdmin** action. It should open the
      configured phpMyAdmin hostname in a new tab with a short-lived sign-on
      token. If no action is available, the operator must deploy/configure the
      optional phpMyAdmin hostname first.
- [ ] Confirm the hosted database is visible and that the account cannot see
      another account's database.

### Suspend and unsuspend

- [ ] As admin, open **Accounts → <account> → Suspend**.
- [ ] Request the site's URL with a fresh browser/incognito session and
      confirm the suspended page is served (not a previously cached site).
- [ ] Click **Unsuspend**, reload the site, and confirm normal content returns.

### Run a backup and restore test

- [ ] As admin, open the backup administration page and create a **local**
      destination pointing to a dedicated path such as `/var/backups/boron`.
      Do not place backups inside an account's webroot.
- [ ] Optionally assign a daily schedule and retention count to the test
      account. The installed root cron evaluates due schedules hourly.
- [ ] As the account user, open **Backups → Create backup**, choose **Full
      account**, select the destination, and start it.
- [ ] Wait for `completed`, browse the manifest, and verify files, domains,
      databases, and mail are represented. For a safe restore check, create a
      disposable file and restore only that file or use a disposable account;
      restores overwrite current data.

## Useful final checks

- [ ] `ufw status` includes SSH, 9443, 80/443, mail, FTP 21, and
      `30000:30100/tcp`.
- [ ] `systemctl is-active lshttpd mariadb postfix dovecot pdns pure-ftpd \
      redis-server fail2ban boron-provisiond boron-api boron-filebrowser`
      reports the expected services active.
- [ ] `ls -l /etc/boron/boron.toml /etc/boron/secrets.env
      /etc/boron/api-secrets.env` shows restrictive ownership/modes.
- [ ] If GeoLite2 was selected, `ls -l /var/lib/boron/GeoLite2-Country.mmdb`
      and `/etc/cron.d/boron-geoip` exist; otherwise the admin site-statistics
      page reports that top-countries is not configured.
- [ ] Replace the bootstrap self-signed panel/FTP certificates with trusted
      certificates through the SSL workflow when DNS and email prerequisites
      are ready.

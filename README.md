# Boron Panel

A single-server Linux hosting control panel — account/domain/database/DNS/
mail/SSL/file management with a REST API, for operators replacing cPanel
licensing on Ubuntu 24.04 with 100% free/open-source components
(OpenLiteSpeed, Postfix/Dovecot, PowerDNS, MariaDB, Pure-FTPd, certbot).

Not a cPanel clone. See `docs/ARCHITECTURE.md` for what's deliberately out
of scope (multi-server/WHM, reseller billing, built-in webmail, etc.).

Start with `docs/RESEARCH.md` and `docs/ARCHITECTURE.md` for the design
reasoning; `docs/CHECKPOINT-*.md` for what was built and verified in each
phase; `docs/STATUS.md` for the current state and what to check first.

## Fresh install in one command

On a clean, supported server, clone the release source and start the
interactive installer (run as a user with `sudo` access):

```bash
sudo apt-get update && sudo apt-get install -y git && \
  work="$(mktemp -d)" && \
  git clone --depth 1 https://github.com/adityavs5/boronpanel.git "$work/boronpanel" && \
  sudo bash "$work/boronpanel/scripts/install.sh"
```

The installer builds the React UI from source, writes the Boron services and
firewall rules, and asks for the panel domain, notification/Let's Encrypt
email, admin password, and optional GeoLite2 key. Cloudflare is optional and is
configured after installation; it is never required for a local PowerDNS
install. Keep the checkout directory until the install has completed and the
post-install checks in [the fresh-install checklist](docs/FRESH-INSTALL-CHECKLIST.md)
are finished. For environment-preseeded or repeatable installs, see the
installer's `--help` output and the checklist's non-interactive example.

## Minimum server requirements

| Requirement | Minimum |
|---|---|
| Operating system | Ubuntu 24.04 LTS (systemd, amd64/x86_64) |
| Privilege | Root or an account with unrestricted `sudo` |
| Memory | 1 GiB RAM (2 GiB+ recommended for mail, databases, and builds) |
| Disk | 10 GiB free on `/` before installation (more for hosted data/backups) |
| Network | Internet access for package/repository downloads and a public IP; DNS A/AAAA records should point hosted domains at the server |
| Firewall access | SSH 22 plus panel 9443, HTTP/HTTPS 80/443, mail 25/110/143/587/993/995, FTP 21 and passive TCP 30000–30100 |

The installer enforces the Ubuntu, RAM, and disk thresholds above. It keeps
SSH open before enabling UFW, but you should still confirm your provider's
out-of-band console access before changing firewall policy.

## Stack

| Layer | Software |
|---|---|
| OS | Ubuntu 24.04 LTS |
| Web server | OpenLiteSpeed (free/OSS edition) |
| PHP | LSAPI (`lsphp`), 8.1 and 8.3 installed |
| Mail | Postfix + Dovecot, SQL-backed virtual mailboxes |
| DNS | PowerDNS, `gsqlite3` backend, REST API only |
| Hosted-account DB | MariaDB |
| Panel's own data | SQLite (control plane) + a MariaDB schema (mail only) |
| FTP | Pure-FTPd |
| SSL | certbot (HTTP-01 webroot or DNS-01 via PowerDNS) |
| Backend | Python 3.12, FastAPI + a separate root daemon |
| Frontend | React 18 SPA (Vite + Tailwind + React Query), served by FastAPI at `/app` |

## Fresh-Ubuntu setup

Run as root on a clean Ubuntu 24.04 server. This is the exact sequence
used to stand up and verify this project — every step below was actually
run and tested, not just written down. Replace `104.234.179.64` with your
own server's public IP throughout.

### 1. Base packages

```bash
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip python3-dev \
  build-essential curl wget jq sqlite3 ufw acl quota quotatool git
```

### 2. Enable disk quotas

```bash
# add usrquota,grpquota to the root filesystem's fstab line, then:
mount -o remount /
quotacheck -ugm /
quotaon -ugv /
```

### 3. OpenLiteSpeed + PHP

```bash
curl -fsSL https://repo.litespeed.sh | bash
apt-get install -y openlitespeed \
  lsphp83 lsphp83-common lsphp83-curl lsphp83-mysql lsphp83-opcache \
  lsphp83-intl lsphp83-imagick lsphp83-redis lsphp83-sqlite3 \
  lsphp81 lsphp81-common lsphp81-curl lsphp81-mysql lsphp81-opcache
systemctl enable --now lshttpd
```

### 4. MariaDB, Postfix, Dovecot, PowerDNS, Pure-FTPd, certbot

```bash
echo "postfix postfix/main_mailer_type select Internet Site" | debconf-set-selections
echo "postfix postfix/mailname string $(hostname -f)" | debconf-set-selections
apt-get install -y mariadb-server postfix dovecot-core dovecot-imapd \
  dovecot-lmtp dovecot-mysql postfix-mysql pdns-server pdns-backend-mysql \
  pdns-backend-sqlite3 pure-ftpd
# remove the unused bind backend pulled in as a dependency
mv /etc/powerdns/pdns.d/bind.conf /etc/powerdns/pdns.d/bind.conf.disabled 2>/dev/null || true
```

certbot is installed into Boron's own venv in step 7, not via apt — see
`daemon/ssl.py`'s `certbot_bin` setting (`/opt/boron/.venv/bin/certbot`),
which carries the `certbot-dns-powerdns` plugin alongside it.

### 5. Secure MariaDB and create Boron's database users

The stock `mariadb-server` package on Ubuntu ships `root` with an **empty
password** — fix this first.

```bash
ROOT_PASS=$(openssl rand -base64 32 | tr -d '=+/' | head -c 32)
DAEMON_PASS=$(openssl rand -base64 32 | tr -d '=+/' | head -c 32)
MAILRO_PASS=$(openssl rand -hex 24)

mysql -u root <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${ROOT_PASS}';
CREATE USER IF NOT EXISTS 'boron_daemon'@'localhost' IDENTIFIED BY '${DAEMON_PASS}';
-- Explicit privilege list, not ALL PRIVILEGES -- this user's own grant is
-- exactly what it can later re-grant to hosted-account DB users
-- (daemon/mariadb.py's HOSTED_DB_PRIVILEGES). Widen both together if you
-- need view/routine/trigger/event support for hosted databases.
GRANT CREATE, DROP, ALTER, INDEX, CREATE USER, GRANT OPTION, SELECT,
  INSERT, UPDATE, DELETE, RELOAD, PROCESS, LOCK TABLES, REFERENCES,
  CREATE TEMPORARY TABLES ON *.* TO 'boron_daemon'@'localhost';
CREATE DATABASE IF NOT EXISTS boron_mail CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'boron_mailro'@'localhost' IDENTIFIED BY '${MAILRO_PASS}';
GRANT SELECT ON boron_mail.* TO 'boron_mailro'@'localhost';
FLUSH PRIVILEGES;
SQL

mysql -u root -p"${ROOT_PASS}" boron_mail <<'SQL'
CREATE TABLE IF NOT EXISTS mail_domain (
  id INT AUTO_INCREMENT PRIMARY KEY,
  domain VARCHAR(253) NOT NULL UNIQUE,
  active TINYINT(1) NOT NULL DEFAULT 1
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS mail_user (
  id INT AUTO_INCREMENT PRIMARY KEY,
  domain_id INT NOT NULL,
  local_part VARCHAR(64) NOT NULL,
  password VARCHAR(255) NOT NULL,
  quota_mb INT NOT NULL DEFAULT 1024,
  active TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_mailbox (domain_id, local_part),
  FOREIGN KEY (domain_id) REFERENCES mail_domain(id) ON DELETE CASCADE
) ENGINE=InnoDB;
SQL

cat > /root/.my.cnf <<EOF
[client]
user=root
password=${ROOT_PASS}
EOF
chmod 600 /root/.my.cnf

mkdir -p /etc/boron/ssl
echo "MARIADB_ROOT_PASSWORD=${ROOT_PASS}" >> /etc/boron/secrets.env
echo "MARIADB_DAEMON_PASSWORD=${DAEMON_PASS}" >> /etc/boron/secrets.env
echo "MARIADB_MAILRO_PASSWORD=${MAILRO_PASS}" >> /etc/boron/secrets.env
chmod 600 /etc/boron/secrets.env
unset ROOT_PASS DAEMON_PASS MAILRO_PASS
```

### 6. Create the `vmail` and `boron-api` system accounts

```bash
# Low system uid (NOT a high number like 30000) -- ARCHITECTURE.md SS5/
# CHECKPOINT-e.md explain why: useradd allocates the *next* uid after the
# highest existing one, so a high vmail uid pushes every hosting account's
# uid up after it.
groupadd --system --gid 150 vmail
useradd --system --uid 150 --gid 150 --home-dir /var/vmail --shell /usr/sbin/nologin vmail
mkdir -p /var/vmail && chown vmail:vmail /var/vmail && chmod 750 /var/vmail

groupadd -f boron-api
useradd --system --no-create-home --shell /usr/sbin/nologin -g boron-api boron-api
```

### 7. Deploy Boron

`/root` is typically mode 700 -- never deploy the running app there
(`boron-api`, the unprivileged process, must be able to traverse into
it). Clone/copy this repo somewhere with a normal home-dir mode, e.g.
`/root/boron-src` if developing as root, then deploy to `/opt/boron`:

```bash
git clone <this-repo> /root/boron-src   # or wherever you keep the checkout
cd /root/boron-src
# scripts/deploy.sh assumes the checkout is at /root/cpanel-clone --
# edit SRC at the top if yours is elsewhere.
bash scripts/deploy.sh

python3 -m venv /opt/boron/.venv
/opt/boron/.venv/bin/pip install --upgrade pip
/opt/boron/.venv/bin/pip install -r /opt/boron/requirements.txt
```

`scripts/deploy.sh` re-applies world-readable permissions on every run
(there's nothing sensitive in the code tree — secrets live under
`/etc/boron`) but does **not** touch `.venv`; re-run the pip install
above yourself if `requirements.txt` changes.

### 7b. Build the web UI (React SPA)

The control panel is a React single-page app (`frontend/`, Vite + Tailwind +
React Query) that is compiled to `static/dist/` and served by FastAPI. The
build output is a static bundle — the running Python services do not need
Node at runtime, only at build time.

```bash
# one-time: Node 18+ and npm
apt-get install -y nodejs npm            # Ubuntu 24.04 ships Node 18.19 (fine)

cd frontend
npm install                              # installs the SPA's dependencies
npm run build                            # -> ../static/dist/{index.html,assets/*}
```

`npm run build` writes `static/dist/`, which `scripts/deploy.sh` then syncs to
`/opt/boron/static/dist/` like the rest of the tree. FastAPI serves the SPA
at **`/app`** (a catch-all in `api/main.py` returns `index.html` for every
`/app/*` path so client-side routes deep-link correctly); its assets are served
by the existing `/static` mount. There are **no backend/API changes** — the SPA
talks to the same `/api/v1/...` endpoints and authenticates with the existing
signed-session cookie.

For local UI development against the live API:

```bash
cd frontend
npm run dev        # Vite dev server on :5173, proxies /api + /login to :9443
```

### 8. Configuration files

```bash
cat > /etc/boron/boron.toml <<EOF
server_public_ip = "104.234.179.64"
letsencrypt_email = "you@example.com"
EOF

SESSION_SECRET=$(openssl rand -hex 32)
echo "SESSION_SECRET=${SESSION_SECRET}" > /etc/boron/api-secrets.env
chown root:boron-api /etc/boron/api-secrets.env
chmod 640 /etc/boron/api-secrets.env
unset SESSION_SECRET

chown root:boron-api /etc/boron/boron.toml
chmod 640 /etc/boron/boron.toml

# PowerDNS REST API key
PDNS_KEY=$(openssl rand -hex 24)
mkdir -p /var/lib/powerdns
sqlite3 /var/lib/powerdns/pdns.sqlite3 < /usr/share/pdns-backend-sqlite3/schema/schema.sqlite3.sql
chown pdns:pdns /var/lib/powerdns/pdns.sqlite3 && chmod 660 /var/lib/powerdns/pdns.sqlite3
cat > /etc/powerdns/pdns.d/boron.conf <<EOF
launch+=gsqlite3
gsqlite3-database=/var/lib/powerdns/pdns.sqlite3
gsqlite3-dnssec=no
webserver=yes
webserver-address=127.0.0.1
webserver-port=8081
webserver-allow-from=127.0.0.1
api=yes
api-key=${PDNS_KEY}
default-soa-content=ns1.boron.invalid hostmaster.@ 0 10800 3600 604800 3600
EOF
echo "POWERDNS_API_KEY=${PDNS_KEY}" >> /etc/boron/secrets.env
mkdir -p /etc/boron/ssl
cat > /etc/boron/ssl/powerdns-credentials.ini <<EOF
dns_powerdns_api_url = http://127.0.0.1:8081/api/v1
dns_powerdns_api_key = ${PDNS_KEY}
EOF
chmod 600 /etc/boron/ssl/powerdns-credentials.ini /etc/boron/secrets.env
unset PDNS_KEY
systemctl restart pdns
```

### 9. Postfix + Dovecot virtual mail

```bash
MAILRO_PASS=$(grep MARIADB_MAILRO_PASSWORD /etc/boron/secrets.env | cut -d= -f2)
mkdir -p /etc/postfix/boron
cat > /etc/postfix/boron/mysql-virtual-domains.cf <<EOF
user = boron_mailro
password = ${MAILRO_PASS}
hosts = unix:/run/mysqld/mysqld.sock
dbname = boron_mail
query = SELECT 1 FROM mail_domain WHERE domain='%s' AND active=1
EOF
cat > /etc/postfix/boron/mysql-virtual-mailboxes.cf <<EOF
user = boron_mailro
password = ${MAILRO_PASS}
hosts = unix:/run/mysqld/mysqld.sock
dbname = boron_mail
query = SELECT CONCAT(d.domain, '/', m.local_part, '/') FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%s' AND m.active = 1 AND d.active = 1
EOF
chown root:postfix /etc/postfix/boron/*.cf && chmod 640 /etc/postfix/boron/*.cf

postconf -e "virtual_mailbox_domains = proxy:mysql:/etc/postfix/boron/mysql-virtual-domains.cf"
postconf -e "virtual_mailbox_maps = proxy:mysql:/etc/postfix/boron/mysql-virtual-mailboxes.cf"
postconf -e "virtual_mailbox_base = /var/vmail"
postconf -e "virtual_uid_maps = static:150"
postconf -e "virtual_gid_maps = static:150"
postconf -e "virtual_minimum_uid = 150"
postconf -e "virtual_transport = lmtp:unix:private/dovecot-lmtp"
postconf -e "smtpd_sasl_type = dovecot"
postconf -e "smtpd_sasl_path = private/auth"
postconf -e "smtpd_sasl_auth_enable = yes"

cat > /etc/dovecot/dovecot-sql.conf.ext <<EOF
driver = mysql
connect = host=/run/mysqld/mysqld.sock dbname=boron_mail user=boron_mailro password=${MAILRO_PASS}
default_pass_scheme = ARGON2ID
password_query = SELECT CONCAT(m.local_part, '@', d.domain) AS user, m.password AS password FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%u' AND m.active = 1 AND d.active = 1
user_query = SELECT '/var/vmail/%d/%n' AS home, 'maildir:/var/vmail/%d/%n' AS mail, 150 AS uid, 150 AS gid, CONCAT('*:bytes=', m.quota_mb, 'M') AS quota_rule FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%u' AND m.active = 1 AND d.active = 1
iterate_query = SELECT CONCAT(m.local_part, '@', d.domain) AS user FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id WHERE m.active = 1 AND d.active = 1
EOF
chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext && chmod 640 /etc/dovecot/dovecot-sql.conf.ext

sed -i 's/^!include auth-system.conf.ext/!include auth-sql.conf.ext/' /etc/dovecot/conf.d/10-auth.conf
sed -i 's|^mail_location = mbox:~/mail:INBOX=/var/mail/%u|mail_location = maildir:/var/vmail/%d/%n|' /etc/dovecot/conf.d/10-mail.conf
sed -i '/#first_valid_uid = 500/a first_valid_uid = 150\nlast_valid_uid = 150' /etc/dovecot/conf.d/10-mail.conf
sed -i '/#first_valid_gid = 1/a first_valid_gid = 150\nlast_valid_gid = 150' /etc/dovecot/conf.d/10-mail.conf

cat > /etc/dovecot/conf.d/90-boron.conf <<'EOF'
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    group = postfix
    mode = 0600
    user = postfix
  }
}
service auth {
  unix_listener /var/spool/postfix/private/auth {
    group = postfix
    mode = 0660
    user = postfix
  }
}
EOF

unset MAILRO_PASS
systemctl restart dovecot postfix
```

### 10. SSL bootstrap cert + panel TLS cert

```bash
mkdir -p /etc/boron/ssl
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/boron/ssl/default.key -out /etc/boron/ssl/default.crt \
  -days 3650 -subj "/CN=boron-default"
chmod 600 /etc/boron/ssl/default.key

mkdir -p /var/www/_suspended
cat > /var/www/_suspended/index.html <<'EOF'
<!DOCTYPE html><html><head><title>Account Suspended</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 10%;">
<h1>This account has been suspended</h1><p>Please contact your hosting provider.</p>
</body></html>
EOF

mkdir -p /etc/boron/ssl/api
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/boron/ssl/api/panel.key -out /etc/boron/ssl/api/panel.crt \
  -days 3650 -subj "/CN=boron-panel"
chown boron-api:boron-api /etc/boron/ssl/api/panel.key /etc/boron/ssl/api/panel.crt
chmod 600 /etc/boron/ssl/api/panel.key
```

The panel's own TLS cert is self-signed (browsers will warn on first visit
— accept once, or replace with your own real cert for the panel's
hostname). Per-customer-site certs are real, browser-trusted Let's Encrypt
certs issued through Boron itself (`POST /api/v1/ssl/issue`).

### 11. Lock down config file permissions

```bash
chown root:boron-api /etc/boron/boron.toml
chmod 640 /etc/boron/boron.toml
```

(`/var/lib/boron` and the control-plane `boron.db`/`-wal`/`-shm`
files are created by `borond` itself on first start, already owned
`root:boron-api` with group-read permissions —
`shared/db.py`'s `init_db()` handles this automatically.)

### 12. systemd units

```bash
cp /opt/boron/deploy/boron-provisiond.service /etc/systemd/system/
cp /opt/boron/deploy/boron-api.service /etc/systemd/system/
```

(`deploy/` ships in this repo with the exact unit files this project
itself runs on — copied directly from a working install, not
reconstructed from memory.)

```bash
systemctl daemon-reload
systemctl enable --now boron-provisiond
# one-time: replace OLS's stock "Example" vhost with a clean baseline
# (ARCHITECTURE.md SS7 / CHECKPOINT-b.md explain why this is required)
/opt/boron/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/boron')
from shared.rpc import RpcClient
RpcClient('/run/boron/provisiond.sock').call('system.bootstrap_ols', _actor='setup', _role='admin')
"
systemctl enable --now boron-api
```

### 13. Create the first admin login

```bash
/opt/boron/.venv/bin/python /opt/boron/scripts/create_admin.py --username admin
```

Then open `https://<server-ip>:9443/login` (accept the self-signed cert
warning on first visit, or swap in your own cert for this hostname).

### 14. Roundcube webmail (optional, Phase 2 feature 3)

Deployed once for the whole server, not per hosting account. Any mailbox
created via Boron (`mail.create_mailbox` / the mail UI) logs in
automatically — Roundcube authenticates straight against Dovecot, there's
no Boron-side account wiring involved.

```bash
# Ubuntu ships Roundcube in universe -- skip dbconfig-common's own
# interactive DB setup, configure it by hand instead (same reasoning as
# every other service's credentials in this project: no surprise prompts,
# no dependency on dbconfig-common's own conventions).
echo "roundcube-core roundcube/dbconfig-install boolean false" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt-get install -y roundcube-core roundcube-mysql

# dedicated DB, own credentials -- NOT the boron_mail schema Postfix/
# Dovecot use for actual mail routing/auth, this is only Roundcube's own
# address book/cache/settings store
RC_DB_PASS=$(openssl rand -hex 24)
mysql -e "
CREATE DATABASE IF NOT EXISTS roundcube CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS 'roundcube'@'localhost' IDENTIFIED BY '${RC_DB_PASS}';
GRANT ALL PRIVILEGES ON roundcube.* TO 'roundcube'@'localhost';
"
mysql roundcube < /usr/share/roundcube/SQL/mysql.initial.sql

# point Roundcube's DB include at those real credentials
cat > /etc/roundcube/debian-db.php << EOF
<?php
\$dbuser='roundcube';
\$dbpass='${RC_DB_PASS}';
\$basepath='';
\$dbname='roundcube';
\$dbserver='localhost';
\$dbport='';
\$dbtype='mysql';
EOF
chown root:www-data /etc/roundcube/debian-db.php /etc/roundcube/config.inc.php
chmod 640 /etc/roundcube/debian-db.php /etc/roundcube/config.inc.php
unset RC_DB_PASS

# post-install hardening: remove the setup wizard
rm -rf /usr/share/roundcube/installer

# the shared/writable dirs the Debian package ships must be owned by the
# SAME uid the roundcube_php extProcessor runs as (www-data) -- confirmed
# live in CHECKPOINT-phase2-3.md: leaving these root:root (the package
# default) fails OLS's own minimum-uid check at reload time
chown -R www-data:www-data /var/lib/roundcube/public_html /var/lib/roundcube/temp

# Roundcube's stock config already targets IMAP localhost:143 / SMTP
# localhost:587 (Dovecot/Postfix) -- enable Postfix's submission (587)
# service if it isn't already (stock master.cf ships it commented out):
#   uncomment the `submission inet ...` block + its `-o` lines in
#   /etc/postfix/master.cf, then `postfix check && systemctl reload postfix`

# set boron.toml's webmail_hostname (e.g. webmail.yourdomain.com, or
# webmail.<ip-with-dashes>.sslip.io for a quick real-domain test), then:
/opt/boron/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/boron')
from shared.rpc import RpcClient
RpcClient('/run/boron/provisiond.sock').call('system.bootstrap_webmail', _actor='setup', _role='admin')
"

# optional: a real trusted cert for the webmail hostname (same RPC every
# hosted domain uses, just pointed at this one instead)
/opt/boron/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/boron')
from shared.rpc import RpcClient
RpcClient('/run/boron/provisiond.sock').call('ssl.issue', domain='<webmail_hostname>', _actor='setup', _role='admin')
"
```

See `docs/CHECKPOINT-phase2-3.md` for the three ownership/permission bugs
this project hit standing this up (and how each was diagnosed), if
`system.bootstrap_webmail` or the webmail vhost misbehaves.

### 15. Resource usage reporting (Phase 2 feature 5)

Per-account disk/inode/bandwidth/database/process usage. The panel's own
lazy on-read cache (15 min) is enough for the UI page itself, but a system
cron keeps the historical trend data (`usage_snapshots`/`bandwidth_daily`)
accumulating even when nobody opens it:

```bash
cat > /etc/cron.d/boron-usage << 'EOF'
*/15 * * * * root /opt/boron/scripts/usage_snapshot.py >> /var/log/boron/usage-snapshot.log 2>&1
EOF
chmod 644 /etc/cron.d/boron-usage
```

Root-owned system cron, not a per-account Boron-managed crontab
(feature 2's `daemon/cron.py`) -- this is infrastructure that needs to run
`du`/`ps` across every account's home directory, the same trust level as
borond itself, not a customer-facing resource.

### 16. Resource limits via cgroups v2 (Phase 2 feature 6)

Per-account CPU/memory/IO/pids limits, enforced via one systemd slice per
account (`boron-<username>.slice`, nested under `boron.slice`).
Nothing to install -- `daemon/cgroups.py` creates/updates/removes each
account's slice automatically on `account.create`/`account.set_limits`/
`account.terminate`, and borond's own startup reconciles every active
account's slice back into existence after a host reboot.

**If upgrading an existing Boron install** (this project uses
`Base.metadata.create_all()`, not a migration framework -- it only
creates *new* tables, never adds columns to existing ones), run this once
against the live DB before restarting `boron-provisiond`, or account
queries will fail with `no such column: accounts.cpu_pct`:

```bash
sqlite3 /var/lib/boron/boron.db << 'EOF'
ALTER TABLE accounts ADD COLUMN cpu_pct INTEGER DEFAULT 25;
ALTER TABLE accounts ADD COLUMN mem_mb INTEGER DEFAULT 512;
ALTER TABLE accounts ADD COLUMN io_mb INTEGER DEFAULT 50;
ALTER TABLE accounts ADD COLUMN pids_max INTEGER DEFAULT 50;
EOF
```

A **fresh** install needs no such step -- `init_db()`'s `create_all()`
creates the `accounts` table with these columns from the start.

Confirm the IO-limited block device matches this deployment target:
`cgroup_io_device` in `boron.toml` defaults to `/dev/vda` (this
project's own dev/test server); check `findmnt -no SOURCE /` and
`lsblk` and set it to the actual whole-disk device (not a partition --
e.g. `/dev/sda`, not `/dev/sda1`) if it differs.

See `docs/CHECKPOINT-phase2-6.md` for the architecture decision behind
*how* LSAPI worker processes get placed into their account's cgroup (a
periodic root-privileged reconciler, not a setuid/capability helper
binary -- and why the latter was deliberately rejected) if limits don't
seem to be taking effect on a live worker.

### 17. Backup system (Phase 2 feature 7)

Full account and granular (file/database/mailbox) backup/restore, local
or rclone-backed remote destinations, per-account/server-default
scheduling with retention. Nothing to install for local destinations;
rclone itself needs installing for remote ones:

```bash
apt-get install -y rclone
mkdir -p /var/lib/boron/backup-staging
```

Create at least one destination and, optionally, a server-default
schedule (admin only, via API or `/ui/backups`):

```bash
# local destination
curl -sk -b <admin-cookie-jar> -H "Content-Type: application/json" \
  -d '{"name":"local1","kind":"local","local_path":"/var/backups/boron"}' \
  -X POST https://<host>:9443/api/v1/backups/destinations

# rclone-backed remote (S3-compatible example) -- credentials are passed
# once here and stored only in rclone's own config (/etc/rclone.conf),
# never duplicated into Boron's DB
curl -sk -b <admin-cookie-jar> -H "Content-Type: application/json" -d '{
  "name": "s3backup", "kind": "rclone", "rclone_remote_type": "s3",
  "rclone_config": {"provider": "AWS", "access_key_id": "...", "secret_access_key": "...", "region": "us-east-1"},
  "rclone_path_prefix": "boron-backups"
}' -X POST https://<host>:9443/api/v1/backups/destinations

# server-wide default schedule (any account without its own override uses this)
curl -sk -b <admin-cookie-jar> -H "Content-Type: application/json" \
  -d '{"frequency":"daily","retention_count":7,"destination_id":1}' \
  -X POST https://<host>:9443/api/v1/backups/schedules
```

Google Drive needs a service-account JSON key for non-interactive setup
(`rclone_config: {"service_account_credentials": "<json>", ...}`) --
rclone's normal interactive OAuth flow has no place in a headless daemon.

Scheduled backups need an hourly cron to check for due accounts:

```bash
cat > /etc/cron.d/boron-backups << 'EOF'
0 * * * * root /opt/boron/scripts/backup_scheduler.py >> /var/log/boron/backup-scheduler.log 2>&1
EOF
chmod 644 /etc/cron.d/boron-backups
```

See `docs/CHECKPOINT-phase2-7.md` for the four related bugs found live
while getting "terminate an account, then fully restore it" working end
to end (each is a variant of the same root cause: rows that survive
termination don't have on-disk state that survives it too) if a restore
doesn't bring the site back up.

### 18. phpMyAdmin auto-login (Phase 3 feature 3)

Deployed once for the whole server, not per hosting account. There is no
interactive username/password login for this install at all -- only
short-lived, single-use, scoped tokens minted by Boron's own "Manage"
button per database.

```bash
echo "phpmyadmin phpmyadmin/dbconfig-install boolean false" | debconf-set-selections
DEBIAN_FRONTEND=noninteractive apt-get install -y phpmyadmin

# phpMyAdmin's stock docroot ownership (root:root) fails OLS's own
# minimum-uid/gid check, the same failure class Roundcube hit in Phase 2
# feature 3 -- fix it the same way:
mkdir -p /usr/share/phpmyadmin/.well-known/acme-challenge
chown -R www-data:www-data /usr/share/phpmyadmin

# set boron.toml's pma_hostname (e.g. pma.yourdomain.com, or
# pma.<ip-with-dashes>.sslip.io for a quick real-domain test), then:
/opt/boron/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/boron')
from shared.rpc import RpcClient
RpcClient('/run/boron/provisiond.sock').call('system.bootstrap_pma', _actor='setup', _role='admin')
"

# optional: a real trusted cert for the phpMyAdmin hostname (same RPC
# every hosted domain uses, just pointed at this one instead)
/opt/boron/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/boron')
from shared.rpc import RpcClient
RpcClient('/run/boron/provisiond.sock').call('ssl.issue', domain='<pma_hostname>', _actor='setup', _role='admin')
"
```

Token cleanup needs a periodic cron (drops the ephemeral MariaDB user +
any still-present token file once a token expires, whether or not it was
ever redeemed):

```bash
cat > /etc/cron.d/boron-pma-tokens << 'EOF'
*/5 * * * * root /opt/boron/scripts/pma_token_cleanup.py >> /var/log/boron/pma-token-cleanup.log 2>&1
EOF
chmod 644 /etc/cron.d/boron-pma-tokens
```

See `docs/CHECKPOINT-phase3-3.md` for three bugs this project hit
standing this up (a missing PHP dependency's `include_path` requirement
under LiteSpeed's bundled `lsphp`, and a token-directory group-ownership
bug) if `system.bootstrap_pma` or a signon attempt misbehaves.

### 19. Email forwarders, autoresponders, catch-all (Phase 3 feature 4)

New MariaDB tables in the existing `boron_mail` schema, plus Postfix
`virtual_alias_maps` (not previously configured -- Phase e only wired
`virtual_mailbox_maps`) and Dovecot's Sieve plugin (`dovecot-sieve`, for
autoresponders):

```bash
mysql boron_mail <<'SQL'
CREATE TABLE IF NOT EXISTS mail_forward (
  id INT AUTO_INCREMENT PRIMARY KEY,
  domain_id INT NOT NULL,
  source_local_part VARCHAR(64) NOT NULL,
  destination VARCHAR(320) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_forward (domain_id, source_local_part, destination),
  FOREIGN KEY (domain_id) REFERENCES mail_domain(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS mail_catchall (
  id INT AUTO_INCREMENT PRIMARY KEY,
  domain_id INT NOT NULL UNIQUE,
  destination VARCHAR(320) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (domain_id) REFERENCES mail_domain(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS mail_autoresponder (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mail_user_id INT NOT NULL UNIQUE,
  subject VARCHAR(255) NOT NULL,
  body TEXT NOT NULL,
  start_date DATE NULL,
  end_date DATE NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (mail_user_id) REFERENCES mail_user(id) ON DELETE CASCADE
) ENGINE=InnoDB;
SQL

MAILRO_PASS=$(grep MARIADB_MAILRO_PASSWORD /etc/boron/secrets.env | cut -d= -f2)
cat > /etc/postfix/boron/mysql-virtual-forwards.cf <<EOF
user = boron_mailro
password = ${MAILRO_PASS}
hosts = unix:/run/mysqld/mysqld.sock
dbname = boron_mail
query = SELECT GROUP_CONCAT(destination SEPARATOR ',') FROM ( SELECT f.destination AS destination FROM mail_forward f JOIN mail_domain d ON f.domain_id = d.id WHERE CONCAT(f.source_local_part, '@', d.domain) = '%s' AND f.active = 1 AND d.active = 1 UNION ALL SELECT CONCAT(m.local_part, '@', d.domain) AS destination FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id JOIN mail_catchall c ON c.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%s' AND m.active = 1 AND d.active = 1 AND c.active = 1 UNION ALL SELECT c.destination AS destination FROM mail_catchall c JOIN mail_domain d ON c.domain_id = d.id WHERE CONCAT('@', d.domain) = '%s' AND c.active = 1 AND d.active = 1 ) combined
EOF
chown root:postfix /etc/postfix/boron/mysql-virtual-forwards.cf
chmod 640 /etc/postfix/boron/mysql-virtual-forwards.cf
postconf -e "virtual_alias_maps = proxy:mysql:/etc/postfix/boron/mysql-virtual-forwards.cf"
unset MAILRO_PASS
postfix check && systemctl reload postfix

# Autoresponders: Dovecot's own Sieve `vacation` extension, not the
# classic vacation(1) binary (see CHECKPOINT-phase3-4.md for why)
apt-get install -y dovecot-sieve
sed -i 's/^  #mail_plugins = \$mail_plugins$/  mail_plugins = $mail_plugins sieve/' /etc/dovecot/conf.d/20-lmtp.conf
doveconf -n > /dev/null && systemctl restart dovecot
```

The middle branch of that query (mailboxes joined through an active
`mail_catchall` row) exists specifically so enabling a catch-all doesn't
also swallow mail for real, existing mailboxes at that domain -- see
`docs/CHECKPOINT-phase3-4.md` for the real bug this fixes (Postfix's own
documented catch-all trap) if this ever needs to be reconstructed by
hand.

### 20. FTP account management (Phase 3 feature 5)

Sub-accounts scoped to a path within their hosting account's home,
implemented as Pure-FTPd virtual (PureDB) users layered alongside the
existing system-account (`-l unix`) login every hosting account already
has. **Two Phase 1 setup gaps fixed here** -- see
`docs/CHECKPOINT-phase3-5.md` for the live testing that found them --
apply both even if you're not using FTP sub-accounts at all, since #2
affects every hosting account's own FTP login:

```bash
# 1. A hosting account's own FTP login never actually worked: pure-ftpd's
#    PAM config rejects any shell not listed in /etc/shells, and every
#    hosting account uses /usr/sbin/nologin (no interactive SSH, by
#    design). This does not grant shell access -- nologin still refuses
#    an interactive session -- it only satisfies pam_shells.so's check.
echo "/usr/sbin/nologin" >> /etc/shells

# 2. Far more serious: hosting accounts' own FTP logins were NOT
#    chrooted to their home directory at all (no ChrootEveryone setting
#    ever existed, despite ARCHITECTURE.md's own locked decision saying
#    they should be) -- confirmed live that a real account could `CWD ..`
#    all the way to the server's real filesystem root and browse
#    everything, including other accounts' home directories.
echo "yes" > /etc/pure-ftpd/conf/ChrootEveryone

# 3. Enable the PureDB backend (for FTP sub-accounts specifically) as an
#    additional, higher-priority auth source -- existing system-account
#    logins keep working via the existing 65unix/70pam chain.
ln -sf ../conf/PureDB /etc/pure-ftpd/auth/30pdb

systemctl restart pure-ftpd
```

## Verifying the install

```bash
systemctl status lshttpd postfix dovecot mariadb pdns pure-ftpd boron-provisiond boron-api
curl -sk https://127.0.0.1:9443/healthz
```

Create a test account end-to-end via the REST API (see
`docs/CHECKPOINT-h.md` for the exact sequence this project itself used to
validate a fresh install, including a real Let's Encrypt issuance against a
`sslip.io` test domain when you don't yet have a real domain pointed at the
server).

## Repository layout

```
daemon/      borond -- the root provisioning daemon (Unix-socket RPC only)
api/         boron-api -- the unprivileged REST API + admin UI
shared/      code used by both (models, config, validation, RPC framing)
templates/   Jinja2 templates for OLS/system config (not the web UI)
api/templates_ui/  Jinja2 templates for the admin UI
static/      vendored CSS, no CDN dependencies
scripts/     deploy.sh, create_admin.py, ssl_deploy_hook.py
tests/       pytest unit tests (no root/live-service dependency)
docs/        RESEARCH.md, ARCHITECTURE.md, CHECKPOINT-*.md, STATUS.md
```

## Development

```bash
cd /root/cpanel-clone   # or wherever your checkout lives
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

After editing code that the running services need, redeploy:

```bash
bash scripts/deploy.sh
systemctl restart boron-provisiond boron-api
```

### 21. SSL expiry notifications (Phase 7b feature 3)

Daily cron so the 14-day expiry-warning email actually goes out on its
own, not just when someone happens to open the SSL dashboard:

```bash
cat > /etc/cron.d/boron-ssl-expiry << 'EOF'
0 6 * * * root /opt/boron/scripts/ssl_expiry_check.py >> /var/log/boron/ssl-expiry-check.log 2>&1
EOF
chmod 644 /etc/cron.d/boron-ssl-expiry
```

Also requires `notifications.settings.set` (admin) to configure a sender
address and `notifications.prefs.set` (per account) to set a customer
email before any notification actually sends — see
`docs/CHECKPOINT-phase7b-3-email-notifications.md`.

### 22. Account usage alerts (Phase 7b feature 5)

Same "server infrastructure, not a per-account crontab" category as
`boron-usage`'s own entry above — needs no explicit ordering relative
to it, since `usage.get_usage()` already lazily refreshes stale data
itself when read:

```bash
cat > /etc/cron.d/boron-usage-alerts << 'EOF'
*/15 * * * * root /opt/boron/scripts/usage_alert_check.py >> /var/log/boron/usage-alert-check.log 2>&1
EOF
chmod 644 /etc/cron.d/boron-usage-alerts
```

### 23. Panel update system

Boron updates itself from **GitHub release tarballs** (never `git
pull` on production — see `docs/RELEASING.md` for how releases are cut
with `scripts/release.sh`). Point the panel at the releases repo in
`/etc/boron/boron.toml`:

```toml
update_github_repo = "adityavs5/boronpanel"
```

then install the daily check (release poll + admin email once per new
release + pruning of version dirs older than the 3-day rollback window):

```bash
install -m 0644 /opt/boron/deploy/boron-update.cron /etc/cron.d/boron-update
```

The admin panel's **Updates** page shows current/latest version, applies
updates one-click (test-suite pre-flight, DB + `/etc/boron` backup to
`/var/backups/boron/`, SHA256-verified download, staged extraction to
`/opt/boron-X.Y.Z`, atomic symlink swap of `/opt/boron`, health
check with automatic swap-back on failure), and can roll back to the
previous version for 3 days. Requires a fresh 2FA code when the admin has
TOTP enabled. Every step is logged to `/var/log/boron/updates.log`.
Note: the first update converts `/opt/boron` from a plain directory
to the versioned-symlink layout automatically. Only the two panel
services restart during an update — OpenLiteSpeed and hosted sites are
never touched.

### 24. GeoLite2 setup (site statistics' top-countries breakdown)

The per-domain and server-wide site statistics pages show a "top
countries" breakdown of visitor IPs, powered by MaxMind's free
**GeoLite2-Country** database. This is genuinely optional — MaxMind has
required a free account + license key for downloads since ~2019 (no
anonymous path exists), so Boron never acquires one on the operator's
behalf (same policy as the Cloudflare API token: real third-party
credentials are always operator-supplied, never auto-registered). Without
one configured, the top-countries card simply doesn't render (a small
"GeoLite2 not configured" note takes its place); every other site-stats
metric (pageviews, bandwidth, per-domain breakdown) works identically
either way.

**To configure at install time**: sign up for a free MaxMind account at
<https://www.maxmind.com/en/geolite2/signup>, generate a license key under
Account → License Keys, then run the installer with it set:

```bash
FH_MAXMIND_LICENSE_KEY=your_license_key_here ./scripts/install.sh
```

The interactive installer asks for the same optional key. Its `setup_geoip()`
step installs the `geoipupdate` package, fetches and installs the database
(`/var/lib/boron/GeoLite2-Country.mmdb`) when the key is present, and installs
a root-only weekly refresh at `/etc/cron.d/boron-geoip`. The key is stored
`0600` in `/etc/boron/maxmind-license`; it is never placed in the cron file.
If the prompt is left blank, the installer clearly reports that only the
top-countries card is unavailable and the rest of Boron continues normally.

**To configure after install** (or to rotate a key), no re-install needed:

```bash
curl -sk -X POST https://<panel-host>:9443/api/v1/admin/sitestats/geoip \
  -H 'Content-Type: application/json' \
  -H "Cookie: <your admin session cookie>" \
  -d '{"license_key": "your_license_key_here"}'
```

(`daemon/geoip.py`'s `download_database` — the same function the installer
calls — verifies the key by actually downloading with it, so a bad key
fails clearly rather than leaving a half-configured state.) The database
is a single file with no expiry; MaxMind's own guidance is to refresh it
periodically (their data updates roughly weekly), which isn't automated
here — re-run either step above whenever a refresh is wanted.

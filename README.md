# Forgehost

A single-server Linux hosting control panel — account/domain/database/DNS/
mail/SSL/file management with a REST API, for operators replacing cPanel
licensing on Ubuntu 24.04 with 100% free/open-source components
(OpenLiteSpeed, Postfix/Dovecot, PowerDNS, MariaDB, Pure-FTPd, certbot).

Not a cPanel clone. See `docs/ARCHITECTURE.md` for what's deliberately out
of scope (multi-server/WHM, reseller billing, built-in webmail, etc.).

Start with `docs/RESEARCH.md` and `docs/ARCHITECTURE.md` for the design
reasoning; `docs/CHECKPOINT-*.md` for what was built and verified in each
phase; `docs/STATUS.md` for the current state and what to check first.

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
| Frontend | Server-rendered Jinja2, plain HTML forms, no JS framework |

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

certbot is installed into Forgehost's own venv in step 7, not via apt — see
`daemon/ssl.py`'s `certbot_bin` setting (`/opt/forgehost/.venv/bin/certbot`),
which carries the `certbot-dns-powerdns` plugin alongside it.

### 5. Secure MariaDB and create Forgehost's database users

The stock `mariadb-server` package on Ubuntu ships `root` with an **empty
password** — fix this first.

```bash
ROOT_PASS=$(openssl rand -base64 32 | tr -d '=+/' | head -c 32)
DAEMON_PASS=$(openssl rand -base64 32 | tr -d '=+/' | head -c 32)
MAILRO_PASS=$(openssl rand -hex 24)

mysql -u root <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${ROOT_PASS}';
CREATE USER IF NOT EXISTS 'forgehost_daemon'@'localhost' IDENTIFIED BY '${DAEMON_PASS}';
-- Explicit privilege list, not ALL PRIVILEGES -- this user's own grant is
-- exactly what it can later re-grant to hosted-account DB users
-- (daemon/mariadb.py's HOSTED_DB_PRIVILEGES). Widen both together if you
-- need view/routine/trigger/event support for hosted databases.
GRANT CREATE, DROP, ALTER, INDEX, CREATE USER, GRANT OPTION, SELECT,
  INSERT, UPDATE, DELETE, RELOAD, PROCESS, LOCK TABLES, REFERENCES,
  CREATE TEMPORARY TABLES ON *.* TO 'forgehost_daemon'@'localhost';
CREATE DATABASE IF NOT EXISTS forgehost_mail CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'forgehost_mailro'@'localhost' IDENTIFIED BY '${MAILRO_PASS}';
GRANT SELECT ON forgehost_mail.* TO 'forgehost_mailro'@'localhost';
FLUSH PRIVILEGES;
SQL

mysql -u root -p"${ROOT_PASS}" forgehost_mail <<'SQL'
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

mkdir -p /etc/forgehost/ssl
echo "MARIADB_ROOT_PASSWORD=${ROOT_PASS}" >> /etc/forgehost/secrets.env
echo "MARIADB_DAEMON_PASSWORD=${DAEMON_PASS}" >> /etc/forgehost/secrets.env
echo "MARIADB_MAILRO_PASSWORD=${MAILRO_PASS}" >> /etc/forgehost/secrets.env
chmod 600 /etc/forgehost/secrets.env
unset ROOT_PASS DAEMON_PASS MAILRO_PASS
```

### 6. Create the `vmail` and `forgehost-api` system accounts

```bash
# Low system uid (NOT a high number like 30000) -- ARCHITECTURE.md SS5/
# CHECKPOINT-e.md explain why: useradd allocates the *next* uid after the
# highest existing one, so a high vmail uid pushes every hosting account's
# uid up after it.
groupadd --system --gid 150 vmail
useradd --system --uid 150 --gid 150 --home-dir /var/vmail --shell /usr/sbin/nologin vmail
mkdir -p /var/vmail && chown vmail:vmail /var/vmail && chmod 750 /var/vmail

groupadd -f forgehost-api
useradd --system --no-create-home --shell /usr/sbin/nologin -g forgehost-api forgehost-api
```

### 7. Deploy Forgehost

`/root` is typically mode 700 -- never deploy the running app there
(`forgehost-api`, the unprivileged process, must be able to traverse into
it). Clone/copy this repo somewhere with a normal home-dir mode, e.g.
`/root/forgehost-src` if developing as root, then deploy to `/opt/forgehost`:

```bash
git clone <this-repo> /root/forgehost-src   # or wherever you keep the checkout
cd /root/forgehost-src
# scripts/deploy.sh assumes the checkout is at /root/cpanel-clone --
# edit SRC at the top if yours is elsewhere.
bash scripts/deploy.sh

python3 -m venv /opt/forgehost/.venv
/opt/forgehost/.venv/bin/pip install --upgrade pip
/opt/forgehost/.venv/bin/pip install -r /opt/forgehost/requirements.txt
```

`scripts/deploy.sh` re-applies world-readable permissions on every run
(there's nothing sensitive in the code tree — secrets live under
`/etc/forgehost`) but does **not** touch `.venv`; re-run the pip install
above yourself if `requirements.txt` changes.

### 8. Configuration files

```bash
cat > /etc/forgehost/forgehost.toml <<EOF
server_public_ip = "104.234.179.64"
letsencrypt_email = "you@example.com"
EOF

SESSION_SECRET=$(openssl rand -hex 32)
echo "SESSION_SECRET=${SESSION_SECRET}" > /etc/forgehost/api-secrets.env
chown root:forgehost-api /etc/forgehost/api-secrets.env
chmod 640 /etc/forgehost/api-secrets.env
unset SESSION_SECRET

chown root:forgehost-api /etc/forgehost/forgehost.toml
chmod 640 /etc/forgehost/forgehost.toml

# PowerDNS REST API key
PDNS_KEY=$(openssl rand -hex 24)
mkdir -p /var/lib/powerdns
sqlite3 /var/lib/powerdns/pdns.sqlite3 < /usr/share/pdns-backend-sqlite3/schema/schema.sqlite3.sql
chown pdns:pdns /var/lib/powerdns/pdns.sqlite3 && chmod 660 /var/lib/powerdns/pdns.sqlite3
cat > /etc/powerdns/pdns.d/forgehost.conf <<EOF
launch+=gsqlite3
gsqlite3-database=/var/lib/powerdns/pdns.sqlite3
gsqlite3-dnssec=no
webserver=yes
webserver-address=127.0.0.1
webserver-port=8081
webserver-allow-from=127.0.0.1
api=yes
api-key=${PDNS_KEY}
default-soa-content=ns1.forgehost.invalid hostmaster.@ 0 10800 3600 604800 3600
EOF
echo "POWERDNS_API_KEY=${PDNS_KEY}" >> /etc/forgehost/secrets.env
mkdir -p /etc/forgehost/ssl
cat > /etc/forgehost/ssl/powerdns-credentials.ini <<EOF
dns_powerdns_api_url = http://127.0.0.1:8081/api/v1
dns_powerdns_api_key = ${PDNS_KEY}
EOF
chmod 600 /etc/forgehost/ssl/powerdns-credentials.ini /etc/forgehost/secrets.env
unset PDNS_KEY
systemctl restart pdns
```

### 9. Postfix + Dovecot virtual mail

```bash
MAILRO_PASS=$(grep MARIADB_MAILRO_PASSWORD /etc/forgehost/secrets.env | cut -d= -f2)
mkdir -p /etc/postfix/forgehost
cat > /etc/postfix/forgehost/mysql-virtual-domains.cf <<EOF
user = forgehost_mailro
password = ${MAILRO_PASS}
hosts = unix:/run/mysqld/mysqld.sock
dbname = forgehost_mail
query = SELECT 1 FROM mail_domain WHERE domain='%s' AND active=1
EOF
cat > /etc/postfix/forgehost/mysql-virtual-mailboxes.cf <<EOF
user = forgehost_mailro
password = ${MAILRO_PASS}
hosts = unix:/run/mysqld/mysqld.sock
dbname = forgehost_mail
query = SELECT CONCAT(d.domain, '/', m.local_part, '/') FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%s' AND m.active = 1 AND d.active = 1
EOF
chown root:postfix /etc/postfix/forgehost/*.cf && chmod 640 /etc/postfix/forgehost/*.cf

postconf -e "virtual_mailbox_domains = proxy:mysql:/etc/postfix/forgehost/mysql-virtual-domains.cf"
postconf -e "virtual_mailbox_maps = proxy:mysql:/etc/postfix/forgehost/mysql-virtual-mailboxes.cf"
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
connect = host=/run/mysqld/mysqld.sock dbname=forgehost_mail user=forgehost_mailro password=${MAILRO_PASS}
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

cat > /etc/dovecot/conf.d/90-forgehost.conf <<'EOF'
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
mkdir -p /etc/forgehost/ssl
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/forgehost/ssl/default.key -out /etc/forgehost/ssl/default.crt \
  -days 3650 -subj "/CN=forgehost-default"
chmod 600 /etc/forgehost/ssl/default.key

mkdir -p /var/www/_suspended
cat > /var/www/_suspended/index.html <<'EOF'
<!DOCTYPE html><html><head><title>Account Suspended</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 10%;">
<h1>This account has been suspended</h1><p>Please contact your hosting provider.</p>
</body></html>
EOF

mkdir -p /etc/forgehost/ssl/api
openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout /etc/forgehost/ssl/api/panel.key -out /etc/forgehost/ssl/api/panel.crt \
  -days 3650 -subj "/CN=forgehost-panel"
chown forgehost-api:forgehost-api /etc/forgehost/ssl/api/panel.key /etc/forgehost/ssl/api/panel.crt
chmod 600 /etc/forgehost/ssl/api/panel.key
```

The panel's own TLS cert is self-signed (browsers will warn on first visit
— accept once, or replace with your own real cert for the panel's
hostname). Per-customer-site certs are real, browser-trusted Let's Encrypt
certs issued through Forgehost itself (`POST /api/v1/ssl/issue`).

### 11. Lock down config file permissions

```bash
chown root:forgehost-api /etc/forgehost/forgehost.toml
chmod 640 /etc/forgehost/forgehost.toml
```

(`/var/lib/forgehost` and the control-plane `forgehost.db`/`-wal`/`-shm`
files are created by `forgehostd` itself on first start, already owned
`root:forgehost-api` with group-read permissions —
`shared/db.py`'s `init_db()` handles this automatically.)

### 12. systemd units

```bash
cp /opt/forgehost/deploy/forgehost-provisiond.service /etc/systemd/system/
cp /opt/forgehost/deploy/forgehost-api.service /etc/systemd/system/
```

(`deploy/` ships in this repo with the exact unit files this project
itself runs on — copied directly from a working install, not
reconstructed from memory.)

```bash
systemctl daemon-reload
systemctl enable --now forgehost-provisiond
# one-time: replace OLS's stock "Example" vhost with a clean baseline
# (ARCHITECTURE.md SS7 / CHECKPOINT-b.md explain why this is required)
/opt/forgehost/.venv/bin/python -c "
import sys; sys.path.insert(0, '/opt/forgehost')
from shared.rpc import RpcClient
RpcClient('/run/forgehost/provisiond.sock').call('system.bootstrap_ols', _actor='setup', _role='admin')
"
systemctl enable --now forgehost-api
```

### 13. Create the first admin login

```bash
/opt/forgehost/.venv/bin/python /opt/forgehost/scripts/create_admin.py --username admin
```

Then open `https://<server-ip>:9443/login` (accept the self-signed cert
warning on first visit, or swap in your own cert for this hostname).

## Verifying the install

```bash
systemctl status lshttpd postfix dovecot mariadb pdns pure-ftpd forgehost-provisiond forgehost-api
curl -sk https://127.0.0.1:9443/healthz
```

Create a test account end-to-end via the REST API (see
`docs/CHECKPOINT-h.md` for the exact sequence this project itself used to
validate a fresh install, including a real Let's Encrypt issuance against a
`sslip.io` test domain when you don't yet have a real domain pointed at the
server).

## Repository layout

```
daemon/      forgehostd -- the root provisioning daemon (Unix-socket RPC only)
api/         forgehost-api -- the unprivileged REST API + admin UI
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
systemctl restart forgehost-provisiond forgehost-api
```

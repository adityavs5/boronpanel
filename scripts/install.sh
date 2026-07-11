#!/usr/bin/env bash
#
# Forgehost installer (Run A feature 9).
#
# One command to stand up Forgehost on a fresh Ubuntu 24.04 server. Faithful,
# idempotent translation of the hand-tested runbook in README.md -- every step
# here corresponds to a numbered section there, and each is safe to re-run.
#
#   sudo bash scripts/install.sh                 # full install (interactive)
#   sudo bash scripts/install.sh --dry-run       # print the plan, change nothing
#   sudo bash scripts/install.sh --uninstall     # remove Forgehost (keeps user data)
#
# Non-interactive install: preseed the prompts with env vars --
#   FH_PANEL_DOMAIN, FH_LE_EMAIL, FH_ADMIN_USER, FH_ADMIN_PASSWORD,
#   FH_SERVER_IP, FH_NONINTERACTIVE=1
#
# No external dependencies beyond bash + coreutils + the OS package manager.
# Progress is printed per step (OK / FAIL / SKIP) and appended to
# /var/log/forgehost-install.log.

set -euo pipefail

# --- constants ---------------------------------------------------------------

readonly DEST="/opt/forgehost"
readonly CONF_DIR="/etc/forgehost"
readonly DATA_DIR="/var/lib/forgehost"
readonly LOG_DIR="/var/log/forgehost"
readonly INSTALL_LOG="/var/log/forgehost-install.log"
readonly VENV="${DEST}/.venv"
readonly MIN_RAM_MB=1024
readonly MIN_DISK_GB=10
# Ports the stack binds and expects to own on a fresh box. A conflict here
# means something else is already installed -- surfaced in pre-flight, not
# discovered halfway through.
readonly REQUIRED_PORTS=(9443 8081 80 443 21 25 587 143 993 3306 53)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT

# Panel version -- read from version.py, the repo-wide single source of truth
# (scripts/release.sh bumps it; the API and SPA display the same value).
FORGEHOST_VERSION="$(sed -n 's/^FORGEHOST_VERSION = "\(.*\)"$/\1/p' "${REPO_ROOT}/version.py" 2>/dev/null || true)"
FORGEHOST_VERSION="${FORGEHOST_VERSION:-unknown}"
readonly FORGEHOST_VERSION

# --- runtime flags -----------------------------------------------------------

DRY_RUN=false
UNINSTALL=false
NONINTERACTIVE="${FH_NONINTERACTIVE:-false}"

PANEL_DOMAIN="${FH_PANEL_DOMAIN:-}"
LE_EMAIL="${FH_LE_EMAIL:-}"
ADMIN_USER="${FH_ADMIN_USER:-admin}"
ADMIN_PASSWORD="${FH_ADMIN_PASSWORD:-}"
SERVER_IP="${FH_SERVER_IP:-}"

STEP_OK=0
STEP_FAIL=0
STEP_SKIP=0

# --- output helpers ----------------------------------------------------------

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
    C_YELLOW=$'\033[33m'; C_BLUE=$'\033[36m'; C_BOLD=$'\033[1m'
else
    C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

_logline() {
    # Append to the install log if we can (best-effort: the log dir may not
    # exist yet during pre-flight, and a dry-run shouldn't need to write it).
    if [[ -w "$(dirname "$INSTALL_LOG")" ]] || [[ -w "$INSTALL_LOG" ]]; then
        printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$1" >>"$INSTALL_LOG" 2>/dev/null || true
    fi
}

info()  { printf '%s\n' "${C_BLUE}==>${C_RESET} $1"; _logline "INFO  $1"; }
ok()    { printf '  %s %s\n' "${C_GREEN}[ OK ]${C_RESET}" "$1"; _logline "OK    $1"; STEP_OK=$((STEP_OK + 1)); }
skip()  { printf '  %s %s\n' "${C_YELLOW}[SKIP]${C_RESET}" "$1"; _logline "SKIP  $1"; STEP_SKIP=$((STEP_SKIP + 1)); }
fail()  { printf '  %s %s\n' "${C_RED}[FAIL]${C_RESET}" "$1"; _logline "FAIL  $1"; STEP_FAIL=$((STEP_FAIL + 1)); }
warn()  { printf '  %s %s\n' "${C_YELLOW}[warn]${C_RESET}" "$1"; _logline "WARN  $1"; }
die()   { printf '%s %s\n' "${C_RED}${C_BOLD}error:${C_RESET}" "$1" >&2; _logline "ERROR $1"; exit 1; }

# --- guarded execution -------------------------------------------------------

# run: execute a command (array form), or just print it under --dry-run.
run() {
    if $DRY_RUN; then
        printf '  %s %s\n' "${C_YELLOW}[dry]${C_RESET}" "$*"
        return 0
    fi
    _logline "RUN   $*"
    "$@" >>"$INSTALL_LOG" 2>&1
}

# run_sh: same, for a shell snippet that genuinely needs pipes/redirects.
run_sh() {
    local snippet="$1"
    if $DRY_RUN; then
        printf '  %s %s\n' "${C_YELLOW}[dry]${C_RESET}" "$snippet"
        return 0
    fi
    _logline "RUN   $snippet"
    bash -c "$snippet" >>"$INSTALL_LOG" 2>&1
}

# write_file PATH MODE [OWNER]: create/overwrite a file from stdin, guarded.
write_file() {
    local path="$1" mode="$2" owner="${3:-}"
    local content
    content="$(cat)"
    if $DRY_RUN; then
        printf '  %s write %s (%s bytes, mode %s%s)\n' \
            "${C_YELLOW}[dry]${C_RESET}" "$path" "${#content}" "$mode" \
            "${owner:+, owner $owner}"
        return 0
    fi
    mkdir -p "$(dirname "$path")"
    printf '%s\n' "$content" >"$path"
    chmod "$mode" "$path"
    [[ -n "$owner" ]] && chown "$owner" "$path"
    _logline "WROTE $path"
}

# ensure_line FILE LINE: append LINE to FILE unless already present (idempotent).
ensure_line() {
    local file="$1" line="$2"
    if [[ -f "$file" ]] && grep -qF -- "$line" "$file"; then
        return 0
    fi
    if $DRY_RUN; then
        printf '  %s append to %s: %s\n' "${C_YELLOW}[dry]${C_RESET}" "$file" "$line"
        return 0
    fi
    printf '%s\n' "$line" >>"$file"
}

# --- pre-flight --------------------------------------------------------------

check_root() {
    if [[ "$(id -u)" -eq 0 ]]; then
        ok "running as root"
    elif $DRY_RUN; then
        warn "not root -- a real install must run as root (ok for --dry-run)"
    else
        die "must run as root (use sudo)"
    fi
}

check_os() {
    local id="" ver=""
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        id="${ID:-}"; ver="${VERSION_ID:-}"
    fi
    if [[ "$id" == "ubuntu" && "$ver" == "24.04" ]]; then
        ok "Ubuntu 24.04 detected"
    elif [[ "$id" == "ubuntu" ]]; then
        warn "Ubuntu ${ver:-unknown} (this installer targets 24.04; continuing)"
    else
        if $DRY_RUN; then
            warn "non-Ubuntu OS (${id:-unknown}) -- real install requires Ubuntu 24.04"
        else
            die "unsupported OS '${id:-unknown}' (Ubuntu 24.04 required)"
        fi
    fi
}

check_ram() {
    local kb mb
    kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    mb=$((kb / 1024))
    if [[ "$mb" -ge "$MIN_RAM_MB" ]]; then
        ok "RAM ${mb}MB (>= ${MIN_RAM_MB}MB)"
    elif $DRY_RUN; then
        warn "RAM ${mb}MB is below the ${MIN_RAM_MB}MB minimum"
    else
        die "insufficient RAM: ${mb}MB < ${MIN_RAM_MB}MB minimum"
    fi
}

check_disk() {
    local avail_kb avail_gb
    avail_kb="$(df --output=avail / 2>/dev/null | tail -1 | tr -d ' ')"
    avail_kb="${avail_kb:-0}"
    avail_gb=$((avail_kb / 1024 / 1024))
    if [[ "$avail_gb" -ge "$MIN_DISK_GB" ]]; then
        ok "disk ${avail_gb}GB free on / (>= ${MIN_DISK_GB}GB)"
    elif $DRY_RUN; then
        warn "only ${avail_gb}GB free on / (need ${MIN_DISK_GB}GB)"
    else
        die "insufficient disk: ${avail_gb}GB < ${MIN_DISK_GB}GB free on /"
    fi
}

check_ports() {
    local conflicts=() port
    for port in "${REQUIRED_PORTS[@]}"; do
        if ss -Hltn "sport = :${port}" 2>/dev/null | grep -q .; then
            # 3306/25/etc. may already be up if this is a re-run over a partial
            # install -- that's expected and not fatal on a re-run.
            conflicts+=("$port")
        fi
    done
    if [[ "${#conflicts[@]}" -eq 0 ]]; then
        ok "no port conflicts on ${REQUIRED_PORTS[*]}"
    else
        warn "ports already in use: ${conflicts[*]} (expected on re-run; a conflict on a truly fresh box means another service owns them)"
    fi
}

preflight() {
    info "Pre-flight checks"
    check_root
    check_os
    check_ram
    check_disk
    check_ports
}

# --- 1. base packages --------------------------------------------------------

readonly BASE_PKGS=(
    python3 python3-venv python3-pip python3-dev build-essential
    curl wget jq sqlite3 ufw acl quota quotatool git ca-certificates
    nodejs npm composer
)
readonly STACK_PKGS=(
    mariadb-server postfix dovecot-core dovecot-imapd dovecot-lmtp
    dovecot-mysql dovecot-sieve postfix-mysql pdns-server pdns-backend-sqlite3
    pure-ftpd certbot rclone spamassassin fail2ban redis-server
)
readonly OLS_PKGS=(
    openlitespeed
    lsphp81 lsphp81-common lsphp81-curl lsphp81-mysql lsphp81-opcache
    lsphp83 lsphp83-common lsphp83-curl lsphp83-mysql lsphp83-opcache
    lsphp83-intl lsphp83-redis lsphp83-sqlite3
)

apt_update() {
    export DEBIAN_FRONTEND=noninteractive
    run apt-get update -qq
    ok "apt cache updated"
}

install_base_packages() {
    info "Installing base packages"
    run apt-get install -y "${BASE_PKGS[@]}"
    ok "base packages installed (python, node, composer, tooling)"
    # wp-cli isn't packaged in apt -- fetch the official phar (curl only).
    if [[ -x /usr/local/bin/wp ]]; then
        skip "wp-cli already installed"
    else
        run curl -fsSL -o /usr/local/bin/wp \
            https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
        run chmod +x /usr/local/bin/wp
        ok "wp-cli installed"
    fi
}

install_openlitespeed() {
    info "Installing OpenLiteSpeed + lsphp"
    if [[ -d /usr/local/lsws ]]; then
        skip "OpenLiteSpeed already present"
    else
        # LiteSpeed's own repo, added exactly as their documented one-liner
        # (curl | bash) -- the same source README §3 uses.
        run_sh "curl -fsSL https://repo.litespeed.sh | bash"
        ok "LiteSpeed apt repo added"
    fi
    run apt-get install -y "${OLS_PKGS[@]}"
    run systemctl enable --now lshttpd
    ok "OpenLiteSpeed + PHP 8.1/8.3 installed"
}

install_stack_packages() {
    info "Installing the hosting stack (mail/DNS/FTP/SSL/security)"
    # Postfix must not launch its interactive config screen.
    run_sh "echo 'postfix postfix/main_mailer_type select Internet Site' | debconf-set-selections"
    run_sh "echo \"postfix postfix/mailname string \$(hostname -f)\" | debconf-set-selections"
    run apt-get install -y "${STACK_PKGS[@]}"
    # The bind backend is pulled in as a pdns dependency but unused here.
    if [[ -f /etc/powerdns/pdns.d/bind.conf ]]; then
        run mv /etc/powerdns/pdns.d/bind.conf /etc/powerdns/pdns.d/bind.conf.disabled
    fi
    ok "hosting stack installed"
}

# --- 2. quotas ---------------------------------------------------------------

setup_quota() {
    info "Enabling filesystem quotas"
    if findmnt -no OPTIONS / 2>/dev/null | grep -q usrquota; then
        skip "usrquota already active on /"
        return 0
    fi
    if ! grep -qE '^\S+\s+/\s' /etc/fstab; then
        warn "no explicit / entry in /etc/fstab -- enable usrquota,grpquota manually"
        return 0
    fi
    # Add usrquota,grpquota to the root mount's options if absent.
    run_sh "sed -i -E '/\s\/\s/ s/(defaults[^[:space:]]*)/\1,usrquota,grpquota/' /etc/fstab"
    run mount -o remount /
    run_sh "quotacheck -ugm / || true"
    run_sh "quotaon -ugv / || true"
    ok "quotas enabled on /"
}

# --- 3. system users ---------------------------------------------------------

ensure_group() {
    local name="$1" gid="${2:-}"
    if getent group "$name" >/dev/null; then
        skip "group '$name' exists"
        return 0
    fi
    if [[ -n "$gid" ]]; then
        run groupadd --system --gid "$gid" "$name"
    else
        run groupadd -f "$name"
    fi
    ok "group '$name' created"
}

setup_system_users() {
    info "Creating system users (vmail, forgehost-api)"
    # vmail: fixed low uid/gid 150 -- a high uid would push every hosting
    # account's uid up after it (README §6).
    ensure_group vmail 150
    if getent passwd vmail >/dev/null; then
        skip "user 'vmail' exists"
    else
        run useradd --system --uid 150 --gid 150 --home-dir /var/vmail \
            --shell /usr/sbin/nologin vmail
        ok "user 'vmail' created"
    fi
    run mkdir -p /var/vmail
    run chown vmail:vmail /var/vmail
    run chmod 750 /var/vmail

    ensure_group forgehost-api
    if getent passwd forgehost-api >/dev/null; then
        skip "user 'forgehost-api' exists"
    else
        run useradd --system --no-create-home --shell /usr/sbin/nologin \
            -g forgehost-api forgehost-api
        ok "user 'forgehost-api' created"
    fi
}

# --- 4. deploy the application -----------------------------------------------

deploy_app() {
    info "Deploying application to ${DEST}"
    run mkdir -p "$DEST" "$CONF_DIR" "$DATA_DIR" "$LOG_DIR"
    # Copy the repo tree (excluding VCS + local venv/node_modules) to /opt.
    run_sh "rsync -a --delete --exclude='.git' --exclude='.venv' --exclude='frontend/node_modules' '${REPO_ROOT}/' '${DEST}/'"
    run_sh "find '${DEST}' -type d -exec chmod 755 {} +"
    ok "code synced to ${DEST}"

    if [[ -d "$VENV" ]]; then
        skip "virtualenv exists"
    else
        run python3 -m venv "$VENV"
        ok "virtualenv created"
    fi
    run "${VENV}/bin/pip" install --upgrade pip
    run "${VENV}/bin/pip" install -r "${DEST}/requirements.txt"
    ok "Python dependencies installed"

    # The React SPA ships prebuilt in static/dist; rebuild only if missing.
    if [[ -f "${DEST}/static/dist/index.html" ]]; then
        skip "web UI bundle already present (static/dist)"
    else
        run_sh "cd '${DEST}/frontend' && npm install && npm run build"
        ok "web UI built"
    fi

    # Run A feature 7: the API (unprivileged) must be able to create its own
    # log files in the shared log dir -- setgid group-write for forgehost-api.
    run chgrp forgehost-api "$LOG_DIR"
    run chmod 2775 "$LOG_DIR"
    ok "log dir ${LOG_DIR} group-writable by forgehost-api"
}

# --- 5. secrets + config -----------------------------------------------------

_rand_hex()    { openssl rand -hex "${1:-24}"; }
_rand_pass()   { openssl rand -base64 32 | tr -d '=+/' | head -c 32; }

# Store one KEY=VALUE in a secrets file, only if the key isn't already there
# (idempotent -- never rotates an existing secret on re-run).
ensure_secret() {
    local file="$1" key="$2" value="$3"
    run mkdir -p "$(dirname "$file")"
    if [[ -f "$file" ]] && grep -q "^${key}=" "$file"; then
        skip "secret ${key} already set"
        return 0
    fi
    if $DRY_RUN; then
        printf '  %s set secret %s in %s\n' "${C_YELLOW}[dry]${C_RESET}" "$key" "$file"
        return 0
    fi
    touch "$file"; chmod 600 "$file"
    printf '%s=%s\n' "$key" "$value" >>"$file"
    ok "secret ${key} generated"
}

setup_config() {
    info "Writing configuration + secrets"
    [[ -z "$SERVER_IP" ]] && SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

    write_file "${CONF_DIR}/forgehost.toml" 640 "root:forgehost-api" <<EOF
server_public_ip = "${SERVER_IP}"
letsencrypt_email = "${LE_EMAIL}"
EOF
    ok "forgehost.toml written"

    # SESSION_SECRET: the one secret the API needs; its own 0640 root:api file.
    if [[ -f "${CONF_DIR}/api-secrets.env" ]] && grep -q '^SESSION_SECRET=' "${CONF_DIR}/api-secrets.env"; then
        skip "SESSION_SECRET already set"
    else
        run_sh "install -m 640 /dev/null '${CONF_DIR}/api-secrets.env'"
        run_sh "chown root:forgehost-api '${CONF_DIR}/api-secrets.env'"
        run_sh "printf 'SESSION_SECRET=%s\n' \"\$(openssl rand -hex 32)\" >> '${CONF_DIR}/api-secrets.env'"
        ok "SESSION_SECRET generated"
    fi
}

setup_mariadb() {
    info "Securing MariaDB + creating Forgehost database users"
    run systemctl enable --now mariadb
    if [[ -f "${CONF_DIR}/secrets.env" ]] && grep -q '^MARIADB_DAEMON_PASSWORD=' "${CONF_DIR}/secrets.env"; then
        skip "MariaDB users already provisioned"
        return 0
    fi
    if $DRY_RUN; then
        printf '  %s secure MariaDB root, create forgehost_daemon + forgehost_mailro, create forgehost_mail schema\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi
    local root_pass daemon_pass mailro_pass
    root_pass="$(_rand_pass)"; daemon_pass="$(_rand_pass)"; mailro_pass="$(_rand_hex 24)"
    mysql -u root <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${root_pass}';
CREATE USER IF NOT EXISTS 'forgehost_daemon'@'localhost' IDENTIFIED BY '${daemon_pass}';
GRANT CREATE, DROP, ALTER, INDEX, CREATE USER, GRANT OPTION, SELECT,
  INSERT, UPDATE, DELETE, RELOAD, PROCESS, LOCK TABLES, REFERENCES,
  CREATE TEMPORARY TABLES ON *.* TO 'forgehost_daemon'@'localhost';
CREATE DATABASE IF NOT EXISTS forgehost_mail CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'forgehost_mailro'@'localhost' IDENTIFIED BY '${mailro_pass}';
GRANT SELECT ON forgehost_mail.* TO 'forgehost_mailro'@'localhost';
FLUSH PRIVILEGES;
SQL
    mysql -u root -p"${root_pass}" forgehost_mail <<'SQL'
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
    printf '[client]\nuser=root\npassword=%s\n' "${root_pass}" >/root/.my.cnf
    chmod 600 /root/.my.cnf
    touch "${CONF_DIR}/secrets.env"; chmod 600 "${CONF_DIR}/secrets.env"
    {
        printf 'MARIADB_ROOT_PASSWORD=%s\n' "${root_pass}"
        printf 'MARIADB_DAEMON_PASSWORD=%s\n' "${daemon_pass}"
        printf 'MARIADB_MAILRO_PASSWORD=%s\n' "${mailro_pass}"
    } >>"${CONF_DIR}/secrets.env"
    ok "MariaDB secured; forgehost_daemon + forgehost_mailro created"
}

setup_powerdns() {
    info "Configuring PowerDNS (gsqlite3 + REST API)"
    if [[ -f /etc/powerdns/pdns.d/forgehost.conf ]]; then
        skip "PowerDNS already configured"
        return 0
    fi
    if $DRY_RUN; then
        printf '  %s init pdns sqlite db, write forgehost.conf with a fresh API key\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi
    local pdns_key; pdns_key="$(_rand_hex 24)"
    mkdir -p /var/lib/powerdns
    if [[ ! -f /var/lib/powerdns/pdns.sqlite3 ]]; then
        sqlite3 /var/lib/powerdns/pdns.sqlite3 < /usr/share/pdns-backend-sqlite3/schema/schema.sqlite3.sql
        chown pdns:pdns /var/lib/powerdns/pdns.sqlite3
        chmod 660 /var/lib/powerdns/pdns.sqlite3
    fi
    cat >/etc/powerdns/pdns.d/forgehost.conf <<EOF
launch+=gsqlite3
gsqlite3-database=/var/lib/powerdns/pdns.sqlite3
gsqlite3-dnssec=no
webserver=yes
webserver-address=127.0.0.1
webserver-port=8081
webserver-allow-from=127.0.0.1
api=yes
api-key=${pdns_key}
default-soa-content=ns1.forgehost.invalid hostmaster.@ 0 10800 3600 604800 3600
EOF
    printf 'POWERDNS_API_KEY=%s\n' "${pdns_key}" >>"${CONF_DIR}/secrets.env"
    mkdir -p "${CONF_DIR}/ssl"
    cat >"${CONF_DIR}/ssl/powerdns-credentials.ini" <<EOF
dns_powerdns_api_url = http://127.0.0.1:8081/api/v1
dns_powerdns_api_key = ${pdns_key}
EOF
    chmod 600 "${CONF_DIR}/ssl/powerdns-credentials.ini"
    systemctl restart pdns
    ok "PowerDNS configured (API on 127.0.0.1:8081)"
}

setup_ssl_bootstrap() {
    info "Creating bootstrap TLS certs + suspended page"
    run mkdir -p "${CONF_DIR}/ssl" "${CONF_DIR}/ssl/api" /var/www/_suspended
    if [[ -f "${CONF_DIR}/ssl/default.crt" ]]; then
        skip "default self-signed cert exists"
    else
        run_sh "openssl req -x509 -nodes -newkey rsa:2048 -keyout '${CONF_DIR}/ssl/default.key' -out '${CONF_DIR}/ssl/default.crt' -days 3650 -subj '/CN=forgehost-default'"
        run chmod 600 "${CONF_DIR}/ssl/default.key"
        ok "default cert generated"
    fi
    if [[ -f "${CONF_DIR}/ssl/api/panel.crt" ]]; then
        skip "panel TLS cert exists"
    else
        run_sh "openssl req -x509 -nodes -newkey rsa:2048 -keyout '${CONF_DIR}/ssl/api/panel.key' -out '${CONF_DIR}/ssl/api/panel.crt' -days 3650 -subj '/CN=${PANEL_DOMAIN:-forgehost-panel}'"
        run_sh "chown forgehost-api:forgehost-api '${CONF_DIR}/ssl/api/panel.key' '${CONF_DIR}/ssl/api/panel.crt'"
        run chmod 600 "${CONF_DIR}/ssl/api/panel.key"
        ok "panel cert generated"
    fi
    write_file /var/www/_suspended/index.html 644 <<'EOF'
<!DOCTYPE html><html><head><title>Account Suspended</title></head>
<body style="font-family: sans-serif; text-align: center; padding-top: 10%;">
<h1>This account has been suspended</h1><p>Please contact your hosting provider.</p>
</body></html>
EOF
    ok "suspended-account page written"
}

# --- 6. systemd services + cron + logrotate ----------------------------------

install_systemd_units() {
    info "Installing systemd units"
    run install -m 644 "${DEST}/deploy/forgehost-provisiond.service" /etc/systemd/system/
    run install -m 644 "${DEST}/deploy/forgehost-api.service" /etc/systemd/system/
    run systemctl daemon-reload
    run systemctl enable forgehost-provisiond
    ok "systemd units installed + provisiond enabled"
}

install_cron_and_logrotate() {
    info "Installing periodic jobs + log rotation"
    # Run A feature 7: API access/error log rotation.
    run install -m 644 "${DEST}/deploy/forgehost-api.logrotate" /etc/logrotate.d/forgehost-api
    # Run A feature 5: service health monitoring, every 5 min.
    run install -m 644 "${DEST}/deploy/forgehost-monitoring.cron" /etc/cron.d/forgehost-monitoring
    # Cloudflare edge-range + zone-activation polls.
    run install -m 644 "${DEST}/deploy/forgehost-cloudflare.cron" /etc/cron.d/forgehost-cloudflare
    # Panel update system: daily release check + admin email + old-version pruning.
    run install -m 644 "${DEST}/deploy/forgehost-update.cron" /etc/cron.d/forgehost-update
    # The remaining infrastructure crons (usage, backups, ssl expiry, pma
    # tokens, usage alerts) -- root-owned, same trust level as the daemon.
    write_file /etc/cron.d/forgehost-jobs 644 <<'EOF'
# Forgehost infrastructure cron jobs (installed by scripts/install.sh).
*/15 * * * * root /opt/forgehost/scripts/usage_snapshot.py >> /var/log/forgehost/usage-snapshot.log 2>&1
0 * * * * root /opt/forgehost/scripts/backup_scheduler.py >> /var/log/forgehost/backup-scheduler.log 2>&1
*/5 * * * * root /opt/forgehost/scripts/pma_token_cleanup.py >> /var/log/forgehost/pma-token-cleanup.log 2>&1
0 6 * * * root /opt/forgehost/scripts/ssl_expiry_check.py >> /var/log/forgehost/ssl-expiry-check.log 2>&1
*/15 * * * * root /opt/forgehost/scripts/usage_alert_check.py >> /var/log/forgehost/usage-alert-check.log 2>&1
EOF
    # Missing-features batch, goal features 2 + 6: maintenance-mode
    # auto-disable sweep + daily site-statistics snapshot.
    run install -m 644 "${DEST}/deploy/forgehost-maintenance.cron" /etc/cron.d/forgehost-maintenance
    run install -m 644 "${DEST}/deploy/forgehost-sitestats.cron" /etc/cron.d/forgehost-sitestats
    ok "logrotate + cron jobs installed"
}

# --- 7. bring-up: migrations, bootstrap, admin -------------------------------

start_services() {
    info "Starting services + bootstrapping OLS"
    run systemctl restart forgehost-provisiond
    # DB schema is created by the daemon's own init_db() on first start
    # (create_all + additive migrations); give it a moment to bind its socket.
    if ! $DRY_RUN; then
        local _wait
        for _wait in 1 2 3 4 5 6 7 8 9 10; do
            [[ -S /run/forgehost/provisiond.sock ]] && break
            sleep 1
        done
    fi
    # One-time: replace OLS's stock Example vhost with a clean baseline.
    run_sh "'${VENV}/bin/python' -c \"import sys; sys.path.insert(0, '${DEST}'); from shared.rpc import RpcClient; RpcClient('/run/forgehost/provisiond.sock').call('system.bootstrap_ols', _actor='setup', _role='admin')\" || true"
    run systemctl enable --now forgehost-api
    ok "provisiond + api started; OLS baseline applied"
}

create_admin() {
    info "Creating the first admin login"
    if $DRY_RUN; then
        printf '  %s prompt for admin username/password, create panel admin\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi
    local args=(--username "$ADMIN_USER")
    if [[ -n "$ADMIN_PASSWORD" ]]; then
        args+=(--password "$ADMIN_PASSWORD")
    fi
    if "${VENV}/bin/python" "${DEST}/scripts/create_admin.py" "${args[@]}"; then
        ok "admin '${ADMIN_USER}' created"
    else
        warn "admin creation returned non-zero (may already exist) -- create manually with scripts/create_admin.py"
    fi
}

# --- 8. firewall + fail2ban --------------------------------------------------

setup_firewall() {
    info "Configuring UFW + fail2ban"
    run ufw --force reset
    run ufw default deny incoming
    run ufw default allow outgoing
    local p
    for p in 22 21 25 53 80 443 587 993 9443; do
        run ufw allow "$p"
    done
    # Passive FTP data range for Pure-FTPd.
    run ufw allow 30000:50000/tcp
    run ufw --force enable
    ok "UFW enabled (SSH/web/mail/DNS/FTP/panel allowed)"

    run systemctl enable --now fail2ban
    write_file /etc/fail2ban/jail.d/forgehost.conf 644 <<'EOF'
[sshd]
enabled = true

[postfix]
enabled = true

[dovecot]
enabled = true

[pure-ftpd]
enabled = true
EOF
    run systemctl restart fail2ban
    ok "fail2ban jails enabled (ssh/postfix/dovecot/ftp)"
}

# --- prompts -----------------------------------------------------------------

prompt_inputs() {
    # Only prompt for what's still unset, and only in an interactive install.
    $DRY_RUN && return 0
    [[ "$NONINTERACTIVE" == "true" || "$NONINTERACTIVE" == "1" ]] && return 0
    if [[ -z "$PANEL_DOMAIN" ]]; then
        read -r -p "Panel domain (e.g. panel.example.com, blank to use IP): " PANEL_DOMAIN
    fi
    if [[ -z "$LE_EMAIL" ]]; then
        read -r -p "Let's Encrypt / admin email: " LE_EMAIL
    fi
    if [[ -z "$ADMIN_USER" ]]; then
        read -r -p "Admin username [admin]: " ADMIN_USER
        ADMIN_USER="${ADMIN_USER:-admin}"
    fi
}

# --- uninstall ---------------------------------------------------------------

uninstall() {
    info "Uninstalling Forgehost"
    if ! $DRY_RUN; then
        printf '%s' "${C_YELLOW}This removes Forgehost's services, code, config and secrets. Hosting account data under /home, /var/vmail and MariaDB is left untouched. Continue? [y/N] ${C_RESET}"
        local reply=""
        [[ "$NONINTERACTIVE" == "true" || "$NONINTERACTIVE" == "1" ]] && reply="y"
        [[ -z "$reply" ]] && read -r reply
        [[ "$reply" =~ ^[Yy] ]] || die "aborted"
    fi
    run_sh "systemctl disable --now forgehost-api forgehost-provisiond 2>/dev/null || true"
    run_sh "rm -f /etc/systemd/system/forgehost-api.service /etc/systemd/system/forgehost-provisiond.service"
    run systemctl daemon-reload
    run_sh "rm -f /etc/cron.d/forgehost-monitoring /etc/cron.d/forgehost-cloudflare /etc/cron.d/forgehost-update /etc/cron.d/forgehost-jobs /etc/cron.d/forgehost-maintenance /etc/cron.d/forgehost-sitestats"
    run_sh "rm -f /etc/logrotate.d/forgehost-api"
    run_sh "rm -rf '${DEST}'"
    run_sh "rm -rf '${CONF_DIR}'"
    warn "kept: ${DATA_DIR} (control-plane DB), ${LOG_DIR}, /home/*, /var/vmail, MariaDB data"
    warn "to remove those too: rm -rf ${DATA_DIR} ${LOG_DIR}  (irreversible)"
    ok "Forgehost services + code removed"
    summary
    exit 0
}

# --- summary + main ----------------------------------------------------------

summary() {
    printf '\n%s\n' "${C_BOLD}Summary:${C_RESET} ${C_GREEN}${STEP_OK} ok${C_RESET}, ${C_YELLOW}${STEP_SKIP} skipped${C_RESET}, ${C_RED}${STEP_FAIL} failed${C_RESET}"
    if ! $DRY_RUN; then
        printf 'Full log: %s\n' "$INSTALL_LOG"
    fi
}

usage() {
    cat <<EOF
Forgehost installer

Usage: sudo bash scripts/install.sh [OPTIONS]

  --dry-run      Print every step without changing anything.
  --uninstall    Remove Forgehost services, code and config (keeps user data).
  -h, --help     Show this help.

Non-interactive install via env vars:
  FH_PANEL_DOMAIN, FH_LE_EMAIL, FH_ADMIN_USER, FH_ADMIN_PASSWORD,
  FH_SERVER_IP, FH_NONINTERACTIVE=1
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)   DRY_RUN=true ;;
            --uninstall) UNINSTALL=true ;;
            -h|--help)   usage; exit 0 ;;
            *)           die "unknown option: $1 (try --help)" ;;
        esac
        shift
    done
}

main() {
    parse_args "$@"

    # NB: this line previously used ${DRY_RUN:+...}, which expands whenever
    # DRY_RUN is non-empty -- and it's always the non-empty string
    # "true"/"false", so every run printed "(dry-run)". Fixed to a real test.
    local dry_marker=""
    $DRY_RUN && dry_marker=" ${C_YELLOW}(dry-run)${C_RESET}"
    printf '%s\n' "${C_BOLD}Forgehost installer v${FORGEHOST_VERSION}${C_RESET}${dry_marker}"
    _logline "=== install run start (dry_run=${DRY_RUN} uninstall=${UNINSTALL}) ==="

    if $UNINSTALL; then
        uninstall
    fi

    preflight
    prompt_inputs

    apt_update
    install_base_packages
    install_openlitespeed
    install_stack_packages
    setup_quota
    setup_system_users
    deploy_app
    setup_config
    setup_mariadb
    setup_powerdns
    setup_ssl_bootstrap
    install_systemd_units
    install_cron_and_logrotate
    start_services
    create_admin
    setup_firewall

    summary
    if [[ "$STEP_FAIL" -eq 0 ]]; then
        info "Done. Forgehost v${FORGEHOST_VERSION} -- Panel: https://${PANEL_DOMAIN:-${SERVER_IP:-<server-ip>}}:9443/login"
        $DRY_RUN && info "This was a dry-run -- nothing was changed."
    else
        die "${STEP_FAIL} step(s) failed -- see ${INSTALL_LOG}"
    fi
}

main "$@"

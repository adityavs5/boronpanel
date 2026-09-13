#!/usr/bin/env bash
#
# Boron installer (Run A feature 9).
#
# One command to stand up Boron on a fresh Ubuntu 24.04 server. Faithful,
# idempotent translation of the hand-tested runbook in README.md -- every step
# here corresponds to a numbered section there, and each is safe to re-run.
#
#   sudo bash scripts/install.sh                 # full install (interactive)
#   sudo bash scripts/install.sh --dry-run       # print the plan, change nothing
#   sudo bash scripts/install.sh --uninstall     # remove Boron (keeps user data)
#
# Non-interactive install: preseed the prompts with env vars --
#   FH_PANEL_DOMAIN, FH_LE_EMAIL, FH_ADMIN_USER, FH_ADMIN_PASSWORD,
#   FH_SERVER_IP, FH_NONINTERACTIVE=1, FH_MAXMIND_LICENSE_KEY (optional,
#   see README.md "GeoLite2 setup")
#
# No external dependencies beyond bash + coreutils + the OS package manager.
# Progress is printed per step (OK / FAIL / SKIP) and appended to
# /var/log/boron-install.log.

set -euo pipefail

# --- constants ---------------------------------------------------------------

readonly DEST="/opt/boron"
readonly CONF_DIR="/etc/boron"
readonly DATA_DIR="/var/lib/boron"
readonly LOG_DIR="/var/log/boron"
readonly INSTALL_LOG="/var/log/boron-install.log"
readonly VENV="${DEST}/.venv"
readonly MIN_RAM_MB=1024
readonly MIN_DISK_GB=10
# Ports the stack binds and expects to own on a fresh box. A conflict here
# means something else is already installed -- surfaced in pre-flight, not
# discovered halfway through.
# Public TCP listeners plus the panel's internal PowerDNS API/database
# listeners. Passive FTP is a range and is configured separately below.
readonly REQUIRED_PORTS=(2222 8081 80 443 21 25 110 143 587 993 995 3306 53)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO_ROOT

# Panel version -- read from version.py, the repo-wide single source of truth
# (scripts/release.sh bumps it; the API and SPA display the same value).
BORON_VERSION="$(sed -n 's/^BORON_VERSION = "\(.*\)"$/\1/p' "${REPO_ROOT}/version.py" 2>/dev/null || true)"
BORON_VERSION="${BORON_VERSION:-unknown}"
readonly BORON_VERSION

# --- runtime flags -----------------------------------------------------------

DRY_RUN=false
UNINSTALL=false
NONINTERACTIVE="${FH_NONINTERACTIVE:-false}"

PANEL_DOMAIN="${FH_PANEL_DOMAIN:-}"
LE_EMAIL="${FH_LE_EMAIL:-}"
ADMIN_USER="${FH_ADMIN_USER:-admin}"
ADMIN_PASSWORD="${FH_ADMIN_PASSWORD:-}"
SERVER_IP="${FH_SERVER_IP:-}"
# QA round 2, item 12: optional -- MaxMind requires a free account + license
# key for GeoLite2 downloads (no anonymous path), so this is never prompted
# interactively (same treatment as CLOUDFLARE_API_TOKEN -- an operator
# credential this project never acquires on the operator's behalf). Site
# statistics' top-countries breakdown degrades gracefully without it.
MAXMIND_LICENSE_KEY="${FH_MAXMIND_LICENSE_KEY:-}"

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
    local output rc
    output="$(mktemp)"
    _logline "RUN   $*"
    if "$@" >"$output" 2>&1; then
        cat "$output" >>"$INSTALL_LOG" 2>/dev/null || true
        rm -f "$output"
        return 0
    else
        # Capture the command status in the else branch; reading `$?` after
        # the `if` compound command would mask a failed service command as 0.
        rc=$?
    fi
    cat "$output" >>"$INSTALL_LOG" 2>/dev/null || true
    fail "command failed (exit ${rc}): $*"
    printf '%s\n' "--- command output ---" >&2
    cat "$output" >&2
    printf '%s\n' '--- end command output ---' >&2
    rm -f "$output"
    die "command failed (exit ${rc}); see ${INSTALL_LOG}"
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
    rsync openssl cron logrotate apache2-utils geoipupdate
    nodejs npm composer sudo php-cli php-mysql
)
readonly STACK_PKGS=(
    mariadb-server postfix dovecot-core dovecot-imapd dovecot-lmtpd
    dovecot-mysql dovecot-sieve postfix-mysql pdns-server pdns-backend-sqlite3
    pure-ftpd certbot rclone restic spamassassin fail2ban redis-server
)
# Ubuntu 24.04 does not publish an `imapsync` binary package. Install the
# Perl modules it needs from Ubuntu, then install the pinned upstream source
# archive in install_imapsync() below. Keeping these separate from STACK_PKGS
# prevents apt from being asked for a package name that Noble cannot resolve.
readonly IMAPSYNC_PKGS=(
    perl libdigest-hmac-perl libencode-imaputf7-perl
    libfile-copy-recursive-perl libfile-tail-perl libio-socket-inet6-perl
    libio-socket-ssl-perl libio-tee-perl libmail-imapclient-perl
    libreadonly-perl libregexp-common-perl libsys-meminfo-perl
    libterm-readkey-perl libunicode-string-perl
)
readonly IMAPSYNC_VERSION="2.229"
readonly IMAPSYNC_URL="https://github.com/imapsync/imapsync/archive/refs/tags/imapsync-2.229.tar.gz"
readonly IMAPSYNC_SHA256="2199899732d5563e88fa5cc75d332dba69ae3e416a84e0edbb45ca3832e0a127"
readonly OLS_PKGS=(
    openlitespeed ols-modsecurity modsecurity-crs
    lsphp81 lsphp81-common lsphp81-curl lsphp81-mysql lsphp81-opcache lsphp81-intl lsphp81-redis lsphp81-sqlite3 lsphp81-imagick
    lsphp82 lsphp82-common lsphp82-curl lsphp82-mysql lsphp82-opcache lsphp82-intl lsphp82-redis lsphp82-sqlite3 lsphp82-imagick
    lsphp83 lsphp83-common lsphp83-curl lsphp83-mysql lsphp83-opcache lsphp83-intl lsphp83-redis lsphp83-sqlite3 lsphp83-imagick
    lsphp84 lsphp84-common lsphp84-curl lsphp84-mysql lsphp84-opcache lsphp84-intl lsphp84-redis lsphp84-sqlite3 lsphp84-imagick
    # LiteSpeed's Noble repository currently publishes no PHP 8.5 OPcache
    # module (and apt-cache show lsphp85 does not include Zend OPcache in the
    # core package). PHP 8.5 therefore installs without OPcache until
    # LiteSpeed publishes a compatible module; 8.1-8.4 keep their modules.
    lsphp85 lsphp85-common lsphp85-curl lsphp85-mysql lsphp85-intl lsphp85-redis lsphp85-sqlite3 lsphp85-imagick
)
readonly NODE_RUNTIME_VERSIONS=(18.20.8 20.19.6 22.16.0)

# apt-get's output is normally redirected to INSTALL_LOG so a failed install
# cannot flood an operator's terminal. Keep that behavior on success, but
# replay the complete captured output on failure. This is especially
# important for the long OLS package list: set -e must never turn a missing
# package into an apparently silent return to the shell prompt.
apt_run() {
    local description="$1"
    shift
    if $DRY_RUN; then
        printf '  %s' "${C_YELLOW}[dry]${C_RESET}"
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi

    local output rc
    output="$(mktemp)"
    _logline "RUN   $*"
    if "$@" >"$output" 2>&1; then
        cat "$output" >>"$INSTALL_LOG" 2>/dev/null || true
        rm -f "$output"
        return 0
    else
        # Capture the command status in the else branch. Reading `$?` after
        # the `if` compound command would report the status of the `if`
        # construct (0 when no branch ran), masking apt-get failures.
        rc=$?
    fi

    cat "$output" >>"$INSTALL_LOG" 2>/dev/null || true
    fail "${description} failed (exit ${rc})"
    printf '%s\n' "--- ${description} output ---" >&2
    cat "$output" >&2
    printf '%s\n' "--- end ${description} output ---" >&2
    rm -f "$output"
    return "$rc"
}

apt_update() {
    export DEBIAN_FRONTEND=noninteractive
    if ! apt_run "apt cache update" apt-get update -qq; then
        die "apt cache update failed; see ${INSTALL_LOG}"
    fi
    ok "apt cache updated"
}

install_base_packages() {
    info "Installing base packages"
    if ! apt_run "base package installation" apt-get install -y "${BASE_PKGS[@]}"; then
        die "base package installation aborted; see ${INSTALL_LOG}"
    fi
    ok "base packages installed (python, node, composer, tooling)"
    # Pin and verify the release before making it executable.
    local wp_version=2.12.0
    local wp_sha256=ce34ddd838f7351d6759068d09793f26755463b4a4610a5a5c0a97b68220d85c
    if [[ -f /usr/local/bin/wp-cli.phar ]] && [[ "$(sha256sum /usr/local/bin/wp-cli.phar | cut -d ' ' -f1)" == "$wp_sha256" ]]; then
        skip "verified wp-cli already installed"
    else
        local wp_download
        wp_download=$(mktemp)
        curl -fsSL --retry 3 -o "$wp_download" "https://github.com/wp-cli/wp-cli/releases/download/v${wp_version}/wp-cli-${wp_version}.phar" || { rm -f "$wp_download"; die "WP-CLI download failed"; }
        [[ "$(sha256sum "$wp_download" | cut -d ' ' -f1)" == "$wp_sha256" ]] || { rm -f "$wp_download"; die "WP-CLI checksum mismatch"; }
        run install -m 0755 "$wp_download" /usr/local/bin/wp-cli.phar
        rm -f "$wp_download"
        ok "verified wp-cli installed"
    fi

}

# OpenLiteSpeed's namespace directive is enabled in the Boron-generated
# httpd_config.conf.  OLS resolves namespaceConf relative to its own conf
# directory, so a fresh install must create this file before lshttpd is
# started (otherwise the first account provision fails at `openlitespeed -t`).
#
# The bind rules below are the live-tested baseline documented in
# docs/NAMESPACE-ANSWERS.md.  The companion lsns files are OLS runtime state:
# initialize them on a fresh host, but never erase an operator's denylist or
# host-exec additions when the installer is re-run.
setup_ols_namespace() {
    info "Configuring OpenLiteSpeed namespace isolation"
    run install -d -m 0755 /usr/local/lsws/conf /usr/local/lsws/lsns/conf

    write_file /usr/local/lsws/conf/nsconf.conf 0644 <<'EOF'
$HOMEDIR/tmp /tmp,tmp
/usr,ro-bind
/lib,ro-bind
/lib64,ro-bind-try
/bin,ro-bind
/sbin,ro-bind
/var,dir
/proc,proc
../tmp var/tmp,symlink
/dev,dev
/etc/localtime,ro-bind-try
/etc/ld.so.cache,ro-bind-try
/etc/resolv.conf,ro-bind-try
/etc/ssl,ro-bind-try
/etc/pki,ro-bind-try
$HOMEDIR,bind-try
/run/mysqld/mysqld.sock,bind-try
/run/user/$UID,bind-try
/var/lib/php/sessions,bind-try
/usr/local/lsws/tmp/lshttpd,bind-try
/var/lib/roundcube,bind-try
/etc/roundcube,ro-bind-try
/etc/phpmyadmin,ro-bind-try
/var/lib/phpmyadmin,bind-try
$PASSWD,nobody,mysql
$GROUP,nogroup,mysql
EOF

    if $DRY_RUN || [[ ! -e /usr/local/lsws/lsns/conf/lsns.conf ]]; then
        write_file /usr/local/lsws/lsns/conf/lsns.conf 0644 <<'EOF'
1000
EOF
    else
        skip "OpenLiteSpeed namespace minimum UID already configured"
    fi
    if $DRY_RUN || [[ ! -e /usr/local/lsws/lsns/conf/ns_disabled_uids.conf ]]; then
        write_file /usr/local/lsws/lsns/conf/ns_disabled_uids.conf 0644 <<'EOF'
EOF
    else
        skip "OpenLiteSpeed namespace denylist already configured"
    fi
    if $DRY_RUN || [[ ! -e /usr/local/lsws/lsns/conf/hostexec.conf ]]; then
        write_file /usr/local/lsws/lsns/conf/hostexec.conf 0644 <<'EOF'
/usr/sbin/sendmail
EOF
    else
        skip "OpenLiteSpeed namespace host-exec list already configured"
    fi
    ok "OpenLiteSpeed namespace config installed (min UID 1000)"
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
    if ! apt_run "OpenLiteSpeed and lsphp package installation" apt-get install -y "${OLS_PKGS[@]}"; then
        die "OpenLiteSpeed and lsphp installation aborted; see ${INSTALL_LOG}"
    fi
    # Must precede the first lshttpd start: httpd_config.conf contains the
    # namespaceConf directive and OLS rejects it when this file is absent.
    setup_ols_namespace
    run systemctl enable --now lshttpd
    ok "OpenLiteSpeed + PHP 8.1 through 8.5 installed"
}

install_stack_packages() {
    info "Installing the hosting stack (mail/DNS/FTP/SSL/security)"
    # Postfix must not launch its interactive config screen.
    run_sh "echo 'postfix postfix/main_mailer_type select Internet Site' | debconf-set-selections"
    run_sh "echo \"postfix postfix/mailname string \$(hostname -f)\" | debconf-set-selections"
    if ! apt_run "hosting stack package installation" apt-get install -y "${STACK_PKGS[@]}"; then
        die "hosting stack installation aborted; see ${INSTALL_LOG}"
    fi
    # The bind backend is pulled in as a pdns dependency but unused here.
    if [[ -f /etc/powerdns/pdns.d/bind.conf ]]; then
        run mv /etc/powerdns/pdns.d/bind.conf /etc/powerdns/pdns.d/bind.conf.disabled
    fi
    ok "hosting stack installed"
}

install_node_runtimes() {
    info "Installing Boron Node.js runtimes (18, 20, 22)"
    local version major
    for version in "${NODE_RUNTIME_VERSIONS[@]}"; do
        major="${version%%.*}"
        if [[ -x "/opt/boron-nodejs/${major}/bin/node" ]] && \
            [[ "$("/opt/boron-nodejs/${major}/bin/node" --version 2>/dev/null || true)" == "v${version}" ]]; then
            skip "Node.js ${version} already installed"
            continue
        fi
        run_sh "set -euo pipefail
node_version='v${version}'
node_archive=\"node-\${node_version}-linux-x64.tar.xz\"
node_tmp=\$(mktemp -d)
trap 'rm -rf \"\$node_tmp\"' EXIT
curl -fsSLo \"\$node_tmp/SHASUMS256.txt\" \"https://nodejs.org/dist/\${node_version}/SHASUMS256.txt\"
expected=\$(awk -v archive=\"\$node_archive\" '\$2 == archive { print \$1 }' \"\$node_tmp/SHASUMS256.txt\")
test -n \"\$expected\"
curl -fsSLo \"\$node_tmp/\$node_archive\" \"https://nodejs.org/dist/\${node_version}/\$node_archive\"
printf '%s  %s\\n' \"\$expected\" \"\$node_tmp/\$node_archive\" | sha256sum -c -
install -d -m 0755 '/opt/boron-nodejs/${major}'
tar -xJf \"\$node_tmp/\$node_archive\" -C '/opt/boron-nodejs/${major}' --strip-components=1"
        ok "Node.js ${version} installed at /opt/boron-nodejs/${major}"
    done
}

install_filebrowser() {
    info "Installing FileBrowser Quantum"
    run bash "${DEST}/scripts/install_filebrowser.sh"
    ok "FileBrowser Quantum installed"
}

install_imapsync() {
    info "Installing ImapSync"
    if [[ -x /usr/local/bin/imapsync ]] && \
        /usr/local/bin/imapsync --version >/dev/null 2>&1; then
        skip "ImapSync already available at /usr/local/bin/imapsync"
        return 0
    fi

    if ! apt_run "ImapSync Perl dependency installation" \
        apt-get install -y "${IMAPSYNC_PKGS[@]}"; then
        die "ImapSync dependency installation aborted; see ${INSTALL_LOG}"
    fi

    # There is no Ubuntu 24.04 `imapsync` package. Fetch the upstream source
    # archive at a pinned version and verify it before installing the script;
    # never execute an unverified remote download as root.
    run_sh "set -euo pipefail
tmpdir=\$(mktemp -d)
trap 'rm -rf \"\$tmpdir\"' EXIT
archive=\"\$tmpdir/imapsync.tar.gz\"
curl -fsSL -o \"\$archive\" '${IMAPSYNC_URL}'
printf '%s  %s\\n' '${IMAPSYNC_SHA256}' \"\$archive\" | sha256sum -c -
tar -xzf \"\$archive\" -C \"\$tmpdir\"
source=\$(find \"\$tmpdir\" -type f -name imapsync -path '*/imapsync' -print -quit)
test -n \"\$source\"
install -m 0755 -o root -g root \"\$source\" /usr/local/bin/imapsync"
    run /usr/local/bin/imapsync --version
    ok "ImapSync ${IMAPSYNC_VERSION} installed at /usr/local/bin/imapsync"
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
    info "Creating system users (vmail, boron-api)"
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

    ensure_group boron-api
    if getent passwd boron-api >/dev/null; then
        skip "user 'boron-api' exists"
    else
        run useradd --system --no-create-home --shell /usr/sbin/nologin \
            -g boron-api boron-api
        ok "user 'boron-api' created"
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
    # requirements.txt pins Certbot and both DNS providers, including
    # certbot-dns-cloudflare, inside this venv used by Boron's ACME jobs.
    run "${VENV}/bin/pip" install -r "${DEST}/requirements.txt"
    ok "Python dependencies installed"

    # Source-only repository: static/dist is generated on every install from
    # the frontend source. Never trust a stale or operator-supplied bundle.
    run_sh "cd '${DEST}/frontend' && npm ci --no-audit --no-fund && npm run build"
    ok "web UI built from frontend source"

    # Security: logs contain cross-tenant operational data and must never be
    # readable by hosted accounts. The API writes through its service group;
    # the daemon's umask and explicit file modes keep files group-readable
    # only. Never make the directory world-readable/traversable.
    run chgrp boron-api "$LOG_DIR"
    run chmod 2770 "$LOG_DIR"
    run find "$LOG_DIR" -type f -exec chmod 0640 {} +
    ok "log dir ${LOG_DIR} restricted to root and boron-api"
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
    # Some minimal/container environments have no assigned address yet; do
    # not let a SIGPIPE/empty `hostname -I` pipeline abort --dry-run under
    # `set -e`. A real operator can still provide FH_SERVER_IP explicitly.
    if [[ -z "$SERVER_IP" ]]; then
        SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi

    write_file "${CONF_DIR}/boron.toml" 640 "root:boron-api" <<EOF
server_public_ip = "${SERVER_IP}"
letsencrypt_email = "${LE_EMAIL}"
EOF
    ok "boron.toml written"

    # SESSION_SECRET: the one secret the API needs; its own 0640 root:api file.
    if [[ -f "${CONF_DIR}/api-secrets.env" ]] && grep -q '^SESSION_SECRET=' "${CONF_DIR}/api-secrets.env"; then
        skip "SESSION_SECRET already set"
    else
        run_sh "install -m 640 /dev/null '${CONF_DIR}/api-secrets.env'"
        run_sh "chown root:boron-api '${CONF_DIR}/api-secrets.env'"
        run_sh "printf 'SESSION_SECRET=%s\n' \"\$(openssl rand -hex 32)\" >> '${CONF_DIR}/api-secrets.env'"
        ok "SESSION_SECRET generated"
    fi
}

setup_mariadb() {
    info "Securing MariaDB + creating Boron database users"
    run systemctl enable --now mariadb
    if [[ -f "${CONF_DIR}/secrets.env" ]] && grep -q '^MARIADB_DAEMON_PASSWORD=' "${CONF_DIR}/secrets.env"; then
        skip "MariaDB users already provisioned"
        return 0
    fi
    if $DRY_RUN; then
        printf '  %s secure MariaDB root, create boron_daemon + boron_mailro, create boron_mail schema\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi
    local root_pass daemon_pass mailro_pass
    root_pass="$(_rand_pass)"; daemon_pass="$(_rand_pass)"; mailro_pass="$(_rand_hex 24)"
    mysql -u root <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '${root_pass}';
CREATE USER IF NOT EXISTS 'boron_daemon'@'localhost' IDENTIFIED BY '${daemon_pass}';
GRANT CREATE, DROP, ALTER, INDEX, CREATE USER, GRANT OPTION, SELECT,
  INSERT, UPDATE, DELETE, RELOAD, PROCESS, LOCK TABLES, REFERENCES,
  CREATE TEMPORARY TABLES ON *.* TO 'boron_daemon'@'localhost';
CREATE DATABASE IF NOT EXISTS boron_mail CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'boron_mailro'@'localhost' IDENTIFIED BY '${mailro_pass}';
GRANT SELECT ON boron_mail.* TO 'boron_mailro'@'localhost';
FLUSH PRIVILEGES;
SQL
    mysql -u root -p"${root_pass}" boron_mail <<'SQL'
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
    printf '[client]\nuser=root\npassword=%s\n' "${root_pass}" >/root/.my.cnf
    chmod 600 /root/.my.cnf
    touch "${CONF_DIR}/secrets.env"; chmod 600 "${CONF_DIR}/secrets.env"
    {
        printf 'MARIADB_ROOT_PASSWORD=%s\n' "${root_pass}"
        printf 'MARIADB_DAEMON_PASSWORD=%s\n' "${daemon_pass}"
        printf 'MARIADB_MAILRO_PASSWORD=%s\n' "${mailro_pass}"
    } >>"${CONF_DIR}/secrets.env"
    ok "MariaDB secured; boron_daemon + boron_mailro created"
}

setup_powerdns() {
    info "Configuring PowerDNS (gsqlite3 + REST API)"
    if [[ -f /etc/powerdns/pdns.d/boron.conf ]]; then
        skip "PowerDNS already configured"
        return 0
    fi
    if $DRY_RUN; then
        printf '  %s init pdns sqlite db, write boron.conf with a fresh API key\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi
    local pdns_key; pdns_key="$(_rand_hex 24)"
    mkdir -p /var/lib/powerdns
    if [[ ! -f /var/lib/powerdns/pdns.sqlite3 ]]; then
        sqlite3 /var/lib/powerdns/pdns.sqlite3 < /usr/share/pdns-backend-sqlite3/schema/schema.sqlite3.sql
        chown pdns:pdns /var/lib/powerdns/pdns.sqlite3
        chmod 660 /var/lib/powerdns/pdns.sqlite3
    fi
    cat >/etc/powerdns/pdns.d/boron.conf <<EOF
launch+=gsqlite3
gsqlite3-database=/var/lib/powerdns/pdns.sqlite3
gsqlite3-dnssec=no
webserver=yes
webserver-address=127.0.0.1
webserver-port=8081
webserver-allow-from=127.0.0.1
api=yes
api-key=${pdns_key}
default-soa-content=ns1.boron.invalid hostmaster.@ 0 10800 3600 604800 3600
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
        run_sh "openssl req -x509 -nodes -newkey rsa:2048 -keyout '${CONF_DIR}/ssl/default.key' -out '${CONF_DIR}/ssl/default.crt' -days 3650 -subj '/CN=boron-default'"
        run chmod 600 "${CONF_DIR}/ssl/default.key"
        ok "default cert generated"
    fi
    if [[ -f "${CONF_DIR}/ssl/api/panel.crt" ]]; then
        skip "panel TLS cert exists"
    else
        run_sh "openssl req -x509 -nodes -newkey rsa:2048 -keyout '${CONF_DIR}/ssl/api/panel.key' -out '${CONF_DIR}/ssl/api/panel.crt' -days 3650 -subj '/CN=${PANEL_DOMAIN:-boron-panel}'"
        run_sh "chown boron-api:boron-api '${CONF_DIR}/ssl/api/panel.key' '${CONF_DIR}/ssl/api/panel.crt'"
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

setup_mail_services() {
    info "Configuring Postfix + Dovecot virtual mail"
    if $DRY_RUN; then
        printf '  %s configure Postfix/Dovecot SQL virtual mail, SMTP submission, and TLS\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi

    local mailro_pass
    mailro_pass="$(awk -F= '$1 == "MARIADB_MAILRO_PASSWORD" { print substr($0, index($0, "=") + 1); exit }' "${CONF_DIR}/secrets.env")"
    [[ -n "$mailro_pass" ]] || die "MARIADB_MAILRO_PASSWORD is missing from ${CONF_DIR}/secrets.env"

    install -d -m 0750 /etc/postfix/boron
    cat >/etc/postfix/boron/mysql-virtual-domains.cf <<EOF
user = boron_mailro
password = ${mailro_pass}
hosts = 127.0.0.1
dbname = boron_mail
query = SELECT domain FROM mail_domain WHERE domain='%s' AND active=1
EOF
    cat >/etc/postfix/boron/mysql-virtual-mailboxes.cf <<EOF
user = boron_mailro
password = ${mailro_pass}
hosts = 127.0.0.1
dbname = boron_mail
query = SELECT CONCAT(d.domain, '/', u.local_part, '/') FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id WHERE CONCAT(u.local_part, '@', d.domain)='%s' AND u.active=1 AND d.active=1
EOF
    cat >/etc/postfix/boron/mysql-virtual-forwards.cf <<EOF
user = boron_mailro
password = ${mailro_pass}
hosts = 127.0.0.1
dbname = boron_mail
query = SELECT GROUP_CONCAT(destination SEPARATOR ',') FROM ( SELECT f.destination AS destination FROM mail_forward f JOIN mail_domain d ON f.domain_id = d.id WHERE CONCAT(f.source_local_part, '@', d.domain) = '%s' AND f.active = 1 AND d.active = 1 UNION ALL SELECT CONCAT(m.local_part, '@', d.domain) AS destination FROM mail_user m JOIN mail_domain d ON m.domain_id = d.id JOIN mail_catchall c ON c.domain_id = d.id WHERE CONCAT(m.local_part, '@', d.domain) = '%s' AND m.active = 1 AND d.active = 1 AND c.active = 1 UNION ALL SELECT c.destination AS destination FROM mail_catchall c JOIN mail_domain d ON c.domain_id = d.id WHERE CONCAT('@', d.domain) = '%s' AND c.active = 1 AND d.active = 1 ) combined
EOF
    chmod 0640 /etc/postfix/boron/*.cf
    chown root:postfix /etc/postfix/boron/*.cf

    postconf -e "virtual_mailbox_domains = mysql:/etc/postfix/boron/mysql-virtual-domains.cf"
    postconf -e "virtual_mailbox_maps = mysql:/etc/postfix/boron/mysql-virtual-mailboxes.cf"
    postconf -e "virtual_alias_maps = mysql:/etc/postfix/boron/mysql-virtual-forwards.cf"
    postconf -e "virtual_mailbox_base = /var/vmail"
    postconf -e "virtual_uid_maps = static:150"
    postconf -e "virtual_gid_maps = static:150"
    postconf -e "virtual_minimum_uid = 150"
    postconf -e "virtual_transport = lmtp:unix:private/dovecot-lmtp"
    postconf -e "smtpd_sasl_type = dovecot"
    postconf -e "smtpd_sasl_path = private/auth"
    postconf -e "smtpd_sasl_auth_enable = yes"
    postconf -e "smtpd_tls_cert_file = ${CONF_DIR}/ssl/default.crt"
    postconf -e "smtpd_tls_key_file = ${CONF_DIR}/ssl/default.key"
    postconf -e "smtpd_tls_security_level = may"
    postconf -e "smtpd_tls_auth_only = yes"
    postconf -Me 'submission/inet=submission inet n - y - - smtpd'
    postconf -P 'submission/inet/syslog_name=postfix/submission'
    postconf -P 'submission/inet/smtpd_tls_security_level=encrypt'
    postconf -P 'submission/inet/smtpd_sasl_auth_enable=yes'
    postconf -P 'submission/inet/smtpd_client_restrictions=permit_sasl_authenticated,reject'

    cat >/etc/dovecot/dovecot-sql.conf.ext <<EOF
driver = mysql
connect = host=127.0.0.1 dbname=boron_mail user=boron_mailro password=${mailro_pass}
default_pass_scheme = ARGON2ID
password_query = SELECT CONCAT(u.local_part, '@', d.domain) AS user, u.password FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1
user_query = SELECT 150 AS uid, 150 AS gid, CONCAT('/var/vmail/', d.domain, '/', u.local_part) AS home, CONCAT('maildir:/var/vmail/', d.domain, '/', u.local_part, '/Maildir') AS mail FROM mail_user u JOIN mail_domain d ON d.id=u.domain_id WHERE CONCAT(u.local_part, '@', d.domain)='%u' AND u.active=1 AND d.active=1
EOF
    chown root:dovecot /etc/dovecot/dovecot-sql.conf.ext
    chmod 0640 /etc/dovecot/dovecot-sql.conf.ext
    sed -i 's/^!include auth-system.conf.ext/#!include auth-system.conf.ext/' /etc/dovecot/conf.d/10-auth.conf
    grep -qxF '!include auth-sql.conf.ext' /etc/dovecot/conf.d/10-auth.conf || echo '!include auth-sql.conf.ext' >>/etc/dovecot/conf.d/10-auth.conf
    cat >/etc/dovecot/conf.d/90-boron.conf <<EOF
mail_location = maildir:/var/vmail/%d/%n/Maildir
first_valid_uid = 150
last_valid_uid = 150
first_valid_gid = 150
last_valid_gid = 150
ssl = required
ssl_cert = <${CONF_DIR}/ssl/default.crt
ssl_key = <${CONF_DIR}/ssl/default.key
service auth {
  unix_listener /var/spool/postfix/private/auth {
    mode = 0660
    user = postfix
    group = postfix
  }
}
service lmtp {
  unix_listener /var/spool/postfix/private/dovecot-lmtp {
    mode = 0600
    user = postfix
    group = postfix
  }
}
EOF
    run doveconf -n
    run postfix check
    run systemctl enable --now dovecot postfix
    run systemctl restart dovecot postfix
    ok "Postfix + Dovecot virtual mail configured"
}

# --- 6. systemd services + cron + logrotate ----------------------------------

install_systemd_units() {
    info "Installing systemd units"
    run install -m 644 "${DEST}/deploy/boron-provisiond.service" /etc/systemd/system/
    run install -m 644 "${DEST}/deploy/boron-api.service" /etc/systemd/system/
    run install -m 644 "${DEST}/deploy/boron-filebrowser.service" /etc/systemd/system/
    run systemctl daemon-reload
    run systemctl enable boron-provisiond
    run systemctl enable boron-filebrowser
    ok "systemd units installed + provisiond enabled"
}

install_cron_and_logrotate() {
    info "Installing periodic jobs + log rotation"
    # Run A feature 7: API access/error log rotation.
    run install -m 644 "${DEST}/deploy/boron-api.logrotate" /etc/logrotate.d/boron-api
    # Run A feature 5: service health monitoring, every 5 min.
    run install -m 644 "${DEST}/deploy/boron-monitoring.cron" /etc/cron.d/boron-monitoring
    # Cloudflare edge-range + zone-activation polls.
    run install -m 644 "${DEST}/deploy/boron-cloudflare.cron" /etc/cron.d/boron-cloudflare
    # Panel update system: daily release check + admin email + old-version pruning.
    run install -m 644 "${DEST}/deploy/boron-update.cron" /etc/cron.d/boron-update
    # Core infrastructure crons (health, usage, backups, ssl expiry, pma
    # tokens, usage alerts) -- root-owned, same trust level as the daemon.
    run install -m 644 "${DEST}/deploy/boron-jobs.cron" /etc/cron.d/boron-jobs
    # Missing-features batch, goal features 2 + 6: maintenance-mode
    # auto-disable sweep + daily site-statistics snapshot.
    run install -m 644 "${DEST}/deploy/boron-maintenance.cron" /etc/cron.d/boron-maintenance
    run install -m 644 "${DEST}/deploy/boron-sitestats.cron" /etc/cron.d/boron-sitestats
    run install -m 644 "${DEST}/deploy/boron-certbot-renew.cron" /etc/cron.d/boron-certbot-renew
    ok "logrotate + cron jobs installed"
}

# --- 7. bring-up: migrations, bootstrap, admin -------------------------------

start_services() {
    info "Starting services + bootstrapping OLS"
    run systemctl restart boron-provisiond
    # DB schema is created by the daemon's own init_db() on first start
    # (create_all + additive migrations); give it a moment to bind its socket.
    if ! $DRY_RUN; then
        local _wait
        for _wait in 1 2 3 4 5 6 7 8 9 10; do
            [[ -S /run/boron/provisiond.sock ]] && break
            sleep 1
        done
    fi
    # One-time: replace OLS's stock Example vhost with a clean baseline.
    run_sh "sudo -u boron-api -- '${VENV}/bin/python' -c \"import sys; sys.path.insert(0, '${DEST}'); from shared.rpc import RpcClient; RpcClient('/run/boron/provisiond.sock').call('system.bootstrap_ols', _actor='setup', _role='admin')\""
    run systemctl restart lshttpd
    run systemctl enable --now boron-api
    run systemctl enable --now boron-filebrowser
    ok "provisiond + api + FileBrowser started; OLS baseline applied"
}

bootstrap_security_services() {
    info "Configuring SpamAssassin + Boron fail2ban jails"
    run_sh "'${VENV}/bin/python' -c \"import sys; sys.path.insert(0, '${DEST}'); from daemon.fail2ban import bootstrap_jails; bootstrap_jails()\""
    run_sh "'${VENV}/bin/python' -c \"import sys; sys.path.insert(0, '${DEST}'); from daemon.spamfilter import bootstrap_spamassassin; bootstrap_spamassassin()\""
    ok "SpamAssassin and Boron fail2ban jails configured"
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
    # provisiond authenticates the Unix socket peer and rejects uid 0. The
    # installer itself runs as root, so bootstrap through the API service
    # identity just as a manual operator invocation must.
    if sudo -u boron-api -- "${VENV}/bin/python" "${DEST}/scripts/create_admin.py" "${args[@]}"; then
        ok "admin '${ADMIN_USER}' created"
    else
        warn "admin creation returned non-zero (may already exist) -- create manually as boron-api: sudo -u boron-api python3 '${DEST}/scripts/create_admin.py' --username '${ADMIN_USER}'"
    fi
}

# --- 8. firewall + fail2ban --------------------------------------------------

setup_firewall() {
    info "Configuring UFW + fail2ban"
    run ufw --force reset
    run ufw default deny incoming
    run ufw default allow outgoing
    # Always allow SSH before UFW is enabled so a remote installation cannot
    # lock its operator out. Keep this explicit instead of relying on a
    # provider-specific default policy.
    run ufw allow 22/tcp
    local p
    for p in 21 25 53 80 110 143 443 587 993 995 2222; do
        run ufw allow "${p}/tcp"
    done
    run ufw allow 53/udp
    # Must exactly match Pure-FTPd's PassivePortRange below.
    run ufw allow 30000:30100/tcp
    run ufw --force enable
    ok "UFW enabled (SSH/web/mail/DNS/FTP/panel allowed)"

    run systemctl enable --now fail2ban
    ok "fail2ban service enabled; Boron jails are configured after daemon startup"
}

# --- 8.5 Pure-FTPd (TLS required, chroot + PureDB auth + passive port range) -
#
# Promoted from a manual README runbook step ("20. FTP account management")
# into the installer itself -- these three fixes were previously something
# an operator had to remember to apply by hand after `apt install pure-ftpd`,
# which is exactly how a live box ended up running with FTP either broken
# (unchrooted logins) or entirely unreachable (see docs/CHECKPOINT-
# phase3-5.md for the live testing that found #1/#2, and the FTP root-cause
# investigation that found #3/#4 missing on an already-provisioned server).

setup_pureftpd() {
    info "Configuring Pure-FTPd (mandatory TLS, chroot, PureDB, passive range)"

    # 1. pure-ftpd's PAM config rejects any login shell not listed in
    # /etc/shells, and every hosting account uses /usr/sbin/nologin (no
    # interactive SSH, by design) -- without this line a hosting account's
    # own FTP login never authenticates at all. Idempotent: only appended
    # if not already present.
    run_sh "grep -qxF '/usr/sbin/nologin' /etc/shells || echo '/usr/sbin/nologin' >> /etc/shells"

    # 2. Chroot every FTP login to its own home directory. Without this,
    # confirmed live, a real hosting account could `CWD ..` all the way to
    # the server's real filesystem root and browse every other account's
    # home directory -- ARCHITECTURE.md's locked decision, never actually
    # applied by the installer until now.
    write_file /etc/pure-ftpd/conf/ChrootEveryone 644 <<'EOF'
yes
EOF

    # 3. Enable the PureDB backend (for FTP sub-accounts, daemon/ftp.py) as
    # an additional, higher-priority auth source -- existing system-account
    # logins keep working via the existing 65unix/70pam chain. The target,
    # /etc/pure-ftpd/conf/PureDB, ships with the pure-ftpd package itself
    # (an empty placeholder is valid, but the binary database must still be
    # generated before pure-ftpd-wrapper will start), so create both files
    # before enabling the auth-chain symlink.
    run mkdir -p /etc/pure-ftpd/auth
    if [[ ! -e /etc/pure-ftpd/pureftpd.passwd ]]; then
        run install -m 600 /dev/null /etc/pure-ftpd/pureftpd.passwd
    fi
    run pure-pw mkdb /etc/pure-ftpd/pureftpd.pdb \
        -f /etc/pure-ftpd/pureftpd.passwd
    run chmod 600 /etc/pure-ftpd/pureftpd.passwd /etc/pure-ftpd/pureftpd.pdb
    run ln -sf ../conf/PureDB /etc/pure-ftpd/auth/30pdb

    # 4. Pin the passive-mode data-port range to exactly what
    # setup_firewall() opens in UFW (30000:30100/tcp). Without this,
    # Pure-FTPd picks its own compiled-in/OS-assigned passive range, which
    # can fall outside the firewall hole -- passive-mode transfers (the
    # common case behind NAT/most FTP clients) would stall even once the
    # control connection on port 21 itself succeeds.
    write_file /etc/pure-ftpd/conf/PassivePortRange 644 <<'EOF'
30000 30100
EOF

    # Require encryption before accepting credentials. Reuse the bootstrap
    # certificate until an operator installs a publicly trusted FTP-specific
    # certificate; TLS is still mandatory and never silently falls back to
    # plaintext. Pure-FTPd expects a combined PEM key/certificate file.
    if [[ -f "${CONF_DIR}/ssl/api/panel.key" && -f "${CONF_DIR}/ssl/api/panel.crt" ]]; then
        run_sh "cat '${CONF_DIR}/ssl/api/panel.crt' '${CONF_DIR}/ssl/api/panel.key' > /etc/ssl/private/pure-ftpd.pem"
        run chmod 600 /etc/ssl/private/pure-ftpd.pem
    fi
    write_file /etc/pure-ftpd/conf/TLS 644 <<'EOF'
2
EOF

    run systemctl restart pure-ftpd
    ok "Pure-FTPd mandatory TLS/chroot/PureDB/passive-range configured"
}

# --- 8.6 PHP hardening (disable_functions default, item 9) -----------------

setup_php_hardening() {
    info "Hardening PHP (disable_functions default)"
    if $DRY_RUN; then
        # The real venv this needs doesn't exist yet in a dry-run (deploy_app
        # never actually ran) -- describe the plan without trying to execute
        # a python interpreter that isn't there (the exact mistake this
        # comment now prevents from being reintroduced: an earlier version
        # of this function computed $funcs via direct command substitution
        # with no dry-run guard at all, which crashed --dry-run outright
        # with "no such file or directory" under set -e, caught by actually
        # running --dry-run rather than just reading the diff).
        printf '  %s hardened disable_functions default (daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS) into lsphp81 through lsphp85 php.ini files\n' "${C_YELLOW}[dry]${C_RESET}"
        return 0
    fi
    # Single source of truth: daemon/phpdirectives.DEFAULT_DISABLE_FUNCTIONS
    # -- the same list an admin's per-account/per-domain override (item 9's
    # admin UI, daemon/phpfunctions.py) layers on top of via OLS's
    # phpIniOverride, so the two can never drift apart. Must run after
    # deploy_app (needs ${DEST} on disk and the venv built).
    local funcs
    funcs="$("${VENV}/bin/python" -c "
import sys
sys.path.insert(0, '${DEST}')
from daemon import phpdirectives
print(','.join(phpdirectives.DEFAULT_DISABLE_FUNCTIONS))
" 2>/dev/null)"
    if [[ -z "$funcs" ]]; then
        warn "could not determine the hardened disable_functions list -- skipping PHP hardening (this box's lsphp php.ini files are unchanged; admin overrides still work once configured manually)"
        return 0
    fi
    local ini version changed=false
    for version in 8.1 8.2 8.3 8.4 8.5; do
        ini="/usr/local/lsws/lsphp${version/./}/etc/php/${version}/litespeed/php.ini"
        if [[ -f "$ini" ]]; then
            run sed -i "s/^disable_functions[[:space:]]*=.*/disable_functions = ${funcs}/" "$ini"
            changed=true
        fi
    done
    if ! $changed; then
        warn "no lsphp php.ini files found -- skipping PHP hardening"
        return 0
    fi
    run systemctl restart lshttpd
    ok "PHP hardened (disable_functions: ${funcs})"
}

# --- 8.7 GeoLite2 (site statistics' top-countries breakdown, item 12) ------

setup_geoip() {
    if [[ -z "$MAXMIND_LICENSE_KEY" ]]; then
        info "GeoLite2 skipped (no MaxMind license key) -- site-statistics top-countries needs GeoLite2, but the rest of Boron works normally. Configure it later via POST /api/v1/admin/sitestats/geoip; see README.md's \"GeoLite2 setup\" section."
        run_sh "rm -f /etc/boron/maxmind-license /etc/cron.d/boron-geoip"
        return 0
    fi
    info "Fetching the MaxMind GeoLite2-Country database"
    # Passed via the environment, never interpolated into the logged
    # command string (run_sh logs/prints the snippet text verbatim, in
    # both dry-run and real runs -- a real secret must never appear in it).
    export MAXMIND_LICENSE_KEY
    if run_sh "'${VENV}/bin/python' -c \"import sys, os; sys.path.insert(0, '${DEST}'); from daemon import geoip; geoip.download_database(os.environ['MAXMIND_LICENSE_KEY'])\""; then
        # Keep the operator credential root-only. The refresh job reads this
        # file instead of embedding the key in a cron command or process args.
        run_sh "install -m 600 /dev/null /etc/boron/maxmind-license"
        if ! $DRY_RUN; then
            printf '%s' "$MAXMIND_LICENSE_KEY" >/etc/boron/maxmind-license
        fi
        run install -m 644 "${DEST}/deploy/boron-geoip.cron" /etc/cron.d/boron-geoip
        ok "GeoLite2-Country database installed"
        ok "GeoLite2 refresh scheduled (geoipupdate package installed)"
    else
        warn "GeoLite2 download failed (bad license key, or MaxMind unreachable) -- top countries will stay hidden; retry later via POST /api/v1/admin/sitestats/geoip"
    fi
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
    if [[ -z "$MAXMIND_LICENSE_KEY" ]]; then
        read -r -s -p "Optional MaxMind GeoLite2 license key (blank skips top-countries stats): " MAXMIND_LICENSE_KEY
        printf '\n'
    fi
}

# --- uninstall ---------------------------------------------------------------

uninstall() {
    info "Uninstalling Boron"
    if ! $DRY_RUN; then
        printf '%s' "${C_YELLOW}This removes Boron's services, code, config and secrets. Hosting account data under /home, /var/vmail and MariaDB is left untouched. Continue? [y/N] ${C_RESET}"
        local reply=""
        [[ "$NONINTERACTIVE" == "true" || "$NONINTERACTIVE" == "1" ]] && reply="y"
        [[ -z "$reply" ]] && read -r reply
        [[ "$reply" =~ ^[Yy] ]] || die "aborted"
    fi
    run_sh "systemctl disable --now boron-api boron-provisiond boron-filebrowser 2>/dev/null || true"
    run_sh "rm -f /etc/systemd/system/boron-api.service /etc/systemd/system/boron-provisiond.service /etc/systemd/system/boron-filebrowser.service"
    run systemctl daemon-reload
    run_sh "rm -f /etc/cron.d/boron-monitoring /etc/cron.d/boron-cloudflare /etc/cron.d/boron-update /etc/cron.d/boron-jobs /etc/cron.d/boron-maintenance /etc/cron.d/boron-sitestats /etc/cron.d/boron-certbot-renew /etc/cron.d/boron-geoip /etc/boron/maxmind-license"
    run_sh "rm -f /etc/logrotate.d/boron-api"
    run_sh "rm -rf '${DEST}'"
    run_sh "rm -rf '${CONF_DIR}'"
    warn "kept: ${DATA_DIR} (control-plane DB), ${LOG_DIR}, /home/*, /var/vmail, MariaDB data"
    warn "to remove those too: rm -rf ${DATA_DIR} ${LOG_DIR}  (irreversible)"
    ok "Boron services + code removed"
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
Boron Panel installer

Usage: sudo bash scripts/install.sh [OPTIONS]

  --dry-run      Print every step without changing anything.
  --uninstall    Remove Boron services, code and config (keeps user data).
  -h, --help     Show this help.

Non-interactive install via env vars:
  FH_PANEL_DOMAIN, FH_LE_EMAIL, FH_ADMIN_USER, FH_ADMIN_PASSWORD,
  FH_SERVER_IP, FH_NONINTERACTIVE=1, FH_MAXMIND_LICENSE_KEY (optional,
  see README.md "GeoLite2 setup")
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
    printf '%s\n' "${C_BOLD}Boron Panel installer v${BORON_VERSION}${C_RESET}${dry_marker}"
    _logline "=== install run start (dry_run=${DRY_RUN} uninstall=${UNINSTALL}) ==="

    if $UNINSTALL; then
        uninstall
    fi

    preflight
    prompt_inputs

    apt_update
    install_base_packages
    run bash "${REPO_ROOT}/scripts/install_time_sync.sh"
    install_openlitespeed
    install_stack_packages
    setup_quota
    setup_system_users
    deploy_app
    install_node_runtimes
    install_filebrowser
    install_imapsync
    setup_config
    setup_mariadb
    setup_powerdns
    setup_ssl_bootstrap
    setup_mail_services
    install_systemd_units
    install_cron_and_logrotate
    setup_firewall
    setup_pureftpd
    setup_php_hardening
    start_services
    bootstrap_security_services
    create_admin
    setup_geoip

    summary
    if [[ "$STEP_FAIL" -eq 0 ]]; then
        info "Done. Boron Panel v${BORON_VERSION} -- https://${PANEL_DOMAIN:-${SERVER_IP:-<server-ip>}}:2222/login"
        if $DRY_RUN; then
            info "This was a dry-run -- nothing was changed."
        fi
    else
        die "${STEP_FAIL} step(s) failed -- see ${INSTALL_LOG}"
    fi
}

main "$@"

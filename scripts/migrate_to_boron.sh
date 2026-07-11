#!/usr/bin/env bash
# Migrate an existing Forgehost installation to Boron Panel naming.
#
# This is NOT run automatically by anything — it is a one-time, operator-run
# migration for a server that already has a live Forgehost install (systemd
# units, /opt, /etc, /var/lib, /var/log paths, and possibly per-account
# Redis/Node/Python app units + cgroup slices under the old naming).
#
# A fresh install never needs this script — scripts/install.sh already
# produces a Boron-named install from scratch.
#
# WHAT THIS DOES NOT TOUCH (by design, see docs/REBRAND-INVENTORY.md
# Decision 1): the MariaDB admin user `forgehost_daemon`, the read-only mail
# user `forgehost_mailro`, and the `forgehost_mail` schema are left exactly
# as they are. Renaming a live database identity is far riskier than a
# filesystem/systemd rename for zero user-visible benefit; this script does
# not attempt it.
#
# READ docs/REBRAND-MIGRATION.md BEFORE RUNNING THIS. It explains every step
# below, what to check before/after, and how to recover if a step fails
# partway through. This causes a real, brief outage of the panel (the API,
# file manager, and every account's own Redis/Node/Python apps are stopped
# and restarted) — schedule a maintenance window.
#
# Idempotent-ish: safe to re-run if it fails partway (checks before acting),
# but NOT safe to run twice successfully in a row against the same state —
# read the log output.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "must run as root" >&2
    exit 1
fi

if [ ! -d /opt/forgehost ]; then
    echo "no /opt/forgehost found -- nothing to migrate (already migrated, or this is a fresh box; use scripts/install.sh instead)" >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }

log "Boron migration starting. Repo: $REPO_DIR"
read -r -p "This stops the panel, every account's Redis/Node/Python apps, and renames system paths/users/units. Continue? [y/N] " ans
[ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "aborted"; exit 1; }

# --- 1. Stop everything, external-facing first ------------------------------

log "Stopping forgehost-api, forgehost-filebrowser"
systemctl stop forgehost-api.service forgehost-filebrowser.service 2>/dev/null || true

log "Stopping + removing every per-account Redis/Node/Python app unit (old naming)"
for unit in $(systemctl list-units --all --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^forgehost-(redis|node|python)-'); do
    echo "  stopping $unit"
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
    rm -f "/etc/systemd/system/$unit"
done

log "Stopping + removing every per-account resource-limit slice (old naming)"
for unit in $(systemctl list-units --all --type=slice --no-legend 2>/dev/null | awk '{print $1}' | grep -E '^forgehost(-.*)?\.slice$'); do
    echo "  removing $unit"
    systemctl stop "$unit" 2>/dev/null || true
    rm -f "/etc/systemd/system/$unit"
done

log "Stopping forgehost-provisiond"
systemctl stop forgehost-provisiond.service 2>/dev/null || true

systemctl daemon-reload

# --- 2. Rename the system user/group -----------------------------------------

log "Renaming system user+group forgehost-api -> boron-api"
if id forgehost-api >/dev/null 2>&1; then
    usermod -l boron-api forgehost-api
    groupmod -n boron-api forgehost-api
else
    warn "forgehost-api user not found -- skipping (already renamed?)"
fi

# --- 3. Move filesystem paths (mv preserves ownership by uid/gid, which is
#        unaffected by the usermod -l rename above -- same uid, new name) ---

move() {
    if [ -e "$1" ] && [ ! -e "$2" ]; then
        log "mv $1 -> $2"
        mv "$1" "$2"
    elif [ -e "$2" ]; then
        warn "$2 already exists -- skipping mv $1"
    else
        warn "$1 not found -- skipping"
    fi
}

move /etc/forgehost /etc/boron
move /var/log/forgehost /var/log/boron
move /var/lib/forgehost /var/lib/boron
move /var/lib/forgehost-pma-tokens /var/lib/boron-pma-tokens
move /opt/forgehost-nodejs /opt/boron-nodejs
[ -d /var/backups/forgehost ] && move /var/backups/forgehost /var/backups/boron

if [ -f /etc/boron/forgehost.toml ]; then
    log "Renaming config file forgehost.toml -> boron.toml"
    mv /etc/boron/forgehost.toml /etc/boron/boron.toml
fi
if [ -f /var/lib/boron/forgehost.db ]; then
    log "Renaming SQLite DB forgehost.db -> boron.db"
    mv /var/lib/boron/forgehost.db /var/lib/boron/boron.db
fi
for ext in db-journal db-wal db-shm; do
    [ -f "/var/lib/boron/forgehost.$ext" ] && mv "/var/lib/boron/forgehost.$ext" "/var/lib/boron/boron.$ext"
done

# Postfix relay_domains map (only relevant if any domain uses "Remote" email
# routing mode -- see daemon/handlers_email_routing.py). Content is unchanged,
# only the path/filename.
if [ -f /etc/postfix/forgehost_relay_domains ]; then
    log "Renaming Postfix relay_domains map"
    mv /etc/postfix/forgehost_relay_domains /etc/postfix/boron_relay_domains
    if grep -q "forgehost_relay_domains" /etc/postfix/main.cf 2>/dev/null; then
        sed -i 's#forgehost_relay_domains#boron_relay_domains#g' /etc/postfix/main.cf
    fi
    postmap /etc/postfix/boron_relay_domains 2>/dev/null || warn "postmap failed -- check /etc/postfix/boron_relay_domains manually"
    systemctl reload postfix 2>/dev/null || warn "postfix reload failed -- check manually"
fi

# --- 4. Move the deployed application code (/opt/forgehost -> /opt/boron),
#        THEN sync the new Boron-branded source over it via deploy.sh ------

move /opt/forgehost /opt/boron

log "Deploying Boron-branded source from $REPO_DIR to /opt/boron"
bash "$REPO_DIR/scripts/deploy.sh"

# --- 5. Install the renamed systemd/cron/logrotate units --------------------

log "Installing boron-*.service / .cron / .logrotate"
install -m 644 "$REPO_DIR/deploy/boron-api.service" /etc/systemd/system/
install -m 644 "$REPO_DIR/deploy/boron-provisiond.service" /etc/systemd/system/
install -m 644 "$REPO_DIR/deploy/boron-filebrowser.service" /etc/systemd/system/
install -m 644 "$REPO_DIR/deploy/boron-api.logrotate" /etc/logrotate.d/boron-api
for cron in boron-cloudflare boron-maintenance boron-monitoring boron-sitestats boron-update; do
    [ -f "$REPO_DIR/deploy/$cron.cron" ] && install -m 644 "$REPO_DIR/deploy/$cron.cron" "/etc/cron.d/$cron"
done

log "Removing leftover old-named unit/cron/logrotate files"
rm -f /etc/systemd/system/forgehost-api.service /etc/systemd/system/forgehost-provisiond.service /etc/systemd/system/forgehost-filebrowser.service
rm -f /etc/logrotate.d/forgehost-api
rm -f /etc/cron.d/forgehost-cloudflare /etc/cron.d/forgehost-maintenance /etc/cron.d/forgehost-monitoring /etc/cron.d/forgehost-sitestats /etc/cron.d/forgehost-update

systemctl daemon-reload

# --- 6. Start everything back up. borond's own startup bootstraps
#        (cgroups.bootstrap_all_slices, redisacct.bootstrap_all_redis,
#        nodeapps.bootstrap_all_node_apps, pythonapps.bootstrap_all_python_apps)
#        recreate every account's slice + Redis/Node/Python units under the
#        NEW boron- naming automatically from DB state -- that is why step 1
#        only needed to STOP and REMOVE the old units, not hand-build new
#        ones. -----------------------------------------------------------

log "Enabling + starting boron-provisiond (this bootstraps per-account units)"
systemctl enable --now boron-provisiond.service
sleep 3
systemctl status boron-provisiond.service --no-pager -l | head -20

log "Enabling + starting boron-filebrowser, boron-api"
systemctl enable --now boron-filebrowser.service
systemctl enable --now boron-api.service

# --- 7. Verify ---------------------------------------------------------------

log "Verifying"
sleep 2
systemctl is-active boron-api boron-provisiond boron-filebrowser
echo "--- per-account units after bootstrap ---"
systemctl list-units --all --type=service --type=slice --no-legend 2>/dev/null | grep -i boron || true
echo "--- confirm no old forgehost-named live units remain ---"
systemctl list-units --all --no-legend 2>/dev/null | grep -i forgehost || echo "  (none found -- clean)"
echo "--- healthz ---"
curl -sk -o /dev/null -w 'HTTP %{http_code}\n' --max-time 5 https://127.0.0.1:9443/healthz || warn "healthz check failed"

log "Migration script finished. Read the verification checklist in docs/REBRAND-MIGRATION.md before considering this done -- in particular, confirm each account's Redis/Node/Python app actually came back up correctly, not just that the unit exists."

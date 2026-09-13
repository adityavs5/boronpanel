#!/usr/bin/env bash
# Ubuntu 24 time synchronization. Keep operator-defined sources intact.
set -euo pipefail
if ! command -v chronyd >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y chrony
fi
install -d -m 0755 /etc/chrony/conf.d
cat > /etc/chrony/conf.d/boron.conf <<'CONF'
# Additional authenticated source, alongside Ubuntu's configured pools.
server time.cloudflare.com iburst nts maxpoll 8
CONF
chmod 0644 /etc/chrony/conf.d/boron.conf
install -d -m 0755 /etc/systemd/system/chrony.service.d
cat > /etc/systemd/system/chrony.service.d/boron-restart.conf <<'CONF'
[Unit]
StartLimitIntervalSec=300
StartLimitBurst=5
[Service]
Restart=on-failure
RestartSec=5s
CONF
chronyd -p -f /etc/chrony/chrony.conf >/dev/null
systemctl daemon-reload
systemctl enable chrony
systemctl restart chrony

#!/usr/bin/env bash
# Install or upgrade the license server on the distribution host. Idempotent.
# Run as root from the unpacked licensing/ directory:  bash deploy/install_server.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

src="$(cd "$(dirname "$0")/.." && pwd)"
base=/opt/boron-license
etc=/etc/boron-license
user=boronlic

apt-get -qq install -y postgresql python3.12-venv >/dev/null
id "$user" >/dev/null 2>&1 || useradd --system --home-dir /var/lib/boron-license --create-home \
    --shell /usr/sbin/nologin "$user"

# Code: replace atomically, keep the venv across upgrades.
install -d -m 0755 "$base"
rm -rf "$base/app.new"
install -d -m 0755 "$base/app.new"
cp -r "$src/boron_license" "$src/requirements.txt" "$base/app.new/"
chown -R root:root "$base/app.new"
chmod -R u=rwX,go=rX "$base/app.new"
rm -rf "$base/app.old"
[[ -d "$base/app" ]] && mv "$base/app" "$base/app.old"
mv "$base/app.new" "$base/app"
[[ -x "$base/.venv/bin/python" ]] || python3.12 -m venv "$base/.venv"
"$base/.venv/bin/pip" install -q --disable-pip-version-check -r "$base/app/requirements.txt"

# Database: local socket only, peer authentication as the service user.
systemctl enable --now postgresql >/dev/null 2>&1
runuser -u postgres -- psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname='$user'" | grep -q 1 \
    || runuser -u postgres -- psql -qc "CREATE ROLE $user LOGIN"
runuser -u postgres -- psql -qtAc "SELECT 1 FROM pg_database WHERE datname='boron_license'" | grep -q 1 \
    || runuser -u postgres -- psql -qc "CREATE DATABASE boron_license OWNER $user"

# Configuration and signing key: readable only by the service user.
install -d -m 0750 -o root -g "$user" "$etc"
if [[ ! -f "$etc/config.env" ]]; then
    cat > "$etc/config.env" <<'EOF'
BORON_LICENSE_DATABASE_URL=postgresql+psycopg://boronlic@/boron_license?host=/var/run/postgresql
BORON_LICENSE_SIGNING_KEY=/etc/boron-license/keys/license-signing.key
BORON_LICENSE_GRACE_DAYS=14
EOF
    chmod 0640 "$etc/config.env"
    chown root:"$user" "$etc/config.env"
fi
install -d -m 0700 -o "$user" -g "$user" "$etc/keys"

cat > /usr/local/bin/boron-license <<EOF
#!/usr/bin/env bash
# Admin CLI for the Boron license server; runs as the service user.
set -euo pipefail
[[ "\$EUID" -eq 0 ]] || { echo 'run as root' >&2; exit 1; }
set -a; . $etc/config.env; set +a
export SUDO_USER="\${SUDO_USER:-\$(id -un)}" PYTHONPATH=$base/app PYTHONDONTWRITEBYTECODE=1
cd /
exec runuser -u $user -- $base/.venv/bin/python -m boron_license.cli "\$@"
EOF
chmod 0755 /usr/local/bin/boron-license
[[ -f "$etc/keys/license-signing.key" ]] || boron-license gen-signing-key >/dev/null
boron-license init-db >/dev/null

# Nightly database dump, 14 kept. Copy these off the server as well.
install -d -m 0700 -o "$user" -g "$user" /var/backups/boron-license
cat > /etc/cron.d/boron-license-backup <<EOF
17 3 * * * $user pg_dump -Fc boron_license > /var/backups/boron-license/boron_license-\$(date +\%F).dump && find /var/backups/boron-license -name '*.dump' -mtime +14 -delete
EOF

install -m 0644 "$src/deploy/boron-license.service" /etc/systemd/system/boron-license.service
systemctl daemon-reload
systemctl enable boron-license >/dev/null 2>&1
systemctl restart boron-license
for _ in $(seq 1 20); do
    curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1 && break
    sleep 0.5
done
curl -fsS http://127.0.0.1:8100/health
echo

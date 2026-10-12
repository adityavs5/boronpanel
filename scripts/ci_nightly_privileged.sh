#!/usr/bin/env bash
# Destructive to the host by design. Run only on a freshly provisioned,
# disposable Ubuntu 24.04 runner carrying the boron-privileged-ephemeral label.
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || { printf 'This job requires root.\n' >&2; exit 2; }
. /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 24.04 ]] || {
  printf 'Expected disposable Ubuntu 24.04, found %s %s.\n' "${ID:-unknown}" "${VERSION_ID:-unknown}" >&2
  exit 2
}

repo_root="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
admin_password="$(PYTHONPATH="$repo_root" python3 -c 'from shared.validation import generate_strong_password; print(generate_strong_password(30))')"
export FH_NONINTERACTIVE=1
export FH_ADMIN_USER=ciadmin
export FH_ADMIN_PASSWORD="$admin_password"
export FH_LE_EMAIL=ci@example.test
export FH_SERVER_IP="$(hostname -I | awk '{print $1}')"
export FH_PANEL_DOMAIN=""
export FH_WEBMAIL_DOMAIN=""
export FH_INSTALL_PROFILE="${FH_INSTALL_PROFILE:-full}"

bash "$repo_root/scripts/install.sh" --profile "$FH_INSTALL_PROFILE"

systemctl is-active --quiet boron-provisiond boron-api lshttpd
if [[ "$FH_INSTALL_PROFILE" != minimal ]]; then
  systemctl is-active --quiet mariadb postfix dovecot pdns fail2ban
fi
if [[ "$FH_INSTALL_PROFILE" == full ]]; then
  systemctl is-active --quiet redis-server
fi
curl --fail --silent --insecure https://127.0.0.1:2222/healthz >/dev/null
ss -lntup | grep -Eq ':(80|443|2222)[[:space:]]'
findmnt -n -o FSTYPE /sys/fs/cgroup | grep -qx cgroup2
test -S /run/boron/provisiond.sock
test -r /var/lib/boron/boron.db

# A second install with the same profile must complete without rotating
# secrets or replacing operator configuration. Package managers and service
# enable calls are allowed to report that the desired state already exists.
before_config="$(find /etc/boron -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
bash "$repo_root/scripts/install.sh" --profile "$FH_INSTALL_PROFILE"
after_config="$(find /etc/boron -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum)"
[[ "$before_config" == "$after_config" ]] || {
  printf 'Idempotent rerun changed /etc/boron unexpectedly.\n' >&2
  exit 1
}

# The full suite runs against the installed environment after all services are
# active. The P5 upgrade fixture will extend this job with N-1 upgrade/rollback.
cd /opt/boron
if [[ "$FH_INSTALL_PROFILE" == full ]]; then
  test_root="${BORON_TEST_TMPDIR:-/srv/bt}"
  install -d -o root -g root -m 0755 "$test_root"
  test_device="$(findmnt -n -o SOURCE -T "$test_root")"
  panel_device="$(findmnt -n -o SOURCE -T /var/lib/boron)"
  if [[ "$test_device" == "$panel_device" ]]; then
    printf 'Full privileged tests require BORON_TEST_TMPDIR on a separate mounted volume (test=%s, panel=%s).\n' \
      "$test_device" "$panel_device" >&2
    exit 2
  fi
  workspace_marker="$test_root/.boron-test-workspace"
  if [[ ! -e "$workspace_marker" ]]; then
    unexpected_entry="$(find "$test_root" -mindepth 1 -maxdepth 1 \
      ! -name lost+found ! -name .boron-test-workspace -print -quit)"
    if [[ -n "$unexpected_entry" ]]; then
      printf 'Refusing to mark non-empty test volume %s; unexpected entry: %s\n' \
        "$test_root" "$unexpected_entry" >&2
      exit 2
    fi
    printf 'Boron test data only. Contents may be removed by the test harness.\n' > "$workspace_marker"
    chmod 0644 "$workspace_marker"
  fi
  export BORON_TEST_TMPDIR="$test_root"
  export BORON_TEST_TMP_MAX_GB="${BORON_TEST_TMP_MAX_GB:-8}"
  export BORON_TEST_MIN_FREE_GB="${BORON_TEST_MIN_FREE_GB:-5}"
  export BORON_TEST_KEEP_RUNS="${BORON_TEST_KEEP_RUNS:-3}"
  export BORON_TEST_REPORT_DIR="${BORON_TEST_REPORT_DIR:-$repo_root/test-artifacts/nightly-$FH_INSTALL_PROFILE}"
  /opt/boron/.venv/bin/python -m pip install --disable-pip-version-check \
    -r "$repo_root/requirements-dev.txt"
  PYTHON=/opt/boron/.venv/bin/python BORON_TEST_TIMEOUT=90m scripts/ci_backend.sh
fi

# Control-plane disaster-recovery rehearsal: export the installed panel,
# authenticate/validate it, then restore it over the same disposable host and
# prove both control-plane services and the public health endpoint recover.
recovery_dir="$(mktemp -d /root/boron-recovery-ci.XXXXXX)"
trap 'rm -rf "$recovery_dir"' EXIT
openssl rand -base64 48 > "$recovery_dir/key"
chmod 0600 "$recovery_dir/key"
./.venv/bin/python scripts/control_plane_recovery.py export \
  --output "$recovery_dir/control-plane.boroncp" \
  --recovery-key-file "$recovery_dir/key" \
  --panel-version "$(./.venv/bin/python -c 'from version import BORON_VERSION; print(BORON_VERSION)')"
./.venv/bin/python scripts/control_plane_recovery.py restore \
  --archive "$recovery_dir/control-plane.boroncp" \
  --recovery-key-file "$recovery_dir/key" \
  --dry-run
systemctl stop boron-api boron-provisiond
./.venv/bin/python scripts/control_plane_recovery.py restore \
  --archive "$recovery_dir/control-plane.boroncp" \
  --recovery-key-file "$recovery_dir/key" \
  --overwrite-live
systemctl start boron-provisiond boron-api
for _attempt in $(seq 1 30); do
  curl --fail --silent --insecure https://127.0.0.1:2222/healthz >/dev/null && break
  sleep 2
done
curl --fail --silent --insecure https://127.0.0.1:2222/healthz >/dev/null
systemctl is-active --quiet boron-provisiond boron-api

printf 'Privileged install, service, listener, cgroup, backend and control-plane recovery checks passed.\n'

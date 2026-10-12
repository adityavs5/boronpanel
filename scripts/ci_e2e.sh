#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ci_root="$(mktemp -d)"
rpc_pid=""
api_pid=""

cleanup() {
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  [[ -z "$rpc_pid" ]] || kill "$rpc_pid" 2>/dev/null || true
  rm -rf "$ci_root"
}
trap cleanup EXIT INT TERM

: "${BORON_CI_ADMIN_PASSWORD:?BORON_CI_ADMIN_PASSWORD is required}"
: "${BORON_CI_CUSTOMER_PASSWORD:?BORON_CI_CUSTOMER_PASSWORD is required}"

session_secret="$(openssl rand -hex 32)"
app_env_key="$(python3 - <<'PY'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)"

cat >"$ci_root/boron.toml" <<EOF
db_path = "$ci_root/boron.db"
rpc_socket = "$ci_root/provisiond.sock"
log_dir = "$ci_root/log"
home_base = "$ci_root/home"
backup_dir = "$ci_root/backups"
snapshot_private_dir = "$ci_root/snapshot-private"
snapshot_download_dir = "$ci_root/downloads"
branding_dir = "$ci_root/branding"
webmail_url = ""
EOF
printf 'SESSION_SECRET=%s\nAPP_ENV_KEY=%s\n' "$session_secret" "$app_env_key" >"$ci_root/secrets.env"
printf 'SESSION_SECRET=%s\n' "$session_secret" >"$ci_root/api-secrets.env"
chmod 600 "$ci_root/secrets.env" "$ci_root/api-secrets.env"
mkdir -p "$ci_root/log" "$ci_root/home/ciaccount/public_html"

export BORON_CONFIG="$ci_root/boron.toml"
export BORON_SECRETS="$ci_root/secrets.env"
export BORON_API_SECRETS="$ci_root/api-secrets.env"
export BORON_CI_BASE_URL="https://127.0.0.1:9443"
export BORON_CI_ADMIN_USERNAME="ciadmin"
export BORON_CI_ACCOUNT_USERNAME="ciaccount"

cd "$repo_root"
python3 scripts/ci_seed_panel.py --database "$ci_root/boron.db"
python3 scripts/ci_rpc_server.py >"$ci_root/rpc.log" 2>&1 &
rpc_pid=$!

for _ in {1..100}; do
  [[ -S "$ci_root/provisiond.sock" ]] && break
  kill -0 "$rpc_pid" 2>/dev/null || { cat "$ci_root/rpc.log" >&2; exit 1; }
  sleep 0.1
done
[[ -S "$ci_root/provisiond.sock" ]] || { printf 'CI RPC socket did not start.\n' >&2; exit 1; }

openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj '/CN=127.0.0.1' -keyout "$ci_root/key.pem" -out "$ci_root/cert.pem" \
  >/dev/null 2>&1
python3 -m uvicorn api.main:app --host 127.0.0.1 --port 9443 \
  --ssl-keyfile "$ci_root/key.pem" --ssl-certfile "$ci_root/cert.pem" \
  >"$ci_root/api.log" 2>&1 &
api_pid=$!

for _ in {1..150}; do
  curl --fail --silent --insecure "$BORON_CI_BASE_URL/healthz" >/dev/null 2>&1 && break
  kill -0 "$api_pid" 2>/dev/null || { cat "$ci_root/api.log" >&2; exit 1; }
  sleep 0.1
done
curl --fail --silent --insecure "$BORON_CI_BASE_URL/healthz" >/dev/null || {
  printf 'CI API did not become healthy.\n' >&2
  cat "$ci_root/api.log" >&2
  exit 1
}

cd "$repo_root/frontend"
npx playwright test --config playwright.ci-smoke.config.js

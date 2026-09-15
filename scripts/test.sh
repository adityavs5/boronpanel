#!/usr/bin/env bash
# Fast, explicit test tiers for development. The release script deliberately
# keeps its own mandatory full-suite invocation so a release cannot
# accidentally inherit a developer shortcut.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
    printf 'Missing test environment: %s\n' "$PY" >&2
    exit 2
fi

usage() {
    cat <<'EOF'
Usage: scripts/test.sh MODE [pytest options]

  targeted TEST...  Run the named test files/nodes (fastest while editing)
  failed            Re-run only failures from the previous cached run
  update            Run the updater/recovery safety suite (~2 minutes)
  quick             Broad regression without long snapshot integration tests
  full              Run every Python test (required before a release)

Examples:
  scripts/test.sh targeted tests/test_wordpress.py -k clone
  scripts/test.sh failed
  scripts/test.sh quick
EOF
}

mode="${1:-}"
[[ -n "$mode" ]] || { usage; exit 2; }
shift

cd "$REPO_ROOT"
common=(-q --tb=short)
update_tests=(
    tests/test_update_version.py
    tests/test_update_api.py
    tests/test_updates.py
    tests/test_update_finalizer.py
    tests/test_db_schema_transaction.py
    tests/test_configtx.py
    tests/test_rpc.py
    tests/test_snapshot_mail_guard_config.py
)
quick_tests=(
    tests/test_validation.py
    tests/test_api_security.py
    tests/test_ratelimit.py
    tests/test_appcrypto.py
    tests/test_procutil.py
    tests/test_safeio.py
    tests/test_handlers_auth.py
    tests/test_cross_account_authorization.py
    tests/test_accounts_api.py
    tests/test_health.py
    tests/test_panel_config.py
    "${update_tests[@]}"
    tests/test_wordpress.py
    tests/test_wordpress_migration.py
    tests/test_wpmanager.py
    tests/test_wpcli.py
    tests/test_wp_install_helper.py
    tests/test_malware.py
    tests/test_malware_api.py
    tests/test_firewall.py
    tests/test_ssl.py
    tests/test_dns_operations.py
    tests/test_database_operations.py
    tests/test_backup.py
    tests/test_cgroups.py
)

case "$mode" in
    targeted)
        (($# > 0)) || { usage; exit 2; }
        exec "$PY" -m pytest "${common[@]}" "$@"
        ;;
    failed)
        exec "$PY" -m pytest "${common[@]}" --lf "$@"
        ;;
    update)
        exec "$PY" -m pytest "${common[@]}" "${update_tests[@]}" "$@"
        ;;
    quick)
        exec "$PY" -m pytest "${common[@]}" "${quick_tests[@]}" "$@"
        ;;
    full)
        exec "$PY" -m pytest "${common[@]}" "$@"
        ;;
    -h|--help|help)
        usage
        ;;
    *)
        printf 'Unknown test mode: %s\n\n' "$mode" >&2
        usage >&2
        exit 2
        ;;
esac

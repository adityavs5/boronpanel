"""A new unauthenticated route must be deliberately reviewed."""
from scripts.security_inventory import inventory_rows


EXPECTED_PUBLIC_ROUTES = {
    "GET /",
    "GET /app",
    "GET /app/{spa_path:path}",
    "GET /healthz",
    "GET /login",
    "POST /login",
    "POST /login/2fa",
    "GET /api/v1/branding",
    "GET /api/v1/branding/logo",
    "GET /api/v1/branding/favicon",
}


def test_all_routes_without_auth_dependency_are_reviewed_public_routes():
    actual = {
        row["name"] for row in inventory_rows()
        if row["kind"] == "route" and not row["auth_dependencies"]
    }
    assert actual == EXPECTED_PUBLIC_ROUTES


def test_inventory_includes_non_http_root_entry_points():
    rows = inventory_rows()
    entries = {(row["kind"], row["name"]) for row in rows}
    for expected in (
        ("service", "boron-provisiond.service"),
        ("service", "boron-api.service"),
        ("cron", "boron-jobs.cron:5"),
        ("shell-script", "install.sh"),
        ("shell-script", "release.sh"),
        ("python-script", "update_finalize.py"),
        ("python-script", "reconcile_litespeed_repo.py"),
        ("native-helper", "mail_restore_gate.c"),
        ("dynamic-service", "per-account Node.js unit"),
        ("dynamic-service", "per-account Python unit"),
        ("dynamic-service", "per-account Redis unit"),
    ):
        assert expected in entries

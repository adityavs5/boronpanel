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

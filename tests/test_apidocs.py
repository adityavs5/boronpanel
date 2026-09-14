from __future__ import annotations

import warnings

from fastapi.testclient import TestClient

import api.main as main
from api.security import Identity, get_identity

warnings.filterwarnings("ignore")

ADMIN = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
CUSTOMER = Identity(panel_user_id=2, username="cust1", role="customer", account_id=1, auth_method="session")


def _as(identity):
    main.app.dependency_overrides[get_identity] = lambda: identity


def _clear():
    main.app.dependency_overrides.pop(get_identity, None)


# --- openapi schema endpoint -------------------------------------------------


def test_openapi_schema_requires_admin(isolated_db):
    client = TestClient(main.app)
    # Unauthenticated: the built-in schema URL is disabled and the guarded one
    # requires a session.
    assert client.get("/api/openapi.json").status_code == 401
    _as(CUSTOMER)
    try:
        assert client.get("/api/openapi.json").status_code == 403
    finally:
        _clear()


def test_builtin_schema_url_is_disabled(isolated_db):
    """FastAPI's default /openapi.json must NOT be served -- only the
    admin-guarded /api/openapi.json exposes the schema."""
    client = TestClient(main.app)
    assert client.get("/openapi.json").status_code == 404


def test_openapi_schema_served_to_admin(isolated_db):
    _as(ADMIN)
    try:
        client = TestClient(main.app)
        r = client.get("/api/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["info"]["title"] == "Boron"
        # A representative spread of endpoints is present ("all endpoints
        # visible").
        paths = schema["paths"]
        assert "/login" in paths
        assert "/api/v1/admin/plans" in paths
        assert "/api/v1/admin/monitoring/settings" in paths
        assert "/api/v1/admin/logs/errors" in paths
        assert "/api/v1/admin/malware/status" in paths
        assert len(paths) > 150
    finally:
        _clear()


# --- swagger UI + redoc ------------------------------------------------------


def test_swagger_ui_requires_admin(isolated_db):
    client = TestClient(main.app)
    assert client.get("/api/docs").status_code == 401
    _as(CUSTOMER)
    try:
        assert client.get("/api/docs").status_code == 403
    finally:
        _clear()


def test_swagger_ui_served_to_admin_with_local_assets(isolated_db):
    _as(ADMIN)
    try:
        client = TestClient(main.app)
        r = client.get("/api/docs")
        assert r.status_code == 200
        body = r.text
        # Vendored, same-origin assets -- never a CDN.
        assert "/static/apidocs/swagger-ui-bundle.js" in body
        assert "cdn.jsdelivr.net" not in body
        # The docs page carries the relaxed, docs-scoped CSP (self + inline),
        # not the site-wide script-src 'none'.
        csp = r.headers["content-security-policy"]
        assert "script-src 'self' 'unsafe-inline'" in csp
        # The site-wide script-src 'none' must NOT apply here (frame-ancestors
        # 'none' is a different, intended directive and is allowed).
        assert "script-src 'none'" not in csp
    finally:
        _clear()


def test_redoc_served_to_admin_offline(isolated_db):
    _as(ADMIN)
    try:
        client = TestClient(main.app)
        r = client.get("/api/redoc")
        assert r.status_code == 200
        body = r.text
        assert "/static/apidocs/redoc.standalone.js" in body
        assert "cdn.jsdelivr.net" not in body
        assert "fonts.googleapis.com" not in body  # with_google_fonts=False
    finally:
        _clear()

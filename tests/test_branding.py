from __future__ import annotations

import base64
import warnings

import pytest

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

import api.main as main
from daemon import branding
from api.security import Identity, get_identity

PNG_BYTES = branding._PNG_MAGIC + b"fake-png-payload"
ICO_BYTES = branding._ICO_MAGIC + b"fake-ico-payload"
SVG_BYTES = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
SVG_WITH_SCRIPT = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- daemon layer: settings -------------------------------------------------


def test_get_settings_defaults_when_no_row(isolated_db):
    data = branding.get_settings({})
    assert data["panel_name"] == "Forgehost"
    assert data["has_logo"] is False
    assert data["has_favicon"] is False
    assert data["support_email"] is None
    assert data["support_url"] is None


def test_set_settings_partial_update(isolated_db):
    branding.set_settings({"panel_name": "Acme Hosting"})
    data = branding.get_settings({})
    assert data["panel_name"] == "Acme Hosting"
    assert data["support_email"] is None  # untouched

    branding.set_settings({"support_email": "help@acme.example"})
    data = branding.get_settings({})
    assert data["panel_name"] == "Acme Hosting"  # still untouched
    assert data["support_email"] == "help@acme.example"


def test_set_settings_rejects_empty_panel_name(isolated_db):
    with pytest.raises(Exception):
        branding.set_settings({"panel_name": "   "})


def test_set_settings_rejects_too_long_panel_name(isolated_db):
    with pytest.raises(Exception):
        branding.set_settings({"panel_name": "x" * 65})


def test_set_settings_rejects_bad_email(isolated_db):
    with pytest.raises(Exception):
        branding.set_settings({"support_email": "not-an-email"})


def test_set_settings_rejects_bad_url(isolated_db):
    with pytest.raises(Exception):
        branding.set_settings({"support_url": "not a url"})


def test_set_settings_clears_field_with_empty_string(isolated_db):
    branding.set_settings({"support_email": "help@acme.example"})
    branding.set_settings({"support_email": ""})
    assert branding.get_settings({})["support_email"] is None


# --- daemon layer: logo/favicon upload --------------------------------------


def test_upload_logo_accepts_png(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    result = branding.upload_logo({"image_base64": _b64(PNG_BYTES)})
    assert result["has_logo"] is True
    assert (tmp_path / "logo.png").read_bytes() == PNG_BYTES


def test_upload_logo_accepts_svg(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    result = branding.upload_logo({"image_base64": _b64(SVG_BYTES)})
    assert result["has_logo"] is True
    assert (tmp_path / "logo.svg").exists()


def test_upload_logo_rejects_svg_with_script(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(SVG_WITH_SCRIPT)})
    assert not list(tmp_path.glob("logo.*"))


def test_upload_logo_rejects_ico(isolated_db, tmp_path, monkeypatch):
    """ICO is accepted for favicons only, not logos."""
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(ICO_BYTES)})


def test_upload_favicon_accepts_ico(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    result = branding.upload_favicon({"image_base64": _b64(ICO_BYTES)})
    assert result["has_favicon"] is True
    assert (tmp_path / "favicon.ico").exists()


def test_upload_rejects_unrecognized_bytes(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(b"not an image at all")})


def test_upload_rejects_bad_base64(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": "!!!not-base64!!!"})


def test_upload_rejects_oversized_file(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    monkeypatch.setattr(branding.settings, "branding_max_upload_bytes", 10)
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(PNG_BYTES)})


def test_reupload_with_different_extension_removes_old_file(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    branding.upload_logo({"image_base64": _b64(PNG_BYTES)})
    assert (tmp_path / "logo.png").exists()
    branding.upload_logo({"image_base64": _b64(SVG_BYTES)})
    assert not (tmp_path / "logo.png").exists()
    assert (tmp_path / "logo.svg").exists()


def test_remove_logo_clears_row_and_deletes_file(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    branding.upload_logo({"image_base64": _b64(PNG_BYTES)})
    branding.remove_logo({})
    assert branding.get_settings({})["has_logo"] is False
    assert not list(tmp_path.glob("logo.*"))


def test_remove_logo_idempotent_when_never_set(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    branding.remove_logo({})  # must not raise
    assert branding.get_settings({})["has_logo"] is False


# --- API layer: public reads vs admin-only writes ---------------------------


def test_get_branding_is_public_no_auth_required(isolated_db):
    client = TestClient(main.app)
    r = client.get("/api/v1/branding")
    assert r.status_code == 200
    assert r.json()["panel_name"] == "Forgehost"


def test_get_logo_404_when_unset(isolated_db):
    client = TestClient(main.app)
    r = client.get("/api/v1/branding/logo")
    assert r.status_code == 404


def test_logo_response_has_restrictive_csp(isolated_db, tmp_path, monkeypatch):
    """Defense-in-depth layer 1 against SVG-embedded XSS (see
    api/routers/branding.py's `_serve_asset` docstring): confirmed
    empirically that the response actually carries `script-src 'none'`,
    not assumed from reading the middleware."""
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    branding.upload_logo({"image_base64": _b64(SVG_BYTES)})
    client = TestClient(main.app)
    r = client.get("/api/v1/branding/logo")
    assert r.status_code == 200
    assert "script-src 'none'" in r.headers["content-security-policy"]


def test_patch_branding_requires_admin(isolated_db):
    customer = Identity(panel_user_id=2, username="cust1", role="customer", account_id=1, auth_method="session")
    main.app.dependency_overrides[get_identity] = lambda: customer
    try:
        client = TestClient(main.app)
        r = client.patch("/api/v1/admin/branding", json={"panel_name": "Hijacked"})
        assert r.status_code == 403
    finally:
        main.app.dependency_overrides.pop(get_identity, None)


def test_patch_branding_unauthenticated_rejected(isolated_db):
    client = TestClient(main.app)
    r = client.patch("/api/v1/admin/branding", json={"panel_name": "Nope"})
    assert r.status_code == 401


def test_patch_branding_as_admin_succeeds(isolated_db, monkeypatch):
    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    main.app.dependency_overrides[get_identity] = lambda: admin
    from api.routers import branding as branding_router

    monkeypatch.setattr(branding_router, "call_daemon", lambda op, identity, **p: branding.set_settings(p))
    try:
        client = TestClient(main.app)
        r = client.patch("/api/v1/admin/branding", json={"panel_name": "Acme"})
        assert r.status_code == 200
        assert r.json()["panel_name"] == "Acme"
    finally:
        main.app.dependency_overrides.pop(get_identity, None)

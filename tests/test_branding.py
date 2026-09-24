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


@pytest.fixture(autouse=True)
def _avoid_host_ownership_changes(monkeypatch):
    # Upload tests use tmp_path; changing its real group is unnecessary and
    # may be disallowed in a restricted test sandbox.
    monkeypatch.setattr(branding, "_grant_api_group_read", lambda _path: None)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- daemon layer: settings -------------------------------------------------


def test_get_settings_defaults_when_no_row(isolated_db):
    data = branding.get_settings({})
    assert data["panel_name"] == "Boron"
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


def test_upload_logo_rejects_svg_with_entity_obfuscated_javascript_uri(isolated_db, tmp_path, monkeypatch):
    # Audit 3 (Area 12): a numeric character reference decodes to a real
    # `javascript:` URI in a browser even though the raw bytes never
    # contain that literal substring -- confirmed live as a working bypass
    # of the plain substring filter before this fix.
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<a xlink:href="&#106;avascript:alert(1)"><rect/></a></svg>'
    )
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(svg)})
    assert not list(tmp_path.glob("logo.*"))


def test_upload_logo_rejects_svg_with_foreignobject_script_smuggling(isolated_db, tmp_path, monkeypatch):
    # Audit 3 (Area 12): foreignObject can embed a nested (X)HTML document
    # (e.g. an iframe srcdoc) whose entity-encoded content decodes to a
    # real <script> tag the browser parses and executes -- confirmed live
    # as a working bypass before this fix.
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
        b'<iframe xmlns="http://www.w3.org/1999/xhtml" '
        b'srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"/>'
        b'</foreignObject></svg>'
    )
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(svg)})
    assert not list(tmp_path.glob("logo.*"))


def test_upload_logo_accepts_plain_svg_with_harmless_entities(isolated_db, tmp_path, monkeypatch):
    # The entity-decode pass must not produce false positives on ordinary
    # text content (e.g. an escaped ampersand in a <title>).
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><title>Acme &amp; Co</title><rect/></svg>'
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    result = branding.upload_logo({"image_base64": _b64(svg)})
    assert result["has_logo"] is True


@pytest.mark.parametrize("svg", [
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="https://example.org"><rect/></a></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://example.org/x)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:text/html,attack"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://example.org/x)"/></svg>',
    b'<!DOCTYPE svg [<!ENTITY x "attack">]><svg xmlns="http://www.w3.org/2000/svg"><title>&x;</title></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><g xmlns="http://www.w3.org/1999/xhtml"><script/></g></svg>',
])
def test_upload_logo_rejects_non_drawing_svg(isolated_db, tmp_path, monkeypatch, svg):
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    with pytest.raises(Exception):
        branding.upload_logo({"image_base64": _b64(svg)})
    assert not list(tmp_path.glob("logo.*"))


def test_upload_logo_serializes_safe_svg(isolated_db, tmp_path, monkeypatch):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><title>Acme &amp; Co</title><rect fill="#123456" width="20" height="10"/></svg>'
    monkeypatch.setattr(branding.settings, "branding_dir", str(tmp_path))
    branding.upload_logo({"image_base64": _b64(svg)})
    stored = (tmp_path / "logo.svg").read_bytes()
    assert b"Acme &amp; Co" in stored
    assert b"<rect" in stored
    assert b"<!" not in stored


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
    assert r.json()["panel_name"] == "Boron"


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


def test_terminal_banner_persists_as_literal_text_and_resets(isolated_db):
    from shared.terminal_welcome import DEFAULT_TERMINAL_BANNER,render_terminal_banner
    value='BORON\n$(touch /tmp/never-execute-this)'
    assert branding.set_settings({'terminal_banner':value})['terminal_banner']==value
    assert render_terminal_banner(value)=='BORON\r\n$(touch /tmp/never-execute-this)\r\n\r\n'
    assert branding.set_settings({'terminal_banner':None})['terminal_banner'] is None
    assert DEFAULT_TERMINAL_BANNER in render_terminal_banner().replace('\r','')
    assert render_terminal_banner('')==''


@pytest.mark.parametrize('value',['\x1b]52;c;clipboard\x07','hello\rworld','\x00','é','x'*4001,'\n'*31])
def test_terminal_banner_rejects_controls_and_oversized_text(isolated_db,value):
    from shared.validation import ValidationError
    with pytest.raises(ValidationError):branding.set_settings({'terminal_banner':value})


def test_public_branding_does_not_disclose_terminal_banner(isolated_db):
    branding.set_settings({'terminal_banner':'Private admin welcome'})
    with TestClient(main.app) as client:
        assert 'terminal_banner' not in client.get('/api/v1/branding').json()


def test_terminal_banner_additive_migration_preserves_branding(tmp_path):
    from sqlalchemy import create_engine,text,inspect
    from shared.db import _apply_additive_migrations
    engine=create_engine('sqlite:///'+str(tmp_path/'old-branding.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE branding_settings (id INTEGER PRIMARY KEY, panel_name VARCHAR(64))'))
        connection.execute(text("INSERT INTO branding_settings VALUES (1,'Existing brand')"))
    _apply_additive_migrations(engine)
    _apply_additive_migrations(engine)
    assert 'terminal_banner' in {col['name'] for col in inspect(engine).get_columns('branding_settings')}
    with engine.connect() as connection:
        assert tuple(connection.execute(text('SELECT panel_name,terminal_banner FROM branding_settings')).one())==('Existing brand',None)
    engine.dispose()


def test_customer_cannot_read_admin_terminal_branding(isolated_db):
    customer=Identity(panel_user_id=2,username='cust1',role='customer',account_id=1,auth_method='session')
    main.app.dependency_overrides[get_identity]=lambda:customer
    try:
        with TestClient(main.app) as client:
            assert client.get('/api/v1/admin/branding').status_code==403
    finally:main.app.dependency_overrides.clear()


def test_api_upload_reads_only_bounded_prefix(monkeypatch):
    import asyncio
    from api.routers import branding as routes
    from fastapi import HTTPException
    monkeypatch.setattr(routes.settings, 'branding_max_upload_bytes', 8)
    class OversizedUpload:
        async def read(self, size=-1):
            assert size == 9
            return b'x' * size
    with pytest.raises(HTTPException) as exc:
        asyncio.run(routes._upload('branding.logo.upload', OversizedUpload(), Identity(1,'admin','admin',None,'session')))
    assert exc.value.status_code == 413

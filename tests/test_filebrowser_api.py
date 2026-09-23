"""API-layer tests for the FileBrowser Quantum proxy (api/routers/filebrowser.py):
header sanitization (strip client X-Fb-User, inject trusted one), the signed
target cookie, the launch endpoint (authorize + audit + set cookie + redirect),
and the unauthenticated proxy behavior.
"""
from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

import api.main as main
from api.routers import filebrowser as fbr
from api.security import Identity, get_identity


@pytest.fixture(autouse=True)
def isolated_backend_client(monkeypatch):
    import httpx
    client = httpx.AsyncClient()
    monkeypatch.setattr(fbr, "_client", client, raising=False)
    monkeypatch.setattr(fbr, "_account_client", lambda _username: client)


# --- pure helpers ----------------------------------------------------------


def test_signed_target_roundtrip_and_tamper_rejected():
    token = fbr._sign_target("demo1")
    assert fbr._unsign_target(token) == "demo1"
    assert fbr._unsign_target(token + "x") is None
    assert fbr._unsign_target("garbage") is None


def test_file_proxy_write_origin_is_exact():
    from starlette.requests import Request

    def request(method, origin=None):
        headers = [(b"host", b"panel.example:2222")]
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        return Request({"type": "http", "scheme": "https", "server": ("panel.example", 2222),
                        "path": "/files/api/resources", "method": method, "headers": headers})

    assert fbr._valid_write_origin(request("POST", "https://panel.example:2222"))
    assert not fbr._valid_write_origin(request("POST", "https://customer.example"))
    assert not fbr._valid_write_origin(request("DELETE"))


def test_file_proxy_rejects_cross_origin_cookie_write_before_backend(monkeypatch):
    import asyncio
    from starlette.requests import Request
    from fastapi import HTTPException
    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    monkeypatch.setattr(fbr, "_resolve_identity", lambda _request: admin)
    monkeypatch.setattr(fbr._client, "send", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("backend reached")))
    request = Request({"type": "http", "scheme": "https", "server": ("panel.example", 2222),
                       "path": "/files/api/resources", "method": "POST",
                       "headers": [(b"host", b"panel.example:2222"), (b"origin", b"https://customer.example")]})
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(fbr.proxy(request, "api/resources"))
    assert rejected.value.status_code == 403


class _FakeRequest:
    def __init__(self, headers):
        from starlette.datastructures import Headers

        # raw= preserves duplicate/case-variant header names (a dict would
        # collapse them), which is exactly what we want to test stripping.
        self.headers = Headers(raw=[(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers])


def test_build_upstream_headers_strips_client_identity_and_cookie():
    header_name = fbr.settings.filebrowser_header
    req = _FakeRequest(
        [
            ("host", "panel.example"),
            ("content-length", "10"),
            ("cookie", "fh_session=abc; fh_fb_target=def"),
            (header_name, "attacker"),           # client-supplied identity — MUST be stripped
            (header_name.upper(), "attacker2"),  # case variant — MUST be stripped
            ("accept", "application/json"),
        ]
    )
    out = fbr._build_upstream_headers(req, "demo1")
    keys = {k.lower() for k, _ in out}
    assert "host" not in keys
    assert "content-length" not in keys
    assert "cookie" not in keys
    # exactly one trusted identity header, set to the server-chosen target
    idents = [(k, v) for k, v in out if k.lower() == header_name.lower()]
    assert idents == [(header_name, "demo1")]
    assert ("accept", "application/json") in out


def test_html_csp_hashes_inline_scripts():
    """Regression for the 'stuck on loading spinner' bug: FB Quantum's SPA
    boots from an inline <script> (window.globalVars = ...) that a static
    script-src 'self' policy blocks. The proxy must hash the actual inline
    script(s) of the HTML it serves into the CSP."""
    import base64 as b64
    import hashlib as hl

    script = b'window.globalVars = {"baseURL":"/files/"};'
    body = b"<html><head><script>" + script + b"</script><script src=/files/x.js></script></head></html>"
    csp = fbr.html_csp(body)
    expected = b64.b64encode(hl.sha256(script).digest()).decode()
    assert f"'sha256-{expected}'" in csp
    # the src= script must NOT be hashed (only true inline blocks)
    assert csp.count("sha256-") == 1
    # posture unchanged: no unsafe-inline for scripts, self-only connect
    assert "script-src 'self' 'wasm-unsafe-eval' 'sha256-" in csp
    assert "connect-src 'self'" in csp


def test_html_csp_no_inline_scripts_stays_strict():
    csp = fbr.html_csp(b"<html><body>hi</body></html>")
    assert "sha256-" not in csp
    assert "script-src 'self' 'wasm-unsafe-eval';" in csp


def test_build_response_headers_drops_hop_by_hop_and_length():
    import httpx

    resp = httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "content-length": "5",
            "connection": "keep-alive",
            "transfer-encoding": "chunked",
            "set-cookie": "x=1",
        },
    )
    out = {k.lower() for k, _ in fbr._build_response_headers(resp)}
    assert "content-type" in out
    assert "set-cookie" in out
    assert "content-length" not in out
    assert "connection" not in out
    assert "transfer-encoding" not in out


# --- launch endpoint -------------------------------------------------------


@pytest.fixture()
def admin_client(monkeypatch):
    """TestClient with get_identity overridden to an admin, and the daemon RPC
    stubbed so no real socket is needed."""
    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    main.app.dependency_overrides[get_identity] = lambda: admin

    audited = []
    monkeypatch.setattr(fbr, "call_daemon", lambda op, identity, **p: audited.append((op, p)) or {"scope": "/demo1"})

    client = TestClient(main.app, follow_redirects=False)
    yield client, audited
    main.app.dependency_overrides.pop(get_identity, None)


def test_launch_authorizes_audits_and_sets_cookie(admin_client):
    client, audited = admin_client
    r = client.get("/api/v1/accounts/demo1/files/launch")
    assert r.status_code == 302
    assert r.headers["location"] == fbr.settings.filebrowser_base_url + "/"
    # audited via the fb.open RPC (this is how admin file access is logged)
    assert audited == [("fb.open", {"username": "demo1"})]
    # signed target cookie set, scoped to the /files path
    setc = r.headers.get("set-cookie", "")
    assert fbr.FB_TARGET_COOKIE in setc
    assert "HttpOnly" in setc


# --- unauthenticated proxy behavior ----------------------------------------


def test_proxy_unauthenticated_html_redirects_to_app():
    client = TestClient(main.app, follow_redirects=False)
    r = client.get(fbr.settings.filebrowser_base_url, headers={"accept": "text/html"})
    assert r.status_code == 302
    assert r.headers["location"] == "/app"


def test_proxy_unauthenticated_xhr_returns_401():
    client = TestClient(main.app, follow_redirects=False)
    r = client.get(
        fbr.settings.filebrowser_base_url + "/api/resources",
        headers={"accept": "application/json"},
    )
    assert r.status_code == 401


def test_proxy_authenticated_injects_trusted_header(monkeypatch):
    """With a valid identity + signed target cookie, the proxy strips any client
    identity header and forwards exactly the server-chosen one to the backend."""
    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    monkeypatch.setattr(fbr, "get_identity", lambda *a, **k: admin)

    captured = {}

    class _FakeResp:
        status_code = 200
        headers = __import__("httpx").Headers({"content-type": "application/json"})

        async def aiter_raw(self):
            yield b"{}"

        async def aclose(self):
            pass

    async def fake_send(req, **kwargs):
        captured["req"] = req
        return _FakeResp()

    monkeypatch.setattr(fbr._client, "send", fake_send)

    client = TestClient(main.app, follow_redirects=False)
    client.cookies.set(fbr.FB_TARGET_COOKIE, fbr._sign_target("demo1"))
    r = client.get(
        fbr.settings.filebrowser_base_url + "/api/resources",
        headers={"accept": "application/json", fbr.settings.filebrowser_header: "attacker"},
    )
    assert r.status_code == 200
    sent = captured["req"]
    assert sent.headers.get(fbr.settings.filebrowser_header) == "demo1"  # injected, not "attacker"


def test_proxy_html_gets_hashed_csp_not_middleware_fallback(monkeypatch):
    """An HTML response through the proxy must carry the per-response hashed
    CSP (allowing FB's inline bootstrap) and the app middleware must not
    overwrite it with the static fallback."""
    import base64 as b64
    import hashlib as hl

    import httpx

    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    monkeypatch.setattr(fbr, "get_identity", lambda *a, **k: admin)

    script = b'window.globalVars = {"baseURL":"/files/"};'
    html = b"<html><head><script>" + script + b"</script></head><body></body></html>"

    class _FakeResp:
        status_code = 200
        headers = httpx.Headers({"content-type": "text/html; charset=utf-8", "Content-Disposition": "inline; filename=evil.html", "Content-Security-Policy": "default-src *", "X-Content-Type-Options": "upstream"})

        async def aread(self):
            return html

        async def aclose(self):
            pass

    async def fake_send(req, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(fbr._client, "send", fake_send)

    client = TestClient(main.app, follow_redirects=False)
    client.cookies.set(fbr.FB_TARGET_COOKIE, fbr._sign_target("demo1"))
    r = client.get(fbr.settings.filebrowser_base_url + "/", headers={"accept": "text/html"})
    assert r.status_code == 200
    expected = b64.b64encode(hl.sha256(script).digest()).decode()
    csp = r.headers.get("content-security-policy", "")
    assert f"'sha256-{expected}'" in csp, csp  # hash present, middleware didn't clobber it


def test_proxy_uploaded_html_cannot_bless_its_own_script(monkeypatch):
    """A customer HTML download is not the trusted FileBrowser SPA shell."""
    import asyncio
    import httpx
    from starlette.requests import Request
    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None,
                     auth_method="session")
    monkeypatch.setattr(fbr, "_resolve_identity", lambda _request: admin)

    class _FakeResp:
        status_code = 200
        headers = httpx.Headers({"content-type": "text/html; charset=utf-8", "Content-Disposition": "inline; filename=evil.html", "Content-Security-Policy": "default-src *", "X-Content-Type-Options": "upstream"})

        async def aread(self):
            return b"<html><script>fetch('/api/v1/whoami')</script></html>"

        async def aclose(self):
            pass

    async def fake_send(req, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(fbr._client, "send", fake_send)
    path = fbr.settings.filebrowser_base_url + "/api/resources/evil.html"
    request = Request({"type": "http", "scheme": "https", "server": ("panel.example", 2222),
                       "path": path, "method": "GET",
                       "headers": [(b"host", b"panel.example:2222"),
                                   (b"cookie", (fbr.FB_TARGET_COOKIE + "=" + fbr._sign_target("demo1")).encode()),
                                   (b"accept", b"text/html")]})
    response = asyncio.run(fbr.proxy(request, "api/resources/evil.html"))
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == fbr.untrusted_html_csp()
    assert "sha256-" not in response.headers["content-security-policy"]
    assert response.headers.getlist("content-disposition") == ["attachment"]
    assert response.headers.getlist("content-security-policy") == [fbr.untrusted_html_csp()]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_proxy_svg_document_is_sandboxed(monkeypatch):
    import asyncio
    import httpx
    from starlette.requests import Request
    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None,
                     auth_method="session")
    monkeypatch.setattr(fbr, "_resolve_identity", lambda _request: admin)

    class _FakeResp:
        status_code = 200
        headers = httpx.Headers({"content-type": "image/svg+xml"})

        async def aiter_raw(self):
            yield b"<svg/>"

        async def aclose(self):
            pass

    async def fake_send(req, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(fbr._client, "send", fake_send)
    request = Request({"type": "http", "scheme": "https", "server": ("panel.example", 2222),
                       "path": "/files/api/resources/evil.svg", "method": "GET",
                       "headers": [(b"host", b"panel.example:2222"),
                                   (b"cookie", (fbr.FB_TARGET_COOKIE + "=" + fbr._sign_target("demo1")).encode())]})
    response = asyncio.run(fbr.proxy(request, "api/resources/evil.svg"))
    assert response.headers["content-security-policy"] == fbr.untrusted_html_csp()
    assert response.headers["x-content-type-options"] == "nosniff"


def test_filebrowser_shell_hashes_only_exact_url():
    import httpx
    from starlette.requests import Request
    base = fbr.settings.filebrowser_base_url
    upstream = httpx.Response(200, headers={"content-type": "text/html"})

    def request(path, query=b""):
        return Request({"type": "http", "scheme": "https", "server": ("panel.example", 2222),
                        "path": path, "query_string": query, "method": "GET",
                        "headers": [(b"host", b"panel.example:2222")]})

    assert fbr._is_spa_shell(request(base + "/"), upstream)
    assert not fbr._is_spa_shell(request(base + "/", b"download=site.html"), upstream)
    assert not fbr._is_spa_shell(request(base + "/api/resources/site.html"), upstream)

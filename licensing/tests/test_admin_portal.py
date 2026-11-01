import re

from conftest import call


def test_portal_page_and_assets_are_served_with_strict_headers(client):
    page = client.get("/admin")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    csp = page.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"

    js = client.get("/admin/app.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")
    css = client.get("/admin/app.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert css.headers["x-frame-options"] == "DENY"

    assert client.get("/admin/", follow_redirects=False).headers["location"] == "/admin"
    for missing in ("/admin/index.html", "/admin/nope.js", "/admin/..%2Fapi.py"):
        assert client.get(missing).status_code == 404, missing


def test_portal_is_self_contained_and_inline_free(client):
    html = client.get("/admin").text
    # No inline scripts, handlers or styles: the CSP would block them anyway.
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son[a-z]+\s*=", html)
    assert "style=" not in html and "<style" not in html
    # Only same-origin assets.
    for asset in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert asset.startswith("/admin/"), asset
    js = client.get("/admin/app.js").text
    assert "innerHTML" not in js and "eval(" not in js and "localStorage" not in js
    assert "sessionStorage" in js


def test_api_responses_carry_the_same_headers(client, admin_headers, issue):
    _, key = issue()
    response = call(client, "activate", key)
    assert response.headers["x-frame-options"] == "DENY" and response.headers["cache-control"] == "no-store"
    assert client.get("/v1/admin/me", headers=admin_headers).json() == {"token": "tests"}
    assert client.get("/v1/admin/me").status_code == 401

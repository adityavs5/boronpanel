"""Phase 8 feature 4: whole-domain forwarding."""
import pytest
from unittest.mock import MagicMock
from sqlalchemy import select

from daemon import forwarding, ols
from shared.db import write_session
from shared.models import Account, Domain, DomainForwarding
from shared.validation import ValidationError


def _setup(monkeypatch, username="demo1", kind="primary"):
    monkeypatch.setattr(forwarding.ols, "refresh_vhost", lambda a: None)
    with write_session() as db:
        account = Account(username=username, status="active", uid=5001, gid=5001, php_version="8.3")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="site.com", kind=kind, docroot="/home/demo1/public_html"))
        return account.id


def test_set_forwarding_creates_row(isolated_db, monkeypatch):
    _setup(monkeypatch)
    result = forwarding.set_forwarding({
        "username": "demo1", "domain": "site.com",
        "target_url": "https://example.org/", "status_code": 301, "keep_path": True,
    })
    assert result["target_url"] == "https://example.org"  # trailing slash stripped
    assert result["status_code"] == 301
    with write_session() as db:
        row = db.scalar(select(DomainForwarding).where(DomainForwarding.domain == "site.com"))
        assert row.target_url == "https://example.org"
        assert row.keep_path is True


def test_set_forwarding_upserts(isolated_db, monkeypatch):
    _setup(monkeypatch)
    forwarding.set_forwarding({"username": "demo1", "domain": "site.com", "target_url": "https://a.com"})
    forwarding.set_forwarding({"username": "demo1", "domain": "site.com", "target_url": "https://b.com", "status_code": 302})
    with write_session() as db:
        rows = db.scalars(select(DomainForwarding).where(DomainForwarding.domain == "site.com")).all()
        assert len(rows) == 1
        assert rows[0].target_url == "https://b.com"
        assert rows[0].status_code == 302


def test_set_forwarding_rejects_bad_status(isolated_db, monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(ValidationError):
        forwarding.set_forwarding({"username": "demo1", "domain": "site.com", "target_url": "https://a.com", "status_code": 307})


def test_set_forwarding_rejects_non_http_target(isolated_db, monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(ValidationError):
        forwarding.set_forwarding({"username": "demo1", "domain": "site.com", "target_url": "ftp://a.com"})


def test_set_forwarding_rejects_parked(isolated_db, monkeypatch):
    _setup(monkeypatch, kind="parked")
    with pytest.raises(RuntimeError, match="parked"):
        forwarding.set_forwarding({"username": "demo1", "domain": "site.com", "target_url": "https://a.com"})


def test_get_and_delete_forwarding(isolated_db, monkeypatch):
    _setup(monkeypatch)
    assert forwarding.get_forwarding({"username": "demo1", "domain": "site.com"})["forwarding"] is None
    forwarding.set_forwarding({"username": "demo1", "domain": "site.com", "target_url": "https://a.com"})
    assert forwarding.get_forwarding({"username": "demo1", "domain": "site.com"})["forwarding"]["target_url"] == "https://a.com"
    forwarding.delete_forwarding({"username": "demo1", "domain": "site.com"})
    assert forwarding.get_forwarding({"username": "demo1", "domain": "site.com"})["forwarding"] is None


# --- vhost render (the actual mechanism) -----------------------------------


def _render(forwarding_ctx):
    acc = MagicMock(); acc.username = "demo1"; acc.php_version = "8.3"
    dom = {"domain": "ex.com", "docroot": "/home/demo1/public_html", "ssl_status": "none",
           "hotlink_protection_enabled": False, "hotlink_allowed_domains": [], "ip_block_list": [], "php_version": None}
    return ols.render_vhost_conf(acc, dom, suspended=False, forwarding=forwarding_ctx)


def test_render_forwarding_keep_path():
    out = _render({"target_url": "https://target.example", "status_code": 301, "keep_path": True})
    assert "RewriteRule ^/?(.*)$ https://target.example/$1 [R=301,L]" in out
    # ACME challenge excluded so SSL stays issuable
    assert "RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/" in out


def test_render_forwarding_no_keep_path():
    out = _render({"target_url": "https://target.example", "status_code": 302, "keep_path": False})
    assert "RewriteRule ^/?(.*)$ https://target.example [R=302,L]" in out


def test_render_no_forwarding_serves_php():
    out = _render(None)
    assert "scripthandler" in out
    assert "target.example" not in out
